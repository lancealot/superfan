"""
Temperature Sensor Tests

This module contains tests for the temperature sensor functionality.
"""

import time
import pytest
from unittest.mock import patch, MagicMock, call
from superfan.ipmi.sensors import (
    SensorReading,
    NVMETemperatureReader,
    SensorReader,
    CombinedTemperatureReader
)
from superfan.ipmi.commander import IPMICommander, IPMIError

# Fixtures

@pytest.fixture
def mock_commander():
    """Create a mock IPMI commander"""
    commander = MagicMock(spec=IPMICommander)
    commander.get_sensor_readings.return_value = []
    return commander

@pytest.fixture
def mock_subprocess():
    """Mock subprocess for NVMe operations"""
    with patch('subprocess.run') as mock_run:
        yield mock_run

# SensorReading Tests

def test_sensor_reading_age():
    """Test sensor reading age calculation"""
    current_time = time.time()
    reading = SensorReading(
        name="CPU1 Temp",
        value=45.0,
        timestamp=current_time - 10,  # 10 seconds old
        state="ok"
    )
    assert 9.9 <= reading.age <= 10.1  # Allow small time difference

def test_sensor_reading_is_critical():
    """Test critical state detection"""
    reading = SensorReading(
        name="CPU1 Temp",
        value=90.0,
        timestamp=time.time(),
        state="cr"
    )
    assert reading.is_critical is True

def test_sensor_reading_is_valid():
    """Test reading validity checks"""
    # Valid reading
    reading1 = SensorReading(
        name="CPU1 Temp",
        value=45.0,
        timestamp=time.time(),
        state="ok"
    )
    assert reading1.is_valid is True

    # Invalid reading (no reading state)
    reading2 = SensorReading(
        name="CPU1 Temp",
        value=None,
        timestamp=time.time(),
        state="ns"
    )
    assert reading2.is_valid is False

def test_sensor_reading_response_id():
    """Test response ID tracking"""
    reading = SensorReading(
        name="CPU1 Temp",
        value=45.0,
        timestamp=time.time(),
        state="ok",
        response_id=123
    )
    assert reading.response_id == 123

# NVMETemperatureReader Tests

def test_nvme_discover_drives(mock_subprocess):
    """Test NVMe drive discovery"""
    mock_subprocess.return_value = MagicMock(
        stdout="/dev/nvme0n1\n/dev/nvme1n1",
        stderr="",
        returncode=0
    )
    
    reader = NVMETemperatureReader()
    assert reader.drives == ["/dev/nvme0n1", "/dev/nvme1n1"]

def test_nvme_discover_drives_error(mock_subprocess):
    """Test NVMe drive discovery error handling"""
    mock_subprocess.side_effect = subprocess.CalledProcessError(1, "nvme list", "Error")
    
    reader = NVMETemperatureReader()
    assert reader.drives == []

