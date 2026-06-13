# OpenGolfSim

OpenFlight streams shots into [OpenGolfSim](https://opengolfsim.com/) using its
TCP/JSON developer API.

See the connector architecture in [README.md](README.md). This page covers
requirements and setup specific to OpenGolfSim.

## Requirements

- **OpenGolfSim desktop app** with the developer API enabled. The API listens
  on **TCP port 3111**.
- **Network reachability.** OpenFlight (the Raspberry Pi) and the PC running
  OpenGolfSim must be on the same LAN. You need the OpenGolfSim PC's IP.
- No account/credentials are sent by OpenFlight — the API has no auth.

Check OpenGolfSim's own documentation for enabling the developer API and any
account/licensing requirements: <https://help.opengolfsim.com/desktop/apis/>.

## Setup

1. **Enable the developer API in OpenGolfSim** and confirm it's listening on
   port 3111.

2. **Find the OpenGolfSim PC's IP** (e.g. `192.168.1.60`).

3. **Configure OpenFlight.** Copy the example config if you haven't already:
   ```bash
   cp config/sim.example.json config/sim.json
   ```
   Enable the OpenGolfSim connector:
   ```jsonc
   {
     "connectors": [
       {
         "type": "opengolfsim",
         "enabled": true,
         "host": "192.168.1.60",
         "port": 3111,
         "units": "imperial"
       }
     ]
   }
   ```
   Or pass it at launch:
   ```bash
   scripts/start-kiosk.sh --kld7 --opengolfsim 192.168.1.60
   ```
   `units` is `imperial` (mph) or `metric` (m/s) — match your OpenGolfSim
   setting.

4. **Start OpenFlight.** On connect it sends a `{"type":"device","status":"ready"}`
   frame; the header OpenGolfSim pill should turn **green**.

5. **Hit a shot.** It appears in OpenGolfSim, and the "Sent to OpenGolfSim"
   panel shows the values sent with measured/estimated badges.

## What gets sent

OpenGolfSim takes a compact ball-only message; it computes carry itself and
does not accept club data:

```json
{
  "type": "shot",
  "unit": "imperial",
  "shot": {
    "ballSpeed": 135.0,
    "verticalLaunchAngle": 11.1,
    "horizontalLaunchAngle": 1.2,
    "spinAxis": -2.5,
    "spinSpeed": 4800
  }
}
```

Spin (`spinSpeed`) uses the measured value when high-confidence, otherwise a
per-club model — the "Sent to OpenGolfSim" badges tell you which.

## Differences from GSPro

- **No heartbeat.** OpenGolfSim documents no keepalive, so OpenFlight sends
  none. Connection health relies on TCP-level detection plus automatic
  reconnect, so a silently dropped socket is detected a little less promptly
  than GSPro's heartbeat-backed link.
- **No documented ack codes.** Shot acknowledgements aren't documented, so
  OpenFlight treats sends as fire-and-forget (errors only surface as socket
  failures).

## Club selection (one-way: OpenGolfSim → OpenFlight)

Club sync is **one-directional**. OpenGolfSim's API has no command for a device
to set the current club — the device can only send `device` status and `shot`
data. So OpenFlight cannot push your OpenFlight-side club choice to the sim;
instead the **sim is the source of truth** and pushes club changes to
OpenFlight via its `player` message:

```json
{
  "type": "player",
  "data": {
    "playerId": "…",
    "currentPosition": { "x": 0, "y": 0, "z": 0 },
    "club": { "name": "3W", "id": "3W", "distance": 205 }
  }
}
```

OpenFlight reads `data.club` and maps the two-letter id / name to its internal
club (used for shot tagging and the carry/spin model). The documented player
message carries no handedness field, so handedness is not tracked for
OpenGolfSim. The id/name mapping lives in
`src/openflight/opengolfsim/clubs.py`.

> The message *shape* now matches OpenGolfSim's documented `player` event, but
> the club-id vocabulary (e.g. how irons/wedges are abbreviated) hasn't been
> verified against the live app. If a club doesn't map, capture the actual
> inbound JSON and extend the mapping — it won't affect outbound shots.

## Troubleshooting

- **Pill stays amber (reconnecting):** OpenFlight can't reach `host:port`.
  Verify the developer API is enabled, the IP is correct, and port 3111 isn't
  firewalled.
- **Shots don't appear:** confirm OpenGolfSim is on a hittable screen and the
  API is connected. Check `sim_send` entries in the session log to confirm
  OpenFlight is sending.

## References

- [OpenGolfSim Developer API](https://help.opengolfsim.com/desktop/apis/)
