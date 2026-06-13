"""Tests for gspro.state — GSPro club-code mapping."""
from openflight.gspro.state import gspro_code_to_club
from openflight.launch_monitor import ClubType


def test_gspro_code_to_club_mapping():
    assert gspro_code_to_club("DR") is ClubType.DRIVER
    assert gspro_code_to_club("W3") is ClubType.WOOD_3
    assert gspro_code_to_club("H5") is ClubType.HYBRID_5
    assert gspro_code_to_club("I7") is ClubType.IRON_7
    assert gspro_code_to_club("PW") is ClubType.PW
    assert gspro_code_to_club("LW") is ClubType.LW


def test_unknown_code_maps_to_unknown():
    assert gspro_code_to_club("XX") is ClubType.UNKNOWN


def test_putter_out_of_scope_maps_to_unknown():
    assert gspro_code_to_club("PT") is ClubType.UNKNOWN
