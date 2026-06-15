"""Simulator-connector configuration: config/sim.json + CLI merge.

A single file lists every connector; the server streams to all that are
enabled. CLI flags enable/override individual connectors for quick local runs.
Precedence: --no-sim > per-sim CLI flag > file > per-type defaults.

A connector's ``type`` is the *product* (gspro, opengolfsim). For OpenGolfSim,
``transport`` selects how it's reached:
  - "openconnect": OGS's OpenConnect plugin on 921 (shots + club sync)
  - "native":      OGS's native API on 3111 (shots only)
GSPro is always OpenConnect.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_CONFIG_PATH = Path("config/sim.json")

KNOWN_TYPES: Tuple[str, ...] = ("gspro", "opengolfsim")
OGS_TRANSPORTS: Tuple[str, ...] = ("openconnect", "native")
DEFAULT_OGS_TRANSPORT = "openconnect"

_COMMON_DEFAULTS = {"device_id": "OpenFlight", "heartbeat_interval_s": 5.0}

# Per-(type, transport) defaults applied when a field is absent from file/CLI.
# OpenGolfSim serves OpenConnect V1 from its built-in Developer API on 3111 (it
# speaks both OpenConnect and native there) — there is no separate 921 server —
# so both OGS transports default to 3111. GSPro proper uses 921.
_DEFAULTS: Dict[Tuple[str, str], dict] = {
    ("gspro", "openconnect"): {"port": 921, "units": "Yards"},
    ("opengolfsim", "openconnect"): {"port": 3111, "units": "Yards"},
    ("opengolfsim", "native"): {"port": 3111, "units": "imperial"},
}


@dataclass
class ConnectorConfig:
    """One resolved simulator endpoint."""

    type: str
    transport: str = DEFAULT_OGS_TRANSPORT
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 0
    units: str = "Yards"
    device_id: str = "OpenFlight"
    heartbeat_interval_s: float = 5.0


def _resolve_transport(connector_type: str, data: dict) -> str:
    """GSPro is always OpenConnect; OGS may pick openconnect (default) or native."""
    if connector_type == "gspro":
        return "openconnect"
    transport = str(data.get("transport", DEFAULT_OGS_TRANSPORT))
    if transport not in OGS_TRANSPORTS:
        raise ValueError(
            f"unknown transport {transport!r} for {connector_type}; "
            f"expected one of {OGS_TRANSPORTS}"
        )
    return transport


def _with_defaults(connector_type: str, data: dict) -> ConnectorConfig:
    transport = _resolve_transport(connector_type, data)
    base = dict(_COMMON_DEFAULTS)
    base.update(_DEFAULTS[(connector_type, transport)])
    base.update(data)
    return ConnectorConfig(
        type=connector_type,
        transport=transport,
        enabled=bool(base.get("enabled", False)),
        host=str(base.get("host", "127.0.0.1")),
        port=int(base["port"]),
        units=str(base.get("units", "Yards")),
        device_id=str(base.get("device_id", "OpenFlight")),
        heartbeat_interval_s=float(base.get("heartbeat_interval_s", 5.0)),
    )


def _parse_cli_value(cli_value: str) -> Tuple[str, Optional[int]]:
    """Parse 'host' or 'host:port' into (host, port)."""
    parts = cli_value.split(":")
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 2:
        try:
            return parts[0], int(parts[1])
        except ValueError as e:
            raise ValueError(f"Invalid port in {cli_value!r}: {e}") from e
    raise ValueError(f"Invalid value {cli_value!r}: expected 'host' or 'host:port'")


def load_sim_config(
    gspro: Optional[str] = None,
    opengolfsim: Optional[str] = None,
    no_sim: bool = False,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> List[ConnectorConfig]:
    """Resolve the enabled connector configs. Returns only enabled connectors.

    ``gspro``/``opengolfsim`` are CLI overrides of the form 'host[:port]' that
    also force that connector enabled (``--opengolfsim`` uses the OpenConnect
    transport by default; set "transport": "native" in the file for 3111).
    ``no_sim`` disables everything.
    """
    by_type: Dict[str, ConnectorConfig] = {}

    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        for entry in data.get("connectors", []):
            ctype = entry.get("type")
            if ctype not in KNOWN_TYPES:
                raise ValueError(f"unknown simulator type in {config_path}: {ctype!r}")
            by_type[ctype] = _with_defaults(ctype, entry)

    cli_overrides = {"gspro": gspro, "opengolfsim": opengolfsim}
    for ctype, cli_value in cli_overrides.items():
        if cli_value is None:
            continue
        host, port = _parse_cli_value(cli_value)
        cfg = by_type.get(ctype) or _with_defaults(ctype, {})
        cfg.host = host
        if port is not None:
            cfg.port = port
        cfg.enabled = True
        by_type[ctype] = cfg

    if no_sim:
        for cfg in by_type.values():
            cfg.enabled = False

    return [cfg for cfg in by_type.values() if cfg.enabled]
