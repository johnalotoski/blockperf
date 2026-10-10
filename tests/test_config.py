import os
import pytest
import json
import blockperf
from unittest import mock
from blockperf.config import AppConfig, ConfigError, NETWORK_STARTTIMES


def _config_with_genesis(genesis):
    """Build an AppConfig without running __init__ and stub out the Shelley
    genesis data so the network_* properties can be exercised in isolation."""
    cfg = object.__new__(AppConfig)
    return cfg, mock.patch.object(
        AppConfig,
        "_shelley_genesis_data",
        new_callable=mock.PropertyMock,
        return_value=genesis,
    )


def test_network_start_time_known_network():
    """mainnet uses the precomputed Byron-era-adjusted reference."""
    cfg, patched = _config_with_genesis({"networkMagic": 764824073})
    with patched:
        assert cfg.network_start_time == NETWORK_STARTTIMES[764824073]


def test_network_start_time_derived_for_arbitrary_network():
    """An unknown network (e.g. leios, magic 164) derives its reference from
    the Shelley genesis systemStart, assuming 1-second slots from slot 0."""
    cfg, patched = _config_with_genesis(
        {
            "networkMagic": 164,
            "slotLength": 1,
            "systemStart": "2024-01-01T00:00:00Z",
        }
    )
    with patched:
        assert cfg.network_start_time == 1704067200


def test_network_start_time_missing_systemstart_exits():
    """An unknown network without a systemStart cannot be supported."""
    cfg, patched = _config_with_genesis({"networkMagic": 999})
    with patched, pytest.raises(SystemExit):
        cfg.network_start_time


def test_config_file_defaults():
    with pytest.raises(SystemExit):
        app_config = AppConfig(None)
        assert (
            app_config.node_config_file.as_posix()
            == "/opt/cardano/cnode/files/config.json"
        )
        assert app_config.node_configdir.as_posix() == "/opt/cardano/cnode/files"


def _test_other():
    os.environ["BLOCKPERF_NODE_CONFIG"] = "/this/config/does/not/exist"
    app_config = AppConfig(None)
    with pytest.raises(ConfigError) as e:
        config_file = app_config.node_config_file
        # assert config_file


def _test_shelley_genesis_file():
    app_config = AppConfig(None)
    _f = app_config._shelley_genesis_file
    assert _f == "shelley-genesis.json"


def test_active_slot_coef():
    with pytest.raises(SystemExit):
        app_config = AppConfig(None)
