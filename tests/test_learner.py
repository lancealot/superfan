"""
Fan Speed Learner Tests

This module contains tests for the fan speed learning functionality.
"""

import pytest
from unittest.mock import patch, MagicMock, mock_open
import yaml
from superfan.control.learner import FanSpeedLearner
from superfan.ipmi.commander import IPMICommander

# Test configuration
TEST_CONFIG = {
    "fans": {
        "min_speed": 5,
        "max_speed": 100,
        "zones": {
            "chassis": {
                "enabled": True,
                "curve": [[0, 30], [10, 50], [20, 70]]
            },
            "cpu": {
                "enabled": True,
                "curve": [[0, 30], [10, 50], [20, 70]]
            }
        }
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
    with patch('builtins.open', mock_open(read_data=yaml.dump(TEST_CONFIG))) as mock_file:
        yield "config.yaml"

@pytest.fixture
def learner(mock_commander, mock_config_file):
    """Create a FanSpeedLearner instance"""
    return FanSpeedLearner(mock_commander, mock_config_file)

# Learning Process Tests

def test_learner_initialization(learner, mock_commander):
    """Test learner initialization"""
    assert learner.commander == mock_commander
    assert learner.config_path == "config.yaml"
    assert learner.min_test_duration == 5  # Default value
    assert learner.stability_threshold == 100  # Default value

def test_verify_fan_stability_stable(learner, mock_commander):
    """Test fan stability verification with stable speeds"""
    # Mock stable fan readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"},
        {"name": "FANA", "value": 3000.0, "state": "ok"}
    ]
    
    assert learner._verify_fan_stability() is True

def test_verify_fan_stability_unstable(learner, mock_commander):
    """Test fan stability verification with unstable speeds"""
    # Mock unstable fan readings (stopped fan)
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 0.0, "state": "ok"},  # Stopped fan
        {"name": "FAN2", "value": 1200.0, "state": "ok"}
    ]
    
    assert learner._verify_fan_stability() is False

def test_verify_fan_stability_no_reading(learner, mock_commander):
    """Test fan stability verification with no readings"""
    # Mock no reading state
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": None, "state": "ns"},
        {"name": "FAN2", "value": None, "state": "ns"}
    ]
    
    assert learner._verify_fan_stability() is False

def test_learn_zone_minimum_speed(learner, mock_commander):
    """Test learning minimum speed for a zone"""
    # Mock fan readings for different speeds
    readings_sequence = [
        # Initial speed (30%) - stable
        [
            {"name": "FAN1", "value": 1500.0, "state": "ok"},
            {"name": "FAN2", "value": 1200.0, "state": "ok"}
        ],
        # 20% speed - stable
        [
            {"name": "FAN1", "value": 1000.0, "state": "ok"},
            {"name": "FAN2", "value": 800.0, "state": "ok"}
        ],
        # 10% speed - unstable
        [
            {"name": "FAN1", "value": 0.0, "state": "ok"},  # Stopped
            {"name": "FAN2", "value": 500.0, "state": "ok"}
        ]
    ]
    mock_commander.get_sensor_readings.side_effect = readings_sequence
    
    min_speed = learner._learn_zone_minimum_speed("chassis", start_speed=30)
    assert min_speed == 20  # Should find 20% as minimum stable speed

def test_learn_zone_minimum_speed_already_minimum(learner, mock_commander):
    """Test learning when starting speed is already minimum"""
    # Mock stable fan readings at minimum speed
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 500.0, "state": "ok"},
        {"name": "FAN2", "value": 400.0, "state": "ok"}
    ]
    
    min_speed = learner._learn_zone_minimum_speed("chassis", start_speed=5)
    assert min_speed == 5  # Should keep minimum speed

def test_learn_min_speeds_success(learner, mock_commander):
    """Test successful learning of minimum speeds"""
    # Mock fan readings indicating stability at 20%
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1000.0, "state": "ok"},
        {"name": "FAN2", "value": 800.0, "state": "ok"},
        {"name": "FANA", "value": 2000.0, "state": "ok"}
    ]
    
    # Mock file operations
    mock_file = mock_open(read_data=yaml.dump(TEST_CONFIG))
    with patch('builtins.open', mock_file):
        min_speeds = learner.learn_min_speeds()
    
    assert min_speeds["chassis"] == 20
    assert min_speeds["cpu"] == 20
    
    # Verify config was updated
    mock_file().write.assert_called()

def test_learn_min_speeds_failure(learner, mock_commander):
    """Test learning failure handling"""
    # Mock fan readings indicating instability
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 0.0, "state": "ok"},  # Stopped fan
        {"name": "FAN2", "value": 0.0, "state": "ok"}   # Stopped fan
    ]
    
    # Should raise exception when can't find stable speeds
    with pytest.raises(Exception, match="Failed to find stable minimum speed"):
        learner.learn_min_speeds()

def test_config_update(learner, mock_commander):
    """Test configuration update with learned speeds"""
    # Mock stable fan readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1000.0, "state": "ok"},
        {"name": "FAN2", "value": 800.0, "state": "ok"}
    ]
    
    # Mock file operations
    config_data = yaml.dump(TEST_CONFIG)
    mock_file = mock_open(read_data=config_data)
    
    with patch('builtins.open', mock_file):
        learner.learn_min_speeds()
    
    # Verify config was written with updated speeds
    mock_file().write.assert_called()
    write_args = mock_file().write.call_args[0][0]
    updated_config = yaml.safe_load(write_args)
    
    # Verify fan curves were updated with new minimum speeds
    assert updated_config["fans"]["zones"]["chassis"]["curve"][0][1] == 20
    assert updated_config["fans"]["zones"]["cpu"]["curve"][0][1] == 20

def test_restore_on_failure(learner, mock_commander):
    """Test BMC control restoration on failure"""
    # Mock fan reading failure
    mock_commander.get_sensor_readings.side_effect = Exception("Sensor reading failed")
    
    # Should restore BMC control and re-raise exception
    with pytest.raises(Exception):
        learner.learn_min_speeds()
    
    # Verify BMC control was restored
    mock_commander.set_auto_mode.assert_called_once()

def test_learning_with_disabled_zone(learner, mock_commander):
    """Test learning with a disabled zone"""
    # Modify config to disable CPU zone
    modified_config = TEST_CONFIG.copy()
    modified_config["fans"]["zones"]["cpu"]["enabled"] = False
    
    # Mock file operations
    mock_file = mock_open(read_data=yaml.dump(modified_config))
    
    with patch('builtins.open', mock_file):
        min_speeds = learner.learn_min_speeds()
    
    # Should only learn chassis zone
    assert "chassis" in min_speeds
    assert "cpu" not in min_speeds

def test_minimum_speed_limit(learner, mock_commander):
    """Test enforcement of minimum speed limit"""
    # Mock stable fan readings at very low speed
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 300.0, "state": "ok"},
        {"name": "FAN2", "value": 250.0, "state": "ok"}
    ]
    
    min_speed = learner._learn_zone_minimum_speed("chassis", start_speed=10)
    assert min_speed >= 5  # Should not go below configured minimum
