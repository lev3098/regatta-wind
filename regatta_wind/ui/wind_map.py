"""Windy-style wind field on a Plotly map (MapLibre engine).

The forecast area is a smooth colour-gradient image overlay (built with Pillow),
with direction arrows, the compute-boundary box, corner labels, a colour legend
and live OpenWeatherMap dots.

Keeping the user's pan/zoom across reruns relies on Plotly's ``uirevision`` — which
Streamlit only honours when it can do an in-place ``Plotly.react``. It falls back
to ``newPlot`` (resetting the camera) when the figure's TRACE COUNT changes between
reruns, so we always emit the SAME set of traces (empty when there's no data).
"""

from __future__ import annotations

import base64
import io
import math
from datetime import datetime

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from ..models import FineField, Waypoint, WindSample

try:
    from PIL import Image, ImageFilter
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False

# Render the field this big (px, long side) before blurring, so it stays smooth
# at any zoom instead of showing the WRF grid as flat-colour squares.
_TARGET_PX = 1400

# Windy-like speed colour ramp: calm blue → teal → green → yellow → orange → red.
_STOPS = [
    (0.00, (40, 90, 160)),
    (0.15, (54, 160, 204)),
    (0.32, (90, 200, 170)),
    (0.50, (150, 210, 120)),
    (0.64, (235, 225, 110)),
    (0.78, (240, 165, 75)),
    (0.90, (224, 80, 60)),
    (1.00, (160, 40, 90)),
]
_PLOTLY_SCALE = [(p, f"rgb({r},{g},{b})") for p, (r, g, b) in _STOPS]
_COMPASS8 = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]


def _compass_short(deg: float) -> str:
    return _COMPASS8[round(deg / 45) % 8]


def _ramp_channels() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    pos = np.array([s[0] for s in _STOPS])
    rgb = np.array([s[1] for s in _STOPS], dtype=float)
    return pos, rgb[:, 0], rgb[:, 1], rgb[:, 2]


def _speed_to_rgba(speed: np.ndarray, vmax: float, alpha: float) -> np.ndarray:
    """Map a 2-D speed array to an RGBA uint8 image; NaN/no-data → transparent."""
    pos, rr, gg, bb = _ramp_channels()
    t = np.clip(np.nan_to_num(speed, nan=0.0) / max(vmax, 1e-3), 0.0, 1.0)
    r = np.interp(t, pos, rr)
    g = np.interp(t, pos, gg)
    b = np.interp(t, pos, bb)
    a = np.where(np.isfinite(speed), int(np.clip(alpha, 0, 1) * 255), 0).astype(np.uint8)
    return np.dstack([r.astype(np.uint8), g.astype(np.uint8), b.astype(np.uint8), a])


