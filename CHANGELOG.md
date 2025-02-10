# Changelog

All notable changes to Superfan will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Testing
- Improved test coverage for IPMI commander module:
  * Added board generation detection tests:
    - DMI info detection for H12
    - IPMI info detection for X9-X13
    - Firmware version fallback detection
    - Multiple detection methods and precedence
  * Added fan control command tests:
    - Fan speed percentage to hex conversion
    - Board-specific command formats
    - Zone-based control (chassis/CPU)
    - Minimum speed enforcement
  * Added sensor reading tests:
    - Temperature and fan speed parsing
    - Kelvin format handling
    - Response ID tracking
    - Edge case handling
  * Added fan speed verification tests:
    - RPM range validation
    - Tolerance checking
    - Mixed fan group handling
  * Added error handling tests:
    - Command retries
    - Device busy conditions
    - Connection failures
    - Unexpected errors
  * Improved test infrastructure:
    - Better subprocess mocking
    - Command validation
    - Error simulation
    - Board detection scenarios

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
