"""Fan curves: temperature in, duty cycle out."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

Point = Tuple[float, float]  # (temperature in deg C, duty in %)


def interpolate(points: Sequence[Point], temp: float) -> float:
    """Duty for `temp`: linear between points, flat beyond either end."""
    if temp <= points[0][0]:
        return points[0][1]
    if temp >= points[-1][0]:
        return points[-1][1]
    for (t1, d1), (t2, d2) in zip(points, points[1:]):
        if t1 <= temp <= t2:
            return d1 + (d2 - d1) * (temp - t1) / (t2 - t1)
    return points[-1][1]


class Hysteresis:
    """Follow a rising temperature at once; ignore falls smaller than `band`.

    The value returned is never below the real temperature, so the curve can
    only err towards more cooling.
    """

    def __init__(self, band: float) -> None:
        self.band = band
        self.value: Optional[float] = None

    def update(self, temp: float) -> float:
        if self.value is None or temp >= self.value or temp < self.value - self.band:
            self.value = temp
        return self.value
