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
