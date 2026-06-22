"""Tests for the CF-NetCDF wind export (regular-grid path + validation)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr

from regatta_wind import wind_export
from regatta_wind.models import FieldFrame, FineField, TerrainGrid


def _regular_field(
    *, n_hours: int = 3, speed_kn: float = 10.0, direction: float = 270.0,
    step_h: int = 1, land: bool = True,
) -> FineField:
    """Synthetic FineField on a regular (separable) lat/lon grid, ascending axes."""
    ny, nx = 8, 10
    lat1d = np.linspace(42.71, 43.34, ny)        # ascending
    lon1d = np.linspace(131.36, 132.34, nx)      # ascending
    lon2d, lat2d = np.meshgrid(lon1d, lat1d)     # (ny, nx), separable
    is_land = np.zeros((ny, nx), dtype=bool)
    if land:
        is_land[:2, :2] = True                   # a small land corner
    t0 = datetime(2026, 6, 22, 0, 0, tzinfo=timezone.utc)
    frames = [
        FieldFrame(
            time=t0 + timedelta(hours=step_h * h),
            speed_kn=np.full((ny, nx), speed_kn),
            dir_deg=np.full((ny, nx), direction),
            gust_kn=np.full((ny, nx), speed_kn * 1.3),
            confidence=np.ones((ny, nx)),
        )
        for h in range(n_hours)
    ]
    terrain = TerrainGrid(lat=lat2d, lon=lon2d, elevation_m=np.zeros((ny, nx)), is_land=is_land)
    return FineField(terrain=terrain, times=[f.time for f in frames], frames=frames,
                     grid_km=1.0, source="Open-Meteo jma_seamless ~5 км (фолбэк)", trusted=True)


def test_export_writes_cf_netcdf_and_manifest(tmp_path):
    field = _regular_field()
    init = field.times[0]
    path = wind_export.export_wind_field(field, str(tmp_path), init)

    assert path.endswith(".nc")
    ds = xr.open_dataset(path)
    try:
        # dims, member axis, ascending coords
        assert ds["u10"].dims == ("member", "time", "lat", "lon")
        assert ds.sizes["member"] == 1
        assert np.all(np.diff(ds["lat"].values) > 0)
        assert np.all(np.diff(ds["lon"].values) > 0)
        # variables + units + reference height
        assert ds["u10"].attrs["units"] == "m s-1"
        assert ds["v10"].attrs["units"] == "m s-1"
        assert ds["u10"].attrs["reference_height"] == "10 m"
        assert ds["u10"].attrs["standard_name"] == "eastward_wind"
        assert "gust" in ds and "sea_mask" in ds
        # global attrs
        assert ds.attrs["Conventions"].startswith("CF-1")
        assert "EPSG:4326" in ds.attrs["crs"]
        assert ds.attrs["forecast_reference_time"] == "2026-06-22T00:00:00Z"
        assert ds.attrs["grid_resolution_m"] == 1000.0
        assert "UTC" in ds.attrs["time_zone_note"]
        # time monotonic, hourly, UTC
        steps = np.diff(ds["time"].values).astype("timedelta64[s]").astype(int)
        assert set(steps.tolist()) == {3600}
    finally:
        ds.close()

    json_name = path.split("/")[-1][:-3] + ".json"
    manifest = json.loads((tmp_path / json_name).read_text())
    assert manifest["n_members"] == 1
    assert manifest["variables"]["u10"] == "m s-1"
    assert manifest["time"]["n_times"] == 3
    assert manifest["grid"]["resolution_m"] == 1000.0
    assert manifest["init_time_utc"] == "2026-06-22T00:00:00Z"


def test_uv_components_match_speed_and_direction(tmp_path):
    # West wind (from 270°) blows toward the east → u10 > 0, v10 ≈ 0.
    field = _regular_field(speed_kn=10.0, direction=270.0)
    path = wind_export.export_wind_field(field, str(tmp_path), field.times[0])
    ds = xr.open_dataset(path)
    try:
        u = ds["u10"].values
        v = ds["v10"].values
        assert np.allclose(u, 10.0 * 0.514444, atol=1e-3)   # 10 kn → m/s, eastward
        assert np.allclose(v, 0.0, atol=1e-3)
        speed = np.sqrt(u ** 2 + v ** 2)
        assert np.allclose(speed, 10.0 * 0.514444, atol=1e-3)
    finally:
        ds.close()


def test_filename_encodes_init_and_domain(tmp_path):
    field = _regular_field()
    path = wind_export.export_wind_field(field, str(tmp_path), field.times[0], domain="pgb")
    assert path.split("/")[-1] == "regatta_wind_2026-06-22T00Z_pgb.nc"


def test_rejects_non_hourly_time(tmp_path):
    field = _regular_field(step_h=2)  # 2-hour steps
    with pytest.raises(ValueError, match="почасов"):
        wind_export.export_wind_field(field, str(tmp_path), field.times[0])


def test_rejects_implausible_wind_speed(tmp_path):
    field = _regular_field(speed_kn=200.0)  # ~103 m/s → out of 0–60 range
    with pytest.raises(ValueError, match="диапазона"):
        wind_export.export_wind_field(field, str(tmp_path), field.times[0])


def test_rejects_nan_over_water(tmp_path):
    field = _regular_field(land=True)        # land corner [:2,:2], rest water → sea_mask exists
    field.frames[1].speed_kn[5, 5] = np.nan  # NaN in a water cell
    with pytest.raises(ValueError, match="NaN"):
        wind_export.export_wind_field(field, str(tmp_path), field.times[0])


def test_curvilinear_grid_uses_2d_coords(tmp_path):
    # A rotated (curvilinear) grid → (member, time, y, x) with 2-D lat/lon coords.
    base = _regular_field()
    ny, nx = base.terrain.lat.shape
    lon2d = base.terrain.lon + 0.02 * (np.arange(ny)[:, None] - ny / 2)  # tilt lon by row
    terrain = TerrainGrid(lat=base.terrain.lat, lon=lon2d,
                          elevation_m=base.terrain.elevation_m, is_land=base.terrain.is_land)
    field = FineField(terrain=terrain, times=base.times, frames=base.frames,
                      grid_km=1.0, source="WRF d03 (1 км)", trusted=True)
    path = wind_export.export_wind_field(field, str(tmp_path), field.times[0])
    ds = xr.open_dataset(path)
    try:
        assert ds["u10"].dims == ("member", "time", "y", "x")
        assert ds["lat"].dims == ("y", "x")
        assert ds["lon"].dims == ("y", "x")
        assert ds.attrs["grid_layout"] == "curvilinear"
    finally:
        ds.close()
