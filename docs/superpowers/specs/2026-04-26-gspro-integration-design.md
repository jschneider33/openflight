# GSPro Integration

**Date:** 2026-04-26
**Status:** Approved (awaiting implementation plan)
**Branch:** `feat/gspro-integration`
**Scope:** Optional TCP client that streams OpenFlight shots into GSPro using the OpenConnectV1 protocol, with model-based fallback for missing fields and per-field UI provenance.

## Problem

OpenFlight produces ball/club speed, launch angles, and spin from radar hardware, but the data only renders in OpenFlight's own UI. Users running golf simulators (GSPro is the most common open-protocol target) currently can't play a sim round with an OpenFlight build — they'd have to retype every shot. We want a first-class, optional integration that delivers shots to GSPro as they're hit, while being honest about which fields are measurements vs. modeled.

## Approach

A new `src/openflight/gspro/` module owns a TCP client that speaks GSPro OpenConnectV1 (TCP, port 921, JSON, no auth, persistent socket — see [official spec](https://gsprogolf.com/GSProConnectV1.html)). The Flask server instantiates it optionally at startup based on config. A `shot_builder` translates `Shot` objects into OpenConnectV1 payloads, applying a model-based fallback when measurements are missing, and tags each field as `measured` or `estimated`. Provenance flows to the UI so the user sees exactly what hardware produced vs. what a model filled in.

The shot detection pipeline is **not** modified. The GSPro client subscribes as another listener alongside the existing WebSocket emitter.

## Architecture

### Module shape

```
src/openflight/gspro/
├── __init__.py           # public: GSProClient, GSProConfig, ShotPayload
├── client.py             # TCP socket lifecycle, send/receive, heartbeat thread
├── messages.py           # OpenConnectV1 JSON dataclasses + serialize/parse
├── shot_builder.py       # Shot → OpenConnectV1 payload + provenance
├── state.py              # ConnectionState enum, PlayerState
└── config.py             # Load/merge config file + CLI flag
```

Boundaries:
- `GSProClient` knows nothing about radars or shot detection. Accepts `Shot` objects, emits OpenConnectV1 frames.
- `shot_builder.build()` is a pure function — easy to unit test, only place that applies the fallback table.
- `client.py` owns the socket and threads; everything else is data.

### Server wiring

In `server.py` startup (~30 LOC, all new):

```python
gspro_config = load_gspro_config(args.gspro, args.no_gspro)  # CLI overrides file
if gspro_config.enabled:
    gspro_client = GSProClient(
        gspro_config,
        on_status=lambda evt: socketio.emit("gspro_status", evt),
        on_player=player_state.update_from_gspro,
    )
    gspro_client.start()
    shot_pipeline.add_listener(gspro_client.send_shot)
```

`shot_pipeline.add_listener` is a thin wrapper over the existing shot-emit code path; if the codebase doesn't have an explicit listener registry today, the implementation plan will introduce one (it's a one-method abstraction).

### Threading model

- **Connection thread**: owns the socket, runs the state machine, calls `recv()` in a loop, dispatches code 200/201/5xx
- **Heartbeat thread**: every 5s (configurable), if `state == CONNECTED` and no other send in last 5s, send heartbeat
- **Shot send**: synchronous on the existing shot pipeline thread (`sendall` is fast; failures are caught, logged, and surfaced to UI without blocking the pipeline)

## Data flow

```
OPS243 + KLD7s → existing shot pipeline ──┬──► WebSocket "shot" (UI)
                                          │
                                          └──► GSProClient.send_shot(shot)
                                                       │
                                                       ▼
                                              shot_builder.build(shot, player_state)
                                                       │   (fallback table + provenance)
                                                       ▼
                                              GSProSend{payload, provenance}
                                                       │
                                                       ├──► socket.sendall(json.dumps(payload).encode())
                                                       ├──► session_logger.log("gspro_send", ...)
                                                       └──► socketio.emit("gspro_shot", {payload, provenance})

GSPro ──► socket.recv ──► messages.parse ──► dispatch:
   200 → log shot ack
   201 → player_state.update(handed, club) → emit "gspro_player"
   5xx → log error + emit "gspro_error" (connection stays up)

Heartbeat thread (5s): if CONNECTED and idle, send {IsHeartBeat:true, LaunchMonitorIsReady:true, ...}
```

### Provenance model

`shot_builder.build()` returns:

```python
@dataclass
class GSProSend:
    payload: dict                      # OpenConnectV1 JSON
    provenance: dict[str, str]         # field_path → "measured" | "estimated"
    # e.g. {"BallData.Speed": "measured",
    #       "BallData.SpinAxis": "estimated",
    #       "BallData.TotalSpin": "measured"}
```

Provenance is written to the session log alongside the payload and shipped to the UI for badge rendering.

## Fallback table

Each row: source priority for the GSPro field, with each tier marked as measurement or model.

| GSPro field | Priority 1 (measured) | Priority 2 (estimated) | If still missing |
|---|---|---|---|
| `BallData.Speed` | `Shot.ball_speed_mph` | — (no honest model) | **Drop shot** |
| `BallData.VLA` | `Shot.launch_angle_vertical` | `_OPTIMAL_LAUNCH[club]` from `launch_monitor.py` | Should never happen (table covers every `ClubType`) |
| `BallData.HLA` | `Shot.launch_angle_horizontal` | `0.0` (assume centered) | — |
| `BallData.TotalSpin` | `Shot.spin_rpm` if `spin_confidence >= SPIN_CONFIDENCE_HIGH` (0.7) | Per-club spin model (see Open Dependencies) | — |
| `BallData.SpinAxis` | `Shot.spin_axis_deg` (already computed in `server.py:1041` as `HLA − club_path`) | `0.0` (straight) | — |
| `BallData.BackSpin` | `total × cos(axis)` | derived | — |
| `BallData.SideSpin` | `total × sin(axis)` | derived | — |
| `BallData.CarryDistance` | `Shot.estimated_carry_yards` (already a model) | derived | — |
| `ClubData.Speed` | `Shot.club_speed_mph` | `0.0` with `ContainsClubData:false` | — |
| `ClubData.Path` | `Shot.club_path_deg` | `0.0` | — |
| `ClubData.AngleOfAttack` / `Loft` / `FaceToTarget` / `Lie` | — (not measured) | `0.0` | — |

A shot is **dropped** (not sent to GSPro) only when ball speed is missing — every other field has a model fallback. Dropped shots are logged with reason and surfaced to the UI as a transient error.

The earlier "no SpinAxis = 0" guidance applies to the *default* behavior — when path data is genuinely unmeasurable (single-radar setup), `0.0` is the honest fallback labeled `estimated` in the UI.

## Configuration & lifecycle

### Config file

Checked in: `config/gspro.example.json`. User copies to `config/gspro.json` (gitignored, matches the existing `config/credentials.env.example` pattern referenced in `scripts/setup/setup_alloy.sh:74`).

```json
{
  "enabled": true,
  "host": "192.168.1.50",
  "port": 921,
  "device_id": "OpenFlight",
  "units": "Yards",
  "heartbeat_interval_s": 5
}
```

Missing file = `enabled: false`. Setup docs and the setup script will mention copying the example.

### CLI flags

In `scripts/start-kiosk.sh` and the underlying server entrypoint:

```
--gspro <host[:port]>     # enables, overrides host/port
--no-gspro                # disables even if config says enabled
```

Precedence: `--no-gspro` > `--gspro` > config file > defaults.

### Connection state machine

```
DISABLED ──► (config.enabled = true) ──► CONNECTING
CONNECTING ──► (socket open) ─────────► CONNECTED
CONNECTING ──► (refused/timeout) ─────► RECONNECT_BACKOFF
CONNECTED  ──► (recv error/EOF) ──────► RECONNECT_BACKOFF
RECONNECT_BACKOFF ──► (timer expired) ► CONNECTING
ANY ──► (shutdown signal) ────────────► STOPPED
```

Backoff: 1s → 2s → 4s → 8s → 16s → 30s, reset on successful connect. No max retries (runs as long as `enabled:true`).

### Heartbeat

5s default, configurable. Only sends if `state == CONNECTED` AND no other send in the last 5s (a real shot resets the timer). Payload sets `IsHeartBeat:true`, `LaunchMonitorIsReady:true`, `LaunchMonitorBallDetected:false`.

### Shutdown

Server's existing shutdown handler calls `gspro_client.stop()` → cancel threads, close socket cleanly, log.

### Status events

Emitted on the existing WebSocket as `gspro_status`:

```json
{"state": "connected", "host": "192.168.1.50", "port": 921}
{"state": "reconnecting", "attempt": 3, "next_retry_in_s": 8}
{"state": "error", "message": "Connection refused"}
```

## GSPro → OpenFlight: player & club feedback

When GSPro sends code 201 with `Player.{Handed, Club}`:

- **Club is canonical when GSPro is connected.** `player_state.club` is updated and used for subsequent shot logging and carry/spin model selection. The UI's club selector becomes display-only ("Club: 7-iron — from GSPro"). When GSPro disconnects, the local selector becomes editable again.
- **Unknown club codes** (anything outside `ClubType`) → `ClubType.UNKNOWN`, log warning, still send shots.
- **Handedness** (`RH`/`LH`) is logged and stored on `player_state.handed` but does not affect HLA/SpinAxis sign convention (those are absolute in OpenConnectV1, per the spec).

Club code mapping (`gspro/state.py`):

| GSPro code | `ClubType` |
|---|---|
| `DR` | `DRIVER` |
| `W3`, `W5`, `W7` | `WOOD_3`, `WOOD_5`, `WOOD_7` |
| `H3`, `H5`, `H7`, `H9` | `HYBRID_3`, `HYBRID_5`, `HYBRID_7`, `HYBRID_9` |
| `I2`–`I9` | `IRON_2`–`IRON_9` |
| `PW`, `GW`, `SW`, `LW` | `PW`, `GW`, `SW`, `LW` |
| `PT` | Putting is **out of scope** for v1 — log + ignore (see Out of Scope) |
| anything else | `UNKNOWN` + warning |

## UI affordances (v1)

### Status pill

Top bar, next to existing radar status indicators:

```
[OPS243 ●] [KLD7v ●] [KLD7h ●] [GSPro ● connected]
```

Colors: green = connected, amber = reconnecting/error, gray = disabled. Hover/tap shows host:port and last error. Driven by `gspro_status` WebSocket events.

### Per-shot provenance badges

Each shot card adds a "Sent to GSPro" section with per-field badges:
- `M` (green) = measured
- `E` (amber) = estimated/modeled

Sketch:

```
┌──────────────────────────────────┐
│ Shot #14   ↗ Sent to GSPro       │
│ Ball: 142 mph [M]                │
│ Spin: 2480 rpm [M]   Axis: -3° [M] │
│ HLA: 1.2° [M]   VLA: 12.4° [M]   │
│ Club path: 0° [E]                │
│ Club: 7-iron (from GSPro)        │
│ ── 5 measured / 1 estimated ──   │
└──────────────────────────────────┘
```

When all sent fields are measured, the summary line collapses to a single ✓.

### Settings panel — out of v1

Defer. v1 ships with config-file-only setup. Follow-up adds an in-app modal with enable toggle, host/port fields, and a "Test connection" button hitting a new `POST /api/gspro/config` endpoint.

## Tests

### Unit tests (no hardware, no network)

| Module | Tests |
|---|---|
| `messages.py` | Round-trip JSON encode/decode. Field names exact match GSPro spec. `APIversion` is string `"1"`. Code 200/201/5xx parsing. Heartbeat payload shape. |
| `shot_builder.py` | Each fallback rule fires when source is missing. Provenance dict matches expected. `BackSpin`/`SideSpin` correctly derived from total + axis. Missing ball speed raises `IncompleteShotError`. Unknown club code maps to `UNKNOWN`. Spin confidence below 0.7 triggers model fallback. |
| `state.py` | `PlayerState.update_from_gspro` mutates from code-201 message. Handedness stored not applied. |
| `config.py` | Defaults; `--gspro` overrides file; `--no-gspro` wins over `enabled:true`; missing file = disabled. |

### Integration test with mock GSPro

`tests/test_gspro_client_integration.py` spins a tiny `socketserver.TCPServer` on `127.0.0.1:0` (random port) that:
- Accepts connection, records bytes received
- Optionally sends scripted code-200/201/5xx replies
- Can disconnect mid-test

Cases:
- Start client → mock receives heartbeat within 6s (heartbeat_interval=1s for test speed)
- Send shot → mock receives correctly-shaped JSON
- Mock drops connection → client transitions through `RECONNECT_BACKOFF` → reconnects → resumes heartbeat (verify backoff timing with shorter intervals injected via config)
- Mock sends code 201 with `Club:"I7"` → next shot's payload reflects `ClubType.IRON_7`
- Mock sends code 5xx → `gspro_status` error event emitted, connection stays up
- Server shutdown → client `STOPPED`, socket closed cleanly
- Partial JSON / malformed reply from mock → logged, connection stays up

Mock-server harness is ~150 LOC; new pattern in this repo (existing tests are pure unit).

### Manual hardware test

One-page section in `docs/gspro-integration.md`:
1. Open GSPro with the OpenAPI Connect window
2. Set `config/gspro.json` to GSPro PC IP
3. Start kiosk
4. Verify status pill goes green
5. Hit a shot, verify it appears in GSPro
6. Confirm provenance badges match what hardware was actually connected

## Files modified / created

| File | Change |
|---|---|
| `src/openflight/gspro/__init__.py` | New |
| `src/openflight/gspro/client.py` | New |
| `src/openflight/gspro/messages.py` | New |
| `src/openflight/gspro/shot_builder.py` | New |
| `src/openflight/gspro/state.py` | New |
| `src/openflight/gspro/config.py` | New |
| `src/openflight/server.py` | Wire optional client, add status WS topic, add shot listener registry if missing |
| `src/openflight/session_logger.py` | New entry types `gspro_send`, `gspro_status`, `gspro_player` |
| `scripts/start-kiosk.sh` | `--gspro <host[:port]>` and `--no-gspro` flags |
| `config/gspro.example.json` | New, checked in |
| `.gitignore` | Add `config/gspro.json` |
| `ui/src/components/StatusBar.*` | Add GSPro status pill alongside existing radar pills |
| `ui/src/components/ShotCard.*` | Provenance badges and "Sent to GSPro" section (exact file paths chosen during implementation planning) |
| `docs/gspro-integration.md` | New: setup, config, manual test |
| `docs/raspberry-pi-setup.md` | Mention copying `config/gspro.example.json` |
| `tests/test_gspro_messages.py` | New unit tests |
| `tests/test_gspro_shot_builder.py` | New unit tests |
| `tests/test_gspro_config.py` | New unit tests |
| `tests/test_gspro_client_integration.py` | New mock-server integration test |
| `pyproject.toml` | No new deps expected (stdlib `socket`, `socketserver`, `threading`) |

## Open dependencies

**Per-club spin model.** The `BallData.TotalSpin` fallback needs a per-club spin table (e.g., driver≈2500, 7i≈7000). PR #61 (ballistics) is building this. Two paths:

- (a) **Wait for #61 to merge.** Cleanest. Risk: blocks this work on an external PR.
- (b) **Inline a small spin table** in `shot_builder.py` for v1, switch to the shared module after #61 lands. ~20 LOC duplication, clearly marked as temporary.

Recommendation: **(b)**. Don't block on #61. The duplication is small and deliberately replaceable.

## Out of scope (v1)

- **Putting mode.** GSPro `Club:"PT"` is logged but not specially handled. Doppler trigger likely wouldn't fire on a putt anyway.
- **Settings panel UI** (deferred follow-up; config file only in v1).
- **Per-shot retry / send queue.** If GSPro is disconnected at shot time, log and surface error. Old shots aren't useful to GSPro.
- **GSCloud (remote GSPro IP).** Should work mechanically (just a different host/port) but not validated as part of v1.
- **Spin axis from radar phase / camera.** D-plane approximation (`HLA − club_path`) is what we have; improving it is a separate research effort.
- **Player handedness mirroring.** Logged only; sign conventions remain absolute per OpenConnectV1.

## References

- [GSPro OpenConnectV1 official spec](https://gsprogolf.com/GSProConnectV1.html)
- [springbok/MLM2PRO-GSPro-Connector](https://github.com/springbok/MLM2PRO-GSPro-Connector) — reference TCP/JSON impl
- [tnbozman/gspro-interface OpenAPI feedback](https://github.com/tnbozman/gspro-interface/blob/main/OpenAPI-Documentation-Feedback.MD) — catalog of doc gaps
- `src/openflight/server.py:1041` — existing D-plane spin-axis derivation reused here
- `docs/superpowers/specs/2026-04-15-spin-angle-validation-design.md` — confidence thresholds reused for spin fallback gate
