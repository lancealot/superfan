"""
Temperature Sensor Management Module

This module handles temperature sensor monitoring and data processing
for Supermicro servers using IPMI and NVMe devices.
"""

import logging
import time
import re
import subprocess
from typing import Dict, List, Optional, Set, Pattern, Tuple
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

class NVMETemperatureReader:
    """Manages NVMe drive temperature monitoring"""
    
    def __init__(self, reading_timeout: float = 30.0, min_readings: int = 2):
        """Initialize NVMe temperature reader
        
        Args:
            reading_timeout: Maximum age in seconds for valid readings
            min_readings: Minimum number of valid readings required
        """
        self.reading_timeout = reading_timeout
        self.min_readings = min_readings
        self._readings: Dict[str, List[SensorReading]] = {}
        self.drives: List[str] = []
        self._discover_nvme_drives()
        
    def _discover_nvme_drives(self) -> None:
        """Discover available NVMe drives"""
        try:
            result = subprocess.run(
                ["sudo", "nvme", "list"],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Parse nvme list output to get drive paths
            drives = []
            for line in result.stdout.splitlines():
                if line.startswith('/dev/nvme'):
                    drive = line.split()[0]
                    drives.append(drive)
                    
            logger.info(f"Discovered NVMe drives: {', '.join(drives)}")
            self.drives = drives
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to discover NVMe drives: {e}")
            self.drives = []
    
    def update_readings(self) -> None:
        """Update temperature readings for all NVMe drives"""
        current_time = time.time()
        
        for drive in self.drives:
            try:
                result = subprocess.run(
                    ["sudo", "nvme", "smart-log", drive],
                    capture_output=True,
                    text=True,
                    check=True
                )
                
                # Parse temperature from smart-log output
                for line in result.stdout.splitlines():
                    if 'temperature' in line.lower():
                        # Handle different temperature output formats
                        temp_str = line.split(':')[1].strip()
                        # Extract temperature value before Kelvin
                        if '(' in temp_str:
                            temp_str = temp_str.split('(')[0]
                        # Remove any unit indicators and spaces
                        temp_str = temp_str.replace('°', '').replace('C', '').replace(' ', '')
                        try:
                            temp = float(temp_str)
                            sensor_name = f"NVMe_{drive.split('/')[-1]}"
                            
                            reading = SensorReading(
                                name=sensor_name,
                                value=temp,
                                timestamp=current_time,
                                state='ok'
                            )
                            
                            if sensor_name not in self._readings:
                                self._readings[sensor_name] = []
                                
                            self._readings[sensor_name].append(reading)
                            
                            # Remove old readings
                            self._readings[sensor_name] = [
                                r for r in self._readings[sensor_name]
                                if r.age <= self.reading_timeout
                            ]
                            break
                        except ValueError:
                            logger.warning(f"Failed to parse temperature value: {temp_str}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to get temperature for {drive}: {e}")
    
    def get_sensor_stats(self, sensor_name: str) -> Optional[Dict[str, float]]:
        """Get statistics for a specific NVMe drive
        
        Args:
            sensor_name: Name of the sensor (NVMe_nvme[X]n1)
            
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
        
        if len(values) > 1:
            stats["stdev"] = stdev(values)
            
        return stats
        
    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all NVMe drives
        
        Returns:
            Dictionary mapping drive names to their statistics
        """
        stats = {}
        for sensor_name in self._readings.keys():
            sensor_stats = self.get_sensor_stats(sensor_name)
            if sensor_stats:
                stats[sensor_name] = sensor_stats
        return stats
        
    def get_sensor_names(self) -> Set[str]:
        """Get the set of NVMe sensor names
        
        Returns:
            Set of sensor names (NVMe_nvme[X]n1)
        """
        return set(self._readings.keys())

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
            # Filter for temperature sensors first
            temp_sensors = [r for r in readings if "Temp" in r["name"]]
            logger.debug(f"Found temperature sensors: {[r['name'] for r in temp_sensors]}")
            
            # If no patterns specified, include all temperature sensors
            if not self.sensor_patterns:
                self.sensor_names = {r["name"] for r in temp_sensors}
            else:
                # Match temperature sensors against patterns
                self.sensor_names = set()
                logger.debug(f"Matching sensors against patterns: {[p.pattern for p in self.sensor_patterns]}")
                for reading in temp_sensors:
                    name = reading["name"]
                    logger.debug(f"Checking sensor: {name}")
                    for pattern in self.sensor_patterns:
                        if pattern.match(name):  # Use match to ensure pattern matches from start
                            logger.debug(f"Sensor {name} matched pattern {pattern.pattern}")
                            self.sensor_names.add(name)
                            break
                        else:
                            logger.debug(f"Sensor {name} did not match pattern {pattern.pattern}")
            logger.info(f"Discovered sensors: {', '.join(self.sensor_names)}")
        except IPMIError as e:
            logger.error(f"Failed to discover sensors: {e}")
            raise

    def update_readings(self) -> None:
        """Update temperature readings for all monitored sensors"""
        try:
            current_time = time.time()
            readings = self.commander.get_sensor_readings()
            
            # Log all raw readings for debugging
            for reading in readings:
                logger.debug(f"Raw reading: {reading['name']} = {reading.get('value')}°C (state: {reading.get('state', 'unknown')})")
            
            # Track critical sensors for immediate notification
            critical_sensors = []
            
            # Process each reading
            for reading in readings:
                name = reading["name"]
                # Log sensor matching
                if name in self.sensor_names:
                    logger.debug(f"Processing sensor {name} (in sensor_names)")
                    value = reading.get("value")
                    state = reading.get("state", "ns")
                    logger.debug(f"Sensor {name} value: {value}°C, state: {state}")
                    
                    sensor_reading = SensorReading(
                        name=name,
                        value=value,
                        timestamp=current_time,
                        state=state,
                        response_id=reading.get("response_id")
                    )
                    
                    # Log validity
                    logger.debug(f"Sensor {name} reading valid: {sensor_reading.is_valid}")
                    
                    # Check for critical state
                    if sensor_reading.is_critical:
                        critical_sensors.append(f"{name}: {sensor_reading.value}°C")
                        logger.debug(f"Sensor {name} is in critical state")
                    
                    # Initialize list if needed
                    if name not in self._readings:
                        self._readings[name] = []
                        logger.debug(f"Initialized readings list for sensor {name}")
                    
                    # Add new reading
                    self._readings[name].append(sensor_reading)
                    logger.debug(f"Added new reading for sensor {name}")
                    
                    # Remove old readings
                    old_count = len(self._readings[name])
                    self._readings[name] = [
                        r for r in self._readings[name]
                        if r.age <= self.reading_timeout
                    ]
                    new_count = len(self._readings[name])
                    if old_count != new_count:
                        logger.debug(f"Removed {old_count - new_count} old readings for sensor {name}")
                else:
                    logger.debug(f"Skipping sensor {name} (not in sensor_names)")
                    continue
            
            # Log critical sensors immediately
            if critical_sensors:
                logger.error(f"Critical temperature alerts: {', '.join(critical_sensors)}")

            logger.debug(f"Updated {len(readings)} sensor readings")

            # Validate response IDs if any readings have them
            response_ids = set()
            for readings_list in self._readings.values():
                if readings_list:  # Check if there are any readings
                    for reading in readings_list:
                        if reading.response_id is not None:
                            response_ids.add(reading.response_id)
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
            logger.debug(f"No readings found for sensor {sensor_name}")
            return None
            
        readings = self._readings[sensor_name]
        logger.debug(f"Found {len(readings)} total readings for sensor {sensor_name}")
        
        # Filter out both old readings and invalid readings (no reading/ns)
        valid_readings = [r for r in readings if r.age <= self.reading_timeout and r.is_valid]
        logger.debug(f"Found {len(valid_readings)} valid readings for sensor {sensor_name}")
        
        if len(valid_readings) < self.min_readings:
            logger.debug(f"Insufficient valid readings for sensor {sensor_name}: {len(valid_readings)} < {self.min_readings}")
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

class CombinedTemperatureReader:
    """Combines IPMI and NVMe temperature monitoring"""
    
    def __init__(self, commander: IPMICommander,
                 sensor_patterns: Optional[List[str]] = None,
                 reading_timeout: float = 30.0,
                 min_readings: int = 2):
        """Initialize combined temperature reader
        
        Args:
            commander: IPMICommander instance for IPMI communication
            sensor_patterns: List of sensor name patterns to monitor
            reading_timeout: Maximum age in seconds for valid readings
            min_readings: Minimum number of valid readings required
        """
        self.ipmi_reader = SensorReader(
            commander,
            sensor_patterns,
            reading_timeout,
            min_readings
        )
        self.nvme_reader = NVMETemperatureReader(
            reading_timeout,
            min_readings
        )
        
    def update_readings(self) -> None:
        """Update temperature readings from all sources"""
        self.ipmi_reader.update_readings()
        self.nvme_reader.update_readings()
        
    def get_sensor_stats(self, sensor_name: str) -> Optional[Dict[str, float]]:
        """Get statistics for a specific sensor
        
        Args:
            sensor_name: Name of the sensor
            
        Returns:
            Dictionary with current, min, max, and average values,
            or None if insufficient valid readings
        """
        if sensor_name.startswith("NVMe_"):
            return self.nvme_reader.get_sensor_stats(sensor_name)
        return self.ipmi_reader.get_sensor_stats(sensor_name)
        
    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all sensors
        
        Returns:
            Dictionary mapping sensor names to their statistics
        """
        stats = {}
        stats.update(self.ipmi_reader.get_all_stats())
        stats.update(self.nvme_reader.get_all_stats())
        return stats
        
    def get_sensor_names(self) -> Set[str]:
        """Get the set of all monitored sensor names
        
        Returns:
            Set of sensor names
        """
        names = self.ipmi_reader.get_sensor_names()
        names.update(self.nvme_reader.get_sensor_names())
        return names
        
    def get_highest_temperature(self) -> Optional[float]:
        """Get the highest current temperature across all sensors
        
        Returns:
            Highest temperature value, or None if no valid readings
        """
        all_stats = self.get_all_stats()
        if not all_stats:
            return None
        return max(stats["current"] for stats in all_stats.values())
        
    def get_average_temperature(self) -> Optional[float]:
        """Get the average temperature across all sensors
        
        Returns:
            Average temperature value, or None if no valid readings
        """
        all_stats = self.get_all_stats()
        if not all_stats:
            return None
        return mean(stats["current"] for stats in all_stats.values())
