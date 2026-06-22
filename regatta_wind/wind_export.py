"""Export the raw (un-smoothed) wind field to CF-NetCDF for an external router.

The route-optimisation app needs the **native simulation field**, not the
Gaussian-blurred version used for the on-screen gradient. That blur lives only in
``ui/wind_map.py`` and ``video.py`` (it builds a display image and never touches the
data), so the raw field is exactly the :class:`~regatta_wind.models.FineField`
produced by ``sources.wrf.read_wrfout`` / ``sources.openmeteo.fetch_fallback_field``
and consumed by ``app.py`` / ``cli.py``. This module writes that field as-is.

Output (next to each other in ``out_dir``):

* ``regatta_wind_<init>_<domain>.nc`` — CF-1.10 NetCDF4 (zlib). Wind as 10 m U/V in
  m/s on dims ``(member, time, lat, lon)`` for a regular lat/lon grid, or
  ``(member, time, y, x)`` with 2-D ``lat``/``lon`` coordinate variables for a
  curvilinear (WRF/Lambert) grid. ``member`` is present (size 1) so an ensemble can
  be added later without a format change.
* ``regatta_wind_<init>_<domain>.json`` — sidecar manifest with run metadata.

Nothing here resamples, interpolates or smooths — the native grid is written as-is.
Wind is exported as U/V (never speed+direction) so the router can average/interpolate
without the 0°/360° wrap.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import numpy as np
import xarray as xr

from .grid import speed_dir_to_uv
from .models import FineField

__version__ = "1.0.0"
log = logging.getLogger(__name__)

# FineField stores speed in the configured unit (project default: knots). Factor to m/s.
_TO_MS = {"kn": 0.514444, "kmh": 1.0 / 3.6, "mph": 1.0 / 2.236936, "ms": 1.0}
_MAX_WIND_MS = 60.0
_HOUR_S = 3600
_REGULAR_TOL_DEG = 1e-4  # axis spread below this across the "wrong" dim ⇒ separable grid
_TZ_NOTE = "data in UTC; local = UTC+10 (Asia/Vladivostok)"


# ── helpers ───────────────────────────────────────────────────────────────────
def _as_utc(dt: datetime | str) -> datetime:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _utc_naive64(dt: datetime) -> np.datetime64:
    return np.datetime64(_as_utc(dt).replace(tzinfo=None), "ns")


def _infer_domain(source: str) -> str:
    for d in ("d03", "d02", "d01"):
        if d in (source or ""):
            return d
    return "pgb"  # Peter the Great Bay


def _grid_layout(lat2d: np.ndarray, lon2d: np.ndarray):
    """('regular', lat1d, lon1d) for a separable mesh, else ('curvilinear', lat2d, lon2d).

    Regular = latitude constant along each row and longitude constant along each
    column (e.g. the Open-Meteo grid). WRF's Lambert grid is curvilinear.
    """
    lat2d = np.asarray(lat2d, dtype=float)
    lon2d = np.asarray(lon2d, dtype=float)
    lat_spread = float(np.max(np.std(lat2d, axis=1))) if lat2d.shape[1] > 1 else 0.0
    lon_spread = float(np.max(np.std(lon2d, axis=0))) if lon2d.shape[0] > 1 else 0.0
    if lat_spread < _REGULAR_TOL_DEG and lon_spread < _REGULAR_TOL_DEG:
        return "regular", lat2d.mean(axis=1), lon2d.mean(axis=0)
    return "curvilinear", lat2d, lon2d


_U_ATTRS = dict(standard_name="eastward_wind", long_name="Eastward wind component at 10 m",
                units="m s-1", reference_height="10 m")
_V_ATTRS = dict(standard_name="northward_wind", long_name="Northward wind component at 10 m",
                units="m s-1", reference_height="10 m")
_GUST_ATTRS = dict(standard_name="wind_speed_of_gust", long_name="10 m wind gust",
                   units="m s-1", reference_height="10 m")
_SEA_ATTRS = dict(long_name="sea mask (1 = water, 0 = land)", flag_values="0 1",
                  flag_meanings="land water")


def _stack_uv(field: FineField, n: int, to_ms: float):
    """(u, v, gust|None) as float32 arrays shaped (time, ny, nx) in m/s."""
    ny, nx = field.terrain.lat.shape
    u = np.empty((n, ny, nx), dtype="float32")
    v = np.empty((n, ny, nx), dtype="float32")
    has_gust = any(np.isfinite(np.asarray(fr.gust_kn)).any() for fr in field.frames[:n])
    gust = np.full((n, ny, nx), np.nan, dtype="float32") if has_gust else None
    for k, fr in enumerate(field.frames[:n]):
        uu, vv = speed_dir_to_uv(np.asarray(fr.speed_kn, float), np.asarray(fr.dir_deg, float))
        u[k] = (uu * to_ms).astype("float32")
        v[k] = (vv * to_ms).astype("float32")
        if gust is not None:
            gust[k] = (np.asarray(fr.gust_kn, float) * to_ms).astype("float32")
    return u, v, gust


def _build_dataset(field: FineField, init_utc: datetime, speed_unit: str):
    n = len(field.frames)
    if n == 0:
        raise ValueError("FineField не содержит кадров (frames пуст) — нечего экспортировать.")
    if len(field.times) < n:
        raise ValueError("Число времён меньше числа кадров — поле несогласовано.")

    to_ms = _TO_MS.get(speed_unit)
    if to_ms is None:
        raise ValueError(f"Неизвестная единица скорости {speed_unit!r} (ожидается {list(_TO_MS)}).")

    time_arr = np.array([_utc_naive64(t) for t in field.times[:n]], dtype="datetime64[ns]")
    u, v, gust = _stack_uv(field, n, to_ms)              # (time, ny, nx)
    u, v = u[None], v[None]                               # add member dim → (1, time, ny, nx)
    if gust is not None:
        gust = gust[None]

    is_land = np.asarray(field.terrain.is_land, dtype=bool)
    sea2d = np.where(is_land, 0, 1).astype("int8") if is_land.any() else None

    layout, a0, a1 = _grid_layout(field.terrain.lat, field.terrain.lon)
    members = np.array([0], dtype="int32")
    common_coords = {
        "member": ("member", members, {"long_name": "ensemble member"}),
        "time": ("time", time_arr,
                 {"standard_name": "time", "axis": "T", "long_name": "valid time (UTC)"}),
    }

    if layout == "regular":
        lat1d, lon1d = a0, a1
        if lat1d[0] > lat1d[-1]:  # enforce ascending lat
            lat1d, u, v = lat1d[::-1], u[..., ::-1, :], v[..., ::-1, :]
            gust = gust[..., ::-1, :] if gust is not None else None
            sea2d = sea2d[::-1, :] if sea2d is not None else None
        if lon1d[0] > lon1d[-1]:  # enforce ascending lon
            lon1d, u, v = lon1d[::-1], u[..., :, ::-1], v[..., :, ::-1]
            gust = gust[..., :, ::-1] if gust is not None else None
            sea2d = sea2d[:, ::-1] if sea2d is not None else None
        dims = ("member", "time", "lat", "lon")
        sea_dims = ("lat", "lon")
        coords = {
            **common_coords,
            "lat": ("lat", lat1d.astype("float64"),
                    {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"}),
            "lon": ("lon", lon1d.astype("float64"),
                    {"standard_name": "longitude", "units": "degrees_east", "axis": "X"}),
        }
    else:
        lat2d, lon2d = a0, a1
        dims = ("member", "time", "y", "x")
        sea_dims = ("y", "x")
        coords = {
            **common_coords,
            "lat": (("y", "x"), lat2d.astype("float64"),
                    {"standard_name": "latitude", "units": "degrees_north"}),
            "lon": (("y", "x"), lon2d.astype("float64"),
                    {"standard_name": "longitude", "units": "degrees_east"}),
        }

    data_vars = {"u10": (dims, u, _U_ATTRS), "v10": (dims, v, _V_ATTRS)}
    if gust is not None:
        data_vars["gust"] = (dims, gust, _GUST_ATTRS)
    if sea2d is not None:
        data_vars["sea_mask"] = (sea_dims, sea2d, _SEA_ATTRS)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ref_iso = init_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    attrs = {
        "Conventions": "CF-1.10",
        "title": "Regatta-wind raw 10 m wind field (native grid, un-smoothed)",
        "source": field.source,
        "institution": "regatta-wind",
        "history": f"{now_iso} created by regatta_wind.wind_export v{__version__}",
        "forecast_reference_time": ref_iso,
        "crs": "EPSG:4326 (WGS84)",
        "grid_resolution_m": float(round(field.grid_km * 1000.0, 1)),
        "grid_layout": layout,
        "time_zone_note": _TZ_NOTE,
        "comment": ("Raw simulation field for routing; this is NOT the smoothed "
                    "visualization field. Wind exported as 10 m U/V (never "
                    "speed/direction) to avoid 0/360 averaging errors."),
    }
    ds = xr.Dataset(data_vars=data_vars, coords=coords, attrs=attrs)
    return ds, layout


# ── validation ──────────────────────────────────────────────────────────────
def _validate(ds: xr.Dataset, layout: str) -> None:
    # time strictly monotonic, exactly 1 h
    if ds.sizes["time"] >= 2:
        steps = np.diff(ds["time"].values).astype("timedelta64[s]").astype(np.int64)
        if not np.all(steps == _HOUR_S):
            uniq = sorted(set(steps.tolist()))
            raise ValueError(f"Ось time не почасовая/не монотонна: шаги (с) = {uniq}.")

    u, v = ds["u10"].values, ds["v10"].values
    if u.shape != v.shape:
        raise ValueError(f"Формы u10 {u.shape} и v10 {v.shape} не совпадают.")
    speed = np.sqrt(u.astype("float64") ** 2 + v.astype("float64") ** 2)
    finite = np.isfinite(speed)
    if not finite.any():
        raise ValueError("Поле ветра целиком NaN — нет данных для экспорта.")
    mx = float(np.nanmax(speed))
    if mx > _MAX_WIND_MS:
        raise ValueError(f"Скорость ветра {mx:.1f} м/с вне диапазона 0–{_MAX_WIND_MS:.0f} "
                         "(вероятна ошибка единиц).")

    if "sea_mask" in ds:  # no NaN over water; land NaN allowed
        water = ds["sea_mask"].values == 1
        bad = (~np.isfinite(u)) & water[None, None, ...]
        if bad.any():
            raise ValueError(f"{int(bad.sum())} NaN-ячеек u10 над водой (по sea_mask).")

    if layout == "regular":
        lat, lon = ds["lat"].values, ds["lon"].values
        if not (np.all(np.diff(lat) > 0) and np.all(np.diff(lon) > 0)):
            raise ValueError("lat/lon должны строго возрастать.")
    else:
        lat2d = ds["lat"].values
        lon2d = ds["lon"].values
        if lat2d[0, :].mean() > lat2d[-1, :].mean() or lon2d[:, 0].mean() > lon2d[:, -1].mean():
            log.warning("Криволинейная сетка не возрастает по строкам/столбцам — "
                        "проверь ориентацию осей.")


# ── manifest & GRIB ───────────────────────────────────────────────────────────
def _write_manifest(path_json: str, ds: xr.Dataset, field: FineField, layout: str,
                    init_utc: datetime, domain: str, nc_name: str) -> None:
    lat, lon = ds["lat"].values, ds["lon"].values
    tvals = ds["time"].values
    variables = {"u10": "m s-1", "v10": "m s-1"}
    if "gust" in ds:
        variables["gust"] = "m s-1"
    if "sea_mask" in ds:
        variables["sea_mask"] = "1=water,0=land"
    manifest = {
        "exporter_version": __version__,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "init_time_utc": init_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "domain": domain,
        "source": field.source,
        "crs": "EPSG:4326",
        "bbox": {
            "lat_min": float(np.min(lat)), "lat_max": float(np.max(lat)),
            "lon_min": float(np.min(lon)), "lon_max": float(np.max(lon)),
        },
        "grid": {
            "layout": layout,
            "resolution_m": float(round(field.grid_km * 1000.0, 1)),
            "shape_member_time_lat_lon": [int(s) for s in ds["u10"].shape],
        },
        "time": {
            "step": "1h",
            "n_times": int(ds.sizes["time"]),
            "start_utc": str(np.datetime_as_string(tvals[0], unit="s")) + "Z",
            "end_utc": str(np.datetime_as_string(tvals[-1], unit="s")) + "Z",
            "timezone_note": _TZ_NOTE,
        },
        "n_members": int(ds.sizes["member"]),
        "variables": variables,
        "reference_height_m": 10,
        "files": {"netcdf": nc_name},
    }
    with open(path_json, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def _maybe_write_grib(ds: xr.Dataset, nc_path: str) -> str | None:
    """Best-effort GRIB2 export of 10 m U/V for qtVlm/Expedition cross-checks.

    Writing GRIB2 needs the low-level eccodes API (cfgrib only *reads*). Until that
    is implemented this is a no-op TODO that never blocks the NetCDF export.
    """
    try:
        import eccodes  # noqa: F401
    except Exception:  # noqa: BLE001
        log.warning("GRIB2-экспорт пропущен: eccodes не установлен (TODO). "
                    "NetCDF записан как обычно.")
        return None
    log.warning("GRIB2-запись ещё не реализована (TODO: eccodes-шаблон 10 m U/V). "
                "NetCDF записан как обычно.")
    return None


# ── public API ────────────────────────────────────────────────────────────────
def export_wind_field(
    source: FineField,
    out_dir: str,
    init_time: datetime | str,
    write_grib: bool = False,
    *,
    speed_unit: str = "kn",
    domain: str | None = None,
) -> str:
    """Write the raw wind field to CF-NetCDF and return the ``.nc`` path.

    Parameters
    ----------
    source:
        The raw, un-smoothed :class:`~regatta_wind.models.FineField` (straight from
        ``sources.wrf`` / ``sources.openmeteo``). It is written as-is — no resampling,
        interpolation or smoothing.
    out_dir:
        Output directory (created if missing).
    init_time:
        Forecast initialization time (UTC). Naive datetimes are assumed UTC.
    write_grib:
        Also attempt a GRIB2 of 10 m U/V (currently a no-op TODO; never blocks).
    speed_unit:
        Unit of ``FineField`` speeds (project default ``"kn"``); converted to m/s.
    domain:
        Domain tag for the filename/manifest; inferred from ``source.source`` if None.
    """
    if not hasattr(source, "frames") or not hasattr(source, "terrain"):
        raise TypeError("source должен быть FineField (с .terrain и .frames).")

    init_utc = _as_utc(init_time)
    domain = domain or _infer_domain(getattr(source, "source", ""))
    os.makedirs(out_dir, exist_ok=True)

    ds, layout = _build_dataset(source, init_utc, speed_unit)
    _validate(ds, layout)

    init_tag = init_utc.strftime("%Y-%m-%dT%HZ")
    nc_name = f"regatta_wind_{init_tag}_{domain}.nc"
    nc_path = os.path.join(out_dir, nc_name)

    encoding = {name: {"zlib": True, "complevel": 4} for name in ds.data_vars}
    encoding["time"] = {
        "units": f"hours since {init_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC",
        "calendar": "standard",
        "dtype": "float64",
    }
    ds.to_netcdf(nc_path, format="NETCDF4", encoding=encoding)

    _write_manifest(nc_path[:-3] + ".json", ds, source, layout, init_utc, domain, nc_name)
    if write_grib:
        _maybe_write_grib(ds, nc_path)

    log.info("Экспортировано: %s (%s, %d ч, %d членов)", nc_path, layout,
             ds.sizes["time"], ds.sizes["member"])
    return nc_path


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Экспорт сырого поля ветра regatta-wind в CF-NetCDF для роутера.")
    parser.add_argument("--wrfout", help="Путь к wrfout_d0X_* (сырое поле WRF).")
    parser.add_argument("--route", default="config/route.yaml",
                        help="YAML-маршрут (для фолбэка Open-Meteo, если без --wrfout).")
    parser.add_argument("--source", default="wrf", choices=["wrf", "open-meteo"],
                        help="Источник поля.")
    parser.add_argument("--domain", default=None, help="Тег домена (d02/d03/...).")
    parser.add_argument("--out-dir", default="export", help="Куда писать .nc/.json.")
    parser.add_argument("--init", default=None,
                        help="Время инициализации UTC (ISO). По умолчанию — первый срез поля.")
    parser.add_argument("--grib", action="store_true", help="Также попытаться записать GRIB2.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.wrfout:
        from .sources import wrf as wrfsrc
        domain = args.domain or wrfsrc._domain_from_path(args.wrfout)
        field = wrfsrc.read_wrfout(args.wrfout, domain=domain)  # full horizon, raw
    else:
        from .config import load_config
        from .sources.openmeteo import fetch_fallback_field
        cfg = load_config(args.route)
        field = fetch_fallback_field(cfg.area, cfg.forecast, cfg.timezone, grid_n=17)
        domain = args.domain or "pgb"

    init = args.init or field.times[0]
    out = export_wind_field(field, args.out_dir, init, write_grib=args.grib, domain=args.domain)
    print(out)


if __name__ == "__main__":
    main()
