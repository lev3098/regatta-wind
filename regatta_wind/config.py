"""Race configuration loaded from YAML.

New blocks vs. the original single-model tool:

* ``forecast.source`` selects the engine — ``wrf`` (local high-res output) or
  ``open-meteo`` (the coarse online fallback).
* ``wrf`` points at the directory where ``wrfout_d0X.nc`` files land and which
  nested domain to prefer.
* ``area`` frames the map and the fallback grid.

All blocks are optional; sensible defaults keep old route files working.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .models import Waypoint

# Peter the Great Bay (Vladivostok) — the tool's home water.
DEFAULT_CENTER_LAT = 43.08
DEFAULT_CENTER_LON = 131.88


@dataclass(frozen=True)
class ForecastConfig:
    source: str = "wrf"               # "wrf" | "open-meteo"
    wind_speed_unit: str = "kn"
    hours_ahead: int = 12             # racing horizon
    tactical_window: tuple[float, float] = (3.0, 5.0)
    fallback_model: str = "jma_seamless"  # used by the open-meteo source/fallback


@dataclass(frozen=True)
class WrfConfig:
    output_dir: str = "wrf/output"
    domain: str = "d02"               # d02 = 3 km, d03 = 1 km (optional nest)


@dataclass(frozen=True)
class AreaConfig:
    center_lat: float = DEFAULT_CENTER_LAT
    center_lon: float = DEFAULT_CENTER_LON
    half_span_deg: float = 0.30       # ~33 km half-width; frames map + fallback grid

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(lat_lo, lat_hi, lon_lo, lon_hi)."""
        return (
            self.center_lat - self.half_span_deg,
            self.center_lat + self.half_span_deg,
            self.center_lon - self.half_span_deg,
            self.center_lon + self.half_span_deg,
        )


@dataclass(frozen=True)
class RaceConfig:
    name: str
    timezone: str
    waypoints: list[Waypoint]
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    wrf: WrfConfig = field(default_factory=WrfConfig)
    area: AreaConfig = field(default_factory=AreaConfig)


def load_config(path: str) -> RaceConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    fc = data.get("forecast", {})
    forecast = ForecastConfig(
        source=fc.get("source", "wrf"),
        wind_speed_unit=fc.get("wind_speed_unit", "kn"),
        hours_ahead=int(fc.get("hours_ahead", 12)),
        tactical_window=tuple(fc.get("tactical_window", [3, 5])),  # type: ignore[arg-type]
        fallback_model=fc.get("fallback_model", "jma_seamless"),
    )

    wr = data.get("wrf", {})
    wrf = WrfConfig(
        output_dir=wr.get("output_dir", "wrf/output"),
        domain=wr.get("domain", "d02"),
    )

    waypoints = [
        Waypoint(name=w["name"], lat=float(w["lat"]), lon=float(w["lon"]))
        for w in data.get("waypoints", [])
    ]

    ar = data.get("area", {})
    if ar:
        area = AreaConfig(
            center_lat=float(ar.get("center_lat", DEFAULT_CENTER_LAT)),
            center_lon=float(ar.get("center_lon", DEFAULT_CENTER_LON)),
            half_span_deg=float(ar.get("half_span_deg", 0.30)),
        )
    elif waypoints:
        # Centre the area on the mean of the marks when not given explicitly.
        area = AreaConfig(
            center_lat=sum(w.lat for w in waypoints) / len(waypoints),
            center_lon=sum(w.lon for w in waypoints) / len(waypoints),
        )
    else:
        area = AreaConfig()

    return RaceConfig(
        name=data.get("name", "Race"),
        timezone=data.get("timezone", "Asia/Vladivostok"),
        waypoints=waypoints,
        forecast=forecast,
        wrf=wrf,
        area=area,
    )


def save_waypoints(path: str, waypoints: list[Waypoint], area: AreaConfig | None = None) -> None:
    """Persist marks (and optionally the area) back to a route YAML.

    Reads the existing file to preserve unrelated blocks, then rewrites the
    ``waypoints`` (and ``area``) sections. Used by the interactive route editor
    so the user never has to hand-edit coordinates.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}

    data["waypoints"] = [
        {"name": w.name, "lat": round(w.lat, 5), "lon": round(w.lon, 5)} for w in waypoints
    ]
    if area is not None:
        data["area"] = {
            "center_lat": round(area.center_lat, 5),
            "center_lon": round(area.center_lon, 5),
            "half_span_deg": round(area.half_span_deg, 4),
        }

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