def _raster_overlay(field: FineField, speed: np.ndarray, vmax: float, alpha: float):
    """Return a MapLibre image-layer dict, or None if Pillow is unavailable.

    Upscaled (bicubic) + sub-cell Gaussian blur so the WRF grid melts into a
    continuous gradient instead of flat-colour squares.
    """
    if not _HAS_PIL:
        return None
    lat, lon = field.terrain.lat, field.terrain.lon
    lat_lo, lat_hi = float(np.min(lat)), float(np.max(lat))
    lon_lo, lon_hi = float(np.min(lon)), float(np.max(lon))

    ny, nx = speed.shape
    fill = float(np.nanmean(speed)) if np.isfinite(speed).any() else 0.0
    filled = np.where(np.isfinite(speed), speed, fill)

    rgba = _speed_to_rgba(filled, vmax, alpha)
    img = Image.fromarray(np.flipud(rgba), mode="RGBA")  # row 0 must be NORTH
    scale = max(8, math.ceil(_TARGET_PX / max(ny, nx)))
    img = img.resize((nx * scale, ny * scale), Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(radius=max(1.0, scale * 0.7)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return dict(
        sourcetype="image", source=uri,
        coordinates=[[lon_lo, lat_hi], [lon_hi, lat_hi], [lon_hi, lat_lo], [lon_lo, lat_lo]],
    )


def _arrow_lines(field: FineField, frame, vmax: float, arrows: int):
    """(lats, lons) with None separators for thinned direction arrows."""
    lat2d, lon2d = field.terrain.lat, field.terrain.lon
    ny, nx = lat2d.shape
    speed, direction = frame.speed_kn, frame.dir_deg
    blat: list = []
    blon: list = []
    if arrows <= 0:
        return blat, blon
    step = max(1, math.ceil(max(ny, nx) / arrows))
    dlat = float(np.median(np.abs(np.diff(lat2d, axis=0)))) if ny > 1 else 0.01
    alen = step * dlat * 0.9 / max(vmax, 1)
    for i in range(0, ny, step):
        for j in range(0, nx, step):
            spd = float(speed[i, j])
            if not np.isfinite(spd) or spd <= 0.5:
                continue
            lat, lon = float(lat2d[i, j]), float(lon2d[i, j])
            cosl = max(math.cos(math.radians(lat)), 0.3)
            length = spd * alen
            bearing = math.radians((float(direction[i, j]) + 180) % 360)
            hlat = lat + math.cos(bearing) * length
            hlon = lon + math.sin(bearing) * length / cosl
            blat += [lat, hlat, None]
            blon += [lon, hlon, None]
            back = math.radians(float(direction[i, j]) % 360)
            for side in (-30, 30):
                a = back + math.radians(side)
                blat += [hlat, hlat + math.cos(a) * length * 0.4, None]
                blon += [hlon, hlon + math.sin(a) * length * 0.4 / cosl, None]
    return blat, blon


def build_figure(
    field: FineField,
    idx: int,
    corners: list[Waypoint],
    *,
    vmax: int = 30,
    alpha: float = 0.6,
    arrows: int = 28,
    owm_points: list[tuple[float, float, WindSample]] | None = None,
    bounds: tuple[float, float, float, float] | None = None,
) -> go.Figure:
    lat2d, lon2d = field.terrain.lat, field.terrain.lon
    ny, nx = lat2d.shape
    frame = field.frames[idx]
    speed, direction, gust = frame.speed_kn, frame.dir_deg, frame.gust_kn
    clat, clon = float(np.mean(lat2d)), float(np.mean(lon2d))

    fig = go.Figure()
    overlay = _raster_overlay(field, speed, vmax, alpha)
    layers = [overlay] if overlay is not None else []
    if overlay is None:  # PIL missing → density fallback (kept first, stable)
        fig.add_trace(go.Densitymap(
            lat=lat2d.ravel(), lon=lon2d.ravel(), z=np.nan_to_num(speed.ravel()),
            radius=25, opacity=alpha, colorscale=_PLOTLY_SCALE, zmin=0, zmax=vmax,
            showscale=False, hoverinfo="skip"))

    # ── A STABLE SET OF TRACES (always the same count/order) so Streamlit does an
    #    in-place react and uirevision keeps the camera. Empty arrays when no data.

    # 1) colour legend (dummy point carrying the colorbar)
    fig.add_trace(go.Scattermap(
        lat=[clat], lon=[clon], mode="markers",
        marker=dict(size=0.1, color=[0], colorscale=_PLOTLY_SCALE, cmin=0, cmax=vmax,
                    showscale=True,
                    colorbar=dict(title=dict(text="узлы", font=dict(color="white")),
                                  thickness=14, len=0.6, x=1.0, tickfont=dict(color="white"))),
        hoverinfo="skip", showlegend=False))

    # 2) direction arrows
    blat, blon = _arrow_lines(field, frame, vmax, arrows)
    fig.add_trace(go.Scattermap(lat=blat, lon=blon, mode="lines",
        line=dict(color="rgba(255,255,255,0.7)", width=1.3),
        hoverinfo="skip", showlegend=False))

    # 3) hover cells (values on tap)
    hstep = max(1, math.ceil(max(ny, nx) / 40))
    hl, hn, cd = [], [], []
    for i in range(0, ny, hstep):
        for j in range(0, nx, hstep):
            s = float(speed[i, j])
            if not np.isfinite(s):
                continue
            hl.append(float(lat2d[i, j])); hn.append(float(lon2d[i, j]))
            cd.append([s, float(direction[i, j]),
                       float(gust[i, j]) if np.isfinite(gust[i, j]) else float("nan")])
    fig.add_trace(go.Scattermap(lat=hl, lon=hn, mode="markers",
        marker=dict(size=14, color="rgba(0,0,0,0)"), customdata=cd,
        hovertemplate="%{customdata[0]:.1f} уз · %{customdata[1]:.0f}°"
                      "<br>порыв %{customdata[2]:.1f} уз<extra></extra>",
        showlegend=False))

    # 4) compute-boundary box
    if bounds is not None:
        lo_a, hi_a, lo_o, hi_o = bounds
        brlat = [lo_a, lo_a, hi_a, hi_a, lo_a]
        brlon = [lo_o, hi_o, hi_o, lo_o, lo_o]
    else:
        brlat, brlon = [], []
    fig.add_trace(go.Scattermap(lat=brlat, lon=brlon, mode="lines",
        line=dict(color="rgba(255,255,255,0.55)", width=1.5),
        hoverinfo="skip", showlegend=False))

    # 5) corner reference points (named limits of the compute area)
    cps = corners or []
    fig.add_trace(go.Scattermap(
        lat=[w.lat for w in cps], lon=[w.lon for w in cps], mode="markers+text",
        marker=dict(size=7, color="rgba(255,255,255,0.9)"),
        text=[w.name for w in cps], textposition="top center",
        textfont=dict(color="rgba(255,255,255,0.85)", size=9),
        hovertext=[w.name for w in cps], hoverinfo="text", showlegend=False))

    # 6) OpenWeatherMap real-wind dots (sampled across the area)
    pts = owm_points or []
    fig.add_trace(go.Scattermap(
        lat=[p[0] for p in pts], lon=[p[1] for p in pts], mode="markers+text",
        marker=dict(size=18, color="#FF9800", opacity=0.85),
        text=[f"{p[2].speed_kn:.0f}" for p in pts], textfont=dict(color="white", size=9),
        hovertext=[f"OWM: {p[2].speed_kn:.1f} уз · {p[2].direction_deg:.0f}° "
                   f"{_compass_short(p[2].direction_deg)}<br>порыв {p[2].gust_kn:.1f} уз"
                   for p in pts],
        hoverinfo="text", name="OWM", showlegend=False))

    fig.update_layout(
        map=dict(style="carto-darkmatter", center=dict(lat=clat, lon=clon),
                 zoom=9.2, layers=layers, uirevision="windmap"),
        margin=dict(t=0, b=0, l=0, r=0), height=640, uirevision="windmap")
    return fig


def render(
    field: FineField,
    corners: list[Waypoint],
    *,
    owm_points: list[tuple[float, float, WindSample]] | None = None,
    bounds: tuple[float, float, float, float] | None = None,
) -> None:
    """Map tab: view controls + hour slider + chart, in one fragment."""

    @st.fragment
    def _panel() -> None:
        c1, c2, c3 = st.columns(3)
        vmax = c1.slider("Макс. шкала, узлы", 5, 50, 30, 5, key="wm_vmax")
        alpha = c2.slider("Непрозрачность", 0.2, 0.9, 0.6, 0.05, key="wm_alpha")
        arrows = c3.slider("Плотность стрелок", 0, 50, 28, 2, key="wm_arrows",
                           help="Сколько стрелок по стороне сетки (0 = без)")

        times = field.times
        if not times:
            st.warning("Поле пустое — нет кадров прогноза.")
            return
        tz = times[0].tzinfo
        now = datetime.now(tz)
        default_idx = min(range(len(times)), key=lambda i: abs((times[i] - now).total_seconds()))
        if len(times) == 1:
            idx = 0
        else:
            idx = st.select_slider("⏱ Час прогноза", options=list(range(len(times))),
                value=default_idx, format_func=lambda i: times[i].strftime("%d %b %H:%M"))
        sel = times[idx]
        dh = (sel - now).total_seconds() / 3600
        when = ("сейчас" if abs(dh) < 0.5
                else (f"{abs(dh):.0f} ч назад" if dh < 0 else f"через {dh:.0f} ч"))
        st.caption(f"**{sel.strftime('%d %b %H:%M')}** · {when}")

        fig = build_figure(field, idx, corners, vmax=vmax, alpha=alpha, arrows=arrows,
                           owm_points=owm_points, bounds=bounds)
        st.plotly_chart(fig, width="stretch", key="wind_field_map")

    _panel()


def render_video_export(field: FineField, corners: list[Waypoint]) -> None:
    """Render the forecast to an .mp4 (smooth gradient + arrows, N s per hour)."""
    from .. import video

    with st.expander("🎬 Экспорт видео (.mp4) — анимация по часам"):
        c1, c2 = st.columns(2)
        sec = c1.slider("Секунд на час", 2, 10, 5, 1, key="vid_sec")
        fps = c2.select_slider("Плавность (FPS)", options=[8, 10, 12, 15, 24],
                               value=12, key="vid_fps")
        vmax = int(st.session_state.get("wm_vmax", 30))
        arrows = int(st.session_state.get("wm_arrows", 28))
        nseg = max(len(field.times) - 1, 1)
        st.caption(f"≈ {sec * nseg + 1} с · {len(field.times)} ч · шкала {vmax} уз · "
                   f"стрелки {arrows} · сетка {field.grid_km:g} км")

        if st.button("🎬 Собрать видео", type="primary", width="stretch", key="vid_make"):
            bar = st.progress(0.0, text="Рендер кадров…")
            try:
                st.session_state["vid_bytes"] = video.render_mp4(
                    field, corners, seconds_per_hour=sec, fps=int(fps), vmax=vmax, arrows=arrows,
                    progress=lambda d, t: bar.progress(d / t, text=f"Кадр {d}/{t}"))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Не удалось собрать видео: {exc}")
            finally:
                bar.empty()

        data = st.session_state.get("vid_bytes")
        if data:
            st.download_button("⬇ Скачать .mp4", data, file_name="regatta_wind.mp4",
                               mime="video/mp4", width="stretch", key="vid_dl")
            st.video(data)
