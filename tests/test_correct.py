"""Bias-correction: the field should move toward observations near 'now' and
decay into the future."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from regatta_wind import correct
from regatta_wind.models import FieldFrame, FineField, TerrainGrid, WindSample


def _uniform_field(speed: float, direction: float):
    now = datetime.now(timezone.utc)
    ny, nx = 11, 11
    lat = np.linspace(42.8, 43.2, ny)[:, None] * np.ones((1, nx))
    lon = np.ones((ny, 1)) * np.linspace(131.6, 132.0, nx)[None, :]
    frames = [
        FieldFrame(time=now + timedelta(hours=h),
                   speed_kn=np.full((ny, nx), speed),
                   dir_deg=np.full((ny, nx), direction),
                   gust_kn=np.full((ny, nx), speed * 1.2),
                   confidence=np.ones((ny, nx)))
        for h in (0, 6)
    ]
    terrain = TerrainGrid(lat=lat, lon=lon, elevation_m=np.zeros((ny, nx)),
                          is_land=np.zeros((ny, nx), dtype=bool))
    return FineField(terrain=terrain, times=[f.time for f in frames], frames=frames,
                     grid_km=3.0, source="test", trusted=True), now


def test_bias_correct_moves_toward_obs_and_decays():
    field, now = _uniform_field(10.0, 270.0)
    clat, clon = 43.0, 131.8
    obs = [(clat, clon, WindSample(time=now, speed_kn=16.0, direction_deg=270.0,
                                   gust_kn=18.0, confidence=0.9))]

    out = correct.bias_correct(field, obs, tau_h=6.0, radius_km=25.0)
    i, j = out.nearest_index(clat, clon)
    s_now = float(out.frames[0].speed_kn[i, j])
    s_future = float(out.frames[1].speed_kn[i, j])

    assert 10.0 < s_now <= 16.5            # moved toward the 16 kn observation
    assert s_future < s_now                # correction decays into the future
    # direction essentially unchanged (obs same bearing)
    assert abs(((out.frames[0].dir_deg[i, j] - 270 + 180) % 360) - 180) < 5
    assert out.source.endswith("факт")


def test_bias_correct_no_obs_is_identity():
    field, _ = _uniform_field(8.0, 200.0)
    assert correct.bias_correct(field, []) is field
