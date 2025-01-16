# Superfan Project Plan

## Project Overview
### Installation & Uninstallation Improvements (Latest Update)

4. Installation Path Standardization
   - Standardized binary installation to /usr/local/bin for consistency
   - Modified components for path standardization:
     * Updated superfan.service to use /usr/local/bin/superfan
     * Modified RPM spec file to install to /usr/local/bin
     * Ensures consistent installation location across install methods:
       - RPM package installation
       - Manual installation via install.sh
       - pip install (default behavior)
   - Rationale:
     * /usr/local/bin is the standard location for locally installed executables
     * Follows Linux Filesystem Hierarchy Standard (FHS) guidelines
     * Avoids conflicts with package manager controlled /usr/bin
     * Consistent with pip's default installation behavior

1. Installation Process
   - Verified systemd service integration
   - Package installation with pip
   - Service file deployment and activation
   - Automatic startup configuration
   - Development vs Production Installation:
     * Development mode (-e flag) creates .pth file in ~/.local/lib/python*/site-packages/
     * Production installation requires sudo for proper system-wide installation
     * Edge case: Development installation may require manual cleanup of .pth file

2. File Locations:
   - Development Installation:
     * ~/.local/lib/python*/site-packages/ (pip install without sudo)
     * Not recommended for production use
   - Production Installation:
     * /usr/lib/python*/site-packages/ (system-wide installation)
     * /etc/superfan/ (production config)
     * /etc/systemd/system/superfan.service (systemd service)
     * /var/log/superfan.log (production logging)
   
3. System-Specific Requirements:
   - Red Hat based systems (RHEL/CentOS/Fedora):
     * Use dnf/yum for package installation
     * Example: sudo dnf install ipmitool nvme-cli
   - Debian based systems (Ubuntu/Debian):
     * Use apt-get for package installation
     * Example: sudo apt-get install ipmitool nvme-cli

2. Uninstallation Process Improvements
   - Enhanced uninstall script with better error handling
   - Added systemd service cleanup
   - Verification steps for each operation
   - Proper cleanup of all created directories and files
   - Status reporting for each step
   - Safe handling of configuration files
   - Special handling for development installations:
     * Manual cleanup of .pth files if needed
     * Verification of pip uninstall success
     * Cleanup of any remaining package files

Superfan is a Python-based utility for controlling Supermicro server fan speeds based on component temperatures and user-defined preferences. The project aims to provide fine-grained control over cooling while maintaining system stability.

## Technical Requirements

### IPMI Communication
- Utilize `ipmitool` for BMC communication (requires root privileges)
- Support different Supermicro generations (X9/X10/X11/X13)
- Handle various fan control commands based on motherboard model
- Ensure proper IPMI device access (ipmi_devintf and ipmi_si kernel modules)

### Temperature Monitoring
- Multiple temperature monitoring sources:
  1. IPMI SDR (Sensor Data Record):
     - CPU Temperature
     - System Temperature
     - Peripheral Temperature
     - Custom sensor support
  2. NVMe Drive Temperatures:
     - Direct monitoring via nvme-cli
     - Automatic drive discovery
     - Per-drive temperature tracking
     - Integration with fan control decisions

### Fan Control
1. Manual Mode Management
   - Set fan control to manual mode (command: 0x30 0x45 0x01 0x01)
   - Implement safeguards to prevent thermal damage
   - Ability to restore automatic control (command: 0x30 0x45 0x01 0x00)

2. Speed Control
   - Support percentage-based control (0-100%)
   - Zone-based control implementation:
     * Zone 0: Chassis fans (FAN1-5)
       - Group 1: FAN1, FAN5 (higher RPM range)
       - Group 2: FAN2-4 (lower RPM range)
     * Zone 1: CPU fan (FANA)
   - Duty cycle calculation and conversion
   - Command structure: 0x30 0x70 0x66 0x01 [zone] [speed]

### User Interface
1. Configuration
   - YAML-based configuration file
   - Temperature thresholds
   - Fan curves definition
   - Sensor priority settings

2. CLI Interface
   - Real-time monitoring
   - Manual control options
   - Configuration reload
   - Emergency override

## Project Structure
```
superfan/
├── src/
│   ├── __init__.py
│   ├── ipmi/
│   │   ├── __init__.py
│   │   ├── commander.py      # IPMI command execution
│   │   ├── sensors.py        # Temperature sensor handling
│   │   └── fans.py          # Fan control implementation
│   ├── control/
│   │   ├── __init__.py
│   │   ├── curve.py         # Fan curve implementation
│   │   └── manager.py       # Main control logic
│   └── cli/
│       ├── __init__.py
│       └── interface.py     # Command-line interface
├── config/
│   └── default.yaml         # Default configuration
├── tests/
└── README.md
```

