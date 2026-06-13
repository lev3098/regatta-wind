"""Coarse online fallback: Open-Meteo on a grid → :class:`FineField`.

This is **not** the high-resolution engine — it is what the app shows before the
first local WRF run finishes (which takes hours). Output is flagged
``trusted=False`` so the UI can warn that it is raw ~5 km model data with no
terrain refinement. All grid points are fetched in one request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import requests

from ..config import AreaConfig, ForecastConfig
from ..grid import build_latlon_grid
from ..models import FieldFrame, FineField, TerrainGrid

BASE_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = "wind_speed_10m,wind_direction_10m,wind_gusts_10m"


def fetch_fallback_field(
    area: AreaConfig,
    forecast: ForecastConfig,
    timezone_name: str,
    *,
    grid_n: int = 14,
    timeout: float = 30.0,
) -> FineField:
    bounds = area.bounds
    lat2d, lon2d = build_latlon_grid(bounds, grid_n, grid_n)
    ny, nx = lat2d.shape
    lats_flat = lat2d.ravel()
    lons_flat = lon2d.ravel()

    params = {
        "latitude": ",".join(f"{v:.4f}" for v in lats_flat),
        "longitude": ",".join(f"{v:.4f}" for v in lons_flat),
        "models": forecast.fallback_model,
        "hourly": HOURLY_VARS,
        "wind_speed_unit": forecast.wind_speed_unit,
        "timezone": timezone_name,
        "forecast_hours": forecast.hours_ahead + 1,  # include the current hour
        "past_hours": 0,
    }
    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    blocks = payload if isinstance(payload, list) else [payload]

    # Times come from the first block that has them.
    first = next((b for b in blocks if b.get("hourly", {}).get("time")), None)
    if first is None:
        raise RuntimeError("Open-Meteo вернул пустой ответ для области.")

    tz = timezone(timedelta(seconds=int(first.get("utc_offset_seconds", 0))))
    time_strs = first["hourly"]["time"]
    times = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in time_strs]
    nt = len(times)

    speed = np.full((nt, ny, nx), np.nan)
    direction = np.full((nt, ny, nx), np.nan)
    gust = np.full((nt, ny, nx), np.nan)

    for idx, block in enumerate(blocks):
        i, j = divmod(idx, nx)
        if i >= ny:
            break
        hourly = block.get("hourly", {})
        sp = hourly.get("wind_speed_10m") or []
        di = hourly.get("wind_direction_10m") or []
        gu = hourly.get("wind_gusts_10m") or []
        for k in range(min(nt, len(sp))):
            if sp[k] is not None:
                speed[k, i, j] = sp[k]
            if k < len(di) and di[k] is not None:
                direction[k, i, j] = di[k]
            if k < len(gu) and gu[k] is not None:
                gust[k, i, j] = gu[k]

    frames = [
        FieldFrame(
            time=times[k],
            speed_kn=speed[k],
            dir_deg=direction[k],
            gust_kn=gust[k],
            confidence=np.ones((ny, nx)),
        )
        for k in range(nt)
    ]

    terrain = TerrainGrid(
        lat=lat2d,
        lon=lon2d,
        elevation_m=np.zeros((ny, nx)),
        is_land=np.zeros((ny, nx), dtype=bool),
    )
    lat_lo, lat_hi, _, _ = bounds
    grid_km = round((lat_hi - lat_lo) * 111.32 / max(ny - 1, 1), 1)

    return FineField(
        terrain=terrain,
        times=times,
        frames=frames,
        grid_km=grid_km,
        source=f"Open-Meteo {forecast.fallback_model} ~5 км (фолбэк)",
        trusted=False,
    )
