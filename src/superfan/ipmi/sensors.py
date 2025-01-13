"""
Temperature Sensor Management Module

This module handles temperature sensor monitoring and data processing
for Supermicro servers using IPMI.
"""

import logging
import time
import re
from typing import Dict, List, Optional, Set, Pattern
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
    state: str  # 'ok', 'cr' (critical), or 'ns' (no reading)
    response_id: Optional[int] = None  # Track IPMI response ID

    @property
    def age(self) -> float:
        """Get age of reading in seconds"""
        return time.time() - self.timestamp

    @property
    def is_critical(self) -> bool:
        """Check if sensor is in critical state"""
        return self.state == 'cr'

    @property
    def is_valid(self) -> bool:
        """Check if sensor reading is valid"""
        return self.state != 'ns' and self.value is not None

class SensorReader:
    """Manages temperature sensor monitoring and data processing"""

    def __init__(self, commander: IPMICommander, 
                 sensor_patterns: Optional[List[str]] = None,
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
        # Convert patterns to regex objects
        self.sensor_patterns = []
        if sensor_patterns:
            for pattern in sensor_patterns:
                # Convert glob-style patterns to regex
                regex = pattern.replace("*", ".*").replace("?", ".")
                self.sensor_patterns.append(re.compile(regex, re.IGNORECASE))
        
        self.sensor_names: Set[str] = set()
        self.reading_timeout = reading_timeout
        self.min_readings = min_readings
        
        # Store recent readings
        self._readings: Dict[str, List[SensorReading]] = {}
        
        # Discover available sensors if none specified
        if not self.sensor_names:
            self._discover_sensors()

    def _discover_sensors(self) -> None:
        """Discover available temperature sensors matching patterns"""
        try:
            readings = self.commander.get_sensor_readings()
            # If no patterns specified, include all sensors
            if not self.sensor_patterns:
                self.sensor_names = {r["name"] for r in readings}
            else:
                # Match sensors against patterns
                self.sensor_names = set()
                for reading in readings:
                    name = reading["name"]
                    for pattern in self.sensor_patterns:
                        if pattern.match(name):
                            self.sensor_names.add(name)
                            break
            logger.info(f"Discovered sensors: {', '.join(self.sensor_names)}")
        except IPMIError as e:
            logger.error(f"Failed to discover sensors: {e}")
            raise

    def update_readings(self) -> None:
        """Update temperature readings for all monitored sensors"""
        try:
            current_time = time.time()
            readings = self.commander.get_sensor_readings()
            
            # Track critical sensors for immediate notification
            critical_sensors = []
            
            # Process each reading
            for reading in readings:
                name = reading["name"]
                if name in self.sensor_names:
                    sensor_reading = SensorReading(
                        name=name,
                        value=reading.get("value"),
                        timestamp=current_time,
                        state=reading.get("state", "ns"),
                        response_id=reading.get("response_id")
                    )

                    # Check for critical state
                    if sensor_reading.is_critical:
                        critical_sensors.append(f"{name}: {sensor_reading.value}°C")
                    
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
            
            # Log critical sensors immediately
            if critical_sensors:
                logger.error(f"Critical temperature alerts: {', '.join(critical_sensors)}")

            logger.debug(f"Updated {len(readings)} sensor readings")

            # Validate response IDs
            response_ids = {r.response_id for r in self._readings.values() if r.response_id is not None}
            if len(response_ids) > 1:
                logger.warning(f"Inconsistent IPMI response IDs detected: {response_ids}")
            
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
        # Filter out both old readings and invalid readings (no reading/ns)
        valid_readings = [r for r in readings if r.age <= self.reading_timeout and r.is_valid]
        
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
