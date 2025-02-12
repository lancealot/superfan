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
    """Represents a temperature or fan speed sensor reading.

    This class encapsulates a sensor reading with metadata including:
    - Sensor name and value
    - Timestamp for age tracking
    - State information (ok, critical, no reading)
    - IPMI response ID for communication validation

    Attributes:
        name: Sensor identifier (e.g., "CPU1 Temp", "FAN1")
        value: Sensor reading value (temperature in °C or fan speed in RPM)
        timestamp: Unix timestamp when reading was taken
        state: Sensor state ("ok", "cr" for critical, "ns" for no reading)
        response_id: Optional IPMI response ID for tracking communication

    Examples:
        >>> reading = SensorReading("CPU1 Temp", 45.0, time.time(), "ok")
        >>> print(f"{reading.name}: {reading.value}°C")
        CPU1 Temp: 45.0°C
        >>> if reading.is_critical:
        ...     print("Temperature critical!")
    """
    name: str
    value: float
    timestamp: float
    state: str  # 'ok', 'cr' (critical), or 'ns' (no reading)
    response_id: Optional[int] = None  # Track IPMI response ID

    @property
    def age(self) -> float:
        """Get age of reading in seconds.

        Returns:
            float: Time elapsed since reading was taken
        """
        return time.time() - self.timestamp

    @property
    def is_critical(self) -> bool:
        """Check if sensor is in critical state.

        Returns:
            bool: True if state is "cr" (critical)
        """
        return self.state == 'cr'

    @property
    def is_valid(self) -> bool:
        """Check if sensor reading is valid.

        A reading is considered valid if:
        1. State is not "ns" (no reading)
        2. Value is not None

        Returns:
            bool: True if reading is valid
        """
        return self.state != 'ns' and self.value is not None

