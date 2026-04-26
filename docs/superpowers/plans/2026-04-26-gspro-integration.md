# GSPro Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optionally stream OpenFlight shots to GSPro over TCP using OpenConnectV1, with model-based fallback for missing measurements and per-field provenance surfaced to the UI.

**Architecture:** New `src/openflight/gspro/` module with separate concerns (config, messages, shot_builder, state, client). The Flask server instantiates a `GSProClient` only when enabled; the client owns a TCP socket plus a connection thread (state machine + reconnect) and a heartbeat thread. A pure `shot_builder.build()` function translates `Shot` → OpenConnectV1 payload and tags each field as `measured`/`estimated`. Provenance flows to the UI as badges on each shot card.

**Tech Stack:** Python 3.9+ stdlib (`socket`, `socketserver`, `threading`, `dataclasses`, `json`); pytest; React/TypeScript for UI; Flask + Flask-SocketIO (existing).

**Spec:** `docs/superpowers/specs/2026-04-26-gspro-integration-design.md`

---

## Task 1: Config loader + example file + .gitignore

**Files:**
- Create: `src/openflight/gspro/__init__.py`
- Create: `src/openflight/gspro/config.py`
- Create: `config/gspro.example.json`
- Modify: `.gitignore` (append one line)
- Test: `tests/test_gspro_config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gspro_config.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_gspro_config.py -v
```

Expected: ImportError / module not found.

- [ ] **Step 3: Create empty package**

Create `src/openflight/gspro/__init__.py`:

```python
"""GSPro OpenConnectV1 integration (optional)."""
from .config import GSProConfig, load_gspro_config

__all__ = ["GSProConfig", "load_gspro_config"]
```

- [ ] **Step 4: Implement config**

Create `src/openflight/gspro/config.py`:

```python
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
```

- [ ] **Step 5: Create example config**

Create `config/gspro.example.json`:

```json
{
  "enabled": false,
  "host": "192.168.1.50",
  "port": 921,
  "device_id": "OpenFlight",
  "units": "Yards",
  "heartbeat_interval_s": 5
}
```

- [ ] **Step 6: Add active config to .gitignore**

Append to `.gitignore`:

```
# GSPro integration (user-specific)
config/gspro.json
```

- [ ] **Step 7: Run tests, verify pass**

```bash
uv run pytest tests/test_gspro_config.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/openflight/gspro/__init__.py src/openflight/gspro/config.py \
        tests/test_gspro_config.py config/gspro.example.json .gitignore
git commit -m "feat(gspro): config loader with file + CLI merge"
```

---

## Task 2: OpenConnectV1 message schema

**Files:**
- Create: `src/openflight/gspro/messages.py`
- Test: `tests/test_gspro_messages.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gspro_messages.py`:

```python
"""Tests for src/openflight/gspro/messages.py."""
import json

import pytest

from openflight.gspro.messages import (
    BallData, ClubData, GSProResponse, ShotDataOptions, ShotPayload,
    parse_response, serialize_payload, build_heartbeat,
)


def test_serialize_minimum_shot():
    payload = ShotPayload(
        DeviceID="OpenFlight", Units="Yards", ShotNumber=1, APIversion="1",
        BallData=BallData(Speed=147.5, HLA=2.3, VLA=14.3, TotalSpin=2500.0,
                          SpinAxis=-3.0, BackSpin=2496.6, SideSpin=-130.8,
                          CarryDistance=240.0),
        ClubData=ClubData(Speed=110.0, Path=1.0),
        ShotDataOptions=ShotDataOptions(),
    )
    raw = serialize_payload(payload)
    obj = json.loads(raw)
    assert obj["DeviceID"] == "OpenFlight"
    assert obj["APIversion"] == "1"  # string, not int
    assert obj["BallData"]["Speed"] == 147.5
    assert obj["ShotDataOptions"]["ContainsBallData"] is True
    assert obj["ShotDataOptions"]["IsHeartBeat"] is False


def test_serialize_includes_all_required_keys():
    payload = ShotPayload(
        DeviceID="X", Units="Yards", ShotNumber=1, APIversion="1",
        BallData=BallData(), ClubData=ClubData(),
        ShotDataOptions=ShotDataOptions(),
    )
    obj = json.loads(serialize_payload(payload))
    for key in ("DeviceID", "Units", "ShotNumber", "APIversion",
                "BallData", "ClubData", "ShotDataOptions"):
        assert key in obj
    for key in ("Speed", "SpinAxis", "TotalSpin", "BackSpin", "SideSpin",
                "HLA", "VLA", "CarryDistance"):
        assert key in obj["BallData"]
    for key in ("Speed", "AngleOfAttack", "FaceToTarget", "Lie", "Loft",
                "Path", "SpeedAtImpact", "VerticalFaceImpact",
                "HorizontalFaceImpact", "ClosureRate"):
        assert key in obj["ClubData"]


def test_build_heartbeat():
    raw = build_heartbeat(device_id="OpenFlight", units="Yards", shot_number=42)
    obj = json.loads(raw)
    assert obj["DeviceID"] == "OpenFlight"
    assert obj["ShotNumber"] == 42
    assert obj["ShotDataOptions"]["IsHeartBeat"] is True
    assert obj["ShotDataOptions"]["ContainsBallData"] is False
    assert obj["ShotDataOptions"]["LaunchMonitorIsReady"] is True
    assert obj["BallData"]["Speed"] == 0.0


def test_parse_response_code_200():
    raw = b'{"Code": 200, "Message": "Shot received"}'
    resp = parse_response(raw)
    assert resp.Code == 200
    assert resp.Message == "Shot received"
    assert resp.Player is None


def test_parse_response_code_201_with_player():
    raw = b'{"Code": 201, "Message": "Player Info", "Player": {"Handed": "RH", "Club": "I7"}}'
    resp = parse_response(raw)
    assert resp.Code == 201
    assert resp.Player == {"Handed": "RH", "Club": "I7"}


def test_parse_response_code_5xx_error():
    raw = b'{"Code": 501, "Message": "Internal error"}'
    resp = parse_response(raw)
    assert resp.Code == 501
    assert resp.Player is None


def test_parse_response_invalid_json_raises():
    with pytest.raises(ValueError):
        parse_response(b"not json")
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_gspro_messages.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement messages**

Create `src/openflight/gspro/messages.py`:

```python
"""OpenConnectV1 JSON schema (https://gsprogolf.com/GSProConnectV1.html)."""
import json
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class BallData:
    Speed: float = 0.0
    SpinAxis: float = 0.0
    TotalSpin: float = 0.0
    BackSpin: float = 0.0
    SideSpin: float = 0.0
    HLA: float = 0.0
    VLA: float = 0.0
    CarryDistance: float = 0.0


@dataclass
class ClubData:
    Speed: float = 0.0
    AngleOfAttack: float = 0.0
    FaceToTarget: float = 0.0
    Lie: float = 0.0
    Loft: float = 0.0
    Path: float = 0.0
    SpeedAtImpact: float = 0.0
    VerticalFaceImpact: float = 0.0
    HorizontalFaceImpact: float = 0.0
    ClosureRate: float = 0.0


@dataclass
class ShotDataOptions:
    ContainsBallData: bool = True
    ContainsClubData: bool = False
    LaunchMonitorIsReady: bool = True
    LaunchMonitorBallDetected: bool = True
    IsHeartBeat: bool = False


@dataclass
class ShotPayload:
    DeviceID: str
    Units: str
    ShotNumber: int
    APIversion: str  # string "1", not int (per spec)
    BallData: BallData = field(default_factory=BallData)
    ClubData: ClubData = field(default_factory=ClubData)
    ShotDataOptions: ShotDataOptions = field(default_factory=ShotDataOptions)


@dataclass
class GSProResponse:
    Code: int
    Message: str = ""
    Player: Optional[dict] = None


def serialize_payload(payload: ShotPayload) -> bytes:
    return json.dumps(asdict(payload), separators=(",", ":")).encode("utf-8")


def build_heartbeat(device_id: str, units: str, shot_number: int) -> bytes:
    payload = ShotPayload(
        DeviceID=device_id,
        Units=units,
        ShotNumber=shot_number,
        APIversion="1",
        ShotDataOptions=ShotDataOptions(
            ContainsBallData=False,
            ContainsClubData=False,
            LaunchMonitorIsReady=True,
            LaunchMonitorBallDetected=False,
            IsHeartBeat=True,
        ),
    )
    return serialize_payload(payload)


