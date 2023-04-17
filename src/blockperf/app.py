import fcntl
import json
import logging
import os
import queue
import random
from cryptography import x509
from cryptography.x509.oid import NameOID
import sys
import threading
import time
import traceback
import urllib
from time import sleep
from configparser import ConfigParser
from dataclasses import InitVar, dataclass, field
from pathlib import Path
from pprint import pprint
from urllib.error import URLError
from urllib.request import Request, urlopen

import paho.mqtt.client as mqtt


from blockperf import __version__ as blockperf_version
from blockperf.blocklog import Blocklog
from blockperf.exceptions import EKGError

logging.basicConfig(level=logging.DEBUG, format="(%(threadName)-9s) %(message)s")


output = Path("output.json")

@dataclass
class EkgResponse:
    """Holds all the relevant datafrom the ekg response json for later use."""

    response: InitVar[dict]
    block_num: int = field(init=False, default=0)
    slot_num: int = field(init=False, default=0)
    forks: int = field(init=False, default=0)

    def __post_init__(self, response: dict) -> None:
        # Assuming cardano.node.metrics will always be there
        metrics = response.get("cardano").get("node").get("metrics")
        self.slot_num = metrics.get("slotNum").get("int").get("val")
        self.block_num = metrics.get("blockNum").get("int").get("val")
        self.forks = metrics.get("forks").get("int").get("val")


class BlocklogProducer(threading.Thread):
    q: queue.Queue
    ekg_url: str
    log_dir: str
    last_block_num: int = 0
    last_fork_height: int = 0
    last_slot_num: int = 0
    count: int = 0
    all_blocklogs: list = dict()

    def __init__(self, queue, ekg_url: str, log_dir: str, network_magic: int):
        super(BlocklogProducer, self).__init__(daemon=True, name="producer")
        self.q = queue
        self.ekg_url = ekg_url
        self.log_dir = log_dir

    def run(self):
        """Runs the Producer. Will get called from the Thread once its ready.
        If run() finishes the thread will finish.
        """
        while True:
            try:
                self._run()
            except EKGError:
                time.sleep(.5)
            except Exception as e:
                print(f"Error: {e}")
                print(traceback.format_exc())
                time.sleep(1)

    def _run(self) -> None:
        # Call ekg to get current slot_num, block_num and fork_num
        ekg_response = self.call_ekg()
        assert ekg_response.slot_num > 0, "EKG did not report a slot_num"
        assert ekg_response.block_num > 0, "EKG did not report a block_num"

        self.count += 1
        # print(f"Run: {self.count} - last_block_num: {self.last_block_num}")
        if self.count == 1:  # If its the first round, set last_* values and continue
            self.last_slot_num = ekg_response.slot_num
            self.last_block_num = ekg_response.block_num
            # last_fork_height = fork_height
            return

        # Produces a list of block_nums, we want to check the logs for
        block_nums = self.calculate_block_nums_from_ekg(ekg_response.block_num)
        if not block_nums: # If no change in block_num found, we are not interested?
            assert (
                ekg_response.forks != 0
            ), "No block_nums found; but forks are reported?"
            return

        # Produces a list of blocklogs we want to report (based upon the block_nums from before)
        blocklogs_to_report = Blocklog.blocklogs_from_block_nums(block_nums, self.log_dir)

        # Handling of forks ... is not implemted yet
        if ekg_response.forks > 0:
            # find the blocklog that is a fork and get its hash
            # blocklog_with_switch = for b in _blocklogs: b.is_forkswitch
            # blocklog_with_switch.all_trace_headers[0].block_num
            # find the blocklog that is a forkswitch
            # Wenn minimal verbosity in config kann newtip kurz sein (less then 64)
            # depending on the node.config
            #    If newtip is less than 64
            #
            # blocklog_from_fork_hash(newtip)
            pass



        for blocklog in blocklogs_to_report:
            print()
            self.to_cli_message(blocklog)
            print()
            self.q.put(blocklog)

        self.last_block_num = ekg_response.block_num
        self.last_slot_num = ekg_response.slot_num

        # self.all_blocklogs.update([(b.block_hash, b) for b in blocklogs_to_report])
        # print(self.all_blocklogs)
        time.sleep(1)

    def calculate_block_nums_from_ekg(self, current_block_num) -> list:
        """
        blocks will hold the block_nums from last_block_num + 1 until the
        currently reported one. So if last_block_num = 14 and ekg_response.block_num
        is 16, then blocks will be [15, 16]
        """
        # If there is no change, or the change is too big (node probably syncing)
        # return an empty list
        delta_block_num = current_block_num - self.last_block_num
        if not delta_block_num or delta_block_num >= 5:
            return []

        block_nums = []
        for num in range(1, delta_block_num + 1):
            block_nums.append(self.last_block_num + num)
        return block_nums

    def call_ekg(self) -> EkgResponse:
        """Calls the EKG Port for as long as needed and returns a response if
        there is one. It is not inspecting the block data itself, meaning it will
        just return a tuple of the block_num, the forks and slit_num."""
        try:
            req = Request(
                url=self.ekg_url,
                headers={"Accept": "application/json"},
            )
            response = urlopen(req)
            if response.status != 200:
                raise EKGError(f"Invalid HTTP response received {response}")
            # Happy path ...
            return EkgResponse(json.loads(response.read()))
        except URLError as _e:
            raise EKGError(f"URLError {_e.reason}")
        except ConnectionResetError:
            raise EKGError("Could not open connection")

    def to_cli_message(self, blocklog: Blocklog):
        """
        The Goal is to print a messages like this per BlockPerf

        Block:.... 792747 ( f581876904 ...)
        Slot..... 24845021 (4s)
        ......... 2023-05-23 13:23:41
        Header... 2023-04-03 13:23:41,170 (+170 ms) from 207.180.196.63:3001
        RequestX. 2023-04-03 13:23:41,170 (+0 ms)
        Block.... 2023-04-03 13:23:41,190 (+20 ms) from 207.180.196.63:3001
        Adopted.. 2023-04-03 13:23:41,190 (+0 ms)
        Size..... 870 bytes
        delay.... 0.192301717 sec
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        """
        slot_num_delta = blocklog.slot_num - self.last_slot_num
        # ? blockSlot-slotHeightPrev -> Delta between this slot and the last one that had a block?
        msg = (
            f"Block:.... {blocklog.block_num} ({blocklog.block_hash_short})\n"
            f"Slot:..... {blocklog.slot_num} ({slot_num_delta}s)\n"
            f".......... {blocklog.slot_time}\n" # Assuming this is the slot_time
            f"Header ... {blocklog.first_trace_header.at} ({blocklog.header_delta}) from {blocklog.header_remote_addr}:{blocklog.header_remote_port}\n"
            f"RequestX.. {blocklog.fetch_request_completed_block.at} ({blocklog.block_request_delta})\n"
            f"Block..... {blocklog.first_completed_block.at} ({blocklog.block_response_delta}) from {blocklog.block_remote_addr}:{blocklog.block_remote_port}\n"
            f"Adopted... {blocklog.block_adopt} ({blocklog.block_adopt_delta})\n"
            f"Size...... {blocklog.block_size} bytes\n"
            f"Delay..... {blocklog.block_delay} sec\n\n"
        )
        sys.stdout.write(msg)


