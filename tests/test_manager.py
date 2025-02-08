"""
Control Manager Tests

This module contains tests for the fan control manager functionality.
"""

import os
import time
import pytest
from unittest.mock import patch, MagicMock, mock_open
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

def test_get_zone_temperature_no_reading(manager, mock_commander):
    """Test zone temperature with no valid readings"""
    # Mock sensor readings with no reading state
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": None, "state": "ns"},
        {"name": "CPU1 Temp", "value": None, "state": "ns"}
    ]
    
    # Update readings
    manager.sensor_manager.update_readings()
    
    # Test both zones
    assert manager._get_zone_temperature("chassis") is None
    assert manager._get_zone_temperature("cpu") is None

# Safety Check Tests

def test_verify_fan_speeds(manager, mock_commander):
    """Test fan speed verification"""
    # Mock normal fan readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"},
        {"name": "FANA", "value": 3000.0, "state": "ok"}
    ]
    
    assert manager._verify_fan_speeds() is True

def test_verify_fan_speeds_failure(manager, mock_commander):
    """Test fan speed verification failure"""
    # Mock fan readings with stopped fan
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 0.0, "state": "ok"},  # Stopped fan
        {"name": "FAN2", "value": 1200.0, "state": "ok"}
    ]
    
    assert manager._verify_fan_speeds() is False

def test_check_safety_normal(manager, mock_commander):
    """Test safety check under normal conditions"""
    # Mock normal sensor readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 60.0, "state": "ok"},
        {"name": "CPU1 Temp", "value": 70.0, "state": "ok"},
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"}
    ]
    
    assert manager._check_safety() is True
    assert manager._in_emergency is False

def test_check_safety_critical(manager, mock_commander):
    """Test safety check with critical conditions"""
    # Mock critical temperature readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 80.0, "state": "cr"},  # Critical state
        {"name": "FAN1", "value": 1500.0, "state": "ok"}
    ]
    
    assert manager._check_safety() is False

def test_emergency_action(manager, mock_commander):
    """Test emergency action execution"""
    manager._emergency_action()
    
    # Verify fans were set to 100%
    mock_commander.set_fan_speed.assert_any_call(100, zone="chassis")
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")
    assert manager._in_emergency is True

# Control Loop Tests

def test_control_loop_normal(manager, mock_commander):
    """Test control loop under normal conditions"""
    # Mock normal temperature readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 60.0, "state": "ok"},
        {"name": "CPU1 Temp", "value": 70.0, "state": "ok"},
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"}
    ]
    
    # Start control loop
    manager.start()
    time.sleep(0.1)  # Allow one iteration
    manager.stop()
    
    # Verify fan speeds were set based on temperatures
    mock_commander.set_fan_speed.assert_any_call(30, zone="chassis")  # 5°C over target -> 30%
    mock_commander.set_fan_speed.assert_any_call(30, zone="cpu")  # 5°C over target -> 30%

def test_control_loop_emergency(manager, mock_commander):
    """Test control loop emergency handling"""
    # First return normal readings, then critical
    readings = [
        # Normal readings
        [
            {"name": "System Temp", "value": 60.0, "state": "ok"},
            {"name": "FAN1", "value": 1500.0, "state": "ok"}
        ],
        # Critical readings
        [
            {"name": "System Temp", "value": 80.0, "state": "cr"},
            {"name": "FAN1", "value": 1500.0, "state": "ok"}
        ]
    ]
    mock_commander.get_sensor_readings.side_effect = readings
    
    # Start control loop
    manager.start()
    time.sleep(0.2)  # Allow two iterations
    manager.stop()
    
    # Verify emergency action was taken
    mock_commander.set_fan_speed.assert_any_call(100, zone="chassis")
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")

# Status Reporting Tests

def test_get_status(manager, mock_commander):
    """Test status reporting"""
    # Mock sensor readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "System Temp", "value": 60.0, "state": "ok"},
        {"name": "CPU1 Temp", "value": 70.0, "state": "ok"}
    ]
    
    # Update readings
    manager.sensor_manager.update_readings()
    
    # Get status
    status = manager.get_status()
    
    assert status["running"] is False
    assert status["emergency"] is False
    assert "System Temp" in status["temperatures"]
    assert "CPU1 Temp" in status["temperatures"]
    assert "chassis" in status["fan_speeds"]
    assert "cpu" in status["fan_speeds"]
