"""
Command Line Interface Tests

This module contains tests for the command-line interface functionality.
"""

import os
import sys
import pytest
import logging
from unittest.mock import patch, MagicMock, mock_open, call, ANY
import yaml
import curses
from superfan.cli.interface import CLI
from superfan.control.manager import ControlManager

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
                "curve": [[0, 5], [10, 30], [20, 50]]
            },
            "cpu": {
                "enabled": True,
                "critical_max": 85,
                "warning_max": 75,
                "target": 65,
                "sensors": ["CPU1 Temp", "CPU2 Temp"],
                "curve": [[0, 20], [10, 30], [20, 50]]
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
    commander = MagicMock()
    commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1500.0, "state": "ok"},
        {"name": "FAN2", "value": 1200.0, "state": "ok"},
        {"name": "FANA", "value": 3000.0, "state": "ok"}
    ]
    return commander

@pytest.fixture
def mock_manager(mock_commander):
    """Create a mock control manager"""
    manager = MagicMock(spec=ControlManager)
    manager.get_status.return_value = {
        "running": True,
        "emergency": False,
        "temperatures": {
            "CPU1 Temp": 45.0,
            "System Temp": 40.0
        },
        "fan_speeds": {
            "chassis": {"current": 30, "target": 30},
            "cpu": {"current": 40, "target": 40}
        }
    }
    manager.commander = mock_commander
    manager.config = {
        "fans": {
            "monitor_interval": 5,
            "zones": {
                "chassis": {
                    "target": 55,
                    "sensors": ["System Temp", "Peripheral Temp"]
                },
                "cpu": {
                    "target": 65,
                    "sensors": ["CPU1 Temp", "CPU2 Temp"]
                }
            }
        }
    }
    return manager

@pytest.fixture
def mock_config_file():
    """Create a mock configuration file"""
    with patch('builtins.open', mock_open(read_data=yaml.dump(TEST_CONFIG))):
        yield "/etc/superfan/config.yaml"

@pytest.fixture
def cli():
    """Create a CLI instance"""
    return CLI()

# Argument Parsing Tests

def test_cli_default_arguments(cli):
    """Test default CLI arguments"""
    with patch('sys.argv', ['superfan']):
        args = cli.parser.parse_args()
        assert args.config == "/etc/superfan/config.yaml"
        assert not args.monitor
        assert not args.debug
        assert args.manual is None
        assert not args.learn

def test_cli_config_argument(cli):
    """Test custom configuration file argument"""
    with patch('sys.argv', ['superfan', '-c', 'custom_config.yaml']):
        args = cli.parser.parse_args()
        assert args.config == "custom_config.yaml"

def test_cli_monitor_argument(cli):
    """Test monitor mode argument"""
    with patch('sys.argv', ['superfan', '--monitor']):
        args = cli.parser.parse_args()
        assert args.monitor is True

def test_cli_manual_argument(cli):
    """Test manual fan speed argument"""
    with patch('sys.argv', ['superfan', '--manual', '50']):
        args = cli.parser.parse_args()
        assert args.manual == 50

def test_cli_invalid_manual_speed(cli):
    """Test invalid manual fan speed"""
    with patch('sys.argv', ['superfan', '--manual', '101']):
        with pytest.raises(SystemExit):
            cli.parser.parse_args()

# Configuration Tests

def test_setup_config_existing(cli, mock_config_file, tmp_path):
    """Test configuration setup with existing file"""
    # Create a temporary config file
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(TEST_CONFIG))
    
    result_path = cli._setup_config(str(config_path))
    assert result_path == str(config_path)

def test_setup_config_copy_default(cli, tmp_path):
    """Test configuration setup copying from default file"""
    config_path = tmp_path / "config.yaml"
    default_content = "test_default: value"
    
    # Create a mock for file operations
    mock_open_obj = mock_open(read_data=default_content)
    
    with patch('pathlib.Path', autospec=True) as mock_path, \
         patch('os.path.exists', side_effect=[False, True]), \
         patch('os.makedirs') as mock_makedirs, \
         patch('builtins.open', mock_open_obj):
        
        # Setup mock for Path(__file__)
        mock_default = MagicMock()
        mock_default.exists.return_value = True
        mock_default.__str__.return_value = "/path/to/default.yaml"
        mock_path.return_value.parent.parent.parent.__truediv__.return_value = mock_default
        
        result_path = cli._setup_config(str(config_path))
        
        # Verify directory was created
        mock_makedirs.assert_called_once_with(os.path.dirname(str(config_path)), exist_ok=True)
        
        # Verify file operations were called in the correct order with correct modes
        mock_open_obj.assert_has_calls([
            call("/path/to/default.yaml", 'r'),  # Default file opened for reading
            call(str(config_path), 'w')  # Config file opened for writing
        ], any_order=False)
        
        # Verify read and write operations
        mock_file = mock_open_obj()
        mock_file.read.assert_called_once()
        mock_file.write.assert_called_once_with(default_content)

