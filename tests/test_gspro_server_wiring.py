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
