"""
IPMI Commander Tests

This module contains tests for the IPMI commander functionality.
"""

import subprocess
import pytest
from unittest.mock import patch, MagicMock
from superfan.ipmi.commander import (
    IPMICommander,
    IPMIError,
    IPMIConnectionError,
    IPMICommandError,
    MotherboardGeneration
)

# Fixtures

@pytest.fixture
def mock_subprocess():
    """Mock subprocess for command execution"""
    with patch('subprocess.run') as mock_run:
        # Setup default responses for board detection
        def mock_run_command(cmd, *args, **kwargs):
            if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
                return MagicMock(
                    stdout="Base Board Information\n\tProduct Name: X13DPH-T",
                    stderr="",
                    returncode=0
                )
            elif cmd == ["ipmitool", "mc", "info"]:
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            return MagicMock(stdout="Success", stderr="", returncode=0)
        
        mock_run.side_effect = mock_run_command
        yield mock_run

@pytest.fixture
def commander(mock_subprocess):
    """Create a basic IPMICommander instance"""
    with patch('superfan.ipmi.commander.subprocess.run', mock_subprocess):
        return IPMICommander()

# Command Validation Tests

def test_validate_raw_command_valid_formats(commander):
    """Test validation of valid command formats"""
    # Standard raw command
    commander._validate_raw_command("raw 0x30 0x45 0x01 0x01")
    
    # Non-raw command (should skip validation)
    commander._validate_raw_command("sdr list")
    
    # Fan control commands
    commander._validate_raw_command("raw 0x30 0x45 0x01 0x00")  # Set auto mode
    commander._validate_raw_command("raw 0x30 0x45 0x01 0x01")  # Set manual mode
    commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x7f")  # Set fan speed

def test_validate_raw_command_invalid_formats(commander):
    """Test validation of invalid command formats"""
    # Malformed hex values
    with pytest.raises(IPMIError, match="Invalid command format: malformed hex value"):
        commander._validate_raw_command("raw 0xZZ 0x45")
    
    # Missing raw prefix
    commander._validate_raw_command("sdr 0x30 0x45")  # Should not raise (non-raw command)
    
    # Invalid hex format
    with pytest.raises(IPMIError, match="Invalid command format: invalid literal for int()"):
        commander._validate_raw_command("raw 0x30 0x45 0x01 0xZZ")

def test_validate_raw_command_blacklisted(commander):
    """Test validation of blacklisted commands"""
    # Known dangerous commands
    with pytest.raises(IPMIError, match="blacklisted for safety"):
        commander._validate_raw_command("raw 0x06 0x01")  # Get supported commands
    
    with pytest.raises(IPMIError, match="blacklisted for safety"):
        commander._validate_raw_command("raw 0x06 0x02")  # Get OEM commands

def test_validate_raw_command_fan_control(commander):
    """Test validation of fan control commands"""
    # Invalid fan mode
    with pytest.raises(IPMIError, match="Invalid fan mode"):
        commander._validate_raw_command("raw 0x30 0x45 0x01 0x02")
    
    # Fan speed too low
    with pytest.raises(IPMIError, match="Fan speed too low"):
        commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x00")

# Command Execution Tests