def test_nvme_update_readings(mock_subprocess):
    """Test NVMe temperature reading updates"""
    # Mock nvme list for drive discovery
    def mock_command(cmd, *args, **kwargs):
        if cmd[1] == "list":
            return MagicMock(
                stdout="/dev/nvme0n1",
                stderr="",
                returncode=0
            )
        elif cmd[1] == "smart-log":
            return MagicMock(
                stdout="temperature : 35 C",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_command
    
    reader = NVMETemperatureReader()
    reader.update_readings()
    
    stats = reader.get_sensor_stats("NVMe_nvme0n1")
    assert stats is not None
    assert stats["current"] == 35.0

def test_nvme_temperature_parsing_formats(mock_subprocess):
    """Test parsing different temperature formats"""
    test_cases = [
        ("temperature : 35 C", 35.0),
        ("temperature : 35°C", 35.0),
        ("temperature : 35(308K)", 35.0),
        ("temperature : 35", 35.0),
    ]
    
    for temp_output, expected_value in test_cases:
        def mock_command(cmd, *args, **kwargs):
            if cmd[1] == "list":
                return MagicMock(
                    stdout="/dev/nvme0n1",
                    stderr="",
                    returncode=0
                )
            elif cmd[1] == "smart-log":
                return MagicMock(
                    stdout=temp_output,
                    stderr="",
                    returncode=0
                )
        mock_subprocess.side_effect = mock_command
        
        reader = NVMETemperatureReader()
        reader.update_readings()
        
        stats = reader.get_sensor_stats("NVMe_nvme0n1")
        assert stats is not None
        assert stats["current"] == expected_value

def test_nvme_smart_log_error(mock_subprocess):
    """Test error handling for NVMe smart-log command"""
    def mock_command(cmd, *args, **kwargs):
        if cmd[1] == "list":
            return MagicMock(
                stdout="/dev/nvme0n1",
                stderr="",
                returncode=0
            )
        elif cmd[1] == "smart-log":
            raise subprocess.CalledProcessError(1, "nvme smart-log", "Error")
    mock_subprocess.side_effect = mock_command
    
    reader = NVMETemperatureReader()
    reader.update_readings()
    
    stats = reader.get_sensor_stats("NVMe_nvme0n1")
    assert stats is None

# SensorReader Tests

def test_sensor_pattern_matching(mock_commander):
    """Test sensor pattern matching"""
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 45.0, "state": "ok"},
        {"name": "CPU2 Temp", "value": 46.0, "state": "ok"},
        {"name": "System Temp", "value": 40.0, "state": "ok"},
        {"name": "Other Sensor", "value": 100, "state": "ok"}
    ]
    
    # Test glob patterns
    reader = SensorReader(mock_commander, sensor_patterns=["CPU* Temp"])
    assert "CPU1 Temp" in reader.get_sensor_names()
    assert "CPU2 Temp" in reader.get_sensor_names()
    assert "System Temp" not in reader.get_sensor_names()
    
    # Test multiple patterns
    reader = SensorReader(mock_commander, sensor_patterns=["CPU* Temp", "System*"])
    assert "CPU1 Temp" in reader.get_sensor_names()
    assert "System Temp" in reader.get_sensor_names()
    assert "Other Sensor" not in reader.get_sensor_names()

def test_sensor_response_id_validation(mock_commander):
    """Test IPMI response ID validation"""
    # Simulate readings with different response IDs
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 45.0, "state": "ok", "response_id": 1},
        {"name": "CPU2 Temp", "value": 46.0, "state": "ok", "response_id": 2}
    ]
    
    with patch('logging.Logger.warning') as mock_warning:
        reader = SensorReader(mock_commander)
        reader.update_readings()
        
        # Should log warning about inconsistent response IDs
        mock_warning.assert_called_with("Inconsistent IPMI response IDs detected: {1, 2}")

def test_sensor_critical_state_handling(mock_commander):
    """Test handling of critical state sensors"""
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 90.0, "state": "cr"},
        {"name": "CPU2 Temp", "value": 91.0, "state": "cr"}
    ]
    
    with patch('logging.Logger.error') as mock_error:
        reader = SensorReader(mock_commander)
        reader.update_readings()
        
        # Should log error about critical temperatures
        mock_error.assert_called_with(
            "Critical temperature alerts: CPU1 Temp: 90.0°C, CPU2 Temp: 91.0°C"
        )

def test_sensor_ipmi_error_handling(mock_commander):
    """Test IPMI error handling"""
    mock_commander.get_sensor_readings.side_effect = IPMIError("IPMI command failed")
    
    with pytest.raises(IPMIError):
        reader = SensorReader(mock_commander)
        reader.update_readings()

def test_sensor_reading_timeout(mock_commander):
    """Test handling of old readings"""
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 45.0, "state": "ok"}
    ]
    
    reader = SensorReader(mock_commander, reading_timeout=1)
    reader.update_readings()
    
    # Wait for readings to expire
    time.sleep(1.1)
    
    stats = reader.get_sensor_stats("CPU1 Temp")
    assert stats is None

