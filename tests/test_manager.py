"""
Control Manager Tests

This module contains tests for the fan control manager functionality.
"""

import os
import time
import pytest
from unittest.mock import patch, MagicMock, mock_open, call
import yaml
from superfan.control.manager import ControlManager
from superfan.ipmi.commander import IPMICommander
from superfan.ipmi.sensors import CombinedTemperatureReader

# Test configuration
TEST_CONFIG = {
    "ipmi": {},
    "temperature": {
        "hysteresis": 3
    },
    "fans": {
        "polling_interval": 30,
        "monitor_interval": 5,
        "min_speed": 5,
        "max_speed": 100,
        "ramp_step": 5,
        "zones": {
            "chassis": {
                "enabled": True,
                "critical_max": 75,
                "warning_max": 65,
                "target": 55,
                "sensors": ["System Temp", "Peripheral Temp", "NVMe_*"],
                "curve": [
                    [0, 5],
                    [10, 30],
                    [20, 50],
                    [30, 70],
                    [40, 85],
                    [50, 100]
                ]
            },
            "cpu": {
                "enabled": True,
                "critical_max": 85,
                "warning_max": 75,
                "target": 65,
                "sensors": ["CPU1 Temp", "CPU2 Temp"],
                "curve": [
                    [0, 20],
                    [10, 30],
                    [20, 50],
                    [30, 70],
                    [40, 100]
                ]
            }
        }
    },
    "safety": {
        "watchdog_timeout": 90,
        "min_temp_readings": 2,
        "min_working_fans": 2,
        "restore_on_exit": True
    }
}

# Fixtures

@pytest.fixture
def mock_commander():
    """Create a mock IPMI commander"""
    commander = MagicMock(spec=IPMICommander)
    commander.get_sensor_readings.return_value = []
    return commander

@pytest.fixture
def mock_config_file():
    """Create a mock configuration file"""
    with patch('builtins.open', mock_open(read_data=yaml.dump(TEST_CONFIG))):
        yield "config.yaml"

@pytest.fixture
def manager(mock_commander, mock_config_file):
    """Create a ControlManager instance with mocked dependencies"""
    with patch('superfan.control.manager.IPMICommander', return_value=mock_commander):
        manager = ControlManager(mock_config_file)
        yield manager
        manager.stop()  # Ensure cleanup

# Initialization Tests

def test_manager_initialization(manager, mock_commander):
    """Test control manager initialization"""
    assert manager.config == TEST_CONFIG
    assert manager.monitor_mode is False
    assert manager.learning_mode is False
    assert manager._running is False
    assert manager._in_emergency is False
    assert len(manager.fan_curves) == 2  # chassis and cpu zones

def test_manager_monitor_mode(mock_commander, mock_config_file):
    """Test monitor mode initialization"""
    with patch('superfan.control.manager.IPMICommander', return_value=mock_commander):
        manager = ControlManager(mock_config_file, monitor_mode=True)
        assert manager.monitor_mode is True

def test_manager_learning_mode(mock_commander, mock_config_file):
    """Test learning mode initialization"""
    with patch('superfan.control.manager.IPMICommander', return_value=mock_commander):
        manager = ControlManager(mock_config_file, learning_mode=True)
        assert manager.learning_mode is True

# Fan Curve Tests

def test_init_fan_curves(manager):
    """Test fan curve initialization"""
    # Verify chassis zone curve
    chassis_curve = manager.fan_curves["chassis"]
    assert chassis_curve.get_speed(0) == 5  # min speed
    assert chassis_curve.get_speed(50) == 100  # max speed
    
    # Verify CPU zone curve
    cpu_curve = manager.fan_curves["cpu"]
    assert cpu_curve.get_speed(0) == 20  # min speed for CPU
    assert cpu_curve.get_speed(40) == 100  # max speed

# Temperature Management Tests

def test_get_zone_temperature(manager, mock_commander):
    """Test zone temperature calculation"""
    # Mock sensor readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 60.0, "state": "ok"},
        {"name": "CPU1 Temp", "value": 70.0, "state": "ok"}
    ]
    
    # Update readings
    manager.sensor_manager.update_readings()
    
    # Test chassis zone temperature (target: 55°C)
    chassis_temp = manager._get_zone_temperature("chassis")
    assert chassis_temp == 5.0  # 60°C - 55°C = 5°C above target
    
    # Test CPU zone temperature (target: 65°C)
    cpu_temp = manager._get_zone_temperature("cpu")
    assert cpu_temp == 5.0  # 70°C - 65°C = 5°C above target