def test_execute_ipmi_command_local(mock_subprocess, commander):
    """Test local IPMI command execution"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Override the default mock response for this test
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: X13DPH-T",
                stderr="",
                returncode=0
            )
        elif cmd == ["ipmitool", "mc", "info"]:
            return MagicMock(
                stdout="Firmware Revision : 3.88",
                stderr="",
                returncode=0
            )
        else:
            return MagicMock(
                stdout="Success output",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    result = commander._execute_ipmi_command("sdr list")
    
    # Verify command construction
    mock_subprocess.assert_called_with(
        ["ipmitool", "sdr", "list"],
        capture_output=True,
        text=True,
        check=True
    )
    assert result == "Success output"

def test_execute_ipmi_command_remote(mock_subprocess):
    """Test remote IPMI command execution"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: X13DPH-T",
                stderr="",
                returncode=0
            )
        elif cmd == ["ipmitool", "-I", "lanplus", "-H", "192.168.1.100", 
                    "-U", "admin", "-P", "secret", "mc", "info"]:
            return MagicMock(
                stdout="Board Info: X13DPH-T\nFirmware Revision : 3.88",
                stderr="",
                returncode=0
            )
        elif cmd == ["ipmitool", "-I", "lanplus", "-H", "192.168.1.100", 
                    "-U", "admin", "-P", "secret", "sdr", "list"]:
            return MagicMock(
                stdout="Success output",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander(
        host="192.168.1.100",
        username="admin",
        password="secret",
        interface="lanplus"
    )
    
    result = commander._execute_ipmi_command("sdr list")
    
    # Get the last call arguments
    last_call = mock_subprocess.call_args
    assert last_call == (
        (["ipmitool", "-I", "lanplus", "-H", "192.168.1.100", 
          "-U", "admin", "-P", "secret", "sdr", "list"],),
        {"capture_output": True, "text": True, "check": True}
    )
    assert result == "Success output"

def test_execute_ipmi_command_retry_busy(mock_subprocess, commander):
    """Test command retry on device busy"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # First call fails with device busy, second succeeds
    mock_subprocess.side_effect = [
        subprocess.CalledProcessError(1, "cmd", stderr="Device or resource busy"),
        MagicMock(stdout="Success output", stderr="", returncode=0)
    ]
    
    with patch('time.sleep') as mock_sleep:  # Mock sleep to speed up test
        result = commander._execute_ipmi_command("sdr list")
    
    # Verify retry behavior
    assert mock_subprocess.call_count == 2
    mock_sleep.assert_called_once_with(1.0)  # Verify retry delay
    assert result == "Success output"

def test_execute_ipmi_command_connection_error(mock_subprocess, commander):
    """Test connection error handling"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    mock_subprocess.side_effect = subprocess.CalledProcessError(
        1, ["cmd"], stderr="Error in open session"
    )
    
    with pytest.raises(IPMIConnectionError, match="Failed to connect to IPMI"):
        commander._execute_ipmi_command("sdr list")

def test_execute_ipmi_command_validation(commander):
    """Test command validation integration"""
    # Should raise IPMIError for blacklisted command
    with pytest.raises(IPMIError, match="blacklisted for safety"):
        commander._execute_ipmi_command("raw 0x06 0x01")

def test_execute_ipmi_command_unexpected_error(mock_subprocess, commander):
    """Test unexpected error handling"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    mock_subprocess.side_effect = Exception("Unexpected error")
    
    with pytest.raises(IPMIError, match="Unexpected error after 3 attempts"):
        commander._execute_ipmi_command("sdr list")

def test_execute_ipmi_command_max_retries(mock_subprocess, commander):
    """Test maximum retries behavior"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # All attempts fail with device busy
    mock_subprocess.side_effect = [
        subprocess.CalledProcessError(1, ["cmd"], stderr="Device or resource busy"),
        subprocess.CalledProcessError(1, ["cmd"], stderr="Device or resource busy"),
        subprocess.CalledProcessError(1, ["cmd"], stderr="Device or resource busy")
    ]
    
    with patch('time.sleep'), pytest.raises(IPMIError, match="Command failed after 3 attempts"):
        commander._execute_ipmi_command("sdr list")
    
    assert mock_subprocess.call_count == 3  # Verify all retries were attempted

# Existing Tests (preserved and organized)