def test_sensor_state_tracking(mock_commander):
    """Test sensor state tracking"""
    # Simulate a sequence of state changes
    readings_sequence = [
        [{"name": "CPU1 Temp", "value": 45.0, "state": "ok"}],
        [{"name": "CPU1 Temp", "value": None, "state": "ns"}],
        [{"name": "CPU1 Temp", "value": 90.0, "state": "cr"}]
    ]
    
    reader = SensorReader(mock_commander)
    
    for readings in readings_sequence:
        mock_commander.get_sensor_readings.return_value = readings
        reader.update_readings()
        
        stats = reader.get_sensor_stats("CPU1 Temp")
        if readings[0]["state"] == "ok":
            assert stats is not None
            assert stats["current"] == 45.0
        elif readings[0]["state"] == "ns":
            assert stats is None
        elif readings[0]["state"] == "cr":
            assert stats is not None
            assert stats["current"] == 90.0

# CombinedTemperatureReader Tests

def test_combined_reader_initialization(mock_commander, mock_subprocess):
    """Test combined reader initialization"""
    # Mock IPMI sensors
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 45.0, "state": "ok"}
    ]
    
    # Mock NVMe drives
    mock_subprocess.return_value = MagicMock(
        stdout="/dev/nvme0n1",
        stderr="",
        returncode=0
    )
    
    reader = CombinedTemperatureReader(mock_commander)
    sensor_names = reader.get_sensor_names()
    
    assert "CPU1 Temp" in sensor_names
    assert "NVMe_nvme0n1" in sensor_names

def test_combined_reader_get_all_stats(mock_commander, mock_subprocess):
    """Test getting all temperature statistics"""
    # Mock IPMI sensors
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 45.0, "state": "ok"}
    ]
    
    # Mock NVMe commands
    def mock_nvme_command(cmd, *args, **kwargs):
        if cmd[1] == "list":
            return MagicMock(
                stdout="/dev/nvme0n1",
                stderr="",
                returncode=0
            )
        elif cmd[1] == "smart-log":
            return MagicMock(
                stdout="temperature : 35 C",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_nvme_command
    
    reader = CombinedTemperatureReader(mock_commander)
    reader.update_readings()
    
    stats = reader.get_all_stats()
    assert len(stats) == 2
    assert stats["CPU1 Temp"]["current"] == 45.0
    assert stats["NVMe_nvme0n1"]["current"] == 35.0

def test_combined_reader_highest_temperature(mock_commander, mock_subprocess):
    """Test getting highest temperature across all sensors"""
    # Mock IPMI sensors
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 45.0, "state": "ok"},
        {"name": "System Temp", "value": 40.0, "state": "ok"}
    ]
    
    # Mock NVMe commands
    def mock_nvme_command(cmd, *args, **kwargs):
        if cmd[1] == "list":
            return MagicMock(
                stdout="/dev/nvme0n1",
                stderr="",
                returncode=0
            )
        elif cmd[1] == "smart-log":
            return MagicMock(
                stdout="temperature : 50 C",  # Highest temperature
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_nvme_command
    
    reader = CombinedTemperatureReader(mock_commander)
    reader.update_readings()
    
    highest_temp = reader.get_highest_temperature()
    assert highest_temp == 50.0

def test_combined_reader_average_temperature(mock_commander, mock_subprocess):
    """Test getting average temperature across all sensors"""
    # Mock IPMI sensors
    mock_commander.get_sensor_readings.return_value = [
        {"name": "CPU1 Temp", "value": 40.0, "state": "ok"},
        {"name": "System Temp", "value": 50.0, "state": "ok"}
    ]
    
    # Mock NVMe commands
    def mock_nvme_command(cmd, *args, **kwargs):
        if cmd[1] == "list":
            return MagicMock(
                stdout="/dev/nvme0n1",
                stderr="",
                returncode=0
            )
        elif cmd[1] == "smart-log":
            return MagicMock(
                stdout="temperature : 60 C",
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_nvme_command
    
    reader = CombinedTemperatureReader(mock_commander)
    reader.update_readings()
    
    avg_temp = reader.get_average_temperature()
    assert avg_temp == 50.0  # (40 + 50 + 60) / 3
