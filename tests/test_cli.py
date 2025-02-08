"""
Command Line Interface Tests

This module contains tests for the command-line interface functionality.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock, mock_open
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
    
    # Configure the mock to stop after one iteration
    mock_curses.refresh.side_effect.stop_after_one = True
    
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
         patch('logging.getLogger') as mock_logger:
        
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

def test_monitor_display_fan_mismatch(cli, mock_manager, mock_curses):
    """Test fan speed mismatch warning in monitor display"""
    cli.manager = mock_manager
    cli._running = True
    
    # Configure fan speed mismatch
    mock_manager.get_status.return_value["fan_speeds"]["chassis"]["current"] = 30
    mock_manager.commander.get_sensor_readings.return_value = [
        {"name": "FAN1", "value": 1900.0, "state": "ok"}  # Will result in ~90% speed
    ]
    
    # Configure the mock to stop after one iteration
    mock_curses.refresh.side_effect.stop_after_one = True
    
    with patch('logging.Logger.warning') as mock_warning:
        cli._monitor_display(mock_curses)
        
        # Verify warning was logged
        mock_warning.assert_called_with("chassis fan speed mismatch - Command: 30%, Actual: 90%")

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
    
    # Configure the mock to stop after one iteration
    mock_curses.refresh.side_effect.stop_after_one = True
    
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
    
    # Configure the mock to stop after one iteration
    mock_curses.refresh.side_effect.stop_after_one = True
    
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
    
    # Configure the mock to raise error once and stop after one iteration
    mock_curses.addstr.side_effect = [curses.error, None]
    mock_curses.refresh.side_effect.error_raised = True
    mock_curses.refresh.side_effect.stop_after_one = True
    
    cli._monitor_display(mock_curses)
    
    # Verify window was resized
    mock_curses.resize.assert_called()

# Operation Mode Tests

def test_run_monitor_mode(cli, mock_manager, mock_config_file):
    """Test running in monitor mode"""
    with patch('sys.argv', ['superfan', '--monitor']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('superfan.cli.interface.curses') as mock_curses, \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('time.sleep'), \
         patch('signal.signal') as mock_signal, \
         patch('signal.SIGINT', 2), \
         patch('signal.pause'):  # Mock signal handling
        
        # Mock curses setup
        mock_window = MagicMock()
        mock_window.getmaxyx.return_value = (24, 80)
        mock_window.keypad = MagicMock()
        mock_curses.initscr.return_value = mock_window
        mock_curses.newwin.return_value = mock_window
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
            if hasattr(refresh, 'stop_after_one'):
                cli._running = False
        mock_window.refresh = MagicMock(side_effect=refresh)
        
        # Configure the mock to stop after one iteration
        mock_window.refresh.side_effect.stop_after_one = True
        
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
        mock_window.keypad.assert_called_with(True)
        
        # Verify display was updated
        mock_window.addstr.assert_any_call(0, 0, "Superfan Monitor", mock_curses.A_BOLD)
        mock_window.refresh.assert_called()
        
        # Verify curses cleanup
        mock_curses.nocbreak.assert_called_once()
        mock_window.keypad.assert_any_call(False)
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
    with patch('sys.argv', ['superfan']), \
         patch('superfan.cli.interface.ControlManager', return_value=mock_manager), \
         patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('signal.signal') as mock_signal, \
         patch('signal.SIGINT', 2), \
         patch('signal.pause') as mock_pause:
        
        # Make signal.pause raise KeyboardInterrupt to simulate Ctrl+C
        mock_pause.side_effect = KeyboardInterrupt
        
        cli.run()
        
        # Verify default mode operations
        mock_manager.start.assert_called_once()
        mock_manager.stop.assert_called_once()
        mock_signal.assert_called_once()
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
