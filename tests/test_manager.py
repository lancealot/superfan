"""
Tests for the Control Manager module
"""

import pytest
import yaml
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List

from superfan.control.manager import ControlManager
from superfan.ipmi import IPMICommander, IPMIError
from superfan.ipmi.sensors import CombinedTemperatureReader

# Test configuration
TEST_CONFIG = {
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

@pytest.fixture
def mock_commander():
    """Create a mock IPMI commander"""
    commander = Mock(spec=IPMICommander)
    
    # Mock sensor readings
    def get_sensor_readings():
        return [
            {"name": "CPU1 Temp", "value": 45, "state": "ok", "response_id": None},
            {"name": "System Temp", "value": 35, "state": "ok", "response_id": None},
            {"name": "FAN1", "value": 1500, "state": "ok", "response_id": None},
            {"name": "FAN2", "value": 1200, "state": "ok", "response_id": None},
            {"name": "FANA", "value": 3000, "state": "ok", "response_id": None}
        ]
    commander.get_sensor_readings.side_effect = get_sensor_readings
    
    return commander

@pytest.fixture
def mock_config(tmp_path):
    """Create a temporary config file"""
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(TEST_CONFIG, f)
    return str(config_file)

@pytest.fixture
def manager(mock_commander, mock_config):
    """Create a ControlManager instance with mocked dependencies"""
    with patch("superfan.control.manager.IPMICommander", return_value=mock_commander):
        manager = ControlManager(mock_config)
        yield manager
        manager.stop()

def test_init(manager, mock_config):
    """Test manager initialization"""
    assert manager.config_path == mock_config
    assert not manager.monitor_mode
    assert not manager.learning_mode
    assert not manager._running
    assert not manager._in_emergency
    assert isinstance(manager._lock, threading.Lock)

def test_get_zone_temperature(manager, mock_commander):
    """Test zone temperature calculation"""
    # Test chassis zone
    temp = manager._get_zone_temperature("chassis")
    assert temp is not None
    assert temp == -20  # 35 (System Temp) - 55 (target)
    
    # Test CPU zone
    temp = manager._get_zone_temperature("cpu")
    assert temp is not None
    assert temp == -20  # 45 (CPU1 Temp) - 65 (target)

def test_verify_fan_speeds(manager, mock_commander):
    """Test fan speed verification"""
    # Test normal operation
    assert manager._verify_fan_speeds()
    
    # Test with stopped fan
    def get_bad_readings():
        return [
            {"name": "FAN1", "value": 0, "state": "ok", "response_id": None},
            {"name": "FAN2", "value": 1200, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_bad_readings
    assert not manager._verify_fan_speeds()

def test_check_safety(manager, mock_commander):
    """Test safety checks"""
    # Test normal operation
    assert manager._check_safety()
    
    # Test critical temperature
    def get_critical_readings():
        return [
            {"name": "CPU1 Temp", "value": 90, "state": "cr", "response_id": None},
            {"name": "System Temp", "value": 35, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_critical_readings
    assert not manager._check_safety()

def test_emergency_action(manager, mock_commander):
    """Test emergency actions"""
    # Test emergency fan speed setting
    manager._emergency_action()
    mock_commander.set_fan_speed.assert_any_call(100, zone="chassis")
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")
    assert manager._in_emergency

def test_control_loop(manager, mock_commander):
    """Test control loop operation"""
    # Start control loop
    manager.start()
    assert manager._running
    assert manager._control_thread is not None
    
    # Let it run briefly
    time.sleep(0.1)
    
    # Verify fan speeds were set
    mock_commander.set_fan_speed.assert_called()
    
    # Stop control loop
    manager.stop()
    assert not manager._running
    assert manager._control_thread is None

def test_monitor_mode(mock_config):
    """Test monitor mode operation"""
    with patch("superfan.control.manager.IPMICommander") as mock_commander_cls:
        manager = ControlManager(mock_config, monitor_mode=True)
        assert manager.monitor_mode
        
        # Start control loop
        manager.start()
        time.sleep(0.1)
        
        # Should use monitor interval
        assert manager.config["fans"]["monitor_interval"] == 5
        
        manager.stop()

def test_emergency_recovery(manager, mock_commander):
    """Test recovery from emergency state"""
    # Trigger emergency
    def get_critical_readings():
        return [
            {"name": "CPU1 Temp", "value": 90, "state": "cr", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_critical_readings
    manager._check_safety()
    assert manager._in_emergency
    
    # Return to normal
    mock_commander.get_sensor_readings.side_effect = lambda: [
        {"name": "CPU1 Temp", "value": 45, "state": "ok", "response_id": None}
    ]
    manager._check_safety()
    assert not manager._in_emergency

def test_nvme_temperature_integration(manager, mock_commander):
    """Test NVMe temperature handling"""
    # Mock NVMe temperatures
    nvme_temps = {
        "nvme0": {"current": 40, "min": 30, "max": 50},
        "nvme1": {"current": 45, "min": 35, "max": 55}
    }
    manager.sensor_manager.nvme_reader.get_all_stats = Mock(return_value=nvme_temps)
    
    # Test chassis zone temperature with NVMe
    temp = manager._get_zone_temperature("chassis")
    assert temp is not None
    # Should use highest temp (45) - target (55)
    assert temp == -10

def test_fan_curve_behavior(manager, mock_commander):
    """Test fan curve response to temperatures"""
    # Test low temperature
    def get_cool_readings():
        return [
            {"name": "CPU1 Temp", "value": 45, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_cool_readings
    manager.start()
    time.sleep(0.1)
    # Should use minimum speed for CPU zone
    mock_commander.set_fan_speed.assert_any_call(20, zone="cpu")
    manager.stop()
    
    # Test high temperature
    def get_hot_readings():
        return [
            {"name": "CPU1 Temp", "value": 85, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_hot_readings
    manager.start()
    time.sleep(0.1)
    # Should use maximum speed for CPU zone
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")
    manager.stop()

def test_watchdog_timeout(manager, mock_commander):
    """Test watchdog timeout handling"""
    # Set old last valid reading
    manager._last_valid_reading = time.time() - 100  # Older than timeout
    assert not manager._check_safety()
    
    # Verify emergency action was taken
    mock_commander.set_fan_speed.assert_any_call(100, zone="chassis")
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")

def test_learning_mode(mock_config):
    """Test learning mode operation"""
    with patch("superfan.control.manager.IPMICommander") as mock_commander_cls, \
         patch("superfan.control.manager.FanSpeedLearner") as mock_learner_cls:
        # Mock learner
        mock_learner = Mock()
        mock_learner.learn_board_config.return_value = {"min_speed": 10}
        mock_learner_cls.return_value = mock_learner
        
        # Create manager in learning mode
        manager = ControlManager(mock_config, learning_mode=True)
        assert manager.learning_mode
        
        # Start should trigger learning
        manager.start()
        mock_learner.learn_board_config.assert_called_once()
        
        manager.stop()

def test_get_status(manager):
    """Test status reporting"""
    status = manager.get_status()
    assert "running" in status
    assert "emergency" in status
    assert "temperatures" in status
    assert "fan_speeds" in status
    
    # Verify temperature reporting
    temps = status["temperatures"]
    assert len(temps) > 0
    
    # Verify fan speed reporting
    fan_speeds = status["fan_speeds"]
    assert "chassis" in fan_speeds
    assert "cpu" in fan_speeds
    assert "current" in fan_speeds["chassis"]
    assert "target" in fan_speeds["chassis"]

def test_error_handling(manager, mock_commander):
    """Test error handling in control loop"""
    # Simulate IPMI error
    mock_commander.get_sensor_readings.side_effect = IPMIError("Test error")
    
    # Start control loop
    manager.start()
    time.sleep(0.1)
    
    # Should trigger emergency action
    assert manager._in_emergency
    mock_commander.set_fan_speed.assert_any_call(100, zone="chassis")
    mock_commander.set_fan_speed.assert_any_call(100, zone="cpu")
    
    manager.stop()

def test_zone_specific_thresholds(manager, mock_commander):
    """Test zone-specific temperature thresholds"""
    # Test chassis zone warning threshold
    def get_chassis_warning():
        return [
            {"name": "System Temp", "value": 70, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_chassis_warning
    temp = manager._get_zone_temperature("chassis")
    assert temp == 15  # 70 - 55 (target)
    
    # Test CPU zone critical threshold
    def get_cpu_critical():
        return [
            {"name": "CPU1 Temp", "value": 90, "state": "cr", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_cpu_critical
    assert not manager._check_safety()

def test_config_validation(mock_config):
    """Test configuration validation"""
    # Test missing required fields
    invalid_config = {
        "fans": {
            "polling_interval": 30
            # Missing required fields
        }
    }
    config_file = mock_config.parent / "invalid_config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(invalid_config, f)
        
    with pytest.raises(ValueError, match="Missing required configuration"):
        ControlManager(str(config_file))
        
    # Test invalid values
    invalid_values = {
        "fans": {
            "polling_interval": -1,  # Invalid negative value
            "monitor_interval": 5,
            "min_speed": 5,
            "max_speed": 100,
            "zones": {
                "chassis": {
                    "enabled": True,
                    "target": 55,
                    "warning_max": 65,
                    "critical_max": 75,
                    "sensors": ["System Temp"],
                    "curve": [[0, 5], [100, 100]]
                }
            }
        },
        "temperature": {"hysteresis": 3},
        "safety": {
            "watchdog_timeout": 90,
            "min_temp_readings": 2,
            "min_working_fans": 2,
            "restore_on_exit": True
        }
    }
    config_file = mock_config.parent / "invalid_values.yaml"
    with open(config_file, "w") as f:
        yaml.dump(invalid_values, f)
        
    with pytest.raises(ValueError, match="Invalid polling interval"):
        ControlManager(str(config_file))

def test_thread_safety(manager, mock_commander):
    """Test thread safety of control operations"""
    import threading
    
    # Test concurrent start/stop operations
    def start_stop():
        for _ in range(10):
            manager.start()
            manager.stop()
            
    threads = [threading.Thread(target=start_stop) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert not manager._running
    assert manager._control_thread is None
    
    # Test concurrent temperature updates
    manager.start()
    readings = []
    
    def update_temp():
        for _ in range(10):
            temp = manager._get_zone_temperature("cpu")
            readings.append(temp)
            
    threads = [threading.Thread(target=update_temp) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    manager.stop()
    assert len(readings) == 50  # 5 threads * 10 readings each

def test_cleanup_procedures(manager, mock_commander):
    """Test cleanup procedures"""
    # Test normal cleanup
    manager.start()
    manager.stop()
    mock_commander.set_auto_mode.assert_called_once()
    
    # Test cleanup after error
    def simulate_error():
        raise Exception("Test error")
        
    with patch.object(manager, "_control_loop", side_effect=simulate_error):
        manager.start()
        time.sleep(0.1)  # Let error occur
        assert not manager._running
        mock_commander.set_auto_mode.assert_called()

def test_sensor_pattern_matching(manager, mock_commander):
    """Test sensor pattern matching"""
    # Test exact match
    temp = manager._get_zone_temperature("chassis")
    assert temp is not None
    
    # Test wildcard match
    def get_wildcard_readings():
        return [
            {"name": "CPU_VRM1 Temp", "value": 60, "state": "ok", "response_id": None},
            {"name": "CPU_VRM2 Temp", "value": 65, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_wildcard_readings
    temp = manager._get_zone_temperature("cpu")
    assert temp is not None
    
    # Test no match
    def get_no_match():
        return [
            {"name": "Unknown Temp", "value": 50, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_no_match
    temp = manager._get_zone_temperature("chassis")
    assert temp is None

def test_hysteresis_behavior(manager, mock_commander):
    """Test temperature hysteresis behavior"""
    # Initial temperature
    def get_initial_temp():
        return [
            {"name": "CPU1 Temp", "value": 70, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_initial_temp
    manager.start()
    time.sleep(0.1)
    initial_speed = manager.current_speeds.get("cpu", 0)
    
    # Small temperature change (within hysteresis)
    def get_small_change():
        return [
            {"name": "CPU1 Temp", "value": 71, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_small_change
    time.sleep(0.1)
    assert manager.current_speeds.get("cpu", 0) == initial_speed  # No change
    
    # Large temperature change (outside hysteresis)
    def get_large_change():
        return [
            {"name": "CPU1 Temp", "value": 75, "state": "ok", "response_id": None}
        ]
    mock_commander.get_sensor_readings.side_effect = get_large_change
    time.sleep(0.1)
    assert manager.current_speeds.get("cpu", 0) > initial_speed  # Speed increased
    
    manager.stop()
