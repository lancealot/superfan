"""
Fan Curve Tests

This module contains tests for the fan curve implementations.
"""

import pytest
from superfan.control.curve import LinearFanCurve, StepFanCurve, HysteresisFanCurve

def test_linear_curve_basic():
    """Test basic linear fan curve functionality"""
    # Create curve with two points
    curve = LinearFanCurve(
        points=[(0, 20), (10, 70)],
        min_speed=20,
        max_speed=100
    )
    
    # Test exact points
    assert curve.get_speed(0) == 20
    assert curve.get_speed(10) == 70
    
    # Test interpolation
    assert curve.get_speed(5) == 45  # Halfway between points
    
    # Test limits
    assert curve.get_speed(-5) == 20  # Below minimum
    assert curve.get_speed(15) == 70  # Above maximum point

def test_linear_curve_validation():
    """Test linear fan curve validation"""
    # Test empty points
    with pytest.raises(ValueError):
        LinearFanCurve(points=[])
    
    # Test invalid speed values
    with pytest.raises(ValueError):
        LinearFanCurve(points=[(0, -10), (10, 50)])
    with pytest.raises(ValueError):
        LinearFanCurve(points=[(0, 110), (10, 50)])
    
    # Test invalid temperature values
    with pytest.raises(ValueError):
        LinearFanCurve(points=[(-5, 50), (10, 50)])
    
    # Test duplicate temperatures
    with pytest.raises(ValueError):
        LinearFanCurve(points=[(5, 50), (5, 60)])

def test_step_curve_basic():
    """Test basic step fan curve functionality"""
    # Create curve with three steps
    curve = StepFanCurve(
        steps=[(0, 20), (5, 50), (10, 80)],
        min_speed=20,
        max_speed=100
    )
    
    # Test exact thresholds
    assert curve.get_speed(0) == 20
    assert curve.get_speed(5) == 50
    assert curve.get_speed(10) == 80
    
    # Test between steps
    assert curve.get_speed(3) == 20   # First step
    assert curve.get_speed(7) == 50   # Second step
    assert curve.get_speed(12) == 80  # Last step
    
    # Test limits
    assert curve.get_speed(-5) == 20   # Below minimum
    assert curve.get_speed(15) == 80   # Above maximum step

def test_hysteresis_curve():
    """Test hysteresis fan curve functionality"""
    # Create base linear curve
    base_curve = LinearFanCurve(
        points=[(0, 20), (10, 70)],
        min_speed=20,
        max_speed=100
    )
    
    # Create hysteresis wrapper
    curve = HysteresisFanCurve(
        base_curve=base_curve,
        hysteresis=2.0,
        min_hold_time=0.0  # No hold time for testing
    )
    
    # Initial reading should use base curve
    assert curve.get_speed(5) == 45
    
    # Small change should not update speed
    assert curve.get_speed(6) == 45  # Within hysteresis
    
    # Large change should update speed
    assert curve.get_speed(8) == 60  # Outside hysteresis (20 + (70-20)/(10-0) * 8 = 60)

def test_curve_limits():
    """Test fan curve speed limits"""
    # Create curve with configured limits
    curve = LinearFanCurve(
        points=[(0, 0), (10, 100)],
        min_speed=30,
        max_speed=90
    )
    
    # Test minimum limit
    assert curve.get_speed(0) == 30   # Below minimum
    assert curve.get_speed(3) == 30   # Still below minimum
    
    # Test maximum limit
    assert curve.get_speed(7) == 70   # Normal range
    assert curve.get_speed(10) == 90  # Capped at maximum
