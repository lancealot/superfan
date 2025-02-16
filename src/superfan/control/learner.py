"""
Fan Speed Learning Module

This module provides functionality to learn the lowest stable fan speeds
for different fan zones.
"""

import logging
import time
from typing import Dict, Optional, List, Any, Tuple
import yaml

from ..ipmi import IPMICommander, IPMIError

logger = logging.getLogger(__name__)

class FanSpeedLearner:
    """Learns minimum stable fan speeds for different zones"""
    
    
    def __init__(self, commander: IPMICommander, config_path: str):
        """Initialize fan speed learner
        
        Args:
            commander: IPMI commander instance
            config_path: Path to configuration file
        """
        self.commander = commander
        self.config_path = config_path
        
        # Load configuration
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

    def _get_fan_readings(self, zone: str) -> List[Dict[str, Any]]:
        """Get fan readings for a specific zone with group classification
        
        Args:
            zone: Fan zone ("chassis" or "cpu")
            
        Returns:
            List of fan readings with RPM values and group info
        """
        readings = self.commander.get_sensor_readings()
        result = []
        
        for r in readings:
            if r["state"] == "ns" or not r["name"].startswith("FAN"):
                continue
                
            # Determine fan group
            if r["name"].startswith("FANA"):
                if zone == "cpu":
                    r["group"] = "cpu"
                    result.append(r)
            elif not r["name"].startswith("FANB"):  # Skip unused FANB
                if zone == "chassis":
                    r["group"] = "high_rpm" if r["name"] in ["FAN1", "FAN5"] else "low_rpm"
                    result.append(r)
                    
        return result

    def _get_fan_stats(self, readings: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Get fan speed statistics for each group
        
        Args:
            readings: List of fan readings with group info
            
        Returns:
            Dictionary of group stats with RPM ranges and stability info
        """
        if not readings:
            return {}
            
        # Group fans by type
        groups = {}
        for r in readings:
            if r["value"] is None:
                continue
            group = r["group"]
            if group not in groups:
                groups[group] = []
            groups[group].append(r)
            
        # Calculate stats for each group
        result = {}
        for group_name, fans in groups.items():
            if fans:
                rpms = [f["value"] for f in fans]
                result[group_name] = {
                    "min": min(rpms),
                    "max": max(rpms),
                    "avg": sum(rpms) / len(rpms),
                    "stable": abs(max(rpms) - min(rpms)) < 100  # Less than 100 RPM variation
                }
                
        return result

    def _test_speed_step(self, hex_speed: str, zone: str, threshold: int,
                        stabilize_time: int = 5, retries: int = 3) -> Dict[str, Any]:
        """Test a specific speed step and record RPM ranges
        
        Args:
            hex_speed: Hex value for speed step (with 0x prefix)
            zone: Fan zone ("chassis" or "cpu")
            threshold: Speed percentage this step represents
            stabilize_time: Time to wait for fans to stabilize
            retries: Number of retry attempts
            
        Returns:
            Dictionary with RPM ranges and stability info
        """
        try:
            # Set speed using proper command format
            zone_id = "0x01" if zone == "cpu" else "0x00"
            command = f"raw 0x30 0x70 0x66 0x01 {zone_id} {hex_speed}"
            self.commander._execute_ipmi_command(command)
            
            # Wait for fans to stabilize
            time.sleep(stabilize_time)
            
            # Try multiple readings to ensure stable readings
            readings = None
            fan_stats = None
            for attempt in range(retries):
                readings = self._get_fan_readings(zone)
                if readings:
                    fan_stats = self._get_fan_stats(readings)
                    if fan_stats:
                        # Check if we have stable readings
                        all_stable = all(group.get("stable", False) for group in fan_stats.values())
                        if all_stable:
                            break
                        logger.warning(f"Attempt {attempt + 1}: Fans not stable")
                    else:
                        logger.warning(f"Attempt {attempt + 1}: No fan stats")
                time.sleep(2)
                
            return {"groups": fan_stats} if fan_stats else None
            
        except Exception as e:
            logger.error(f"Speed step test failed: {e}")
            return None

    def _test_temperature_response(self, zone: str, step_name: str, hex_speed: str,
                                 duration: int = 300) -> Dict[str, Any]:
        """Test temperature response at a specific fan speed
        
        Args:
            zone: Fan zone ("chassis" or "cpu")
            step_name: Name of speed step being tested
            hex_speed: Hex value for speed command (with 0x prefix)
            duration: Test duration in seconds
            
        Returns:
            Dictionary with temperature response data
        """
        try:
            # Set fan speed
            zone_id = "0x01" if zone == "cpu" else "0x00"
            command = f"raw 0x30 0x70 0x66 0x01 {zone_id} {hex_speed}"
            self.commander._execute_ipmi_command(command)
            
            # Monitor temperatures
            start_time = time.time()
            temps = []
            while time.time() - start_time < duration:
                readings = self.commander.get_sensor_readings()
                temp_readings = [r for r in readings if "temp" in r["name"].lower()]
                
                # Get relevant temperatures for zone
                if zone == "cpu":
                    zone_temps = [r["value"] for r in temp_readings 
                                if any(s in r["name"].lower() for s in ["cpu", "vrm"])
                                and r["value"] is not None]
                else:
                    zone_temps = [r["value"] for r in temp_readings 
                                if not any(s in r["name"].lower() for s in ["cpu", "vrm"])
                                and r["value"] is not None]
                
                if zone_temps:
                    temps.append({
                        "time": time.time() - start_time,
                        "temp": max(zone_temps)
                    })
                
                time.sleep(10)  # Sample every 10 seconds
            
            # Calculate temperature response
            if len(temps) >= 2:
                temp_change = temps[-1]["temp"] - temps[0]["temp"]
                time_to_stable = None
                
                # Find time to stabilize (when temp change < 1°C over 60s)
                for i in range(len(temps)-6):  # 6 samples = 60s
                    if abs(temps[i+6]["temp"] - temps[i]["temp"]) < 1:
                        time_to_stable = temps[i+6]["time"]
                        break
                
                return {
                    "initial_temp": temps[0]["temp"],
                    "final_temp": temps[-1]["temp"],
                    "temp_change": temp_change,
                    "time_to_stable": time_to_stable,
                    "readings": temps
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Temperature response test failed: {e}")
            return None

    def learn_board_config(self) -> Dict[str, Any]:
        """Learn board-specific fan control parameters
        
        Returns:
            Dictionary with learned configuration
        """
        board_config = {
            "speed_steps": {},
            "min_speed": None,  # Will be set based on discovered minimums
            "max_speed": 100,   # Maximum speed
            "thermal_response": {}  # Temperature response data
        }
        
        try:
            # Enter manual mode
            self.commander.set_manual_mode()
            
            # Test full range of speeds for H12 board
            STEPS = [
                # Start with lowest speeds to find minimums
                ("off", "0x00", 0),      # Off - find if fans can stop
                ("very_low", "0x10", 12), # Very low - find minimum RPM
                ("low", "0x20", 25),     # Low speed
                ("medium", "0x40", 50),  # Medium speed
                ("high", "0x60", 75),    # High speed
                ("full", "0xff", 100)    # Full speed
            ]
            
            logger.info("Starting fan speed and temperature response learning")
            
            # Initialize thermal response sections
            for zone in ["chassis", "cpu"]:
                board_config["thermal_response"][zone] = {}
            
            total_steps = len(STEPS) * 2  # 2 zones per step
            current_step = 0
            
            # Test each speed step
            for step_name, hex_speed, threshold in STEPS:
                logger.info(f"\nTesting speed step: {step_name} ({threshold}%)")
                
                # Create step configuration
                step_config = {
                    "threshold": threshold,
                    "hex_speed": hex_speed,
                    "needs_prefix": False,  # H12 board never needs prefix
                    "rpm_ranges": {}
                }
                
                # Test each zone at this speed
                for zone in ["chassis", "cpu"]:
                    current_step += 1
                    progress = (current_step / total_steps) * 100
                    
                    logger.info(f"\nProgress: {progress:.1f}%")
                    logger.info(f"Testing {zone} zone at {threshold}% speed")
                    logger.info("1. Testing fan speed stability...")
                    
                    # Get fan readings and stats
                    readings = self._get_fan_readings(zone)
                    if readings:
                        fan_stats = self._get_fan_stats(readings)
                        if fan_stats:
                            step_config["rpm_ranges"][zone] = fan_stats
                            logger.info(f"Fan stats at {threshold}%: {fan_stats}")
                        
                        # Test temperature response
                        logger.info("2. Testing temperature response (5 minutes)...")
                        temp_response = self._test_temperature_response(
                            zone, step_name, hex_speed)
                        if temp_response:
                            board_config["thermal_response"][zone][step_name] = temp_response
                            logger.info(f"   Initial temp: {temp_response['initial_temp']:.1f}°C")
                            logger.info(f"   Final temp: {temp_response['final_temp']:.1f}°C")
                            logger.info(f"   Change: {temp_response['temp_change']:.1f}°C")
                            if temp_response['time_to_stable']:
                                logger.info(f"   Time to stabilize: {temp_response['time_to_stable']:.0f}s")
                
                # Add step config if we got valid readings
                if any(step_config["rpm_ranges"].values()):
                    board_config["speed_steps"][step_name] = step_config
            
            # Find lowest speed where fans are stable
            min_speed = None
            for step_name, step_config in board_config["speed_steps"].items():
                if step_config["rpm_ranges"]:
                    # Check if fans are running at this speed
                    chassis_rpms = step_config["rpm_ranges"].get("chassis", {})
                    cpu_rpms = step_config["rpm_ranges"].get("cpu", {})
                    
                    if chassis_rpms and cpu_rpms:
                        # Check if any fans are running
                        chassis_running = any(group.get("min", 0) > 0 for group in chassis_rpms.values())
                        cpu_running = any(group.get("min", 0) > 0 for group in cpu_rpms.values())
                        
                        # If fans are running and stable
                        if chassis_running and cpu_running:
                            chassis_stable = all(group.get("stable", False) for group in chassis_rpms.values())
                            cpu_stable = all(group.get("stable", False) for group in cpu_rpms.values())
                            
                            if chassis_stable and cpu_stable:
                                if min_speed is None or step_config["threshold"] < min_speed:
                                    min_speed = step_config["threshold"]
            
            # Set min speed (default to 0 if fans can stop)
            board_config["min_speed"] = min_speed if min_speed is not None else 0
            
            # Update configuration with learned values
            self.config["fans"]["board_config"] = board_config
            
            # Save updated configuration
            with open(self.config_path, 'w') as f:
                yaml.safe_dump(self.config, f, default_flow_style=False)
            
            logger.info("Board configuration learned and saved")
            return board_config
            
        except Exception as e:
            logger.error(f"Learning failed: {e}")
            # Ensure we restore automatic mode
            try:
                self.commander.set_auto_mode()
            except Exception as restore_error:
                logger.error(f"Failed to restore auto mode: {restore_error}")
            raise
            
        finally:
            # Restore automatic mode
            try:
                self.commander.set_auto_mode()
            except Exception as e:
                logger.error(f"Failed to restore auto mode: {e}")
