"""
Fan Control Manager Module

This module provides the main control loop logic for managing
fan speeds based on temperature readings.
"""

import logging
import time
import threading
import re
from typing import Dict, Optional, List, Tuple, Any
import yaml

from ..ipmi import IPMICommander, IPMIError
from ..ipmi.sensors import CombinedTemperatureReader
from .curve import FanCurve, StableSpeedCurve, HysteresisCurve
from .learner import FanSpeedLearner

logger = logging.getLogger(__name__)

class ControlManager:
    """Manages fan control loop and safety features"""
    
    
    def __init__(self, config_path: str, monitor_mode: bool = False, learning_mode: bool = False):
        """Initialize control manager
        
        Args:
            config_path: Path to YAML configuration file
            monitor_mode: If True, use faster polling interval for monitoring
            learning_mode: If True, run fan speed learning before control
        """
        # Store configuration and modes
        self.config_path = config_path
        self.monitor_mode = monitor_mode
        self.learning_mode = learning_mode
        
        # Track current fan speeds and states
        self.current_speeds: Dict[str, Dict[str, Any]] = {}
        
        # Load configuration
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
            
        # Initialize IPMI with config path
        self.commander = IPMICommander(config_path)
        
        # Initialize sensor manager with patterns
        sensor_patterns = []
        for zone_config in self.config["fans"]["zones"].values():
            if zone_config["enabled"]:
                # Add zone's sensor patterns
                sensor_patterns.extend(zone_config["sensors"])
                logger.info(f"Added sensor patterns: {zone_config['sensors']}")
        
        logger.info(f"Initializing sensor manager with patterns: {sensor_patterns}")
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
                
            # Create stable speed curve
            base_curve = StableSpeedCurve(config=self.config)
            
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
        logger.info(f"Getting temperature for zone {zone_name}")
        logger.debug(f"Base sensors: {base_sensors}")
        
        # Get highest temperature from matching sensors
        highest_temp = None
        for base_sensor in base_sensors:
            # Create pattern for this sensor
            pattern = base_sensor.replace('*', '.*')  # Convert glob to regex
            pattern_re = re.compile(pattern, re.IGNORECASE)
            logger.debug(f"Using pattern: {pattern}")
            
            # Get all sensor readings
            readings = self.commander.get_sensor_readings()
            temp_readings = [r for r in readings if "temp" in r["name"].lower()]
            
            # Check IPMI temperature sensors
            for reading in temp_readings:
                if reading["value"] is None or reading["state"] == "ns":
                    continue
                    
                if pattern_re.search(reading["name"]):
                    temp = reading["value"]
                    logger.debug(f"Got temperature: {temp}°C from {reading['name']}")
                    if highest_temp is None or temp > highest_temp:
                        highest_temp = temp
                        logger.info(f"New highest temperature {temp}°C from {reading['name']}")
            
            # Check NVMe temperatures if pattern matches
            if "NVMe" in base_sensor:
                nvme_stats = self.sensor_manager.nvme_reader.get_all_stats()
                if nvme_stats:
                    for drive, stats in nvme_stats.items():
                        temp = stats["current"]
                        logger.debug(f"Got NVMe temperature: {temp}°C from {drive}")
                        if highest_temp is None or temp > highest_temp:
                            highest_temp = temp
                            logger.info(f"New highest temperature {temp}°C from {drive}")
                    
        if highest_temp is None:
            logger.warning(f"No valid temperatures found for zone {zone_name}")
            return None
            
        # Calculate delta from zone-specific target
        target_temp = self.config["fans"]["zones"][zone_name]["target"]
        delta = max(0, highest_temp - target_temp)
        logger.info(f"Zone {zone_name}: highest temp {highest_temp}°C, target {target_temp}°C, delta {delta}°C")
        return delta

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
                
            # Group fans by type
            groups = {
                'high_rpm': [],
                'low_rpm': [],
                'cpu': []
            }
            
            for fan in fan_readings:
                if fan["state"] == "ns" or fan["value"] is None:
                    continue
                    
                # Determine fan group
                if fan["name"].startswith("FANA"):
                    groups['cpu'].append(fan)
                elif fan["name"] in ["FAN1", "FAN5"]:
                    groups['high_rpm'].append(fan)
                elif fan["name"].startswith("FAN"):
                    groups['low_rpm'].append(fan)
            
            # Check each group against known ranges
            for group_name, fans in groups.items():
                if not fans:
                    continue
                    
                # Get RPM ranges from config
                board_config = self.config["fans"]["board_config"]
                speed_steps = board_config["speed_steps"]
                
                # Get current RPMs
                group_rpms = [f["value"] for f in fans]
                avg_rpm = sum(group_rpms) / len(group_rpms)
                logger.info(f"{group_name} fan RPMs: {group_rpms}, average: {avg_rpm}")
                
                # Find appropriate speed step for current speeds
                current_step = None
                best_match_diff = float('inf')
                best_match_step = None
                
                # Find appropriate speed step for current speeds
                current_step = None
                best_match_diff = float('inf')
                best_match_step = None
                
                # First try to find exact match
                for step_name, step in speed_steps.items():
                    # Get RPM ranges for this step
                    if group_name == "cpu":
                        rpm_ranges = step["rpm_ranges"]["cpu"]["cpu"]
                    else:
                        rpm_ranges = step["rpm_ranges"]["chassis"][group_name]
                    
                    # If min_speed is specified, only use that step
                    if min_speed is not None:
                        if step["threshold"] == min_speed:
                            current_step = step
                            break
                    # For normal operation, find exact match first
                    elif rpm_ranges["min"] <= avg_rpm <= rpm_ranges["max"]:
                        current_step = step
                        break
                
                # If no exact match, find closest match based on stable_rpm
                if current_step is None:
                    for step_name, step in speed_steps.items():
                        # Get RPM ranges for this step
                        if group_name == "cpu":
                            rpm_ranges = step["rpm_ranges"]["cpu"]["cpu"]
                        else:
                            rpm_ranges = step["rpm_ranges"]["chassis"][group_name]
                        
                        if rpm_ranges.get("stable_rpm") is not None:
                            diff = abs(avg_rpm - rpm_ranges["stable_rpm"])
                            if diff < best_match_diff:
                                best_match_diff = diff
                                best_match_step = step
                    
                    # Use closest match or full speed as last resort
                    if best_match_step is not None:
                        current_step = best_match_step
                        logger.info(f"Using closest matching step ({current_step['threshold']}%) for {group_name} fans")
                    else:
                        current_step = speed_steps["full"]
                        logger.info(f"No matching step found, using full speed for {group_name} fans")
                
                # Get RPM ranges for this group
                if group_name == "cpu":
                    rpm_ranges = current_step["rpm_ranges"]["cpu"]["cpu"]
                else:
                    rpm_ranges = current_step["rpm_ranges"]["chassis"][group_name]
                
                # Log RPM ranges
                logger.info(f"Using RPM ranges for {group_name} fans at {current_step['threshold']}% speed: {rpm_ranges}")
                
                group_rpms = [f["value"] for f in fans]
                
                # Get current RPMs
                group_rpms = [f["value"] for f in fans]
                logger.info(f"{group_name} fan RPMs: {group_rpms}")
                
                # Check minimum speed if specified
                if min_speed is not None:
                    min_rpm = rpm_ranges["min"]  # Use exact min from step
                    if min(group_rpms) < min_rpm:
                        logger.error(f"{group_name} fans below required {min_speed}% speed (min RPM: {min_rpm})")
                        return False
                
                # Check maximum expected speed
                max_rpm = rpm_ranges["max"]
                if max(group_rpms) > max_rpm:
                    logger.warning(f"{group_name} fans above maximum expected speed (max RPM: {max_rpm})")
                
                # Check stability
                avg_rpm = sum(group_rpms) / len(group_rpms)
                if rpm_ranges.get("stable_rpm") is not None:
                    expected_rpm = rpm_ranges["stable_rpm"]
                    if expected_rpm > 0:  # Avoid division by zero
                        variation = abs(avg_rpm - expected_rpm) / expected_rpm * 100
                        logger.info(f"{group_name} fans average RPM: {avg_rpm}, expected: {expected_rpm}, variation: {variation:.1f}%")
                        if variation > 30:  # 30% variation from stable point
                            logger.warning(f"{group_name} fans unstable")
                    else:
                        logger.info(f"{group_name} fans average RPM: {avg_rpm} (stability check skipped - zero expected RPM)")
                else:
                    logger.info(f"{group_name} fans average RPM: {avg_rpm} (stability check skipped - no stable RPM)")
            
            # Ensure we have enough working fans
            min_fans = self.config["safety"].get("min_working_fans", 1)
            working_fans = sum(len(fans) for fans in groups.values())
            if working_fans < min_fans:
                logger.error(f"Insufficient working fans: {working_fans} < {min_fans}")
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
                
                logger.info(f"Checking temperatures for zone {zone_name}")
                logger.debug(f"Base sensors: {base_sensors}")
                
                for base_sensor in base_sensors:
                    pattern = base_sensor.replace('*', '.*')
                    pattern_re = re.compile(pattern, re.IGNORECASE)
                    logger.debug(f"Using pattern: {pattern}")
                    
                    # Check IPMI temperatures
                    for reading in temp_readings:
                        logger.debug(f"Checking reading: {reading['name']} = {reading.get('value')}°C (state: {reading.get('state')})")
                        if pattern_re.match(reading["name"]) and reading["value"] is not None:
                            zone_temps.append(reading["value"])
                            logger.debug(f"Added temperature {reading['value']}°C from {reading['name']}")
                    
                    # Check NVMe temperatures if pattern matches
                    if "NVMe" in base_sensor:
                        logger.debug(f"Adding NVMe temperatures: {nvme_temps}")
                        zone_temps.extend(nvme_temps)

                if zone_temps:
                    max_zone_temp = max(zone_temps)
                    logger.info(f"Zone {zone_name} temperatures: {zone_temps}")
                    logger.info(f"Max temperature for zone {zone_name}: {max_zone_temp}°C")
                    if max_zone_temp >= zone_config["critical_max"]:
                        logger.error(f"Critical temperature reached in {zone_name} zone: {max_zone_temp}°C")
                        return False
                else:
                    logger.warning(f"No valid temperatures found for zone {zone_name}")
                
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
                    # Get target temperature from config
                    target_temp = self.config["fans"]["zones"][zone_name]["target"]
                    
                    # Calculate temperature delta using pattern matching for all zones
                    temp_delta = self._get_zone_temperature(zone_name)
                    if temp_delta is None:
                        logger.warning(f"No valid temperature for zone {zone_name}")
                        continue
                    
                    # Get speed info from curve
                    speed_info = curve.get_speed(temp_delta)
                    target_speed = speed_info['speed']
                    
                    # Only update if speed has changed significantly
                    current_speed = self.current_speeds.get(zone_name, {}).get('speed', 0)
                    if abs(target_speed - current_speed) >= 5:  # 5% threshold for changes
                        # Set new speed using proper hex formatting
                        self.commander.set_fan_speed(
                            speed_percent=target_speed,
                            zone=zone_name
                        )
                        
                        # Update tracking
                        self.current_speeds[zone_name] = {
                            'speed': target_speed,
                            'hex_speed': speed_info['hex_speed'],
                            'needs_prefix': speed_info['needs_prefix'],
                            'expected_rpms': speed_info['expected_rpms']
                        }
                        
                        logger.info(f"Fan speed set to {target_speed}% for zone {zone_name}")
                        logger.debug(f"Zone {zone_name}: {temp_delta:.1f}°C -> {target_speed}%")
                        
                        # Verify speeds match expectations
                        readings = self.commander.get_sensor_readings()
                        fan_readings = [r for r in readings if r["name"].startswith("FAN")]
                        
                        for fan in fan_readings:
                            if fan["state"] == "ns" or fan["value"] is None:
                                continue
                                
                            # Determine fan group
                            if fan["name"].startswith("FANA"):
                                group = 'cpu'
                            elif fan["name"] in ["FAN1", "FAN5"]:
                                group = 'high_rpm'
                            else:
                                group = 'low_rpm'
                                
                            # Check against expected range
                            rpm = fan["value"]
                            expected = speed_info['expected_rpms'][group]
                            if rpm < expected['min']:
                                logger.warning(f"{fan['name']} RPM ({rpm}) below expected minimum ({expected['min']})")
                            elif rpm > expected['max']:
                                logger.warning(f"{fan['name']} RPM ({rpm}) above expected maximum ({expected['max']})")
                    
            except Exception as e:
                logger.error(f"Control loop error: {e}")
                self._emergency_action()
                
            # Wait for next iteration - use monitor_interval if in monitor mode
            interval = self.config["fans"]["monitor_interval"] if self.monitor_mode else self.config["fans"]["polling_interval"]
            time.sleep(interval)

    def start(self) -> None:
        """Start the control loop"""
        with self._lock:
            if self._running:
                return
                
            # Set manual mode
            self.commander.set_manual_mode()
            
            # If in learning mode, learn board configuration
            if self.learning_mode:
                logger.info("Starting board configuration learning")
                learner = FanSpeedLearner(self.commander, self.config_path)
                board_config = learner.learn_board_config()
                logger.info("Board configuration learned and saved")
                
                # Reload configuration with learned parameters
                with open(self.config_path) as f:
                    self.config = yaml.safe_load(f)
                self._init_fan_curves()
                
                # Return early - don't start control loop in learning mode
                return
            
            # Set initial fan speeds based on current temperatures
            for zone_name, curve in self.fan_curves.items():
                temp_delta = self._get_zone_temperature(zone_name)
                if temp_delta is not None:
                    speed_info = curve.get_speed(temp_delta)
                    self.commander.set_fan_speed(
                        speed_percent=speed_info['speed'],
                        zone=zone_name
                    )
                    self.current_speeds[zone_name] = {
                        'speed': speed_info['speed'],
                        'hex_speed': speed_info['hex_speed'],
                        'needs_prefix': speed_info['needs_prefix'],
                        'expected_rpms': speed_info['expected_rpms']
                    }
                else:
                    # If no valid temperature, set a safe default
                    default_speed = 50  # 50% as safe minimum
                    speed_info = curve.get_speed(0)  # Get info for minimum speed
                    self.commander.set_fan_speed(
                        speed_percent=default_speed,
                        zone=zone_name
                    )
                    self.current_speeds[zone_name] = {
                        'speed': default_speed,
                        'hex_speed': speed_info['hex_speed'],
                        'needs_prefix': speed_info['needs_prefix'],
                        'expected_rpms': speed_info['expected_rpms']
                    }
            
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

    def get_status(self) -> Dict[str, Any]:
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
            
        # Get current and target fan speeds for each zone
        for zone_name in self.fan_curves:
            # Get current speed info from tracking
            current = self.current_speeds.get(zone_name, {})
            
            # Get target speed from temperature and curve
            temp_delta = self._get_zone_temperature(zone_name)
            target = None
            if temp_delta is not None:
                target = self.fan_curves[zone_name].get_speed(temp_delta)
            
            status["fan_speeds"][zone_name] = {
                "current": current.get('speed'),
                "target": target['speed'] if target else None,
                "hex_speed": current.get('hex_speed'),
                "needs_prefix": current.get('needs_prefix'),
                "expected_rpms": current.get('expected_rpms')
            }
                
        return status
