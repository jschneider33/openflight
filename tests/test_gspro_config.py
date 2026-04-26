"""Tests for src/openflight/gspro/config.py."""
import json
from pathlib import Path

import pytest

from openflight.gspro.config import GSProConfig, load_gspro_config


def test_missing_file_returns_disabled(tmp_path):
    cfg = load_gspro_config(cli_value=None, no_gspro=False, config_path=tmp_path / "missing.json")
    assert cfg.enabled is False
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 921
    assert cfg.heartbeat_interval_s == 5
    assert cfg.device_id == "OpenFlight"
    assert cfg.units == "Yards"


def test_loads_from_file(tmp_path):
    path = tmp_path / "gspro.json"
    path.write_text(json.dumps({
        "enabled": True, "host": "10.0.0.5", "port": 9000,
        "device_id": "Test", "units": "Meters", "heartbeat_interval_s": 2,
    }))
    cfg = load_gspro_config(cli_value=None, no_gspro=False, config_path=path)
    assert cfg.enabled is True
    assert cfg.host == "10.0.0.5"
    assert cfg.port == 9000
    assert cfg.units == "Meters"
    assert cfg.heartbeat_interval_s == 2


def test_cli_overrides_file_host_only(tmp_path):
    path = tmp_path / "gspro.json"
    path.write_text(json.dumps({"enabled": False, "host": "1.1.1.1", "port": 921}))
    cfg = load_gspro_config(cli_value="2.2.2.2", no_gspro=False, config_path=path)
    assert cfg.enabled is True  # CLI flag implies enabled
    assert cfg.host == "2.2.2.2"
    assert cfg.port == 921  # default kept


def test_cli_overrides_file_host_port(tmp_path):
    cfg = load_gspro_config(cli_value="2.2.2.2:9000", no_gspro=False, config_path=tmp_path / "x.json")
    assert cfg.enabled is True
    assert cfg.host == "2.2.2.2"
    assert cfg.port == 9000


def test_no_gspro_overrides_everything(tmp_path):
    path = tmp_path / "gspro.json"
    path.write_text(json.dumps({"enabled": True, "host": "1.1.1.1", "port": 921}))
    cfg = load_gspro_config(cli_value="2.2.2.2", no_gspro=True, config_path=path)
    assert cfg.enabled is False


def test_invalid_cli_value_raises():
    with pytest.raises(ValueError):
        load_gspro_config(cli_value="bad:port:format", no_gspro=False, config_path=Path("/dev/null"))
    with pytest.raises(ValueError):
        load_gspro_config(cli_value="host:notaport", no_gspro=False, config_path=Path("/dev/null"))
