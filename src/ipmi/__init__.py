"""
IPMI communication package for Superfan

This package provides modules for interacting with Supermicro IPMI,
including fan control and temperature monitoring.
"""

from .commander import IPMICommander, IPMIError, IPMIConnectionError, IPMICommandError, MotherboardGeneration

__all__ = [
    'IPMICommander',
    'IPMIError',
    'IPMIConnectionError',
    'IPMICommandError',
    'MotherboardGeneration'
]
