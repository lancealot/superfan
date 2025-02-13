"""Fan curve implementations."""

from typing import List, Tuple, Dict, Any
import bisect
import logging

logger = logging.getLogger(__name__)

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


class LinearCurve(FanCurve):
    """Linear interpolation between temperature/speed points."""
    
    def __init__(self, points: List[Tuple[float, float]], min_speed: float = 0, max_speed: float = 100):
        """Initialize with temperature/speed points.
        
        Args:
            points: List of (temp_delta, speed) tuples
            min_speed: Minimum fan speed percentage (0-100)
            max_speed: Maximum fan speed percentage (0-100)
        """
        if not points:
            raise ValueError("Must provide at least one point")
            
        # Validate speed limits
        if not 0 <= min_speed <= 100:
            raise ValueError(f"Invalid min_speed {min_speed}%, must be 0-100")
        if not 0 <= max_speed <= 100:
            raise ValueError(f"Invalid max_speed {max_speed}%, must be 0-100")
        if min_speed > max_speed:
            raise ValueError(f"min_speed ({min_speed}%) cannot be greater than max_speed ({max_speed}%)")
            
        self.min_speed = min_speed
        self.max_speed = max_speed
        
        # Sort points by temperature
        self.points = sorted(points)
        
        # Validate points
        temps = set()
        for temp, speed in self.points:
            if temp in temps:
                raise ValueError(f"Duplicate temperature {temp}°C")
            temps.add(temp)
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
            return max(self.min_speed, min(self.max_speed, self.points[0][1]))
            
        # Handle temperature above last point
        if temp_delta >= self.points[-1][0]:
            return max(self.min_speed, min(self.max_speed, self.points[-1][1]))
            
        # Find surrounding points for interpolation
        idx = bisect.bisect_right([p[0] for p in self.points], temp_delta)
        p1 = self.points[idx-1]
        p2 = self.points[idx]
        
        # Linear interpolation
        t1, s1 = p1
        t2, s2 = p2
        ratio = (temp_delta - t1) / (t2 - t1)
        speed = s1 + ratio * (s2 - s1)
        return max(self.min_speed, min(self.max_speed, speed))


class StepCurve(FanCurve):
    """Step function between temperature/speed points."""
    
    def __init__(self, steps: List[Tuple[float, float]], min_speed: float = 0, max_speed: float = 100):
        """Initialize with temperature/speed points.
        
        Args:
            steps: List of (temp_delta, speed) tuples defining step thresholds
            min_speed: Minimum fan speed percentage (0-100)
            max_speed: Maximum fan speed percentage (0-100)
        """
        if not steps:
            raise ValueError("Must provide at least one step")
            
        # Validate speed limits
        if not 0 <= min_speed <= 100:
            raise ValueError(f"Invalid min_speed {min_speed}%, must be 0-100")
        if not 0 <= max_speed <= 100:
            raise ValueError(f"Invalid max_speed {max_speed}%, must be 0-100")
        if min_speed > max_speed:
            raise ValueError(f"min_speed ({min_speed}%) cannot be greater than max_speed ({max_speed}%)")
            
        self.min_speed = min_speed
        self.max_speed = max_speed
        
        # Sort points by temperature
        self.points = sorted(steps)
        
        # Validate points
        temps = set()
        for temp, speed in self.points:
            if temp in temps:
                raise ValueError(f"Duplicate temperature {temp}°C")
            temps.add(temp)
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
            return max(self.min_speed, min(self.max_speed, self.points[0][1]))
        return max(self.min_speed, min(self.max_speed, self.points[idx-1][1]))


