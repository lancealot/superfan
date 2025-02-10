"""
Fan Speed Learner Tests

This module contains tests for the fan speed learning functionality.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, mock_open, call
import yaml
from superfan.control.learner import FanSpeedLearner
from superfan.ipmi.commander import IPMICommander, IPMIError

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

# Initialization Tests

def test_learner_initialization(learner, mock_commander):
    """Test learner initialization"""
    assert learner.commander == mock_commander
    assert learner.config_path == "config.yaml"
    assert isinstance(learner.config, dict)
    assert "fans" in learner.config

# Speed Stability Tests

def test_is_speed_stable_check_duration(learner, mock_commander):
    """Test speed stability with different check durations"""
    # Mock stable fan readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"}
    ]
    
    start_time = time.time()
    learner._is_speed_stable(30, "chassis", check_duration=3)
    duration = time.time() - start_time
    
    assert 3 <= duration <= 4  # Allow small overhead

def test_zone_specific_fan_filtering(learner, mock_commander):
    """Test fan filtering by zone"""
    # Mock mixed fan readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"},
        {"name": "FANA", "value": 3000.0, "state": "ok"},
        {"name": "FANB", "value": None, "state": "ns"}
    ]
    
    # Test chassis zone (should only check FAN1, FAN2)
    learner._is_speed_stable(30, "chassis")
    
    # Test CPU zone (should only check FANA)
    learner._is_speed_stable(30, "cpu")

def test_non_responsive_fan_handling(learner, mock_commander):
    """Test handling of non-responsive fans"""
    # Mock readings with mix of responsive and non-responsive fans
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": None, "state": "ns"},    # Non-responsive
        {"name": "FAN3", "value": 1200.0, "state": "ok"}
    ]
    
    # Should still be stable if enough fans are responsive
    assert learner._is_speed_stable(30, "chassis") is True

def test_rpm_threshold_validation(learner, mock_commander):
    """Test RPM threshold validation"""
    test_cases = [
        # RPM, Expected Result
        (1500.0, True),   # Normal speed
        (99.0, False),    # Below threshold
        (0.0, False),     # Stopped
        (None, False)     # No reading
    ]
    
    for rpm, expected in test_cases:
        mock_commander.get_sensor_readings.return_value = [
            {"name": "FAN1", "value": rpm, "state": "ok"}
        ]
        assert learner._is_speed_stable(30, "chassis") is expected

# Learning Process Tests

def test_speed_decrement_behavior(learner, mock_commander):
    """Test gradual speed reduction during learning"""
    # Track tested speeds
    tested_speeds = []
    
    def mock_is_stable(speed, zone, check_duration=10):
        tested_speeds.append(speed)
        return speed >= 20  # Stable above 20%
    
    with patch.object(learner, '_is_speed_stable', side_effect=mock_is_stable):
        min_speed = learner._learn_zone_minimum_speed("chassis", start_speed=30)
    
    # Verify speed was reduced in 2% increments
    assert tested_speeds == [30, 28, 26, 24, 22, 20, 18]
    assert min_speed == 20

def test_minimum_speed_threshold(learner, mock_commander):
    """Test minimum speed threshold enforcement"""
    def mock_is_stable(speed, zone, check_duration=10):
        return True  # Always stable
    
    with patch.object(learner, '_is_speed_stable', side_effect=mock_is_stable):
        min_speed = learner._learn_zone_minimum_speed("chassis", start_speed=15)
    
    # Should not test below 8%
    assert min_speed >= 8

def test_manual_mode_handling(learner, mock_commander):
    """Test manual mode entry and exit"""
    # Mock stable readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"}
    ]
    
    learner.learn_min_speeds()
    
    # Verify manual mode was entered and exited
    mock_commander.set_manual_mode.assert_called_once()
    mock_commander.set_auto_mode.assert_called_once()

def test_error_handling_during_learning(learner, mock_commander):
    """Test error handling during learning process"""
    # Mock error during learning
    mock_commander.set_fan_speed.side_effect = IPMIError("Fan control failed")
    
    with pytest.raises(Exception):
        learner.learn_min_speeds()
    
    # Verify auto mode was restored
    mock_commander.set_auto_mode.assert_called_once()

# Configuration Update Tests

def test_config_backup_creation(learner, mock_commander):
    """Test configuration backup before updates"""
    # Mock successful learning
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"}
    ]
    
    # Mock file operations
    mock_file = mock_open(read_data=yaml.dump(TEST_CONFIG))
    
    with patch('builtins.open', mock_file) as mocked_open:
        learner.learn_min_speeds()
        
        # Verify backup was created
        mocked_open.assert_any_call(learner.config_path + '.bak', 'w')

def test_invalid_config_handling(learner, mock_commander):
    """Test handling of invalid configuration"""
    # Create invalid config
    invalid_config = {"fans": {"invalid": "config"}}
    
    with patch('builtins.open', mock_open(read_data=yaml.dump(invalid_config))):
        with pytest.raises(KeyError):
            learner = FanSpeedLearner(mock_commander, "config.yaml")

def test_partial_learning_results(learner, mock_commander):
    """Test handling of partial learning results"""
    def mock_learn_zone(zone, start_speed=30):
        if zone == "chassis":
            return 20
        else:
            raise IPMIError("Learning failed for CPU zone")
    
    with patch.object(learner, '_learn_zone_minimum_speed', side_effect=mock_learn_zone):
        with pytest.raises(IPMIError):
            learner.learn_min_speeds()
        
        # Verify auto mode was restored
        mock_commander.set_auto_mode.assert_called_once()

def test_verify_fan_stability_stable(learner, mock_commander):
    """Test fan stability verification with stable speeds"""
    # Mock stable fan readings
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"},
        {"name": "FANA", "value": 3000.0, "state": "ok"}
    ]
    
    assert learner._is_speed_stable(30, "chassis") is True

def test_verify_fan_stability_unstable(learner, mock_commander):
    """Test fan stability verification with unstable speeds"""
    # Mock unstable fan readings (stopped fan)
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 0.0, "state": "ok"},  # Stopped fan
        {"name": "FAN2", "value": 1200.0, "state": "ok"}
    ]
    
    assert learner._is_speed_stable(30, "chassis") is False

def test_verify_fan_stability_no_reading(learner, mock_commander):
    """Test fan stability verification with no readings"""
    # Mock no reading state
    mock_commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": None, "state": "ns"},
        {"name": "FAN2", "value": None, "state": "ns"}
    ]
    
    assert learner._is_speed_stable(30, "chassis") is False

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
    
    min_speed = learner._learn_zone_minimum_speed("chassis", start_speed=8)
    assert min_speed == 8  # Should keep minimum speed

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
    with pytest.raises(Exception, match="Learning failed"):
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
    assert updated_config["fans"]["min_speed"] >= 8  # Should not go below minimum

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
    assert min_speed >= 8  # Should not go below minimum threshold
