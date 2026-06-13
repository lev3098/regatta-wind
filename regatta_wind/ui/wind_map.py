"""Windy-style wind field on a Plotly map.

Renders the full forecast area as a smooth colour gradient (a raster image overlay
built with Pillow), like windy.com, with optional direction arrows, the race marks
and an hour slider. Falls back to a dense Densitymapbox if Pillow is missing.
"""

from __future__ import annotations

import base64
import io
import math
from datetime import datetime

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from ..models import FineField, Waypoint

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False

# Windy-like speed colour ramp: calm blue → teal → green → yellow → orange → red.
# (position 0..1, RGB). Used both for the raster and the Plotly colourbar.
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


def _ramp_channels() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    pos = np.array([s[0] for s in _STOPS])
    rgb = np.array([s[1] for s in _STOPS], dtype=float)
    return pos, rgb[:, 0], rgb[:, 1], rgb[:, 2]


def _speed_to_rgba(speed: np.ndarray, vmax: float, alpha: float) -> np.ndarray:
    """Map a 2-D speed array to an RGBA uint8 image; NaN/zero-data → transparent."""
    pos, rr, gg, bb = _ramp_channels()
    t = np.clip(np.nan_to_num(speed, nan=0.0) / max(vmax, 1e-3), 0.0, 1.0)
    r = np.interp(t, pos, rr)
    g = np.interp(t, pos, gg)
    b = np.interp(t, pos, bb)
    a = np.where(np.isfinite(speed), int(alpha * 255), 0).astype(np.uint8)
    return np.dstack([r.astype(np.uint8), g.astype(np.uint8), b.astype(np.uint8), a])


