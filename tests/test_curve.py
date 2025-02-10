"""
Fan Curve Tests

This module contains tests for the fan curve implementations.
"""

import pytest
from superfan.control.curve import (
    FanCurve,
    LinearCurve,
    StepCurve,
    HysteresisCurve
)

# Test Data
LINEAR_POINTS = [
    (0, 30),   # At target temp -> 30%
    (10, 50),  # 10°C over -> 50%
    (20, 70),  # 20°C over -> 70%
    (30, 100)  # 30°C over -> 100%
]

STEP_POINTS = [
    (0, 30),   # Up to 10°C -> 30%
    (10, 50),  # 10-20°C -> 50%
    (20, 70),  # 20-30°C -> 70%
    (30, 100)  # Over 30°C -> 100%
]

# LinearCurve Tests

def test_linear_curve_initialization():
    """Test LinearCurve initialization and validation"""
    # Valid initialization
    curve = LinearCurve(LINEAR_POINTS, min_speed=20, max_speed=100)
    assert curve.min_speed == 20
    assert curve.max_speed == 100
    assert curve.points == LINEAR_POINTS

    # Invalid cases
    with pytest.raises(ValueError, match="Must provide at least one point"):
        LinearCurve([])

    with pytest.raises(ValueError, match="Invalid min_speed"):
        LinearCurve(LINEAR_POINTS, min_speed=-1)

    with pytest.raises(ValueError, match="Invalid max_speed"):
        LinearCurve(LINEAR_POINTS, max_speed=101)

    with pytest.raises(ValueError, match="min_speed .* cannot be greater than max_speed"):
        LinearCurve(LINEAR_POINTS, min_speed=50, max_speed=30)

    # Duplicate temperature
    with pytest.raises(ValueError, match="Duplicate temperature"):
        LinearCurve([(0, 30), (0, 40)])

    # Invalid speed
    with pytest.raises(ValueError, match="Invalid speed"):
        LinearCurve([(0, 101)])

    # Invalid temperature
    with pytest.raises(ValueError, match="Invalid temp delta"):
        LinearCurve([(-1, 50)])

def test_linear_curve_interpolation():
    """Test LinearCurve speed interpolation"""
    curve = LinearCurve(LINEAR_POINTS)

    # Test exact points
    assert curve.get_speed(0) == 30
    assert curve.get_speed(10) == 50
    assert curve.get_speed(20) == 70
    assert curve.get_speed(30) == 100

    # Test interpolated points
    assert curve.get_speed(5) == 40   # Halfway between 30% and 50%
    assert curve.get_speed(15) == 60  # Halfway between 50% and 70%
    assert curve.get_speed(25) == 85  # Halfway between 70% and 100%

def test_linear_curve_limits():
    """Test LinearCurve speed limits"""
    curve = LinearCurve(LINEAR_POINTS, min_speed=20, max_speed=90)

    # Below range
    assert curve.get_speed(-10) == 30  # First point speed
    # Above range
    assert curve.get_speed(40) == 90   # Limited by max_speed

def test_linear_curve_single_point():
    """Test LinearCurve with single point"""
    curve = LinearCurve([(0, 50)])
    assert curve.get_speed(-10) == 50  # Below point
    assert curve.get_speed(0) == 50    # At point
    assert curve.get_speed(10) == 50   # Above point

# StepCurve Tests

def test_step_curve_initialization():
    """Test StepCurve initialization and validation"""
    # Valid initialization
    curve = StepCurve(STEP_POINTS, min_speed=20, max_speed=100)
    assert curve.min_speed == 20
    assert curve.max_speed == 100
    assert curve.points == STEP_POINTS

    # Invalid cases
    with pytest.raises(ValueError, match="Must provide at least one step"):
        StepCurve([])

    with pytest.raises(ValueError, match="Invalid min_speed"):
        StepCurve(STEP_POINTS, min_speed=-1)

    with pytest.raises(ValueError, match="Invalid max_speed"):
        StepCurve(STEP_POINTS, max_speed=101)

    with pytest.raises(ValueError, match="min_speed .* cannot be greater than max_speed"):
        StepCurve(STEP_POINTS, min_speed=50, max_speed=30)

