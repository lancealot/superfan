# Superfan Project Plan

## Project Overview
Superfan is a Python-based utility for controlling Supermicro server fan speeds based on component temperatures and user-defined preferences. The project aims to provide fine-grained control over cooling while maintaining system stability.

## Technical Requirements

### IPMI Communication
- Utilize `ipmitool` for BMC communication (requires root privileges)
- Support different Supermicro generations (X9/X10/X11/X13)
- Handle various fan control commands based on motherboard model
- Ensure proper IPMI device access (ipmi_devintf and ipmi_si kernel modules)

### Temperature Monitoring
- Poll temperature sensors via IPMI SDR (Sensor Data Record)
- Monitor multiple temperature zones:
  - CPU Temperature
  - System Temperature
  - Peripheral Temperature
  - Custom sensor support

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

### Phase 4: Advanced Features
- Multiple fan curve profiles
- Temperature trending
- Emergency thermal protection
- Logging and diagnostics
- Flexible sensor name matching
  * Pattern-based sensor matching for different motherboards
  * Auto-detection of equivalent sensors
  * Fallback sensor selection logic

### Fan Control Capabilities (Verified)
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
1. Critical Issues Found (Latest Testing)
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
     * Fixed response_id tracking:
       - Now properly associating response IDs with specific sensor readings
       - Improved error handling for unexpected response IDs
       - Added validation to prevent using readings with mismatched IDs
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
