"""Read a local WRF ``wrfout`` file into a :class:`FineField`.

WRF is the primary engine: it resolves the coast, terrain sheltering and the
sea/land breeze with real model physics at 3 km (optionally 1 km). This module
turns its output into the same gridded structure the UI and CLI consume.

Implementation notes (the subtle bits that make wind *correct*):

* **Times** in ``wrfout`` are UTC strings (``2026-06-13_00:00:00``). We parse to
  UTC and convert to the race timezone for display.
* **U10/V10 are grid-relative.** On a map projection the model grid is rotated
  from true north, so they must be rotated to earth-relative with the file's
  ``COSALPHA``/``SINALPHA`` before computing direction — otherwise the wind angle
  is off by the projection rotation (several degrees here). ARWpost/NCL do the
  same rotation::

      u_earth = u_grid·cosα − v_grid·sinα
      v_earth = v_grid·cosα + u_grid·sinα

* **Gust** uses ``WSPD10MAX`` (enable ``nwp_diagnostics=1`` in the namelist);
  absent → NaN.
* **Terrain** comes straight from ``HGT`` and ``LANDMASK`` — no extra API needed.
"""

from __future__ import annotations

import glob
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import netCDF4  # type: ignore[import-untyped]
import numpy as np

from ..models import FieldFrame, FineField, TerrainGrid

# m/s → target unit (matches Open-Meteo's wind_speed_unit keys).
_MS_TO = {"kn": 1.943844, "kmh": 3.6, "mph": 2.236936, "ms": 1.0}

_DOMAIN_RES_KM = {"d01": 12.0, "d02": 3.0, "d03": 1.0}


def find_wrfout(output_dir: str, domain: str = "d02") -> str | None:
    """Newest ``wrfout_<domain>_*`` file in *output_dir*, or ``None`` if none."""
    pattern = os.path.join(output_dir, f"wrfout_{domain}_*")
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return matches[0] if matches else None


def _strip_time_dim(arr: np.ndarray) -> np.ndarray:
    """WRF static fields carry a leading Time dim that is constant — drop it."""
    return arr[0] if arr.ndim == 3 else arr


def _parse_times(nc: "netCDF4.Dataset", tz: ZoneInfo) -> list[datetime]:
    raw = nc.variables["Times"][:]
    strings = netCDF4.chartostring(raw)
    out: list[datetime] = []
    for s in np.atleast_1d(strings):
        text = s.decode() if isinstance(s, bytes) else str(s)
        dt_utc = datetime.strptime(text, "%Y-%m-%d_%H:%M:%S").replace(tzinfo=timezone.utc)
        out.append(dt_utc.astimezone(tz))
    return out


def read_wrfout(
    path: str,
    *,
    timezone_name: str = "Asia/Vladivostok",
    wind_speed_unit: str = "kn",
    domain: str | None = None,
    hours_ahead: int | None = None,
) -> FineField:
    """Read *path* into a :class:`FineField`.

    Parameters
    ----------
    domain:
        Used only to label the source and pick the nominal resolution; the real
        grid spacing is read from the file's ``DX`` attribute.
    hours_ahead:
        If given, keep only the first ``hours_ahead`` + 1 frames (the racing
        window) so the UI is not cluttered with hours past the race.
    """
    tz = ZoneInfo(timezone_name)
    unit_factor = _MS_TO.get(wind_speed_unit, _MS_TO["kn"])

    with netCDF4.Dataset(path) as nc:
        times = _parse_times(nc, tz)

        lat = np.asarray(_strip_time_dim(nc.variables["XLAT"][:]), dtype=float)
        lon = np.asarray(_strip_time_dim(nc.variables["XLONG"][:]), dtype=float)
        hgt = np.asarray(_strip_time_dim(nc.variables["HGT"][:]), dtype=float)
        landmask = np.asarray(_strip_time_dim(nc.variables["LANDMASK"][:]), dtype=float)

        # Rotation grid→earth (identity if the file has no alpha, e.g. lat-lon).
        if "COSALPHA" in nc.variables and "SINALPHA" in nc.variables:
            cosa = np.asarray(_strip_time_dim(nc.variables["COSALPHA"][:]), dtype=float)
            sina = np.asarray(_strip_time_dim(nc.variables["SINALPHA"][:]), dtype=float)
        else:
            cosa = np.ones_like(hgt)
            sina = np.zeros_like(hgt)

        u10_all = np.asarray(nc.variables["U10"][:], dtype=float)  # (nt, ny, nx) m/s
        v10_all = np.asarray(nc.variables["V10"][:], dtype=float)
        gust_all = (
            np.asarray(nc.variables["WSPD10MAX"][:], dtype=float)
            if "WSPD10MAX" in nc.variables
            else None
        )

        dx_m = float(getattr(nc, "DX", _DOMAIN_RES_KM.get(domain or "d02", 3.0) * 1000.0))

    grid_km = round(dx_m / 1000.0, 3)
    terrain = TerrainGrid(
        lat=lat, lon=lon, elevation_m=hgt, is_land=(landmask > 0.5)
    )

    n = len(times)
    if hours_ahead is not None:
        n = min(n, hours_ahead + 1)

    frames: list[FieldFrame] = []
    for k in range(n):
        u_grid, v_grid = u10_all[k], v10_all[k]
        u_e = u_grid * cosa - v_grid * sina
        v_e = v_grid * cosa + u_grid * sina

        speed = np.hypot(u_e, v_e) * unit_factor
        # meteorological "from" direction
        direction = (np.degrees(np.arctan2(-u_e, -v_e)) + 360.0) % 360.0
        gust = (
            gust_all[k] * unit_factor
            if gust_all is not None
            else np.full_like(speed, np.nan)
        )
        frames.append(
            FieldFrame(
                time=times[k],
                speed_kn=speed,
                dir_deg=direction,
                gust_kn=gust,
                confidence=np.ones_like(speed),
            )
        )

    label = domain or _domain_from_path(path)
    res = _DOMAIN_RES_KM.get(label, grid_km)
    return FineField(
        terrain=terrain,
        times=times[:n],
        frames=frames,
        grid_km=grid_km,
        source=f"WRF {label} ({res:g} км)",
        trusted=True,
    )


def _domain_from_path(path: str) -> str:
    base = os.path.basename(path)
    for d in ("d03", "d02", "d01"):
        if d in base:
            return d
    return "d02"