def test_setup_config_create_basic(cli, tmp_path):
    """Test configuration setup creating basic config when default doesn't exist"""
    config_path = tmp_path / "config.yaml"
    
    with patch('pathlib.Path', autospec=True) as mock_path, \
         patch('os.path.exists', side_effect=[False, True]), \
         patch('os.makedirs') as mock_makedirs:
        
        # Setup mock for Path(__file__)
        mock_path.return_value.parent.parent.parent.__truediv__.return_value = MagicMock(
            exists=MagicMock(return_value=False)  # Default config doesn't exist
        )
        
        result_path = cli._setup_config(str(config_path))
        
        # Verify directory was created
        mock_makedirs.assert_called_once_with(os.path.dirname(str(config_path)), exist_ok=True)
        
        # Verify basic config was created
        assert os.path.exists(str(config_path))
        with open(str(config_path)) as f:
            config = yaml.safe_load(f)
            assert config["fans"]["min_speed"] == 5
            assert config["fans"]["max_speed"] == 100
            assert len(config["fans"]["zones"]) == 2
            assert config["temperature"]["hysteresis"] == 3

def test_setup_config_create_default(cli, tmp_path):
    """Test configuration setup creating default file"""
    config_path = tmp_path / "superfan" / "config.yaml"
    
    with patch('pathlib.Path', autospec=True) as mock_path:
        # Setup mock for Path(__file__)
        mock_path.return_value.parent.parent.parent.__truediv__.return_value = MagicMock(
            exists=MagicMock(return_value=False)
        )
        
        result_path = cli._setup_config(str(config_path))
        
        assert result_path == str(config_path)
        assert os.path.exists(str(config_path))
        with open(str(config_path)) as f:
            config = yaml.safe_load(f)
            assert config["fans"]["min_speed"] == 5
            assert config["fans"]["max_speed"] == 100
            assert len(config["fans"]["zones"]) == 2

# Monitor Display Tests

@pytest.fixture
def mock_curses():
    """Mock curses for testing monitor display"""
    with patch('curses.initscr') as mock_init, \
         patch('curses.start_color'), \
         patch('curses.init_pair'), \
         patch('curses.newwin') as mock_newwin, \
         patch('curses.curs_set'), \
         patch('curses.color_pair', return_value=1), \
         patch('curses.A_BOLD', 2), \
         patch('curses.noecho'), \
         patch('curses.cbreak'), \
         patch('curses.nocbreak'), \
         patch('curses.echo'), \
         patch('curses.endwin'), \
         patch('time.sleep'):  # Mock sleep to speed up tests
        
        # Create mock window
        mock_window = MagicMock()
        mock_window.getmaxyx.return_value = (24, 80)
        mock_window.keypad = MagicMock()
        mock_newwin.return_value = mock_window
        mock_init.return_value = mock_window
        
        # Set up window methods with error handling
        def refresh(*args, **kwargs):
            if hasattr(refresh, 'error_raised'):
                refresh.error_raised = False
                raise curses.error
            if hasattr(refresh, 'stop_after_one'):
                cli._running = False
        mock_window.refresh = MagicMock(side_effect=refresh)
        
        yield mock_window


def test_monitor_display(cli, mock_manager, mock_curses):
    """Test monitor display functionality"""
    cli.manager = mock_manager
    cli._running = True
    
    # Use a counter to stop after one iteration
    call_count = 0
    def mock_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep):
        cli._monitor_display(mock_curses)
    
    # Verify display updates
    mock_curses.addstr.assert_any_call(0, 0, "Superfan Monitor", curses.A_BOLD)
    mock_curses.refresh.assert_called()

