"""Run the WRF batch from the UI and show live progress.

The "Просчитать прогноз" button launches ``wrf/run_all_macos.sh`` as a detached
process (survives Streamlit reruns and even closing the tab). Progress is tracked
via ``wrf/.compute_status`` + ``wrf/.compute.log`` and auto-refreshed.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

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


def start_compute(center_lat: float, center_lon: float, run_hours: int, max_dom: int) -> None:
    env = dict(os.environ)
    env.update(
        CENTER_LAT=f"{center_lat:.4f}", CENTER_LON=f"{center_lon:.4f}",
        RUN_HOURS=str(run_hours), MAX_DOM=str(max_dom),
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


def render(center_lat: float, center_lon: float, run_hours: int, max_dom: int) -> None:
    """Compute button + live status panel (sidebar)."""
    state, when, detail = _read_status()
    running = is_running()

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
        return

    # not running — show last result + the launch button
    if state == "DONE":
        st.success(f"✅ Прогноз готов ({when[:16].replace('T', ' ')} UTC)")
    elif state == "FAILED":
        st.error(f"❌ Не посчиталось: {detail or 'см. wrf/.compute.log'}")

    label = "🧮 Просчитать прогноз здесь"
    if st.button(label, type="primary", width="stretch",
                 help=f"Запустит WRF на центр области ({center_lat:.2f}, {center_lon:.2f}). "
                      "Считается ~10–20 мин, можно закрыть вкладку — посчитается в фоне."):
        start_compute(center_lat, center_lon, run_hours, max_dom)
        st.rerun()
