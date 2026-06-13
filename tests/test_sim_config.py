"""Tests for sim.config — config/sim.json + CLI merge and precedence."""
import json

import pytest

from openflight.sim.config import load_sim_config


def _write(tmp_path, obj):
    p = tmp_path / "sim.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_missing_file_means_no_connectors(tmp_path):
    cfgs = load_sim_config(config_path=tmp_path / "absent.json")
    assert cfgs == []


def test_file_enabled_connector_loaded(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": True, "host": "10.0.0.5", "port": 921},
    ]})
    cfgs = load_sim_config(config_path=p)
    assert len(cfgs) == 1
    assert cfgs[0].type == "gspro"
    assert cfgs[0].host == "10.0.0.5"
    assert cfgs[0].units == "Yards"  # per-type default


def test_disabled_connectors_excluded(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": False, "port": 921},
        {"type": "opengolfsim", "enabled": True, "port": 3111},
    ]})
    cfgs = load_sim_config(config_path=p)
    assert [c.type for c in cfgs] == ["opengolfsim"]


def test_cli_enables_and_overrides_host_port(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": False, "host": "1.1.1.1", "port": 921},
    ]})
    cfgs = load_sim_config(gspro="192.168.1.50:9000", config_path=p)
    assert len(cfgs) == 1
    assert cfgs[0].host == "192.168.1.50"
    assert cfgs[0].port == 9000


def test_cli_without_port_keeps_default_port(tmp_path):
    cfgs = load_sim_config(opengolfsim="192.168.1.9", config_path=tmp_path / "absent.json")
    assert len(cfgs) == 1
    assert cfgs[0].type == "opengolfsim"
    assert cfgs[0].port == 3111  # default for opengolfsim


def test_no_sim_disables_everything(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": True, "port": 921},
    ]})
    cfgs = load_sim_config(gspro="1.2.3.4", no_sim=True, config_path=p)
    assert cfgs == []


def test_multiple_connectors_both_enabled(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": True, "port": 921},
        {"type": "opengolfsim", "enabled": True, "port": 3111},
    ]})
    cfgs = load_sim_config(config_path=p)
    assert {c.type for c in cfgs} == {"gspro", "opengolfsim"}


def test_unknown_type_in_file_raises(tmp_path):
    p = _write(tmp_path, {"connectors": [{"type": "bogus", "port": 1}]})
    with pytest.raises(ValueError):
        load_sim_config(config_path=p)


def test_bad_cli_port_raises(tmp_path):
    with pytest.raises(ValueError):
        load_sim_config(gspro="host:notaport", config_path=tmp_path / "absent.json")
