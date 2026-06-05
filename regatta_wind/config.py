from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .models import Waypoint


@dataclass
class ForecastConfig:
    model: str = "jma_seamless"
    wind_speed_unit: str = "kn"
    hours_ahead: int = 24
    past_hours: int = 3
    tactical_window: tuple[float, float] = (3.0, 5.0)


@dataclass
class RaceConfig:
    name: str
    timezone: str
    waypoints: list[Waypoint]
    forecast: ForecastConfig = field(default_factory=ForecastConfig)


def load_config(path: str) -> RaceConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    fc = data.get("forecast", {})
    forecast = ForecastConfig(
        model=fc.get("model", "jma_seamless"),
        wind_speed_unit=fc.get("wind_speed_unit", "kn"),
        hours_ahead=fc.get("hours_ahead", 24),
        past_hours=fc.get("past_hours", 3),
        tactical_window=tuple(fc.get("tactical_window", [3, 5])),
    )

    waypoints = [
        Waypoint(name=w["name"], lat=float(w["lat"]), lon=float(w["lon"]))
        for w in data.get("waypoints", [])
    ]

    return RaceConfig(
        name=data.get("name", "Race"),
        timezone=data.get("timezone", "Asia/Vladivostok"),
        waypoints=waypoints,
        forecast=forecast,
    )
