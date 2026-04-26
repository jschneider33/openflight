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