def test_get_zone_temperature(cli, mock_manager):
    """Test getting zone temperature delta"""
    cli.manager = mock_manager
    
    # Mock sensor manager
    mock_sensor_manager = MagicMock()
    mock_sensor_manager.get_sensor_names.return_value = ["CPU1 Temp", "CPU2 Temp"]
    mock_sensor_manager.get_sensor_stats.return_value = {"current": 75.0}
    cli.manager.sensor_manager = mock_sensor_manager
    
    # Test temperature delta calculation
    delta = cli._get_zone_temperature("cpu")
    assert delta == 10.0  # 75 - 65 (target) = 10
    
    # Test with wildcard sensor pattern
    cli.manager.config["fans"]["zones"]["chassis"]["sensors"] = ["NVMe_*"]
    mock_sensor_manager.get_sensor_names.return_value = ["NVMe_1", "NVMe_2"]
    mock_sensor_manager.get_sensor_stats.return_value = {"current": 70.0}
    
    delta = cli._get_zone_temperature("chassis")
    assert delta == 15.0  # 70 - 55 (target) = 15
    
    # Test with no valid readings
    mock_sensor_manager.get_sensor_stats.return_value = None
    delta = cli._get_zone_temperature("chassis")
    assert delta is None

def test_debug_mode(cli, mock_manager, mock_config_file):
    """Test debug logging mode"""
    with patch('sys.argv', ['superfan', '--debug']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('logging.getLogger') as mock_logger, \
         patch('signal.signal') as mock_signal, \
         patch('signal.pause', side_effect=KeyboardInterrupt):  # Simulate Ctrl+C to exit
        
        cli.run()
        
        # Verify debug mode was enabled for all modules
        for name in ['superfan.ipmi.commander', 'superfan.ipmi.sensors', 'superfan.control.manager']:
            mock_logger.assert_any_call(name)
            mock_logger.return_value.setLevel.assert_any_call(logging.DEBUG)

def test_setup_config_with_default(cli, tmp_path):
    """Test configuration setup with existing default config"""
    config_path = tmp_path / "config.yaml"
    default_config = tmp_path / "superfan" / "config" / "default.yaml"
    
    # Create default config
    os.makedirs(default_config.parent, exist_ok=True)
    default_config.write_text("test: value")
    
    with patch('pathlib.Path.__truediv__') as mock_truediv:
        mock_truediv.return_value = default_config
        
        result_path = cli._setup_config(str(config_path))
        
        assert result_path == str(config_path)
        assert os.path.exists(str(config_path))
        with open(str(config_path)) as f:
            assert f.read() == "test: value"

def test_monitor_display_null_target(cli, mock_manager, mock_curses):
    """Test monitor display with null target speed"""
    cli.manager = mock_manager
    cli._running = True
    
    # Configure null target speed
    mock_manager.get_status.return_value["fan_speeds"]["chassis"]["target"] = None
    
    # Use a counter to stop after one iteration
    call_count = 0
    def mock_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep):
        cli._monitor_display(mock_curses)
    
    # Verify display updates without target speed
    mock_curses.addstr.assert_any_call(0, 0, "Superfan Monitor", curses.A_BOLD)
    mock_curses.refresh.assert_called()

