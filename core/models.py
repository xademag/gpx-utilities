from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GPXPoint:
    lat: float
    lon: float
    ele: Optional[float] = None
    time: Optional[str] = None
    extensions: Optional[str] = None


@dataclass
class GPXTrack:
    name: str = ""
    points: List[GPXPoint] = field(default_factory=list)
