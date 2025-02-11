"""
Fan Speed Learning Module

This module provides functionality to learn the lowest stable fan speeds
for different fan zones.
"""

import logging
import time
from typing import Dict, Optional, List, Any
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

    def _get_fan_readings(self, zone: str) -> List[Dict[str, float]]:
        """Get fan readings for a specific zone
        
        Args:
            zone: Fan zone ("chassis" or "cpu")
            
        Returns:
            List of fan readings with RPM values
        """
        readings = self.commander.get_sensor_readings()
        
        # Filter fans by zone
        if zone == "cpu":
            return [r for r in readings if r["name"].startswith("FANA") and r["state"] != "ns"]
        else:  # chassis zone
            return [r for r in readings if r["name"].startswith("FAN") and 
                   not r["name"].startswith("FANA") and r["state"] != "ns"]

    def _test_speed_step(self, hex_speed: str, zone: str, stabilize_time: int = 5) -> Dict[str, Any]:
        """Test a specific speed step and record RPM ranges
        
        Args:
            hex_speed: Hex value for speed step (e.g., "20", "40", "60", "ff")
            zone: Fan zone ("chassis" or "cpu")
            stabilize_time: Time to wait for fans to stabilize
            
        Returns:
            Dictionary with RPM ranges and stability info
        """
        try:
            # Set speed using raw command
            zone_id = "0x01" if zone == "cpu" else "0x00"
            command = f"raw 0x30 0x70 0x66 0x01 {zone_id} 0x{hex_speed}"
            self.commander._execute_ipmi_command(command)
            
            # Wait for fans to stabilize
            time.sleep(stabilize_time)
            
            # Get fan readings
            readings = self._get_fan_readings(zone)
            if not readings:
                return None
                
            # Calculate RPM ranges
            rpms = [r["value"] for r in readings if r["value"] is not None]
            if not rpms:
                return None
                
            return {
                "min": min(rpms),
                "max": max(rpms),
                "stable": all(rpm >= 100 for rpm in rpms)  # Consider stable if all fans running
            }
            
        except Exception as e:
            logger.error(f"Speed step test failed: {e}")
            return None

    def _test_temperature_response(self, zone: str, step_name: str, hex_speed: str, duration: int = 300) -> Dict[str, Any]:
        """Test temperature response at a specific fan speed
        
        Args:
            zone: Fan zone ("chassis" or "cpu")
            step_name: Name of speed step being tested
            hex_speed: Hex value for speed command
            duration: Test duration in seconds
            
        Returns:
            Dictionary with temperature response data
        """
        try:
            # Set fan speed
            zone_id = "0x01" if zone == "cpu" else "0x00"
            command = f"raw 0x30 0x70 0x66 0x01 {zone_id} 0x{hex_speed}"
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
            "min_speed": None,
            "max_speed": None,
            "thermal_response": {}  # New section for temperature response data
        }
        
        try:
            # Enter manual mode
            self.commander.set_manual_mode()
            
            # Define speed steps to test
            STEPS = [
                ("low", "20", 30),      # Low speed step (up to 30%)
                ("medium", "40", 50),    # Medium speed step (up to 50%)
                ("high", "60", 80),      # High speed step (up to 80%)
                ("full", "ff", 100)      # Full speed step (up to 100%)
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
                    "rpm_ranges": {}
                }
                
                # Test each zone at this speed
                for zone in ["chassis", "cpu"]:
                    current_step += 1
                    progress = (current_step / total_steps) * 100
                    
                    logger.info(f"\nProgress: {progress:.1f}%")
                    logger.info(f"Testing {zone} zone at {threshold}% speed")
                    logger.info("1. Testing fan speed stability...")
                    
                    # Test fan speeds
                    rpm_range = self._test_speed_step(hex_speed, zone)
                    if rpm_range and rpm_range["stable"]:
                        step_config["rpm_ranges"][zone] = {
                            "min": rpm_range["min"],
                            "max": rpm_range["max"]
                        }
                        
                        # Test temperature response
                        logger.info("2. Testing temperature response (5 minutes)...")
                        temp_response = self._test_temperature_response(zone, step_name, hex_speed)
                        if temp_response:
                            board_config["thermal_response"][zone][step_name] = temp_response
                            logger.info(f"   Initial temp: {temp_response['initial_temp']:.1f}°C")
                            logger.info(f"   Final temp: {temp_response['final_temp']:.1f}°C")
                            logger.info(f"   Change: {temp_response['temp_change']:.1f}°C")
                            if temp_response['time_to_stable']:
                                logger.info(f"   Time to stabilize: {temp_response['time_to_stable']:.0f}s")
                
                # Add step config if we got valid readings
                if step_config["rpm_ranges"]:
                    board_config["speed_steps"][step_name] = step_config
            
            # Set min/max speeds based on discovered ranges
            if board_config["speed_steps"]:
                lowest_step = next(iter(board_config["speed_steps"].values()))
                highest_step = list(board_config["speed_steps"].values())[-1]
                board_config["min_speed"] = lowest_step["threshold"]
                board_config["max_speed"] = highest_step["threshold"]
            
            # Update configuration
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
