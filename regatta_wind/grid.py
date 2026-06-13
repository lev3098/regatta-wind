"""Grid geometry and wind-vector helpers shared across sources and UI.

Wind directions are meteorological ("from"); vector components ``(u, v)`` are
eastward/northward in the *toward* convention (the direction the air moves).
"""

from __future__ import annotations

import numpy as np

KM_PER_DEG_LAT = 111.32


def km_to_deg_lat(km: float) -> float:
    return km / KM_PER_DEG_LAT


def km_to_deg_lon(km: float, at_lat: float) -> float:
    return km / (KM_PER_DEG_LAT * max(np.cos(np.radians(at_lat)), 1e-6))


def grid_dims_for(bounds: tuple[float, float, float, float], grid_km: float) -> tuple[int, int]:
    """Number of (ny, nx) cells covering *bounds* at roughly *grid_km* spacing."""
    lat_lo, lat_hi, lon_lo, lon_hi = bounds
    mid_lat = (lat_lo + lat_hi) / 2
    ny = max(int(round((lat_hi - lat_lo) / km_to_deg_lat(grid_km))) + 1, 2)
    nx = max(int(round((lon_hi - lon_lo) / km_to_deg_lon(grid_km, mid_lat))) + 1, 2)
    return ny, nx


def build_latlon_grid(
    bounds: tuple[float, float, float, float], ny: int, nx: int
) -> tuple[np.ndarray, np.ndarray]:
    """Regular lat/lon grid as two ``(ny, nx)`` arrays."""
    lat_lo, lat_hi, lon_lo, lon_hi = bounds
    lats = np.linspace(lat_lo, lat_hi, ny)
    lons = np.linspace(lon_lo, lon_hi, nx)
    lon2d, lat2d = np.meshgrid(lons, lats)
    return lat2d, lon2d


def speed_dir_to_uv(speed: np.ndarray, dir_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Meteorological speed/direction → toward-vector components (u east, v north)."""
    phi = np.radians(dir_deg)
    u = -speed * np.sin(phi)
    v = -speed * np.cos(phi)
    return u, v


def uv_to_speed_dir(u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Toward-vector components → meteorological speed/direction."""
    speed = np.hypot(u, v)
    dir_deg = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    return speed, dir_deg