def test_step_curve_steps():
    """Test StepCurve step behavior"""
    curve = StepCurve(STEP_POINTS)

    # Test at step points
    assert curve.get_speed(0) == 30
    assert curve.get_speed(10) == 50
    assert curve.get_speed(20) == 70
    assert curve.get_speed(30) == 100

    # Test between steps
    assert curve.get_speed(5) == 30   # First step
    assert curve.get_speed(15) == 50  # Second step
    assert curve.get_speed(25) == 70  # Third step
    assert curve.get_speed(35) == 100 # Last step

def test_step_curve_limits():
    """Test StepCurve speed limits"""
    curve = StepCurve(STEP_POINTS, min_speed=20, max_speed=90)

    # Below range
    assert curve.get_speed(-10) == 30  # First step speed
    # Above range
    assert curve.get_speed(40) == 90   # Limited by max_speed

def test_step_curve_single_step():
    """Test StepCurve with single step"""
    curve = StepCurve([(0, 50)])
    assert curve.get_speed(-10) == 50  # Below step
    assert curve.get_speed(0) == 50    # At step
    assert curve.get_speed(10) == 50   # Above step

# HysteresisCurve Tests

def test_hysteresis_curve_initialization():
    """Test HysteresisCurve initialization"""
    base_curve = LinearCurve(LINEAR_POINTS)
    curve = HysteresisCurve(base_curve, hysteresis=3.0)
    assert curve.curve == base_curve
    assert curve.hysteresis == 3.0
    assert curve._last_temp is None
    assert curve._last_speed is None

def test_hysteresis_curve_first_reading():
    """Test HysteresisCurve first reading behavior"""
    base_curve = LinearCurve(LINEAR_POINTS)
    curve = HysteresisCurve(base_curve, hysteresis=3.0)

    # First reading should always update
    speed = curve.get_speed(10)
    assert speed == 50
    assert curve._last_temp == 10
    assert curve._last_speed == 50

def test_hysteresis_curve_small_changes():
    """Test HysteresisCurve behavior with small temperature changes"""
    base_curve = LinearCurve(LINEAR_POINTS)
    curve = HysteresisCurve(base_curve, hysteresis=3.0)

    # Initial reading
    speed = curve.get_speed(10)  # 50%
    assert speed == 50

    # Small changes (within hysteresis) should maintain speed
    assert curve.get_speed(11) == 50  # +1°C
    assert curve.get_speed(9) == 50   # -1°C
    assert curve.get_speed(12) == 50  # +2°C
    assert curve.get_speed(8) == 50   # -2°C

def test_hysteresis_curve_large_changes():
    """Test HysteresisCurve behavior with large temperature changes"""
    base_curve = LinearCurve(LINEAR_POINTS)
    curve = HysteresisCurve(base_curve, hysteresis=3.0)

    # Initial reading
    speed = curve.get_speed(10)  # 50%
    assert speed == 50

    # Large changes (exceeding hysteresis) should update speed
    assert curve.get_speed(14) > 50  # +4°C
    assert curve.get_speed(6) < 50   # -8°C

def test_hysteresis_curve_absolute_change():
    """Test HysteresisCurve uses absolute temperature change"""
    base_curve = LinearCurve(LINEAR_POINTS)
    curve = HysteresisCurve(base_curve, hysteresis=3.0)

    # Initial reading
    speed = curve.get_speed(10)  # 50%
    assert speed == 50

    # Both positive and negative changes should be considered
    assert curve.get_speed(13) != 50  # +3°C
    speed = curve.get_speed(10)       # Reset
    assert curve.get_speed(7) != 50   # -3°C

def test_hysteresis_curve_chained():
    """Test HysteresisCurve behavior with multiple curves"""
    # Create a chain of curves
    base_curve = LinearCurve(LINEAR_POINTS)
    step_curve = StepCurve(STEP_POINTS)
    hyst_curve1 = HysteresisCurve(base_curve, hysteresis=3.0)
    hyst_curve2 = HysteresisCurve(step_curve, hysteresis=3.0)

    # Both should maintain speed within hysteresis
    assert hyst_curve1.get_speed(10) == hyst_curve1.get_speed(11)
    assert hyst_curve2.get_speed(10) == hyst_curve2.get_speed(11)

    # Both should update with large changes
    assert hyst_curve1.get_speed(14) != hyst_curve1.get_speed(10)
    assert hyst_curve2.get_speed(14) != hyst_curve2.get_speed(10)
