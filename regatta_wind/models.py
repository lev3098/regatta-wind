"""Data models for the regatta wind pipeline.

Two families of objects:

* **Point models** (`Waypoint`, `WindSample`, `WaypointForecast`) — used by the
  CLI and per-mark charts.
* **Field models** (`TerrainGrid`, `FieldFrame`, `FineField`) — hold gridded numpy
  arrays. Both the WRF reader (`sources.wrf`) and the Open-Meteo fallback
  (`sources.openmeteo`) produce a `FineField`, so the whole UI/CLI is engine-agnostic.

Wind direction throughout follows the **meteorological convention**: the angle
the wind blows *from* (0° = northerly, blowing toward the south).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np


# ── point models ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Waypoint:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class WindSample:
    time: datetime
    speed_kn: float
    direction_deg: float
    gust_kn: float
    confidence: float = 1.0  # 0..1 (1 = trusted; lower for the coarse fallback)


@dataclass(frozen=True)
class WaypointForecast:
    waypoint: Waypoint
    grid_lat: float
    grid_lon: float
    samples: list[WindSample]
    elevation_m: float = 0.0
    is_land: bool = False


# ── field models ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TerrainGrid:
    """Static terrain on the fine grid. Arrays share shape ``(ny, nx)``.

    For the WRF source these come straight from ``HGT`` / ``LANDMASK`` in the
    output file; for the fallback they are filled from the Open-Meteo elevation
    API (or left flat when unavailable).
    """

    lat: np.ndarray
    lon: np.ndarray
    elevation_m: np.ndarray
    is_land: np.ndarray  # bool, True over land

    @property
    def shape(self) -> tuple[int, int]:
        return self.elevation_m.shape  # type: ignore[return-value]


@dataclass(frozen=True)
class FieldFrame:
    """Wind field at a single forecast hour. Arrays are ``(ny, nx)``."""

    time: datetime
    speed_kn: np.ndarray
    dir_deg: np.ndarray     # meteorological (from) direction
    gust_kn: np.ndarray     # may be all-NaN if the source has no gust diagnostic
    confidence: np.ndarray  # 0..1


@dataclass(frozen=True)
class FineField:
    """Full gridded product over the forecast window, from any engine."""

    terrain: TerrainGrid
    times: list[datetime]
    frames: list[FieldFrame]
    grid_km: float
    source: str  # human-readable label, e.g. "WRF d02 (3 км)" or "Open-Meteo 5 км (фолбэк)"
    trusted: bool = True  # False for the coarse fallback → UI shows a warning banner

    def nearest_index(self, lat: float, lon: float) -> tuple[int, int]:
        """Row/col of the fine cell closest to a coordinate."""
        d2 = (self.terrain.lat - lat) ** 2 + (self.terrain.lon - lon) ** 2
        i, j = np.unravel_index(int(np.argmin(d2)), d2.shape)
        return int(i), int(j)

    def sample_series(self, lat: float, lon: float) -> list[WindSample]:
        """Extract the per-hour series at the cell nearest a coordinate."""
        i, j = self.nearest_index(lat, lon)
        out: list[WindSample] = []
        for t, frame in zip(self.times, self.frames):
            out.append(
                WindSample(
                    time=t,
                    speed_kn=float(frame.speed_kn[i, j]),
                    direction_deg=float(frame.dir_deg[i, j]),
                    gust_kn=float(frame.gust_kn[i, j]),
                    confidence=float(frame.confidence[i, j]),
                )
            )
        return out
