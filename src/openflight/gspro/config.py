"""GSPro client configuration loader (file + CLI merge)."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_PATH = Path("config/gspro.json")
DEFAULT_PORT = 921


@dataclass
class GSProConfig:
    """Resolved GSPro client configuration."""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    device_id: str = "OpenFlight"
    units: str = "Yards"
    heartbeat_interval_s: float = 5.0


def _parse_cli_value(cli_value: str) -> tuple[str, Optional[int]]:
    """Parse '--gspro host' or '--gspro host:port' into (host, port)."""
    parts = cli_value.split(":")
    if len(parts) == 1:
        return parts[0], None
    if len(parts) == 2:
        try:
            return parts[0], int(parts[1])
        except ValueError as e:
            raise ValueError(f"Invalid port in --gspro {cli_value!r}: {e}") from e
    raise ValueError(f"Invalid --gspro value {cli_value!r}: expected 'host' or 'host:port'")


def load_gspro_config(
    cli_value: Optional[str],
    no_gspro: bool,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> GSProConfig:
    """Merge defaults < file < CLI flags. --no-gspro wins over everything."""
    cfg = GSProConfig()
    if config_path.exists():
        data = json.loads(config_path.read_text())
        for key in ("enabled", "host", "port", "device_id", "units", "heartbeat_interval_s"):
            if key in data:
                setattr(cfg, key, data[key])
    if cli_value is not None:
        host, port = _parse_cli_value(cli_value)
        cfg.host = host
        if port is not None:
            cfg.port = port
        cfg.enabled = True
    if no_gspro:
        cfg.enabled = False
    return cfg