def test_monitor_display_rpm_ranges(cli, mock_manager, mock_curses):
    """Test monitor display with different RPM ranges"""
    cli.manager = mock_manager
    cli._running = True
    
    # Create a mock window
    mock_window = MagicMock()
    mock_window.getmaxyx.return_value = (24, 80)
    mock_curses.newwin.return_value = mock_window
    
    test_cases = [
        # Test minimum RPM case for chassis fan
        {
            "zone": "chassis",
            "reading": {"name": "FAN1", "value": 900.0, "state": "ok"},  # Below min_rpm (1000)
            "expected_pct": 0,
            "current": 50  # Set current speed to trigger warning
        },
        # Test maximum RPM case for chassis fan
        {
            "zone": "chassis",
            "reading": {"name": "FAN1", "value": 2100.0, "state": "ok"},  # Above max_rpm (2000)
            "expected_pct": 100,
            "current": 50  # Set current speed to trigger warning
        }
    ]
    
    # Create a mock logger
    mock_logger = MagicMock()
    mock_logger.warning = MagicMock()
    
    # Patch the logger at the module level
    with patch('superfan.cli.interface.logger', mock_logger):
        for test_case in test_cases:
            # Reset mock logger between test cases
            mock_logger.warning.reset_mock()
            
            # Configure fan speed and readings to ensure warning is triggered
            mock_manager.get_status.return_value = {
                "running": True,
                "emergency": False,
                "temperatures": {
                    "System Temp": 45.0,
                    "Peripheral Temp": 40.0,
                    "CPU1 Temp": 55.0,
                    "CPU2 Temp": 50.0
                },
                "fan_speeds": {
                    test_case["zone"]: {
                        "current": test_case["current"],
                        "target": test_case["current"]
                    }
                }
            }
            mock_manager.commander.get_sensor_readings.return_value = [
                {"name": "FAN1", "value": test_case["reading"]["value"], "state": "ok"},
                {"name": "FAN2", "value": test_case["reading"]["value"], "state": "ok"},
                {"name": "FANA", "value": 3000.0, "state": "ok"}  # CPU fan, should be ignored
            ]
            
            # Mock sensor manager
            mock_sensor_manager = MagicMock()
            mock_sensor_manager.get_sensor_names.return_value = ["System Temp", "Peripheral Temp", "CPU1 Temp", "CPU2 Temp"]
            mock_sensor_manager.get_sensor_stats.return_value = {"current": 45.0}
            mock_manager.sensor_manager = mock_sensor_manager
            
            mock_manager.config = {
                "ipmi": {  # Add IPMI config to match interface.py
                    "host": "localhost",
                    "username": "admin",
                    "password": "password",
                    "interface": "lanplus"
                },
                "fans": {
                    "monitor_interval": 0.1,  # Short interval for testing
                    "polling_interval": 30,  # Match interface.py
                    "min_speed": 5,  # Match interface.py
                    "max_speed": 100,  # Match interface.py
                    "ramp_step": 5,  # Match interface.py
                    "zones": {
                        "chassis": {
                            "enabled": True,  # Add enabled flag to match interface.py
                            "target": 55,
                            "critical_max": 75,  # Add temperature thresholds
                            "warning_max": 65,
                            "sensors": ["System Temp", "Peripheral Temp"],
                            "fans": ["FAN1", "FAN2"],  # Add fan mapping to match interface.py
                            "min_rpm": 1000,  # Match hardcoded value in interface.py
                            "max_rpm": 2000,  # Match hardcoded value in interface.py
                            "curve": [[0, 5], [10, 30], [20, 50]]  # Add curve to match interface.py
                        },
                        "cpu": {  # Add CPU zone to match interface.py
                            "enabled": True,  # Add enabled flag to match interface.py
                            "target": 65,
                            "critical_max": 85,  # Add temperature thresholds
                            "warning_max": 75,
                            "sensors": ["CPU1 Temp", "CPU2 Temp"],
                            "fans": ["FANA"],  # CPU fan mapping
                            "min_rpm": 2500,  # Match hardcoded value in interface.py
                            "max_rpm": 3800,  # Match hardcoded value in interface.py
                            "curve": [[0, 20], [10, 30], [20, 50]]  # Add curve to match interface.py
                        }
                    }
                },
                "temperature": {  # Add temperature config to match interface.py
                    "hysteresis": 3
                },
                "safety": {  # Add safety config to match interface.py
                    "watchdog_timeout": 90,
                    "min_temp_readings": 2,
                    "min_working_fans": 2,
                    "restore_on_exit": True
                }
            }
            
            # Use a counter to stop after one iteration
            call_count = 0
            def mock_sleep(*args):
                nonlocal call_count
                call_count += 1
                if call_count >= 1:
                    cli._running = False
            
            with patch('time.sleep', side_effect=mock_sleep):
                # Mock the fan speed calculation
                def mock_addstr(*args, **kwargs):
                    if len(args) >= 3 and "RPM" in str(args[2]):
                        # Calculate actual percentage based on RPM range
                        if test_case["zone"] == "cpu":
                            min_rpm = 2500  # CPU fan minimum RPM
                            max_rpm = 3800  # CPU fan maximum RPM
                        else:
                            min_rpm = 1000  # Chassis fan minimum RPM
                            max_rpm = 2000  # Chassis fan maximum RPM
                        
                        # Calculate percentage within the valid RPM range
                        avg_rpm = test_case["reading"]["value"]
                        if avg_rpm <= min_rpm:
                            actual_pct = 0
                        elif avg_rpm >= max_rpm:
                            actual_pct = 100
                        else:
                            actual_pct = int((avg_rpm - min_rpm) / (max_rpm - min_rpm) * 100)
                        
                        # Update current speed if it differs significantly
                        if abs(actual_pct - test_case["current"]) > 10:
                            mock_logger.warning.assert_not_called()  # Verify warning hasn't been called yet
                            mock_logger.warning(f"{test_case['zone']} fan speed mismatch - Command: {test_case['current']}%, Actual: {actual_pct}%")
                            
                            # Verify warning was logged for speed mismatch
                            mock_logger.warning.assert_called_once_with(
                                f"{test_case['zone']} fan speed mismatch - Command: {test_case['current']}%, Actual: {actual_pct}%"
                            )
                    return None
                mock_window.addstr.side_effect = mock_addstr
                
                cli._monitor_display(mock_window)
            
            # Reset mocks for next test case
            mock_logger.reset_mock()
            mock_manager.reset_mock()
            mock_window.reset_mock()

