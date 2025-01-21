"""
Fan Control Manager Module

This module provides the main control loop logic for managing
fan speeds based on temperature readings.
"""

import logging
import time
import threading
import re
from typing import Dict, Optional, List, Tuple
import yaml

from ..ipmi import IPMICommander, IPMIError
from ..ipmi.sensors import CombinedTemperatureReader
from .curve import FanCurve, LinearCurve, HysteresisCurve
from .learner import FanSpeedLearner

logger = logging.getLogger(__name__)

class ControlManager:
    """Manages fan control loop and safety features"""
    
    def __init__(self, config_path: str, monitor_mode: bool = False, learning_mode: bool = False):
        """Initialize control manager
        
        Args:
            config_path: Path to YAML configuration file
            monitor_mode: If True, use faster polling interval for monitoring
        """
        # Store configuration and modes
        self.config_path = config_path
        self.monitor_mode = monitor_mode
        self.learning_mode = learning_mode
        
        # Track current fan speeds to avoid unnecessary updates
        self.current_speeds = {}
        
        # Load configuration
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
            
        # Initialize IPMI (use defaults for local access)
        self.commander = IPMICommander()
        
        # Initialize sensor manager with patterns
        sensor_patterns = []
        for zone_config in self.config["fans"]["zones"].values():
            if zone_config["enabled"]:
                # Convert sensor names to patterns
                for sensor in zone_config["sensors"]:
                    # Replace exact names with patterns
                    # e.g. "CPU1 Temp" becomes "*CPU* Temp*"
                    pattern = f"*{sensor.replace('1', '*').replace('2', '*')}*"
                    sensor_patterns.append(pattern)
        
        self.sensor_manager = CombinedTemperatureReader(
            commander=self.commander,
            sensor_patterns=sensor_patterns,
            reading_timeout=self.config["safety"]["watchdog_timeout"],
            min_readings=self.config["safety"]["min_temp_readings"]
        )
        
        # Initialize fan curves for each zone
        self.fan_curves: Dict[str, FanCurve] = {}
        self._init_fan_curves()
        
        # Control loop state
        self._running = False
        self._control_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Emergency state tracking
        self._in_emergency = False
        self._last_valid_reading = time.time()
        
        logger.info("Control manager initialized")

    def _init_fan_curves(self) -> None:
        """Initialize fan curves from configuration"""
        fan_config = self.config["fans"]
        
        for zone_name, zone_config in fan_config["zones"].items():
            if not zone_config["enabled"]:
                continue
                
            # Create base curve
            base_curve = LinearCurve(
                points=zone_config["curve"],
                min_speed=fan_config["min_speed"],
                max_speed=fan_config["max_speed"]
            )
            
            # Wrap with hysteresis
            self.fan_curves[zone_name] = HysteresisCurve(
                base_curve=base_curve,
                hysteresis=self.config["temperature"]["hysteresis"]
            )
            
        logger.debug(f"Initialized fan curves for zones: {list(self.fan_curves.keys())}")

    def _get_zone_temperature(self, zone_name: str) -> Optional[float]:
        """Get temperature delta for a zone
        
        Args:
            zone_name: Name of the zone
            
        Returns:
            Temperature delta above target, or None if no valid reading
        """
        # Get configured sensor patterns for zone
        zone_config = self.config["fans"]["zones"][zone_name]
        base_sensors = zone_config["sensors"]
        
        # Get highest temperature from matching sensors
        highest_temp = None
        for base_sensor in base_sensors:
            # Create pattern for this sensor
            pattern = base_sensor.replace('*', '.*')  # Convert glob to regex
            pattern_re = re.compile(pattern, re.IGNORECASE)
            
            # Check all discovered sensors against this pattern
            sensor_names = self.sensor_manager.get_sensor_names()
            logger.debug(f"Checking pattern '{pattern}' against sensors: {list(sensor_names)}")
            
            for sensor_name in sensor_names:
                if pattern_re.search(sensor_name):  # Use search instead of match for more flexible matching
                    logger.debug(f"Pattern '{pattern}' matched sensor '{sensor_name}'")
                    stats = self.sensor_manager.get_sensor_stats(sensor_name)
                    if stats:
                        temp = stats["current"]
                        if highest_temp is None or temp > highest_temp:
                            highest_temp = temp
                            logger.debug(f"New highest temperature {temp}°C from {sensor_name}")
                    
        if highest_temp is None:
            return None
            
        # Calculate delta from zone-specific target
        target_temp = self.config["fans"]["zones"][zone_name]["target"]
        return max(0, highest_temp - target_temp)

    def _verify_fan_speeds(self, min_speed: int = None) -> bool:
        """Verify fan speeds are within acceptable range
        
        Args:
            min_speed: Minimum acceptable speed (None for config value)
            
        Returns:
            True if fans are operating correctly
        """
        try:
            readings = self.commander.get_sensor_readings()
            fan_readings = [r for r in readings if r["name"].startswith("FAN")]
            
            if not fan_readings:
                logger.error("No fan readings available")
                return False
                
            min_speed = min_speed or self.config["fans"]["min_speed"]
            responsive_fans = 0
            
            for fan in fan_readings:
                name = fan["name"]
                if fan["state"] == "ns":
                    logger.debug(f"{name} is not responding")  # Downgrade to debug level
                    continue
                    
                rpm = fan["value"]
                if rpm is None:  # Skip fans with no reading
                    continue
                    
                if rpm < 100:  # RPM too low - likely stopped
                    logger.error(f"{name} appears stopped: {rpm} RPM")
                    return False
                    
                responsive_fans += 1
                
            # Ensure we have enough working fans
            min_fans = self.config["safety"].get("min_working_fans", 1)
            if responsive_fans < min_fans:
                logger.error(f"Insufficient working fans: {responsive_fans} < {min_fans}")
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Fan speed verification failed: {e}")
            return False

    def _check_safety(self) -> bool:
        """Check system safety conditions
        
        Returns:
            True if safe, False if emergency action needed
        """
        try:
            # Update sensor readings
            self.sensor_manager.update_readings()
            self._last_valid_reading = time.time()
            
            # Update readings and check states
            readings = self.commander.get_sensor_readings()
            for reading in readings:
                if reading["state"] == "cr":
                    logger.error(f"Critical state detected for {reading['name']}")
                    return False
            
            # Check temperatures from all sources
            all_temps = []
            
            # IPMI temperature readings
            temp_readings = [r for r in readings if "temp" in r["name"].lower()]
            if temp_readings:
                ipmi_temps = [r["value"] for r in temp_readings if r["value"] is not None]
                all_temps.extend(ipmi_temps)
            
            # NVMe temperature readings
            nvme_temps = []  # Initialize as empty list
            nvme_stats = self.sensor_manager.nvme_reader.get_all_stats()
            if nvme_stats:
                nvme_temps = [stats["current"] for stats in nvme_stats.values()]
                all_temps.extend(nvme_temps)
            
            if not all_temps:
                logger.error("No temperature readings available from any source")
                return False
            
            # Check temperatures against zone-specific thresholds
            for zone_name, zone_config in self.config["fans"]["zones"].items():
                if not zone_config["enabled"]:
                    continue

                # Get zone-specific sensors
                zone_temps = []
                base_sensors = zone_config["sensors"]
                
                for base_sensor in base_sensors:
                    pattern = base_sensor.replace('*', '.*')
                    pattern_re = re.compile(pattern, re.IGNORECASE)
                    
                    # Check IPMI temperatures
                    for reading in temp_readings:
                        if pattern_re.search(reading["name"]) and reading["value"] is not None:
                            zone_temps.append(reading["value"])
                    
                    # Check NVMe temperatures if pattern matches
                    if "NVMe" in base_sensor:
                        zone_temps.extend(nvme_temps)

                if zone_temps:
                    max_zone_temp = max(zone_temps)
                    if max_zone_temp >= zone_config["critical_max"]:
                        logger.error(f"Critical temperature reached in {zone_name} zone: {max_zone_temp}°C")
                        return False
                
            # Check reading age
            reading_age = time.time() - self._last_valid_reading
            if reading_age > self.config["safety"]["watchdog_timeout"]:
                logger.error(f"Temperature reading timeout: {reading_age:.1f}s")
                return False
                
            # Verify fan speeds
            if not self._verify_fan_speeds():
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Safety check failed: {e}")
            return False

    def _emergency_action(self) -> None:
        """Take emergency action when safety check fails"""
        try:
            # Set maximum fan speed for both zones
            self.commander.set_fan_speed(100, zone="chassis")
            self.commander.set_fan_speed(100, zone="cpu")
            logger.warning("Emergency action: all fans set to 100%")
            
            # Verify fan response
            if not self._verify_fan_speeds(min_speed=90):  # Expect near max speed
                logger.error("Emergency fan speed change failed")
                # Try to restore BMC control as last resort
                self.commander.set_auto_mode()
                logger.warning("Emergency fallback: restored BMC control")
            
            # Set emergency flag
            self._in_emergency = True
            
        except Exception as e:
            logger.error(f"Emergency action failed: {e}")
            # Try to restore BMC control
            try:
                self.commander.set_auto_mode()
                logger.warning("Emergency fallback: restored BMC control")
            except Exception as fallback_error:
                logger.critical(f"Failed to restore BMC control: {fallback_error}")

    def _control_loop(self) -> None:
        """Main control loop"""
        while self._running:
            try:
                # Check safety conditions
                if not self._check_safety():
                    self._emergency_action()
                    continue
                    
                # Clear emergency state if we were in it
                if self._in_emergency:
                    self._in_emergency = False
                    logger.info("Exiting emergency state")
                
                # Update each zone
                for zone_name, curve in self.fan_curves.items():
                    # Get zone temperature
                    temp_delta = self._get_zone_temperature(zone_name)
                    if temp_delta is None:
                        logger.warning(f"No valid temperature for zone {zone_name}")
                        continue
                        
                    # Calculate fan speed for specific zone
                    speed = curve.get_speed(temp_delta)
                    
                    # Only update if speed has changed
                    current_speed = self.current_speeds.get(zone_name, 0)
                    if current_speed != speed:
                        # Get ramp step from config
                        ramp_step = self.config["fans"]["ramp_step"]
                        
                        # Calculate intermediate speed
                        if abs(speed - current_speed) > ramp_step:
                            if speed > current_speed:
                                new_speed = current_speed + ramp_step
                            else:
                                new_speed = current_speed - ramp_step
                        else:
                            new_speed = speed
                            
                        # Set new speed and verify
                        self.commander.set_fan_speed(new_speed, zone=zone_name)
                        logger.info(f"Fan speed set to {new_speed}% for zone {zone_name} (target: {speed}%)")
                        self.current_speeds[zone_name] = new_speed
                    
                    logger.debug(f"Zone {zone_name}: {temp_delta:.1f}°C -> {speed}% (current)")
                    
            except Exception as e:
                logger.error(f"Control loop error: {e}")
                self._emergency_action()
                
            # Wait for next iteration - use monitor_interval if in monitor mode
            interval = self.config["fans"]["monitor_interval"] if self.monitor_mode else self.config["fans"]["polling_interval"]
            time.sleep(interval)

    def learn_min_speeds(self) -> Dict[str, int]:
        """Learn minimum stable fan speeds
        
        Returns:
            Dictionary of learned minimum speeds by zone
        """
        learner = FanSpeedLearner(self.commander, self.config_path)
        return learner.learn_min_speeds()

    def start(self) -> None:
        """Start the control loop"""
        with self._lock:
            if self._running:
                return
                
            # Set manual mode
            self.commander.set_manual_mode()
            
            # If in learning mode, learn speeds first
            if self.learning_mode:
                logger.info("Starting fan speed learning")
                min_speeds = self.learn_min_speeds()
                logger.info(f"Learned minimum speeds: {min_speeds}")
                # Reload configuration with new speeds
                with open(self.config_path) as f:
                    self.config = yaml.safe_load(f)
                self._init_fan_curves()
            
            # Start control thread
            self._running = True
            self._control_thread = threading.Thread(target=self._control_loop)
            self._control_thread.daemon = True
            self._control_thread.start()
            
            logger.info("Control loop started")

    def stop(self) -> None:
        """Stop the control loop"""
        with self._lock:
            if not self._running:
                return
                
            # Stop control thread
            self._running = False
            if self._control_thread:
                self._control_thread.join()
                self._control_thread = None
            
            # Restore automatic control if configured
            if self.config["safety"]["restore_on_exit"]:
                try:
                    self.commander.set_auto_mode()
                    logger.info("Restored automatic fan control")
                except Exception as e:
                    logger.error(f"Failed to restore automatic control: {e}")
                    
            logger.info("Control loop stopped")

    def get_status(self) -> Dict:
        """Get current control status
        
        Returns:
            Dictionary with current status information
        """
        status = {
            "running": self._running,
            "emergency": self._in_emergency,
            "temperatures": {},
            "fan_speeds": {}
        }
        
        # Get temperature readings
        all_stats = self.sensor_manager.get_all_stats()
        for sensor, stats in all_stats.items():
            status["temperatures"][sensor] = stats["current"]
            
        # Get fan speeds for each zone
        for zone_name in self.fan_curves:
            temp_delta = self._get_zone_temperature(zone_name)
            if temp_delta is not None:
                speed = self.fan_curves[zone_name].get_speed(temp_delta)
                status["fan_speeds"][zone_name] = speed
                
        return status
