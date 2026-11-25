import pytest

from superfan.curve import Hysteresis, interpolate

CURVE = [(40.0, 20.0), (60.0, 40.0), (80.0, 100.0)]


@pytest.mark.parametrize("temp, duty", [
    (-10, 20), (40, 20), (50, 30), (60, 40), (70, 70), (80, 100), (150, 100),
])
def test_interpolates_between_points_and_is_flat_outside(temp, duty):
    assert interpolate(CURVE, temp) == pytest.approx(duty)


def test_single_point_curve_is_constant():
    assert interpolate([(50.0, 35.0)], 10) == 35
    assert interpolate([(50.0, 35.0)], 90) == 35


def test_hysteresis_follows_rises_at_once():
    h = Hysteresis(3)
    assert [h.update(t) for t in (50, 51, 55)] == [50, 51, 55]


def test_hysteresis_ignores_small_falls_and_accepts_large_ones():
    h = Hysteresis(3)
    h.update(60)
    assert h.update(58) == 60      # 2 below: hold
    assert h.update(57) == 60      # exactly the band: still hold
    assert h.update(56.9) == 56.9  # more than the band: follow
    assert h.update(55) == 56.9


def test_hysteresis_never_reports_below_the_real_temperature():
    h = Hysteresis(5)
    for temp in (40, 70, 66, 64, 72, 30, 31, 29, 80, 79):
        assert h.update(temp) >= temp
