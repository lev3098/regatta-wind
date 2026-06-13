"""Streamlit UI — regatta wind, local-WRF first with an Open-Meteo fallback.

* **WRF** — newest ``wrfout_<domain>_*`` from the local batch (see ``wrf/README.md``).
  Compute it right here with the "Просчитать прогноз" button.
* **Open-Meteo fallback** — coarse online field shown until WRF has run.
"""

from __future__ import annotations

import os

import streamlit as st

from regatta_wind.config import AreaConfig, ForecastConfig, load_config
from regatta_wind.models import FineField
from regatta_wind.sources import wrf as wrfsrc
from regatta_wind.sources.openmeteo import fetch_fallback_field
from regatta_wind.ui import charts, compute, route_editor, state, wind_map

st.set_page_config(page_title="Regatta Wind · WRF", page_icon="⛵", layout="wide")


@st.cache_data(show_spinner="Чтение WRF…")
def _load_wrf(path: str, mtime: float, tz: str, unit: str, domain: str, hours: int) -> FineField:
    return wrfsrc.read_wrfout(path, timezone_name=tz, wind_speed_unit=unit,
                              domain=domain, hours_ahead=hours)


@st.cache_data(ttl=3600, show_spinner="Загрузка фолбэка Open-Meteo…")
def _load_fallback(clat: float, clon: float, span: float, model: str,
                   unit: str, hours: int, tz: str) -> FineField:
    area = AreaConfig(center_lat=clat, center_lon=clon, half_span_deg=span)
    fc = ForecastConfig(source="open-meteo", wind_speed_unit=unit,
                        hours_ahead=hours, fallback_model=model)
    return fetch_fallback_field(area, fc, tz, grid_n=17)


def _resolve_field(cfg, source_choice: str, out_dir: str, domain: str):
    hours, unit = cfg.forecast.hours_ahead, cfg.forecast.wind_speed_unit
    area = state.get_area()

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
    state.ensure_state(cfg)
    area = state.get_area()

    st.subheader("Прогноз")
    source_choice = st.radio("Источник", ["Авто", "WRF", "Open-Meteo (фолбэк)"],
                             label_visibility="collapsed",
                             help="Авто: WRF, если есть свежий wrfout, иначе Open-Meteo.")
    out_dir = st.text_input("Папка вывода WRF", value=cfg.wrf.output_dir)
    domain = st.selectbox("Домен", ["d02", "d03"],
                         index=0 if cfg.wrf.domain == "d02" else 1,
                         help="d02 = 3 км, d03 = 1 км")
    compute.render(area.center_lat, area.center_lon, cfg.forecast.hours_ahead,
                   2 if domain == "d02" else 3)

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

c0, c1 = st.columns([3, 1])
c0.caption(f"Источник: **{field.source}** · сетка ~{field.grid_km:g} км · "
           f"горизонт {cfg.forecast.hours_ahead} ч · TZ {cfg.timezone}")
if used_wrf and wrf_path:
    c1.caption(f"📄 {os.path.basename(wrf_path)}")
if not field.trusted:
    st.warning("⚠️ Грубый фолбэк Open-Meteo (~5 км), без рельефа/бриза — пока не посчитан WRF. "
               "Нажми «Просчитать прогноз» в боковой панели.")

tab_map, tab_area, tab_charts = st.tabs(["🗺 Карта ветра", "🧭 Область и знаки", "📈 Графики"])
with tab_map:
    wind_map.render(field, state.get_waypoints(), vmax=hm_zmax, alpha=hm_alpha, arrows=arrows)
with tab_area:
    route_editor.render(route_path)
with tab_charts:
    charts.render(field, state.get_waypoints(), cfg)