def test_get_zone_temperature_pattern_matching(manager, mock_commander):
    """Test pattern-based sensor matching"""
    # Mock sensor readings with various patterns
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 70.0, "state": "ok"},
        {"name": "CPU2 Temp", "value": 75.0, "state": "ok"},
        {"name": "System Temp", "value": 60.0, "state": "ok"},
        {"name": "NVMe_nvme0n1", "value": 65.0, "state": "ok"},
        {"name": "Other Sensor", "value": 80.0, "state": "ok"}
    ]
    
    manager.sensor_manager.update_readings()
    
    # Test CPU zone (should match both CPU1 and CPU2)
    cpu_temp = manager._get_zone_temperature("cpu")
    assert cpu_temp == 10.0  # 75°C - 65°C = 10°C (uses highest temp)
    
    # Test chassis zone (should match System Temp and NVMe)
    chassis_temp = manager._get_zone_temperature("chassis")
    assert chassis_temp == 10.0  # 65°C - 55°C = 10°C (uses highest temp)

def test_get_zone_temperature_nvme_integration(manager, mock_commander):
    """Test NVMe temperature integration"""
    # Mock IPMI sensor readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 60.0, "state": "ok"}
    ]
    
    # Mock NVMe temperature readings
    with patch('subprocess.run') as mock_run:
        def mock_nvme_command(cmd, *args, **kwargs):
            if cmd[1] == "list":
                return MagicMock(
                    stdout="/dev/nvme0n1\n/dev/nvme1n1",
                    stderr="",
                    returncode=0
                )
            elif cmd[1] == "smart-log":
                return MagicMock(
                    stdout="temperature : 70 C",
                    stderr="",
                    returncode=0
                )
        mock_run.side_effect = mock_nvme_command
        
        manager.sensor_manager.update_readings()
        
        # Test chassis zone (should include NVMe temperature)
        chassis_temp = manager._get_zone_temperature("chassis")
        assert chassis_temp == 15.0  # 70°C - 55°C = 15°C (NVMe is highest)

def test_zone_temperature_thresholds(manager, mock_commander):
    """Test zone-specific temperature thresholds"""
    # Test each zone's thresholds
    test_cases = [
        # Zone, Temperature, Expected Result
        ("chassis", 74.0, True),   # Below critical (75°C)
        ("chassis", 76.0, False),  # Above critical
        ("cpu", 84.0, True),       # Below critical (85°C)
        ("cpu", 86.0, False)       # Above critical
    ]
    
    for zone, temp, expected_safe in test_cases:
        mock_commander.get_sensor_readings.return_value = [
            {f"name": f"{zone.upper()} Temp", "value": temp, "state": "ok"}
        ]
        
        manager.sensor_manager.update_readings()
        assert manager._check_safety() == expected_safe

# Fan Speed Control Tests

def test_gradual_speed_ramping(manager, mock_commander):
    """Test gradual fan speed changes"""
    # Mock initial temperature reading
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 85.0, "state": "ok"}  # 20°C over target
    ]
    
    manager.sensor_manager.update_readings()
    manager.current_speeds["cpu"] = 30  # Current speed
    
    # Start control loop
    manager.start()
    time.sleep(0.1)  # Allow one iteration
    manager.stop()
    
    # Verify speed was increased by ramp_step (5%)
    expected_calls = [
        call(35, zone="cpu"),  # 30% + 5% step
    ]
    mock_commander.set_fan_speed.assert_has_calls(expected_calls)

def test_speed_change_verification(manager, mock_commander):
    """Test fan speed change verification"""
    # Mock fan readings for verification
    def mock_readings():
        return [
            {"name": "FAN1", "value": 1500.0, "state": "ok"},
            {"name": "FAN2", "value": 1200.0, "state": "ok"}
        ]
    
    mock_commander.get_sensor_readings.side_effect = [
        # First call: temperature reading
        [{"name": "CPU1 Temp", "value": 85.0, "state": "ok"}],
        # Second call: fan speed verification
        mock_readings(),
        # Third call: temperature reading
        [{"name": "CPU1 Temp", "value": 85.0, "state": "ok"}],
        # Fourth call: fan speed verification (failed)
        [{"name": "FAN1", "value": 0.0, "state": "ok"}]  # Fan stopped
    ]
    
    manager.start()
    time.sleep(0.2)  # Allow two iterations
    manager.stop()
    
    # Verify emergency action was taken after fan verification failed
    mock_commander.set_fan_speed.assert_any_call(100, zone="chassis")
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")

