"""CLI: таблица ветра по точкам дистанции."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from .config import RaceConfig, load_config
from .models import WaypointForecast
from .openmeteo import fetch_forecasts
from . import tactics

console = Console()


def _build_table(fc: WaypointForecast, cfg: RaceConfig) -> Table:
    dist = tactics.haversine_km(
        fc.waypoint.lat, fc.waypoint.lon, fc.grid_lat, fc.grid_lon
    )
    title = (
        f"[bold]{fc.waypoint.name}[/]  "
        f"[dim]({fc.waypoint.lat:.3f}, {fc.waypoint.lon:.3f}  "
        f"→ узел сетки {dist:.1f} км)[/]"
    )

    table = Table(title=title, title_justify="left", header_style="bold cyan")
    table.add_column("Время", no_wrap=True)
    table.add_column("Напр.", justify="right")
    table.add_column("Румб", justify="center")
    table.add_column("Ветер, kn", justify="right")
    table.add_column("Поры́в, kn", justify="right")
    table.add_column("Заход", justify="left")

    tz = fc.samples[0].time.tzinfo if fc.samples else None
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    win_lo, win_hi = cfg.forecast.tactical_window
    win_start = now + timedelta(hours=win_lo)
    win_end = now + timedelta(hours=win_hi)

    prev_dir: float | None = None
    for s in fc.samples:
        delta = tactics.shift(prev_dir, s.direction_deg) if prev_dir is not None else 0.0
        prev_dir = s.direction_deg

        arrow = tactics.shift_arrow(delta) if delta else ""
        row_style = None
        if s.time < now:
            row_style = "dim"                   # модельное прошлое
        elif win_start <= s.time <= win_end:
            row_style = "bold yellow"           # тактическое окно 3–5 ч

        table.add_row(
            s.time.strftime("%H:%M"),
            f"{s.direction_deg:.0f}°",
            tactics.compass(s.direction_deg),
            f"{s.speed_kn:.1f}",
            f"{s.gust_kn:.1f}" if s.gust_kn == s.gust_kn else "—",
            arrow,
            style=row_style,
        )

    amp = tactics.oscillation_range([s for s in fc.samples if s.time >= now])
    table.caption = f"[dim]Амплитуда колебаний направления (будущее): {amp:.0f}°[/]"
    table.caption_justify = "left"
    return table


def run(route_path: str) -> None:
    cfg = load_config(route_path)
    console.print(f"\n[bold green]{cfg.name}[/]")
    console.print(
        f"[dim]Модель {cfg.forecast.model} · единицы {cfg.forecast.wind_speed_unit} · "
        f"TZ {cfg.timezone}[/]\n"
    )

    forecasts = fetch_forecasts(cfg.waypoints, cfg.forecast, cfg.timezone)
    for fc in forecasts:
        console.print(_build_table(fc, cfg))
        console.print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Оперативный прогноз ветра (JMA MSM) по точкам дистанции."
    )
    parser.add_argument(
        "--route",
        default="config/route.yaml",
        help="Путь к YAML-маршруту (по умолчанию config/route.yaml).",
    )
    args = parser.parse_args()
    run(args.route)


if __name__ == "__main__":
    main()
