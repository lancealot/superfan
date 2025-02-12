"""
Tests for the Temperature Sensor Management module
"""

import pytest
import time
from unittest.mock import Mock, patch, call
from typing import Dict, List, Set

from superfan.ipmi import IPMICommander, IPMIError
from superfan.ipmi.sensors import (
    SensorReading,
    NVMETemperatureReader,
    SensorReader,
    CombinedTemperatureReader
)

# Test Data
MOCK_NVME_LIST = """
Node             SN                   Model                                    Namespace Usage                      Format           FW Rev  
---------------- -------------------- ---------------------------------------- --------- -------------------------- ---------------- --------
/dev/nvme0n1     ABC123              SAMSUNG MZVL2512HCJQ-00B00              1         500.11  GB /   512.11  GB  512   B +  0 B   2B4QFXO7
/dev/nvme1n1     DEF456              SAMSUNG MZVL2512HCJQ-00B00              1         500.11  GB /   512.11  GB  512   B +  0 B   2B4QFXO7
"""

MOCK_NVME_SMART = """
Smart Log for NVME device:nvme0n1 namespace-id:ffffffff
critical_warning                    : 0
temperature                         : 38 C
available_spare                     : 100%
available_spare_threshold          : 10%
percentage_used                    : 0%
"""

MOCK_SENSOR_READINGS = [
    {"name": "CPU1 Temp", "value": 45.0, "state": "ok", "response_id": 1},
    {"name": "CPU2 Temp", "value": 47.0, "state": "ok", "response_id": 1},
    {"name": "System Temp", "value": 35.0, "state": "ok", "response_id": 1},
    {"name": "Peripheral Temp", "value": 40.0, "state": "ok", "response_id": 1},
    {"name": "PCH Temp", "value": None, "state": "ns", "response_id": 1},
    {"name": "FAN1", "value": 1500, "state": "ok", "response_id": 1}
]

@pytest.fixture
def mock_commander():
    """Create a mock IPMI commander"""
    commander = Mock(spec=IPMICommander)
    commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
    return commander

def test_sensor_reading_properties():
    """Test SensorReading dataclass properties"""
    # Test valid reading
    reading = SensorReading(
        name="CPU1 Temp",
        value=45.0,
        timestamp=time.time(),
        state="ok"
    )
    assert reading.is_valid
    assert not reading.is_critical
    assert reading.age >= 0
    
    # Test critical reading
    critical = SensorReading(
        name="CPU1 Temp",
        value=90.0,
        timestamp=time.time(),
        state="cr"
    )
    assert critical.is_valid
    assert critical.is_critical
    
    # Test invalid reading
    invalid = SensorReading(
        name="CPU1 Temp",
        value=None,
        timestamp=time.time(),
        state="ns"
    )
    assert not invalid.is_valid
    assert not invalid.is_critical

class TestNVMETemperatureReader:
    @pytest.fixture
    def nvme_reader(self):
        """Create NVMETemperatureReader instance"""
        with patch("subprocess.run") as mock_run:
            # Mock nvme list command
            mock_run.side_effect = [
                Mock(stdout=MOCK_NVME_LIST, returncode=0),  # nvme list
                Mock(stdout=MOCK_NVME_SMART, returncode=0)  # nvme smart-log
            ]
            reader = NVMETemperatureReader()
            return reader
            
    def test_drive_discovery(self, nvme_reader):
        """Test NVMe drive discovery"""
        assert len(nvme_reader.drives) == 2
        assert "/dev/nvme0n1" in nvme_reader.drives
        assert "/dev/nvme1n1" in nvme_reader.drives
        
    def test_temperature_reading(self, nvme_reader):
        """Test NVMe temperature reading"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            nvme_reader.update_readings()
            
            stats = nvme_reader.get_sensor_stats("NVMe_nvme0n1")
            assert stats is not None
            assert stats["current"] == 38.0
            
    def test_reading_timeout(self, nvme_reader):
        """Test reading timeout handling"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            nvme_reader.update_readings()
            
            # Set old timestamp
            old_time = time.time() - nvme_reader.reading_timeout - 1
            for readings in nvme_reader._readings.values():
                for reading in readings:
                    reading.timestamp = old_time
                    
            # Should return None for old readings
            assert nvme_reader.get_sensor_stats("NVMe_nvme0n1") is None
            
    def test_error_handling(self, nvme_reader):
        """Test error handling"""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "nvme", "Error")
            nvme_reader.update_readings()
            assert not nvme_reader.get_all_stats()

