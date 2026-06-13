#!/usr/bin/env python3
"""Raw TCP probe for a golf-simulator connector endpoint.

Connects to a sim's API port, optionally sends a device-ready hello (and a test
shot), then prints every byte the sim sends back — framed as pretty JSON when
possible — with timestamps. Use it to confirm a connection works and to capture
a simulator's exact wire format (e.g. what OpenGolfSim sends), independent of
the OpenFlight server.

Examples:
    # Watch what the sim sends (change clubs / hit shots while this runs):
    uv run python scripts/probe_sim.py --host 127.0.0.1 --port 3111

    # Also push a test shot to confirm the outbound path:
    uv run python scripts/probe_sim.py --port 3111 --shot

    # Fish for an undocumented "give me the current player/club" command:
    uv run python scripts/probe_sim.py --port 3111 --poke

Note: stop the OpenFlight server first (or run it with --no-sim) if the sim
only accepts one device connection at a time.
"""
import argparse
import json
import socket
import threading
import time

# Speculative request messages tried by --poke, in order. OpenGolfSim documents
# no query command for player/club state, so these are guesses: if any of them
# makes the sim reply with a player/club block, we've found a way to poll.
# Edit freely — this list is meant to be experimented with.
POKE_MESSAGES = [
    {"type": "player"},
    {"type": "getPlayer"},
    {"type": "get", "what": "player"},
    {"type": "query", "target": "player"},
    {"type": "player", "action": "get"},
    {"type": "request", "data": "player"},
    {"type": "subscribe", "events": ["player"]},
    {"type": "device", "status": "ready"},
    {"type": "status"},
]


def _pretty(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    try:
        return json.dumps(json.loads(text), indent=2)
    except json.JSONDecodeError:
        return text


def _reader(sock: socket.socket, stop: threading.Event) -> None:
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            data = sock.recv(8192)
        except socket.timeout:
            continue
        except OSError:
            return
        if not data:
            print("\nconnection closed by the sim")
            stop.set()
            return
        ts = time.strftime("%H:%M:%S")
        print(f"\n[{ts}] received {len(data)} bytes:\n{_pretty(data)}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3111, help="sim API port (OpenGolfSim=3111, GSPro=921)")
    ap.add_argument("--no-ready", action="store_true", help="don't send the device-ready hello on connect")
    ap.add_argument("--shot", action="store_true", help="send one sample shot after connecting")
    ap.add_argument("--poke", action="store_true", help="send speculative query messages to fish for a player/club reply")
    ap.add_argument("--poke-delay", type=float, default=1.5, help="seconds between poke messages (default 1.5)")
    ap.add_argument("--newline", action="store_true", help="append a newline delimiter to every sent message (test NDJSON framing)")
    ap.add_argument("--coalesce-test", action="store_true", help="send device-ready + a player query in ONE write to test concatenated-message handling")
    ap.add_argument("--set-club", metavar="CLUBID", help="try to SET the club in the sim (e.g. 7I) using several candidate messages; watch the sim's club display to see which works")
    ap.add_argument("--set-club-delay", type=float, default=3.0, help="seconds between set-club attempts (default 3.0, time to watch the sim)")
    args = ap.parse_args()

    print(f"connecting to {args.host}:{args.port} ...")
    try:
        sock = socket.create_connection((args.host, args.port), timeout=10)
    except OSError as e:
        print(f"CONNECT FAILED: {e}")
        print("  → is the sim's developer API enabled and listening on this port?")
        return
    print("CONNECTED")

    stop = threading.Event()
    reader = threading.Thread(target=_reader, args=(sock, stop), daemon=True)
    reader.start()

    delim = "\n" if args.newline else ""

    def send(label: str, obj: dict) -> bool:
        raw = (json.dumps(obj) + delim).encode("utf-8")
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {label} → {raw!r}")
        try:
            sock.sendall(raw)
            return True
        except OSError as e:
            print(f"  send failed: {e}")
            stop.set()
            return False

    if args.coalesce_test:
        # Both objects in a SINGLE write — reproduces the concatenation that
        # triggers a 400. With --newline this tests whether a delimiter fixes it.
        blob = (json.dumps({"type": "device", "status": "ready"}) + delim
                + json.dumps({"type": "player"}) + delim).encode("utf-8")
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] coalesce-test (one write) → {blob!r}")
        try:
            sock.sendall(blob)
        except OSError as e:
            print(f"  send failed: {e}")
            stop.set()
    elif not args.no_ready:
        send("device-ready", {"type": "device", "status": "ready"})

    if args.shot:
        time.sleep(0.5)
        send("test-shot", {
            "type": "shot",
            "shot": {
                "ballSpeed": 135.0, "verticalLaunchAngle": 11.1,
                "horizontalLaunchAngle": 1.2, "spinAxis": -2.5, "spinSpeed": 4800,
            },
        })

    if args.poke:
        print(f"\npoking with {len(POKE_MESSAGES)} speculative query messages "
              f"({args.poke_delay}s apart) — watch for any reply above each gap\n")
        for obj in POKE_MESSAGES:
            if stop.is_set():
                break
            if not send("poke", obj):
                break
            time.sleep(args.poke_delay)

    if args.set_club:
        cid = args.set_club
        # Candidate "set the current club" messages, most-likely first. The first
        # mirrors the exact shape OGS *emits* for a player update.
        candidates = [
            {"type": "player", "data": {"club": {"id": cid, "name": cid}}},
            {"type": "player", "club": {"id": cid, "name": cid}},
            {"type": "club", "id": cid},
            {"type": "setClub", "club": cid},
            {"type": "device", "status": "ready", "club": cid},
        ]
        print(f"\nattempting to SET club to {cid!r} with {len(candidates)} candidate messages "
              f"({args.set_club_delay}s apart).")
        print(">>> WATCH OPENGOLFSIM'S CLUB DISPLAY <<< — note which attempt (if any) changes it.\n")
        for i, obj in enumerate(candidates, 1):
            if stop.is_set():
                break
            # Newline-terminate to avoid the concatenation 400 we found earlier.
            raw = (json.dumps(obj) + "\n").encode("utf-8")
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] set-club attempt {i}/{len(candidates)} → {json.dumps(obj)}")
            try:
                sock.sendall(raw)
            except OSError as e:
                print(f"  send failed: {e}")
                break
            time.sleep(args.set_club_delay)

    print("\nlistening — change clubs / hit shots in the sim now (Ctrl-C to stop)\n")
    try:
        while not stop.is_set():
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        stop.set()
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
