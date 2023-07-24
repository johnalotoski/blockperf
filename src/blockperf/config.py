"""App Configuration based on pythons stdlib configparser module.
Why configparser? Because its simple. There is a blockperf.ini file in the
contrib/ folder which has all options and a short explanation of what they do.
"""
import json
import os
from configparser import ConfigParser
from pathlib import Path
from typing import Union


class ConfigError(Exception):
    pass


class AppConfig:
    config_parser: ConfigParser

    def __init__(self, config_file: Union[Path, None], verbose=False):
        self.config_parser = ConfigParser()
        if config_file:
            self.config_parser.read(config_file)
        self.verbose = verbose

    @property
    def node_config_file(self) -> Path:
        node_config_file = os.getenv(
            "BLOCKPERF_NODE_CONFIG",
            self.config_parser.get(
                "DEFAULT",
                "node_config",
                fallback="/opt/cardano/cnode/files/config.json",
            )
        )
        node_config = Path(node_config_file)
        if not node_config.exists():
            raise ConfigError(f"Could not open {node_config_file}")
        return node_config

    @property
    def node_config(self) -> dict:
        """Return Path to config.json file from env var, ini file or builtin default"""
        return json.loads(self.node_config_file.read_text())

    @property
    def node_configdir(self) -> Path:
        """Return Path to directory of config.json"""
        return self.node_config_file.parent

    @property
    def node_logdir(self) -> Path:
        for ss in self.node_config.get("setupScribes", []):
            if ss.get("scFormat") == "ScJson" and ss.get("scKind") == "FileSK":
                _node_logdir = Path(ss.get("scName")).parent
                return _node_logdir
        else:
            raise ConfigError(f"Could not determine node logdir")

    @property
    def blockperf_logfile(self) -> Union[Path, None]:
        blockperf_logfile = os.getenv(
            "BLOCKPERF_LOGFILE",
            self.config_parser.get("DEFAULT", "blockperf_logfile", fallback=None)
        )
        if blockperf_logfile:
            return Path(blockperf_logfile)
        return None

    @property
    def network_magic(self) -> int:
        """Retrieve network magic from ShelleyGenesisFile"""
        shelley_genesis = json.loads(
            self.node_configdir.joinpath(
                self.node_config.get("ShelleyGenesisFile", "")
            ).read_text()
        )
        return int(shelley_genesis.get("networkMagic", 0))

    @property
    def relay_public_ip(self) -> str:
        relay_public_ip = os.getenv(
            "BLOCKPERF_RELAY_PUBLIC_IP",
            self.config_parser.get("DEFAULT", "relay_public_ip", fallback=None)
        )
        if not relay_public_ip:
            raise ConfigError("'relay_public_ip' not set!")
        return relay_public_ip

    @property
    def relay_public_port(self) -> int:
        relay_public_port = int(os.getenv(
            "BLOCKPERF_RELAY_PUBLIC_PORT",
            self.config_parser.get("DEFAULT", "relay_public_port", fallback=3001)
        ))
        return relay_public_port

    @property
    def client_cert(self) -> str:
        client_cert = os.getenv(
            "BLOCKPERF_CLIENT_CERT",
            self.config_parser.get("DEFAULT", "client_cert", fallback=None)
        )
        if not client_cert:
            raise ConfigError("No client_cert set")
        return client_cert

    @property
    def client_key(self) -> str:
        client_key = os.getenv(
            "BLOCKPERF_CLIENT_KEY",
            self.config_parser.get("DEFAULT", "client_key", fallback=None)
        )
        if not client_key:
            raise ConfigError("No client_key set")
        return client_key

    @property
    def operator(self) -> str:
        operator = os.getenv(
            "BLOCKPERF_OPERATOR",
            self.config_parser.get("DEFAULT", "operator", fallback=None)
        )
        if not operator:
            raise ConfigError("No operator set")
        return operator

    @property
    def topic_base(self) -> str:
        topic_base = os.getenv(
            "BLOCKPERF_TOPIC_BASE",
            self.config_parser.get("DEFAULT", "topic_base", fallback="develop")
        )
        return topic_base

    @property
    def mqtt_broker_url(self) -> str:
        broker_url = os.getenv(
            "BLOCKPERF_BROKER_URL",
            self.config_parser.get(
                "DEFAULT",
                "mqtt_broker_url",
                fallback="a12j2zhynbsgdv-ats.iot.eu-central-1.amazonaws.com",
            )
        )
        return broker_url

    @property
    def mqtt_broker_port(self) -> int:
        broker_port = int(os.getenv(
            "BLOCKPERF_BROKER_PORT",
            self.config_parser.get("DEFAULT", "mqtt_broker_port", fallback=8883)
        ))
        return broker_port

    @property
    def enable_tracelogs(self) -> bool:
        return bool(self.config_parser.get("DEFAULT", "enable_tracelogs", fallback=False))

    @property
    def tracelogs_dir(self) -> str:
        return str(self.config_parser.get("DEFAULT", "tracelogs_dir", fallback=""))

    @property
    def topic(self) -> str:
        return f"{self.topic_base}/{self.operator}/{self.relay_public_ip}"
    # def _read_config(self, config: ConfigParser):
    #    """ """
    #    # Try to check whether CN of cert matches given operator
    #    cert = x509.load_pem_x509_certificate(Path(self.client_cert).read_bytes())
    #    name_attribute = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME).pop()
    #    assert (
    #        name_attribute.value == self.operator
    #    ), "Given operator does not match CN in certificate"
