"""Session state for the interactive route editor.

Waypoints and the forecast area live in ``st.session_state`` so they are shared
across tabs and survive reruns. Waypoints are stored as plain dicts (easy to
mutate) and converted to :class:`Waypoint` on demand.
"""

from __future__ import annotations

import streamlit as st

from ..config import AreaConfig, RaceConfig
from ..models import Waypoint

_WPTS = "rw_waypoints"
_AREA = "rw_area"
_INIT = "rw_initialised"


def ensure_state(cfg: RaceConfig) -> None:
    """Populate session state from the config once per session."""
    if st.session_state.get(_INIT):
        return
    st.session_state[_WPTS] = [
        {"name": w.name, "lat": float(w.lat), "lon": float(w.lon)} for w in cfg.waypoints
    ]
    st.session_state[_AREA] = {
        "center_lat": cfg.area.center_lat,
        "center_lon": cfg.area.center_lon,
        "half_span_deg": cfg.area.half_span_deg,
    }
    st.session_state[_INIT] = True


# ── waypoints ─────────────────────────────────────────────────────────────────
def get_waypoints() -> list[Waypoint]:
    return [Waypoint(**w) for w in st.session_state.get(_WPTS, [])]


def _raw() -> list[dict]:
    return st.session_state.setdefault(_WPTS, [])


def add_waypoint(lat: float, lon: float, name: str | None = None) -> None:
    wpts = _raw()
    name = name or f"Знак {len(wpts) + 1}"
    wpts.append({"name": name, "lat": round(float(lat), 5), "lon": round(float(lon), 5)})


def update_waypoint(idx: int, *, lat: float | None = None, lon: float | None = None,
                    name: str | None = None) -> None:
    wpts = _raw()
    if not 0 <= idx < len(wpts):
        return
    if lat is not None:
        wpts[idx]["lat"] = round(float(lat), 5)
    if lon is not None:
        wpts[idx]["lon"] = round(float(lon), 5)
    if name is not None:
        wpts[idx]["name"] = name


def remove_waypoint(idx: int) -> None:
    wpts = _raw()
    if 0 <= idx < len(wpts):
        wpts.pop(idx)


def move_waypoint(idx: int, delta: int) -> None:
    wpts = _raw()
    j = idx + delta
    if 0 <= idx < len(wpts) and 0 <= j < len(wpts):
        wpts[idx], wpts[j] = wpts[j], wpts[idx]


def clear_waypoints() -> None:
    st.session_state[_WPTS] = []


# ── area ────────────────────────────────────────────────────────────────────
def get_area() -> AreaConfig:
    a = st.session_state.get(_AREA, {})
    return AreaConfig(
        center_lat=a.get("center_lat", AreaConfig().center_lat),
        center_lon=a.get("center_lon", AreaConfig().center_lon),
        half_span_deg=a.get("half_span_deg", AreaConfig().half_span_deg),
    )


def set_area_from_bounds(lat_lo: float, lat_hi: float, lon_lo: float, lon_hi: float) -> None:
    st.session_state[_AREA] = {
        "center_lat": (lat_lo + lat_hi) / 2,
        "center_lon": (lon_lo + lon_hi) / 2,
        "half_span_deg": max((lat_hi - lat_lo) / 2, (lon_hi - lon_lo) / 2, 0.05),
    }


def set_area(center_lat: float, center_lon: float, half_span_deg: float) -> None:
    st.session_state[_AREA] = {
        "center_lat": float(center_lat),
        "center_lon": float(center_lon),
        "half_span_deg": float(half_span_deg),
    }
