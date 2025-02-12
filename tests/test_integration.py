"""
Integration Tests for Superfan

These tests verify that the different modules work together correctly
in real-world scenarios.
"""

import pytest
import yaml
import time
import threading
from unittest.mock import Mock, patch, call
from typing import Dict, List

from superfan.control.manager import ControlManager
from superfan.ipmi import IPMICommander, IPMIError
from superfan.ipmi.sensors import CombinedTemperatureReader
from superfan.control.learner import FanSpeedLearner

# Test Data
MOCK_SENSOR_READINGS = [
    {"name": "CPU1 Temp", "value": 45.0, "state": "ok", "response_id": 1},
    {"name": "CPU2 Temp", "value": 47.0, "state": "ok", "response_id": 1},
    {"name": "System Temp", "value": 35.0, "state": "ok", "response_id": 1},
    {"name": "Peripheral Temp", "value": 40.0, "state": "ok", "response_id": 1},
    {"name": "FAN1", "value": 1500, "state": "ok", "response_id": 1},
    {"name": "FAN2", "value": 1200, "state": "ok", "response_id": 1},
    {"name": "FANA", "value": 3000, "state": "ok", "response_id": 1}
]

MOCK_NVME_LIST = """
Node             SN                   Model                                    Namespace Usage                      Format           FW Rev  
---------------- -------------------- ---------------------------------------- --------- -------------------------- ---------------- --------
/dev/nvme0n1     ABC123              SAMSUNG MZVL2512HCJQ-00B00              1         500.11  GB /   512.11  GB  512   B +  0 B   2B4QFXO7
"""

MOCK_NVME_SMART = """
Smart Log for NVME device:nvme0n1 namespace-id:ffffffff
critical_warning                    : 0
temperature                         : 38 C
available_spare                     : 100%
available_spare_threshold          : 10%
percentage_used                    : 0%
"""

@pytest.fixture
def mock_config(tmp_path):
    """Create a temporary config file"""
    config = {
        "fans": {
            "polling_interval": 30,
            "monitor_interval": 5,
            "min_speed": 5,
            "max_speed": 100,
            "zones": {
                "chassis": {
                    "enabled": True,
                    "target": 55,
                    "warning_max": 65,
                    "critical_max": 75,
                    "sensors": ["System Temp", "NVMe_*"],
                    "curve": [
                        [0, 5],
                        [10, 30],
                        [20, 50],
                        [30, 70],
                        [40, 100]
                    ]
                },
                "cpu": {
                    "enabled": True,
                    "target": 65,
                    "warning_max": 75,
                    "critical_max": 85,
                    "sensors": ["CPU* Temp"],
                    "curve": [
                        [0, 20],
                        [10, 40],
                        [20, 60],
                        [30, 80],
                        [40, 100]
                    ]
                }
            }
        },
        "temperature": {
            "hysteresis": 3
        },
        "safety": {
            "watchdog_timeout": 90,
            "min_temp_readings": 2,
            "min_working_fans": 2,
            "restore_on_exit": True
        }
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)
    return str(config_file)