def test_fan_speed_percentage_conversion(mock_subprocess):
    """Test fan speed percentage to hex conversion"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            result = MagicMock()
            result.stdout = "Base Board Information\n\tProduct Name: H12SSL-i"
            result.stderr = ""
            result.returncode = 0
            return result
        elif cmd == ["ipmitool", "mc", "info"]:
            result = MagicMock()
            result.stdout = "Firmware Revision : 3.88"
            result.stderr = ""
            result.returncode = 0
            return result
        elif "raw" in cmd:
            # Store the command for verification
            mock_run_command.last_command = cmd
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            return result
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    
    # Test standard board conversion (0-100% -> 0x00-0xFF)
    commander.board_gen = MotherboardGeneration.X13
    commander.set_fan_speed(0, zone="chassis")
    assert "0x04" in mock_run_command.last_command  # Minimum 2%
    
    commander.set_fan_speed(50, zone="chassis")
    assert "0x7f" in mock_run_command.last_command  # 50% -> ~0x7F
    
    commander.set_fan_speed(100, zone="chassis")
    assert "0xff" in mock_run_command.last_command  # 100% -> 0xFF
    
    # Test H12 board conversion (direct percentage)
    commander.board_gen = MotherboardGeneration.H12
    commander.set_fan_speed(0, zone="chassis")
    assert "0x14" in mock_run_command.last_command  # Minimum 20%
    
    commander.set_fan_speed(50, zone="chassis")
    assert "0x32" in mock_run_command.last_command  # 50% -> 0x32
    
    commander.set_fan_speed(100, zone="chassis")
    assert "0x64" in mock_run_command.last_command  # 100% -> 0x64

def test_fan_speed_command_construction(mock_subprocess):
    """Test fan speed command construction for different boards"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            result = MagicMock()
            result.stdout = "Base Board Information\n\tProduct Name: H12SSL-i"
            result.stderr = ""
            result.returncode = 0
            return result
        elif cmd == ["ipmitool", "mc", "info"]:
            result = MagicMock()
            result.stdout = "Firmware Revision : 3.88"
            result.stderr = ""
            result.returncode = 0
            return result
        elif "raw" in cmd:
            # Store the command for verification
            mock_run_command.last_command = " ".join(cmd)
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            return result
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    
    # Test X9 command format
    commander.board_gen = MotherboardGeneration.X9
    commander.set_fan_speed(50, zone="chassis")
    assert "raw 0x30 0x91 0x5A 0x3 0x10" in mock_run_command.last_command
    
    # Test X10/X11/X13 command format
    commander.board_gen = MotherboardGeneration.X13
    commander.set_fan_speed(50, zone="chassis")
    assert "raw 0x30 0x70 0x66 0x01 0x00" in mock_run_command.last_command
    
    # Test H12 command format
    commander.board_gen = MotherboardGeneration.H12
    commander.set_fan_speed(50, zone="chassis")
    assert "raw 0x30 0x91 0x5A 0x03 0x10" in mock_run_command.last_command
    
    # Test zone ID selection
    commander.set_fan_speed(50, zone="cpu")
    assert "0x11" in mock_run_command.last_command  # CPU zone
    commander.set_fan_speed(50, zone="chassis")
    assert "0x10" in mock_run_command.last_command  # Chassis zone

def test_board_detection_firmware_version(mock_subprocess):
    """Test board detection via firmware version"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            raise subprocess.CalledProcessError(1, cmd, stderr="Error")
        elif cmd == ["ipmitool", "mc", "info"]:
            if mock_run_command.case == "x13":
                return MagicMock(stdout="Firmware Revision : 3.88", stderr="", returncode=0)
            elif mock_run_command.case == "x11":
                return MagicMock(stdout="Firmware Revision : 2.45", stderr="", returncode=0)
            elif mock_run_command.case == "x10":
                return MagicMock(stdout="Firmware Revision : 1.71", stderr="", returncode=0)
    
    # Test X13 detection via firmware 3.x
    mock_run_command.case = "x13"
    mock_subprocess.side_effect = mock_run_command
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X13
    
    # Test X11 detection via firmware 2.x
    mock_run_command.case = "x11"
    mock_subprocess.side_effect = mock_run_command
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X11
    
    # Test X10 detection via firmware 1.x
    mock_run_command.case = "x10"
    mock_subprocess.side_effect = mock_run_command
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X10

def test_board_detection_multiple_methods(mock_subprocess):
    """Test board detection with multiple detection methods"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            # DMI info fails, forcing IPMI detection
            raise subprocess.CalledProcessError(1, cmd, stderr="Error")
        elif cmd == ["ipmitool", "mc", "info"]:
            # Return both board info and firmware version
            return MagicMock(
                stdout=(
                    "Board Info: X13DPH-T\n"
                    "Firmware Revision : 1.71\n"  # X10 firmware, but X13 board info should take precedence
                ),
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    # Board info (X13) should take precedence over firmware version (1.71 would suggest X10)
    assert commander.board_gen == MotherboardGeneration.X13

def test_board_detection_fallback(mock_subprocess):
    """Test board detection fallback behavior"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            # First attempt: DMI info fails
            raise subprocess.CalledProcessError(1, cmd, stderr="Error")
        elif cmd == ["ipmitool", "mc", "info"]:
            if mock_run_command.attempts == 0:
                # First attempt: IPMI info fails
                mock_run_command.attempts += 1
                raise subprocess.CalledProcessError(1, cmd, stderr="Error")
            else:
                # Second attempt: IPMI info succeeds
                return MagicMock(stdout="Firmware Revision : 3.88", stderr="", returncode=0)
    mock_run_command.attempts = 0
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    # Should detect X13 via firmware version after retry
    assert commander.board_gen == MotherboardGeneration.X13
    assert mock_run_command.attempts == 1  # Verify retry occurred

# Fan Mode Tests

def test_get_fan_mode_auto(mock_subprocess, commander):
    """Test getting fan mode when in auto mode"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock response for auto mode (00)
    mock_subprocess.return_value = MagicMock(
        stdout="00\n",  # Add newline to test stripping
        stderr="",
        returncode=0
    )
    
    result = commander.get_fan_mode()
    
    # Verify command construction
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x45", "0x00"],
        capture_output=True,
        text=True,
        check=True
    )
    assert result is False  # Auto mode

