"""Tests for the WRF reader against a synthetic wrfout file.

We can't run WRF here, so we fabricate a netCDF file with the same variable
names, dimensions and conventions WRF uses, and verify the reader handles the
tricky parts: UTC→local time, grid→earth wind rotation, terrain mask, unit
conversion and the racing-window truncation.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import netCDF4
import numpy as np
import pytest

from regatta_wind.models import FineField
from regatta_wind.sources import wrf as wrfsrc


def _make_wrfout(
    path: str,
    *,
    nt: int = 6,
    ny: int = 4,
    nx: int = 5,
    alpha_deg: float = 0.0,
    u_grid: float = 5.0,
    v_grid: float = 0.0,
    dx_m: float = 3000.0,
    with_gust: bool = True,
) -> None:
    """Write a minimal WRF-like file with uniform fields for deterministic checks."""
    a = math.radians(alpha_deg)
    with netCDF4.Dataset(path, "w") as nc:
        nc.createDimension("Time", nt)
        nc.createDimension("DateStrLen", 19)
        nc.createDimension("south_north", ny)
        nc.createDimension("west_east", nx)
        nc.DX = dx_m
        nc.DY = dx_m

        times = nc.createVariable("Times", "S1", ("Time", "DateStrLen"))
        for i in range(nt):
            label = f"2026-06-13_{i % 24:02d}:00:00"  # 19 chars
            times[i, :] = np.frombuffer(label.encode("ascii"), dtype="S1")

        def field(name, value):
            v = nc.createVariable(name, "f4", ("Time", "south_north", "west_east"))
            v[:] = np.full((nt, ny, nx), value, dtype="f4")
            return v

        # XLAT/XLONG: a simple regular grid around the bay.
        lat2d = np.broadcast_to(
            np.linspace(43.0, 43.0 + 0.03 * (ny - 1), ny)[:, None], (ny, nx)
        )
        lon2d = np.broadcast_to(
            np.linspace(131.8, 131.8 + 0.03 * (nx - 1), nx)[None, :], (ny, nx)
        )
        nc.createVariable("XLAT", "f4", ("Time", "south_north", "west_east"))[:] = (
            np.broadcast_to(lat2d, (nt, ny, nx))
        )
        nc.createVariable("XLONG", "f4", ("Time", "south_north", "west_east"))[:] = (
            np.broadcast_to(lon2d, (nt, ny, nx))
        )

        # Terrain: left half water (HGT 0, LANDMASK 0), right half land.
        hgt = np.zeros((ny, nx), dtype="f4")
        mask = np.zeros((ny, nx), dtype="f4")
        hgt[:, nx // 2:] = 120.0
        mask[:, nx // 2:] = 1.0
        nc.createVariable("HGT", "f4", ("Time", "south_north", "west_east"))[:] = (
            np.broadcast_to(hgt, (nt, ny, nx))
        )
        nc.createVariable("LANDMASK", "f4", ("Time", "south_north", "west_east"))[:] = (
            np.broadcast_to(mask, (nt, ny, nx))
        )

        field("COSALPHA", math.cos(a))
        field("SINALPHA", math.sin(a))
        field("U10", u_grid)
        field("V10", v_grid)
        if with_gust:
            field("WSPD10MAX", math.hypot(u_grid, v_grid) * 1.4)


def test_times_parsed_utc_to_local(tmp_path):
    p = str(tmp_path / "wrfout_d02_2026-06-13_00:00:00")
    _make_wrfout(p, nt=4)
    ff = wrfsrc.read_wrfout(p, timezone_name="Asia/Vladivostok")
    assert isinstance(ff, FineField)
    assert len(ff.times) == 4
    # 00:00 UTC → 10:00 Asia/Vladivostok (UTC+10)
    assert ff.times[0].hour == 10
    assert ff.times[0].utcoffset().total_seconds() == 10 * 3600
    # hourly cadence preserved
    assert (ff.times[1] - ff.times[0]).total_seconds() == 3600


def test_terrain_mask_from_landmask(tmp_path):
    p = str(tmp_path / "wrfout_d02_x")
    _make_wrfout(p, ny=4, nx=5)
    ff = wrfsrc.read_wrfout(p)
    # left half water, right half land
    assert not ff.terrain.is_land[0, 0]
    assert ff.terrain.is_land[0, -1]
    assert ff.terrain.elevation_m[0, -1] == pytest.approx(120.0, abs=1.0)


def test_speed_unit_conversion_knots(tmp_path):
    p = str(tmp_path / "wrfout_d02_x")
    _make_wrfout(p, u_grid=10.0, v_grid=0.0, alpha_deg=0.0)
    ff = wrfsrc.read_wrfout(p, wind_speed_unit="kn")
    # 10 m/s = 19.438 kn
    assert ff.frames[0].speed_kn[0, 0] == pytest.approx(19.438, abs=0.01)
    ff_ms = wrfsrc.read_wrfout(p, wind_speed_unit="ms")
    assert ff_ms.frames[0].speed_kn[0, 0] == pytest.approx(10.0, abs=0.01)


def test_wind_rotation_shifts_direction_by_alpha(tmp_path):
    """Same grid-relative wind, different projection rotation → direction shifts by α."""
    p0 = str(tmp_path / "wrfout_d02_a0")
    p10 = str(tmp_path / "wrfout_d02_a10")
    _make_wrfout(p0, u_grid=5.0, v_grid=0.0, alpha_deg=0.0)
    _make_wrfout(p10, u_grid=5.0, v_grid=0.0, alpha_deg=10.0)

    d0 = wrfsrc.read_wrfout(p0).frames[0].dir_deg[0, 0]
    d10 = wrfsrc.read_wrfout(p10).frames[0].dir_deg[0, 0]

    # Grid-east wind, no rotation → "from west" = 270°. Rotating the grid +10°
    # via the standard ARWpost grid→earth transform gives 260° (verified from
    # first principles), confirming the rotation is applied with correct sign.
    assert d0 == pytest.approx(270.0, abs=0.5)
    assert d10 == pytest.approx(260.0, abs=0.5)
    # speed is rotation-invariant
    s0 = wrfsrc.read_wrfout(p0).frames[0].speed_kn[0, 0]
    s10 = wrfsrc.read_wrfout(p10).frames[0].speed_kn[0, 0]
    assert s0 == pytest.approx(s10, abs=1e-3)


def test_direction_meteorological_convention(tmp_path):
    """Grid-east wind, no rotation: air moves toward east → wind is 'from west' = 270°."""
    p = str(tmp_path / "wrfout_d02_e")
    _make_wrfout(p, u_grid=6.0, v_grid=0.0, alpha_deg=0.0)
    ff = wrfsrc.read_wrfout(p)
    assert ff.frames[0].dir_deg[0, 0] == pytest.approx(270.0, abs=0.5)


def test_gust_present_and_absent(tmp_path):
    p = str(tmp_path / "wrfout_d02_g")
    _make_wrfout(p, u_grid=10.0, v_grid=0.0, with_gust=True)
    ff = wrfsrc.read_wrfout(p, wind_speed_unit="ms")
    assert ff.frames[0].gust_kn[0, 0] == pytest.approx(14.0, abs=0.1)

    p2 = str(tmp_path / "wrfout_d02_ng")
    _make_wrfout(p2, with_gust=False)
    ff2 = wrfsrc.read_wrfout(p2)
    assert np.isnan(ff2.frames[0].gust_kn[0, 0])


def test_hours_ahead_truncation(tmp_path):
    p = str(tmp_path / "wrfout_d02_h")
    _make_wrfout(p, nt=13)
    ff = wrfsrc.read_wrfout(p, hours_ahead=12)
    assert len(ff.frames) == 13  # 12 h ahead = 13 hourly frames (incl. t0)
    ff6 = wrfsrc.read_wrfout(p, hours_ahead=5)
    assert len(ff6.frames) == 6


def test_grid_km_from_dx(tmp_path):
    p = str(tmp_path / "wrfout_d03_x")
    _make_wrfout(p, dx_m=1000.0)
    ff = wrfsrc.read_wrfout(p, domain="d03")
    assert ff.grid_km == pytest.approx(1.0)
    assert "d03" in ff.source


def test_find_wrfout_picks_newest(tmp_path):
    import os
    import time

    old = tmp_path / "wrfout_d02_2026-06-13_00:00:00"
    new = tmp_path / "wrfout_d02_2026-06-13_06:00:00"
    _make_wrfout(str(old))
    time.sleep(0.01)
    _make_wrfout(str(new))
    os.utime(old, (1, 1))  # force old mtime
    found = wrfsrc.find_wrfout(str(tmp_path), "d02")
    assert found == str(new)
    assert wrfsrc.find_wrfout(str(tmp_path), "d03") is None