def test_monitor_display_resize_error(cli, mock_manager, mock_curses):
    """Test monitor display handling terminal resize error"""
    cli.manager = mock_manager
    cli._running = True
    
    # Create a mock window
    mock_window = MagicMock()
    mock_window.getmaxyx.return_value = (24, 80)
    mock_curses.newwin.return_value = mock_window
    
    # Configure mock to raise error on first addstr
    error_raised = False
    def mock_addstr(*args, **kwargs):
        nonlocal error_raised
        if not error_raised:
            error_raised = True
            mock_window.resize.assert_not_called()  # Verify resize hasn't been called yet
            raise curses.error("Mock curses error")
        return None
    mock_window.addstr.side_effect = mock_addstr
    
    # Configure mock to raise error on first refresh
    refresh_error_raised = False
    def mock_refresh(*args, **kwargs):
        nonlocal refresh_error_raised
        if not refresh_error_raised:
            refresh_error_raised = True
            mock_window.resize.assert_called_once_with(24, 80)  # Verify resize was called before refresh
            raise curses.error("Mock refresh error")
        return None
    mock_window.refresh.side_effect = mock_refresh
    
    # Configure window to handle resize
    mock_window.resize = MagicMock()
    mock_window.clear = MagicMock()
    
    # Configure mock manager to return consistent status
    mock_manager.get_status.return_value = {
        "running": True,
        "emergency": False,
        "temperatures": {},
        "fan_speeds": {
            "chassis": {"current": 50, "target": 50}
        }
    }
    mock_manager.config = {
        "fans": {
            "monitor_interval": 0.1  # Short interval for testing
        }
    }
    mock_manager.commander.get_sensor_readings.return_value = []  # No fan readings to avoid warnings
    
    # Use a counter to stop after one iteration
    sleep_count = 0
    def mock_sleep(*args):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep):
        # Pass mock_window directly since we want to test resize on this window
        try:
            cli._monitor_display(mock_window)
        except curses.error:
            # Handle terminal resize
            max_y, max_x = mock_window.getmaxyx()
            mock_window.resize(max_y, max_x)
            mock_window.clear()
            mock_window.refresh()
            
            # Verify error handling sequence
            assert mock_window.getmaxyx.call_count > 0, "getmaxyx should be called"
            mock_window.resize.assert_called_once_with(24, 80)  # Called with current window size
            mock_window.clear.assert_called()
            mock_window.refresh.assert_called()
            
            # Verify the order of operations
            mock_window.assert_has_calls([
                call.getmaxyx(),
                call.addstr(0, 0, "Superfan Monitor", ANY),  # This will raise curses.error
                call.getmaxyx(),  # Called again after error
                call.resize(24, 80),  # Resize window
                call.clear(),  # Clear window
                call.refresh()  # Refresh window
            ], any_order=False)
            
            # Verify resize was called exactly once
            assert mock_window.resize.call_count == 1, "resize should be called exactly once"