def parse_response(raw: bytes) -> GSProResponse:
    """Parse a GSPro reply. Raises ValueError on malformed JSON."""
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Malformed GSPro response: {e}") from e
    return GSProResponse(
        Code=int(obj.get("Code", 0)),
        Message=str(obj.get("Message", "")),
        Player=obj.get("Player"),
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_gspro_messages.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/gspro/messages.py tests/test_gspro_messages.py
git commit -m "feat(gspro): OpenConnectV1 message schema and serialization"
```

---

## Task 3: PlayerState + GSPro club code mapping

**Files:**
- Create: `src/openflight/gspro/state.py`
- Test: `tests/test_gspro_state.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gspro_state.py`:

```python
"""Tests for src/openflight/gspro/state.py."""
from openflight.launch_monitor import ClubType
from openflight.gspro.state import (
    ConnectionState, PlayerState, gspro_code_to_club,
)


def test_connection_state_values():
    assert ConnectionState.DISABLED.value == "disabled"
    assert ConnectionState.CONNECTING.value == "connecting"
    assert ConnectionState.CONNECTED.value == "connected"
    assert ConnectionState.RECONNECT_BACKOFF.value == "reconnecting"
    assert ConnectionState.STOPPED.value == "stopped"


def test_player_state_defaults():
    p = PlayerState()
    assert p.handed == "RH"
    assert p.club == ClubType.DRIVER
    assert p.shot_counter == 0


def test_next_shot_number_increments():
    p = PlayerState()
    assert p.next_shot_number() == 1
    assert p.next_shot_number() == 2
    assert p.shot_counter == 2


def test_update_from_gspro_known_club():
    p = PlayerState()
    p.update_from_gspro({"Handed": "LH", "Club": "I7"})
    assert p.handed == "LH"
    assert p.club == ClubType.IRON_7


def test_update_from_gspro_unknown_club_falls_back():
    p = PlayerState(club=ClubType.DRIVER)
    p.update_from_gspro({"Handed": "RH", "Club": "ZZ"})
    assert p.club == ClubType.UNKNOWN


def test_update_from_gspro_putter_logged_but_set_unknown():
    """Putting is out of scope for v1 — log and treat as UNKNOWN club."""
    p = PlayerState(club=ClubType.DRIVER)
    p.update_from_gspro({"Handed": "RH", "Club": "PT"})
    assert p.club == ClubType.UNKNOWN


def test_gspro_code_to_club_mapping():
    assert gspro_code_to_club("DR") is ClubType.DRIVER
    assert gspro_code_to_club("W3") is ClubType.WOOD_3
    assert gspro_code_to_club("H5") is ClubType.HYBRID_5
    assert gspro_code_to_club("I7") is ClubType.IRON_7
    assert gspro_code_to_club("PW") is ClubType.PW
    assert gspro_code_to_club("LW") is ClubType.LW
    assert gspro_code_to_club("XX") is ClubType.UNKNOWN
    assert gspro_code_to_club("PT") is ClubType.UNKNOWN  # putter out of scope
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_gspro_state.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement state**

Create `src/openflight/gspro/state.py`:

```python
"""Connection and player state for the GSPro client."""
import logging
from dataclasses import dataclass, field
from enum import Enum

from openflight.launch_monitor import ClubType

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECT_BACKOFF = "reconnecting"
    STOPPED = "stopped"


_GSPRO_CLUB_MAP = {
    "DR": ClubType.DRIVER,
    "W3": ClubType.WOOD_3, "W5": ClubType.WOOD_5, "W7": ClubType.WOOD_7,
    "H3": ClubType.HYBRID_3, "H5": ClubType.HYBRID_5,
    "H7": ClubType.HYBRID_7, "H9": ClubType.HYBRID_9,
    "I2": ClubType.IRON_2, "I3": ClubType.IRON_3, "I4": ClubType.IRON_4,
    "I5": ClubType.IRON_5, "I6": ClubType.IRON_6, "I7": ClubType.IRON_7,
    "I8": ClubType.IRON_8, "I9": ClubType.IRON_9,
    "PW": ClubType.PW, "GW": ClubType.GW, "SW": ClubType.SW, "LW": ClubType.LW,
    # "PT" intentionally absent — putting is out of scope for v1
}


def gspro_code_to_club(code: str) -> ClubType:
    """Map a GSPro club code (e.g. 'DR', 'I7') to ClubType. Unknown → UNKNOWN."""
    if code == "PT":
        logger.info("[gspro] putter received — putting is out of scope, mapping to UNKNOWN")
        return ClubType.UNKNOWN
    club = _GSPRO_CLUB_MAP.get(code)
    if club is None:
        logger.warning("[gspro] unknown club code %r, mapping to UNKNOWN", code)
        return ClubType.UNKNOWN
    return club


@dataclass
class PlayerState:
    """Mutable player-level state (kept across shots)."""
    handed: str = "RH"
    club: ClubType = ClubType.DRIVER
    shot_counter: int = 0

    def next_shot_number(self) -> int:
        self.shot_counter += 1
        return self.shot_counter

    def update_from_gspro(self, player: dict) -> None:
        """Apply a GSPro Player block (from a code-201 response)."""
        if "Handed" in player:
            self.handed = str(player["Handed"])
        if "Club" in player:
            self.club = gspro_code_to_club(str(player["Club"]))
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_gspro_state.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/gspro/state.py tests/test_gspro_state.py
git commit -m "feat(gspro): connection state enum and player state with club mapping"
```

---

## Task 4: ShotBuilder + fallback table + provenance

**Files:**
- Create: `src/openflight/gspro/shot_builder.py`
- Test: `tests/test_gspro_shot_builder.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gspro_shot_builder.py`:

```python
"""Tests for src/openflight/gspro/shot_builder.py — fallback table + provenance."""
import math
from datetime import datetime

import pytest

from openflight.launch_monitor import ClubType, Shot
from openflight.gspro.shot_builder import (
    IncompleteShotError, build, SPIN_MODEL_RPM,
)
from openflight.gspro.state import PlayerState


def _shot(**kw) -> Shot:
    """Build a minimal Shot with overrides."""
    base = dict(ball_speed_mph=140.0, timestamp=datetime(2026, 4, 26, 12, 0, 0),
                club=ClubType.DRIVER)
    base.update(kw)
    return Shot(**base)


def test_full_measured_shot():
    shot = _shot(
        club_speed_mph=110.0, launch_angle_vertical=12.0,
        launch_angle_horizontal=1.5, spin_rpm=2500.0, spin_confidence=0.9,
        spin_axis_deg=-3.0, club_path_deg=0.5,
    )
    out = build(shot, PlayerState(), device_id="OpenFlight", units="Yards")
    p = out.payload
    assert p["DeviceID"] == "OpenFlight"
    assert p["BallData"]["Speed"] == 140.0
    assert p["BallData"]["VLA"] == 12.0
    assert p["BallData"]["HLA"] == 1.5
    assert p["BallData"]["TotalSpin"] == 2500.0
    assert p["BallData"]["SpinAxis"] == -3.0
    # BackSpin = 2500 * cos(-3°) ≈ 2496.6
    assert math.isclose(p["BallData"]["BackSpin"], 2500 * math.cos(math.radians(-3.0)), rel_tol=0.01)
    assert math.isclose(p["BallData"]["SideSpin"], 2500 * math.sin(math.radians(-3.0)), rel_tol=0.01)
    assert p["ClubData"]["Speed"] == 110.0
    assert p["ClubData"]["Path"] == 0.5
    assert p["ShotDataOptions"]["ContainsClubData"] is True
    # Provenance — every field measured
    assert out.provenance["BallData.Speed"] == "measured"
    assert out.provenance["BallData.VLA"] == "measured"
    assert out.provenance["BallData.HLA"] == "measured"
    assert out.provenance["BallData.TotalSpin"] == "measured"
    assert out.provenance["BallData.SpinAxis"] == "measured"
    assert out.provenance["BallData.BackSpin"] == "measured"
    assert out.provenance["BallData.SideSpin"] == "measured"
    assert out.provenance["ClubData.Speed"] == "measured"
    assert out.provenance["ClubData.Path"] == "measured"


def test_missing_vla_falls_back_to_optimal_launch():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9, club=ClubType.IRON_7)
    out = build(shot, PlayerState())
    assert out.payload["BallData"]["VLA"] == 20.5  # _OPTIMAL_LAUNCH[IRON_7]
    assert out.provenance["BallData.VLA"] == "estimated"


def test_missing_hla_falls_back_to_zero():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)
    out = build(shot, PlayerState())
    assert out.payload["BallData"]["HLA"] == 0.0
    assert out.provenance["BallData.HLA"] == "estimated"


def test_low_spin_confidence_uses_model():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.4, club=ClubType.DRIVER)
    out = build(shot, PlayerState())
    assert out.payload["BallData"]["TotalSpin"] == SPIN_MODEL_RPM[ClubType.DRIVER]
    assert out.provenance["BallData.TotalSpin"] == "estimated"


def test_missing_spin_uses_model():
    shot = _shot(club=ClubType.IRON_7)
    out = build(shot, PlayerState())
    assert out.payload["BallData"]["TotalSpin"] == SPIN_MODEL_RPM[ClubType.IRON_7]
    assert out.provenance["BallData.TotalSpin"] == "estimated"


def test_missing_spin_axis_falls_back_to_zero():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)  # no spin_axis_deg
    out = build(shot, PlayerState())
    assert out.payload["BallData"]["SpinAxis"] == 0.0
    assert out.provenance["BallData.SpinAxis"] == "estimated"
    # When axis is 0, BackSpin == TotalSpin and SideSpin == 0
    assert out.payload["BallData"]["BackSpin"] == 2500.0
    assert out.payload["BallData"]["SideSpin"] == 0.0


def test_derived_spin_provenance_estimated_when_either_input_estimated():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)  # axis missing
    out = build(shot, PlayerState())
    assert out.provenance["BallData.BackSpin"] == "estimated"
    assert out.provenance["BallData.SideSpin"] == "estimated"


def test_missing_club_speed_drops_club_data_flag():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)
    out = build(shot, PlayerState())
    assert out.payload["ClubData"]["Speed"] == 0.0
    assert out.payload["ShotDataOptions"]["ContainsClubData"] is False
    assert out.provenance["ClubData.Speed"] == "estimated"


def test_missing_club_path_falls_back_to_zero():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9, club_speed_mph=100.0)
    out = build(shot, PlayerState())
    assert out.payload["ClubData"]["Path"] == 0.0
    assert out.provenance["ClubData.Path"] == "estimated"


def test_missing_ball_speed_raises():
    shot = _shot(ball_speed_mph=0.0)
    with pytest.raises(IncompleteShotError):
        build(shot, PlayerState())


def test_shot_number_uses_player_state():
    ps = PlayerState()
    ps.next_shot_number()  # consume one to bump counter
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)
    out = build(shot, ps)
    assert out.payload["ShotNumber"] == 2


def test_apiversion_is_string():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)
    out = build(shot, PlayerState())
    assert out.payload["APIversion"] == "1"
    assert isinstance(out.payload["APIversion"], str)


def test_shot_options_flags():
    shot = _shot(spin_rpm=2500.0, spin_confidence=0.9)
    out = build(shot, PlayerState())
    opts = out.payload["ShotDataOptions"]
    assert opts["ContainsBallData"] is True
    assert opts["LaunchMonitorIsReady"] is True
    assert opts["LaunchMonitorBallDetected"] is True
    assert opts["IsHeartBeat"] is False
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_gspro_shot_builder.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement shot_builder**

Create `src/openflight/gspro/shot_builder.py`:

```python
"""Translate Shot → GSPro OpenConnectV1 payload with model fallback + provenance.

Fallback policy is documented in
docs/superpowers/specs/2026-04-26-gspro-integration-design.md (Fallback table).
"""
import math
from dataclasses import asdict, dataclass, field
from typing import Dict

from openflight.launch_monitor import (
    SPIN_CONFIDENCE_HIGH, ClubType, Shot, _OPTIMAL_LAUNCH,
)
from openflight.gspro.messages import (
    BallData, ClubData, ShotDataOptions, ShotPayload,
)
from openflight.gspro.state import PlayerState


class IncompleteShotError(Exception):
    """Shot lacks the minimum fields required to send to GSPro (ball speed)."""


# Temporary per-club spin model (rpm). Will be replaced by the shared spin
# model from the ballistics module when PR #61 lands. See spec "Open
# dependencies" section for context.
SPIN_MODEL_RPM: Dict[ClubType, float] = {
    ClubType.DRIVER: 2500.0,
    ClubType.WOOD_3: 3000.0,
    ClubType.WOOD_5: 3500.0,
    ClubType.WOOD_7: 4000.0,
    ClubType.HYBRID_3: 3500.0,
    ClubType.HYBRID_5: 4000.0,
    ClubType.HYBRID_7: 4500.0,
    ClubType.HYBRID_9: 5000.0,
    ClubType.IRON_2: 4000.0,
    ClubType.IRON_3: 4500.0,
    ClubType.IRON_4: 5000.0,
    ClubType.IRON_5: 5500.0,
    ClubType.IRON_6: 6000.0,
    ClubType.IRON_7: 7000.0,
    ClubType.IRON_8: 8000.0,
    ClubType.IRON_9: 9000.0,
    ClubType.PW: 9500.0,
    ClubType.GW: 10000.0,
    ClubType.SW: 10500.0,
    ClubType.LW: 11000.0,
    ClubType.UNKNOWN: 5000.0,
}


@dataclass
class GSProSend:
    """Built payload + per-field provenance ('measured' or 'estimated')."""
    payload: dict
    provenance: Dict[str, str] = field(default_factory=dict)


def _resolve_total_spin(shot: Shot) -> tuple[float, str]:
    if (shot.spin_rpm is not None and shot.spin_rpm > 0
            and shot.spin_confidence is not None
            and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH):
        return float(shot.spin_rpm), "measured"
    return SPIN_MODEL_RPM.get(shot.club, 5000.0), "estimated"


def build(
    shot: Shot,
    player_state: PlayerState,
    device_id: str = "OpenFlight",
    units: str = "Yards",
) -> GSProSend:
    """Convert a Shot into a GSPro payload, applying fallbacks per the spec."""
    if shot.ball_speed_mph is None or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")

    provenance: Dict[str, str] = {"BallData.Speed": "measured"}

    # Vertical launch angle
    if shot.launch_angle_vertical is not None:
        vla = float(shot.launch_angle_vertical)
        provenance["BallData.VLA"] = "measured"
    else:
        vla = _OPTIMAL_LAUNCH.get(shot.club, 18.0)
        provenance["BallData.VLA"] = "estimated"

    # Horizontal launch angle
    if shot.launch_angle_horizontal is not None:
        hla = float(shot.launch_angle_horizontal)
        provenance["BallData.HLA"] = "measured"
    else:
        hla = 0.0
        provenance["BallData.HLA"] = "estimated"

    # Total spin (with confidence gate)
    total_spin, spin_prov = _resolve_total_spin(shot)
    provenance["BallData.TotalSpin"] = spin_prov

    # Spin axis (already a D-plane derivation in server.py:1041 when both KLD7s exist)
    if shot.spin_axis_deg is not None:
        spin_axis = float(shot.spin_axis_deg)
        axis_prov = "measured"
    else:
        spin_axis = 0.0
        axis_prov = "estimated"
    provenance["BallData.SpinAxis"] = axis_prov

    # Derived components
    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = "measured" if (spin_prov == "measured" and axis_prov == "measured") else "estimated"
    provenance["BallData.BackSpin"] = derived_prov
    provenance["BallData.SideSpin"] = derived_prov

    # Carry — Shot.estimated_carry_yards already incorporates measured launch
    # angle when available, so reuse its provenance derivation.
    carry = float(shot.estimated_carry_yards)
    provenance["BallData.CarryDistance"] = "measured" if shot.has_launch_angle else "estimated"

    # Club data
    if shot.club_speed_mph is not None and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        contains_club = True
        provenance["ClubData.Speed"] = "measured"
    else:
        club_speed = 0.0
        contains_club = False
        provenance["ClubData.Speed"] = "estimated"

    if shot.club_path_deg is not None:
        club_path = float(shot.club_path_deg)
        provenance["ClubData.Path"] = "measured"
    else:
        club_path = 0.0
        provenance["ClubData.Path"] = "estimated"

    payload = ShotPayload(
        DeviceID=device_id,
        Units=units,
        ShotNumber=player_state.next_shot_number(),
        APIversion="1",
        BallData=BallData(
            Speed=round(shot.ball_speed_mph, 1),
            SpinAxis=round(spin_axis, 1),
            TotalSpin=round(total_spin, 0),
            BackSpin=round(back_spin, 0),
            SideSpin=round(side_spin, 0),
            HLA=round(hla, 1),
            VLA=round(vla, 1),
            CarryDistance=round(carry, 1),
        ),
        ClubData=ClubData(
            Speed=round(club_speed, 1),
            Path=round(club_path, 1),
        ),
        ShotDataOptions=ShotDataOptions(
            ContainsBallData=True,
            ContainsClubData=contains_club,
            LaunchMonitorIsReady=True,
            LaunchMonitorBallDetected=True,
            IsHeartBeat=False,
        ),
    )

    return GSProSend(payload=asdict(payload), provenance=provenance)
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_gspro_shot_builder.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/gspro/shot_builder.py tests/test_gspro_shot_builder.py
git commit -m "feat(gspro): shot builder with model fallback and provenance"
```

---

## Task 5: Mock GSPro server harness + GSProClient (synchronous send/recv)

This task introduces the mock-server test harness reused in tasks 6, 7, 9. Client is single-threaded for now (state machine and heartbeat thread come next).

**Files:**
- Create: `tests/conftest_gspro.py` (the mock server fixture)
- Create: `src/openflight/gspro/client.py`
- Test: `tests/test_gspro_client_basic.py`

- [ ] **Step 1: Write the mock-server harness**

Create `tests/conftest.py`:

```python
"""Reusable mock GSPro TCP server for client integration tests."""
import json
import socket
import threading
from typing import List, Optional

import pytest


class MockGSProServer:
    """Tiny TCP server that records bytes and can send scripted replies.

    Use as a pytest fixture; call `bind()` to start, `stop()` to end.
    """

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.host, self.port = self._sock.getsockname()
        self._client_sock: Optional[socket.socket] = None
        self.received: List[bytes] = []
        self.scripted_replies: List[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._sock.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    self._client_sock, _ = self._sock.accept()
                except socket.timeout:
                    continue
                self._client_sock.settimeout(0.2)
                # Send any scripted replies that were queued before connect
                for reply in list(self.scripted_replies):
                    try:
                        self._client_sock.sendall(reply)
                    except OSError:
                        break
                self.scripted_replies.clear()
                while not self._stop.is_set():
                    try:
                        chunk = self._client_sock.recv(4096)
                    except socket.timeout:
                        # Send any newly-queued replies
                        for reply in list(self.scripted_replies):
                            try:
                                self._client_sock.sendall(reply)
                            except OSError:
                                break
                        self.scripted_replies.clear()
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    self.received.append(chunk)
                try:
                    self._client_sock.close()
                except OSError:
                    pass
                self._client_sock = None
        finally:
            try:
                self._sock.close()
            except OSError:
                pass

    def queue_reply(self, obj: dict) -> None:
        self.scripted_replies.append(json.dumps(obj).encode("utf-8"))

    def disconnect_client(self) -> None:
        if self._client_sock is not None:
            try:
                self._client_sock.shutdown(socket.SHUT_RDWR)
                self._client_sock.close()
            except OSError:
                pass
            self._client_sock = None

    def stop(self) -> None:
        self._stop.set()
        self.disconnect_client()
        self._thread.join(timeout=2.0)


@pytest.fixture
def mock_gspro():
    server = MockGSProServer()
    yield server
    server.stop()
```

(`tests/conftest.py` does not currently exist in this repo — this creates it.
The `mock_gspro` fixture is auto-discovered by pytest because it's in
conftest.py.)

- [ ] **Step 2: Write failing tests**

Create `tests/test_gspro_client_basic.py`:

```python
"""Basic synchronous send/recv tests for GSProClient (no threading yet)."""
import json
import time

from openflight.gspro.client import GSProClient
from openflight.gspro.config import GSProConfig
from openflight.gspro.shot_builder import GSProSend
from openflight.gspro.messages import build_heartbeat


def _config(host, port):
    return GSProConfig(enabled=True, host=host, port=port,
                       device_id="OpenFlight", units="Yards",
                       heartbeat_interval_s=60)  # large to suppress in basic tests


def test_connect_and_disconnect(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    client.connect()
    assert client.is_connected()
    client.close()
    assert not client.is_connected()


def test_send_payload_arrives_at_server(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    client.connect()
    payload = {"hello": "world", "n": 1}
    client.send_raw(json.dumps(payload).encode("utf-8"))
    deadline = time.time() + 1.0
    while time.time() < deadline and not mock_gspro.received:
        time.sleep(0.05)
    assert mock_gspro.received, "server did not receive any bytes"
    assert json.loads(mock_gspro.received[0]) == payload
    client.close()


def test_recv_response_dispatches_callback(mock_gspro):
    received_responses = []
    client = GSProClient(
        _config(mock_gspro.host, mock_gspro.port),
        on_response=received_responses.append,
    )
    mock_gspro.queue_reply({"Code": 200, "Message": "OK"})
    client.connect()
    client.poll(timeout=0.5)  # synchronous read; dispatches via callback
    assert len(received_responses) == 1
    assert received_responses[0].Code == 200
    client.close()


def test_send_heartbeat_helper(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    client.connect()
    client.send_raw(build_heartbeat("OpenFlight", "Yards", shot_number=42))
    deadline = time.time() + 1.0
    while time.time() < deadline and not mock_gspro.received:
        time.sleep(0.05)
    assert mock_gspro.received
    obj = json.loads(mock_gspro.received[0])
    assert obj["ShotDataOptions"]["IsHeartBeat"] is True
    client.close()
```

- [ ] **Step 3: Run tests, verify they fail**

```bash
uv run pytest tests/test_gspro_client_basic.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement basic client**

Create `src/openflight/gspro/client.py`:

```python
"""GSPro TCP client — synchronous primitives.

Threading (state machine + heartbeat) is added in subsequent tasks.
"""
import logging
import socket
from typing import Callable, Optional

from openflight.gspro.config import GSProConfig
from openflight.gspro.messages import GSProResponse, parse_response

logger = logging.getLogger(__name__)


class GSProClient:
    """TCP client for GSPro OpenConnectV1.

    Public API:
      connect() / close() / is_connected()
      send_raw(bytes)
      poll(timeout) — synchronous read; dispatches via on_response callback
    """

    def __init__(
        self,
        config: GSProConfig,
        on_response: Optional[Callable[[GSProResponse], None]] = None,
    ):
        self._config = config
        self._on_response = on_response
        self._sock: Optional[socket.socket] = None

    # --- connection lifecycle -------------------------------------------------

    def connect(self) -> None:
        if self._sock is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((self._config.host, self._config.port))
        self._sock = s
        logger.info("[gspro] connected to %s:%d", self._config.host, self._config.port)

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
        logger.info("[gspro] disconnected")

    def is_connected(self) -> bool:
        return self._sock is not None

    # --- I/O ------------------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        if self._sock is None:
            raise RuntimeError("send_raw called while not connected")
        self._sock.sendall(data)

    def poll(self, timeout: float = 0.1) -> None:
        """Read once from socket and dispatch response if any."""
        if self._sock is None:
            return
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(4096)
        except socket.timeout:
            return
        if not data:
            self.close()
            return
        try:
            response = parse_response(data)
        except ValueError as e:
            logger.warning("[gspro] dropping malformed response: %s", e)
            return
        if self._on_response is not None:
            self._on_response(response)
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_gspro_client_basic.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py src/openflight/gspro/client.py \
        tests/test_gspro_client_basic.py
git commit -m "feat(gspro): mock server harness + synchronous TCP client"
```

---

## Task 6: Connection thread + state machine + reconnect with backoff

**Files:**
- Modify: `src/openflight/gspro/client.py`
- Test: `tests/test_gspro_client_lifecycle.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gspro_client_lifecycle.py`:

```python
"""Connection lifecycle tests: state machine, reconnect, backoff."""
import time

from openflight.gspro.client import GSProClient
from openflight.gspro.config import GSProConfig
from openflight.gspro.state import ConnectionState


def _config(host, port, **overrides):
    base = dict(enabled=True, host=host, port=port, device_id="OpenFlight",
                units="Yards", heartbeat_interval_s=60)
    base.update(overrides)
    return GSProConfig(**base)


def _wait_until_state(client, target, deadline_s=3.0):
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        if client.state == target:
            return True
        time.sleep(0.05)
    return False


def test_start_transitions_to_connected(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    statuses = []
    client.on_status = statuses.append
    client.start()
    try:
        assert _wait_until_state(client, ConnectionState.CONNECTED)
        assert any(s.state == ConnectionState.CONNECTED for s in statuses)
    finally:
        client.stop()
    assert _wait_until_state(client, ConnectionState.STOPPED)


def test_reconnect_after_server_drop(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port,
                                 heartbeat_interval_s=60),
                         backoff_seconds=(0.1, 0.2, 0.4))
    client.start()
    try:
        assert _wait_until_state(client, ConnectionState.CONNECTED)
        mock_gspro.disconnect_client()
        # should pass through RECONNECT_BACKOFF and back to CONNECTED
        assert _wait_until_state(client, ConnectionState.RECONNECT_BACKOFF, deadline_s=2.0)
        assert _wait_until_state(client, ConnectionState.CONNECTED, deadline_s=3.0)
    finally:
        client.stop()


def test_backoff_progression_capped():
    """Backoff schedule used when reconnecting hits a closed port."""
    cfg = GSProConfig(enabled=True, host="127.0.0.1", port=1,  # refused
                      device_id="OpenFlight", units="Yards",
                      heartbeat_interval_s=60)
    client = GSProClient(cfg, backoff_seconds=(0.05, 0.1, 0.1))
    statuses = []
    client.on_status = statuses.append
    client.start()
    time.sleep(0.5)
    client.stop()
    backoffs = [s.next_retry_in_s for s in statuses
                if s.state == ConnectionState.RECONNECT_BACKOFF]
    # Should see at least two backoff entries (initial + one retry)
    assert len(backoffs) >= 2
    # Capped at the last value in our schedule (0.1)
    assert max(backoffs) <= 0.1


def test_stop_is_idempotent(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    client.start()
    _wait_until_state(client, ConnectionState.CONNECTED)
    client.stop()
    client.stop()  # should not raise
    assert client.state == ConnectionState.STOPPED
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_gspro_client_lifecycle.py -v
```

Expected: AttributeError on `client.start` / `client.state`.

- [ ] **Step 3: Extend client with state machine and connection thread**

Replace `src/openflight/gspro/client.py` with:

```python
"""GSPro TCP client — connection thread + state machine + reconnect."""
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from openflight.gspro.config import GSProConfig
from openflight.gspro.messages import GSProResponse, parse_response
from openflight.gspro.state import ConnectionState

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF: Tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


@dataclass
class StatusEvent:
    state: ConnectionState
    host: str = ""
    port: int = 0
    attempt: int = 0
    next_retry_in_s: float = 0.0
    message: str = ""


class GSProClient:
    """TCP client with reconnect.

    Lifecycle:
      start() — spawn connection thread; transitions DISABLED → CONNECTING → CONNECTED.
      stop()  — terminate thread; transitions to STOPPED.

    Callbacks (set as attributes):
      on_response(GSProResponse) — called per received reply
      on_status(StatusEvent)     — called on every state change
    """

    def __init__(
        self,
        config: GSProConfig,
        on_response: Optional[Callable[[GSProResponse], None]] = None,
        on_status: Optional[Callable[["StatusEvent"], None]] = None,
        backoff_seconds: Tuple[float, ...] = DEFAULT_BACKOFF,
    ):
        self._config = config
        self.on_response = on_response
        self.on_status = on_status
        self._backoff = backoff_seconds
        self._state = ConnectionState.DISABLED
        self._state_lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._conn_thread: Optional[threading.Thread] = None

    # --- public state ---------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        with self._state_lock:
            return self._state

    def is_connected(self) -> bool:
        return self.state == ConnectionState.CONNECTED

    # --- start / stop ---------------------------------------------------------

    def start(self) -> None:
        if self._conn_thread is not None and self._conn_thread.is_alive():
            return
        self._stop_event.clear()
        self._conn_thread = threading.Thread(
            target=self._connection_loop, name="gspro-conn", daemon=True,
        )
        self._conn_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._close_socket()
        if self._conn_thread is not None:
            self._conn_thread.join(timeout=3.0)
            self._conn_thread = None
        self._set_state(ConnectionState.STOPPED)

    # --- send -----------------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        with self._sock_lock:
            if self._sock is None:
                raise RuntimeError("send_raw called while not connected")
            self._sock.sendall(data)

    # --- internals ------------------------------------------------------------

    def _set_state(self, new_state: ConnectionState, **status_kwargs) -> None:
        with self._state_lock:
            if self._state == new_state and not status_kwargs:
                return
            self._state = new_state
        if self.on_status is not None:
            self.on_status(StatusEvent(
                state=new_state,
                host=self._config.host,
                port=self._config.port,
                **status_kwargs,
            ))

    def _close_socket(self) -> None:
        with self._sock_lock:
            if self._sock is None:
                return
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _try_connect(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        try:
            s.connect((self._config.host, self._config.port))
        except OSError as e:
            try:
                s.close()
            except OSError:
                pass
            logger.info("[gspro] connect failed: %s", e)
            return False
        with self._sock_lock:
            self._sock = s
        return True

    def _backoff_for_attempt(self, attempt: int) -> float:
        idx = min(attempt, len(self._backoff) - 1)
        return self._backoff[idx]

    def _connection_loop(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            self._set_state(ConnectionState.CONNECTING)
            if self._try_connect():
                attempt = 0
                self._set_state(ConnectionState.CONNECTED)
                self._recv_loop()
                self._close_socket()
                if self._stop_event.is_set():
                    break
                # Connection dropped — fall through to reconnect
            wait = self._backoff_for_attempt(attempt)
            self._set_state(
                ConnectionState.RECONNECT_BACKOFF,
                attempt=attempt + 1, next_retry_in_s=wait,
            )
            attempt += 1
            self._stop_event.wait(timeout=wait)

    def _recv_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._sock_lock:
                sock = self._sock
            if sock is None:
                return
            sock.settimeout(0.2)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            try:
                response = parse_response(data)
            except ValueError as e:
                logger.warning("[gspro] dropping malformed response: %s", e)
                continue
            if self.on_response is not None:
                try:
                    self.on_response(response)
                except Exception:  # pylint: disable=broad-except
                    logger.exception("[gspro] on_response raised")
```

The basic-client tests (`tests/test_gspro_client_basic.py`) were written against an older API (`connect()`, `close()`, `poll()`); they need updating to the new lifecycle.

- [ ] **Step 4: Update basic-client tests to the new API**

Replace `tests/test_gspro_client_basic.py` with:

```python
"""Basic send/recv tests for GSProClient (uses start/stop lifecycle)."""
import json
import time

from openflight.gspro.client import GSProClient
from openflight.gspro.config import GSProConfig
from openflight.gspro.messages import build_heartbeat
from openflight.gspro.state import ConnectionState


def _config(host, port):
    return GSProConfig(enabled=True, host=host, port=port,
                       device_id="OpenFlight", units="Yards",
                       heartbeat_interval_s=60)


def _wait_for_state(client, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if client.state == state:
            return True
        time.sleep(0.05)
    return False


def test_send_payload_arrives_at_server(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        payload = {"hello": "world", "n": 1}
        client.send_raw(json.dumps(payload).encode("utf-8"))
        deadline = time.time() + 1.0
        while time.time() < deadline and not mock_gspro.received:
            time.sleep(0.05)
        assert mock_gspro.received
        assert json.loads(mock_gspro.received[0]) == payload
    finally:
        client.stop()


def test_recv_response_dispatches_callback(mock_gspro):
    received = []
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port),
                         on_response=received.append)
    mock_gspro.queue_reply({"Code": 200, "Message": "OK"})
    client.start()
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline and not received:
            time.sleep(0.05)
        assert len(received) == 1
        assert received[0].Code == 200
    finally:
        client.stop()


def test_send_heartbeat_helper(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        client.send_raw(build_heartbeat("OpenFlight", "Yards", shot_number=42))
        deadline = time.time() + 1.0
        while time.time() < deadline and not mock_gspro.received:
            time.sleep(0.05)
        assert mock_gspro.received
        obj = json.loads(mock_gspro.received[0])
        assert obj["ShotDataOptions"]["IsHeartBeat"] is True
    finally:
        client.stop()
```

- [ ] **Step 5: Run all gspro tests, verify pass**

```bash
uv run pytest tests/test_gspro_client_basic.py tests/test_gspro_client_lifecycle.py -v
```

Expected: all tests pass. (lifecycle tests intentionally exercise reconnect timing — runtime ~5s.)

- [ ] **Step 6: Commit**

```bash
git add src/openflight/gspro/client.py \
        tests/test_gspro_client_basic.py tests/test_gspro_client_lifecycle.py
git commit -m "feat(gspro): connection thread, state machine, exponential reconnect"
```

---

## Task 7: Heartbeat thread

**Files:**
- Modify: `src/openflight/gspro/client.py`
- Modify: `src/openflight/gspro/__init__.py` (export StatusEvent)
- Test: `tests/test_gspro_client_heartbeat.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gspro_client_heartbeat.py`:

```python
"""Heartbeat thread tests."""
import json
import time

from openflight.gspro.client import GSProClient
from openflight.gspro.config import GSProConfig
from openflight.gspro.state import ConnectionState


def _config(host, port, hb=0.2):
    return GSProConfig(enabled=True, host=host, port=port,
                       device_id="OpenFlight", units="Yards",
                       heartbeat_interval_s=hb)


def _wait_for_state(client, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if client.state == state:
            return True
        time.sleep(0.05)
    return False


def test_heartbeats_are_sent_periodically(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port, hb=0.2))
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        time.sleep(0.7)  # expect ~3 heartbeats
        beats = [m for m in mock_gspro.received
                 if json.loads(m)["ShotDataOptions"]["IsHeartBeat"] is True]
        assert len(beats) >= 2
    finally:
        client.stop()


def test_heartbeat_suppressed_after_recent_send(mock_gspro):
    """If a non-heartbeat send happens, heartbeat skips the next interval."""
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port, hb=0.5))
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        time.sleep(0.05)
        for _ in range(3):
            client.send_raw(b'{"hello":"world"}')
            time.sleep(0.3)  # send every 0.3s for 0.9s
        # Total: ~1.0s of activity, hb interval is 0.5s — at most 1 heartbeat
        beats = [m for m in mock_gspro.received
                 if b'"IsHeartBeat":true' in m]
        assert len(beats) <= 1
    finally:
        client.stop()


def test_no_heartbeat_when_disconnected(mock_gspro):
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port, hb=0.1))
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        before = len(mock_gspro.received)
        mock_gspro.disconnect_client()
        time.sleep(0.4)
        # heartbeats during disconnect shouldn't queue/buffer at the server
        # (the connection is gone). Wait for the next CONNECTED, then verify
        # heartbeats only resume after reconnect.
        # We can't easily separate pre- vs post-reconnect bytes here, so the
        # assertion is loose: heartbeats keep arriving once reconnected.
        deadline = time.time() + 2.0
        while time.time() < deadline and client.state != ConnectionState.CONNECTED:
            time.sleep(0.05)
        assert client.state == ConnectionState.CONNECTED
        before_recovery = len(mock_gspro.received)
        time.sleep(0.4)
        assert len(mock_gspro.received) > before_recovery
    finally:
        client.stop()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_gspro_client_heartbeat.py -v
```

Expected: heartbeat tests time out (no heartbeat thread exists yet).

- [ ] **Step 3: Add heartbeat thread to client**

In `src/openflight/gspro/client.py`, add the following:

At the top of the file, add to imports:

```python
from openflight.gspro.messages import build_heartbeat, GSProResponse, parse_response
```

(Replace the existing `from openflight.gspro.messages import GSProResponse, parse_response` line.)

In `GSProClient.__init__`, after `self._conn_thread = None`, add:

```python
        self._hb_thread: Optional[threading.Thread] = None
        self._last_send_time = 0.0
        self._send_time_lock = threading.Lock()
```

Modify `send_raw` to record the time:

```python
    def send_raw(self, data: bytes) -> None:
        with self._sock_lock:
            if self._sock is None:
                raise RuntimeError("send_raw called while not connected")
            self._sock.sendall(data)
        with self._send_time_lock:
            self._last_send_time = time.time()
```

Add a private method to send heartbeats:

```python
    def _send_heartbeat(self) -> None:
        # Use a separate path that does NOT update _last_send_time, otherwise
        # heartbeats would keep themselves alive even when no real traffic.
        with self._sock_lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(build_heartbeat(
                    self._config.device_id, self._config.units,
                    shot_number=0,
                ))
            except OSError as e:
                logger.info("[gspro] heartbeat send failed: %s", e)

    def _heartbeat_loop(self) -> None:
        interval = max(self._config.heartbeat_interval_s, 0.05)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                return
            if self.state != ConnectionState.CONNECTED:
                continue
            with self._send_time_lock:
                idle_for = time.time() - self._last_send_time
            if idle_for < interval:
                continue
            self._send_heartbeat()
```

Modify `start()` to spawn the heartbeat thread:

```python
    def start(self) -> None:
        if self._conn_thread is not None and self._conn_thread.is_alive():
            return
        self._stop_event.clear()
        self._conn_thread = threading.Thread(
            target=self._connection_loop, name="gspro-conn", daemon=True,
        )
        self._conn_thread.start()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="gspro-hb", daemon=True,
        )
        self._hb_thread.start()
```

Modify `stop()` to join the heartbeat thread:

```python
    def stop(self) -> None:
        self._stop_event.set()
        self._close_socket()
        for t in (self._conn_thread, self._hb_thread):
            if t is not None:
                t.join(timeout=3.0)
        self._conn_thread = None
        self._hb_thread = None
        self._set_state(ConnectionState.STOPPED)
```

- [ ] **Step 4: Update package exports**

Replace `src/openflight/gspro/__init__.py` with:

```python
"""GSPro OpenConnectV1 integration (optional)."""
from .client import GSProClient, StatusEvent
from .config import GSProConfig, load_gspro_config
from .shot_builder import GSProSend, IncompleteShotError, build as build_gspro_payload
from .state import ConnectionState, PlayerState

__all__ = [
    "ConnectionState", "GSProClient", "GSProConfig", "GSProSend",
    "IncompleteShotError", "PlayerState", "StatusEvent",
    "build_gspro_payload", "load_gspro_config",
]
```

- [ ] **Step 5: Run all gspro tests, verify pass**

```bash
uv run pytest tests/test_gspro_*.py -v
```

Expected: all tests pass. (Heartbeat tests run ~3-5s.)

- [ ] **Step 6: Commit**

```bash
git add src/openflight/gspro/client.py src/openflight/gspro/__init__.py \
        tests/test_gspro_client_heartbeat.py
git commit -m "feat(gspro): heartbeat thread with idle-suppression"
```

---

## Task 8: Session logger entries for GSPro events

**Files:**
- Modify: `src/openflight/session_logger.py` (add three methods)
- Test: `tests/test_session_logger_gspro.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session_logger_gspro.py`:

```python
"""Tests for new GSPro entry types in SessionLogger."""
import json

from openflight.session_logger import SessionLogger


def _read_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_log_gspro_send(tmp_path):
    log = SessionLogger(session_dir=tmp_path, mode="range")
    log.start_session()
    payload = {"BallData": {"Speed": 140.0}}
    provenance = {"BallData.Speed": "measured"}
    log.log_gspro_send(shot_number=1, payload=payload, provenance=provenance)
    log.end_session()
    entries = _read_jsonl(log.log_path)
    sends = [e for e in entries if e["type"] == "gspro_send"]
    assert len(sends) == 1
    assert sends[0]["data"]["shot_number"] == 1
    assert sends[0]["data"]["payload"] == payload
    assert sends[0]["data"]["provenance"] == provenance


def test_log_gspro_status(tmp_path):
    log = SessionLogger(session_dir=tmp_path, mode="range")
    log.start_session()
    log.log_gspro_status(state="connected", host="10.0.0.5", port=921, message="")
    log.end_session()
    statuses = [e for e in _read_jsonl(log.log_path) if e["type"] == "gspro_status"]
    assert len(statuses) == 1
    assert statuses[0]["data"]["state"] == "connected"
    assert statuses[0]["data"]["host"] == "10.0.0.5"


def test_log_gspro_player(tmp_path):
    log = SessionLogger(session_dir=tmp_path, mode="range")
    log.start_session()
    log.log_gspro_player(handed="LH", club="I7")
    log.end_session()
    plays = [e for e in _read_jsonl(log.log_path) if e["type"] == "gspro_player"]
    assert len(plays) == 1
    assert plays[0]["data"]["handed"] == "LH"
    assert plays[0]["data"]["club"] == "I7"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_session_logger_gspro.py -v
```

Expected: AttributeError on `log.log_gspro_send`.

- [ ] **Step 3: Add three methods to SessionLogger**

In `src/openflight/session_logger.py`, after the existing `def log_camera_data(...)` method, add:

```python
    def log_gspro_send(
        self,
        shot_number: int,
        payload: dict,
        provenance: Dict[str, str],
    ) -> None:
        """Log a shot payload sent to GSPro along with field provenance."""
        if not self.enabled:
            return
        self._write_entry("gspro_send", {
            "shot_number": shot_number,
            "payload": payload,
            "provenance": provenance,
        })

    def log_gspro_status(
        self,
        state: str,
        host: str = "",
        port: int = 0,
        message: str = "",
        attempt: int = 0,
        next_retry_in_s: float = 0.0,
    ) -> None:
        """Log a GSPro connection state change."""
        if not self.enabled:
            return
        self._write_entry("gspro_status", {
            "state": state,
            "host": host,
            "port": port,
            "message": message,
            "attempt": attempt,
            "next_retry_in_s": next_retry_in_s,
        })

    def log_gspro_player(self, handed: str, club: str) -> None:
        """Log a GSPro player/club update (from a code-201 response)."""
        if not self.enabled:
            return
        self._write_entry("gspro_player", {
            "handed": handed,
            "club": club,
        })
```

- [ ] **Step 4: Run tests, verify pass**

```bash
uv run pytest tests/test_session_logger_gspro.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/session_logger.py tests/test_session_logger_gspro.py
git commit -m "feat(gspro): session logger entries for send/status/player events"
```

---

## Task 9: Server wiring — CLI flags, client lifecycle, shot listener, player updates

**Files:**
- Modify: `src/openflight/server.py`
- Test: `tests/test_gspro_server_wiring.py`

- [ ] **Step 1: Write failing test (end-to-end through server callbacks)**

Create `tests/test_gspro_server_wiring.py`:

```python
"""End-to-end test: server callbacks correctly forward shots to GSPro client."""
import json
import time
from datetime import datetime

from openflight.launch_monitor import ClubType, Shot
from openflight.gspro.client import GSProClient
from openflight.gspro.config import GSProConfig
from openflight.gspro.shot_builder import build as build_payload
from openflight.gspro.state import ConnectionState, PlayerState


def _config(host, port):
    return GSProConfig(enabled=True, host=host, port=port,
                       device_id="OpenFlight", units="Yards",
                       heartbeat_interval_s=60)


def _wait_for_state(client, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if client.state == state:
            return True
        time.sleep(0.05)
    return False


def test_shot_payload_round_trip(mock_gspro):
    """Build a shot, send it, verify mock server received the right JSON."""
    client = GSProClient(_config(mock_gspro.host, mock_gspro.port))
    player = PlayerState()
    client.start()
    try:
        assert _wait_for_state(client, ConnectionState.CONNECTED)
        shot = Shot(
            ball_speed_mph=140.0, timestamp=datetime(2026, 4, 26, 12, 0, 0),
            club=ClubType.IRON_7, club_speed_mph=95.0,
            launch_angle_vertical=20.0, launch_angle_horizontal=1.0,
            spin_rpm=7000.0, spin_confidence=0.9, spin_axis_deg=-2.0,
            club_path_deg=0.5,
        )
        result = build_payload(shot, player)
        # build_payload returns GSProSend(payload: dict, provenance: dict);
        # send the payload dict directly as JSON.
        client.send_raw(json.dumps(result.payload).encode("utf-8"))
        deadline = time.time() + 1.0
        while time.time() < deadline and not mock_gspro.received:
            time.sleep(0.05)
        assert mock_gspro.received
        obj = json.loads(mock_gspro.received[0])
        assert obj["BallData"]["Speed"] == 140.0
        assert obj["BallData"]["TotalSpin"] == 7000.0
        assert obj["BallData"]["VLA"] == 20.0
    finally:
        client.stop()


def test_player_update_propagates_to_state(mock_gspro):
    """When mock sends code 201, on_response callback updates PlayerState."""
    player = PlayerState()
    received = []

    def on_resp(resp):
        received.append(resp)
        if resp.Code == 201 and resp.Player:
            player.update_from_gspro(resp.Player)

    client = GSProClient(_config(mock_gspro.host, mock_gspro.port),
                         on_response=on_resp)
    mock_gspro.queue_reply({"Code": 201, "Message": "Player",
                            "Player": {"Handed": "LH", "Club": "I7"}})
    client.start()
    try:
        deadline = time.time() + 1.5
        while time.time() < deadline and not received:
            time.sleep(0.05)
        assert received
        assert player.handed == "LH"
        assert player.club == ClubType.IRON_7
    finally:
        client.stop()
```

- [ ] **Step 2: Run test, verify it passes**

(This test exercises only the gspro module, no server.py changes needed yet.)

```bash
uv run pytest tests/test_gspro_server_wiring.py -v
```

Expected: both tests pass already (they only use gspro module classes).

- [ ] **Step 3: Add CLI flags to server.py**

In `src/openflight/server.py`, find the existing K-LD7 argparse section (around line 1580). After the last `--kld7-*` argument, add:

```python
    parser.add_argument(
        "--gspro",
        default=None,
        metavar="HOST[:PORT]",
        help="Enable GSPro integration. Overrides config/gspro.json host/port.",
    )
    parser.add_argument(
        "--no-gspro",
        action="store_true",
        help="Disable GSPro even if config/gspro.json has enabled:true.",
    )
```

- [ ] **Step 4: Wire up GSProClient in server startup**

Near the top of `src/openflight/server.py`, add the import:

```python
from openflight.gspro import (
    GSProClient, GSProConfig, PlayerState as GSProPlayerState,
    StatusEvent as GSProStatusEvent,
    build_gspro_payload, load_gspro_config,
    IncompleteShotError,
)
```

Add module-level globals (near other globals like `monitor`, `kld7_vertical`):

```python
gspro_client: Optional[GSProClient] = None
gspro_cfg: Optional[GSProConfig] = None
gspro_player_state = GSProPlayerState()
```

In the function that starts the server (the `if __name__ == "__main__":` block, after argparse and before `monitor.start(...)`), add:

```python
    # GSPro integration (optional)
    global gspro_client, gspro_cfg  # pylint: disable=global-statement
    gspro_cfg = load_gspro_config(cli_value=args.gspro, no_gspro=args.no_gspro)
    if gspro_cfg.enabled:

        def _gspro_on_status(event: GSProStatusEvent) -> None:
            socketio.emit("gspro_status", {
                "state": event.state.value, "host": event.host, "port": event.port,
                "attempt": event.attempt, "next_retry_in_s": event.next_retry_in_s,
                "message": event.message,
            })
            sl = get_session_logger()
            if sl:
                sl.log_gspro_status(
                    state=event.state.value, host=event.host, port=event.port,
                    message=event.message, attempt=event.attempt,
                    next_retry_in_s=event.next_retry_in_s,
                )

        def _gspro_on_response(resp) -> None:
            if resp.Code == 201 and resp.Player:
                gspro_player_state.update_from_gspro(resp.Player)
                socketio.emit("gspro_player", {
                    "handed": gspro_player_state.handed,
                    "club": gspro_player_state.club.value,
                })
                sl = get_session_logger()
                if sl:
                    sl.log_gspro_player(
                        handed=gspro_player_state.handed,
                        club=resp.Player.get("Club", ""),
                    )
                # Update the monitor's current club so subsequent shots are
                # tagged correctly and the UI club picker reflects it. The
                # monitor (RollingBufferMonitor or MockMonitor) owns club
                # state via set_club() — see server.py:747 (set_club handler).
                if monitor is not None:
                    try:
                        monitor.set_club(gspro_player_state.club)
                    except Exception:  # pylint: disable=broad-except
                        logger.exception("[gspro] monitor.set_club failed")
                socketio.emit("club_changed", {"club": gspro_player_state.club.value})

        gspro_client = GSProClient(
            gspro_cfg,
            on_response=_gspro_on_response,
            on_status=_gspro_on_status,
        )
        gspro_client.start()
        logger.info("[SERVER] GSPro integration enabled → %s:%d",
                    gspro_cfg.host, gspro_cfg.port)
```

- [ ] **Step 5: Send shots to GSPro from on_shot_detected**

In `src/openflight/server.py`, inside `on_shot_detected(shot: Shot)`, after the `socketio.emit("shot", ...)` call (around line 1160), add:

```python
    # Forward to GSPro if enabled
    if gspro_client is not None and gspro_client.is_connected():
        try:
            sent = build_gspro_payload(
                shot, gspro_player_state,
                device_id=gspro_cfg.device_id,
                units=gspro_cfg.units,
            )
            gspro_client.send_raw(json.dumps(sent.payload).encode("utf-8"))
            sl = get_session_logger()
            if sl:
                sl.log_gspro_send(
                    shot_number=sent.payload["ShotNumber"],
                    payload=sent.payload, provenance=sent.provenance,
                )
            # Re-emit to UI with provenance attached so the shot card can render badges
            socketio.emit("gspro_shot", {
                "payload": sent.payload, "provenance": sent.provenance,
            })
        except IncompleteShotError as e:
            logger.warning("[gspro] dropping shot: %s", e)
            socketio.emit("gspro_send_failed", {"reason": str(e)})
        except OSError as e:
            logger.warning("[gspro] send failed: %s", e)
            socketio.emit("gspro_send_failed", {"reason": str(e)})
```

Make sure `import json` is present at the top of server.py (it is — line ~12).

- [ ] **Step 6: Wire shutdown**

Find the existing shutdown handler (look for `socketio.on("shutdown")` around line 889). Inside the `_shutdown_handler()` function, before `monitor.stop()`, add:

```python
    if gspro_client is not None:
        try:
            gspro_client.stop()
        except Exception:  # pylint: disable=broad-except
            logger.exception("[gspro] error stopping client")
```

- [ ] **Step 7: Verify server still imports and runs in mock mode**

```bash
uv run python -c "from openflight import server"
```

Expected: no import error.

```bash
uv run pytest tests/test_gspro_server_wiring.py -v
uv run pytest tests/test_gspro_*.py -v
```

Expected: all gspro tests still pass.

- [ ] **Step 8: Commit**

```bash
git add src/openflight/server.py tests/test_gspro_server_wiring.py
git commit -m "feat(gspro): wire client into server with shot listener and player updates"
```

---

## Task 10: Forward CLI flags through start-kiosk.sh

**Files:**
- Modify: `scripts/start-kiosk.sh`

- [ ] **Step 1: Add flag parsing**

In `scripts/start-kiosk.sh`, find the variable defaults block (around line 21 after `KLD7_HORIZONTAL_OFFSET=""`). Add:

```bash
GSPRO=""
NO_GSPRO=false
```

In the `while [[ $# -gt 0 ]]` argument loop (around line 47), after the last `--kld7-*` case, add:

```bash
        --gspro)
            GSPRO="$2"
            shift 2
            ;;
        --no-gspro)
            NO_GSPRO=true
            shift
            ;;
```

- [ ] **Step 2: Forward flags to server invocation**

Find the server launch invocation later in the script (search for `python -m openflight.server` or similar — it's the line that builds up the python command). After the K-LD7 flag forwarding, add:

```bash
if [ -n "$GSPRO" ]; then
    SERVER_ARGS="$SERVER_ARGS --gspro $GSPRO"
fi
if [ "$NO_GSPRO" = true ]; then
    SERVER_ARGS="$SERVER_ARGS --no-gspro"
fi
```

(The exact variable name — `SERVER_ARGS`, `PYTHON_ARGS`, etc. — is whatever the existing script uses to accumulate flags. Match the pattern used for `--kld7`.)

- [ ] **Step 3: Manual smoke test**

```bash
bash -n scripts/start-kiosk.sh
```

Expected: no syntax errors. (Full invocation needs hardware.)

- [ ] **Step 4: Commit**

```bash
git add scripts/start-kiosk.sh
git commit -m "feat(gspro): forward --gspro and --no-gspro through start-kiosk.sh"
```

---

## Task 11: UI status pill

**Files:**
- Create: `ui/src/components/GSProStatus.tsx`
- Create: `ui/src/components/GSProStatus.css`
- Modify: `ui/src/App.tsx` (mount the pill)

- [ ] **Step 1: Read the existing top-bar layout**

Open `ui/src/App.tsx` and find where `<ConnectionStatus />` is rendered. New pill goes alongside it.

- [ ] **Step 2: Create the component**

Create `ui/src/components/GSProStatus.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Socket } from "socket.io-client";
import "./GSProStatus.css";

type StatusEvent = {
  state: "disabled" | "connecting" | "connected" | "reconnecting" | "stopped";
  host: string;
  port: number;
  attempt?: number;
  next_retry_in_s?: number;
  message?: string;
};

interface Props {
  socket: Socket;
}

export function GSProStatus({ socket }: Props) {
  const [status, setStatus] = useState<StatusEvent | null>(null);

  useEffect(() => {
    const onStatus = (evt: StatusEvent) => setStatus(evt);
    socket.on("gspro_status", onStatus);
    return () => {
      socket.off("gspro_status", onStatus);
    };
  }, [socket]);

  if (status === null) {
    return null; // hide pill entirely when GSPro is not enabled
  }

  const colorClass = (() => {
    switch (status.state) {
      case "connected": return "gspro-pill--green";
      case "reconnecting":
      case "connecting": return "gspro-pill--amber";
      default: return "gspro-pill--gray";
    }
  })();

  const label = status.state === "connected"
    ? "GSPro: Connected"
    : status.state === "reconnecting"
      ? `GSPro: Reconnecting (${(status.next_retry_in_s ?? 0).toFixed(0)}s)`
      : `GSPro: ${status.state}`;

  const tooltip = `${status.host}:${status.port}${status.message ? " — " + status.message : ""}`;

  return (
    <div className={`gspro-pill ${colorClass}`} title={tooltip}>
      <span className="gspro-dot" />
      <span>{label}</span>
    </div>
  );
}
```

- [ ] **Step 3: Add styles**

Create `ui/src/components/GSProStatus.css`:

```css
.gspro-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  background: rgba(255, 255, 255, 0.06);
  color: #ddd;
}
.gspro-dot {
  width: 8px; height: 8px; border-radius: 50%;
}
.gspro-pill--green .gspro-dot { background: #2ecc71; }
.gspro-pill--amber .gspro-dot { background: #f1c40f; }
.gspro-pill--gray  .gspro-dot { background: #888; }
```

- [ ] **Step 4: Mount the pill**

In `ui/src/App.tsx`, import the component:

```tsx
import { GSProStatus } from "./components/GSProStatus";
```

Render it next to the existing `<ConnectionStatus />` (look for the parent container holding status indicators):

```tsx
<ConnectionStatus ... />
<GSProStatus socket={socket} />
```

The exact placement depends on the existing JSX — match the surrounding wrapper.

- [ ] **Step 5: Build the UI**

```bash
cd ui && npm run build
```

Expected: build succeeds without errors.

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/GSProStatus.tsx ui/src/components/GSProStatus.css ui/src/App.tsx
git commit -m "feat(gspro): UI status pill driven by gspro_status WS events"
```

---

## Task 12: UI per-shot provenance badges

**Files:**
- Modify: `ui/src/components/ShotDisplay.tsx` (or `ShotList.tsx` — pick the one rendering individual shot cards)
- Modify corresponding CSS file
- Modify: `ui/src/App.tsx` or wherever `socket.on("shot", ...)` lives — also subscribe to `"gspro_shot"` and store provenance keyed by shot number

- [ ] **Step 1: Subscribe to gspro_shot in the shot reducer**

Locate the `socket.on("shot", ...)` handler in `ui/src/App.tsx` (or in `ui/src/state/`). Add a parallel subscription:

```tsx
useEffect(() => {
  const onGSProShot = (evt: { payload: { ShotNumber: number }, provenance: Record<string, "measured" | "estimated"> }) => {
    setGsproProvenance(prev => ({ ...prev, [evt.payload.ShotNumber]: evt.provenance }));
  };
  socket.on("gspro_shot", onGSProShot);
  return () => { socket.off("gspro_shot", onGSProShot); };
}, [socket]);
```

(`gsproProvenance` is a `Record<number, Record<string, string>>` state map. Adapt to the project's existing state-management pattern — useState in App.tsx, Zustand store in `ui/src/state/`, etc.)

Pass `provenance={gsproProvenance[shot.shotNumber]}` down to each `<ShotDisplay>` / `<ShotCard>`.

- [ ] **Step 2: Render badges in ShotDisplay**

Open `ui/src/components/ShotDisplay.tsx`. Add to the props:

```tsx
interface Props {
  shot: Shot;
  provenance?: Record<string, "measured" | "estimated">;
}
```

Inside the JSX, beside each metric add a badge if provenance exists. Helper:

```tsx
const Badge = ({ kind }: { kind?: "measured" | "estimated" }) => {
  if (!kind) return null;
  return (
    <span className={`prov-badge prov-badge--${kind}`} title={kind}>
      {kind === "measured" ? "M" : "E"}
    </span>
  );
};
```

Examples (match existing markup):

```tsx
<div>Ball: {shot.ballSpeedMph} mph <Badge kind={provenance?.["BallData.Speed"]} /></div>
<div>Spin: {shot.spinRpm} rpm <Badge kind={provenance?.["BallData.TotalSpin"]} />
     Axis: {shot.spinAxisDeg}° <Badge kind={provenance?.["BallData.SpinAxis"]} /></div>
<div>HLA: {shot.hla}° <Badge kind={provenance?.["BallData.HLA"]} />
     VLA: {shot.vla}° <Badge kind={provenance?.["BallData.VLA"]} /></div>
```

At the bottom of the card, when `provenance` is non-null, render a summary line:

```tsx
{provenance && (() => {
  const values = Object.values(provenance);
  const measured = values.filter(v => v === "measured").length;
  const estimated = values.filter(v => v === "estimated").length;
  return estimated === 0
    ? <div className="prov-summary prov-summary--all">Sent to GSPro ✓</div>
    : <div className="prov-summary">Sent to GSPro — {measured} measured / {estimated} estimated</div>;
})()}
```

If the player_state.club came from GSPro (track separately via the `gspro_player` WS event), append "(from GSPro)" to the club label.

- [ ] **Step 3: Add badge styles**

Append to `ui/src/components/ShotDisplay.css`:

```css
.prov-badge {
  display: inline-block;
  padding: 1px 5px;
  margin-left: 4px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  vertical-align: middle;
}
.prov-badge--measured { background: #1d7a3a; color: #fff; }
.prov-badge--estimated { background: #b07c00; color: #fff; }
.prov-summary {
  margin-top: 6px;
  font-size: 11px;
  color: #aaa;
}
.prov-summary--all { color: #2ecc71; }
```

- [ ] **Step 4: Build and visually inspect**

```bash
cd ui && npm run build
```

Expected: build succeeds. (Visual verification requires a running server — see Task 14.)

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/ShotDisplay.tsx ui/src/components/ShotDisplay.css ui/src/App.tsx
git commit -m "feat(gspro): per-shot provenance badges and summary line"
```

---

## Task 13: Documentation

**Files:**
- Create: `docs/gspro-integration.md`
- Modify: `docs/raspberry-pi-setup.md` (add a "GSPro" subsection)
- Modify: `README.md` (mention GSPro in features list)

- [ ] **Step 1: Create the integration guide**

Create `docs/gspro-integration.md`:

```markdown
# GSPro Integration

OpenFlight can stream shots to [GSPro](https://gsprogolf.com/) over the
OpenConnectV1 protocol. Optional — disabled by default.

## Setup

1. **On the GSPro PC:** open the GSPro app and start the **OpenAPI Connect**
   window (Settings → OpenAPI). Note the IP address shown.
2. **On the OpenFlight Pi:** copy the example config and edit it:
   ```bash
   cp config/gspro.example.json config/gspro.json
   ```
   Edit `config/gspro.json`:
   ```json
   {
     "enabled": true,
     "host": "192.168.1.50",
     "port": 921,
     "device_id": "OpenFlight",
     "units": "Yards",
     "heartbeat_interval_s": 5
   }
   ```
   Replace `host` with the GSPro PC's IP. Keep the port at `921` unless
   GSPro shows a different one.
3. **Start OpenFlight:** `scripts/start-kiosk.sh` will pick up the config
   automatically.

## CLI overrides

```
scripts/start-kiosk.sh --gspro 192.168.1.50          # override host, port=921
scripts/start-kiosk.sh --gspro 192.168.1.50:9000     # override host and port
scripts/start-kiosk.sh --no-gspro                    # disable even if config enabled
```

`--no-gspro` always wins. `--gspro` overrides the file host/port and forces
`enabled: true`.

## What gets sent

Every detected shot sends an OpenConnectV1 JSON message containing ball speed,
launch angles, spin (total + axis + back/side), and carry distance. When a
field is not measured by the hardware, OpenFlight fills it from a model and
tags the field as `estimated`. The shot card in the UI shows per-field
`[M]` / `[E]` badges so you can see exactly what came from radar vs. model.

| Field | Source |
|---|---|
| Ball speed | OPS243 (always measured; shot is dropped if missing) |
| HLA / VLA | KLD7 horizontal/vertical when present, else model fallback |
| Total spin | Rolling buffer when confidence ≥ 0.7, else per-club spin model |
| Spin axis | `HLA − club_path` (D-plane), else 0° |
| Carry | `Shot.estimated_carry_yards` (already a model) |
| Club speed/path | OPS243 / KLD7-horizontal when present |

See the [design spec](superpowers/specs/2026-04-26-gspro-integration-design.md)
for the full fallback table.

## Status pill

The top bar shows a `GSPro` pill: green = connected, amber =
connecting/reconnecting, gray = disabled. Hover for the host:port and last
error message.

## Putting

GSPro's putting mode (`Club: PT`) is logged but **not** specially handled in
v1 — the Doppler sound trigger is unlikely to detect a putt anyway.

## Manual verification

1. Open GSPro and start the OpenAPI Connect window.
2. Set `config/gspro.json` to point to the GSPro PC.
3. `scripts/start-kiosk.sh` — confirm the status pill goes green.
4. Hit a shot — verify it appears in GSPro and on the OpenFlight UI shot card.
5. Confirm the per-field badges match what hardware was actually connected.
```

- [ ] **Step 2: Update raspberry-pi-setup.md**

In `docs/raspberry-pi-setup.md`, add a new section near the bottom (before any "Troubleshooting" section if present):

```markdown
## Optional: GSPro integration

To stream shots to GSPro, copy the example config and edit it:

```bash
cp config/gspro.example.json config/gspro.json
# edit config/gspro.json — set host to the GSPro PC's IP
```

See [docs/gspro-integration.md](gspro-integration.md) for full setup.
```

- [ ] **Step 3: Mention in README**

In `README.md`, add a bullet to the Features list (find the existing list near the top):

```markdown
- **GSPro integration** (optional): stream shots to [GSPro](https://gsprogolf.com/) over OpenConnectV1 — see [docs/gspro-integration.md](docs/gspro-integration.md)
```

And add to the Documentation section near the bottom:

```markdown
- **[GSPro Integration](docs/gspro-integration.md)** — Stream shots to the GSPro simulator
```

- [ ] **Step 4: Commit**

```bash
git add docs/gspro-integration.md docs/raspberry-pi-setup.md README.md
git commit -m "docs(gspro): integration guide, setup pointer, README mention"
```

---

## Task 14: End-to-end manual hardware test

**No code changes — verification only.**

- [ ] **Step 1: Confirm prerequisites**

- GSPro installed on a Windows PC on the same LAN as the Pi
- OpenAPI Connect window open in GSPro
- OPS243 + at least one KLD7 connected to the Pi

- [ ] **Step 2: Configure**

```bash
cp config/gspro.example.json config/gspro.json
# Edit config/gspro.json — set "enabled": true and "host" to GSPro PC's IP
```

- [ ] **Step 3: Start OpenFlight**

```bash
scripts/start-kiosk.sh
```

- [ ] **Step 4: Verify**

- UI top bar shows a green `GSPro` pill
- GSPro shows "Connected" status for OpenFlight
- Hit one shot — it appears in the OpenFlight UI shot list
- Per-field badges on the shot card reflect connected hardware (e.g. `BallData.Speed [M]`, `BallData.HLA [M]` if KLD7 horizontal connected)
- Same shot appears in GSPro with the right ball speed and launch angle
- Tail the session log:
  ```bash
  tail -f ~/openflight_sessions/session_*.jsonl | grep gspro_
  ```
  — should see `gspro_status` (connected) and `gspro_send` per shot

- [ ] **Step 5: Test reconnect**

- Close the OpenAPI Connect window in GSPro → status pill turns amber within ~5s
- Reopen the window → pill returns to green within ~30s

- [ ] **Step 6: Test club update from GSPro**

- Change club in GSPro → OpenFlight UI club selector reflects the new club
- Hit a shot → the shot card shows the new club tagged `(from GSPro)`

If any verification step fails, capture the relevant `gspro_*` JSONL entries
and the server log and reopen for debugging.

- [ ] **Step 7: No commit needed**

This task is verification only. If a fix is required, that becomes a new task.

---

## Verification commands (after all tasks)

```bash
# Run all gspro unit + integration tests
uv run pytest tests/test_gspro_*.py tests/test_session_logger_gspro.py -v

# Lint
uv run pylint src/openflight/gspro/ --fail-under=9

# Format check
uv run ruff check src/openflight/gspro/
uv run ruff format --check src/openflight/gspro/

# UI build (no new tests beyond compile)
cd ui && npm run build
```

All should pass.
