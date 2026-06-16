"""Per-mark hourly charts: speed (+gusts), direction with shifts, tactical window.

Optionally overlays OpenWeatherMap observations (current + 3-h forecast) when an
API key is present, giving a real-time sanity-check next to the model field.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from .. import tactics
from ..config import RaceConfig
from ..models import FineField, Waypoint, WindSample

_COMPASS8 = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]


def _render_one(
    field: FineField,
    w: Waypoint,
    cfg: RaceConfig,
    *,
    owm_current: WindSample | None = None,
    owm_forecast: list[WindSample] | None = None,
) -> None:
    samples = field.sample_series(w.lat, w.lon)
    if not samples:
        st.warning("Нет данных по этому знаку.")
        return

    i, j = field.nearest_index(w.lat, w.lon)
    dist = tactics.haversine_km(w.lat, w.lon, float(field.terrain.lat[i, j]),
                                float(field.terrain.lon[i, j]))
    st.caption(f"({w.lat:.3f}, {w.lon:.3f}) · ближайший узел {field.grid_km:g} км: **{dist:.2f} км**")

    tz = samples[0].time.tzinfo
    now = datetime.now(tz)
    win_start = now + timedelta(hours=cfg.forecast.tactical_window[0])
    win_end = now + timedelta(hours=cfg.forecast.tactical_window[1])

    times = [s.time for s in samples]
    speeds = [s.speed_kn for s in samples]
    gusts = [s.gust_kn if not math.isnan(s.gust_kn) else None for s in samples]
    dirs_uw = tactics.unwrap_directions([s.direction_deg for s in samples])

    tick_lo = math.floor(min(dirs_uw) / 45) * 45
    tick_hi = math.ceil(max(dirs_uw) / 45) * 45
    tickvals = list(range(tick_lo, tick_hi + 1, 45))
    ticktext = [f"{_COMPASS8[v % 360 // 45]} ({v % 360}°)" for v in tickvals]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        subplot_titles=("Скорость, kn", "Направление"), vertical_spacing=0.14)

    fig.add_trace(go.Scatter(x=times, y=speeds, name="Ветер", mode="lines+markers",
        line=dict(color="#4C9BE8", width=2), marker=dict(size=4),
        hovertemplate="%{x|%H:%M}  %{y:.1f} kn<extra>Ветер</extra>"), row=1, col=1)
    if any(g is not None for g in gusts):
        fig.add_trace(go.Scatter(x=times, y=gusts, name="Порывы", mode="lines",
            line=dict(color="#E8844C", width=1.5, dash="dot"),
            hovertemplate="%{x|%H:%M}  %{y:.1f} kn<extra>Порывы</extra>"), row=1, col=1)

    fig.add_trace(go.Scatter(x=times, y=dirs_uw, name="Направление", mode="lines+markers",
        line=dict(color="#6ECC6E", width=2), marker=dict(size=4),
        customdata=[s.direction_deg for s in samples],
        hovertemplate="%{x|%H:%M}  %{customdata:.0f}°<extra>Направление</extra>"), row=2, col=1)

    sx, sy, stxt = [], [], []
    for k in range(1, len(samples)):
        if samples[k].time < now:
            continue
        delta = tactics.shift(samples[k - 1].direction_deg, samples[k].direction_deg)
        if abs(delta) >= 15:
            sx.append(samples[k].time); sy.append(dirs_uw[k])
            stxt.append("→" if delta > 0 else "←")
    if sx:
        fig.add_trace(go.Scatter(x=sx, y=sy, mode="text", text=stxt,
            textfont=dict(size=18, color="yellow"), name="Заход", hoverinfo="skip"), row=2, col=1)

    # OWM 3-h forecast overlay
    if owm_forecast:
        tz = samples[0].time.tzinfo
        owm_times = [s.time.astimezone(tz) for s in owm_forecast]
        owm_speeds = [s.speed_kn for s in owm_forecast]
        owm_dirs = [s.direction_deg for s in owm_forecast]
        owm_gusts = [s.gust_kn for s in owm_forecast]
        fig.add_trace(go.Scatter(x=owm_times, y=owm_speeds, name="OWM прогноз",
            mode="lines+markers", line=dict(color="#FF9800", width=1.5, dash="dot"),
            marker=dict(size=7, color="#FF9800"),
            hovertemplate="%{x|%H:%M}  %{y:.1f} kn<extra>OWM</extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=owm_times, y=owm_gusts, name="OWM порывы",
            mode="markers", marker=dict(size=5, color="#FF9800", symbol="triangle-up"),
            hovertemplate="%{x|%H:%M}  %{y:.1f} kn<extra>OWM порыв</extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=owm_times, y=owm_dirs, name="OWM направление",
            mode="lines+markers", line=dict(color="#FF9800", width=1.5, dash="dot"),
            marker=dict(size=7, color="#FF9800"),
            hovertemplate="%{x|%H:%M}  %{customdata:.0f}°<extra>OWM направление</extra>",
            customdata=owm_dirs), row=2, col=1)

    # OWM current obs — single dot at observation time
    if owm_current:
        tz = samples[0].time.tzinfo
        obs_t = owm_current.time.astimezone(tz)
        spd_lbl = f"{obs_t.strftime('%H:%M')}  {owm_current.speed_kn:.1f} kn"
        dir_lbl = f"{obs_t.strftime('%H:%M')}  {owm_current.direction_deg:.0f}°"
        fig.add_trace(go.Scatter(x=[obs_t], y=[owm_current.speed_kn], name="OWM сейчас",
            mode="markers", marker=dict(size=14, color="#FF9800", symbol="star",
                                        line=dict(color="white", width=1)),
            hovertemplate=spd_lbl + "<extra>OWM</extra>"),
            row=1, col=1)
        fig.add_trace(go.Scatter(x=[obs_t], y=[owm_current.direction_deg],
            name="OWM сейчас (напр)", mode="markers",
            marker=dict(size=14, color="#FF9800", symbol="star",
                        line=dict(color="white", width=1)),
            hovertemplate=dir_lbl + "<extra>OWM</extra>"),
            row=2, col=1)

    fig.add_vline(x=now.isoformat(), line=dict(color="rgba(255,255,255,0.5)", width=1.5, dash="dash"))
    fig.add_vrect(x0=win_start.isoformat(), x1=win_end.isoformat(),
        fillcolor="rgba(255,220,0,0.07)", line=dict(color="rgba(255,220,0,0.35)", width=1),
        annotation_text="тактич. окно", annotation_position="top left",
        annotation_font=dict(color="rgba(255,220,0,0.8)", size=11))

    fig.update_yaxes(title_text="kn", showgrid=True, gridcolor="#2A2A2A", zeroline=False, row=1, col=1)
    fig.update_yaxes(tickvals=tickvals, ticktext=ticktext, showgrid=True, gridcolor="#2A2A2A",
                     zeroline=False, row=2, col=1)
    fig.update_xaxes(showgrid=True, gridcolor="#2A2A2A", tickformat="%H:%M\n%d %b")
    fig.update_layout(height=540, margin=dict(t=60, b=10, l=10, r=10),
        legend=dict(orientation="h", y=1.06, x=1, xanchor="right"),
        plot_bgcolor="#0E1117", paper_bgcolor="#0E1117",
        font=dict(color="#FAFAFA", size=12), hovermode="x unified")
    st.plotly_chart(fig, width="stretch", key=f"chart_{w.name}_{i}_{j}")

    future = [s for s in samples if s.time >= now]
    if future:
        amp = tactics.oscillation_range(future)
        peak = max(future, key=lambda s: s.speed_kn)
        c1, c2, c3 = st.columns(3)
        c1.metric("Пик ветра", f"{peak.speed_kn:.1f} kn", peak.time.strftime("%H:%M"))
        c2.metric("Амплитуда", f"{amp:.0f}°")
        c3.metric("Характер", "осциллирующий" if amp > 30 else "устойчивый")

    if owm_current:
        tz = samples[0].time.tzinfo
        obs_t = owm_current.time.astimezone(tz)
        st.caption(f"**OWM сейчас** ({obs_t.strftime('%H:%M')}) "
                   f"· {owm_current.speed_kn:.1f} kn "
                   f"· порыв {owm_current.gust_kn:.1f} kn "
                   f"· {owm_current.direction_deg:.0f}° "
                   f"({tactics.compass(owm_current.direction_deg)})")


def render(
    field: FineField,
    waypoints: list[Waypoint],
    cfg: RaceConfig,
    *,
    owm_current: list[WindSample | None] | None = None,
    owm_forecast: list[list[WindSample]] | None = None,
) -> None:
    if not waypoints:
        st.info("Поставь знаки на вкладке «Маршрут», чтобы увидеть графики по точкам.")
        return
    tabs = st.tabs([f"{i + 1}. {w.name}" for i, w in enumerate(waypoints)])
    for idx, (tab, w) in enumerate(zip(tabs, waypoints)):
        with tab:
            cur = owm_current[idx] if owm_current and idx < len(owm_current) else None
            fcast = owm_forecast[idx] if owm_forecast and idx < len(owm_forecast) else None
            _render_one(field, w, cfg, owm_current=cur, owm_forecast=fcast)
