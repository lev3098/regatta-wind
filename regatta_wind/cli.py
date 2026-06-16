"""CLI: wind table per race mark, from local WRF or the Open-Meteo fallback."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from . import tactics
from .config import RaceConfig, load_config
from .models import FineField, Waypoint
from .sources import wrf as wrfsrc
from .sources.openmeteo import fetch_fallback_field

console = Console()


def _resolve_field(cfg: RaceConfig, source: str) -> FineField:
    """auto → WRF if a wrfout exists, else Open-Meteo fallback."""
    hours = cfg.forecast.hours_ahead
    unit = cfg.forecast.wind_speed_unit

    if source in ("auto", "wrf"):
        path = wrfsrc.find_wrfout(cfg.wrf.output_dir, cfg.wrf.domain)
        if path:
            console.print(f"[dim]WRF: {os.path.basename(path)}[/]")
            return wrfsrc.read_wrfout(path, timezone_name=cfg.timezone,
                                      wind_speed_unit=unit, domain=cfg.wrf.domain,
                                      hours_ahead=hours)
        if source == "wrf":
            raise SystemExit(
                f"Нет wrfout_{cfg.wrf.domain}_* в {cfg.wrf.output_dir}. "
                "Запусти батч WRF (wrf/README.md) или используй --source open-meteo."
            )
    return fetch_fallback_field(cfg.area, cfg.forecast, cfg.timezone)


def _build_table(field: FineField, w: Waypoint, idx: int, cfg: RaceConfig) -> Table:
    samples = field.sample_series(w.lat, w.lon)
    i, j = field.nearest_index(w.lat, w.lon)
    dist = tactics.haversine_km(w.lat, w.lon, float(field.terrain.lat[i, j]),
                                float(field.terrain.lon[i, j]))
    title = (f"[bold]{idx + 1}. {w.name}[/]  "
             f"[dim]({w.lat:.3f}, {w.lon:.3f} → узел {field.grid_km:g} км: {dist:.2f} км)[/]")

    table = Table(title=title, title_justify="left", header_style="bold cyan")
    for col, just in (("Время", "left"), ("Напр.", "right"), ("Румб", "center"),
                      ("Ветер, kn", "right"), ("Поры́в, kn", "right"), ("Заход", "left")):
        table.add_column(col, justify=just, no_wrap=(col == "Время"))

    tz = samples[0].time.tzinfo if samples else None
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    win_start = now + timedelta(hours=cfg.forecast.tactical_window[0])
    win_end = now + timedelta(hours=cfg.forecast.tactical_window[1])

    prev: float | None = None
    for s in samples:
        delta = tactics.shift(prev, s.direction_deg) if prev is not None else 0.0
        prev = s.direction_deg
        style = "dim" if s.time < now else ("bold yellow" if win_start <= s.time <= win_end else None)
        table.add_row(
            s.time.strftime("%H:%M"), f"{s.direction_deg:.0f}°", tactics.compass(s.direction_deg),
            f"{s.speed_kn:.1f}", f"{s.gust_kn:.1f}" if s.gust_kn == s.gust_kn else "—",
            tactics.shift_arrow(delta) if delta else "", style=style,
        )

    amp = tactics.oscillation_range([s for s in samples if s.time >= now])
    table.caption = f"[dim]Амплитуда направления (будущее): {amp:.0f}°[/]"
    table.caption_justify = "left"
    return table


def run(route_path: str, source: str) -> None:
    cfg = load_config(route_path)
    field = _resolve_field(cfg, source)
    console.print(f"\n[bold green]{cfg.name}[/]")
    trust = "" if field.trusted else "  [yellow](грубый фолбэк — опирайся на WRF)[/]"
    console.print(f"[dim]{field.source} · сетка {field.grid_km:g} км · TZ {cfg.timezone}[/]{trust}\n")

    if not cfg.landmarks:
        console.print("[yellow]Нет контрольных точек. Задай блок `landmarks:` в YAML.[/]")
        return
    for idx, w in enumerate(cfg.landmarks):
        console.print(_build_table(field, w, idx, cfg))
        console.print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Прогноз ветра по точкам дистанции (локальный WRF или Open-Meteo)."
    )
    parser.add_argument("--route", default="config/route.yaml", help="Путь к YAML-маршруту.")
    parser.add_argument("--source", default="auto", choices=["auto", "wrf", "open-meteo"],
                        help="Источник: auto (по умолчанию), wrf, open-meteo.")
    args = parser.parse_args()
    run(args.route, args.source)


if __name__ == "__main__":
    main()
