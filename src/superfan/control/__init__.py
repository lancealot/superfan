"""
Control package for Superfan

This package provides modules for fan speed control logic,
including fan curves and control loop management.
"""

from .curve import FanCurve, LinearFanCurve, StepFanCurve, HysteresisFanCurve

__all__ = [
    'FanCurve',
    'LinearFanCurve',
    'StepFanCurve',
    'HysteresisFanCurve'
]