def test_get_fan_mode_manual(mock_subprocess, commander):
    """Test getting fan mode when in manual mode"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Override the default mock response for this test
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: X13DPH-T",
                stderr="",
                returncode=0
            )
        elif cmd == ["ipmitool", "mc", "info"]:
            return MagicMock(
                stdout="Firmware Revision : 3.88",
                stderr="",
                returncode=0
            )
        else:
            return MagicMock(
                stdout="01\n",  # Manual mode with newline to test stripping
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    result = commander.get_fan_mode()
    
    # Verify command construction
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x45", "0x00"],
        capture_output=True,
        text=True,
        check=True
    )
    assert result is True  # Manual mode

def test_get_fan_mode_error(mock_subprocess, commander):
    """Test error handling when getting fan mode fails"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock command failure
    mock_subprocess.side_effect = subprocess.CalledProcessError(
        1, ["cmd"], stderr="Command failed"
    )
    
    with pytest.raises(IPMIError, match="Failed to get fan mode"):
        commander.get_fan_mode()

def test_get_fan_mode_invalid_response(mock_subprocess, commander):
    """Test handling of invalid mode response"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock invalid response
    mock_subprocess.return_value = MagicMock(
        stdout="invalid\n",  # Add newline to test stripping
        stderr="",
        returncode=0
    )
    
    # Should return False for any response that's not "01"
    result = commander.get_fan_mode()
    assert result is False

# Fan Control Mode Tests

def test_set_manual_mode_success(mock_subprocess, commander):
    """Test setting manual mode successfully"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock responses for command execution and verification
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["ipmitool", "raw", "0x30", "0x45", "0x00"]:  # get_fan_mode check
            return MagicMock(stdout="01\n", stderr="", returncode=0)  # Manual mode
        else:
            return MagicMock(stdout="", stderr="", returncode=0)
    mock_subprocess.side_effect = mock_run_command
    
    # Should succeed and not raise any exceptions
    commander.set_manual_mode()
    
    # Verify command was executed
    mock_subprocess.assert_any_call(
        ["ipmitool", "raw", "0x30", "0x45", "0x01", "0x01"],  # SET_MANUAL_MODE command
        capture_output=True,
        text=True,
        check=True
    )

def test_set_manual_mode_verification_failed(mock_subprocess, commander):
    """Test setting manual mode with failed verification"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock responses - command succeeds but verification fails
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["ipmitool", "raw", "0x30", "0x45", "0x00"]:  # get_fan_mode check
            return MagicMock(stdout="00\n", stderr="", returncode=0)  # Still in auto mode
        else:
            return MagicMock(stdout="", stderr="", returncode=0)
    mock_subprocess.side_effect = mock_run_command
    
    # Should raise IPMIError due to verification failure
    with pytest.raises(IPMIError, match="Failed to enter manual mode"):
        commander.set_manual_mode()

def test_set_manual_mode_unknown_board(mock_subprocess):
    """Test setting manual mode with unknown board generation"""
    # Create commander with unknown board generation
    commander = IPMICommander()
    commander.board_gen = MotherboardGeneration.UNKNOWN
    
    # Should raise IPMIError due to unknown board
    with pytest.raises(IPMIError, match="Unknown board generation"):
        commander.set_manual_mode()

def test_set_auto_mode_success(mock_subprocess, commander):
    """Test setting auto mode successfully"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock responses for command execution and verification
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["ipmitool", "raw", "0x30", "0x45", "0x00"]:  # get_fan_mode check
            return MagicMock(stdout="00\n", stderr="", returncode=0)  # Auto mode
        else:
            return MagicMock(stdout="", stderr="", returncode=0)
    mock_subprocess.side_effect = mock_run_command
    
    # Should succeed and not raise any exceptions
    commander.set_auto_mode()
    
    # Verify command was executed
    mock_subprocess.assert_any_call(
        ["ipmitool", "raw", "0x30", "0x45", "0x01", "0x00"],  # SET_AUTO_MODE command
        capture_output=True,
        text=True,
        check=True
    )