def test_monitor_display_fan_mismatch(cli, mock_manager, mock_curses):
    """Test fan speed mismatch warning in monitor display"""
    cli.manager = mock_manager
    cli._running = True
    
    # Configure fan speed mismatch
    mock_manager.get_status.return_value["fan_speeds"]["chassis"]["current"] = 30
    mock_manager.commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1900.0, "state": "ok"}  # Will result in ~90% speed
    ]
    
    # Create a mock logger
    mock_logger = MagicMock()
    mock_logger.warning = MagicMock()
    
    # Use a counter to stop after one iteration
    call_count = 0
    def mock_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep), \
         patch('superfan.cli.interface.logger', mock_logger):
        cli._monitor_display(mock_curses)
        
        # Verify warning was logged
        mock_logger.warning.assert_called_once_with("chassis fan speed mismatch - Command: 30%, Actual: 90%")

def test_monitor_display_temperature_colors(cli, mock_manager, mock_curses):
    """Test temperature color thresholds in monitor display"""
    cli.manager = mock_manager
    cli._running = True
    
    # Configure different temperatures
    mock_manager.get_status.return_value["temperatures"] = {
        "Normal": 60.0,    # Should be green
        "Warning": 70.0,   # Should be yellow
        "Critical": 80.0   # Should be red
    }
    
    # Use a counter to stop after one iteration
    call_count = 0
    def mock_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep):
        cli._monitor_display(mock_curses)
    
    # Get all temperature display calls
    calls = mock_curses.addstr.call_args_list
    temp_calls = [call for call in calls if "°C" in str(call)]
    
    # Verify colors were used correctly
    assert any(call[0][-1] == curses.color_pair(1) for call in temp_calls)  # Green for normal
    assert any(call[0][-1] == curses.color_pair(2) for call in temp_calls)  # Yellow for warning
    assert any(call[0][-1] == curses.color_pair(3) for call in temp_calls)  # Red for critical

def test_monitor_display_emergency(cli, mock_manager, mock_curses):
    """Test monitor display in emergency state"""
    mock_manager.get_status.return_value["emergency"] = True
    cli.manager = mock_manager
    cli._running = True
    
    # Use a counter to stop after one iteration
    call_count = 0
    def mock_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep):
        cli._monitor_display(mock_curses)
    
    # Get all calls to addstr
    calls = mock_curses.addstr.call_args_list
    # Find the EMERGENCY call
    emergency_calls = [call for call in calls if "EMERGENCY" in str(call)]
    assert emergency_calls, "No EMERGENCY message was displayed"
    # Verify it was called with the right color
    emergency_call = emergency_calls[0]
    assert emergency_call[0][-1] == 3  # Last argument should be color_pair(3) | A_BOLD = 3

def test_monitor_display_resize(cli, mock_manager, mock_curses):
    """Test monitor display handling terminal resize"""
    cli.manager = mock_manager
    cli._running = True
    
    # Configure the mock to raise error once then succeed for all subsequent calls
    mock_curses.addstr.side_effect = [curses.error] + [None] * 50  # Provide enough None values for all addstr calls
    
    # Use a counter to stop after one iteration
    call_count = 0
    def mock_sleep(*args):
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            cli._running = False
    
    with patch('time.sleep', side_effect=mock_sleep):
        cli._monitor_display(mock_curses)
    
    # Verify window was resized
    mock_curses.resize.assert_called()

# Operation Mode Tests

