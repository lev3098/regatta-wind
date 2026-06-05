"""Streamlit UI — regatta wind forecast."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from regatta_wind.config import RaceConfig, load_config
from regatta_wind.models import Waypoint, WaypointForecast, WindSample
from regatta_wind.openmeteo import fetch_forecasts
from regatta_wind import tactics


st.set_page_config(page_title="Regatta Wind", page_icon="⛵", layout="wide")

# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⛵ Regatta Wind")
    route_path = st.text_input("Маршрут (YAML)", value="config/route.yaml")

    st.caption("**Сетка прогноза**")
    grid_n = st.slider("Плотность, N×N точек", min_value=4, max_value=14, value=8)
    grid_pad = st.select_slider(
        "Охват от центра рамки, °",
        options=[0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60],
        value=0.30,
        format_func=lambda v: f"{v}°  ≈ {v * 111:.0f} км",
    )

    st.caption("**Оверлей скорости**")
    hm_opacity = st.slider("Прозрачность", 0.0, 1.0, 0.45, 0.05)
    hm_radius  = st.slider("Сглаживание, px", 15, 120, 50, 5)
    hm_zmax    = st.slider("Макс. скорость на шкале, kn", 5, 50, 30, 5)

    if st.button("Обновить прогноз", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Источник: JMA MSM 5 км via Open-Meteo")


# ── цветовые бакеты для стрелок ───────────────────────────────────────────────
BUCKETS = [
    (0,  5,   "#4169E1", "0–5 kn"),
    (5,  10,  "#00CED1", "5–10 kn"),
    (10, 15,  "#3CB371", "10–15 kn"),
    (15, 20,  "#FFD700", "15–20 kn"),
    (20, 25,  "#FF8C00", "20–25 kn"),
    (25, 999, "#DC143C", "25+ kn"),
]


def _make_grid(lat_lo: float, lat_hi: float, lon_lo: float, lon_hi: float, n: int) -> list[Waypoint]:
    step_lat = (lat_hi - lat_lo) / max(n - 1, 1)
    step_lon = (lon_hi - lon_lo) / max(n - 1, 1)
    return [
        Waypoint(name=f"G{i},{j}",
                 lat=round(lat_lo + i * step_lat, 4),
                 lon=round(lon_lo + j * step_lon, 4))
        for i in range(n) for j in range(n)
    ]


@st.cache_data(ttl=3600, show_spinner="Загружаю маршрут…")
def _load_route(path: str):
    cfg = load_config(path)
    return cfg, fetch_forecasts(cfg.waypoints, cfg.forecast, cfg.timezone)


@st.cache_data(ttl=3600, show_spinner="Загружаю сетку прогноза…")
def _load_grid(path: str, lat_lo: float, lat_hi: float, lon_lo: float, lon_hi: float, n: int):
    cfg = load_config(path)
    return fetch_forecasts(_make_grid(lat_lo, lat_hi, lon_lo, lon_hi, n), cfg.forecast, cfg.timezone)


try:
    cfg, route_forecasts = _load_route(route_path)
except Exception as exc:
    st.error(f"Ошибка загрузки маршрута: {exc}")
    st.stop()

center_lat = sum(w.lat for w in cfg.waypoints) / len(cfg.waypoints)
center_lon = sum(w.lon for w in cfg.waypoints) / len(cfg.waypoints)

# инициализация позиции рамки от центра маршрута (только первый раз)
if "box_lat" not in st.session_state:
    st.session_state.box_lat = center_lat
if "box_lon" not in st.session_state:
    st.session_state.box_lon = center_lon

st.title(cfg.name)
st.caption(
    f"Модель: **{cfg.forecast.model}** · "
    f"Единицы: **{cfg.forecast.wind_speed_unit}** · "
    f"TZ: **{cfg.timezone}**"
)

tab_map, tab_charts = st.tabs(["🗺 Карта", "📈 Графики"])


# ── MAP TAB ───────────────────────────────────────────────────────────────────
with tab_map:
    # ── позиция рамки ─────────────────────────────────────────────────────────
    ca, cb, cc = st.columns([2, 2, 1])
    with ca:
        new_lat = st.number_input(
            "Широта центра рамки", value=float(round(st.session_state.box_lat, 2)),
            min_value=40.0, max_value=50.0, step=0.05, format="%.2f",
        )
    with cb:
        new_lon = st.number_input(
            "Долгота центра рамки", value=float(round(st.session_state.box_lon, 2)),
            min_value=125.0, max_value=140.0, step=0.05, format="%.2f",
        )
    with cc:
        st.write("")
        st.write("")
        if st.button("↺ К маршруту"):
            st.session_state.box_lat = center_lat
            st.session_state.box_lon = center_lon
            st.rerun()

    st.session_state.box_lat = new_lat
    st.session_state.box_lon = new_lon
    box_lat = new_lat
    box_lon = new_lon

    lat_lo = round(box_lat - grid_pad, 2)
    lat_hi = round(box_lat + grid_pad, 2)
    lon_lo = round(box_lon - grid_pad, 2)
    lon_hi = round(box_lon + grid_pad, 2)

    try:
        grid_forecasts = _load_grid(route_path, lat_lo, lat_hi, lon_lo, lon_hi, grid_n)
    except Exception as exc:
        st.error(f"Ошибка загрузки сетки: {exc}")
        st.stop()

    samples0 = grid_forecasts[0].samples if grid_forecasts else []
    if not samples0:
        st.warning("Нет данных сетки.")
        st.stop()

    times = [s.time for s in samples0]
    tz = times[0].tzinfo
    now = datetime.now(tz)
    default_idx = min(range(len(times)), key=lambda i: abs((times[i] - now).total_seconds()))

    hour_idx = st.select_slider(
        "⏱ Время прогноза",
        options=list(range(len(times))),
        value=default_idx,
        format_func=lambda i: times[i].strftime("%d %b  %H:%M"),
    )

    sel_time = times[hour_idx]
    delta_h = (sel_time - now).total_seconds() / 3600
    st.caption(
        f"**{sel_time.strftime('%d %b %H:%M')}** · "
        + (f"{abs(delta_h):.0f} ч назад" if delta_h < 0 else f"через {delta_h:.0f} ч")
    )

    # ── данные для текущего часа ──────────────────────────────────────────────
    # масштаб стрелок: 40% шага сетки при hm_zmax скорости
    grid_step = 2 * grid_pad / max(grid_n - 1, 1)
    SCALE = grid_step * 0.40 / max(hm_zmax, 1)

    hm_lats, hm_lons, hm_speeds = [], [], []
    segs: list[tuple[list, list]] = [([], []) for _ in BUCKETS]

    for fc in grid_forecasts:
        if hour_idx >= len(fc.samples):
            continue
        s = fc.samples[hour_idx]
        spd, dir_deg = s.speed_kn, s.direction_deg
        lat, lon = fc.waypoint.lat, fc.waypoint.lon

        hm_lats.append(lat); hm_lons.append(lon); hm_speeds.append(spd)

        b_idx = len(BUCKETS) - 1
        for i, (lo, hi, *_) in enumerate(BUCKETS):
            if spd < hi:
                b_idx = i
                break

        bearing = math.radians((dir_deg + 180) % 360)
        length  = spd * SCALE
        dx, dy  = math.sin(bearing) * length, math.cos(bearing) * length
        h_lon, h_lat = lon + dx, lat + dy

        blats, blons = segs[b_idx]
        blats += [lat, h_lat, None]
        blons += [lon, h_lon, None]
        if spd > 1.5:
            back = math.radians(dir_deg % 360)
            hl = length * 0.45
            for side in (-35, 35):
                a = back + math.radians(side)
                blats += [h_lat, h_lat + math.cos(a) * hl, None]
                blons += [h_lon, h_lon + math.sin(a) * hl, None]

    # ── строим карту ──────────────────────────────────────────────────────────
    fig_map = go.Figure()

    # 1. тепловой оверлей
    fig_map.add_trace(go.Densitymapbox(
        lat=hm_lats, lon=hm_lons, z=hm_speeds,
        radius=hm_radius, opacity=hm_opacity,
        colorscale="RdYlBu_r", zmin=0, zmax=hm_zmax,
        showscale=True,
        colorbar=dict(
            title=dict(text="kn", font=dict(color="white")),
            thickness=14, len=0.6, x=1.01,
            tickfont=dict(color="white"),
        ),
        hoverinfo="skip",
    ))

    # 2. стрелки
    for i, (lo, hi, color, label) in enumerate(BUCKETS):
        blats, blons = segs[i]
        if not blats:
            continue
        fig_map.add_trace(go.Scattermapbox(
            lat=blats, lon=blons, mode="lines",
            line=dict(color=color, width=2),
            name=label,
        ))

    # 3. знаки дистанции
    fig_map.add_trace(go.Scattermapbox(
        lat=[w.lat for w in cfg.waypoints],
        lon=[w.lon for w in cfg.waypoints],
        mode="markers+text",
        marker=dict(size=10, color="white"),
        text=[w.name for w in cfg.waypoints],
        textfont=dict(color="white", size=12),
        textposition="top right",
        name="Знаки",
    ))

    # 4. рамка прогнозируемой площади (пунктир — чередующиеся сегменты)
    def _dashed_rect(lat_lo, lat_hi, lon_lo, lon_hi, steps=16):
        """Dashed rectangle as alternating drawn/gap segments with None separators."""
        lats, lons = [], []
        edges = [
            ([lat_lo] * (steps + 1), [lon_lo + (lon_hi - lon_lo) * i / steps for i in range(steps + 1)]),
            ([lat_lo + (lat_hi - lat_lo) * i / steps for i in range(steps + 1)], [lon_hi] * (steps + 1)),
            ([lat_hi] * (steps + 1), [lon_hi - (lon_hi - lon_lo) * i / steps for i in range(steps + 1)]),
            ([lat_hi - (lat_hi - lat_lo) * i / steps for i in range(steps + 1)], [lon_lo] * (steps + 1)),
        ]
        for edge_lats, edge_lons in edges:
            for i in range(steps):
                if i % 2 == 0:
                    lats += [edge_lats[i], edge_lats[i + 1], None]
                    lons += [edge_lons[i], edge_lons[i + 1], None]
        return lats, lons

    box_lats, box_lons = _dashed_rect(lat_lo, lat_hi, lon_lo, lon_hi)
    fig_map.add_trace(go.Scattermapbox(
        lat=box_lats, lon=box_lons,
        mode="lines",
        line=dict(color="rgba(255,255,255,0.65)", width=2),
        showlegend=False, hoverinfo="skip",
    ))

    # 5. маркер центра рамки
    fig_map.add_trace(go.Scattermapbox(
        lat=[box_lat], lon=[box_lon],
        mode="markers",
        marker=dict(size=10, color="rgba(255,255,255,0.8)"),
        showlegend=False,
        hovertemplate=f"Центр: {box_lat:.2f}°N, {box_lon:.2f}°E<extra></extra>",
    ))

    fig_map.update_layout(
        mapbox=dict(
            style="carto-darkmatter",
            center=dict(lat=box_lat, lon=box_lon),
            zoom=10,
        ),
        margin=dict(t=0, b=0, l=0, r=0),
        height=650,
        legend=dict(
            bgcolor="rgba(0,0,0,0.6)", font=dict(color="white", size=11),
            x=0.01, y=0.99, xanchor="left", yanchor="top",
        ),
    )

    st.plotly_chart(fig_map, use_container_width=True, key="wind_map")


# ── CHARTS TAB ────────────────────────────────────────────────────────────────
def _unwrap_dirs(samples: list[WindSample]) -> list[float]:
    if not samples:
        return []
    out = [samples[0].direction_deg]
    for s in samples[1:]:
        delta = (s.direction_deg - out[-1] + 180) % 360 - 180
        out.append(out[-1] + delta)
    return out


def _render_charts(fc: WaypointForecast, cfg: RaceConfig) -> None:
    samples = fc.samples
    if not samples:
        st.warning("Нет данных.")
        return

    tz = samples[0].time.tzinfo
    now = datetime.now(tz)
    win_start = now + timedelta(hours=cfg.forecast.tactical_window[0])
    win_end   = now + timedelta(hours=cfg.forecast.tactical_window[1])

    dist = tactics.haversine_km(fc.waypoint.lat, fc.waypoint.lon, fc.grid_lat, fc.grid_lon)
    st.caption(f"({fc.waypoint.lat:.3f}, {fc.waypoint.lon:.3f}) · узел сетки: **{dist:.1f} км**")

    times_s = [s.time for s in samples]
    speeds   = [s.speed_kn for s in samples]
    gusts    = [s.gust_kn if not math.isnan(s.gust_kn) else None for s in samples]
    dirs_uw  = _unwrap_dirs(samples)

    d_min, d_max = min(dirs_uw), max(dirs_uw)
    tick_lo = math.floor(d_min / 45) * 45
    tick_hi = math.ceil(d_max / 45) * 45
    compass8 = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
    tickvals = list(range(tick_lo, tick_hi + 1, 45))
    ticktext = [f"{compass8[v % 360 // 45]} ({v % 360}°)" for v in tickvals]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=("Скорость, kn", "Направление"),
                        vertical_spacing=0.14)

    fig.add_trace(go.Scatter(x=times_s, y=speeds, name="Ветер", mode="lines+markers",
        line=dict(color="#4C9BE8", width=2), marker=dict(size=4),
        hovertemplate="%{x|%H:%M}  %{y:.1f} kn<extra>Ветер</extra>"), row=1, col=1)

    if any(g is not None for g in gusts):
        fig.add_trace(go.Scatter(x=times_s, y=gusts, name="Порывы", mode="lines",
            line=dict(color="#E8844C", width=1.5, dash="dot"),
            hovertemplate="%{x|%H:%M}  %{y:.1f} kn<extra>Порывы</extra>"), row=1, col=1)

    fig.add_trace(go.Scatter(x=times_s, y=dirs_uw, name="Направление", mode="lines+markers",
        line=dict(color="#6ECC6E", width=2), marker=dict(size=4),
        hovertemplate="%{x|%H:%M}  %{customdata}°<extra>Направление</extra>",
        customdata=[s.direction_deg for s in samples]), row=2, col=1)

    sx, sy, st_ = [], [], []
    for i, (s, ud) in enumerate(zip(samples, dirs_uw)):
        if i == 0 or s.time < now:
            continue
        delta = tactics.shift(samples[i - 1].direction_deg, s.direction_deg)
        if abs(delta) >= 15:
            sx.append(s.time); sy.append(ud)
            st_.append("→" if delta > 0 else "←")
    if sx:
        fig.add_trace(go.Scatter(x=sx, y=sy, mode="text", text=st_,
            textfont=dict(size=18, color="yellow"),
            name="Заход", hoverinfo="skip"), row=2, col=1)

    fig.add_vline(x=now.isoformat(), line=dict(color="rgba(255,255,255,0.5)", width=1.5, dash="dash"))
    fig.add_vrect(x0=win_start.isoformat(), x1=win_end.isoformat(),
        fillcolor="rgba(255,220,0,0.07)",
        line=dict(color="rgba(255,220,0,0.35)", width=1),
        annotation_text="тактич. окно",
        annotation_font=dict(color="rgba(255,220,0,0.8)", size=11),
        annotation_position="top left")

    fig.update_yaxes(title_text="kn", showgrid=True, gridcolor="#2A2A2A", zeroline=False, row=1, col=1)
    fig.update_yaxes(tickvals=tickvals, ticktext=ticktext,
                     showgrid=True, gridcolor="#2A2A2A", zeroline=False, row=2, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#2A2A2A", tickformat="%H:%M\n%d %b")
    fig.update_layout(
        height=540, margin=dict(t=60, b=10, l=10, r=10),
        legend=dict(orientation="h", y=1.06, x=1, xanchor="right"),
        plot_bgcolor="#0E1117", paper_bgcolor="#0E1117",
        font=dict(color="#FAFAFA", size=12), hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    future = [s for s in samples if s.time >= now]
    if future:
        amp  = tactics.oscillation_range(future)
        peak = max(future, key=lambda s: s.speed_kn)
        c1, c2, c3 = st.columns(3)
        c1.metric("Пик ветра", f"{peak.speed_kn:.1f} kn", peak.time.strftime("%H:%M"))
        c2.metric("Амплитуда", f"{amp:.0f}°")
        c3.metric("Характер", "осциллирующий" if amp > 30 else "устойчивый")


with tab_charts:
    chart_tabs = st.tabs([fc.waypoint.name for fc in route_forecasts])
    for ct, fc in zip(chart_tabs, route_forecasts):
        with ct:
            _render_charts(fc, cfg)
