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
   - Set fan control to manual mode
   - Implement safeguards to prevent thermal damage
   - Ability to restore automatic control

2. Speed Control
   - Support percentage-based control (0-100%)
   - Individual fan zone control where supported
   - Duty cycle calculation and conversion

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
1. Unit Tests
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
     * Manual mode entry/exit works
     * Fan speed control responds to commands
     * System returns to automatic control properly

## Future Enhancements
- Web interface for monitoring
- Remote control capabilities
- Multiple server management
- Custom sensor plugin support
- Temperature prediction modeling
