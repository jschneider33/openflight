"""Data types for K-LD7 angle radar integration."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class KLD7Frame:
    """A single frame from the K-LD7 radar stream."""

    timestamp: float
    tdat: Optional[dict] = None  # {"distance", "speed", "angle", "magnitude"}
    pdat: list = field(default_factory=list)  # list of target dicts
    radc: Optional[bytes] = None  # 3072-byte raw ADC payload (RADC mode only)


@dataclass
class KLD7Angle:
    """Angle measurement extracted from K-LD7 ring buffer after a shot."""

    vertical_deg: Optional[float] = None
    horizontal_deg: Optional[float] = None
    distance_m: float = 0.0
    magnitude: float = 0.0
    confidence: float = 0.0
    num_frames: int = 0
    # "ball", "club", or None (unclassified / horizontal orientation)
    detection_class: Optional[str] = None
