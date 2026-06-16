"""Nudge a model wind field toward real point observations (bias correction).

This is *not* data assimilation inside WRF. After the fact we take point
observations (OpenWeatherMap + Open-Meteo current), compute the model's vector
error at those points for the current hour, spread that correction smoothly over
the grid (inverse-distance weighting) and apply it to every forecast frame with a
time decay — observations only constrain the near term, WRF takes over later.

Correction is done in wind-vector (u, v) space so speed *and* direction shift
coherently. Per-component shifts are clamped so a single bad station can't blow up
the field.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from .grid import speed_dir_to_uv, uv_to_speed_dir
from .models import FieldFrame, FineField, WindSample

_DEG_LAT_KM = 111.32


def bias_correct(
    field: FineField,
    obs: list[tuple[float, float, WindSample]],
    *,
    tau_h: float = 6.0,        # time-decay constant (correction ~e-folds every tau_h)
    radius_km: float = 25.0,   # IDW length scale
    max_kn: float = 12.0,      # clamp per-component correction
) -> FineField:
    """Return a new field nudged toward the observations, or the field unchanged."""
    if not obs or not field.frames:
        return field

    lat2d = field.terrain.lat
    lon2d = field.terrain.lon
    ny, nx = lat2d.shape

    olat = np.array([o[0] for o in obs], dtype=float)
    olon = np.array([o[1] for o in obs], dtype=float)
    ospd = np.array([o[2].speed_kn for o in obs], dtype=float)
    odir = np.array([o[2].direction_deg for o in obs], dtype=float)
    ou, ov = speed_dir_to_uv(ospd, odir)

    # model frame nearest "now" (observations are ~current)
    tz = field.times[0].tzinfo
    now = datetime.now(tz)
    base_idx = min(range(len(field.times)),
                   key=lambda i: abs((field.times[i] - now).total_seconds()))
    base = field.frames[base_idx]
    mu, mv = speed_dir_to_uv(base.speed_kn, base.dir_deg)

    # residual (obs - model) at each obs point, clamped
    resu = np.zeros(len(obs))
    resv = np.zeros(len(obs))
    for k in range(len(obs)):
        d2 = (lat2d - olat[k]) ** 2 + (lon2d - olon[k]) ** 2
        i, j = np.unravel_index(int(np.argmin(d2)), d2.shape)
        if not (np.isfinite(mu[i, j]) and np.isfinite(mv[i, j])
                and np.isfinite(ou[k]) and np.isfinite(ov[k])):
            continue
        resu[k] = np.clip(ou[k] - mu[i, j], -max_kn, max_kn)
        resv[k] = np.clip(ov[k] - mv[i, j], -max_kn, max_kn)

    # inverse-distance-weighted correction field (ny, nx) from the point residuals
    cosl = float(np.cos(np.radians(np.mean(lat2d))))
    r2 = (radius_km / _DEG_LAT_KM) ** 2  # length scale (deg^2) — also avoids /0
    cu = np.zeros((ny, nx))
    cv = np.zeros((ny, nx))
    wsum = np.zeros((ny, nx))
    for k in range(len(obs)):
        dy = lat2d - olat[k]
        dx = (lon2d - olon[k]) * cosl
        w = 1.0 / (dx * dx + dy * dy + r2)
        cu += w * resu[k]
        cv += w * resv[k]
        wsum += w
    cu /= wsum
    cv /= wsum

    # apply to every frame, decaying with hours from now
    new_frames: list[FieldFrame] = []
    for t, fr in zip(field.times, field.frames):
        hours = abs((t - now).total_seconds()) / 3600.0
        decay = float(np.exp(-hours / max(tau_h, 1e-3)))
        u, v = speed_dir_to_uv(fr.speed_kn, fr.dir_deg)
        spd, dr = uv_to_speed_dir(u + decay * cu, v + decay * cv)
        ratio = np.where(fr.speed_kn > 0.1, spd / np.maximum(fr.speed_kn, 0.1), 1.0)
        new_frames.append(FieldFrame(time=t, speed_kn=spd, dir_deg=dr,
                                     gust_kn=fr.gust_kn * ratio, confidence=fr.confidence))

    return FineField(terrain=field.terrain, times=field.times, frames=new_frames,
                     grid_km=field.grid_km, source=field.source + " + факт", trusted=field.trusted)
