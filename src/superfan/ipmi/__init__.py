"""
IPMI Communication Package for Superfan

This package provides a comprehensive interface for interacting with Supermicro IPMI,
enabling advanced fan control and temperature monitoring capabilities.

Key Components:
- IPMICommander: Core class for IPMI communication and fan control
- CombinedTemperatureReader: Unified temperature monitoring from IPMI and NVMe sources

Features:
- Automatic board generation detection (X9, X10, X11, H12, X13)
- Manual and automatic fan control modes
- Temperature monitoring from multiple sources
- Statistical analysis of sensor readings
- Robust error handling and safety checks
- Support for various Supermicro server generations

Example Usage:
    >>> from superfan.ipmi import IPMICommander, CombinedTemperatureReader
    >>> 
    >>> # Initialize IPMI control
    >>> commander = IPMICommander("config.yaml")
    >>> 
    >>> # Monitor temperatures
    >>> reader = CombinedTemperatureReader(commander)
    >>> reader.update_readings()
    >>> stats = reader.get_all_stats()
    >>> 
    >>> # Control fans
    >>> commander.set_manual_mode()
    >>> commander.set_fan_speed(50, zone="chassis")
    >>> commander.set_auto_mode()  # Return to automatic control

Note:
    This package requires:
    - ipmitool for IPMI communication
    - nvme-cli for NVMe drive monitoring
    - Root/sudo access for certain operations
"""

from .commander import IPMICommander, IPMIError, IPMIConnectionError, IPMICommandError, MotherboardGeneration
from .sensors import CombinedTemperatureReader

__all__ = [
    'IPMICommander',
    'IPMIError',
    'IPMIConnectionError',
    'IPMICommandError',
    'MotherboardGeneration',
    'CombinedTemperatureReader'
]
