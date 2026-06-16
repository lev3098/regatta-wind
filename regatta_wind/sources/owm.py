"""OpenWeatherMap: current weather + 3-h point forecasts at waypoints.

Real-time observation overlay on top of gridded WRF / Open-Meteo fields.
Requires OPENWEATHERMAP_API_KEY in the environment (free tier: 60 req/min,
1 000 calls/day — sufficient for a handful of race marks).

Free-tier endpoints used:
  /data/2.5/weather  — current conditions (one call per waypoint)
  /data/2.5/forecast — 3-h steps up to 5 days (one call per waypoint)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from ..models import WindSample

_BASE = "https://api.openweathermap.org"
_MS_TO_KN = 1.94384  # m/s → knots

# Free-tier default key (no personal data; safe to ship). Override via the
# OPENWEATHERMAP_API_KEY env var to use your own.
_DEFAULT_KEY = "469a1cd3d9fbc18298ccd241ab59469f"


def api_key() -> str | None:
    return os.environ.get("OPENWEATHERMAP_API_KEY") or _DEFAULT_KEY


def available() -> bool:
    return bool(api_key())


def fetch_current(lat: float, lon: float, *, timeout: float = 10.0) -> WindSample | None:
    """Current conditions at a point.  Returns None if no API key or request fails."""
    key = api_key()
    if not key:
        return None
    resp = requests.get(
        f"{_BASE}/data/2.5/weather",
        params={"lat": lat, "lon": lon, "appid": key, "units": "metric"},
        timeout=timeout,
    )
    resp.raise_for_status()
    d = resp.json()
    wind = d.get("wind", {})
    speed_ms = float(wind.get("speed", 0.0))
    gust_ms = float(wind.get("gust", speed_ms))
    direction = float(wind.get("deg", 0.0))
    dt_utc = datetime.fromtimestamp(d["dt"], tz=timezone.utc)
    return WindSample(
        time=dt_utc,
        speed_kn=round(speed_ms * _MS_TO_KN, 1),
        direction_deg=direction,
        gust_kn=round(gust_ms * _MS_TO_KN, 1),
        confidence=0.9,
    )


def sample_grid(
    bounds: tuple[float, float, float, float],
    n: int = 3,
) -> list[tuple[float, float, WindSample]]:
    """Current OWM obs on an n×n grid inset inside ``bounds`` (lat_lo,lat_hi,lon_lo,lon_hi).

    Independent of the named corner points — gives real wind spread across the area.
    Returns [(lat, lon, sample), …]; skips points that fail. [] if no key.
    """
    if not api_key():
        return []
    lat_lo, lat_hi, lon_lo, lon_hi = bounds
    # inset so samples sit inside the area, not on the very edge
    pad_lat = (lat_hi - lat_lo) * 0.12
    pad_lon = (lon_hi - lon_lo) * 0.12
    lats = [lat_lo + pad_lat + (lat_hi - lat_lo - 2 * pad_lat) * i / max(n - 1, 1) for i in range(n)]
    lons = [lon_lo + pad_lon + (lon_hi - lon_lo - 2 * pad_lon) * j / max(n - 1, 1) for j in range(n)]
    out: list[tuple[float, float, WindSample]] = []
    for la in lats:
        for lo in lons:
            try:
                s = fetch_current(la, lo)
            except Exception:  # noqa: BLE001
                s = None
            if s is not None:
                out.append((round(la, 4), round(lo, 4), s))
    return out


def fetch_forecast(
    lat: float,
    lon: float,
    hours: int = 12,
    *,
    timeout: float = 15.0,
) -> list[WindSample]:
    """3-h-step forecast at a single point (free tier /data/2.5/forecast).

    Returns at most ceil(hours/3)+1 samples, or [] if no key / request fails.
    """
    key = api_key()
    if not key:
        return []
    cnt = (hours + 2) // 3 + 1  # number of 3-h blocks to request
    resp = requests.get(
        f"{_BASE}/data/2.5/forecast",
        params={"lat": lat, "lon": lon, "appid": key, "units": "metric", "cnt": cnt},
        timeout=timeout,
    )
    resp.raise_for_status()
    out: list[WindSample] = []
    for item in resp.json().get("list", []):
        wind = item.get("wind", {})
        speed_ms = float(wind.get("speed", 0.0))
        gust_ms = float(wind.get("gust", speed_ms))
        direction = float(wind.get("deg", 0.0))
        dt_utc = datetime.fromtimestamp(item["dt"], tz=timezone.utc)
        out.append(
            WindSample(
                time=dt_utc,
                speed_kn=round(speed_ms * _MS_TO_KN, 1),
                direction_deg=direction,
                gust_kn=round(gust_ms * _MS_TO_KN, 1),
                confidence=0.85,
            )
        )
    return out