def _raster_overlay(field: FineField, speed: np.ndarray, vmax: float, alpha: float):
    """Return (mapbox image-layer dict, bbox) or None if Pillow is unavailable."""
    if not _HAS_PIL:
        return None
    lat, lon = field.terrain.lat, field.terrain.lon
    lat_lo, lat_hi = float(np.min(lat)), float(np.max(lat))
    lon_lo, lon_hi = float(np.min(lon)), float(np.max(lon))

    rgba = _speed_to_rgba(speed, vmax, alpha)
    # image row 0 must be the NORTH edge (lat_hi); WRF row 0 is south → flip
    img = Image.fromarray(np.flipud(rgba), mode="RGBA")
    # upscale smoothly so the gradient looks continuous (Windy-like)
    ny, nx = speed.shape
    scale = max(1, int(700 / max(ny, nx)))
    if scale > 1:
        img = img.resize((nx * scale, ny * scale), Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    layer = dict(
        sourcetype="image",
        source=uri,
        coordinates=[[lon_lo, lat_hi], [lon_hi, lat_hi], [lon_hi, lat_lo], [lon_lo, lat_lo]],
        below="traces",
    )
    return layer, (lat_lo, lat_hi, lon_lo, lon_hi)


def build_figure(
    field: FineField,
    idx: int,
    waypoints: list[Waypoint],
    *,
    vmax: int = 30,
    alpha: float = 0.6,
    arrows: int = 28,
) -> go.Figure:
    terrain = field.terrain
    lat2d, lon2d = terrain.lat, terrain.lon
    ny, nx = lat2d.shape
    frame = field.frames[idx]
    speed, direction, gust = frame.speed_kn, frame.dir_deg, frame.gust_kn

    fig = go.Figure()
    overlay = _raster_overlay(field, speed, vmax, alpha)
    layers: list[dict] = []
    if overlay is not None:
        layer, _ = overlay
        layers.append(layer)
    else:
        # fallback: dense density heatmap
        fig.add_trace(go.Densitymapbox(
            lat=lat2d.ravel(), lon=lon2d.ravel(), z=np.nan_to_num(speed.ravel()),
            radius=25, opacity=alpha, colorscale=_PLOTLY_SCALE, zmin=0, zmax=vmax,
            showscale=False, hoverinfo="skip"))

    # colourbar (dummy trace carrying the scale)
    fig.add_trace(go.Scattermapbox(
        lat=[float(np.mean(lat2d))], lon=[float(np.mean(lon2d))], mode="markers",
        marker=dict(size=0.1, color=[0], colorscale=_PLOTLY_SCALE, cmin=0, cmax=vmax,
                    showscale=True,
                    colorbar=dict(title=dict(text="узлы", font=dict(color="white")),
                                  thickness=14, len=0.6, x=1.0, tickfont=dict(color="white"))),
        hoverinfo="skip", showlegend=False))

    # direction arrows (thinned); arrows = max count per side, 0 = off
    if arrows > 0:
        step = max(1, math.ceil(max(ny, nx) / arrows))
        dlat = float(np.median(np.abs(np.diff(lat2d, axis=0)))) if ny > 1 else 0.01
        alen = step * dlat * 0.9 / max(vmax, 1)
        blat: list = []
        blon: list = []
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
        if blat:
            fig.add_trace(go.Scattermapbox(lat=blat, lon=blon, mode="lines",
                line=dict(color="rgba(255,255,255,0.7)", width=1.3),
                hoverinfo="skip", showlegend=False))

    # hover cells (values on tap)
    step = max(1, math.ceil(max(ny, nx) / 40))
    hl, hn, cd = [], [], []
    for i in range(0, ny, step):
        for j in range(0, nx, step):
            s = float(speed[i, j])
            if not np.isfinite(s):
                continue
            hl.append(float(lat2d[i, j])); hn.append(float(lon2d[i, j]))
            cd.append([s, float(direction[i, j]),
                       float(gust[i, j]) if np.isfinite(gust[i, j]) else float("nan")])
    if hl:
        fig.add_trace(go.Scattermapbox(lat=hl, lon=hn, mode="markers",
            marker=dict(size=14, color="rgba(0,0,0,0)"), customdata=cd,
            hovertemplate="%{customdata[0]:.1f} уз · %{customdata[1]:.0f}°"
                          "<br>порыв %{customdata[2]:.1f} уз<extra></extra>",
            showlegend=False))

    # marks + route
    if waypoints:
        if len(waypoints) >= 2:
            fig.add_trace(go.Scattermapbox(lat=[w.lat for w in waypoints],
                lon=[w.lon for w in waypoints], mode="lines",
                line=dict(color="rgba(255,255,255,0.9)", width=2),
                hoverinfo="skip", showlegend=False))
        fig.add_trace(go.Scattermapbox(lat=[w.lat for w in waypoints],
            lon=[w.lon for w in waypoints], mode="markers+text",
            marker=dict(size=12, color="white"),
            text=[str(i + 1) for i in range(len(waypoints))],
            textfont=dict(color="black", size=11),
            hovertext=[w.name for w in waypoints], hoverinfo="text", showlegend=False))

    fig.update_layout(
        mapbox=dict(style="carto-darkmatter",
                    center=dict(lat=float(np.mean(lat2d)), lon=float(np.mean(lon2d))),
                    zoom=9.2, layers=layers),
        margin=dict(t=0, b=0, l=0, r=0), height=640, uirevision="windmap")
    return fig


def render(field: FineField, waypoints: list[Waypoint], *,
           vmax: int, alpha: float, arrows: int) -> None:
    times = field.times
    if not times:
        st.warning("Поле пустое — нет кадров прогноза.")
        return
    tz = times[0].tzinfo
    now = datetime.now(tz)
    default_idx = min(range(len(times)), key=lambda i: abs((times[i] - now).total_seconds()))
    idx = st.select_slider("⏱ Час прогноза", options=list(range(len(times))),
        value=default_idx, format_func=lambda i: times[i].strftime("%d %b %H:%M"))
    sel = times[idx]
    dh = (sel - now).total_seconds() / 3600
    when = "сейчас" if abs(dh) < 0.5 else (f"{abs(dh):.0f} ч назад" if dh < 0 else f"через {dh:.0f} ч")
    st.caption(f"**{sel.strftime('%d %b %H:%M')}** · {when}")
    fig = build_figure(field, idx, waypoints, vmax=vmax, alpha=alpha, arrows=arrows)
    st.plotly_chart(fig, width="stretch", key="wind_field_map")
