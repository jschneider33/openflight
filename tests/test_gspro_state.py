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