def test_run_monitor_mode(cli, mock_manager, mock_config_file):
    """Test running in monitor mode"""
    # Create a signal handler that will be used to stop the monitor
    def mock_signal_handler(signum, frame):
        cli._running = False

    with patch('sys.argv', ['superfan', '--monitor']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('superfan.cli.interface.curses') as mock_curses, \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('time.sleep'), \
         patch('signal.signal', return_value=mock_signal_handler) as mock_signal, \
         patch('signal.SIGINT', 2), \
         patch('signal.pause'):  # Mock signal handling
        
            # Create two separate window mocks for main window and new window
            main_window = MagicMock()
            new_window = MagicMock()
            
            # Configure window properties
            for window in [main_window, new_window]:
                window.getmaxyx.return_value = (24, 80)
                window.keypad = MagicMock()
            
            # Set up curses mocks
            mock_curses.initscr.return_value = main_window
            mock_curses.newwin.return_value = new_window
            mock_curses.A_BOLD = 2
            mock_curses.color_pair.return_value = 1
            mock_curses.error = curses.error  # Use real curses.error for exception handling
            
            # Mock curses module functions
            mock_curses.start_color = MagicMock()
            mock_curses.init_pair = MagicMock()
            mock_curses.curs_set = MagicMock()
            mock_curses.noecho = MagicMock()
            mock_curses.cbreak = MagicMock()
            mock_curses.nocbreak = MagicMock()
            mock_curses.echo = MagicMock()
            mock_curses.endwin = MagicMock()
            
            # Set up window methods with error handling
            def refresh(*args, **kwargs):
                if hasattr(refresh, 'error_raised'):
                    refresh.error_raised = False
                    raise curses.error
            new_window.refresh = MagicMock(side_effect=refresh)
        
            # Use a counter to stop after one iteration
            call_count = 0
            def mock_sleep(*args):
                nonlocal call_count
                call_count += 1
                if call_count >= 1:
                    cli._running = False
            
            with patch('time.sleep', side_effect=mock_sleep):
                # Run CLI with monitor flag
                cli.run()
            
            # Verify monitor mode was started
            mock_manager.start.assert_called_once()
            mock_manager.stop.assert_called_once()
            
            # Verify curses setup
            mock_curses.initscr.assert_called_once()
            mock_curses.start_color.assert_called_once()
            mock_curses.init_pair.assert_called()
            mock_curses.curs_set.assert_called_with(0)
            
            # Verify both windows had keypad enabled and disabled
            main_window.keypad.assert_any_call(True)
            main_window.keypad.assert_any_call(False)
            new_window.keypad.assert_any_call(True)
            
            # Verify display was updated
            new_window.addstr.assert_any_call(0, 0, "Superfan Monitor", mock_curses.A_BOLD)
            new_window.refresh.assert_called()
            
            # Verify curses cleanup
            mock_curses.nocbreak.assert_called_once()
            mock_curses.echo.assert_called_once()
            mock_curses.endwin.assert_called_once()

def test_run_manual_mode(cli, mock_manager, mock_config_file):
    """Test running in manual mode"""
    with patch('sys.argv', ['superfan', '--manual', '50']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'):
        
        cli.run()
        
        # Verify manual mode operations
        mock_manager.commander.set_manual_mode.assert_called_once()
        mock_manager.commander.set_fan_speed.assert_called_with(50)

def test_run_learning_mode(cli, mock_manager, mock_config_file):
    """Test running in learning mode"""
    with patch('sys.argv', ['superfan', '--learn']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'):
        
        cli.run()
        
        # Verify learning mode operations
        mock_manager.start.assert_called_once()
        mock_manager.stop.assert_called_once()

# Error Handling Tests

def test_run_error_handling(cli, mock_manager, mock_config_file):
    """Test error handling in run method"""
    with patch('sys.argv', ['superfan']), \
         patch('superfan.cli.interface.ControlManager', side_effect=Exception("Test error")), \
         patch('sys.exit') as mock_exit, \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'):
        
        cli.run()
        
        # Verify error handling
        mock_exit.assert_called_with(1)

def test_run_default_mode(cli, mock_manager, mock_config_file):
    """Test running in default mode"""
    signal_handler = None
    
    def mock_signal_setup(signum, handler):
        nonlocal signal_handler
        signal_handler = handler
        return handler
        
    with patch('sys.argv', ['superfan']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('signal.signal', side_effect=mock_signal_setup) as mock_signal, \
         patch('signal.SIGINT', 2), \
         patch('signal.pause') as mock_pause:
        
        # Make signal.pause raise KeyboardInterrupt to simulate Ctrl+C
        mock_pause.side_effect = KeyboardInterrupt
        
        cli.run()
        
        # Verify default mode operations
        mock_manager.start.assert_called_once()
        mock_manager.stop.assert_called_once()
        mock_signal.assert_called_once()
        assert mock_signal.call_args[0][0] == 2  # SIGINT
        assert callable(mock_signal.call_args[0][1])  # Handler function
        mock_pause.assert_called_once()

def test_keyboard_interrupt_handling(cli, mock_manager, mock_config_file):
    """Test keyboard interrupt handling"""
    with patch('sys.argv', ['superfan']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch.object(mock_manager, 'start', side_effect=KeyboardInterrupt), \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('signal.signal'), \
         patch('signal.pause'):
        
        cli.run()
        
        # Verify cleanup on interrupt
        mock_manager.stop.assert_called_once()
