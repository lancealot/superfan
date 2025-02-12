"""
Performance Tests for Superfan

These tests verify the system's performance characteristics under various conditions.
"""

import pytest
import yaml
import time
import threading
import psutil
import gc
import resource
from unittest.mock import Mock, patch
from typing import Dict, List

from superfan.control.manager import ControlManager
from superfan.ipmi import IPMICommander, IPMIError
from superfan.ipmi.sensors import CombinedTemperatureReader

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

class TestResponseTime:
    """Test system response times"""
    
    def test_temperature_update_time(self, mock_config):
        """Test time taken to update temperature readings"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                
                # Measure temperature update time
                start_time = time.time()
                manager.sensor_manager.update_readings()
                update_time = time.time() - start_time
                
                # Should complete within 100ms
                assert update_time < 0.1
                
                manager.stop()
    
    def test_fan_speed_change_time(self, mock_config):
        """Test time taken to change fan speeds"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                
                # Measure fan speed change time
                start_time = time.time()
                manager.commander.set_fan_speed(50, zone="cpu")
                change_time = time.time() - start_time
                
                # Should complete within 50ms
                assert change_time < 0.05
                
                manager.stop()

class TestResourceUsage:
    """Test system resource usage"""
    
    def test_memory_usage(self, mock_config):
        """Test memory usage during operation"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                # Get initial memory usage
                process = psutil.Process()
                initial_memory = process.memory_info().rss
                
                # Create and run manager
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(1)  # Let it run briefly
                
                # Check memory usage
                current_memory = process.memory_info().rss
                memory_increase = current_memory - initial_memory
                
                # Should use less than 50MB additional memory
                assert memory_increase < 50 * 1024 * 1024
                
                manager.stop()
    
    def test_cpu_usage(self, mock_config):
        """Test CPU usage during operation"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                # Create and run manager
                manager = ControlManager(mock_config)
                manager.start()
                
                # Measure CPU usage over 1 second
                process = psutil.Process()
                cpu_percent = process.cpu_percent(interval=1.0)
                
                # Should use less than 5% CPU on average
                assert cpu_percent < 5.0
                
                manager.stop()

class TestStressHandling:
    """Test system behavior under stress"""
    
    def test_rapid_temperature_changes(self, mock_config):
        """Test handling of rapid temperature changes"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                manager = ControlManager(mock_config)
                manager.start()
                
                # Simulate rapid temperature changes
                for temp in range(45, 85, 5):
                    mock_commander.get_sensor_readings.return_value = [
                        {"name": "CPU1 Temp", "value": float(temp), "state": "ok", "response_id": 1}
                    ]
                    time.sleep(0.1)
                
                # Verify system remained stable
                assert not manager._in_emergency
                assert manager._running
                
                manager.stop()
    
    def test_concurrent_operations(self, mock_config):
        """Test handling of concurrent operations"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                
                # Create multiple threads performing operations
                def update_temps():
                    for _ in range(100):
                        manager.sensor_manager.update_readings()
                        time.sleep(0.01)
                
                threads = [threading.Thread(target=update_temps) for _ in range(5)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                
                # Verify system remained stable
                assert not manager._in_emergency
                assert manager._running
                
                manager.stop()

class TestLoadHandling:
    """Test system behavior under load"""
    
    def test_many_sensors(self, mock_config):
        """Test handling of many temperature sensors"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                
                # Create many sensor readings
                many_readings = []
                for i in range(100):
                    many_readings.append({
                        "name": f"CPU{i} Temp",
                        "value": 45.0,
                        "state": "ok",
                        "response_id": 1
                    })
                mock_commander.get_sensor_readings.return_value = many_readings
                
                # Measure time to process readings
                start_time = time.time()
                manager = ControlManager(mock_config)
                manager.start()
                time.sleep(0.1)
                
                # Should handle many sensors efficiently
                assert time.time() - start_time < 0.5
                
                manager.stop()
    
    def test_continuous_operation(self, mock_config):
        """Test continuous operation over time"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                manager = ControlManager(mock_config)
                manager.start()
                
                # Run for 60 seconds
                start_time = time.time()
                while time.time() - start_time < 60:
                    # Check system health every second
                    time.sleep(1)
                    assert manager._running
                    assert not manager._in_emergency
                    
                    # Monitor resource usage
                    process = psutil.Process()
                    assert process.memory_info().rss < 100 * 1024 * 1024  # Less than 100MB
                    assert process.cpu_percent() < 10  # Less than 10% CPU
                
                manager.stop()

class TestMemoryLeaks:
    """Test for memory leaks"""
    
    def test_long_running_memory(self, mock_config):
        """Test memory usage over long operation"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                # Force garbage collection
                gc.collect()
                
                # Get initial memory usage
                process = psutil.Process()
                initial_memory = process.memory_info().rss
                
                manager = ControlManager(mock_config)
                manager.start()
                
                # Run operations that could leak memory
                for _ in range(1000):
                    manager.sensor_manager.update_readings()
                    time.sleep(0.01)
                
                # Force garbage collection again
                gc.collect()
                
                # Check final memory usage
                final_memory = process.memory_info().rss
                memory_growth = final_memory - initial_memory
                
                # Should not have significant memory growth
                assert memory_growth < 10 * 1024 * 1024  # Less than 10MB growth
                
                manager.stop()
    
    def test_repeated_operations(self, mock_config):
        """Test memory usage during repeated operations"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(stdout="", returncode=0)
            
            with patch("superfan.ipmi.commander.IPMICommander") as mock_commander_cls:
                mock_commander = mock_commander_cls.return_value
                mock_commander.get_sensor_readings.return_value = MOCK_SENSOR_READINGS
                
                # Track memory usage over repeated operations
                memory_usage = []
                manager = ControlManager(mock_config)
                manager.start()
                
                for _ in range(100):
                    # Perform various operations
                    manager.sensor_manager.update_readings()
                    manager.commander.set_fan_speed(50, zone="cpu")
                    manager.get_status()
                    
                    # Record memory usage
                    process = psutil.Process()
                    memory_usage.append(process.memory_info().rss)
                    
                    time.sleep(0.1)
                
                # Calculate memory growth trend
                memory_growth = memory_usage[-1] - memory_usage[0]
                average_growth = memory_growth / len(memory_usage)
                
                # Should have minimal average growth
                assert average_growth < 1024  # Less than 1KB per operation
                
                manager.stop()