class StableSpeedCurve(FanCurve):
    """Fan curve using discovered stable speed points.
    
    This curve implementation uses the stable speed points discovered during testing
    for the H12 board. It ensures fan speeds are set to known stable points and handles:
    - Different fan groups (high/low RPM, CPU)
    - Hex value formatting requirements
    - RPM range validation
    """
    
    # Known stable speed points for H12 board
    STABLE_POINTS = [
        {
            'temp': 40,     # High temperature threshold
            'speed': 100,   # Full speed
            'hex': 'ff',    # 0xFF step (100%)
            'prefix': False,
            'rpm_ranges': {
                'high_rpm': {'min': 980, 'max': 1820},   # FAN1, FAN5
                'low_rpm': {'min': 700, 'max': 1400},    # FAN2-4
                'cpu': {'min': 2520, 'max': 3640}        # FANA
            }
        },
        {
            'temp': 35,
            'speed': 75,
            'hex': '60',    # 0x60 step (37.5%)
            'prefix': False,
            'rpm_ranges': {
                'high_rpm': {'min': 980, 'max': 1820},
                'low_rpm': {'min': 700, 'max': 1400},
                'cpu': {'min': 2520, 'max': 3640}
            }
        },
        {
            'temp': 30,
            'speed': 50,
            'hex': '32',    # 0x32 step (20%)
            'prefix': False,
            'rpm_ranges': {
                'high_rpm': {'min': 980, 'max': 1820},
                'low_rpm': {'min': 700, 'max': 1400},
                'cpu': {'min': 2520, 'max': 3640}
            }
        }
    ]
    
    # Fan group RPM ranges
    FAN_RANGES = {
        'high_rpm': {  # FAN1, FAN5
            'min': 980,    # Minimum safe RPM
            'max': 1820,   # Maximum observed RPM
            'stable': 980  # Most stable operating point
        },
        'low_rpm': {   # FAN2-4
            'min': 700,    # Minimum safe RPM
            'max': 1400,   # Maximum observed RPM
            'stable': 700  # Most stable operating point
        },
        'cpu': {       # FANA
            'min': 2520,   # Minimum safe RPM
            'max': 3640,   # Maximum observed RPM
            'stable': 2520 # Most stable operating point
        }
    }
    
    def __init__(self, min_speed: float = 50, max_speed: float = 100):
        """Initialize with speed limits.
        
        Args:
            min_speed: Minimum fan speed percentage (50-100)
            max_speed: Maximum fan speed percentage (50-100)
        """
        # Validate speed limits
        if not 50 <= min_speed <= 100:  # Enforce minimum 50% for safety
            raise ValueError(f"Invalid min_speed {min_speed}%, must be 50-100")
        if not 50 <= max_speed <= 100:
            raise ValueError(f"Invalid max_speed {max_speed}%, must be 50-100")
        if min_speed > max_speed:
            raise ValueError(f"min_speed ({min_speed}%) cannot be greater than max_speed ({max_speed}%)")
            
        self.min_speed = min_speed
        self.max_speed = max_speed
        
        # Filter points within speed range
        self.points = [p for p in self.STABLE_POINTS 
                      if min_speed <= p['speed'] <= max_speed]
        if not self.points:
            raise ValueError(f"No stable points available between {min_speed}% and {max_speed}%")
    
    def get_speed(self, temp_delta: float) -> Dict[str, Any]:
        """Get stable fan speed point for temperature delta.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Dictionary containing:
            - speed: Fan speed percentage (50-100)
            - hex_speed: Hex value for IPMI command
            - needs_prefix: Whether hex value needs 0x prefix
            - expected_rpms: Expected RPM ranges per fan group
        """
        if not self.points:
            # Fail safe to highest stable point
            point = self.STABLE_POINTS[0]
        else:
            # Find first point with temperature >= delta
            for point in self.points:
                if temp_delta <= point['temp']:
                    break
            else:
                # Temperature above all points, use highest
                point = self.points[0]
        
        # Use predefined RPM ranges for each speed point
        return {
            'speed': point['speed'],
            'hex_speed': point['hex'],
            'needs_prefix': point['prefix'],
            'expected_rpms': point['rpm_ranges']
        }


class HysteresisCurve(FanCurve):
    """Adds hysteresis to another curve type."""
    
    def __init__(self, base_curve: FanCurve, hysteresis: float = 3.0, min_hold_time: float = 0.0):
        """Initialize with base curve and hysteresis.
        
        Args:
            base_curve: Base curve implementation
            hysteresis: Temperature change required to update speed
            min_hold_time: Minimum time in seconds to hold a speed before changing
        """
        self.curve = base_curve
        self.hysteresis = abs(hysteresis)
        self._last_temp = None
        self._last_speed = None
    
    def get_speed(self, temp_delta: float) -> Dict[str, Any]:
        """Get fan speed with hysteresis.
        
        Only changes speed if temperature has changed by more than
        the hysteresis amount.
        
        Args:
            temp_delta: Temperature above target in Celsius
            
        Returns:
            Dictionary with speed information from base curve
        """
        if self._last_temp is None:
            # First reading
            speed_info = self.curve.get_speed(temp_delta)
            self._last_temp = temp_delta
            self._last_speed = speed_info
            return speed_info
            
        if abs(temp_delta - self._last_temp) >= self.hysteresis:
            # Temperature changed enough to update
            speed_info = self.curve.get_speed(temp_delta)
            self._last_temp = temp_delta
            self._last_speed = speed_info
            return speed_info
            
        # Not enough change, maintain previous speed
        return self._last_speed