## Implementation Phases

### Phase 1: Core IPMI Communication
- Implement basic IPMI command wrapper
- Detect motherboard generation
- Test basic fan control commands
- Implement temperature sensor reading
- Simplified local IPMI access (removed unnecessary authentication and interface settings)

### Phase 2: Fan Control Logic
- Implement fan curve calculation
- Create fan speed manager
- Add safety checks and limits
- Test different fan zones

### Phase 3: Configuration & CLI
- Create configuration file structure
- Implement configuration parsing
- Build basic CLI interface
- Add real-time monitoring

### Phase 4: Fan Speed Learning
- Implemented automatic fan speed learning capability:
  * Safe discovery of minimum stable fan speeds
  * Progressive testing from current minimum
  * Stability verification at each speed level
  * Automatic configuration updates
  * Safety-first approach with fallback mechanisms
  * Proper cleanup and BMC control restoration

### Phase 5: Advanced Features
- Multiple fan curve profiles
- Temperature trending
- Emergency thermal protection
- Logging and diagnostics
- Enhanced temperature monitoring:
  * IPMI sensor support:
    - Pattern-based sensor matching for different motherboards
    - Auto-detection of equivalent sensors
    - Fallback sensor selection logic
  * NVMe drive monitoring:
    - Automatic drive discovery
    - Per-drive temperature tracking
    - Integration with fan curves
    - Health status monitoring

### Fan Speed Learning Implementation
- New Components Added:
  * FanSpeedLearner class in control/learner.py
  * Learning mode integration in ControlManager
  * CLI support for --learn flag
  * Documentation in README.md and USAGE.md
- Safety Features:
  * Gradual speed reduction with stability checks
  * Minimum 5% speed limit
  * Automatic BMC control restoration
  * Error handling and cleanup
  * RPM verification at each step
- Configuration Integration:
  * Automatic updates to config.yaml
  * Preservation of other settings
  * Backup of original configuration
  * Validation of learned values

### Fan Speed Testing Results (Latest)
- CPU Fan Speed Testing:
  * Conducted controlled testing of CPU fan speeds and temperature response
  * Found stable operation at 10% speed (1260 RPM)
  * Temperature rise from 40°C to 54°C observed over testing period
  * Verified safe operation up to target temperature of 60°C
  * Configuration updated based on findings:
    - Reduced minimum fan speed from 30% to 10%
    - Adjusted CPU fan curve for more gradual scaling
    - Decreased hysteresis from 5°C to 3°C for better responsiveness
    - Maintained critical safety thresholds
  * Response ID inconsistency observed but does not affect operation
  * Automatic mode restoration confirmed working

## Fan Control Capabilities (Verified)
- Zone-based control only (individual fan control not supported)
  * Zone 0 (Chassis): Controls FAN1-5 as a group
    - Group 1 (FAN1, FAN5): Higher RPM range
    - Group 2 (FAN2-4): Lower RPM range
  * Zone 1 (CPU): Controls FANA independently
    - FANA: CPU cooler with independent RPM range
    - FANB: Non-responsive (expected for unpopulated slot)
- IPMI Command Structure:
  * Manual Mode: 0x30 0x45 0x01 [0x01=manual/0x00=auto]
  * Fan Control: 0x30 0x70 0x66 0x01 [zone] [speed]
    - zone: 0x00 (chassis) or 0x01 (CPU)
    - speed: 0x00-0x64 (0-100%)

## Safety Considerations
1. Temperature Limits
   - Hard-coded maximum temperature thresholds
   - Automatic fallback to full speed
   - Minimum fan speed requirements

2. Fail-safes
   - Watchdog timer for control loop
   - Automatic restoration of BMC control on failure
   - Temperature trend monitoring

3. Error Handling
   - IPMI communication failures
   - Sensor reading errors
   - Configuration validation

## Testing Strategy
1. Latest Changes
   - Modified polling intervals:
     * Normal operation: 30 second polling interval for efficient operation
     * Monitor mode: 5 second polling for responsive monitoring
     * Reduced system overhead during normal operation
     * Maintains quick response time when actively monitoring
     * Verified both intervals work correctly with safety features
     * Watchdog and emergency timeouts remain compatible

   - Added NVMe temperature monitoring:
     * Successfully integrated nvme-cli for drive temperature readings
     * Implemented automatic drive discovery
     * Added NVMe temperature tracking to sensor system
     * Verified temperature readings from all detected drives
     * Integrated NVMe temperatures into fan control decisions
     * Added NVMe sensor naming convention (NVMe_nvme[X]n1)
     * Removed dependency on IPMI for NVMe temperatures
     * Added proper error handling for NVMe operations
     * Verified sudo access for nvme-cli commands

