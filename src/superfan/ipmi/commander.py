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

    # IPMI raw commands for different board generations
    COMMANDS = {
        # Common commands across generations
        "GET_BOARD_ID": "mc info",
        
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
        # X11 uses same commands as X10
        MotherboardGeneration.X11: {
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

    def _execute_ipmi_command(self, command: str) -> str:
        """Execute an IPMI command and return its output

        Args:
            command: IPMI command to execute

        Returns:
            Command output as string

        Raises:
            IPMIConnectionError: If connection fails
            IPMICommandError: If command execution fails
        """
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
            output = self._execute_ipmi_command(self.COMMANDS["GET_BOARD_ID"])
            
            # Extract board info from output
            for line in output.splitlines():
                if "Product ID" in line:
                    # Parse product ID to determine generation
                    if "X9" in line:
                        self.board_gen = MotherboardGeneration.X9
                    elif "X10" in line:
                        self.board_gen = MotherboardGeneration.X10
                    elif "X11" in line:
                        self.board_gen = MotherboardGeneration.X11
                    elif "X13" in line:
                        self.board_gen = MotherboardGeneration.X13
                    else:
                        self.board_gen = MotherboardGeneration.UNKNOWN
                    break
            
            if not self.board_gen:
                self.board_gen = MotherboardGeneration.UNKNOWN
                raise IPMIError("Could not determine board generation")
                
            return self.board_gen
            
        except Exception as e:
            self.board_gen = MotherboardGeneration.UNKNOWN
            raise IPMIError(f"Failed to detect board generation: {str(e)}")

    def set_manual_mode(self) -> None:
        """Set fan control to manual mode"""
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
            
        command = self.COMMANDS[self.board_gen]["SET_MANUAL_MODE"]
        self._execute_ipmi_command(command)
        logger.info("Fan control set to manual mode")

    def set_auto_mode(self) -> None:
        """Restore automatic fan control"""
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
            
        command = self.COMMANDS[self.board_gen]["SET_AUTO_MODE"]
        self._execute_ipmi_command(command)
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

    def get_sensor_readings(self) -> List[Dict[str, Union[str, float]]]:
        """Get temperature sensor readings

        Returns:
            List of sensor readings with name and value
        """
        output = self._execute_ipmi_command("sdr list")
        readings = []
        
        for line in output.splitlines():
            parts = line.split('|')
            if len(parts) >= 3 and "degrees C" in line:
                try:
                    name = parts[0].strip()
                    value = float(parts[1].strip().split()[0])
                    readings.append({
                        "name": name,
                        "value": value
                    })
                except (ValueError, IndexError):
                    continue
                    
        return readings
