"""
IPMI Command Execution Module

This module provides a wrapper around ipmitool for executing IPMI commands
and managing fan control on Supermicro servers.
"""

import subprocess
import logging
import math
import time
import yaml
from typing import Optional, List, Dict, Tuple, Union
from enum import Enum

logger = logging.getLogger(__name__)

class MotherboardGeneration(Enum):
    """Supported Supermicro motherboard generations"""
    X9 = "X9"
    X10 = "X10"
    X11 = "X11"
    H12 = "H12"  # AMD EPYC 7002 series
    X13 = "X13"
    UNKNOWN = "UNKNOWN"

class IPMIError(Exception):
    """Base exception for IPMI-related errors"""
    pass

class IPMIConnectionError(IPMIError):
    """Raised when IPMI connection fails"""
    pass

class IPMICommandError(IPMIError):
    """Raised when an IPMI command fails"""
    pass

class IPMICommander:
    """Handles IPMI command execution and fan control operations"""

    # Known dangerous commands that should never be executed
    BLACKLISTED_COMMANDS = {
        # Commands that affect fan/sensor behavior
        (0x06, 0x01),  # Get supported commands - causes fans to drop speed
        (0x06, 0x02),  # Get OEM commands - may affect sensor readings
    }

    # IPMI raw commands for different board generations
    COMMANDS = {
        # Common commands across generations
        "GET_BOARD_ID": "mc info",
        "GET_DMI_INFO": "sudo dmidecode -t baseboard",
        
        # Fan control commands by generation
        MotherboardGeneration.X9: {
            "SET_MANUAL_MODE": "raw 0x30 0x45 0x01 0x01",
            "SET_AUTO_MODE": "raw 0x30 0x45 0x01 0x00",
            "SET_FAN_SPEED": "raw 0x30 0x91 0x5A 0x3 0x10",  # Append hex speed
        },
        MotherboardGeneration.X10: {
            "SET_MANUAL_MODE": "raw 0x30 0x45 0x01 0x01",
            "SET_AUTO_MODE": "raw 0x30 0x45 0x01 0x00",
            "SET_FAN_SPEED": "raw 0x30 0x70 0x66 0x01 0x00",  # Append hex speed
        },
        # X11/H12 use same commands as X10
        MotherboardGeneration.X11: {
            "SET_MANUAL_MODE": "raw 0x30 0x45 0x01 0x01",
            "SET_AUTO_MODE": "raw 0x30 0x45 0x01 0x00",
            "SET_FAN_SPEED": "raw 0x30 0x70 0x66 0x01 0x00",  # Append hex speed
        },
        MotherboardGeneration.H12: {
            "SET_MANUAL_MODE": "raw 0x30 0x45 0x01 0x01",
            "SET_AUTO_MODE": "raw 0x30 0x45 0x01 0x00",
            "SET_FAN_SPEED": "raw 0x30 0x70 0x66 0x01",  # H12 uses different command sequence
            "GET_FAN_MODE": "raw 0x30 0x45 0x00",  # Get current fan control mode
        },
        MotherboardGeneration.X13: {
            "SET_MANUAL_MODE": "raw 0x30 0x45 0x01 0x01",
            "SET_AUTO_MODE": "raw 0x30 0x45 0x01 0x00",
            "SET_FAN_SPEED": "raw 0x30 0x70 0x66 0x01 0x00",  # Append hex speed
        }
    }

    def __init__(self, config_path: str, host: str = "localhost", username: str = "ADMIN",
                 password: str = "ADMIN", interface: str = "lanplus"):
        """Initialize IPMI commander with connection details

        Args:
            config_path: Path to configuration file
            host: IPMI host address
            username: IPMI username
            password: IPMI password
            interface: IPMI interface type
        """
        self.host = host
        self.username = username
        self.password = password
        self.interface = interface
        self.board_gen: Optional[MotherboardGeneration] = None
        
        # Load configuration
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        
        # Detect board generation on init
        self.detect_board_generation()

    def _validate_raw_command(self, command: str) -> None:
        """Validate a raw IPMI command for safety

        Args:
            command: Raw IPMI command string

        Raises:
            IPMIError: If command is blacklisted or invalid
        """
        # Parse raw command format (e.g., "raw 0x30 0x45 0x01 0x01")
        parts = command.split()
        if len(parts) < 3 or parts[0] != "raw":
            return  # Not a raw command, skip validation
            
        # First validate hex format before trying to convert
        for p in parts[1:3]:
            # Remove 0x prefix if present
            hex_val = p[2:] if p.startswith('0x') else p
            # Check if remaining string is valid hex
            if not all(c in '0123456789abcdefABCDEF' for c in hex_val):
                raise IPMIError("Invalid command format: malformed hex value")
            
        try:
            # Convert hex strings to integers
            netfn = int(parts[1], 16)
            cmd = int(parts[2], 16)
            
            # Check against blacklist
            if (netfn, cmd) in self.BLACKLISTED_COMMANDS:
                raise IPMIError(f"Command {hex(netfn)} {hex(cmd)} is blacklisted for safety")
                
            # Additional safety checks for fan control
            if netfn == 0x30:  # Fan control commands
                if cmd == 0x45:  # Mode control
                    if len(parts) >= 5:
                        mode = int(parts[4], 16)
                        if mode not in [0x00, 0x01]:  # Only allow get mode and manual/auto
                            raise IPMIError(f"Invalid fan mode: {hex(mode)}")
                elif cmd in [0x70, 0x91]:  # Fan speed control
                    if len(parts) >= 7:
                        speed = int(parts[-1], 16)
                        if speed < 0x04:  # Minimum 2% (0x04)
                            raise IPMIError(f"Fan speed too low: {hex(speed)}")
                            
        except ValueError as e:
            raise IPMIError(f"Invalid command format: {e}")

    def _execute_ipmi_command(self, command: str, retries: int = 3, retry_delay: float = 1.0) -> str:
        """Execute an IPMI command and return its output

        Args:
            command: IPMI command to execute
            retries: Number of retry attempts
            retry_delay: Delay between retries in seconds

        Returns:
            Command output as string

        Raises:
            IPMIConnectionError: If connection fails
            IPMICommandError: If command execution fails
            IPMIError: If command is invalid or unsafe
        """
        # Validate command safety
        self._validate_raw_command(command)
        
        # For local access, just use ipmitool
        if self.host == "localhost":
            base_cmd = ["ipmitool"]
        else:
            # For remote access, include connection parameters
            base_cmd = [
                "ipmitool", "-I", self.interface,
                "-H", self.host,
                "-U", self.username,
                "-P", self.password
            ]
        
        last_error = None
        for attempt in range(retries):
            if attempt > 0:
                time.sleep(retry_delay)
                logger.debug(f"Retrying IPMI command (attempt {attempt + 1}/{retries})")
                
            full_cmd = base_cmd + command.split()
            try:
                result = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    text=True,
                    check=True
                )
                # Add delay after each successful command
                time.sleep(2)
                return result.stdout.strip()
            except subprocess.CalledProcessError as e:
                last_error = e
                if "Device or resource busy" in e.stderr:
                    logger.debug(f"IPMI device busy, retrying... ({attempt + 1}/{retries})")
                    continue
                if "Error in open session" in e.stderr:
                    raise IPMIConnectionError(f"Failed to connect to IPMI: {e.stderr}")
                if attempt == retries - 1:  # Last attempt
                    raise IPMICommandError(f"Command failed after {retries} attempts: {e.stderr}")
            except Exception as e:
                last_error = e
                if attempt == retries - 1:  # Last attempt
                    raise IPMIError(f"Unexpected error after {retries} attempts: {str(e)}")
                
        # If we get here, all retries failed
        raise IPMIError(f"Command failed after {retries} attempts: {str(last_error)}")

    def detect_board_generation(self) -> MotherboardGeneration:
        """Detect the Supermicro motherboard generation

        Returns:
            Detected motherboard generation

        Raises:
            IPMIError: If board generation cannot be determined
        """
        try:
            # First try dmidecode for most accurate information
            try:
                dmi_output = subprocess.run(
                    ["sudo", "dmidecode", "-t", "baseboard"],
                    capture_output=True,
                    text=True,
                    check=True
                ).stdout.lower()
                
                # Check for H12 series explicitly
                if "h12" in dmi_output:
                    self.board_gen = MotherboardGeneration.H12
                    logger.info("Detected H12 series board via DMI")
                    return self.board_gen
                
            except subprocess.CalledProcessError:
                logger.warning("Failed to get DMI info, falling back to IPMI detection")
            
            # Fall back to IPMI detection
            output = self._execute_ipmi_command(self.COMMANDS["GET_BOARD_ID"])
            board_info = output.lower()
            
            # Try different detection methods
            if any(x in board_info for x in ["x13", "h13", "b13"]):
                self.board_gen = MotherboardGeneration.X13
            elif any(x in board_info for x in ["h12", "b12"]):
                self.board_gen = MotherboardGeneration.H12
            elif any(x in board_info for x in ["x11", "h11", "b11"]):
                self.board_gen = MotherboardGeneration.X11
            elif any(x in board_info for x in ["x10", "h10", "b10"]):
                self.board_gen = MotherboardGeneration.X10
            elif any(x in board_info for x in ["x9", "h9", "b9"]):
                self.board_gen = MotherboardGeneration.X9
            else:
                # Try to detect from firmware version
                for line in output.splitlines():
                    if "Firmware Revision" in line:
                        version = line.lower()
                        if "3." in version:
                            self.board_gen = MotherboardGeneration.X13
                        elif "2." in version:
                            self.board_gen = MotherboardGeneration.X11
                        elif "1." in version:
                            self.board_gen = MotherboardGeneration.X10
                        break
                
            if not self.board_gen:
                self.board_gen = MotherboardGeneration.UNKNOWN
                raise IPMIError("Could not determine board generation")
                
            logger.info(f"Detected board generation: {self.board_gen.value}")
            return self.board_gen
            
        except Exception as e:
            self.board_gen = MotherboardGeneration.UNKNOWN
            raise IPMIError(f"Failed to detect board generation: {str(e)}")

    def get_fan_mode(self) -> bool:
        """Get current fan control mode
        
        Returns:
            True if in manual mode, False if in automatic mode
        
        Raises:
            IPMIError: If mode cannot be determined
        """
        try:
            result = self._execute_ipmi_command("raw 0x30 0x45 0x00")
            # Returns "01" for manual mode, "00" for auto mode
            return result.strip() == "01"
        except Exception as e:
            raise IPMIError(f"Failed to get fan mode: {e}")

    def set_manual_mode(self) -> None:
        """Set fan control to manual mode"""
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
            
        command = self.COMMANDS[self.board_gen]["SET_MANUAL_MODE"]
        self._execute_ipmi_command(command)
        
        # Verify mode change
        if not self.get_fan_mode():
            raise IPMIError("Failed to enter manual mode")
            
        logger.info("Fan control set to manual mode")

    def set_auto_mode(self) -> None:
        """Restore automatic fan control"""
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
            
        command = self.COMMANDS[self.board_gen]["SET_AUTO_MODE"]
        self._execute_ipmi_command(command)
        
        # Verify mode change
        if self.get_fan_mode():
            raise IPMIError("Failed to enter automatic mode")
            
        logger.info("Fan control restored to automatic mode")

    def set_fan_speed(self, speed_percent: int, zone: str = "chassis") -> None:
        """Set fan speed as percentage for a specific zone

        Args:
            speed_percent: Fan speed percentage (0-100)
            zone: Fan zone ("chassis" or "cpu")

        Raises:
            ValueError: If speed_percent is out of range or invalid zone
            IPMIError: If command fails
        """
        if not 0 <= speed_percent <= 100:
            raise ValueError("Fan speed must be between 0 and 100")
            
        if zone not in ["chassis", "cpu"]:
            raise ValueError("Zone must be 'chassis' or 'cpu'")
            
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
        
        # Ensure minimum speed of 20% for H12 boards, 5% for others
        min_speed = 20 if self.board_gen == MotherboardGeneration.H12 else 5
        speed_percent = max(min_speed, speed_percent)
        
        # Convert percentage to hex value (0-255)
        hex_val = int(speed_percent * 255 / 100)
        hex_val = max(51, min(255, hex_val))  # Ensure between 0x33 (20%) and 0xFF
        hex_speed = format(hex_val, '02x')
        
        # Get base command
        base_command = self.COMMANDS[self.board_gen]["SET_FAN_SPEED"]
        
        # Set zone ID (0x00 for chassis, 0x01 for CPU)
        zone_id = "0x01" if zone == "cpu" else "0x00"
        
        # For H12, use verified command format with proper speed steps
        if self.board_gen == MotherboardGeneration.H12:
            try:
                # Get board configuration from config file
                board_config = self.config["fans"]["board_config"]
                speed_steps = board_config["speed_steps"]
                
                # H12 Fan speed steps:
                # 0xFF (100%):  FAN1=1120, FAN2-4=980, FAN5=1260
                # 0x60 (37.5%): FAN1=1680, FAN2-4=1400, FAN5=1820
                # 0x40 (25%):   FAN1=1260, FAN2-4=980, FAN5=1260
                # 0x20 (12.5%): FAN1=980, FAN2-4=840, FAN5=1260
                
                # Map to closest step based on actual board values
                if speed_percent < 25:
                    hex_speed = "20"  # 12.5% step
                    step_name = "low"
                    actual_percent = 12.5
                elif speed_percent < 37.5:
                    hex_speed = "40"  # 25% step
                    step_name = "medium"
                    actual_percent = 25
                elif speed_percent < 50:
                    hex_speed = "60"  # 37.5% step
                    step_name = "high"
                    actual_percent = 37.5
                else:
                    hex_speed = "ff"  # 100% step
                    step_name = "full"
                    actual_percent = 100
                
                selected_step = speed_steps[step_name]
                actual_percent = selected_step["threshold"]
                logger.info(f"H12 board: Requested {speed_percent}%, using {actual_percent}% step")
                
                # H12 command format: 0x30 0x70 0x66 0x01 [zone] [speed]
                command = f"{base_command} {zone_id} 0x{hex_speed}"
                self._execute_ipmi_command(command)
                
                # Wait for fans to stabilize
                time.sleep(2)
                
                # Verify fans are working
                new_readings = self.get_sensor_readings()
                new_fans = [r for r in new_readings if r["name"].startswith("FAN")]
                working_fans = [f for f in new_fans if f["value"] is not None and f["value"] > 0]
                
                if len(working_fans) < 2:
                    logger.error("Insufficient working fans after speed change")
                    self.set_auto_mode()
                    raise IPMIError("Fan speed change failed - insufficient working fans")
                
                # Verify fans are working
                for fan in working_fans:
                    zone = "cpu" if fan["name"].startswith("FANA") else "chassis"
                    rpm = fan["value"]
                    
                    # Get RPM range for current step
                    rpm_range = selected_step["rpm_ranges"][zone]
                    min_rpm = rpm_range["min"] * 0.8  # Allow 20% below minimum
                    
                    if rpm < min_rpm:
                        logger.warning(f"{fan['name']} RPM ({rpm}) below minimum ({min_rpm})")
                
            except Exception as e:
                logger.error(f"Failed to set fan speed: {e}")
                self.set_auto_mode()
                raise
        else:
            # For other boards, use standard command format
            command = f"{base_command} {zone_id} 0x{hex_speed}"
            self._execute_ipmi_command(command)
        
        logger.info(f"{zone.title()} fan speed set to {speed_percent}%")

    def get_sensor_readings(self) -> List[Dict[str, Union[str, float, int]]]:
        """Get temperature sensor readings

        Returns:
            List of sensor readings with name, value, state and response ID
        """
        output = self._execute_ipmi_command("sdr list")
        readings = []
        current_reading = None
        
        for line in output.splitlines():
            # Check for response ID message that applies to previous reading
            if "Received a response with unexpected ID" in line and current_reading:
                try:
                    response_id = int(line.split()[-1])
                    current_reading["response_id"] = response_id
                    logger.warning(f"Unexpected IPMI response ID {response_id} for sensor {current_reading['name']}")
                except (ValueError, IndexError):
                    pass
                continue
            
            parts = line.split('|')
            if len(parts) >= 3:  # We need at least name, value, and state
                try:
                    name = parts[0].strip()
                    value_part = parts[1].strip()
                    state = parts[2].strip().lower()
                    
                    # Parse value if present and state is not 'ns'
                    value = None
                    if state != 'ns':  # Only parse value if not "no reading"
                        # Clean up value string and handle Kelvin format
                        value_str = value_part.split('(')[0]  # Take part before Kelvin
                        value_str = value_str.replace('°', '').replace('degrees', '').replace('C', '').replace('RPM', '').strip()
                        try:
                            value = float(value_str)
                        except (ValueError, IndexError):
                            state = 'ns'  # Mark as no reading if value parse fails
                    
                    current_reading = {
                        "name": name,
                        "value": value,
                        "state": state,  # 'ok', 'cr', or 'ns'
                        "response_id": None
                    }
                    
                    readings.append(current_reading)
                    
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse sensor reading: {line} - {str(e)}")
                    continue
                    
        return readings

    def verify_fan_speed(self, target_speed: int, tolerance: int = 10) -> bool:
        """Verify fan speeds are near the target value

        Args:
            target_speed: Target speed percentage
            tolerance: Acceptable percentage deviation

        Returns:
            True if fans are operating within tolerance
        """
        readings = self.get_sensor_readings()
        fan_readings = [r for r in readings if r["name"].startswith("FAN")]
        
        if not fan_readings:
            logger.error("No fan readings available")
            return False
            
        # Get board configuration
        board_config = self.config["fans"]["board_config"]
        speed_steps = board_config["speed_steps"]
        
        # Find appropriate speed step for target speed
        current_step = None
        for step_name, step_info in speed_steps.items():
            if target_speed <= step_info["threshold"]:
                current_step = step_info
                break
        else:
            current_step = speed_steps["full"]
        
        working_fans = 0
        for fan in fan_readings:
            if fan["state"] == "ns" or fan["value"] is None:
                continue
                
            # Determine fan zone
            zone = "cpu" if fan["name"].startswith("FANA") else "chassis"
            rpm = fan["value"]
            
            # Get RPM range for this step and zone
            rpm_range = current_step["rpm_ranges"][zone]
            min_rpm = rpm_range["min"] * (1 - tolerance/100.0)  # Allow for tolerance
            
            if rpm >= min_rpm:
                working_fans += 1
            else:
                logger.warning(f"{fan['name']} RPM ({rpm}) below expected minimum ({min_rpm})")
                
        # Require at least 2 working fans
        min_working = 2
        if working_fans < min_working:
            logger.error(f"Insufficient working fans: {working_fans} < {min_working}")
            return False
            
        return True
