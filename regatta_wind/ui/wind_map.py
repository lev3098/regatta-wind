"""Windy-style wind field on a folium map.

The forecast area is drawn as a smooth colour-gradient image overlay (built with
Pillow), with direction arrows, the compute-boundary box, corner labels, a colour
legend and live OpenWeatherMap dots.

The map view (pan/zoom) is preserved across reruns by echoing folium's own
center/zoom back into ``st_folium`` and feeding them in again — this is
deterministic and does not rely on Plotly's uirevision (which Streamlit does not
honour for the map camera).
"""

from __future__ import annotations

import math
from datetime import datetime

import folium
import numpy as np
import streamlit as st
from branca.colormap import LinearColormap
from streamlit_folium import st_folium

from ..models import FineField, Waypoint, WindSample

try:
    from PIL import Image, ImageFilter
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False

# Render the field this big (px, long side) before blurring, so it stays smooth
# at any map zoom instead of showing the WRF grid as flat-colour squares. Kept
# modest because the PNG is embedded in the folium HTML on every interaction.
_TARGET_PX = 800

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


def _field_rgba(speed: np.ndarray, vmax: float, alpha: float) -> np.ndarray | None:
    """North-up RGBA image of the field, upscaled + blurred so it reads as a
    continuous gradient (not flat-colour WRF squares)."""
    if not _HAS_PIL:
        return None
    ny, nx = speed.shape
    fill = float(np.nanmean(speed)) if np.isfinite(speed).any() else 0.0
    filled = np.where(np.isfinite(speed), speed, fill)
    rgba = _speed_to_rgba(filled, vmax, alpha)
    img = Image.fromarray(np.flipud(rgba), mode="RGBA")  # row 0 must be NORTH
    scale = max(8, math.ceil(_TARGET_PX / max(ny, nx)))
    img = img.resize((nx * scale, ny * scale), Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(radius=max(1.0, scale * 0.7)))
    return np.asarray(img)


def _arrow_segments(field: FineField, frame, vmax: float, arrows: int) -> list:
    """MultiLineString coordinates for thinned direction arrows."""
    lat2d, lon2d = field.terrain.lat, field.terrain.lon
    ny, nx = lat2d.shape
    speed, direction = frame.speed_kn, frame.dir_deg
    step = max(1, math.ceil(max(ny, nx) / arrows))
    dlat = float(np.median(np.abs(np.diff(lat2d, axis=0)))) if ny > 1 else 0.01
    alen = step * dlat * 0.9 / max(vmax, 1)
    segs: list = []
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
            segs.append([[lon, lat], [hlon, hlat]])
            back = math.radians(float(direction[i, j]) % 360)
            for side in (-30, 30):
                a = back + math.radians(side)
                segs.append([[hlon, hlat],
                             [hlon + math.sin(a) * length * 0.4 / cosl,
                              hlat + math.cos(a) * length * 0.4]])
    return segs


