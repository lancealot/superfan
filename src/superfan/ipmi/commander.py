"""
IPMI Command Execution Module

This module provides a wrapper around ipmitool for executing IPMI commands
and managing fan control on Supermicro servers.
"""

import subprocess
import logging
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
            "SET_FAN_SPEED": "raw 0x30 0x70 0x66 0x01 0x00",  # Append hex speed
        },
        MotherboardGeneration.X13: {
            "SET_MANUAL_MODE": "raw 0x30 0x45 0x01 0x01",
            "SET_AUTO_MODE": "raw 0x30 0x45 0x01 0x00",
            "SET_FAN_SPEED": "raw 0x30 0x70 0x66 0x01 0x00",  # Append hex speed
        }
    }

    def __init__(self, host: str = "localhost", username: str = "ADMIN",
                 password: str = "ADMIN", interface: str = "lanplus"):
        """Initialize IPMI commander with connection details

        Args:
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
                        if speed < 0x19:  # Minimum 10% (0x19)
                            raise IPMIError(f"Fan speed too low: {hex(speed)}")
                            
        except ValueError as e:
            raise IPMIError(f"Invalid command format: {e}")

    def _execute_ipmi_command(self, command: str) -> str:
        """Execute an IPMI command and return its output

        Args:
            command: IPMI command to execute

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
        
        full_cmd = base_cmd + command.split()
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            if "Error in open session" in e.stderr:
                raise IPMIConnectionError(f"Failed to connect to IPMI: {e.stderr}")
            raise IPMICommandError(f"Command failed: {e.stderr}")
        except Exception as e:
            raise IPMIError(f"Unexpected error: {str(e)}")

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

    def set_fan_speed(self, speed_percent: int) -> None:
        """Set fan speed as percentage

        Args:
            speed_percent: Fan speed percentage (0-100)

        Raises:
            ValueError: If speed_percent is out of range
            IPMIError: If command fails
        """
        if not 0 <= speed_percent <= 100:
            raise ValueError("Fan speed must be between 0 and 100")
            
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
        
        # Convert percentage to hex
        hex_speed = format(int(speed_percent * 255 / 100), '02x')
        
        # Get base command and append hex speed
        base_command = self.COMMANDS[self.board_gen]["SET_FAN_SPEED"]
        command = f"{base_command} 0x{hex_speed}"
        
        self._execute_ipmi_command(command)
        logger.info(f"Fan speed set to {speed_percent}%")

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
            
        # Convert target speed percentage to expected RPM range
        # This is a rough approximation - would need calibration per system
        base_rpm = 1800  # Typical max RPM
        expected_rpm = (target_speed / 100.0) * base_rpm
        min_rpm = expected_rpm * (1 - tolerance/100.0)
        
        working_fans = 0
        for fan in fan_readings:
            if fan["state"] == "ns":
                continue
                
            rpm = fan["value"]
            if rpm is not None and rpm > min_rpm:
                working_fans += 1
                
        # Require at least 2 working fans
        return working_fans >= 2