class TestEndToEndFanControl:
    """Test complete fan control scenarios"""
    
    def test_normal_operation(self, mock_config):
        """Test normal fan control operation"""
        with patch("subprocess.run") as mock_run:
            # Mock NVMe commands
            mock_run.side_effect = [
                Mock(stdout=MOCK_NVME_LIST, returncode=0),
                Mock(stdout=MOCK_NVME_SMART, returncode=0)
            ]
            
            # Create manager with mocked IPMI
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Set up sensor readings
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                # Create and start manager
                manager = ControlManager(mock_config)
                manager.start()
                
                # Let it run briefly
                time.sleep(0.1)
                
                # Verify fan speeds were set based on temperatures
                mock_commander.set_fan_speed.assert_any_call(20, zone="cpu")  # CPU at 45°C
                mock_commander.set_fan_speed.assert_any_call(5, zone="chassis")  # System at 35°C
                
                manager.stop()
                
                # Verify cleanup
                mock_commander.set_auto_mode.assert_called_once()

    def test_temperature_spike(self, mock_config):
        """Test response to sudden temperature increase"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Start with normal temperatures
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Simulate temperature spike
                mock_commander.get_sensor_readings.return_value = [
                    {"name": "CPU1 Temp", "value": 80.0, "state": "ok", "response_id": 1},
                    {"name": "CPU2 Temp", "value": 82.0, "state": "ok", "response_id": 1}
                ]
                
                time.sleep(0.1)
                
                # Verify fan speeds increased
                mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")
                
                manager.stop()

class TestEmergencyScenarios:
    """Test emergency handling scenarios"""
    
    def test_critical_temperature(self, mock_config):
        """Test response to critical temperature"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Start with normal temperatures
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Simulate critical temperature
                mock_commander.get_sensor_readings.return_value = [
                    {"name": "CPU1 Temp", "value": 90.0, "state": "cr", "response_id": 1}
                ]
                
                time.sleep(0.1)
                
                # Verify emergency action
                assert manager._in_emergency
                mock_commander.set_fan_speed.assert_has_calls([
                    call(100, zone="chassis"),
                    call(100, zone="cpu")
                ])
                
                manager.stop()

    def test_fan_failure(self, mock_config):
        """Test response to fan failure"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Start with normal readings
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Simulate fan failure
                mock_commander.get_sensor_readings.return_value = [
                    {"name": "FAN1", "value": 0, "state": "ok", "response_id": 1},
                    {"name": "FAN2", "value": 0, "state": "ok", "response_id": 1}
                ]
                
                time.sleep(0.1)
                
                # Verify emergency action and BMC control restoration
                assert manager._in_emergency
                mock_commander.set_auto_mode.assert_called()
                
                manager.stop()

class TestTemperatureMonitoring:
    """Test temperature monitoring integration"""
    
    def test_combined_temperature_sources(self, mock_config):
        """Test integration of IPMI and NVMe temperatures"""
        with patch("subprocess.run") as mock_run:
            # Mock NVMe with high temperature
            mock_run.return_value = Mock(stdout="""
Smart Log for NVME device:nvme0n1 namespace-id:ffffffff
temperature                         : 70 C
""", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Normal IPMI temperatures
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Verify fan speeds account for NVMe temperature
                mock_commander.set_fan_speed.assert_any_call(70, zone="chassis")
                
                manager.stop()

    def test_sensor_pattern_matching(self, mock_config):
        """Test sensor pattern matching across modules"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Add some variant sensor names
                mock_commander.get_sensor_readings.return_value = [
                    {"name": "CPU1_VRM Temp", "value": 60.0, "state": "ok", "response_id": 1},
                    {"name": "CPU2_VRM Temp", "value": 62.0, "state": "ok", "response_id": 1},
                    {"name": "System_Ambient Temp", "value": 35.0, "state": "ok", "response_id": 1}
                ]
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Verify patterns matched and temperatures affected fan speeds
                status = manager.get_status()
                assert len(status["temperatures"]) > 0
                assert status["fan_speeds"]["cpu"]["current"] > 20  # Increased due to VRM temps
                
                manager.stop()

class TestLearningIntegration:
    """Test fan speed learning integration"""
    
    def test_learning_mode(self, mock_config):
        """Test learning mode with all components"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                # Create manager in learning mode
                manager = ControlManager(mock_config, learning_mode=True)
                
                # Start learning
                manager.start()
                
                # Verify learning process
                mock_commander.set_manual_mode.assert_called_once()
                mock_commander.set_fan_speed.assert_called()
                
                # Verify cleanup
                manager.stop()
                mock_commander.set_auto_mode.assert_called_once()

class TestErrorRecovery:
    """Test error recovery across modules"""
    
    def test_ipmi_error_recovery(self, mock_config):
        """Test recovery from IPMI errors"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Start with normal operation
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Simulate IPMI error
                mock_commander.get_sensor_readings.side_effect = IPMIError("Test error")
                time.sleep(0.1)
                
                # Verify emergency action
                assert manager._in_emergency
                mock_commander.set_auto_mode.assert_called()
                
                # Simulate recovery
                mock_commander.get_sensor_readings.side_effect = None
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                time.sleep(0.1)
                
                # Verify normal operation resumed
                assert not manager._in_emergency
                
                manager.stop()

    def test_nvme_error_recovery(self, mock_config):
        """Test recovery from NVMe errors"""
        with patch("subprocess.run") as mock_run:
            # Start with working NVMe
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Simulate NVMe error
                mock_run.side_effect = Exception("NVMe error")
                time.sleep(0.1)
                
                # Verify system continues with IPMI sensors only
                status = manager.get_status()
                assert "NVMe_nvme0n1" not in status["temperatures"]
                assert not manager._in_emergency  # NVMe failure shouldn't trigger emergency
                
                manager.stop()
