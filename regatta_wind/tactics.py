from __future__ import annotations

import math

from .models import WindSample


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compass(deg: float) -> str:
    dirs = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
    return dirs[round(deg / 45) % 8]


def shift(prev: float, curr: float) -> float:
    """Signed angular shift in -180..180."""
    return (curr - prev + 180) % 360 - 180


def shift_arrow(delta: float) -> str:
    if abs(delta) < 5:
        return ""
    return "→" if delta > 0 else "←"


def oscillation_range(samples: list[WindSample]) -> float:
    """Peak-to-peak oscillation of wind direction (unwrapped)."""
    if len(samples) < 2:
        return 0.0
    dirs = [s.direction_deg for s in samples]
    unwrapped = [dirs[0]]
    for d in dirs[1:]:
        delta = (d - unwrapped[-1] + 180) % 360 - 180
        unwrapped.append(unwrapped[-1] + delta)
    return max(unwrapped) - min(unwrapped)
