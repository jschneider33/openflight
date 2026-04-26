"""Connection and player state for the GSPro client."""
import logging
from dataclasses import dataclass, field
from enum import Enum

from openflight.launch_monitor import ClubType

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECT_BACKOFF = "reconnecting"
    STOPPED = "stopped"


_GSPRO_CLUB_MAP = {
    "DR": ClubType.DRIVER,
    "W3": ClubType.WOOD_3, "W5": ClubType.WOOD_5, "W7": ClubType.WOOD_7,
    "H3": ClubType.HYBRID_3, "H5": ClubType.HYBRID_5,
    "H7": ClubType.HYBRID_7, "H9": ClubType.HYBRID_9,
    "I2": ClubType.IRON_2, "I3": ClubType.IRON_3, "I4": ClubType.IRON_4,
    "I5": ClubType.IRON_5, "I6": ClubType.IRON_6, "I7": ClubType.IRON_7,
    "I8": ClubType.IRON_8, "I9": ClubType.IRON_9,
    "PW": ClubType.PW, "GW": ClubType.GW, "SW": ClubType.SW, "LW": ClubType.LW,
    # "PT" intentionally absent — putting is out of scope for v1
}


def gspro_code_to_club(code: str) -> ClubType:
    """Map a GSPro club code (e.g. 'DR', 'I7') to ClubType. Unknown -> UNKNOWN."""
    if code == "PT":
        logger.info("[gspro] putter received — putting is out of scope, mapping to UNKNOWN")
        return ClubType.UNKNOWN
    club = _GSPRO_CLUB_MAP.get(code)
    if club is None:
        logger.warning("[gspro] unknown club code %r, mapping to UNKNOWN", code)
        return ClubType.UNKNOWN
    return club


@dataclass
class PlayerState:
    """Mutable player-level state (kept across shots)."""
    handed: str = "RH"
    club: ClubType = ClubType.DRIVER
    shot_counter: int = 0

    def next_shot_number(self) -> int:
        self.shot_counter += 1
        return self.shot_counter

    def update_from_gspro(self, player: dict) -> None:
        """Apply a GSPro Player block (from a code-201 response)."""
        if "Handed" in player:
            self.handed = str(player["Handed"])
        if "Club" in player:
            self.club = gspro_code_to_club(str(player["Club"]))
