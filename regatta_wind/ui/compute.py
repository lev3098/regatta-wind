"""Run the WRF batch from the UI and show live progress.

The "Просчитать прогноз" button launches ``wrf/run_all_macos.sh`` as a detached
process (survives Streamlit reruns and even closing the tab). Progress is tracked
via ``wrf/.compute_status`` + ``wrf/.compute.log`` and auto-refreshed.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

_TZ = ZoneInfo("Asia/Vladivostok")
_GFS_LAG_H = 6        # NOMADS publishes a cycle ~4-5 h after it; step back 6 h to be safe
_GFS_STEP_H = 3       # GFS boundary interval (matches interval_seconds=10800)
_MAX_HORIZON_H = 48   # cap the racing horizon (compute grows with it)

_WRF_DIR = Path(__file__).resolve().parents[2] / "wrf"
_STATUS = _WRF_DIR / ".compute_status"
_LOG = _WRF_DIR / ".compute.log"
_PID = _WRF_DIR / ".compute_pid"
_META = _WRF_DIR / ".compute_meta"
# WRF integration log (latest sim-time per domain) — defaults to the native install.
_RSL = (Path(os.environ.get("WRF_ROOT", str(Path.home() / "wrf")))
        / "WRFV4.8.0" / "test" / "em_real" / "rsl.error.0000")
_TIMING_RE = re.compile(r"Timing for main: time (\d{4}-\d\d-\d\d_\d\d:\d\d:\d\d) on domain\s+1")


def _read_status() -> tuple[str, str, str]:
    """(state, iso_time, detail). state ∈ IDLE|RUNNING|DONE|FAILED."""
    try:
        parts = _STATUS.read_text(encoding="utf-8").strip().split(maxsplit=2)
    except FileNotFoundError:
        return ("IDLE", "", "")
    state = parts[0] if parts else "IDLE"
    when = parts[1] if len(parts) > 1 else ""
    detail = parts[2] if len(parts) > 2 else ""
    return (state, when, detail)


def _pid_alive() -> bool:
    try:
        pid = int(_PID.read_text())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def _current_stage() -> str:
    """Most informative recent line from the batch log."""
    try:
        lines = [l.strip() for l in _LOG.read_text(errors="ignore").splitlines() if l.strip()]
    except FileNotFoundError:
        return ""
    for l in reversed(lines):
        if any(k in l for k in ("[1/5]", "[2/5]", "[3/5]", "[4/5]", "[5/5]", "=====", "FAILED", "✅")):
            return l
    return lines[-1] if lines else ""


def _wrf_fraction() -> tuple[float, float, float] | None:
    """(done_hours, total_hours, fraction 0..1) during wrf.exe, else None.

    Reads the latest 'Timing for main: time … on domain 1' line from rsl.error.0000
    and compares it to the forecast start (GFS cycle) and length.
    """
    try:
        total = float(json.loads(_META.read_text()).get("run_hours", 12))
    except (FileNotFoundError, ValueError):
        total = 12.0
    try:
        cyc = (_WRF_DIR / "gfs" / "CYCLE").read_text().strip()
        start = datetime.strptime(cyc, "%Y-%m-%d_%H")
    except (FileNotFoundError, ValueError):
        return None
    try:
        text = _RSL.read_text(errors="ignore")
    except FileNotFoundError:
        return None
    matches = _TIMING_RE.findall(text)
    if not matches:
        return None
    cur = datetime.strptime(matches[-1], "%Y-%m-%d_%H:%M:%S")
    done = max(0.0, min((cur - start).total_seconds() / 3600.0, total))
    return (done, total, done / total if total else 0.0)


def _elapsed(iso: str) -> str:
    try:
        t0 = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = int((datetime.now(timezone.utc) - t0).total_seconds())
        return f"{secs // 60} мин {secs % 60} с"
    except ValueError:
        return "—"


def is_running() -> bool:
    state, _, _ = _read_status()
    return state == "RUNNING" and _pid_alive()


def latest_gfs_cycle(now_utc: datetime) -> datetime:
    """Newest GFS cycle (00/06/12/18 UTC) likely published by ``now``."""
    base = now_utc - timedelta(hours=_GFS_LAG_H)
    return base.replace(hour=(base.hour // 6) * 6, minute=0, second=0, microsecond=0)


def run_hours_until(end_utc: datetime, now_utc: datetime) -> int:
    """Integration hours from the latest GFS cycle to ``end`` (3-h aligned).

    WRF must spin up from the cycle analysis, so this spans cycle→end even though
    only now→end is shown. Floored at one 3-h step, capped for sanity.
    """
    cycle = latest_gfs_cycle(now_utc)
    hours = (end_utc - cycle).total_seconds() / 3600.0
    stepped = math.ceil(hours / _GFS_STEP_H) * _GFS_STEP_H
    cap = math.ceil((_GFS_LAG_H + _MAX_HORIZON_H) / _GFS_STEP_H) * _GFS_STEP_H
    return int(max(_GFS_STEP_H, min(stepped, cap)))


def start_compute(center_lat: float, center_lon: float, run_hours: int, max_dom: int) -> None:
    env = dict(os.environ)
    env.update(
        CENTER_LAT=f"{center_lat:.4f}", CENTER_LON=f"{center_lon:.4f}",
        RUN_HOURS=str(run_hours), FCST_HOURS=str(run_hours), MAX_DOM=str(max_dom),
    )
    logf = open(_LOG, "w")
    proc = subprocess.Popen(
        ["bash", str(_WRF_DIR / "run_all_macos.sh")],
        cwd=str(_WRF_DIR.parent), env=env,
        stdout=logf, stderr=subprocess.STDOUT,
        start_new_session=True,  # detach so it survives app reruns
    )
    _PID.write_text(str(proc.pid))
    _META.write_text(json.dumps({"run_hours": run_hours, "max_dom": max_dom}))
    _STATUS.write_text(f"RUNNING {datetime.now(timezone.utc).isoformat()} запуск")


def render(center_lat: float, center_lon: float) -> str:
    """Compute panel (sidebar): resolution + horizon + button + live progress.

    Returns the display domain (``d02`` for 3 km, ``d03`` for 1 km) so the app
    reads the matching wrfout.
    """
    state, when, detail = _read_status()
    running = is_running()

    # Resolution → which nest to compute and display.
    res = st.radio("Точность", ["3 км — быстрее", "1 км — точнее, дольше"],
                   index=0, help="1 км: convection-permitting, ловит бриз и тень мысов, "
                                 "но считается заметно дольше.")
    max_dom = 3 if res.startswith("1") else 2
    domain = "d03" if max_dom == 3 else "d02"

    # Horizon → end time. Forecast is shown from now to this point (not earlier).
    horizon = st.slider("Прогноз вперёд, ч", _GFS_STEP_H, _MAX_HORIZON_H, 12, _GFS_STEP_H)
    now_utc = datetime.now(timezone.utc)
    end_loc = (now_utc + timedelta(hours=horizon)).astimezone(_TZ)
    run_hours = run_hours_until(now_utc + timedelta(hours=horizon), now_utc)
    st.caption(f"до **{end_loc:%d %b %H:%M}** (Влд) · WRF интегрирует {run_hours} ч "
               f"от цикла GFS {latest_gfs_cycle(now_utc):%H}Z")

    if running:
        st.info(f"⏳ Считается… запущено {when[11:16]} UTC")
        if st.button("⏹ Прервать", width="stretch"):
            try:
                os.killpg(os.getpgid(int(_PID.read_text())), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass
            _STATUS.write_text(f"FAILED {datetime.now(timezone.utc).isoformat()} прервано")
            st.rerun()

        @st.fragment(run_every=5)
        def _live() -> None:
            if not is_running():
                st.rerun()  # finished → full rerun picks up the new wrfout
                return
            fr = _wrf_fraction()
            if fr is not None:
                done, total, f = fr
                st.progress(f, text=f"WRF: {done:.1f} / {total:.0f} ч посчитано ({f * 100:.0f}%)")
            else:
                st.progress(0.0, text="подготовка: geogrid → ungrib → metgrid → real…")
            st.caption(f"этап: {_current_stage()[:70] or '…'}")
            st.caption(f"⏱ {_elapsed(when)} · обновлено {datetime.now().strftime('%H:%M:%S')}")
        _live()
        return domain

    # not running — show last result + the launch button
    if state == "DONE":
        st.success(f"✅ Прогноз готов ({when[:16].replace('T', ' ')} UTC)")
    elif state == "FAILED":
        st.error(f"❌ Не посчиталось: {detail or 'см. wrf/.compute.log'}")

    label = f"🧮 Просчитать {res.split(' — ')[0]} прогноз"
    if st.button(label, type="primary", width="stretch",
                 help=f"WRF на залив Петра Великого ({center_lat:.2f}, {center_lon:.2f}). "
                      "Можно закрыть вкладку — посчитается в фоне."):
        start_compute(center_lat, center_lon, run_hours, max_dom)
        st.rerun()
    return domain
