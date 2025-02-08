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
def commander():
    """Create a basic IPMICommander instance"""
    return IPMICommander()

@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run for testing"""
    with patch('subprocess.run') as mock_run:
        yield mock_run

# Board Generation Detection Tests

def test_detect_board_generation_h12(mock_subprocess):
    """Test H12 board detection via DMI info"""
    # Mock dmidecode output
    mock_subprocess.return_value = MagicMock(
        stdout="Base Board Information\n\tProduct Name: H12SSL-i",
        stderr="",
        returncode=0
    )
    
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.H12

def test_detect_board_generation_x13(mock_subprocess):
    """Test X13 board detection via IPMI info"""
    # Mock dmidecode failure to force IPMI detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            raise subprocess.CalledProcessError(1, cmd, "Error")
        elif cmd[0] == "ipmitool":
            return MagicMock(
                stdout="Firmware Revision : 3.88",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X13

# Command Validation Tests

def test_validate_raw_command_valid(commander):
    """Test validation of valid raw commands"""
    # Test valid fan control commands
    commander._validate_raw_command("raw 0x30 0x45 0x01 0x01")  # Set manual mode
    commander._validate_raw_command("raw 0x30 0x45 0x01 0x00")  # Set auto mode
    commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x32")  # Set fan speed 50%

def test_validate_raw_command_blacklisted(commander):
    """Test validation of blacklisted commands"""
    # Test blacklisted commands
    with pytest.raises(IPMIError, match="Command 0x6 0x1 is blacklisted for safety"):
        commander._validate_raw_command("raw 0x06 0x01")
    
    with pytest.raises(IPMIError, match="Command 0x6 0x2 is blacklisted for safety"):
        commander._validate_raw_command("raw 0x06 0x02")

def test_validate_raw_command_invalid_mode(commander):
    """Test validation of invalid fan mode commands"""
    # Test invalid fan mode value
    with pytest.raises(IPMIError, match="Invalid fan mode"):
        commander._validate_raw_command("raw 0x30 0x45 0x01 0x02")  # Invalid mode 0x02

def test_validate_raw_command_invalid_speed(commander):
    """Test validation of invalid fan speed commands"""
    # Test fan speed too low
    with pytest.raises(IPMIError, match="Fan speed too low"):
        commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x02")  # Speed < 4%

def test_validate_raw_command_non_raw(commander):
    """Test validation of non-raw commands"""
    # Test non-raw commands pass through without validation
    commander._validate_raw_command("sdr list")
    commander._validate_raw_command("mc info")

def test_validate_raw_command_malformed(commander):
    """Test validation of malformed raw commands"""
    # Test malformed command format
    with pytest.raises(IPMIError, match="Invalid command format"):
        commander._validate_raw_command("raw 0xGG 0x01")  # Invalid hex value

# Fan Mode Operation Tests

def test_get_fan_mode_manual(mock_subprocess):
    """Test getting fan mode when in manual mode"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            else:  # Fan mode command
                return MagicMock(
                    stdout="01",  # Manual mode
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.get_fan_mode() is True

def test_get_fan_mode_auto(mock_subprocess):
    """Test getting fan mode when in automatic mode"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            else:  # Fan mode command
                return MagicMock(
                    stdout="00",  # Auto mode
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.get_fan_mode() is False

def test_get_fan_mode_error(mock_subprocess):
    """Test getting fan mode when command fails"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            else:  # Fan mode command
                raise subprocess.CalledProcessError(1, "ipmitool", "Error")
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    with pytest.raises(IPMIError, match="Failed to get fan mode"):
        commander.get_fan_mode()

def test_set_manual_mode_success(mock_subprocess):
    """Test setting manual mode successfully"""
    # Mock responses for board detection, mode setting, and verification
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x01 0x01" in cmd:  # Set manual mode
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x00" in cmd:  # Get mode verification
                return MagicMock(
                    stdout="01",  # Manual mode
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    commander.set_manual_mode()  # Should not raise any exceptions

def test_set_manual_mode_failure(mock_subprocess):
    """Test setting manual mode with verification failure"""
    # Mock responses for board detection, mode setting, and failed verification
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x01 0x01" in cmd:  # Set manual mode
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x00" in cmd:  # Get mode verification
                return MagicMock(
                    stdout="00",  # Still in auto mode
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    with pytest.raises(IPMIError, match="Failed to enter manual mode"):
        commander.set_manual_mode()

def test_set_auto_mode_success(mock_subprocess):
    """Test setting automatic mode successfully"""
    # Mock responses for board detection, mode setting, and verification
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x01 0x00" in cmd:  # Set auto mode
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x00" in cmd:  # Get mode verification
                return MagicMock(
                    stdout="00",  # Auto mode
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    commander.set_auto_mode()  # Should not raise any exceptions

def test_set_auto_mode_failure(mock_subprocess):
    """Test setting automatic mode with verification failure"""
    # Mock responses for board detection, mode setting, and failed verification
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x01 0x00" in cmd:  # Set auto mode
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x45 0x00" in cmd:  # Get mode verification
                return MagicMock(
                    stdout="01",  # Still in manual mode
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    with pytest.raises(IPMIError, match="Failed to enter automatic mode"):
        commander.set_auto_mode()

# Fan Speed Control Tests

def test_set_fan_speed_chassis(mock_subprocess):
    """Test setting chassis fan speed"""
    # Mock responses for board detection and fan speed setting
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x70 0x66 0x01 0x00" in cmd:  # Set chassis fan speed
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    commander.set_fan_speed(50, zone="chassis")  # Should not raise any exceptions

def test_set_fan_speed_cpu(mock_subprocess):
    """Test setting CPU fan speed"""
    # Mock responses for board detection and fan speed setting
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x70 0x66 0x01 0x01" in cmd:  # Set CPU fan speed
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    commander.set_fan_speed(50, zone="cpu")  # Should not raise any exceptions

def test_set_fan_speed_h12_board(mock_subprocess):
    """Test setting fan speed on H12 board"""
    # Mock responses for board detection and fan speed setting
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "raw 0x30 0x91 0x5A 0x03 0x10" in cmd:  # H12 chassis fan command
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    commander.set_fan_speed(50, zone="chassis")  # Should not raise any exceptions

def test_set_fan_speed_invalid_zone(mock_subprocess):
    """Test setting fan speed with invalid zone"""
    # Mock board detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    with pytest.raises(ValueError, match="Zone must be 'chassis' or 'cpu'"):
        commander.set_fan_speed(50, zone="invalid")

def test_set_fan_speed_invalid_percentage(mock_subprocess):
    """Test setting fan speed with invalid percentage"""
    # Mock board detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    with pytest.raises(ValueError, match="Fan speed must be between 0 and 100"):
        commander.set_fan_speed(101, zone="chassis")
    with pytest.raises(ValueError, match="Fan speed must be between 0 and 100"):
        commander.set_fan_speed(-1, zone="chassis")

def test_set_fan_speed_minimum_enforced(mock_subprocess):
    """Test minimum fan speed enforcement"""
    # Mock responses for board detection and fan speed setting
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x70 0x66 0x01 0x00" in cmd:  # Set chassis fan speed
                # Verify the speed is at least 2% (0x04)
                speed_hex = cmd[-1]
                assert int(speed_hex, 16) >= 0x04
                return MagicMock(
                    stdout="",
                    stderr="",
                    returncode=0
                )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    # Setting 1% should be automatically raised to 2%
    commander.set_fan_speed(1, zone="chassis")  # Should not raise any exceptions

def test_set_fan_speed_command_error(mock_subprocess):
    """Test fan speed setting command failure"""
    # Mock responses for board detection and failed fan speed setting
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            return MagicMock(
                stdout="Base Board Information\n\tProduct Name: H12SSL-i",
                stderr="",
                returncode=0
            )
        elif cmd[0] == "ipmitool":
            if "mc info" in cmd:  # Board detection
                return MagicMock(
                    stdout="Firmware Revision : 3.88",
                    stderr="",
                    returncode=0
                )
            elif "raw 0x30 0x70 0x66" in cmd:  # Fan speed command
                raise subprocess.CalledProcessError(1, cmd, "Error setting fan speed")
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    with pytest.raises(IPMICommandError, match="Command failed"):
        commander.set_fan_speed(50, zone="chassis")

# Sensor Reading Tests

def test_get_sensor_readings_parse(mock_subprocess):
    """Test parsing of sensor readings"""
    # Mock sensor data output
    mock_subprocess.return_value = MagicMock(
        stdout=(
            "CPU1 Temp        | 45.000     | degrees C  | ok    | 0.000     | 85.000    | 85.000    | 85.000    | 85.000    \n"
            "System Temp      | 47.000     | degrees C  | ok    | -9.000    | 80.000    | 80.000    | 80.000    | 80.000    \n"
            "Peripheral Temp  | 43.000     | degrees C  | ok    | -9.000    | 80.000    | 80.000    | 80.000    | 80.000    \n"
            "FAN1            | 1420.000   | RPM        | ok    | 300.000   | 1800.000  | 1800.000  | 1800.000  | 1800.000  \n"
            "FAN2            | 1120.000   | RPM        | ok    | 300.000   | 1800.000  | 1800.000  | 1800.000  | 1800.000  \n"
            "FANA            | 3500.000   | RPM        | ok    | 300.000   | 3900.000  | 3900.000  | 3900.000  | 3900.000  \n"
            "FANB            | na         | RPM        | ns    | 300.000   | 3900.000  | 3900.000  | 3900.000  | 3900.000  \n"
        ),
        stderr="",
        returncode=0
    )
    
    commander = IPMICommander()
    readings = commander.get_sensor_readings()
    
    # Verify temperature readings
    cpu_temp = next(r for r in readings if r["name"] == "CPU1 Temp")
    assert cpu_temp["value"] == 45.0
    assert cpu_temp["state"] == "ok"
    
    # Verify fan readings
    fan1 = next(r for r in readings if r["name"] == "FAN1")
    assert fan1["value"] == 1420.0
    assert fan1["state"] == "ok"
    
    # Verify non-responsive sensor
    fanb = next(r for r in readings if r["name"] == "FANB")
    assert fanb["value"] is None
    assert fanb["state"] == "ns"

def test_get_sensor_readings_kelvin_format(mock_subprocess):
    """Test parsing of Kelvin format temperature readings"""
    # Mock sensor data with Kelvin format
    mock_subprocess.return_value = MagicMock(
        stdout="CPU1 Temp        | 45(318K)   | degrees C  | ok    | 0.000     | 85.000    | 85.000    | 85.000    | 85.000    \n",
        stderr="",
        returncode=0
    )
    
    commander = IPMICommander()
    readings = commander.get_sensor_readings()
    
    cpu_temp = next(r for r in readings if r["name"] == "CPU1 Temp")
    assert cpu_temp["value"] == 45.0
    assert cpu_temp["state"] == "ok"

def test_get_sensor_readings_critical_state(mock_subprocess):
    """Test parsing of critical state sensor readings"""
    # Mock sensor data with critical state
    mock_subprocess.return_value = MagicMock(
        stdout="CPU1 Temp        | 90.000     | degrees C  | cr    | 0.000     | 85.000    | 85.000    | 85.000    | 85.000    \n",
        stderr="",
        returncode=0
    )
    
    commander = IPMICommander()
    readings = commander.get_sensor_readings()
    
    cpu_temp = next(r for r in readings if r["name"] == "CPU1 Temp")
    assert cpu_temp["value"] == 90.0
    assert cpu_temp["state"] == "cr"

def test_get_sensor_readings_response_id(mock_subprocess):
    """Test handling of IPMI response IDs"""
    # Mock sensor data with response ID message
    mock_subprocess.return_value = MagicMock(
        stdout=(
            "CPU1 Temp        | 45.000     | degrees C  | ok    | 0.000     | 85.000    | 85.000    | 85.000    | 85.000    \n"
            "Received a response with unexpected ID: 123\n"
        ),
        stderr="",
        returncode=0
    )
    
    commander = IPMICommander()
    readings = commander.get_sensor_readings()
    
    cpu_temp = next(r for r in readings if r["name"] == "CPU1 Temp")
    assert cpu_temp["response_id"] == 123

def test_get_sensor_readings_error(mock_subprocess):
    """Test sensor reading command failure"""
    mock_subprocess.side_effect = subprocess.CalledProcessError(1, "ipmitool", "Error reading sensors")
    
    commander = IPMICommander()
    with pytest.raises(IPMICommandError, match="Command failed"):
        commander.get_sensor_readings()

def test_get_sensor_readings_malformed(mock_subprocess):
    """Test handling of malformed sensor data"""
    # Mock malformed sensor data
    mock_subprocess.return_value = MagicMock(
        stdout="CPU1 Temp        | invalid    | degrees C  | ok    | 0.000     | 85.000    | 85.000    | 85.000    | 85.000    \n",
        stderr="",
        returncode=0
    )
    
    commander = IPMICommander()
    readings = commander.get_sensor_readings()
    
    cpu_temp = next(r for r in readings if r["name"] == "CPU1 Temp")
    assert cpu_temp["value"] is None
    assert cpu_temp["state"] == "ns"  # Should be marked as no reading when value is invalid

def test_detect_board_generation_x11(mock_subprocess):
    """Test X11 board detection via board info"""
    # Mock dmidecode failure to force IPMI detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            raise subprocess.CalledProcessError(1, cmd, "Error")
        elif cmd[0] == "ipmitool":
            return MagicMock(
                stdout="Board Info: X11DPH-T",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X11

def test_detect_board_generation_x10(mock_subprocess):
    """Test X10 board detection via firmware version"""
    # Mock dmidecode failure to force IPMI detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            raise subprocess.CalledProcessError(1, cmd, "Error")
        elif cmd[0] == "ipmitool":
            return MagicMock(
                stdout="Firmware Revision : 1.71",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X10

def test_detect_board_generation_x9(mock_subprocess):
    """Test X9 board detection via board info"""
    # Mock dmidecode failure to force IPMI detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            raise subprocess.CalledProcessError(1, cmd, "Error")
        elif cmd[0] == "ipmitool":
            return MagicMock(
                stdout="Board Info: X9DRi-LN4F+",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X9

def test_detect_board_generation_unknown(mock_subprocess):
    """Test unknown board detection"""
    # Mock both dmidecode and IPMI failures
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            raise subprocess.CalledProcessError(1, cmd, "Error")
        elif cmd[0] == "ipmitool":
            return MagicMock(
                stdout="Board Info: Unknown Board",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    with pytest.raises(IPMIError, match="Could not determine board generation"):
        commander = IPMICommander()

def test_detect_board_generation_dmi_fallback(mock_subprocess):
    """Test fallback to IPMI detection when DMI fails"""
    # Mock dmidecode failure and successful IPMI detection
    def mock_run_command(cmd, *args, **kwargs):
        if cmd[0] == "sudo" and cmd[1] == "dmidecode":
            raise subprocess.CalledProcessError(1, cmd, "Error")
        elif cmd[0] == "ipmitool":
            return MagicMock(
                stdout="Board Info: X13DPH-T",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X13
