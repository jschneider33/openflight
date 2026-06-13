"""Tests for opengolfsim.codec and club mapping."""
import json

from openflight.launch_monitor import ClubType
from openflight.opengolfsim.clubs import ogs_club_to_club
from openflight.opengolfsim.codec import OpenGolfSimCodec
from openflight.sim.types import PlayerUpdate, ResolvedShot, ShotAck


def _resolved(**kw) -> ResolvedShot:
    base = dict(
        shot_number=1, ball_speed_mph=135.0, vla=11.1, hla=1.2,
        total_spin_rpm=4800.0, spin_axis_deg=-2.5, back_spin_rpm=4795.0,
        side_spin_rpm=-209.0, carry_yards=240.0, club_path_deg=0.0,
        club=ClubType.DRIVER, club_speed_mph=None, provenance={},
    )
    base.update(kw)
    return ResolvedShot(**base)


def _shot_obj(codec, resolved):
    return json.loads(codec.build_shot(resolved).decode("utf-8"))


def test_build_shot_envelope_and_fields():
    obj = _shot_obj(OpenGolfSimCodec(), _resolved())
    assert obj["type"] == "shot"
    assert obj["unit"] == "imperial"
    shot = obj["shot"]
    assert shot["ballSpeed"] == 135.0
    assert shot["verticalLaunchAngle"] == 11.1
    assert shot["horizontalLaunchAngle"] == 1.2
    assert shot["spinAxis"] == -2.5
    assert shot["spinSpeed"] == 4800
    assert isinstance(shot["spinSpeed"], int)


def test_build_shot_omits_club_and_carry():
    shot = _shot_obj(OpenGolfSimCodec(), _resolved())["shot"]
    assert "carryDistance" not in shot
    assert "clubSpeed" not in shot
    assert "backSpin" not in shot


def test_units_configurable():
    obj = _shot_obj(OpenGolfSimCodec(units="metric"), _resolved())
    assert obj["unit"] == "metric"


def test_no_heartbeat():
    assert OpenGolfSimCodec().heartbeat_bytes() is None


def test_on_connect_sends_device_ready():
    obj = json.loads(OpenGolfSimCodec().on_connect_bytes().decode("utf-8"))
    assert obj == {"type": "device", "status": "ready"}


def test_fields_for_target_excludes_club_and_carry():
    fields = OpenGolfSimCodec().fields_for_target()
    assert "ball_speed" in fields and "total_spin" in fields
    assert "carry" not in fields and "club_speed" not in fields


def test_parse_player_update_by_name():
    raw = json.dumps({"type": "player", "handed": "RH",
                      "club": {"name": "7 Iron", "id": "7I"}}).encode()
    events = OpenGolfSimCodec().parse_inbound(raw)
    assert len(events) == 1
    assert isinstance(events[0], PlayerUpdate)
    assert events[0].handed == "RH"
    assert events[0].club is ClubType.IRON_7


def test_parse_shot_result_is_ack():
    events = OpenGolfSimCodec().parse_inbound(json.dumps({"type": "shot_result"}).encode())
    assert isinstance(events[0], ShotAck)


def test_parse_unknown_type_ignored():
    assert OpenGolfSimCodec().parse_inbound(json.dumps({"type": "ping"}).encode()) == []


def test_parse_malformed_raises_valueerror():
    import pytest
    with pytest.raises(ValueError):
        OpenGolfSimCodec().parse_inbound(b'{not json')


# --- club mapping ------------------------------------------------------------


def test_club_mapping_names():
    assert ogs_club_to_club("Driver") is ClubType.DRIVER
    assert ogs_club_to_club("Pitching Wedge") is ClubType.PW
    assert ogs_club_to_club("7 Iron") is ClubType.IRON_7
    assert ogs_club_to_club("7i") is ClubType.IRON_7
    assert ogs_club_to_club("i7") is ClubType.IRON_7
    assert ogs_club_to_club("3 Wood") is ClubType.WOOD_3


def test_club_mapping_dict_form():
    assert ogs_club_to_club({"name": "Sand Wedge", "id": "SW"}) is ClubType.SW
    assert ogs_club_to_club({"id": "DR"}) is ClubType.DRIVER


def test_club_mapping_putter_and_unknown():
    assert ogs_club_to_club("Putter") is ClubType.UNKNOWN
    assert ogs_club_to_club("frisbee") is ClubType.UNKNOWN


def test_club_mapping_none_is_none():
    assert ogs_club_to_club(None) is None
    assert ogs_club_to_club({}) is None
