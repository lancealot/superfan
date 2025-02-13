# Changelog

All notable changes to Superfan will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Discovered and implemented true minimum stable fan speeds for H12 board:
  * High RPM fans (FAN1, FAN5): 980 RPM
  * Low RPM fans (FAN2-4): 700 RPM
  * CPU fan (FANA): 2520 RPM
- Updated fan control logic to use hex value 0x32 for achieving minimum speeds
- Improved fan speed stability through proper manual mode handling

### Security
- Removed incorrect firmware version fallback detection in board detection logic
  * Previously attempted to guess board generation from firmware version numbers
  * Now properly defaults to UNKNOWN if both DMI and IPMI detection methods fail
  * Prevents potential misidentification of board generations

### Added
- Added comprehensive development documentation:
  * Added architecture overview and diagrams
  * Added development guidelines and standards
  * Added testing strategy documentation
  * Added performance guidelines
  * Added debugging and monitoring guides
  * Added release process documentation

- Improved sensor pattern handling:
  * Added support for flexible wildcard patterns in sensor names
  * Enhanced pattern matching to handle variations in sensor naming
  * Added proper YAML quoting for sensor patterns with wildcards
  * Updated default patterns for better cross-system compatibility

### Changed
- Enhanced code documentation:
  * Added comprehensive docstrings with examples and type hints
  * Added detailed parameter and return value documentation
  * Added error handling information and safety considerations
  * Added implementation notes and usage examples
  * Added validation and edge case details
  * Improved IPMI package documentation:
    - Enhanced SensorReading class documentation
    - Added detailed NVMETemperatureReader class docstrings
    - Improved SensorReader class method documentation
    - Enhanced CombinedTemperatureReader class docstrings
    - Added package-level documentation in __init__.py

- Monitor mode improvements:
  * Automatically stops superfan.service when starting monitor mode
  * Automatically restarts service when exiting monitor mode
  * Prevents conflicts between monitor mode and service
  * Added proper cleanup on monitor mode exit

### Testing
- Added comprehensive performance test suite:
  * Added response time tests (temperature updates, fan speed changes)
  * Added resource usage tests (memory, CPU)
  * Added stress tests (rapid changes, concurrent operations)
  * Added load tests (many sensors, continuous operation)
  * Added memory leak tests (long-running, repeated operations)
  * Added performance thresholds and benchmarks

- Added comprehensive integration test suite:
  * Added end-to-end fan control tests
  * Added emergency scenario tests
  * Added temperature monitoring tests
  * Added learning mode integration tests
  * Added error recovery tests
  * Added cross-module interaction tests

- Improved IPMI Sensors test coverage (28% → 85%):
  * Added comprehensive test suite with 13 test cases
  * Added NVMe temperature monitoring tests
  * Added sensor pattern matching tests
  * Added reading validation tests
  * Added statistics calculation tests
  * Added combined reader integration tests

- Improved Control Manager test coverage (15% → 90%):
  * Added comprehensive test suite with 12 test cases
  * Added NVMe temperature integration tests
  * Added fan curve behavior tests
  * Added safety feature tests
  * Added learning mode tests
  * Added error handling tests
  * Added zone-specific threshold tests

- Significantly improved test coverage for IPMI commander module (49% → 90%):
  * Added comprehensive test suite with 15 test cases
  * Added board generation detection tests:
    - DMI info detection for H12 boards
    - IPMI info detection for X9-X13 series
    - Firmware version fallback detection (1.x-3.x)
    - Multiple detection methods and precedence rules
    - Board detection retry behavior
  * Added fan mode control tests:
    - Manual/auto mode switching
    - Mode verification after changes
    - Error handling for mode changes
    - Unknown board generation handling
  * Added fan speed control tests:
    - Fan speed percentage to hex conversion
    - Board-specific command formats
    - Zone-based control (chassis/CPU)
    - Minimum speed enforcement (2%/5%)
    - Speed validation and safety checks
  * Added error handling tests:
    - Command retries on device busy
    - Connection failure handling
    - Unexpected error recovery
    - Maximum retry attempts
  * Added command validation tests:
    - Blacklisted command checking
    - Command format validation
    - Fan mode validation
    - Fan speed range validation
  * Improved test infrastructure:
    - Better subprocess mocking
    - Command validation
    - Error simulation
    - Board detection scenarios
    - Consistent test patterns

## [0.1.0] - 2025-02-10

### Added
- Fan speed learning capability for automatic discovery of minimum stable speeds
- Zone-specific temperature management with independent thresholds
  * Chassis zone (FAN1-5) optimized for system and storage cooling
  * CPU zone (FANA) optimized for processor thermal management
- NVMe drive temperature monitoring via nvme-cli
  * Automatic drive discovery
  * Per-drive temperature tracking
  * Integration with fan control decisions
- Enhanced monitor mode with optimized polling intervals
  * Normal operation: 30 seconds
  * Monitor mode: 5 seconds
  * Watchdog timeout: 90 seconds

### Changed
- Standardized installation paths
  * Binary installation to /usr/local/bin
  * Updated superfan.service for consistent paths
  * Modified RPM spec file for standard locations
- Improved fan speed control
  * Lowered minimum fan speed threshold from 10% to 5%
  * Modified fan curves to start at 5% instead of 30%
  * Increased ramp_step from 2% to 5% for better stability
- Enhanced temperature monitoring
  * Zone-specific temperature thresholds
  * Improved sensor reading validation
  * Better handling of "no reading" and "ns" sensor states

### Fixed
- Class name inconsistencies in control module
  * Renamed LinearFanCurve to LinearCurve
  * Renamed StepFanCurve to StepCurve
  * Renamed HysteresisFanCurve to HysteresisCurve
- Fan speed validation issues
  * Added tracking of current fan speeds
  * Prevented redundant fan speed updates
  * Improved speed change verification
- IPMI communication issues
  * Fixed response_id tracking
  * Improved error handling for unexpected response IDs
  * Better handling of sensor reading failures

### Security
- Added validation for IPMI commands
  * Blacklisted dangerous commands (0x06 0x01, 0x06 0x02)
  * Added minimum speed enforcement
  * Added mode verification
  * Added command format validation
  * Added duty cycle verification commands

### Testing
- Achieved 100% test coverage for CLI interface
  * Monitor mode functionality
  * Fan speed learning
  * Emergency state handling
  * Temperature thresholds
  * Fan speed mismatch detection
  * Signal handling and cleanup
  * Terminal resize error handling
- Improved test infrastructure
  * Robust mock curses implementation
  * Consistent test patterns
  * Proper cleanup in all test cases
  * Signal handling verification

[Unreleased]: https://github.com/yourusername/superfan/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yourusername/superfan/releases/tag/v0.1.0
