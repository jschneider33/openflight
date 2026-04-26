"""Translate Shot → GSPro OpenConnectV1 payload with model fallback + provenance.

Fallback policy is documented in
docs/superpowers/specs/2026-04-26-gspro-integration-design.md (Fallback table).
"""
import math
from dataclasses import asdict, dataclass, field
from typing import Dict

from openflight.gspro.messages import (
    BallData,
    ClubData,
    ShotDataOptions,
    ShotPayload,
)
from openflight.gspro.state import PlayerState
from openflight.launch_monitor import (
    _OPTIMAL_LAUNCH,
    SPIN_CONFIDENCE_HIGH,
    ClubType,
    Shot,
)


class IncompleteShotError(Exception):
    """Shot lacks the minimum fields required to send to GSPro (ball speed)."""


# Temporary per-club spin model (rpm). Will be replaced by the shared spin
# model from the ballistics module when PR #61 lands. See spec "Open
# dependencies" section for context.
SPIN_MODEL_RPM: Dict[ClubType, float] = {
    ClubType.DRIVER: 2500.0,
    ClubType.WOOD_3: 3000.0,
    ClubType.WOOD_5: 3500.0,
    ClubType.WOOD_7: 4000.0,
    ClubType.HYBRID_3: 3500.0,
    ClubType.HYBRID_5: 4000.0,
    ClubType.HYBRID_7: 4500.0,
    ClubType.HYBRID_9: 5000.0,
    ClubType.IRON_2: 4000.0,
    ClubType.IRON_3: 4500.0,
    ClubType.IRON_4: 5000.0,
    ClubType.IRON_5: 5500.0,
    ClubType.IRON_6: 6000.0,
    ClubType.IRON_7: 7000.0,
    ClubType.IRON_8: 8000.0,
    ClubType.IRON_9: 9000.0,
    ClubType.PW: 9500.0,
    ClubType.GW: 10000.0,
    ClubType.SW: 10500.0,
    ClubType.LW: 11000.0,
    ClubType.UNKNOWN: 5000.0,
}


@dataclass
class GSProSend:
    """Built payload + per-field provenance ('measured' or 'estimated')."""
    payload: dict
    provenance: Dict[str, str] = field(default_factory=dict)


def _resolve_total_spin(shot: Shot) -> tuple[float, str]:
    if (shot.spin_rpm is not None and shot.spin_rpm > 0
            and shot.spin_confidence is not None
            and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH):
        return float(shot.spin_rpm), "measured"
    return SPIN_MODEL_RPM.get(shot.club, 5000.0), "estimated"


def build(
    shot: Shot,
    player_state: PlayerState,
    device_id: str = "OpenFlight",
    units: str = "Yards",
) -> GSProSend:
    """Convert a Shot into a GSPro payload, applying fallbacks per the spec."""
    if shot.ball_speed_mph is None or shot.ball_speed_mph <= 0:
        raise IncompleteShotError("ball_speed_mph is required")

    provenance: Dict[str, str] = {"BallData.Speed": "measured"}

    # Vertical launch angle
    if shot.launch_angle_vertical is not None:
        vla = float(shot.launch_angle_vertical)
        provenance["BallData.VLA"] = "measured"
    else:
        vla = _OPTIMAL_LAUNCH.get(shot.club, 18.0)
        provenance["BallData.VLA"] = "estimated"

    # Horizontal launch angle
    if shot.launch_angle_horizontal is not None:
        hla = float(shot.launch_angle_horizontal)
        provenance["BallData.HLA"] = "measured"
    else:
        hla = 0.0
        provenance["BallData.HLA"] = "estimated"

    # Total spin (with confidence gate)
    total_spin, spin_prov = _resolve_total_spin(shot)
    provenance["BallData.TotalSpin"] = spin_prov

    # Spin axis (already a D-plane derivation in server.py:1041 when both KLD7s exist)
    if shot.spin_axis_deg is not None:
        spin_axis = float(shot.spin_axis_deg)
        axis_prov = "measured"
    else:
        spin_axis = 0.0
        axis_prov = "estimated"
    provenance["BallData.SpinAxis"] = axis_prov

    # Derived components
    axis_rad = math.radians(spin_axis)
    back_spin = total_spin * math.cos(axis_rad)
    side_spin = total_spin * math.sin(axis_rad)
    derived_prov = "measured" if (spin_prov == "measured" and axis_prov == "measured") else "estimated"
    provenance["BallData.BackSpin"] = derived_prov
    provenance["BallData.SideSpin"] = derived_prov

    # Carry — Shot.estimated_carry_yards already incorporates measured launch
    # angle when available, so reuse its provenance derivation.
    carry = float(shot.estimated_carry_yards)
    provenance["BallData.CarryDistance"] = "measured" if shot.has_launch_angle else "estimated"

    # Club data
    if shot.club_speed_mph is not None and shot.club_speed_mph > 0:
        club_speed = float(shot.club_speed_mph)
        contains_club = True
        provenance["ClubData.Speed"] = "measured"
    else:
        club_speed = 0.0
        contains_club = False
        provenance["ClubData.Speed"] = "estimated"

    if shot.club_path_deg is not None:
        club_path = float(shot.club_path_deg)
        provenance["ClubData.Path"] = "measured"
    else:
        club_path = 0.0
        provenance["ClubData.Path"] = "estimated"

    payload = ShotPayload(
        DeviceID=device_id,
        Units=units,
        ShotNumber=player_state.next_shot_number(),
        APIversion="1",
        BallData=BallData(
            Speed=round(shot.ball_speed_mph, 1),
            SpinAxis=round(spin_axis, 1),
            TotalSpin=round(total_spin, 0),
            BackSpin=round(back_spin, 0),
            SideSpin=round(side_spin, 0),
            HLA=round(hla, 1),
            VLA=round(vla, 1),
            CarryDistance=round(carry, 1),
        ),
        ClubData=ClubData(
            Speed=round(club_speed, 1),
            Path=round(club_path, 1),
        ),
        ShotDataOptions=ShotDataOptions(
            ContainsBallData=True,
            ContainsClubData=contains_club,
            LaunchMonitorIsReady=True,
            LaunchMonitorBallDetected=True,
            IsHeartBeat=False,
        ),
    )

    return GSProSend(payload=asdict(payload), provenance=provenance)