def test_set_auto_mode_verification_failed(mock_subprocess, commander):
    """Test setting auto mode with failed verification"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock responses - command succeeds but verification fails
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["ipmitool", "raw", "0x30", "0x45", "0x00"]:  # get_fan_mode check
            return MagicMock(stdout="01\n", stderr="", returncode=0)  # Still in manual mode
        else:
            return MagicMock(stdout="", stderr="", returncode=0)
    mock_subprocess.side_effect = mock_run_command
    
    # Should raise IPMIError due to verification failure
    with pytest.raises(IPMIError, match="Failed to enter automatic mode"):
        commander.set_auto_mode()

def test_set_auto_mode_unknown_board(mock_subprocess):
    """Test setting auto mode with unknown board generation"""
    # Create commander with unknown board generation
    commander = IPMICommander()
    commander.board_gen = MotherboardGeneration.UNKNOWN
    
    # Should raise IPMIError due to unknown board
    with pytest.raises(IPMIError, match="Unknown board generation"):
        commander.set_auto_mode()

# Fan Speed Control Tests

def test_set_fan_speed_validation(mock_subprocess, commander):
    """Test fan speed input validation"""
    # Invalid speed range
    with pytest.raises(ValueError, match="Fan speed must be between 0 and 100"):
        commander.set_fan_speed(-1)
    with pytest.raises(ValueError, match="Fan speed must be between 0 and 100"):
        commander.set_fan_speed(101)
    
    # Invalid zone
    with pytest.raises(ValueError, match="Zone must be 'chassis' or 'cpu'"):
        commander.set_fan_speed(50, zone="invalid")

def test_set_fan_speed_unknown_board(mock_subprocess):
    """Test setting fan speed with unknown board generation"""
    # Create commander with unknown board generation
    commander = IPMICommander()
    commander.board_gen = MotherboardGeneration.UNKNOWN
    
    # Should raise IPMIError due to unknown board
    with pytest.raises(IPMIError, match="Unknown board generation"):
        commander.set_fan_speed(50)

def test_set_fan_speed_x13_board(mock_subprocess, commander):
    """Test setting fan speed on X13 board"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Set board generation
    commander.board_gen = MotherboardGeneration.X13
    
    # Test different speeds and zones
    commander.set_fan_speed(0, zone="chassis")  # Should use minimum 2%
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x00", "0x05"],
        capture_output=True,
        text=True,
        check=True
    )
    
    commander.set_fan_speed(50, zone="cpu")
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x01", "0x7f"],
        capture_output=True,
        text=True,
        check=True
    )
    
    commander.set_fan_speed(100, zone="chassis")
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x00", "0xff"],
        capture_output=True,
        text=True,
        check=True
    )

def test_set_fan_speed_h12_board(mock_subprocess, commander):
    """Test setting fan speed on H12 board"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Set board generation
    commander.board_gen = MotherboardGeneration.H12
    
    # Test different speeds and zones
    commander.set_fan_speed(0, zone="chassis")  # Should use minimum 20%
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x91", "0x5A", "0x03", "0x10", "0x14"],
        capture_output=True,
        text=True,
        check=True
    )
    
    commander.set_fan_speed(50, zone="cpu")
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x91", "0x5A", "0x03", "0x11", "0x32"],
        capture_output=True,
        text=True,
        check=True
    )
    
    commander.set_fan_speed(100, zone="chassis")
    mock_subprocess.assert_called_with(
        ["ipmitool", "raw", "0x30", "0x91", "0x5A", "0x03", "0x10", "0x64"],
        capture_output=True,
        text=True,
        check=True
    )

def test_set_fan_speed_command_error(mock_subprocess, commander):
    """Test error handling when setting fan speed fails"""
    # Reset mock after board detection
    mock_subprocess.reset_mock()
    
    # Mock command failure
    mock_subprocess.side_effect = subprocess.CalledProcessError(
        1, ["cmd"], stderr="Command failed"
    )
    
    with pytest.raises(IPMIError):
        commander.set_fan_speed(50)
