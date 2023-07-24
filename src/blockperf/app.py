
import collections
import json
import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from timeit import default_timer as timer

import paho.mqtt.client as mqtt

from blockperf import __version__ as blockperf_version
from blockperf import logger_name
from blockperf.config import AppConfig
from blockperf.sampling import BlockSample, LogLine, LogLineKind

LOG = logging.getLogger(logger_name)


class App:
    q: queue.Queue
    app_config: AppConfig
    node_config: dict
    _mqtt_client: mqtt.Client

    logevents = dict() # holds all logevents for each hash encountered
    published_hashes = collections.deque()

    def __init__(self, config: AppConfig) -> None:
        self.q = queue.Queue(maxsize=20)
        self.app_config = config

    def run(self):
        """Run the App by creating the two threads and starting them."""
        producer_thread = threading.Thread(target=self.produce_blocksamples, args=())
        producer_thread.start()
        # LOG.info("Producer thread started")

        # consumer = BlocklogConsumer(queue=self.q, app_config=self.app_config)
        consumer_thread = threading.Thread(target=self.consume_blocksample, args=())
        consumer_thread.start()
        # LOG.info("Consumer thread started")

        # Blocks main thread until all joined threads are finished
        producer_thread.join()
        consumer_thread.join()

    @property
    def mqtt_client(self) -> mqtt.Client:
        # Returns the mqtt client, or creates one if there is none
        if not hasattr(self, "_mqtt_client"):
            LOG.info("(Re)Creating new mqtt client")
            # Every new client will start clean unless client_id is specified
            self._mqtt_client = mqtt.Client(protocol=mqtt.MQTTv5)
            self._mqtt_client.on_connect = self.on_connect_callback
            self._mqtt_client.on_disconnect = self.on_disconnect_callback
            self._mqtt_client.on_publish = self.on_publish_callback
            self._mqtt_client.on_log = self.on_log

            # tls_set has an argument 'ca_certs'. I used to provide a file
            # whith one of the certificates from https://www.amazontrust.com/repository/
            # But from readig the tls_set() code i suspect when i leave that out
            # thessl.SSLContext will try to autodownload that CA!?
            self._mqtt_client.tls_set(
                # ca_certs="/tmp/AmazonRootCA1.pem",
                certfile=self.app_config.client_cert,
                keyfile=self.app_config.client_key,
            )
        return self._mqtt_client

    def on_connect_callback(self, client, userdata, flags, reasonCode, properties) -> None:
        """Called when the broker responds to our connection request.
        See paho.mqtt.client.py on_connect()"""
        if not reasonCode == 0:
            LOG.error("Connection error " + str(reasonCode))
            self._mqtt_connected = False
        else:
            self._mqtt_connected = True

    def on_disconnect_callback(self, client, userdata, reasonCode, properties) -> None:
        """Called when disconnected from broker
        See paho.mqtt.client.py on_disconnect()"""
        LOG.error(f"Connection lost {reasonCode}")

    def on_publish_callback(self, client, userdata, mid) -> None:
        """Called when a message is actually received by the broker.
        See paho.mqtt.client.py on_publish()"""
        # There should be a way to know which messages belongs to which
        # item in the queue and acknoledge that specifically
        # self.q.task_done()
        pass
        # LOG.debug(f"Message {mid} published to broker")

    def on_log(self, client, userdata, level, buf):
        """
        client:     the client instance for this callback
        userdata:   the private user data as set in Client() or userdata_set()
        level:      gives the severity of the message and will be one of
                    MQTT_LOG_INFO, MQTT_LOG_NOTICE, MQTT_LOG_WARNING,
                    MQTT_LOG_ERR, and MQTT_LOG_DEBUG.
        buf:        the message itself
        """
        LOG.debug(f"MQTT: {level} - {buf}")

    def consume_blocksample(self):
        """Runs the Consumer thread. Will get called from Thread base once ready.
        If run() finishes, the thread will finish.
        """
        self.mqtt_client
        broker_url, broker_port = (
            self.app_config.mqtt_broker_url,
            self.app_config.mqtt_broker_port,
        )
        while True:
            LOG.debug(
                f"Connecting to {broker_url}:{broker_port}"
            )
            self.mqtt_client.connect(broker_url, broker_port)
            self.mqtt_client.loop_start()  # Starts thread for pahomqtt to process messages
            # Sometimes the connect took a moment to settle. To not have
            # the consumer accept messages (and not be able to publish)
            # i decided to ensure the connection is established this way
            while not self.mqtt_client.is_connected:
                LOG.debug("Waiting for mqtt connection ... ")
                time.sleep(0.5)  # Block until connected
            LOG.debug(
                f"Waiting for next item in queue, Current size: {self.q.qsize()}"
            )
            # The call to get() blocks until there is something in the queue
            blocksample = self.q.get()
            LOG.info("\n" + blocksample.as_msg_string())
            if self.q.qsize() > 0:
                LOG.debug(f"{self.q.qsize()} left in queue")
            payload = blocksample.as_payload_dict()
            LOG.debug(payload)
            start_publish = timer()
            message_info = self.mqtt_client.publish(
                topic=self.app_config.topic,
                payload=json.dumps(payload, default=str)
            )
            # wait_for_publish blocks until timeout for the message to be published
            message_info.wait_for_publish(5)
            end_publish = timer()
            publish_time = end_publish - start_publish
            if self.app_config.verbose:
                LOG.info(
                    f"Published {blocksample.block_hash_short} with mid='{message_info.mid}' to {self.app_config.topic} in {publish_time}"
                )
            if publish_time > 5.0:
                LOG.warning("Publish time > 5.0")


    def add_logline(self, _hash, logline) -> None:
        """Add given logline to the list indentified by _hash
        If there is no list create it along the way."""
        if not _hash in self.logevents:
            LOG.debug(f"Creating new list for {_hash}. {len(self.logevents)}" )
            self.logevents[_hash] = list()
        self.logevents[_hash].append(logline)

    def blocksample(self, _hash):
        """Create BlockSample for given _hash from the list of all lines for it
        Then pass that BlockSample to the queue to have the consumer publish it"""
        events = self.logevents[_hash]
        sample = BlockSample(events, self.app_config)
        self.published_hashes.append(_hash)
        LOG.debug(f"Published hashes {len(self.published_hashes)} {self.published_hashes}")
        # push smaple to consumer
        self.q.put(sample)
        if len(self.published_hashes) >= 10:
            # Remove from left; Latest 10 hashes published will be in deque
            removed_hash = self.published_hashes.popleft()
            # Delete that hash from all logevents
            del self.logevents[removed_hash]
            LOG.debug(f"Removed {removed_hash} from published_hashes")

        LOG.debug(f"Unpublished blocks: {len(self.logevents.keys())}; [{' '.join([ h[0:10] for h in self.logevents.keys()])}]")
        LOG.debug(f"Published hashes {len(self.published_hashes)} {self.published_hashes}")

    def produce_blocksamples(self):
        """Producer thread that reads the logfile and sends blocksamples to the queue (not mqtt!) """
        adopting_block_kinds = (
            LogLineKind.ADDED_TO_CURRENT_CHAIN,
            LogLineKind.SWITCHED_TO_A_FORK,
        )
        for logline in self.generate_loglines():
            _block_hash = logline.block_hash
            assert _block_hash, f"Found a trace that has no hash {logline}!"
            # If the same hash has already been seen published, dont bother
            # But that ac
            if _block_hash in self.published_hashes:
                continue
            LOG.debug(logline)
            self.add_logline(_block_hash, logline)
            if logline.kind in adopting_block_kinds:
                self.blocksample(_block_hash)

    def generate_loglines(self):
        """Generator that yields new lines from the logfile as they come in."""
        interesting_kinds = (
            LogLineKind.TRACE_DOWNLOADED_HEADER,
            LogLineKind.SEND_FETCH_REQUEST,
            LogLineKind.COMPLETED_BLOCK_FETCH,
            LogLineKind.ADDED_TO_CURRENT_CHAIN,
        )
        node_log = self.app_config.node_logdir.joinpath("node.json")
        if not node_log.exists():
            sys.exit(f"{node_log} does not exist!")

        now_minus_x = int(datetime.now().timestamp()) - int(timedelta(seconds=10).total_seconds())
        while True:
            real_node_log = self.app_config.node_logdir.joinpath(node_log.readlink())
            same_file = True
            with open(real_node_log, "r") as fp:
                fp.seek(0, 2)
                LOG.debug(f"Opened {real_node_log}")
                while same_file:
                    new_line = fp.readline()
                    # if no new_line is returned from fp, check that the symlink
                    # is still the same. If it is, wait 0.1 seconds and try again
                    # If its is not set same_file to False to reopen the symlink
                    if not new_line:
                        if real_node_log.name != node_log.readlink().name:
                            LOG.debug(f"Symlink changed from {real_node_log.name} to {node_log.readlink()} ")
                            same_file = False
                        time.sleep(0.1)
                        continue

                    # Parse the logline
                    logline = LogLine.from_logline(new_line)
                    if not logline:
                        continue
                    if not int(logline.at.timestamp()) > now_minus_x:
                        continue
                    if not logline.kind in interesting_kinds:
                        continue

                    # We now know, there is a line, that is not too old and its
                    # of a kind that we are interested in -> yield it!
                    yield (logline)
