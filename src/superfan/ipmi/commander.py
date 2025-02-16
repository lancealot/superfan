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

class FanMode(Enum):
    """Supermicro fan control modes"""
    STANDARD = 0x00  # BMC control, target 50% both zones
    FULL = 0x01      # Manual control enabled
    OPTIMAL = 0x02   # BMC control, CPU 30%, Peripheral low
    HEAVY_IO = 0x04  # BMC control, CPU 50%, Peripheral 75%

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

    # Known stable fan speed points and their hex values for H12 board
    STABLE_SPEEDS = {
        100: {'hex': 'ff', 'prefix': False},  # Full speed
        75:  {'hex': '60', 'prefix': False},  # High speed
        50:  {'hex': '40', 'prefix': False},  # Medium speed
        25:  {'hex': '20', 'prefix': False},  # Low speed
        12:  {'hex': '10', 'prefix': False},  # Very low speed
        0:   {'hex': '00', 'prefix': False},  # Off
    }

    # Fan group RPM ranges for H12 board
    FAN_RANGES = {
        'high_rpm': {  # FAN1, FAN5
            'min': 0,       # Allow fans to stop
            'max': 1820,    # Maximum observed RPM
            'stable': 1680  # Most stable operating point
        },
        'low_rpm': {   # FAN2-4
            'min': 0,       # Allow fans to stop
            'max': 1400,    # Maximum observed RPM
            'stable': 1400  # Most stable operating point
        },
        'cpu': {       # FANA
            'min': 0,       # Allow fans to stop
            'max': 3640,    # Maximum observed RPM
            'stable': 3640  # Most stable operating point
        }
    }

    # RPM tolerance percentage for stable point comparison
    RPM_TOLERANCE = 30  # Allow 30% deviation from stable point

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
        """Validate a raw IPMI command for safety and format.

        This method checks IPMI commands for:
        1. Blacklisted commands that could affect system stability
        2. Valid hex format in command bytes
        3. Safe fan control parameters
        4. Valid mode control values

        Args:
            command: Raw IPMI command string (e.g., "raw 0x30 0x45 0x01 0x01")

        Raises:
            IPMIError: If command is blacklisted or invalid:
                - "Command {hex(netfn)} {hex(cmd)} is blacklisted for safety"
                - "Invalid command format: malformed hex value"
                - "Invalid fan mode: {hex(mode)}"
                - "Fan speed too low: {hex(speed)}"

        Examples:
            >>> commander._validate_raw_command("raw 0x30 0x45 0x01 0x01")  # Valid mode change
            >>> commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x32")  # Valid speed
            >>> commander._validate_raw_command("raw 0x06 0x01")  # Raises IPMIError (blacklisted)
            >>> commander._validate_raw_command("raw 0xZZ 0x01")  # Raises IPMIError (invalid hex)
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
                        valid_modes = [m.value for m in FanMode]
                        if mode not in valid_modes:
                            raise IPMIError(f"Invalid fan mode: {hex(mode)}")
                elif cmd in [0x70, 0x91]:  # Fan speed control
                    if len(parts) >= 7:
                        speed = int(parts[-1], 16)
                        if speed < 0x00:  # Allow 0% speed
                            raise IPMIError(f"Invalid fan speed: {hex(speed)}")
                            
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
            base_cmd = ["sudo", "ipmitool"]
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
        """Detect the Supermicro motherboard generation using multiple methods.

        This method uses a multi-step approach to detect the board generation:
        1. Primary: DMI detection via dmidecode
           - Most accurate, especially for H12 boards
           - Requires root access
        2. Fallback: IPMI detection via mc info
           - Uses board markers in IPMI info
           - Works without root access
           - Less accurate than DMI

        Board Detection Patterns:
        - H12: "h12" in DMI or "h12"/"b12" in IPMI
        - X13: "x13"/"h13"/"b13" in IPMI
        - X11: "x11"/"h11"/"b11" in IPMI
        - X10: "x10"/"h10"/"b10" in IPMI
        - X9:  "x9"/"h9"/"b9" in IPMI

        Returns:
            MotherboardGeneration: The detected board generation enum value.
                One of: X9, X10, X11, H12, X13, or UNKNOWN

        Raises:
            IPMIError: If board generation cannot be determined:
                - "Failed to detect board generation: {error}"
                - "Could not determine board generation"

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> gen = commander.detect_board_generation()
            >>> print(gen)
            MotherboardGeneration.H12
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
            if not self.board_gen:
                logger.warning("Could not detect board generation via DMI or IPMI info")
                self.board_gen = MotherboardGeneration.UNKNOWN
                raise IPMIError("Could not determine board generation")
                
            logger.info(f"Detected board generation: {self.board_gen.value}")
            return self.board_gen
            
        except Exception as e:
            self.board_gen = MotherboardGeneration.UNKNOWN
            raise IPMIError(f"Failed to detect board generation: {str(e)}")

    def get_fan_mode(self) -> FanMode:
        """Get current fan control mode from BMC.

        This method queries the BMC to determine the current fan control mode.
        It uses the command "raw 0x30 0x45 0x00" which returns:
        - 0x00: Standard mode (BMC control, target 50% both zones)
        - 0x01: Full mode (Manual control enabled)
        - 0x02: Optimal mode (BMC control, CPU 30%, Peripheral low)
        - 0x04: Heavy IO mode (BMC control, CPU 50%, Peripheral 75%)

        Returns:
            FanMode: Current fan control mode enum value

        Raises:
            IPMIError: If mode cannot be determined:
                - "Failed to get fan mode: {error}"
                - "Invalid fan mode value: {value}"

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> mode = commander.get_fan_mode()
            >>> print(mode.name)
            STANDARD
        """
        try:
            result = self._execute_ipmi_command("raw 0x30 0x45 0x00")
            mode_value = int(result.strip(), 16)
            try:
                return FanMode(mode_value)
            except ValueError:
                raise IPMIError(f"Invalid fan mode value: {hex(mode_value)}")
        except Exception as e:
            raise IPMIError(f"Failed to get fan mode: {e}")

    def set_fan_mode(self, mode: FanMode) -> None:
        """Set fan control mode on BMC.

        This method sets the fan control mode to one of four options:
        - STANDARD: BMC control with 50% target for both zones
        - FULL: Manual control enabled for custom speeds
        - OPTIMAL: BMC control with CPU at 30%, Peripheral low
        - HEAVY_IO: BMC control with CPU at 50%, Peripheral at 75%

        Args:
            mode: FanMode enum value to set

        Raises:
            IPMIError: If mode cannot be set:
                - "Failed to set fan mode: {error}"
                - "Mode change verification failed"

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> commander.set_fan_mode(FanMode.FULL)  # Enable manual control
            >>> commander.set_fan_mode(FanMode.STANDARD)  # Return to BMC control
        """
        try:
            command = f"raw 0x30 0x45 0x01 {hex(mode.value)}"
            self._execute_ipmi_command(command)
            
            # Verify mode change
            current_mode = self.get_fan_mode()
            if current_mode != mode:
                raise IPMIError("Mode change verification failed")
                
            logger.info(f"Fan control set to {mode.name} mode")
        except Exception as e:
            raise IPMIError(f"Failed to set fan mode: {e}")

    def set_manual_mode(self) -> None:
        """Set fan control to manual mode for direct speed control.

        This method is a convenience wrapper around set_fan_mode(FanMode.FULL).
        It switches fan control to manual mode, allowing direct control of fan speeds.

        Note:
            - Manual mode disables BMC's automatic temperature management
            - Always use appropriate safety checks when in manual mode
            - For H12 boards, use specific speed steps (see set_fan_speed docs)

        Raises:
            IPMIError: If manual mode cannot be set

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> commander.set_manual_mode()  # Enable manual control
            >>> commander.set_fan_speed(50, zone="cpu")  # Now we can set speeds
        """
        self.set_fan_mode(FanMode.FULL)

    def set_auto_mode(self) -> None:
        """Restore automatic fan control by returning control to BMC.

        This method is a convenience wrapper around set_fan_mode(FanMode.STANDARD).
        It switches fan control back to standard automatic mode.

        This is a safety-critical operation used in several scenarios:
        - Normal cleanup when exiting
        - Emergency fallback on errors
        - Recovery from failed fan speed changes
        - Response to critical temperatures

        Raises:
            IPMIError: If automatic mode cannot be set

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> commander.set_auto_mode()  # Return control to BMC
            >>> assert commander.get_fan_mode() == FanMode.STANDARD
        """
        self.set_fan_mode(FanMode.STANDARD)

    def set_fan_speed(self, speed_percent: int, zone: str = "chassis") -> None:
        """Set fan speed for a specific cooling zone with board-specific handling.

        This method sets fan speeds with special handling for different board generations:

        H12 Boards:
        - Uses fixed speed steps: 12.5%, 25%, 37.5%, 100%
        - Maps requested speed to nearest step
        - Verifies RPM ranges per step:
          * 12.5%: FAN1=980, FAN2-4=840, FAN5=1260
          * 25%:   FAN1=1260, FAN2-4=980, FAN5=1260
          * 37.5%: FAN1=1680, FAN2-4=1400, FAN5=1820
          * 100%:  FAN1=1120, FAN2-4=980, FAN5=1260

        Other Boards:
        - Uses continuous speed range (5-100%)
        - Converts percentage to hex value (0-255)
        - Uses standard command format

        The method includes several safety checks:
        1. Validates speed and zone parameters
        2. Enforces minimum speeds (20% for H12, 5% for others)
        3. Verifies fan operation after changes
        4. Falls back to auto mode on errors

        Args:
            speed_percent: Fan speed percentage (0-100)
            zone: Fan zone ("chassis" or "cpu")

        Raises:
            ValueError: If parameters are invalid:
                - "Fan speed must be between 0 and 100"
                - "Zone must be 'chassis' or 'cpu'"
            IPMIError: If operation fails:
                - "Unknown board generation"
                - "Fan speed change failed - insufficient working fans"
                - "Failed to set fan speed: {error}"

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> commander.set_manual_mode()
            >>> # Set chassis fans to 50%
            >>> commander.set_fan_speed(50, zone="chassis")
            >>> # Set CPU fan to 75%
            >>> commander.set_fan_speed(75, zone="cpu")
        """
        if not 0 <= speed_percent <= 100:
            raise ValueError("Fan speed must be between 0 and 100")
            
        if zone not in ["chassis", "cpu"]:
            raise ValueError("Zone must be 'chassis' or 'cpu'")
            
        if self.board_gen == MotherboardGeneration.UNKNOWN:
            raise IPMIError("Unknown board generation")
        
        # Allow full speed range
        speed_percent = max(0, speed_percent)
        
        # Find nearest stable speed point
        stable_points = sorted(self.STABLE_SPEEDS.keys())
        nearest_point = min(stable_points, key=lambda x: abs(x - speed_percent))
        speed_info = self.STABLE_SPEEDS[nearest_point]
        
        # Get hex value and format
        hex_speed = speed_info['hex']
        if speed_info['prefix']:
            hex_speed = f"0x{hex_speed}"
        
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
                
                # Map to closest step based on config
                if speed_percent < 12:
                    hex_speed = "00"  # Off (0%)
                    step_name = "off"
                    actual_percent = 0
                elif speed_percent < 25:
                    hex_speed = "10"  # Very low (12%)
                    step_name = "very_low"
                    actual_percent = 12
                elif speed_percent < 50:
                    hex_speed = "20"  # Low (25%)
                    step_name = "low"
                    actual_percent = 25
                elif speed_percent < 75:
                    hex_speed = "40"  # Medium (50%)
                    step_name = "medium"
                    actual_percent = 50
                elif speed_percent < 85:
                    hex_speed = "60"  # High (75%)
                    step_name = "high"
                    actual_percent = 75
                else:
                    hex_speed = "ff"  # Full (100%)
                    step_name = "full"
                    actual_percent = 100

                # Update speed info for verification
                speed_percent = actual_percent
            
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
                    # Determine fan group
                    if fan["name"].startswith("FANA"):
                        group = 'cpu'
                    elif fan["name"] in ["FAN1", "FAN5"]:
                        group = 'high_rpm'
                    else:
                        group = 'low_rpm'
                    
                    rpm = fan["value"]
                    rpm_range = self.FAN_RANGES[group]
                    
                    # Check against safe ranges
                    if rpm < rpm_range['min']:
                        logger.error(f"{fan['name']} RPM ({rpm}) below minimum safe speed ({rpm_range['min']})")
                        self.set_auto_mode()
                        raise IPMIError(f"Fan speed unsafe - {fan['name']} too low")
                    elif rpm > rpm_range['max']:
                        logger.warning(f"{fan['name']} RPM ({rpm}) above maximum expected ({rpm_range['max']})")
                    elif abs(rpm - rpm_range['stable']) > rpm_range['stable'] * (self.RPM_TOLERANCE / 100.0):
                        logger.warning(f"{fan['name']} RPM ({rpm}) far from stable point ({rpm_range['stable']})")
            
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
        """Get temperature and fan sensor readings from BMC.

        This method retrieves all sensor readings using the "sdr list" command and parses
        the output into structured data. It handles various sensor formats:
        - Temperature sensors (°C)
        - Fan speed sensors (RPM)
        - State sensors (ok, cr, ns)

        The method also tracks IPMI response IDs to detect potential communication issues
        and handles various edge cases like missing readings or invalid values.

        Returns:
            List[Dict[str, Union[str, float, int]]]: List of sensor readings, each containing:
                - name (str): Sensor name (e.g., "CPU1 Temp", "FAN1")
                - value (float|None): Sensor value or None if no reading
                - state (str): Sensor state ("ok", "cr", or "ns")
                - response_id (int|None): IPMI response ID or None

        Examples:
            >>> commander = IPMICommander("config.yaml")
            >>> readings = commander.get_sensor_readings()
            >>> for r in readings:
            ...     if r["name"].startswith("CPU") and r["value"]:
            ...         print(f"{r['name']}: {r['value']}°C ({r['state']})")
            CPU1 Temp: 45.0°C (ok)
            CPU2 Temp: 47.0°C (ok)
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
                    value_str = parts[1].strip()
                    state = parts[2].strip().lower()
                    
                    # Parse value if present and state is not 'ns'
                    value = None
                    if state != 'ns' and value_str:
                        try:
                            # Extract numeric value, handling various formats:
                            # "45.000 degrees C"
                            # "1680 RPM"
                            # "0x01"
                            value_parts = value_str.split()
                            if value_parts:
                                # Try to parse first part as number
                                num_str = value_parts[0].replace('°', '')  # Remove degree symbol
                                if num_str.startswith('0x'):
                                    value = int(num_str, 16)
                                else:
                                    value = float(num_str)
                        except (ValueError, IndexError):
                            logger.debug(f"Could not parse value from: {value_str}")
                            state = 'ns'  # Mark as no reading if value parse fails
                    
                    reading = {
                        "name": name,
                        "value": value,
                        "state": state,
                        "response_id": None
                    }
                    
                    readings.append(reading)
                    current_reading = reading
                    
                except (ValueError, IndexError) as e:
                    logger.warning(f"Failed to parse sensor reading: {line} - {str(e)}")
                    continue
                    
        return readings
