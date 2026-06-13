"""Pick the forecast area and manage race marks — reliably, without map jumping.

* **Area**: draw a rectangle on the map (Draw tool) OR set centre/size on the right.
  Both feed the same `area` state. The map view is kept stable across reruns by
  echoing folium's own center/zoom back into `st_folium` — this is what stops the
  constant "jumping".
* **Marks**: a data table (add / edit / delete rows). Deterministic, unlike map clicks.
"""

from __future__ import annotations

import json

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium

from ..config import save_waypoints
from . import state


def _init_view() -> None:
    a = state.get_area()
    st.session_state.setdefault("rv_center", [a.center_lat, a.center_lon])
    st.session_state.setdefault("rv_zoom", 10)


def _apply_pending_area_to_inputs() -> None:
    """A drawn rectangle writes here; push it into the number-input widgets before
    they are created (so the inputs and the drawing stay in sync)."""
    pend = st.session_state.pop("rv_pending_area", None)
    if pend:
        st.session_state["ar_clat"], st.session_state["ar_clon"], st.session_state["ar_span"] = pend


def _map(left_width_hint: bool = True) -> None:
    a = state.get_area()
    lat_lo, lat_hi, lon_lo, lon_hi = a.bounds
    m = folium.Map(location=st.session_state.rv_center, zoom_start=st.session_state.rv_zoom,
                   tiles="CartoDB positron", control_scale=True)
    folium.Rectangle([[lat_lo, lon_lo], [lat_hi, lon_hi]], color="#1f6feb", weight=2,
                     dash_array="6 6", fill=True, fill_opacity=0.05,
                     tooltip="Область прогноза").add_to(m)
    wpts = state.get_waypoints()
    if len(wpts) >= 2:
        folium.PolyLine([[w.lat, w.lon] for w in wpts], color="#e8590c", weight=3, opacity=0.85).add_to(m)
    for i, w in enumerate(wpts):
        folium.Marker(
            [w.lat, w.lon], tooltip=f"{i + 1}. {w.name}",
            icon=folium.DivIcon(
                html=(f'<div style="background:#1f6feb;color:#fff;border:2px solid #fff;'
                      'border-radius:50%;width:24px;height:24px;line-height:20px;'
                      f'text-align:center;font-weight:700">{i + 1}</div>'),
                icon_size=(24, 24), icon_anchor=(12, 12)),
        ).add_to(m)
    Draw(export=False,
         draw_options={"polyline": False, "polygon": False, "circle": False,
                       "circlemarker": False, "marker": False, "rectangle": True},
         edit_options={"edit": False, "remove": False}).add_to(m)

    out = st_folium(m, key="route_map", height=470,
                    center=st.session_state.rv_center, zoom=st.session_state.rv_zoom,
                    returned_objects=["all_drawings", "center", "zoom"])
    if not out:
        return
    # echo the user's pan/zoom back so the next rerun does not snap the map away
    if out.get("center"):
        st.session_state.rv_center = [out["center"]["lat"], out["center"]["lng"]]
    if out.get("zoom"):
        st.session_state.rv_zoom = out["zoom"]
    # a freshly drawn rectangle → forecast area
    draws = out.get("all_drawings") or []
    if draws:
        sig = json.dumps(draws[-1].get("geometry", {}))
        if sig != st.session_state.get("rv_draw_sig"):
            st.session_state.rv_draw_sig = sig
            try:
                ring = draws[-1]["geometry"]["coordinates"][0]
                lats = [c[1] for c in ring]
                lons = [c[0] for c in ring]
                clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
                span_km = max(max(lats) - min(lats), max(lons) - min(lons)) * 111 / 2
                st.session_state["rv_pending_area"] = (round(clat, 3), round(clon, 3),
                                                       int(max(span_km, 5)))
                st.rerun()
            except (KeyError, IndexError, TypeError):
                pass


def _area_controls() -> None:
    a = state.get_area()
    st.markdown("##### Область прогноза")
    c1, c2 = st.columns(2)
    clat = c1.number_input("Центр, широта", value=float(round(a.center_lat, 3)),
                           min_value=40.0, max_value=48.0, step=0.01, format="%.3f", key="ar_clat")
    clon = c2.number_input("Центр, долгота", value=float(round(a.center_lon, 3)),
                           min_value=128.0, max_value=140.0, step=0.01, format="%.3f", key="ar_clon")
    span = st.slider("Размах области, км", 10, 120,
                     int(round(a.half_span_deg * 111 * 2)), 5, key="ar_span")
    half = span / 111 / 2
    if (abs(clat - a.center_lat) > 1e-4 or abs(clon - a.center_lon) > 1e-4
            or abs(half - a.half_span_deg) > 1e-4):
        state.set_area(clat, clon, half)
        st.rerun()
    if st.button("↺ Центрировать по знакам", width="stretch"):
        wpts = state.get_waypoints()
        if wpts:
            st.session_state["rv_pending_area"] = (
                round(sum(w.lat for w in wpts) / len(wpts), 3),
                round(sum(w.lon for w in wpts) / len(wpts), 3),
                int(round(a.half_span_deg * 111 * 2)),
            )
            st.session_state.rv_center = [st.session_state["rv_pending_area"][0],
                                          st.session_state["rv_pending_area"][1]]
            st.rerun()


def _marks_table(route_path: str) -> None:
    st.markdown("##### Знаки дистанции")
    wpts = state.get_waypoints()
    df = pd.DataFrame(
        [{"Знак": w.name, "Широта": round(w.lat, 4), "Долгота": round(w.lon, 4)} for w in wpts]
        or [{"Знак": "", "Широта": None, "Долгота": None}]
    )
    edited = st.data_editor(
        df, num_rows="dynamic", width="stretch", key="wp_editor",
        column_config={
            "Широта": st.column_config.NumberColumn(format="%.4f", min_value=40.0, max_value=48.0),
            "Долгота": st.column_config.NumberColumn(format="%.4f", min_value=128.0, max_value=140.0),
        },
    )
    new: list[dict] = []
    for _, row in edited.iterrows():
        try:
            lat, lon = float(row["Широта"]), float(row["Долгота"])
        except (TypeError, ValueError):
            continue
        name = str(row["Знак"]).strip() or f"Знак {len(new) + 1}"
        new.append({"name": name, "lat": round(lat, 5), "lon": round(lon, 5)})
    if new != st.session_state.get("rw_waypoints"):
        st.session_state["rw_waypoints"] = new

    if st.button("💾 Сохранить в YAML", type="primary", width="stretch"):
        try:
            save_waypoints(route_path, state.get_waypoints(), state.get_area())
            st.success(f"Сохранено в {route_path}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Не удалось сохранить: {exc}")


def render(route_path: str) -> None:
    _init_view()
    _apply_pending_area_to_inputs()
    st.caption("Нарисуй прямоугольник на карте (инструмент ▭ слева) или задай центр и размах справа — "
               "это область, где считается и рисуется прогноз. Знаки правь в таблице.")
    left, right = st.columns([3, 2])
    with left:
        _map()
    with right:
        _area_controls()
        st.divider()
        _marks_table(route_path)
