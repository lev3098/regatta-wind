"""Tests for tactical helpers (shifts, oscillation, compass, distance)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from regatta_wind import tactics
from regatta_wind.models import WindSample


def test_compass_points():
    assert tactics.compass(0) == "С"
    assert tactics.compass(90) == "В"
    assert tactics.compass(180) == "Ю"
    assert tactics.compass(270) == "З"
    assert tactics.compass(359) == "С"  # wraps to north


def test_shift_signed_shortest_arc():
    assert tactics.shift(10, 20) == pytest.approx(10)
    assert tactics.shift(20, 10) == pytest.approx(-10)
    # crossing 0/360 takes the short way
    assert tactics.shift(350, 10) == pytest.approx(20)
    assert tactics.shift(10, 350) == pytest.approx(-20)


def test_unwrap_directions_no_jump():
    dirs = [350, 355, 5, 15]  # crosses 360
    uw = tactics.unwrap_directions(dirs)
    # should be monotonic-ish without a 360 drop
    assert uw == pytest.approx([350, 355, 365, 375])


def test_oscillation_range():
    base = datetime(2026, 6, 13, tzinfo=timezone.utc)
    dirs = [10, 40, 5, 35, 0]
    samples = [
        WindSample(base + timedelta(hours=i), 8.0, d, float("nan")) for i, d in enumerate(dirs)
    ]
    # unwrapped span from min to max
    assert tactics.oscillation_range(samples) == pytest.approx(40.0)


def test_haversine_known_distance():
    # ~1 degree of latitude ≈ 111 km
    d = tactics.haversine_km(43.0, 131.9, 44.0, 131.9)
    assert d == pytest.approx(111.2, abs=1.0)
    assert tactics.haversine_km(43.0, 131.9, 43.0, 131.9) == pytest.approx(0.0, abs=1e-6)


def test_shift_arrow():
    assert tactics.shift_arrow(20) == "→"
    assert tactics.shift_arrow(-20) == "←"
    assert tactics.shift_arrow(2) == ""