2. Critical Issues Found (Latest Testing)
   - H12 Board Support:
     * Board detection working correctly via DMI info
     * Basic fan control commands (0x30 0x45) confirmed working
     * Fan speed command (0x30 0x70) verified working:
       - Fans respond proportionally to speed changes
       - Zone-based control confirmed:
         * Zone 0 (Chassis): Controls all chassis fans (FAN1-5)
         * Zone 1 (CPU): Controls CPU fan (FANA)
       - Updated RPM ranges (verified through testing):
         * FAN1: 1400-1820 RPM (confirmed max)
         * FAN2-4: 1120-1400 RPM (confirmed max)
         * FAN5: 1680-1960 RPM (exceeds previous max)
         * FANA: 3500-3780 RPM (exceeds previous max)
         * FANB: Non-responsive (expected)
       - Important Note: Individual fan control not supported, only zone-based control
       - Fan duty cycle verification:
         * Successfully read current duty cycle using 0x30 0x70 0x66 0x00 0x[0|1]
         * Confirmed proper response to speed changes
         * Both CPU and peripheral zones respond correctly
     * X9-style commands not supported (0x30 0x91)
     * Command validation implemented:
       - Blacklisted dangerous commands (0x06 0x01, 0x06 0x02)
       - Added minimum speed enforcement
       - Added mode verification
       - Added command format validation
       - Added duty cycle verification commands
     * CRITICAL: Command 0x06 0x01 (get supported commands) causes:
       - Fans to drop to minimum speed
       - Sensors to return 'na' values
       - Potentially unsafe thermal conditions
     * Remaining Tasks:
       - Calibrate fan curves based on observed RPM ranges
       - Implement gradual speed changes to prevent sudden transitions
       - Add per-fan RPM verification after speed changes

   - Safety Concerns:
     * Some fans stop completely at 10% speed instead of maintaining minimum speed
     * No enforcement of minimum fan speed in manual mode
     * M2_SSD1 temperature remains in critical state (71-72°C) despite maximum fan speeds
       - Suggests potential thermal design issue rather than fan control problem
       - Requires investigation of M.2 SSD cooling solution
     * FANB consistently shows no reading (expected behavior)
     * Fan speed changes now properly verified using duty cycle reading commands

   - IPMI Communication Issues:
     * Fixed handling of "no reading" and "ns" sensor states:
       - Properly filtering out invalid readings in sensor statistics
       - Improved value parsing to handle "no reading" cases
       - Added validation to prevent using invalid sensor data
       - Added support for Kelvin temperature format (e.g., "45(318K)")
       - Fixed temperature parsing for both standard and Kelvin formats
     * Fixed response_id tracking:
       - Now properly associating response IDs with specific sensor readings
       - Improved error handling for unexpected response IDs
       - Added validation to prevent using readings with mismatched IDs
       - Downgraded non-responsive fan warnings to debug level
     * Monitor mode improvements:
       - Fixed safety check failures by properly handling response_id
       - Resolved fan speed reporting inconsistency
       - Improved emergency state handling
       - Better handling of sensor reading failures

   - Required Code Changes:
     * commander.py:
       - Add H12-specific command validation
       - Implement fan speed command verification
       - Add retry logic for failed commands
       - Add command response validation
       - Calibrate fan speed ranges for H12

     * sensors.py:
       - Add sensor state tracking (ok/cr/ns) in SensorReading class
       - Implement critical state detection and immediate alerting
       - Add validation for IPMI response IDs
       - Add retry mechanism for failed sensor readings
       
     * manager.py:
       - Add fan speed verification after changes
       - Implement per-fan minimum speed enforcement
       - Add handling for non-responsive fans
       - Add emergency action verification
       - Implement fan speed ramping to prevent sudden changes
       
     * New Safety Features Needed:
       - Hardware-level minimum speed enforcement
       - Immediate temperature threshold monitoring in manual mode
       - Fan failure detection and failover
       - Temperature trend analysis for predictive action
       - Board-specific safety limits and thresholds
       - Improved emergency state recovery mechanism
       - Fan speed reporting validation between monitor and raw commands
       - Safety check response validation

2. Unit Tests
   - IPMI command formation
   - Fan curve calculations (85% coverage achieved)
   - Configuration parsing
   - Need additional coverage for:
     * CLI interface (currently 0%)
     * Control manager (currently 15%)
     * IPMI commander (currently 27%)
     * IPMI sensors (currently 28%)

