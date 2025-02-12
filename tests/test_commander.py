"""
Tests for the IPMI Commander module
"""

import pytest
import subprocess
from unittest.mock import Mock, patch, call
from typing import Dict, List

from superfan.ipmi.commander import (
    IPMICommander,
    IPMIError,
    IPMIConnectionError,
    IPMICommandError,
    MotherboardGeneration
)

# Test Data
MOCK_DMI_X11 = """
Base Board Information
    Manufacturer: Supermicro
    Product Name: X11DPi-N
    Version: 1.02
"""

MOCK_DMI_H12 = """
Base Board Information
    Manufacturer: Supermicro
    Product Name: H12SSL-i
    Version: 2.00
"""

MOCK_IPMI_INFO_X11 = """
Device ID                 : 32
Device Revision          : 1
Firmware Revision        : 2.10
IPMI Version             : 2.0
Manufacturer ID          : 47488
Product ID               : 43707
Product Name             : X11 BMC
"""

MOCK_IPMI_INFO_H12 = """
Device ID                 : 32
Device Revision          : 1
Firmware Revision        : 3.00
IPMI Version             : 2.0
Manufacturer ID          : 47488
Product ID               : 43707
Product Name             : H12 BMC
"""

@pytest.fixture
def mock_config(tmp_path):
    """Create a temporary config file"""
    config_file = tmp_path / "config.yaml"
    config = {
        "fans": {
            "board_config": {
                "speed_steps": {
                    "low": {"threshold": 12.5, "rpm_ranges": {
                        "chassis": {"min": 800, "max": 1200},
                        "cpu": {"min": 2000, "max": 3000}
                    }},
                    "medium": {"threshold": 25, "rpm_ranges": {
                        "chassis": {"min": 1000, "max": 1500},
                        "cpu": {"min": 2500, "max": 3500}
                    }},
                    "high": {"threshold": 37.5, "rpm_ranges": {
                        "chassis": {"min": 1200, "max": 1800},
                        "cpu": {"min": 3000, "max": 4000}
                    }},
                    "full": {"threshold": 100, "rpm_ranges": {
                        "chassis": {"min": 1500, "max": 2000},
                        "cpu": {"min": 3500, "max": 4500}
                    }}
                }
            }
        }
    }
    with open(config_file, "w") as f:
        import yaml
        yaml.dump(config, f)
    return str(config_file)

class TestBoardDetection:
    """Test board generation detection"""
    
    def test_dmi_detection_h12(self, mock_config):
        """Test H12 detection via DMI"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_DMI_H12, returncode=0)
            commander = IPMICommander(mock_config)
            assert commander.board_gen == MotherboardGeneration.H12
            
    def test_dmi_detection_x11(self, mock_config):
        """Test X11 detection via DMI"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_DMI_X11, returncode=0)
            commander = IPMICommander(mock_config)
            assert commander.board_gen == MotherboardGeneration.X11
            
    def test_ipmi_fallback_h12(self, mock_config):
        """Test H12 detection via IPMI fallback"""
        with patch("subprocess.run") as mock_run:
            # Make DMI fail
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "dmidecode"),
                Mock(stdout=MOCK_IPMI_INFO_H12, returncode=0)
            ]
            commander = IPMICommander(mock_config)
            assert commander.board_gen == MotherboardGeneration.H12
            
    def test_ipmi_fallback_x11(self, mock_config):
        """Test X11 detection via IPMI fallback"""
        with patch("subprocess.run") as mock_run:
            # Make DMI fail
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "dmidecode"),
                Mock(stdout=MOCK_IPMI_INFO_X11, returncode=0)
            ]
            commander = IPMICommander(mock_config)
            assert commander.board_gen == MotherboardGeneration.X11
            
    def test_detection_failure(self, mock_config):
        """Test fallback to UNKNOWN on detection failure"""
        with patch("subprocess.run") as mock_run:
            # Make both DMI and IPMI fail
            mock_run.side_effect = subprocess.CalledProcessError(1, "command")
            commander = IPMICommander(mock_config)
            assert commander.board_gen == MotherboardGeneration.UNKNOWN