class TestSensorReader:
    @pytest.fixture
    def sensor_reader(self, mock_commander):
        """Create SensorReader instance"""
        patterns = ["CPU* Temp", "System Temp", "Peripheral Temp"]
        return SensorReader(mock_commander, patterns)
        
    def test_sensor_discovery(self, sensor_reader):
        """Test sensor pattern matching"""
        sensor_names = sensor_reader.get_sensor_names()
        assert "CPU1 Temp" in sensor_names
        assert "CPU2 Temp" in sensor_names
        assert "System Temp" in sensor_names
        assert "PCH Temp" not in sensor_names  # Not matched by patterns
        
    def test_reading_updates(self, sensor_reader):
        """Test reading updates"""
        sensor_reader.update_readings()
        stats = sensor_reader.get_sensor_stats("CPU1 Temp")
        assert stats is not None
        assert stats["current"] == 45.0
        
    def test_invalid_readings(self, sensor_reader):
        """Test handling of invalid readings"""
        # Modify mock to include invalid reading
        sensor_reader.commander.get_sensor_readings.return_value = [
            {"name": "CPU1 Temp", "value": None, "state": "ns", "response_id": 1}
        ]
        sensor_reader.update_readings()
        assert sensor_reader.get_sensor_stats("CPU1 Temp") is None
        
    def test_response_id_validation(self, sensor_reader):
        """Test response ID validation"""
        # Modify mock to include different response IDs
        sensor_reader.commander.get_sensor_readings.return_value = [
            {"name": "CPU1 Temp", "value": 45.0, "state": "ok", "response_id": 1},
            {"name": "CPU2 Temp", "value": 47.0, "state": "ok", "response_id": 2}
        ]
        with patch("logging.Logger.warning") as mock_warning:
            sensor_reader.update_readings()
            mock_warning.assert_called_with("Inconsistent IPMI response IDs detected: {1, 2}")
            
    def test_temperature_statistics(self, sensor_reader):
        """Test temperature statistics calculation"""
        sensor_reader.update_readings()
        # Add another reading with different temperature
        sensor_reader.commander.get_sensor_readings.return_value = [
            {"name": "CPU1 Temp", "value": 50.0, "state": "ok", "response_id": 1}
        ]
        sensor_reader.update_readings()
        
        stats = sensor_reader.get_sensor_stats("CPU1 Temp")
        assert stats is not None
        assert stats["min"] == 45.0
        assert stats["max"] == 50.0
        assert "stdev" in stats  # Should have standard deviation with multiple readings

class TestCombinedTemperatureReader:
    @pytest.fixture
    def combined_reader(self, mock_commander):
        """Create CombinedTemperatureReader instance"""
        with patch("subprocess.run") as mock_run:
            # Mock nvme commands
            mock_run.side_effect = [
                Mock(stdout=MOCK_NVME_LIST, returncode=0),
                Mock(stdout=MOCK_NVME_SMART, returncode=0)
            ]
            patterns = ["CPU* Temp", "System Temp", "NVMe_*"]
            return CombinedTemperatureReader(mock_commander, patterns)
            
    def test_combined_sensor_names(self, combined_reader):
        """Test combined sensor name list"""
        names = combined_reader.get_sensor_names()
        assert "CPU1 Temp" in names
        assert "System Temp" in names
        assert "NVMe_nvme0n1" in names
        
    def test_combined_stats(self, combined_reader):
        """Test combined statistics"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            combined_reader.update_readings()
            
            stats = combined_reader.get_all_stats()
            assert "CPU1 Temp" in stats
            assert "NVMe_nvme0n1" in stats
            
    def test_highest_temperature(self, combined_reader):
        """Test highest temperature calculation"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            combined_reader.update_readings()
            
            highest = combined_reader.get_highest_temperature()
            assert highest == 47.0  # CPU2 Temp is highest
            
    def test_average_temperature(self, combined_reader):
        """Test average temperature calculation"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout=MOCK_NVME_SMART, returncode=0)
            combined_reader.update_readings()
            
            avg = combined_reader.get_average_temperature()
            assert avg is not None
            # Average of CPU1 (45), CPU2 (47), System (35), NVMe (38)
            expected_avg = (45 + 47 + 35 + 38) / 4
            assert abs(avg - expected_avg) < 0.01