class NVMETemperatureReader:
    """Manages NVMe drive temperature monitoring.

    This class handles:
    1. NVMe drive discovery using nvme-cli
    2. Temperature reading via smart-log
    3. Reading history and statistics
    4. Timeout and validation

    The reader maintains a history of readings for each drive to:
    - Calculate statistics (min, max, avg)
    - Track reading validity
    - Handle timeouts
    - Detect trends

    Note:
        Requires nvme-cli package and sudo access for NVMe commands
    """
    
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
        """Discover available NVMe drives using nvme-cli.

        This method uses the 'nvme list' command to find all NVMe drives in the system.
        Drive paths are stored in self.drives for temperature monitoring.

        Note:
            - Requires sudo access for nvme-cli commands
            - Silently handles errors by setting empty drive list
            - Logs discovery results at INFO level

        Example drive paths:
            - /dev/nvme0n1
            - /dev/nvme1n1
        """
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
        """Update temperature readings for all NVMe drives.

        For each discovered drive:
        1. Retrieves smart-log data using nvme-cli
        2. Parses temperature value from output
        3. Creates new SensorReading with current timestamp
        4. Adds to reading history
        5. Removes readings older than timeout

        Note:
            - Handles various temperature output formats
            - Skips drives with parsing errors
            - Maintains reading history for statistics
            - Logs errors at WARNING level

        Example smart-log output:
            Smart Log for NVME device:nvme0n1 namespace-id:ffffffff
            temperature                         : 38 C
            available_spare                     : 100%
        """
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
        """Get statistics for a specific NVMe drive.

        Calculates statistics from valid readings within timeout period:
        - current: Most recent temperature
        - min: Lowest temperature
        - max: Highest temperature
        - avg: Average temperature
        - stdev: Standard deviation (if >1 reading)

        Args:
            sensor_name: Drive sensor name (e.g., "NVMe_nvme0n1")

        Returns:
            Dict[str, float]: Statistics dictionary with keys:
                - "current": Latest temperature (°C)
                - "min": Minimum temperature (°C)
                - "max": Maximum temperature (°C)
                - "avg": Average temperature (°C)
                - "stdev": Standard deviation (if >1 reading)
            None: If insufficient valid readings or sensor not found

        Example:
            >>> stats = reader.get_sensor_stats("NVMe_nvme0n1")
            >>> if stats:
            ...     print(f"Current: {stats['current']}°C")
            ...     print(f"Range: {stats['min']}-{stats['max']}°C")
            Current: 38.0°C
            Range: 35.0-42.0°C
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
        """Get statistics for all NVMe drives.

        Collects statistics for all drives with valid readings:
        - Skips drives with insufficient readings
        - Uses same format as get_sensor_stats()

        Returns:
            Dict[str, Dict[str, float]]: Dictionary mapping drive names to their statistics:
                {
                    "NVMe_nvme0n1": {
                        "current": 38.0,
                        "min": 35.0,
                        "max": 42.0,
                        "avg": 37.5,
                        "stdev": 1.2
                    },
                    ...
                }

        Example:
            >>> stats = reader.get_all_stats()
            >>> for drive, drive_stats in stats.items():
            ...     print(f"{drive}: {drive_stats['current']}°C")
            NVMe_nvme0n1: 38.0°C
            NVMe_nvme1n1: 42.0°C
        """
        stats = {}
        for sensor_name in self._readings.keys():
            sensor_stats = self.get_sensor_stats(sensor_name)
            if sensor_stats:
                stats[sensor_name] = sensor_stats
        return stats
        
    def get_sensor_names(self) -> Set[str]:
        """Get the set of NVMe sensor names.

        Returns a set of all discovered NVMe drive sensor names. Each name follows
        the format "NVMe_nvme[X]n1" where X is the drive number (0, 1, etc.).

        Returns:
            Set[str]: Set of sensor names in format "NVMe_nvme[X]n1"

        Example:
            >>> names = reader.get_sensor_names()
            >>> print(sorted(names))
            ['NVMe_nvme0n1', 'NVMe_nvme1n1']
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
        """Discover available temperature sensors matching patterns.

        This method:
        1. Gets all sensor readings from IPMI
        2. Filters for temperature sensors
        3. If no patterns specified, includes all temperature sensors
        4. Otherwise matches sensors against provided glob patterns
        5. Stores matched sensor names for monitoring

        Note:
            - Case-insensitive pattern matching
            - Supports glob patterns (*, ?)
            - Logs discovery process at DEBUG level
            - Logs final sensor list at INFO level

        Raises:
            IPMIError: If sensor discovery fails
        """
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
        """Update temperature readings for all monitored sensors.

        This method:
        1. Gets current sensor readings from IPMI
        2. Processes each reading for monitored sensors
        3. Creates SensorReading objects with timestamps
        4. Maintains reading history for each sensor
        5. Removes readings older than timeout
        6. Tracks and reports critical temperature alerts

        Note:
            - Logs raw readings at DEBUG level
            - Logs critical alerts at ERROR level
            - Validates IPMI response IDs
            - Handles sensor state transitions
            - Maintains reading history for statistics

        Raises:
            IPMIError: If reading update fails:
                - Connection errors
                - Command failures
                - Response validation errors
        """
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
        """Get statistics for a specific temperature sensor.

        Calculates statistics from valid readings within timeout period:
        - current: Most recent temperature
        - min: Lowest temperature
        - max: Highest temperature
        - avg: Average temperature
        - stdev: Standard deviation (if >1 reading)

        The method filters readings based on:
        1. Age (within reading_timeout)
        2. Validity (state != 'ns' and value is not None)
        3. Count (>= min_readings)

        Args:
            sensor_name: Name of the temperature sensor (e.g., "CPU1 Temp")

        Returns:
            Dict[str, float]: Statistics dictionary with keys:
                - "current": Latest temperature (°C)
                - "min": Minimum temperature (°C)
                - "max": Maximum temperature (°C)
                - "avg": Average temperature (°C)
                - "stdev": Standard deviation (if >1 reading)
            None: If insufficient valid readings or sensor not found

        Example:
            >>> stats = reader.get_sensor_stats("CPU1 Temp")
            >>> if stats:
            ...     print(f"Current: {stats['current']}°C")
            ...     print(f"Range: {stats['min']}-{stats['max']}°C")
            Current: 45.0°C
            Range: 42.0-48.0°C
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
        """Get statistics for all monitored temperature sensors.

        Collects statistics for all sensors with valid readings:
        - Skips sensors with insufficient readings
        - Uses same format as get_sensor_stats()
        - Includes only sensors in sensor_names set

        Returns:
            Dict[str, Dict[str, float]]: Dictionary mapping sensor names to their statistics:
                {
                    "CPU1 Temp": {
                        "current": 45.0,
                        "min": 42.0,
                        "max": 48.0,
                        "avg": 44.5,
                        "stdev": 1.2
                    },
                    "CPU2 Temp": {
                        "current": 47.0,
                        "min": 44.0,
                        "max": 49.0,
                        "avg": 46.5,
                        "stdev": 1.1
                    },
                    ...
                }

        Example:
            >>> stats = reader.get_all_stats()
            >>> for sensor, sensor_stats in stats.items():
            ...     print(f"{sensor}: {sensor_stats['current']}°C")
            CPU1 Temp: 45.0°C
            CPU2 Temp: 47.0°C
        """
        stats = {}
        for sensor_name in self.sensor_names:
            sensor_stats = self.get_sensor_stats(sensor_name)
            if sensor_stats:
                stats[sensor_name] = sensor_stats
        return stats

    def get_highest_temperature(self) -> Optional[float]:
        """Get the highest current temperature across all monitored sensors.

        This method:
        1. Gets statistics for each monitored sensor
        2. Extracts current temperature values
        3. Returns the maximum temperature found

        Note:
            - Only considers sensors with valid readings
            - Uses get_sensor_stats() for each sensor
            - Skips sensors with insufficient readings
            - Returns None if no valid readings found

        Returns:
            float: Highest current temperature in °C
            None: If no valid readings available

        Example:
            >>> reader = SensorReader(commander)
            >>> max_temp = reader.get_highest_temperature()
            >>> if max_temp is not None:
            ...     print(f"Highest temperature: {max_temp}°C")
            Highest temperature: 48.5°C
        """
        current_temps = []
        
        for sensor_name in self.sensor_names:
            stats = self.get_sensor_stats(sensor_name)
            if stats:
                current_temps.append(stats["current"])
                
        return max(current_temps) if current_temps else None

    def get_average_temperature(self) -> Optional[float]:
        """Get the average temperature across all monitored sensors.

        This method:
        1. Gets statistics for each monitored sensor
        2. Extracts current temperature values
        3. Returns the mean temperature

        Note:
            - Only considers sensors with valid readings
            - Uses get_sensor_stats() for each sensor
            - Skips sensors with insufficient readings
            - Returns None if no valid readings found

        Returns:
            float: Average current temperature in °C
            None: If no valid readings available

        Example:
            >>> reader = SensorReader(commander)
            >>> avg_temp = reader.get_average_temperature()
            >>> if avg_temp is not None:
            ...     print(f"Average temperature: {avg_temp:.1f}°C")
            Average temperature: 46.5°C
        """
        current_temps = []
        
        for sensor_name in self.sensor_names:
            stats = self.get_sensor_stats(sensor_name)
            if stats:
                current_temps.append(stats["current"])
                
        return mean(current_temps) if current_temps else None

    def get_sensor_names(self) -> Set[str]:
        """Get the set of monitored temperature sensor names.

        Returns a copy of the sensor names set to prevent modification.
        Only includes sensors that were discovered and matched any
        provided patterns during initialization.

        Returns:
            Set[str]: Set of monitored sensor names (e.g., "CPU1 Temp", "System Temp")

        Example:
            >>> reader = SensorReader(commander)
            >>> names = reader.get_sensor_names()
            >>> print(sorted(names))
            ['CPU1 Temp', 'CPU2 Temp', 'System Temp']
        """
        return self.sensor_names.copy()

class CombinedTemperatureReader:
    """Combines IPMI and NVMe temperature monitoring.

    This class provides a unified interface for monitoring temperatures from:
    1. IPMI sensors (CPU, System, etc.)
    2. NVMe drive smart-log data

    The reader maintains separate SensorReader instances for each source
    but provides combined access through common methods. This allows for:
    - Consistent statistics calculation
    - Unified temperature monitoring
    - Source-agnostic temperature thresholds
    - Combined temperature reporting

    Example:
        >>> reader = CombinedTemperatureReader(commander)
        >>> reader.update_readings()
        >>> stats = reader.get_all_stats()
        >>> for sensor, sensor_stats in stats.items():
        ...     print(f"{sensor}: {sensor_stats['current']}°C")
        CPU1 Temp: 45.0°C
        NVMe_nvme0n1: 38.0°C
    """
    
    def __init__(self, commander: IPMICommander,
                 sensor_patterns: Optional[List[str]] = None,
                 reading_timeout: float = 30.0,
                 min_readings: int = 2):
        """Initialize combined temperature reader.

        Creates separate readers for IPMI and NVMe temperature sources
        with shared configuration parameters.

        Args:
            commander: IPMICommander instance for IPMI communication
            sensor_patterns: List of sensor name patterns to monitor (e.g., ["CPU*", "System*"])
                           None to monitor all temperature sensors
            reading_timeout: Maximum age in seconds for valid readings (default: 30.0)
            min_readings: Minimum number of valid readings required for statistics (default: 2)

        Note:
            - IPMI sensors are filtered by patterns if provided
            - NVMe drives are always monitored if present
            - Both readers use the same timeout and min_readings values
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
        """Update temperature readings from all monitored sources.

        This method:
        1. Updates IPMI sensor readings via IPMICommander
        2. Updates NVMe drive readings via nvme-cli
        3. Maintains reading history for both sources
        4. Handles timeouts and validation

        Note:
            - Both readers operate independently
            - Errors in one reader don't affect the other
            - Each reader handles its own error logging
        """
        self.ipmi_reader.update_readings()
        self.nvme_reader.update_readings()
        
    def get_sensor_stats(self, sensor_name: str) -> Optional[Dict[str, float]]:
        """Get statistics for a specific temperature sensor.

        Routes the request to the appropriate reader based on sensor name:
        - NVMe sensors (prefixed with "NVMe_") -> NVMETemperatureReader
        - All other sensors -> SensorReader

        Args:
            sensor_name: Name of the sensor (e.g., "CPU1 Temp" or "NVMe_nvme0n1")

        Returns:
            Dict[str, float]: Statistics dictionary with keys:
                - "current": Latest temperature (°C)
                - "min": Minimum temperature (°C)
                - "max": Maximum temperature (°C)
                - "avg": Average temperature (°C)
                - "stdev": Standard deviation (if >1 reading)
            None: If insufficient valid readings or sensor not found

        Example:
            >>> reader = CombinedTemperatureReader(commander)
            >>> stats = reader.get_sensor_stats("CPU1 Temp")
            >>> if stats:
            ...     print(f"Current: {stats['current']}°C")
            Current: 45.0°C
            >>> nvme_stats = reader.get_sensor_stats("NVMe_nvme0n1")
            >>> if nvme_stats:
            ...     print(f"Current: {nvme_stats['current']}°C")
            Current: 38.0°C
        """
        if sensor_name.startswith("NVMe_"):
            return self.nvme_reader.get_sensor_stats(sensor_name)
        return self.ipmi_reader.get_sensor_stats(sensor_name)
        
    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all monitored temperature sensors.

        Combines statistics from both IPMI and NVMe sources:
        1. Gets statistics from IPMI sensors
        2. Gets statistics from NVMe drives
        3. Merges results into a single dictionary

        Returns:
            Dict[str, Dict[str, float]]: Dictionary mapping sensor names to their statistics:
                {
                    "CPU1 Temp": {
                        "current": 45.0,
                        "min": 42.0,
                        "max": 48.0,
                        "avg": 44.5,
                        "stdev": 1.2
                    },
                    "NVMe_nvme0n1": {
                        "current": 38.0,
                        "min": 35.0,
                        "max": 42.0,
                        "avg": 37.5,
                        "stdev": 1.1
                    },
                    ...
                }

        Example:
            >>> reader = CombinedTemperatureReader(commander)
            >>> stats = reader.get_all_stats()
            >>> for sensor, sensor_stats in sorted(stats.items()):
            ...     print(f"{sensor}: {sensor_stats['current']}°C")
            CPU1 Temp: 45.0°C
            NVMe_nvme0n1: 38.0°C
        """
        stats = {}
        stats.update(self.ipmi_reader.get_all_stats())
        stats.update(self.nvme_reader.get_all_stats())
        return stats
        
    def get_sensor_names(self) -> Set[str]:
        """Get the set of all monitored temperature sensor names.

        Combines sensor names from both IPMI and NVMe sources into a single set.
        IPMI sensors have standard names (e.g., "CPU1 Temp") while NVMe sensors
        are prefixed with "NVMe_" (e.g., "NVMe_nvme0n1").

        Returns:
            Set[str]: Combined set of all monitored sensor names

        Example:
            >>> reader = CombinedTemperatureReader(commander)
            >>> names = reader.get_sensor_names()
            >>> print(sorted(names))
            ['CPU1 Temp', 'CPU2 Temp', 'NVMe_nvme0n1', 'System Temp']
        """
        names = self.ipmi_reader.get_sensor_names()
        names.update(self.nvme_reader.get_sensor_names())
        return names
        
    def get_highest_temperature(self) -> Optional[float]:
        """Get the highest current temperature across all monitored sensors.

        Finds the maximum temperature from both IPMI and NVMe sources:
        1. Gets statistics for all sensors
        2. Extracts current temperature values
        3. Returns the highest value found

        Note:
            - Only considers sensors with valid readings
            - Skips sensors with insufficient readings
            - Returns None if no valid readings found

        Returns:
            float: Highest current temperature in °C
            None: If no valid readings available

        Example:
            >>> reader = CombinedTemperatureReader(commander)
            >>> max_temp = reader.get_highest_temperature()
            >>> if max_temp is not None:
            ...     print(f"Highest temperature: {max_temp}°C")
            Highest temperature: 48.5°C
        """
        all_stats = self.get_all_stats()
        if not all_stats:
            return None
        return max(stats["current"] for stats in all_stats.values())
        
    def get_average_temperature(self) -> Optional[float]:
        """Get the average temperature across all monitored sensors.

        Calculates the mean temperature from both IPMI and NVMe sources:
        1. Gets statistics for all sensors
        2. Extracts current temperature values
        3. Returns the mean value

        Note:
            - Only considers sensors with valid readings
            - Skips sensors with insufficient readings
            - Returns None if no valid readings found

        Returns:
            float: Average current temperature in °C
            None: If no valid readings available

        Example:
            >>> reader = CombinedTemperatureReader(commander)
            >>> avg_temp = reader.get_average_temperature()
            >>> if avg_temp is not None:
            ...     print(f"Average temperature: {avg_temp:.1f}°C")
            Average temperature: 42.5°C
        """
        all_stats = self.get_all_stats()
        if not all_stats:
            return None
        return mean(stats["current"] for stats in all_stats.values())
