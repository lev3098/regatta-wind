"""Streamlit UI — regatta wind over Peter the Great Bay.

* **WRF** — newest ``wrfout_<domain>_*`` from the local batch (compute it from the
  sidebar: pick 1 km / 3 km and a horizon, hit the button — runs in the background).
* **Open-Meteo fallback** — coarse online field shown until WRF has run.
* **OpenWeatherMap** — real-time obs + 3-h point forecast at the fixed landmarks.

The forecast area is fixed to the bay (Amur / Ussuri / Slavyansky bays and the
islands); there is no rectangle to draw and no editable race marks.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from regatta_wind.config import AreaConfig, ForecastConfig, load_config
from regatta_wind.models import FineField, WindSample
from regatta_wind.sources import owm as owmsrc
from regatta_wind.sources import wrf as wrfsrc
from regatta_wind.sources.openmeteo import fetch_fallback_field
from regatta_wind.ui import charts, compute, wind_map

st.set_page_config(page_title="Regatta Wind · WRF", page_icon="⛵", layout="wide")


@st.cache_data(show_spinner="Чтение WRF…")
def _load_wrf(path: str, mtime: float, tz: str, unit: str, domain: str, hours: int) -> FineField:
    return wrfsrc.read_wrfout(path, timezone_name=tz, wind_speed_unit=unit,
                              domain=domain, hours_ahead=hours)


@st.cache_data(ttl=600, show_spinner=False)
def _load_owm_current(lats: tuple[float, ...], lons: tuple[float, ...]) -> list[WindSample | None]:
    out: list[WindSample | None] = []
    for lat, lon in zip(lats, lons):
        try:
            out.append(owmsrc.fetch_current(lat, lon))
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def _load_owm_forecast(
    lats: tuple[float, ...], lons: tuple[float, ...], hours: int
) -> list[list[WindSample]]:
    out: list[list[WindSample]] = []
    for lat, lon in zip(lats, lons):
        try:
            out.append(owmsrc.fetch_forecast(lat, lon, hours))
        except Exception:  # noqa: BLE001
            out.append([])
    return out


@st.cache_data(ttl=3600, show_spinner="Загрузка фолбэка Open-Meteo…")
def _load_fallback(clat: float, clon: float, span: float, model: str,
                   unit: str, hours: int, tz: str) -> FineField:
    area = AreaConfig(center_lat=clat, center_lon=clon, half_span_deg=span)
    fc = ForecastConfig(source="open-meteo", wind_speed_unit=unit,
                        hours_ahead=hours, fallback_model=model)
    return fetch_fallback_field(area, fc, tz, grid_n=17)


def _resolve_field(cfg, source_choice: str, out_dir: str, domain: str):
    hours, unit = cfg.forecast.hours_ahead, cfg.forecast.wind_speed_unit
    area = cfg.area

    def fallback() -> FineField:
        return _load_fallback(area.center_lat, area.center_lon, area.half_span_deg,
                              cfg.forecast.fallback_model, unit, hours, cfg.timezone)

    if source_choice == "Open-Meteo (фолбэк)":
        return fallback(), False, None
    wrf_path = wrfsrc.find_wrfout(out_dir, domain)
    if wrf_path:
        field = _load_wrf(wrf_path, os.path.getmtime(wrf_path), cfg.timezone, unit, domain, hours)
        return field, True, wrf_path
    if source_choice == "WRF":
        return None, False, None
    return fallback(), False, None


# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⛵ Regatta Wind")
    route_path = st.text_input("Маршрут (YAML)", value="config/route.yaml")
    try:
        cfg = load_config(route_path)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Ошибка конфигурации: {exc}")
        st.stop()

    st.subheader("Прогноз")
    source_choice = st.radio("Источник", ["Авто", "WRF", "Open-Meteo (фолбэк)"],
                             label_visibility="collapsed",
                             help="Авто: WRF, если есть свежий wrfout, иначе Open-Meteo.")
    out_dir = st.text_input("Папка вывода WRF", value=cfg.wrf.output_dir)
    domain = compute.render(cfg.area.center_lat, cfg.area.center_lon)

    st.subheader("OpenWeatherMap")
    if owmsrc.available():
        st.success("OWM подключён · данные на ориентирах", icon="🌐")
    else:
        st.caption("Нет ключа OWM — реальные данные на точках отключены.")

    st.subheader("Вид")
    hm_zmax = st.slider("Макс. шкала, узлы", 5, 50, 30, 5)
    hm_alpha = st.slider("Непрозрачность поля", 0.2, 0.9, 0.6, 0.05)
    arrows = st.slider("Плотность стрелок (0 = без)", 0, 50, 28, 2,
                       help="Сколько стрелок по стороне сетки")
    if st.button("↻ Обновить", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Прогноз WRF считается локально — см. wrf/README.md")


# ── load + header ───────────────────────────────────────────────────────────
try:
    field, used_wrf, wrf_path = _resolve_field(cfg, source_choice, out_dir, domain)
except Exception as exc:  # noqa: BLE001
    st.error(f"Не удалось загрузить прогноз: {exc}")
    st.stop()

st.title(cfg.name)
if field is None:
    st.warning(f"Нет `wrfout_{domain}_*` в `{out_dir}`. Нажми «Просчитать прогноз» в боковой "
               "панели или переключи источник на «Open-Meteo (фолбэк)».")
    st.stop()

# Drop hours already in the past — show now → end only.
field = field.since(datetime.now(ZoneInfo(cfg.timezone)))

c0, c1 = st.columns([3, 1])
c0.caption(f"Источник: **{field.source}** · сетка ~{field.grid_km:g} км · "
           f"{'1 км' if domain == 'd03' else '3 км'} · TZ {cfg.timezone}")
if used_wrf and wrf_path:
    c1.caption(f"📄 {os.path.basename(wrf_path)}")
if not field.trusted:
    st.warning("⚠️ Грубый фолбэк Open-Meteo (~5 км), без рельефа/бриза — пока не посчитан WRF. "
               "Нажми «Просчитать прогноз» в боковой панели.")

landmarks = cfg.landmarks

# OWM point observations at the fixed landmarks (cached; key shipped by default).
owm_current: list[WindSample | None] | None = None
owm_forecast: list[list[WindSample]] | None = None
if owmsrc.available() and landmarks:
    lats = tuple(w.lat for w in landmarks)
    lons = tuple(w.lon for w in landmarks)
    span_h = ((field.times[-1] - field.times[0]).total_seconds() / 3600
              if len(field.times) > 1 else cfg.forecast.hours_ahead)
    owm_current = _load_owm_current(lats, lons)
    owm_forecast = _load_owm_forecast(lats, lons, int(span_h) + 3)

tab_map, tab_charts = st.tabs(["🗺 Карта ветра", "📈 Графики по точкам"])
with tab_map:
    wind_map.render(field, landmarks, vmax=hm_zmax, alpha=hm_alpha, arrows=arrows,
                    owm_obs=owm_current)
with tab_charts:
    charts.render(field, landmarks, cfg,
                  owm_current=owm_current, owm_forecast=owm_forecast)
