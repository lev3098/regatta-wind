"""Open-Meteo client for JMA MSM model (5 km).

Docs: https://open-meteo.com/en/docs/jma-api
License: CC BY 4.0. No API key required.

Multiple coordinates are sent in one request (comma-separated) and
the API returns results in the same order — so the full route is
fetched in a single HTTP call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .config import ForecastConfig
from .models import Waypoint, WaypointForecast, WindSample

BASE_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = "wind_speed_10m,wind_direction_10m,wind_gusts_10m"


def fetch_forecasts(
    waypoints: list[Waypoint],
    cfg: ForecastConfig,
    timezone_name: str,
    *,
    timeout: float = 30.0,
) -> list[WaypointForecast]:
    """Fetch wind forecast for all waypoints in a single API call."""
    if not waypoints:
        return []

    params = {
        "latitude": ",".join(f"{w.lat:.4f}" for w in waypoints),
        "longitude": ",".join(f"{w.lon:.4f}" for w in waypoints),
        "models": cfg.model,
        "hourly": HOURLY_VARS,
        "wind_speed_unit": cfg.wind_speed_unit,
        "timezone": timezone_name,
        "forecast_hours": cfg.hours_ahead,
        "past_hours": cfg.past_hours,
    }

    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()

    # Single waypoint → dict; multiple → list
    blocks = payload if isinstance(payload, list) else [payload]

    return [_parse_block(wp, block) for wp, block in zip(waypoints, blocks)]


def _parse_block(waypoint: Waypoint, block: dict) -> WaypointForecast:
    hourly = block.get("hourly", {})
    times = hourly.get("time", [])
    speeds = hourly.get("wind_speed_10m", [])
    dirs = hourly.get("wind_direction_10m", [])
    gusts = hourly.get("wind_gusts_10m", [])

    # Open-Meteo returns local time strings when timezone is set;
    # utc_offset_seconds lets us attach the correct tzinfo.
    tz = timezone(timedelta(seconds=int(block.get("utc_offset_seconds", 0))))

    samples: list[WindSample] = []
    for t, spd, dr, gst in zip(times, speeds, dirs, gusts):
        if spd is None or dr is None:
            continue
        samples.append(
            WindSample(
                time=datetime.fromisoformat(t).replace(tzinfo=tz),
                speed_kn=float(spd),
                direction_deg=float(dr),
                gust_kn=float(gst) if gst is not None else float("nan"),
            )
        )

    return WaypointForecast(
        waypoint=waypoint,
        grid_lat=float(block.get("latitude", waypoint.lat)),
        grid_lon=float(block.get("longitude", waypoint.lon)),
        samples=samples,
    )
