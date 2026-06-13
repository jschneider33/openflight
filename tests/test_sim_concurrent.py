"""Integration: two connectors (GSPro + OpenGolfSim) running concurrently."""
import json
import time
from datetime import datetime

from openflight.launch_monitor import ClubType, Shot
from openflight.sim.codec import build_connectors
from openflight.sim.config import ConnectorConfig
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import ConnectionState, PlayerState
from tests.conftest import MockSimServer


def _wait(connector, state, deadline=3.0):
    end = time.time() + deadline
    while time.time() < end:
        if connector.state == state:
            return True
        time.sleep(0.05)
    return False


def test_shot_reaches_both_sims_in_their_own_formats():
    gspro_srv = MockSimServer()
    ogs_srv = MockSimServer()
    try:
        cfgs = [
            ConnectorConfig(type="gspro", enabled=True, host=gspro_srv.host,
                            port=gspro_srv.port),
            ConnectorConfig(type="opengolfsim", enabled=True, host=ogs_srv.host,
                            port=ogs_srv.port, units="imperial"),
        ]
        connectors = build_connectors(cfgs)
        for c in connectors:
            c.start()
        try:
            assert all(_wait(c, ConnectionState.CONNECTED) for c in connectors)

            shot = Shot(ball_speed_mph=135.0, timestamp=datetime(2026, 6, 13, 12, 0, 0),
                        club=ClubType.DRIVER, launch_angle_vertical=11.1,
                        launch_angle_horizontal=1.2)
            resolved = resolve_shot(shot, PlayerState())
            for c in connectors:
                c.send_shot(resolved)

            deadline = time.time() + 1.5
            while time.time() < deadline and not (gspro_srv.received and ogs_srv.received):
                time.sleep(0.05)

            # OpenGolfSim sends a device-ready frame on connect, then the shot.
            gspro_msgs = [json.loads(m) for m in _split(gspro_srv.received)]
            ogs_msgs = [json.loads(m) for m in _split(ogs_srv.received)]

            gspro_shot = next(m for m in gspro_msgs if "BallData" in m)
            assert gspro_shot["BallData"]["Speed"] == 135.0
            assert gspro_shot["APIversion"] == "1"

            ogs_shot = next(m for m in ogs_msgs if m.get("type") == "shot")
            assert ogs_shot["shot"]["ballSpeed"] == 135.0
            assert ogs_shot["shot"]["spinSpeed"] == int(round(resolved.total_spin_rpm))
            assert any(m.get("type") == "device" for m in ogs_msgs)
        finally:
            for c in connectors:
                c.stop()
    finally:
        gspro_srv.stop()
        ogs_srv.stop()


def _split(received_chunks):
    """Concatenate received bytes and split into individual JSON objects."""
    from openflight.sim.transport import find_json_end

    buf = b"".join(received_chunks)
    out = []
    while True:
        end = find_json_end(buf)
        if end is None:
            break
        out.append(buf[:end])
        buf = buf[end:]
    return out
