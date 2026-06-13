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


def unwrap_directions(dirs: list[float]) -> list[float]:
    """Unwrap a sequence of compass directions into a continuous curve.

    Avoids the 360°→0° jump so direction can be plotted as a smooth line and
    oscillation amplitude is meaningful.
    """
    if not dirs:
        return []
    out = [dirs[0]]
    for d in dirs[1:]:
        delta = (d - out[-1] + 180) % 360 - 180
        out.append(out[-1] + delta)
    return out


def oscillation_range(samples: list[WindSample]) -> float:
    """Peak-to-peak oscillation of wind direction (unwrapped)."""
    if len(samples) < 2:
        return 0.0
    unwrapped = unwrap_directions([s.direction_deg for s in samples])
    return max(unwrapped) - min(unwrapped)