def _build_map(
    field: FineField,
    idx: int,
    corners: list[Waypoint],
    *,
    vmax: int,
    alpha: float,
    arrows: int,
    rgba: np.ndarray | None,
    owm_points: list[tuple[float, float, WindSample]] | None,
    bounds: tuple[float, float, float, float] | None,
    center: list[float],
    zoom: float,
) -> folium.Map:
    lat2d, lon2d = field.terrain.lat, field.terrain.lon
    lat_lo, lat_hi = float(np.min(lat2d)), float(np.max(lat2d))
    lon_lo, lon_hi = float(np.min(lon2d)), float(np.max(lon2d))
    frame = field.frames[idx]

    m = folium.Map(location=center, zoom_start=zoom, tiles="CartoDB dark_matter",
                   control_scale=True, zoom_control=True)

    # smooth gradient field (image precomputed/memoised by the caller)
    if rgba is not None:
        folium.raster_layers.ImageOverlay(
            image=rgba, bounds=[[lat_lo, lon_lo], [lat_hi, lon_hi]],
            opacity=1.0, origin="upper", zindex=1).add_to(m)

    # direction arrows (one efficient MultiLineString layer)
    if arrows > 0:
        segs = _arrow_segments(field, frame, vmax, arrows)
        if segs:
            folium.GeoJson(
                {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": segs}},
                style_function=lambda _f: {"color": "#ffffff", "weight": 1.2, "opacity": 0.75},
            ).add_to(m)

    # compute-boundary box (forecast is not needed beyond it)
    if bounds is not None:
        lo_a, hi_a, lo_o, hi_o = bounds
        folium.Rectangle([[lo_a, lo_o], [hi_a, hi_o]], color="#ffffff", weight=1.5,
                         opacity=0.55, fill=False).add_to(m)

    # corner reference points (named limits of the compute area)
    for w in corners or []:
        folium.CircleMarker([w.lat, w.lon], radius=4, color="#ffffff", weight=1,
                            fill=True, fill_color="#ffffff", fill_opacity=0.9,
                            tooltip=w.name).add_to(m)
        folium.map.Marker(
            [w.lat, w.lon],
            icon=folium.DivIcon(
                icon_size=(120, 16), icon_anchor=(-6, 8),
                html=f'<div style="font:600 10px sans-serif;color:#fff;'
                     f'text-shadow:0 0 3px #000">{w.name}</div>'),
        ).add_to(m)

    # OpenWeatherMap real-wind dots (sampled across the area)
    for plat, plon, obs in owm_points or []:
        folium.CircleMarker(
            [plat, plon], radius=9, color="#FF9800", weight=1,
            fill=True, fill_color="#FF9800", fill_opacity=0.85,
            tooltip=f"OWM: {obs.speed_kn:.1f} уз · {obs.direction_deg:.0f}° "
                    f"{_compass_short(obs.direction_deg)} · порыв {obs.gust_kn:.1f}").add_to(m)
        folium.map.Marker(
            [plat, plon],
            icon=folium.DivIcon(
                icon_size=(24, 14), icon_anchor=(12, 7),
                html=f'<div style="font:700 9px sans-serif;color:#fff;text-align:center">'
                     f'{obs.speed_kn:.0f}</div>'),
        ).add_to(m)

    # colour legend (knots)
    colours = ["#%02x%02x%02x" % rgb for _p, rgb in _STOPS]
    cmap = LinearColormap(colours, index=[p * vmax for p, _ in _STOPS],
                          vmin=0, vmax=vmax, caption="узлы")
    cmap.add_to(m)
    return m


def render(
    field: FineField,
    corners: list[Waypoint],
    *,
    owm_points: list[tuple[float, float, WindSample]] | None = None,
    bounds: tuple[float, float, float, float] | None = None,
) -> None:
    """Map tab: view controls + hour slider + folium map, in one fragment.

    The view (pan/zoom) is kept by echoing folium's center/zoom through
    session_state, so changing any control does not recentre the map.
    """
    clat = float(np.mean(field.terrain.lat))
    clon = float(np.mean(field.terrain.lon))
    st.session_state.setdefault("wm_center", [clat, clon])
    st.session_state.setdefault("wm_zoom", 10)

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

        # Memoise the (heavy) field image so a pure pan/zoom rerun doesn't rebuild
        # it — only hour/scale/opacity changes do.
        img_key = (id(field), idx, int(vmax), round(float(alpha), 2))
        memo = st.session_state.get("wm_img_memo")
        if memo and memo[0] == img_key:
            rgba = memo[1]
        else:
            rgba = _field_rgba(field.frames[idx].speed_kn, vmax, alpha)
            st.session_state["wm_img_memo"] = (img_key, rgba)

        m = _build_map(field, idx, corners, vmax=vmax, alpha=alpha, arrows=arrows,
                       rgba=rgba, owm_points=owm_points, bounds=bounds,
                       center=st.session_state.wm_center, zoom=st.session_state.wm_zoom)
        out = st_folium(m, key="wind_field_map", use_container_width=True, height=620,
                        center=st.session_state.wm_center, zoom=st.session_state.wm_zoom,
                        returned_objects=["center", "zoom"])
        # echo the user's pan/zoom back so the next rerun keeps the same view
        if out:
            if out.get("center"):
                st.session_state.wm_center = [out["center"]["lat"], out["center"]["lng"]]
            if out.get("zoom") is not None:
                st.session_state.wm_zoom = out["zoom"]

    _panel()
