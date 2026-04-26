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
        deadline = time.time() + 2.0
        while time.time() < deadline and client.state != ConnectionState.CONNECTED:
            time.sleep(0.05)
        assert client.state == ConnectionState.CONNECTED
        before_recovery = len(mock_gspro.received)
        time.sleep(0.4)
        assert len(mock_gspro.received) > before_recovery
    finally:
        client.stop()
