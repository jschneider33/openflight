"""OpenGolfSim codec — ResolvedShot <-> wire bytes.

Protocol: TCP :3111, JSON, no auth. Outbound shot and device-ready messages are
documented (https://help.opengolfsim.com/desktop/apis/); there is no documented
heartbeat or response/ack code, so heartbeat_bytes() returns None and inbound
parsing is best-effort (see clubs.py).
"""

import json
from typing import List, Optional

from openflight.opengolfsim.clubs import ogs_club_to_club
from openflight.sim.types import (
    InboundEvent,
    PlayerUpdate,
    ResolvedShot,
    ShotAck,
)

# Logical fields OpenGolfSim actually transmits (drives the UI provenance badges).
# OGS computes carry itself and takes no club data, so those are omitted.
_OGS_FIELDS = ["ball_speed", "vla", "hla", "spin_axis", "total_spin"]

_PLAYER_TYPES = {"player", "playerupdate", "player_update"}
_RESULT_TYPES = {"shot", "shotresult", "shot_result", "result"}


def _dumps(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


class OpenGolfSimCodec:
    """OpenGolfSim JSON wire format."""

    name = "opengolfsim"

    def __init__(self, units: str = "imperial"):
        self.units = units

    def build_shot(self, resolved: ResolvedShot) -> bytes:
        return _dumps(
            {
                "type": "shot",
                "unit": self.units,
                "shot": {
                    "ballSpeed": round(resolved.ball_speed_mph, 1),
                    "verticalLaunchAngle": round(resolved.vla, 1),
                    "horizontalLaunchAngle": round(resolved.hla, 1),
                    "spinAxis": round(resolved.spin_axis_deg, 1),
                    "spinSpeed": int(round(resolved.total_spin_rpm)),
                },
            }
        )

    def parse_inbound(self, frame: bytes) -> List[InboundEvent]:
        try:
            obj = json.loads(frame.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Malformed OpenGolfSim message: {e}") from e
        mtype = str(obj.get("type", "")).lower()
        if mtype in _PLAYER_TYPES:
            handed = obj.get("handed") or obj.get("hand")
            return [
                PlayerUpdate(
                    handed=str(handed) if handed else None,
                    club=ogs_club_to_club(obj.get("club")),
                )
            ]
        if mtype in _RESULT_TYPES:
            return [ShotAck(ok=True)]
        return []

    def heartbeat_bytes(self) -> Optional[bytes]:
        return None  # OpenGolfSim documents no keepalive

    def on_connect_bytes(self) -> Optional[bytes]:
        return _dumps({"type": "device", "status": "ready"})

    def fields_for_target(self) -> List[str]:
        return list(_OGS_FIELDS)
