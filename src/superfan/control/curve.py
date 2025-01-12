"""
Fan Curve Module

This module provides fan curve implementations for mapping
temperature readings to fan speeds.
"""

from abc import ABC, abstractmethod
from typing import List, Tuple, Optional
import bisect
import logging

logger = logging.getLogger(__name__)

class FanCurve(ABC):
    """Abstract base class for fan curves"""
    
    @abstractmethod
    def get_speed(self, temp_delta: float) -> int:
        """Get fan speed percentage for temperature delta
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        pass

class LinearFanCurve(FanCurve):
    """Linear fan curve implementation with configurable points"""
    
    def __init__(self, points: List[Tuple[float, int]], 
                 min_speed: int = 20,
                 max_speed: int = 100):
        """Initialize linear fan curve
        
        Args:
            points: List of (temp_delta, speed) tuples defining the curve
            min_speed: Minimum fan speed percentage
            max_speed: Maximum fan speed percentage
            
        Raises:
            ValueError: If points are invalid
        """
        if not points:
            raise ValueError("At least one point required")
            
        # Validate and sort points
        self._validate_points(points)
        self.points = sorted(points)
        
        # Store limits
        self.min_speed = min_speed
        self.max_speed = max_speed
        
        # Pre-calculate slopes between points
        self._slopes: List[float] = []
        for i in range(len(self.points) - 1):
            x1, y1 = self.points[i]
            x2, y2 = self.points[i + 1]
            slope = (y2 - y1) / (x2 - x1)
            self._slopes.append(slope)
            
        logger.debug(f"Initialized fan curve with {len(points)} points")

    def _validate_points(self, points: List[Tuple[float, int]]) -> None:
        """Validate fan curve points
        
        Args:
            points: List of (temp_delta, speed) tuples
            
        Raises:
            ValueError: If points are invalid
        """
        if not all(isinstance(x, (int, float)) and isinstance(y, int) 
                  for x, y in points):
            raise ValueError("Points must be (float, int) tuples")
            
        if not all(0 <= y <= 100 for _, y in points):
            raise ValueError("Speed values must be 0-100")
            
        if not all(x >= 0 for x, _ in points):
            raise ValueError("Temperature deltas must be non-negative")
            
        # Check for duplicate x values
        x_values = [x for x, _ in points]
        if len(x_values) != len(set(x_values)):
            raise ValueError("Duplicate temperature delta values")

    def get_speed(self, temp_delta: float) -> int:
        """Get fan speed percentage for temperature delta
        
        Uses linear interpolation between curve points.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        # Handle temperature below first point
        if temp_delta <= self.points[0][0]:
            return max(self.min_speed, self.points[0][1])
            
        # Handle temperature above last point    
        if temp_delta >= self.points[-1][0]:
            return min(self.max_speed, self.points[-1][1])
            
        # Find surrounding points for interpolation
        idx = bisect.bisect_right([x for x, _ in self.points], temp_delta) - 1
        x1, y1 = self.points[idx]
        
        # Calculate speed using pre-computed slope
        slope = self._slopes[idx]
        speed = int(y1 + slope * (temp_delta - x1))
        
        # Clamp to limits
        return max(self.min_speed, min(self.max_speed, speed))

class StepFanCurve(FanCurve):
    """Step function fan curve implementation"""
    
    def __init__(self, steps: List[Tuple[float, int]],
                 min_speed: int = 20,
                 max_speed: int = 100):
        """Initialize step fan curve
        
        Args:
            steps: List of (temp_threshold, speed) tuples defining steps
            min_speed: Minimum fan speed percentage
            max_speed: Maximum fan speed percentage
            
        Raises:
            ValueError: If steps are invalid
        """
        if not steps:
            raise ValueError("At least one step required")
            
        # Validate and sort steps
        self._validate_steps(steps)
        self.steps = sorted(steps)
        
        # Store limits
        self.min_speed = min_speed
        self.max_speed = max_speed
        
        logger.debug(f"Initialized step fan curve with {len(steps)} steps")

    def _validate_steps(self, steps: List[Tuple[float, int]]) -> None:
        """Validate fan curve steps
        
        Args:
            steps: List of (temp_threshold, speed) tuples
            
        Raises:
            ValueError: If steps are invalid
        """
        if not all(isinstance(x, (int, float)) and isinstance(y, int)
                  for x, y in steps):
            raise ValueError("Steps must be (float, int) tuples")
            
        if not all(0 <= y <= 100 for _, y in steps):
            raise ValueError("Speed values must be 0-100")
            
        if not all(x >= 0 for x, _ in steps):
            raise ValueError("Temperature thresholds must be non-negative")
            
        # Check for duplicate thresholds
        x_values = [x for x, _ in steps]
        if len(x_values) != len(set(x_values)):
            raise ValueError("Duplicate temperature threshold values")

    def get_speed(self, temp_delta: float) -> int:
        """Get fan speed percentage for temperature delta
        
        Returns the speed associated with the highest step threshold
        that is less than or equal to the temperature delta.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        # Handle temperature below first step
        if temp_delta < self.steps[0][0]:
            return self.min_speed
            
        # Find appropriate step
        idx = bisect.bisect_right([x for x, _ in self.steps], temp_delta) - 1
        speed = self.steps[idx][1]
        
        # Clamp to limits
        return max(self.min_speed, min(self.max_speed, speed))

class HysteresisFanCurve(FanCurve):
    """Fan curve with hysteresis to prevent oscillation"""
    
    def __init__(self, base_curve: FanCurve,
                 hysteresis: float = 2.0,
                 min_hold_time: float = 30.0):
        """Initialize hysteresis fan curve
        
        Args:
            base_curve: Base fan curve implementation
            hysteresis: Temperature change required to update speed
            min_hold_time: Minimum seconds between speed changes
        """
        self.base_curve = base_curve
        self.hysteresis = hysteresis
        self.min_hold_time = min_hold_time
        
        # State tracking
        self._last_temp: Optional[float] = None
        self._last_speed: Optional[int] = None
        self._last_update: Optional[float] = None

    def get_speed(self, temp_delta: float) -> int:
        """Get fan speed percentage with hysteresis
        
        Only changes speed if temperature has changed more than
        hysteresis amount and minimum hold time has elapsed.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        import time
        current_time = time.time()
        
        # Handle first reading
        if self._last_temp is None:
            self._last_temp = temp_delta
            self._last_speed = self.base_curve.get_speed(temp_delta)
            self._last_update = current_time
            return self._last_speed
            
        # Check if we should update
        temp_change = abs(temp_delta - self._last_temp)
        time_elapsed = (current_time - self._last_update) if self._last_update else 0
        
        if (temp_change >= self.hysteresis and 
            time_elapsed >= self.min_hold_time):
            # Get new speed from base curve
            new_speed = self.base_curve.get_speed(temp_delta)
            
            # Only update if speed would actually change
            if new_speed != self._last_speed:
                self._last_temp = temp_delta
                self._last_speed = new_speed
                self._last_update = current_time
                logger.debug(f"Updated fan speed to {new_speed}% "
                           f"(Δt={temp_change:.1f}°C)")
                
        return self._last_speed
