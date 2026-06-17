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

from regatta_wind import correct
from regatta_wind.config import AreaConfig, ForecastConfig, load_config
from regatta_wind.models import FineField
from regatta_wind.sources import openmeteo as omsrc
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
def _load_owm_grid(bounds: tuple[float, float, float, float], n: int):
    """Current OWM obs on a grid across the area (real wind, cached 10 min)."""
    try:
        return owmsrc.sample_grid(bounds, n)
    except Exception:  # noqa: BLE001
        return []


@st.cache_data(ttl=600, show_spinner=False)
def _load_om_grid(bounds: tuple[float, float, float, float], n: int, unit: str):
    """Open-Meteo current wind on a grid (free second source, cached 10 min)."""
    try:
        return omsrc.sample_current_grid(bounds, n, unit=unit)
    except Exception:  # noqa: BLE001
        return []


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
        # Keep ALL computed hours; the visible window is trimmed to now->end later
        # via FineField.since(). (The WRF run already starts at the GFS cycle, which
        # is in the past, so truncating from file start here would drop the future.)
        field = _load_wrf(wrf_path, os.path.getmtime(wrf_path), cfg.timezone, unit, domain, 240)
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

    st.subheader("Реальные данные")
    correct_obs = st.checkbox("Корректировать поле по факту", value=True,
                              help="Сдвигать модель к реальным данным OWM + Open-Meteo на "
                                   "ближних часах (затухает к концу прогноза).")
    if owmsrc.available():
        st.caption("Источники факта: OpenWeatherMap + Open-Meteo (сетка по акватории).")
    else:
        st.caption("OWM-ключа нет — факт только по Open-Meteo.")

    st.caption("Вид (шкала, прозрачность, стрелки, час) — над картой. Прогноз WRF "
               "считается локально, см. wrf/README.md.")
    if st.button("↻ Сбросить кэш данных", width="stretch"):
        st.cache_data.clear()
        st.rerun()


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

corners = cfg.landmarks  # named LIMITS of the compute area, not OWM stations

# Bounding box of the compute area (forecast is not needed beyond it).
if corners:
    bounds = (min(w.lat for w in corners), max(w.lat for w in corners),
              min(w.lon for w in corners), max(w.lon for w in corners))
else:
    a = cfg.area
    bounds = (a.center_lat - a.half_span_deg, a.center_lat + a.half_span_deg,
              a.center_lon - a.half_span_deg, a.center_lon + a.half_span_deg)

# Real wind sampled on a grid across the area: OWM (orange dots) + Open-Meteo
# current (free second source). Both feed the bias correction; OWM is also drawn.
owm_points = _load_owm_grid(bounds, 3) if owmsrc.available() else []
om_points = _load_om_grid(bounds, 3, cfg.forecast.wind_speed_unit)
obs_points = list(owm_points) + list(om_points)

if correct_obs and obs_points:
    field = correct.bias_correct(field, obs_points)

c0, c1 = st.columns([3, 1])
c0.caption(f"Источник: **{field.source}** · сетка ~{field.grid_km:g} км · "
           f"{'1 км' if domain == 'd03' else '3 км'} · TZ {cfg.timezone}")
if used_wrf and wrf_path:
    c1.caption(f"📄 {os.path.basename(wrf_path)}")
if not field.trusted:
    st.warning("⚠️ Грубый фолбэк Open-Meteo (~5 км), без рельефа/бриза — пока не посчитан WRF. "
               "Нажми «Просчитать прогноз» в боковой панели.")
if correct_obs and obs_points:
    st.caption(f"✔ Поле скорректировано по факту: {len(obs_points)} точек "
               f"(OWM {len(owm_points)} + Open-Meteo {len(om_points)}), затухание к концу прогноза.")

tab_map, tab_charts = st.tabs(["🗺 Карта ветра", "📈 Графики по точкам"])
with tab_map:
    wind_map.render(field, corners, owm_points=owm_points, bounds=bounds)
    wind_map.render_video_export(field, corners)
with tab_charts:
    charts.render(field, corners, cfg)