class BlocklogConsumer(threading.Thread):
    """Consumes every Blocklog that is put into the queue.
    Consuming means, taking its message and sending it through MQTT.
    """

    q: queue.Queue
    _mqtt_client: mqtt.Client = None
    messages_sent: list = []
    app: "App"

    def __init__(self, queue, client_cert, client_key, app):
        super(BlocklogConsumer, self).__init__(daemon=True)
        print("BlocklogConsumer::__init__")
        self.name = "consumer"
        self.app = app
        self.q = queue
        # Every new client will start clean unless client_id is specified
        self._mqtt_client = mqtt.Client(protocol=mqtt.MQTTv5)
        self._mqtt_client.on_connect = self.on_connect_callback
        self._mqtt_client.on_disconnect = self.on_disconnect_callback
        self._mqtt_client.on_publish = self.on_publish_callback
        self._mqtt_client.tls_set(
            ca_certs="/home/msch/src/cf/blockperf.py/tmp/AmazonRootCA1.pem",
            certfile=client_cert,
            keyfile=client_key,
        )

    def build_payload_from(self, blocklog: Blocklog) -> str:
        """ """
        message = {
            "magic": self.app.network_magic,
            "bpVersion": blockperf_version,
            "blockNo": blocklog.block_num,
            "slotNo": blocklog.slot_num,
            "blockHash": blocklog.block_hash,
            "blockSize": blocklog.block_size,
            "headerRemoteAddr": blocklog.header_remote_addr,
            "headerRemotePort": blocklog.header_remote_port,
            "headerDelta": blocklog.header_delta,
            "blockReqDelta": blocklog.block_request_delta,
            "blockRspDelta": blocklog.block_response_delta,
            "blockAdoptDelta": blocklog.block_adopt_delta,
            "blockRemoteAddress": blocklog.block_remote_addr,
            "blockRemotePort": blocklog.block_remote_port,
            #"blockLocalAddress": blocklog.block_local_address,
            #"blockLocalPort": blocklog.block_local_port,
            "blockG": blocklog.block_g,
        }
        return json.dumps(message, default=str)

    @property
    def mqtt_client(self) -> mqtt.Client:
        return self._mqtt_client

    def run(self):
        """ """
        self.mqtt_client.connect(
            "a12j2zhynbsgdv-ats.iot.eu-central-1.amazonaws.com", port=8883
        )
        self.mqtt_client.loop_start()  # Start thread for pahomqtt to process messages

        # Sometimes the connect took a moment to settle. To not have
        # the consumer accept messages (and not be able to publish)
        # i decided to ensure the connection is established this way
        while not self.mqtt_client.is_connected:
            print("Waiting for mqtt connection")
            sleep(0.5)

        from pprint import pprint
        from io import StringIO


        while True:
            unpublished_messages = list(
                filter(lambda msg: not msg.is_published(), self.messages_sent)
            )
            if unpublished_messages:
                print("Some messages have not yet been published (ack'ed from broker)")
                # Do we really want to block here for the messages to
                for m in unpublished_messages:
                    print(f"Blocking on message {m.mid}")
                    m.wait_for_publish(10)

            # The call to get() blocks until there is something in the queue
            print("Waiting for next during q.get() ")
            blocklog: Blocklog = self.q.get()
            print(f"Fetching {blocklog} : {self.q.qsize()} items left ")
            # payload = json.dumps({"message": "Foobar"})
            payload = self.build_payload_from(blocklog)
            topic = self.get_topic()
            pprint(payload)
            message_info = self.mqtt_client.publish(topic=topic, payload=payload)
            print(f"Published message {message_info.mid} to {topic}")
            self.messages_sent.append(message_info)

    def get_topic(self) -> str:
        topic = f"develop/{self.app.operator}/{self.app.relay_public_ip}"
        return topic

    def on_connect_callback(self, client, userdata, flags, reasonCode, properties):
        """
        Called when the broker responds to our connection request.
        """
        print("Connection returned " + str(reasonCode))
        self._mqtt_connected = True

    def on_disconnect_callback(client, userdata, reasonCode, properties):
        """Look into reasonCode for reason of disconnection"""
        print("on_disconnect_callback")
        print(reasonCode)

    def on_publish_callback(self, client, userdata, mid):
        print(f"on_publish_callback for message {mid}")
        # There should be a way to know which messages belongs to which
        # item in the queue and acknoledge that specifically
        self.q.task_done()