class TestCommandValidation:
    """Test IPMI command validation"""
    
    @pytest.fixture
    def commander(self, mock_config):
        """Create IPMICommander instance"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_IPMI_INFO_X11, returncode=0)
            return IPMICommander(mock_config)
    
    def test_blacklisted_commands(self, commander):
        """Test blacklisted command rejection"""
        # Try blacklisted command (0x06 0x01)
        with pytest.raises(IPMIError, match="blacklisted"):
            commander._validate_raw_command("raw 0x06 0x01")
            
    def test_invalid_hex_format(self, commander):
        """Test invalid hex format rejection"""
        with pytest.raises(IPMIError, match="malformed hex"):
            commander._validate_raw_command("raw 0xZZ 0x01")
            
    def test_fan_mode_validation(self, commander):
        """Test fan mode command validation"""
        # Valid modes
        commander._validate_raw_command("raw 0x30 0x45 0x01 0x00")  # Auto
        commander._validate_raw_command("raw 0x30 0x45 0x01 0x01")  # Manual
        
        # Invalid mode
        with pytest.raises(IPMIError, match="Invalid fan mode"):
            commander._validate_raw_command("raw 0x30 0x45 0x01 0x02")
            
    def test_fan_speed_validation(self, commander):
        """Test fan speed command validation"""
        # Valid speed
        commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x32")  # 50%
        
        # Speed too low
        with pytest.raises(IPMIError, match="Fan speed too low"):
            commander._validate_raw_command("raw 0x30 0x70 0x66 0x01 0x00 0x02")

class TestFanControl:
    """Test fan control operations"""
    
    @pytest.fixture
    def commander(self, mock_config):
        """Create IPMICommander instance"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_IPMI_INFO_X11, returncode=0)
            return IPMICommander(mock_config)
    
    def test_set_manual_mode(self, commander):
        """Test setting manual mode"""
        with patch.object(commander, "_execute_ipmi_command") as mock_exec:
            mock_exec.side_effect = ["01"]  # get_fan_mode returns manual
            commander.set_manual_mode()
            mock_exec.assert_any_call("raw 0x30 0x45 0x01 0x01")
            
    def test_set_auto_mode(self, commander):
        """Test setting auto mode"""
        with patch.object(commander, "_execute_ipmi_command") as mock_exec:
            mock_exec.side_effect = ["00"]  # get_fan_mode returns auto
            commander.set_auto_mode()
            mock_exec.assert_any_call("raw 0x30 0x45 0x01 0x00")
            
    def test_set_fan_speed_x11(self, commander):
        """Test setting fan speed on X11"""
        with patch.object(commander, "_execute_ipmi_command") as mock_exec:
            commander.set_fan_speed(50, zone="chassis")
            mock_exec.assert_called_with("raw 0x30 0x70 0x66 0x01 0x00 0x7f")
            
    def test_set_fan_speed_h12(self, mock_config):
        """Test setting fan speed on H12"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_DMI_H12, returncode=0)
            commander = IPMICommander(mock_config)
            
            with patch.object(commander, "_execute_ipmi_command") as mock_exec:
                # Mock sensor readings for verification
                mock_exec.side_effect = [
                    None,  # set_fan_speed
                    [  # get_sensor_readings
                        {"name": "FAN1", "value": 1200, "state": "ok"},
                        {"name": "FAN2", "value": 1000, "state": "ok"}
                    ]
                ]
                commander.set_fan_speed(50, zone="chassis")
                # Should use H12 command format
                mock_exec.assert_any_call("raw 0x30 0x70 0x66 0x01 0x00 0xff")

class TestErrorHandling:
    """Test error handling and recovery"""
    
    @pytest.fixture
    def commander(self, mock_config):
        """Create IPMICommander instance"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_IPMI_INFO_X11, returncode=0)
            return IPMICommander(mock_config)
    
    def test_connection_error(self, commander):
        """Test connection error handling"""
        with patch.object(commander, "_execute_ipmi_command") as mock_exec:
            mock_exec.side_effect = IPMIConnectionError("Connection failed")
            with pytest.raises(IPMIConnectionError):
                commander.set_manual_mode()
                
    def test_command_retry(self, commander):
        """Test command retry on device busy"""
        with patch("subprocess.run") as mock_run:
            # First two attempts fail with device busy
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "ipmitool", stderr="Device or resource busy"),
                subprocess.CalledProcessError(1, "ipmitool", stderr="Device or resource busy"),
                Mock(stdout="Success", returncode=0)
            ]
            result = commander._execute_ipmi_command("test command")
            assert result == "Success"
            assert mock_run.call_count == 3
            
    def test_max_retries_exceeded(self, commander):
        """Test max retries exceeded"""
        with patch("subprocess.run") as mock_run:
            # All attempts fail
            mock_run.side_effect = subprocess.CalledProcessError(1, "ipmitool", stderr="Error")
            with pytest.raises(IPMICommandError, match="Command failed after 3 attempts"):
                commander._execute_ipmi_command("test command")
                
    def test_unexpected_error(self, commander):
        """Test unexpected error handling"""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Unexpected error")
            with pytest.raises(IPMIError, match="Unexpected error"):
                commander._execute_ipmi_command("test command")
