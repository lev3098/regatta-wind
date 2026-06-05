from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Waypoint:
    name: str
    lat: float
    lon: float


@dataclass
class WindSample:
    time: datetime
    speed_kn: float
    direction_deg: float
    gust_kn: float


@dataclass
class WaypointForecast:
    waypoint: Waypoint
    grid_lat: float
    grid_lon: float
    samples: list[WindSample]