def test_current_speed_tracking(manager, mock_commander):
    """Test current fan speed tracking"""
    # Set initial speed
    manager.current_speeds["chassis"] = 50
    
    # Mock temperature that would result in same speed
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 65.0, "state": "ok"}  # Results in 50% speed
    ]
    
    manager.sensor_manager.update_readings()
    manager.start()
    time.sleep(0.1)  # Allow one iteration
    manager.stop()
    
    # Verify no speed change command was sent (speed already at target)
    mock_commander.set_fan_speed.assert_not_called()

def test_default_speed_handling(manager, mock_commander):
    """Test default speed handling when no temperature reading"""
    # Mock no valid temperature readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": None, "state": "ns"}
    ]
    
    manager.start()
    time.sleep(0.1)  # Allow one iteration
    manager.stop()
    
    # Verify default speed was set
    mock_commander.set_fan_speed.assert_any_call(30, zone="chassis")  # Default 30%
    mock_commander.set_fan_speed.assert_any_call(30, zone="cpu")      # Default 30%

# Learning Mode Tests

def test_learning_mode_integration(manager, mock_commander):
    """Test fan speed learning integration"""
    # Mock successful learning
    with patch('superfan.control.manager.FanSpeedLearner') as mock_learner_class:
        mock_learner = MagicMock()
        mock_learner.learn_min_speeds.return_value = {
            "chassis": 10,
            "cpu": 15
        }
        mock_learner_class.return_value = mock_learner
        
        # Create manager in learning mode
        manager = ControlManager(mock_config_file, learning_mode=True)
        
        # Start control (triggers learning)
        manager.start()
        
        # Verify learning was performed
        mock_learner.learn_min_speeds.assert_called_once()
        
        # Verify fan curves were updated with learned speeds
        assert manager.fan_curves["chassis"].get_speed(0) == 10
        assert manager.fan_curves["cpu"].get_speed(0) == 15

def test_learning_mode_config_update(manager, mock_commander):
    """Test configuration update after learning"""
    # Mock successful learning
    with patch('superfan.control.manager.FanSpeedLearner') as mock_learner_class:
        mock_learner = MagicMock()
        mock_learner.learn_min_speeds.return_value = {
            "chassis": 10,
            "cpu": 15
        }
        mock_learner_class.return_value = mock_learner
        
        # Create manager in learning mode
        manager = ControlManager(mock_config_file, learning_mode=True)
        
        # Mock config file operations
        mock_config = TEST_CONFIG.copy()
        mock_config["fans"]["min_speed"] = 10  # Updated min speed
        
        with patch('builtins.open', mock_open()) as mock_file:
            manager.start()
            
            # Verify config was updated
            mock_file().write.assert_called()
            # Note: Can't easily verify exact YAML content due to formatting

def test_minimum_speed_validation(manager, mock_commander):
    """Test minimum speed validation after learning"""
    # Mock successful learning with very low speed
    with patch('superfan.control.manager.FanSpeedLearner') as mock_learner_class:
        mock_learner = MagicMock()
        mock_learner.learn_min_speeds.return_value = {
            "chassis": 2,  # Too low
            "cpu": 15
        }
        mock_learner_class.return_value = mock_learner
        
        # Create manager in learning mode
        manager = ControlManager(mock_config_file, learning_mode=True)
        
        # Start control (triggers learning)
        manager.start()
        
        # Verify minimum speed is enforced
        assert manager.fan_curves["chassis"].get_speed(0) == 5  # Config minimum
        assert manager.fan_curves["cpu"].get_speed(0) == 15    # Learned value

# Status Reporting Tests

def test_get_status(manager, mock_commander):
    """Test status reporting"""
    # Mock sensor readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 60.0, "state": "ok"},
        {"name": "CPU1 Temp", "value": 70.0, "state": "ok"}
    ]
    
    # Set some current speeds
    manager.current_speeds = {
        "chassis": 40,
        "cpu": 50
    }
    
    # Update readings
    manager.sensor_manager.update_readings()
    
    # Get status
    status = manager.get_status()
    
    assert status["running"] is False
    assert status["emergency"] is False
    assert "System Temp" in status["temperatures"]
    assert status["temperatures"]["System Temp"] == 60.0
    assert "CPU1 Temp" in status["temperatures"]
    assert status["temperatures"]["CPU1 Temp"] == 70.0
    assert status["fan_speeds"]["chassis"]["current"] == 40
    assert status["fan_speeds"]["cpu"]["current"] == 50
    assert status["fan_speeds"]["chassis"]["target"] is not None
    assert status["fan_speeds"]["cpu"]["target"] is not None
