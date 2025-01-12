"""
Temperature Sensor Management Module

This module handles temperature sensor monitoring and data processing
for Supermicro servers using IPMI.
"""

import logging
import time
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from statistics import mean, stdev
from .commander import IPMICommander, IPMIError

logger = logging.getLogger(__name__)

@dataclass
class SensorReading:
    """Represents a temperature sensor reading"""
    name: str
    value: float
    timestamp: float

    @property
    def age(self) -> float:
        """Get age of reading in seconds"""
        return time.time() - self.timestamp

class SensorManager:
    """Manages temperature sensor monitoring and data processing"""

    def __init__(self, commander: IPMICommander, 
                 sensor_names: Optional[List[str]] = None,
                 reading_timeout: float = 30.0,
                 min_readings: int = 2):
        """Initialize sensor manager

        Args:
            commander: IPMICommander instance for IPMI communication
            sensor_names: List of sensor names to monitor (None for all temperature sensors)
            reading_timeout: Maximum age in seconds for valid readings
            min_readings: Minimum number of valid readings required
        """
        self.commander = commander
        self.sensor_names = set(sensor_names) if sensor_names else set()
        self.reading_timeout = reading_timeout
        self.min_readings = min_readings
        
        # Store recent readings
        self._readings: Dict[str, List[SensorReading]] = {}
        
        # Discover available sensors if none specified
        if not self.sensor_names:
            self._discover_sensors()

    def _discover_sensors(self) -> None:
        """Discover available temperature sensors"""
        try:
            readings = self.commander.get_sensor_readings()
            self.sensor_names = {r["name"] for r in readings}
            logger.info(f"Discovered sensors: {', '.join(self.sensor_names)}")
        except IPMIError as e:
            logger.error(f"Failed to discover sensors: {e}")
            raise

    def update_readings(self) -> None:
        """Update temperature readings for all monitored sensors"""
        try:
            current_time = time.time()
            readings = self.commander.get_sensor_readings()
            
            # Process each reading
            for reading in readings:
                name = reading["name"]
                if name in self.sensor_names:
                    sensor_reading = SensorReading(
                        name=name,
                        value=reading["value"],
                        timestamp=current_time
                    )
                    
                    # Initialize list if needed
                    if name not in self._readings:
                        self._readings[name] = []
                    
                    # Add new reading
                    self._readings[name].append(sensor_reading)
                    
                    # Remove old readings
                    self._readings[name] = [
                        r for r in self._readings[name]
                        if r.age <= self.reading_timeout
                    ]
            
            logger.debug(f"Updated {len(readings)} sensor readings")
            
        except IPMIError as e:
            logger.error(f"Failed to update sensor readings: {e}")
            raise

    def get_sensor_stats(self, sensor_name: str) -> Optional[Dict[str, float]]:
        """Get statistics for a specific sensor

        Args:
            sensor_name: Name of the sensor

        Returns:
            Dictionary with current, min, max, and average values,
            or None if insufficient valid readings
        """
        if sensor_name not in self._readings:
            return None
            
        readings = self._readings[sensor_name]
        valid_readings = [r for r in readings if r.age <= self.reading_timeout]
        
        if len(valid_readings) < self.min_readings:
            return None
            
        values = [r.value for r in valid_readings]
        
        stats = {
            "current": valid_readings[-1].value,
            "min": min(values),
            "max": max(values),
            "avg": mean(values)
        }
        
        # Add standard deviation if we have enough readings
        if len(values) > 1:
            stats["stdev"] = stdev(values)
            
        return stats

    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all monitored sensors

        Returns:
            Dictionary mapping sensor names to their statistics
        """
        stats = {}
        for sensor_name in self.sensor_names:
            sensor_stats = self.get_sensor_stats(sensor_name)
            if sensor_stats:
                stats[sensor_name] = sensor_stats
        return stats

    def get_highest_temperature(self) -> Optional[float]:
        """Get the highest current temperature across all sensors

        Returns:
            Highest temperature value, or None if no valid readings
        """
        current_temps = []
        
        for sensor_name in self.sensor_names:
            stats = self.get_sensor_stats(sensor_name)
            if stats:
                current_temps.append(stats["current"])
                
        return max(current_temps) if current_temps else None

    def get_average_temperature(self) -> Optional[float]:
        """Get the average temperature across all sensors

        Returns:
            Average temperature value, or None if no valid readings
        """
        current_temps = []
        
        for sensor_name in self.sensor_names:
            stats = self.get_sensor_stats(sensor_name)
            if stats:
                current_temps.append(stats["current"])
                
        return mean(current_temps) if current_temps else None

    def get_sensor_names(self) -> Set[str]:
        """Get the set of monitored sensor names

        Returns:
            Set of sensor names
        """
        return self.sensor_names.copy()
