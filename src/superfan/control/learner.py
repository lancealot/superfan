"""
Fan Speed Learning Module

This module provides functionality to learn the lowest stable fan speeds
for different fan zones.
"""

import logging
import time
from typing import Dict, Optional
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

    def _is_speed_stable(self, speed: int, zone: str, check_duration: int = 10) -> bool:
        """Check if fan speed is stable
        
        Args:
            speed: Speed percentage to check
            zone: Fan zone ("chassis" or "cpu")
            check_duration: Duration to monitor stability in seconds
            
        Returns:
            True if speed is stable
        """
        try:
            # Set initial speed for specific zone
            self.commander.set_fan_speed(speed, zone=zone)
            
            # Wait for fans to adjust
            time.sleep(5)
            
            # Monitor for stability
            start_time = time.time()
            while time.time() - start_time < check_duration:
                readings = self.commander.get_sensor_readings()
                
                # Filter fans by zone
                if zone == "cpu":
                    fan_readings = [r for r in readings if r["name"].startswith("FANA")]
                else:  # chassis zone
                    fan_readings = [r for r in readings if r["name"].startswith("FAN") and not r["name"].startswith("FANA")]
                
                # Check each fan
                for fan in fan_readings:
                    if fan["state"] != "ns":  # Skip non-responsive fans
                        rpm = fan["value"]
                        if rpm is None or rpm < 100:  # RPM too low or missing
                            return False
                
                time.sleep(1)
                
            return True
            
        except Exception as e:
            logger.error(f"Stability check failed for {zone} zone: {e}")
            return False

    def _learn_zone_speed(self, zone: str) -> int:
        """Learn minimum stable speed for a specific fan zone
        
        Args:
            zone: Fan zone ("chassis" or "cpu")
            
        Returns:
            Minimum stable speed percentage
        """
        # Start from a safe speed
        test_speed = 30  # Start from 30% as a safe baseline
        last_stable = test_speed
        
        # Test decreasing speeds gradually
        while test_speed >= 8:  # Don't test below 8% to ensure fan operation
            if self._is_speed_stable(test_speed, zone=zone):
                # Speed is stable, record it and try lower
                last_stable = test_speed
                test_speed -= 2  # Decrease in 2% increments for smoother testing
            else:
                # Speed unstable, use last stable speed
                break
                
        return last_stable

    def learn_min_speeds(self) -> Dict[str, int]:
        """Learn minimum stable speeds for each fan zone
        
        Returns:
            Dictionary mapping zone names to minimum stable speeds
        """
        min_speeds = {}
        
        try:
            # Enter manual mode
            self.commander.set_manual_mode()
            
            # Learn speeds for each zone
            for zone in ["chassis", "cpu"]:
                min_speeds[zone] = self._learn_zone_speed(zone)
                logger.info(f"Learned minimum speed for {zone} zone: {min_speeds[zone]}%")
            
            # Update configuration with the highest minimum speed
            # This ensures safe operation across all zones
            self.config["fans"]["min_speed"] = max(min_speeds.values())
            
            # Save updated configuration
            with open(self.config_path, 'w') as f:
                yaml.safe_dump(self.config, f, default_flow_style=False)
                
            logger.info(f"Learned minimum speeds: {min_speeds}")
            return min_speeds
            
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