2. Bug Fixes
   - Fixed class name inconsistencies in control module:
     * Renamed LinearFanCurve to LinearCurve
     * Renamed StepFanCurve to StepCurve
     * Renamed HysteresisFanCurve to HysteresisCurve
     * Updated imports in manager.py and __init__.py
   - Optimized fan speed control:
     * Added tracking of current fan speeds to prevent redundant updates
     * Fan speeds are now only set when they actually change
     * Improved logging to only show fan speed changes
     * Reduced unnecessary IPMI commands
     * Added current_speeds tracking in ControlManager

2. Integration Tests
   - End-to-end control flow
   - Temperature response
   - Profile switching
   - Manual testing verified:
     * Basic IPMI sensor readings work
     * Manual fan speed control functions
     * Fan speed changes confirmed via sensors

3. Safety Tests
   - Failure mode handling
   - Temperature limit responses
   - Communication loss recovery
   - Verified functionality:
     * Manual mode entry/exit works (tested with raw IPMI commands)
     * Fan speed control responds to commands (verified with RPM changes)
     * System returns to automatic control properly
     * IPMI sensor readings working correctly:
       - CPU Temp: 46°C
       - System Temp: 47°C
       - Peripheral Temp: 43°C
       - Multiple M.2 SSD temps detected
     * Fan control verified:
       - Successfully entered manual mode
       - Set fans to 50% speed
       - Observed RPM changes in all fans
       - Successfully restored BMC control
     * Installation considerations:
       - Package needs system-wide installation for sudo operation
       - IPMI commands require root privileges

## Systemd Service Integration

### Required Code Changes for Systemd
1. Logging Improvements
   - Switch to systemd journal logging
   - Add structured logging for better journalctl integration
   - Include more detailed error states and transitions
   - Add service status reporting

2. Signal Handling
   - Implement proper SIGTERM handling for clean shutdown
   - Add SIGHUP handler for config reload
   - Improve graceful shutdown process

3. Service Status Reporting
   - Add systemd status notification (sd_notify)
   - Report service state transitions
   - Include health check information
   - Add watchdog integration

4. Error Recovery
   - Implement automatic recovery procedures
   - Add failure state detection
   - Improve emergency mode handling
   - Add startup failure detection

### Deployment Considerations
1. Security
   - Run as root (required for IPMI)
   - Implement service hardening
   - Add resource limits
   - Restrict capabilities

2. Configuration
   - Use standard paths (/etc/superfan)
   - Support config reload
   - Validate all settings
   - Handle missing/invalid configs

3. Monitoring
   - Integration with system monitoring
   - Export metrics for collection
   - Status reporting
   - Health checks

## Dependency Analysis (Latest Update)
1. Core Dependencies Review
   - python3-pip:
     * Required for Python package installation
     * Added as system dependency in install.sh
     * Essential for both production and development installations
     * Required for installing other Python dependencies
   - pyyaml (>=5.1):
     * Essential for YAML configuration parsing
     * Used in both production and development
     * No viable alternatives for simplification
     * Cannot be pruned due to core functionality

2. Development Dependencies Review
   - pytest (>=7.0) & pytest-cov (>=3.0):
     * Essential for test suite
     * Current coverage needs improvement:
       - CLI interface: 0% coverage
       - Control manager: 15% coverage
       - IPMI commander: 27% coverage
       - IPMI sensors: 28% coverage
     * Required for ongoing development
   - black (>=22.0) & isort (>=5.0):
     * Used for code formatting and import organization
     * Essential for maintaining code quality
     * Actively used in development workflow
   - flake8 (>=4.0):
     * Complements black/isort for linting
     * Catches potential issues early
   - mypy (>=0.9):
     * Critical for static type checking
     * Important for safety-critical fan control logic
     * Helps prevent runtime errors

3. Installation Process Analysis
   - Optimized package structure:
     * Clear separation of core vs dev dependencies
     * Minimal core requirements (single package)
     * Development tools only installed when needed
   - No opportunities for further pruning:
     * All packages serve distinct purposes
     * No redundant or obsolete dependencies
     * Each tool actively used in development

## Future Enhancements
- Web interface for monitoring
- Remote control capabilities
- Multiple server management
- Custom sensor plugin support
- Temperature prediction modeling
- Enhanced sensor compatibility
  * Machine learning for sensor name recognition
  * Vendor-specific sensor mapping profiles
  * Dynamic sensor group detection
- Systemd Integration
  * Native systemd journal logging
  * Service status notifications
  * Watchdog integration
  * Health monitoring
