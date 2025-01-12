"""
Fan Control Manager Module

This module provides the main control loop logic for managing
fan speeds based on temperature readings.
"""

import logging
import time
import threading
import re
from typing import Dict, Optional, List
import yaml

from ..ipmi import IPMICommander, IPMIError
from ..ipmi.sensors import SensorReader
from .curve import FanCurve, LinearCurve, HysteresisCurve

logger = logging.getLogger(__name__)

class ControlManager:
    """Manages fan control loop and safety features"""
    
    def __init__(self, config_path: str):
        """Initialize control manager
        
        Args:
            config_path: Path to YAML configuration file
        """
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
        
        self.sensor_manager = SensorReader(
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
            pattern = f"*{base_sensor.replace('1', '*').replace('2', '*')}*"
            pattern_re = re.compile(pattern.replace("*", ".*"), re.IGNORECASE)
            
            # Check all discovered sensors against this pattern
            for sensor_name in self.sensor_manager.get_sensor_names():
                if pattern_re.match(sensor_name):
                    stats = self.sensor_manager.get_sensor_stats(sensor_name)
                    if stats:
                        temp = stats["current"]
                        if highest_temp is None or temp > highest_temp:
                            highest_temp = temp
                    
        if highest_temp is None:
            return None
            
        # Calculate delta from target
        target_temp = self.config["temperature"]["target"]
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
                    logger.warning(f"{name} is not responding")
                    continue
                    
                rpm = fan["value"]
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
            
            # Check temperatures
            temp_readings = [r for r in readings if "temp" in r["name"].lower()]
            if not temp_readings:
                logger.error("No temperature readings available")
                return False
            
            max_temp = max(r["value"] for r in temp_readings if r["value"] is not None)
            if max_temp >= self.config["temperature"]["critical_max"]:
                logger.error(f"Critical temperature reached: {max_temp}°C")
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
            # Set maximum fan speed
            self.commander.set_fan_speed(100)
            logger.warning("Emergency action: fans set to 100%")
            
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
                        
                    # Calculate and set fan speed
                    speed = curve.get_speed(temp_delta)
                    self.commander.set_fan_speed(speed)
                    
                    logger.debug(f"Zone {zone_name}: {temp_delta:.1f}°C -> {speed}%")
                    
            except Exception as e:
                logger.error(f"Control loop error: {e}")
                self._emergency_action()
                
            # Wait for next iteration
            time.sleep(self.config["fans"]["polling_interval"])

    def start(self) -> None:
        """Start the control loop"""
        with self._lock:
            if self._running:
                return
                
            # Set manual mode
            self.commander.set_manual_mode()
            
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
