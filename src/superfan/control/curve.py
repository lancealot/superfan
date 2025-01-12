"""Fan curve implementations."""

from typing import List, Tuple
import bisect

class FanCurve:
    """Base class for fan speed curves."""
    
    def get_speed(self, temp_delta: float) -> float:
        """Get fan speed percentage for temperature delta.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        raise NotImplementedError


class LinearFanCurve(FanCurve):
    """Linear interpolation between temperature/speed points."""
    
    def __init__(self, points: List[Tuple[float, float]]):
        """Initialize with temperature/speed points.
        
        Args:
            points: List of (temp_delta, speed) tuples
        """
        # Sort points by temperature
        self.points = sorted(points)
        
        # Validate points
        for temp, speed in self.points:
            if not 0 <= speed <= 100:
                raise ValueError(f"Invalid speed {speed}%, must be 0-100")
            if temp < 0:
                raise ValueError(f"Invalid temp delta {temp}°C, must be >= 0")
    
    def get_speed(self, temp_delta: float) -> float:
        """Get interpolated fan speed for temperature delta.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        if not self.points:
            return 100  # Fail safe to full speed
            
        # Handle temperature below first point
        if temp_delta <= self.points[0][0]:
            return self.points[0][1]
            
        # Handle temperature above last point
        if temp_delta >= self.points[-1][0]:
            return self.points[-1][1]
            
        # Find surrounding points for interpolation
        idx = bisect.bisect_right([p[0] for p in self.points], temp_delta)
        p1 = self.points[idx-1]
        p2 = self.points[idx]
        
        # Linear interpolation
        t1, s1 = p1
        t2, s2 = p2
        ratio = (temp_delta - t1) / (t2 - t1)
        return s1 + ratio * (s2 - s1)


class StepFanCurve(FanCurve):
    """Step function between temperature/speed points."""
    
    def __init__(self, points: List[Tuple[float, float]]):
        """Initialize with temperature/speed points.
        
        Args:
            points: List of (temp_delta, speed) tuples
        """
        # Sort points by temperature
        self.points = sorted(points)
        
        # Validate points
        for temp, speed in self.points:
            if not 0 <= speed <= 100:
                raise ValueError(f"Invalid speed {speed}%, must be 0-100")
            if temp < 0:
                raise ValueError(f"Invalid temp delta {temp}°C, must be >= 0")
    
    def get_speed(self, temp_delta: float) -> float:
        """Get stepped fan speed for temperature delta.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        if not self.points:
            return 100  # Fail safe to full speed
            
        # Find first point with temperature >= delta
        idx = bisect.bisect_right([p[0] for p in self.points], temp_delta)
        if idx == 0:
            return self.points[0][1]
        return self.points[idx-1][1]


class HysteresisFanCurve(FanCurve):
    """Adds hysteresis to another curve type."""
    
    def __init__(self, curve: FanCurve, hysteresis: float = 3.0):
        """Initialize with base curve and hysteresis.
        
        Args:
            curve: Base curve implementation
            hysteresis: Temperature change required to update speed
        """
        self.curve = curve
        self.hysteresis = abs(hysteresis)
        self._last_temp = None
        self._last_speed = None
    
    def get_speed(self, temp_delta: float) -> float:
        """Get fan speed with hysteresis.
        
        Only changes speed if temperature has changed by more than
        the hysteresis amount.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Fan speed percentage (0-100)
        """
        if self._last_temp is None:
            # First reading
            speed = self.curve.get_speed(temp_delta)
            self._last_temp = temp_delta
            self._last_speed = speed
            return speed
            
        if abs(temp_delta - self._last_temp) >= self.hysteresis:
            # Temperature changed enough to update
            speed = self.curve.get_speed(temp_delta)
            self._last_temp = temp_delta
            self._last_speed = speed
            return speed
            
        # Not enough change, maintain previous speed
        return self._last_speed
