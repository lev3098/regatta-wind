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
# Centre chosen so the validated 1 km WRF nest covers all the named landmarks
# below (Amur Bay, Ussuri Bay, Slavyansky Bay, the islands) with margin.
DEFAULT_CENTER_LAT = 43.00
DEFAULT_CENTER_LON = 131.75

# Fixed reference landmarks the forecast must cover. These are NOT editable race
# marks — they orient the map and anchor the OWM observations / per-point charts.
# Coordinates are approximate; correct any in route.yaml's `landmarks:` block.
DEFAULT_LANDMARKS: list[Waypoint] = [
    Waypoint("Мыс Песчаный", 43.34, 131.79),       # запад Амурского залива
    Waypoint("Бухта Миноносок", 42.71, 131.36),    # Славянский залив
    Waypoint("Остров Попова", 42.95, 131.73),      # архипелаг Императрицы Евгении
    Waypoint("Остров Аскольд", 42.78, 132.34),     # вход в Уссурийский залив
    Waypoint("Кирпичный завод", 43.23, 132.05),    # север Уссурийского зал. (≈)
]


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
    half_span_deg: float = 0.75       # covers the whole bay; frames map + fallback grid

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
    landmarks: list[Waypoint]
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

    # Fixed reference landmarks (optional override of the built-in set).
    raw_lm = data.get("landmarks")
    if raw_lm:
        landmarks = [
            Waypoint(name=w["name"], lat=float(w["lat"]), lon=float(w["lon"]))
            for w in raw_lm
        ]
    else:
        landmarks = list(DEFAULT_LANDMARKS)

    ar = data.get("area", {})
    area = AreaConfig(
        center_lat=float(ar.get("center_lat", DEFAULT_CENTER_LAT)),
        center_lon=float(ar.get("center_lon", DEFAULT_CENTER_LON)),
        half_span_deg=float(ar.get("half_span_deg", AreaConfig().half_span_deg)),
    )

    return RaceConfig(
        name=data.get("name", "Race"),
        timezone=data.get("timezone", "Asia/Vladivostok"),
        landmarks=landmarks,
        forecast=forecast,
        wrf=wrf,
        area=area,
    )