class App:
    q: queue.Queue
    # config: ConfigParser
    node_config: dict = None
    network_magic: int = 0
    lockfile_path: str = "/tmp/blockperf.lock"
    relay_public_ip: str = None
    operator: str = None

    def __init__(self, config: str) -> None:
        _config = ConfigParser()
        _config.read(config)
        self._read_config(_config)
        self.q = queue.Queue(maxsize=10)

    def check_already_running(self) -> bool:
        """Checks if an instance is already running, exitst if it does!
        Will not work on windows since fcntl is unavailable there!!
        """
        lock_file_fp = open(self.lockfile_path, "a")
        try:
            # Try to get exclusive lock on lockfile
            fcntl.lockf(lock_file_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_file_fp.seek(0)
            lock_file_fp.truncate()
            lock_file_fp.write(str(os.getpid()))
            lock_file_fp.flush()
            # Could acquire lock, seems no instance is already running
        except (IOError, BlockingIOError):
            # Could not acquire lock,
            # pid = Path(self.lockfile_path).read_text()
            sys.exit(f"Could not acquire exclusive lock on {self.lockfile_path}")

    def _read_config(self, config: ConfigParser):
        """ """
        node_config_path = Path(
            config.get(
                "DEFAULT",
                "node_config",
                fallback="/opt/cardano/cnode/files/config.json",
            )
        )
        node_config_folder = node_config_path.parent
        if not node_config_path.exists():
            sys.exit(f"Node config not found {node_config_path}!")

        self.node_config = json.loads(node_config_path.read_text())
        self.ekg_url = config.get(
            "DEFAULT", "ekg_url", fallback="http://127.0.0.1:12788"
        )
        self.log_dir = config.get(
            "DEFAULT", "node_logs_dir", fallback="/opt/cardano/cnode/logs"
        )

        # for now assuming that these are relative paths to config.json
        # print(self.node_config["AlonzoGenesisFile"])
        # print(self.node_config["ByronGenesisFile"])
        shelly_genesis = json.loads(
            node_config_folder.joinpath(
                self.node_config["ShelleyGenesisFile"]
            ).read_text()
        )
        self.network_magic = int(shelly_genesis["networkMagic"])
        print(self.network_magic)

        self.relay_public_ip = config.get("DEFAULT", "relay_public_ip")
        if not self.relay_public_ip:
            # For now its required ... but we could implement a "best effort guess"?
            sys.exit("You need to set the relays ip addres!")

        self.client_cert = config.get("DEFAULT", "client_cert")
        self.client_key = config.get("DEFAULT", "client_key")

        if not self.client_cert or not self.client_key:
            sys.exit("You need to set client_cert and client_key!")

        self.operator = config.get("DEFAULT", "operator")
        if not self.operator:
            # Could be picked up from cert, see above
            sys.exit("You need to set the operator!")

        # Try to check whether CN of cert matches given operator
        cert = x509.load_pem_x509_certificate(Path(self.client_cert).read_bytes())
        name_attribute = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME).pop()
        assert name_attribute.value == self.operator, "Given operator does not match CN in certificate"

    def run(self):
        self.check_already_running()
        producer = BlocklogProducer(
            queue=self.q,
            ekg_url=self.ekg_url,
            log_dir=self.log_dir,
            network_magic=self.network_magic,
        )
        producer.start()
        consumer = BlocklogConsumer(
            queue=self.q,
            client_cert=self.client_cert,
            client_key=self.client_key,
            app=self,
        )
        consumer.start()
        print("Created both")

        producer.join()
        print("producer joined")

        consumer.join()
        print("consumer joined")

        # self.q.join()
