#!/usr/bin/env python3
"""Raw TCP probe for a golf-simulator connector endpoint.

Connects to a sim's API port, optionally sends a device-ready hello (and a test
shot), then prints every byte the sim sends back — framed as pretty JSON when
possible — with timestamps. Use it to confirm a connection works and to capture
a simulator's exact wire format (e.g. what OpenGolfSim sends when you change
clubs), independent of the OpenFlight server.

Examples:
    # Watch what OpenGolfSim sends (change clubs in the sim while this runs):
    uv run python scripts/probe_sim.py --host 127.0.0.1 --port 3111

    # Also push a test shot to confirm the outbound path:
    uv run python scripts/probe_sim.py --port 3111 --shot

Note: stop the OpenFlight server first (or run it with --no-sim) if the sim
only accepts one device connection at a time.
"""
import argparse
import json
import socket
import time


def _pretty(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    try:
        return json.dumps(json.loads(text), indent=2)
    except json.JSONDecodeError:
        return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3111, help="sim API port (OpenGolfSim=3111, GSPro=921)")
    ap.add_argument("--no-ready", action="store_true", help="don't send the device-ready hello on connect")
    ap.add_argument("--shot", action="store_true", help="send one sample shot after connecting")
    args = ap.parse_args()

    print(f"connecting to {args.host}:{args.port} ...")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=10)
    except OSError as e:
        print(f"CONNECT FAILED: {e}")
        print("  → is the sim's developer API enabled and listening on this port?")
        return
    print("CONNECTED")

    if not args.no_ready:
        hello = json.dumps({"type": "device", "status": "ready"}).encode("utf-8")
        sock.sendall(hello)
        print(f"sent device-ready: {hello.decode()}")

    if args.shot:
        shot = json.dumps({
            "type": "shot",
            "shot": {
                "ballSpeed": 135.0, "verticalLaunchAngle": 11.1,
                "horizontalLaunchAngle": 1.2, "spinAxis": -2.5, "spinSpeed": 4800,
            },
        }).encode("utf-8")
        sock.sendall(shot)
        print(f"sent test shot: {shot.decode()}")

    print("\nlistening for inbound data — change clubs / hit shots in the sim now")
    print("(Ctrl-C to stop)\n")
    sock.settimeout(1.0)
    try:
        while True:
            try:
                data = sock.recv(8192)
            except socket.timeout:
                continue
            if not data:
                print("connection closed by the sim")
                break
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] received {len(data)} bytes:\n{_pretty(data)}\n")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
