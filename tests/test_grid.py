"""Tests for grid geometry and wind-vector conversions."""

from __future__ import annotations

import numpy as np
import pytest

from regatta_wind import grid


def test_uv_roundtrip():
    speed = np.array([0.0, 5.0, 12.3, 25.0])
    direction = np.array([0.0, 90.0, 215.0, 359.0])
    u, v = grid.speed_dir_to_uv(speed, direction)
    s2, d2 = grid.uv_to_speed_dir(u, v)
    assert np.allclose(s2, speed, atol=1e-9)
    # direction is undefined at zero speed; compare the rest
    assert np.allclose(d2[1:], direction[1:], atol=1e-6)


def test_meteorological_convention():
    # northerly (from north) blows toward south → v negative, u zero
    u, v = grid.speed_dir_to_uv(np.array([10.0]), np.array([0.0]))
    assert u[0] == pytest.approx(0.0, abs=1e-9)
    assert v[0] == pytest.approx(-10.0, abs=1e-9)
    # easterly (from east) blows toward west → u negative
    u, v = grid.speed_dir_to_uv(np.array([10.0]), np.array([90.0]))
    assert u[0] == pytest.approx(-10.0, abs=1e-9)
    assert v[0] == pytest.approx(0.0, abs=1e-9)


def test_build_latlon_grid_corners():
    bounds = (43.0, 43.6, 131.6, 132.2)
    lat2d, lon2d = grid.build_latlon_grid(bounds, 7, 9)
    assert lat2d.shape == (7, 9)
    assert lat2d[0, 0] == pytest.approx(43.0)
    assert lat2d[-1, 0] == pytest.approx(43.6)
    assert lon2d[0, 0] == pytest.approx(131.6)
    assert lon2d[0, -1] == pytest.approx(132.2)


def test_grid_dims_for_resolution():
    bounds = (43.0, 43.5, 131.6, 132.1)  # ~55 km lat span
    ny, nx = grid.grid_dims_for(bounds, grid_km=1.0)
    # 0.5° lat ≈ 55.6 km → ~56 cells at 1 km
    assert 50 <= ny <= 62
    assert nx >= 2


def test_km_to_deg():
    assert grid.km_to_deg_lat(111.32) == pytest.approx(1.0, abs=1e-6)
    # one degree of longitude shrinks with latitude
    assert grid.km_to_deg_lon(111.32, 0.0) == pytest.approx(1.0, abs=1e-3)
    assert grid.km_to_deg_lon(111.32, 60.0) == pytest.approx(2.0, abs=0.05)
