# Superfan Usage Guide

## Installation

1. Install system dependencies:
```bash
# Install IPMI, NVMe tools, and Python pip
sudo apt-get install ipmitool nvme-cli python3-pip

# Ensure IPMI device access
sudo modprobe ipmi_devintf
sudo modprobe ipmi_si

# Add modules to load at boot
echo "ipmi_devintf" | sudo tee -a /etc/modules
echo "ipmi_si" | sudo tee -a /etc/modules
```

Note: Root privileges (sudo) are required for IPMI operations.

2. Install the package:
```bash
sudo pip install .
```

3. (Optional) Install as systemd service:
```bash
# Copy service file
sudo cp superfan.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start the service
sudo systemctl enable superfan
sudo systemctl start superfan
```

## Deployment Options

### 1. Manual Execution
- Run directly with `superfan` command
- Requires manual restart if system reboots
- Good for testing and development
- Allows real-time monitoring with `--monitor` flag

### 2. Systemd Service (Recommended for Production)
- Automatic startup on boot
- Automatic restart on failures
- Proper service management and logging
- Enhanced security through service isolation

#### Advantages of Systemd Deployment:
- Reliable startup/shutdown handling
- Automatic restart on crashes
- Proper dependency management
- Integrated logging (journalctl)
- Security hardening through service isolation
- Resource control capabilities

#### Potential Drawbacks:
- Less interactive (no real-time monitor mode)
- Requires service restart to apply config changes
- May need additional logging configuration
- Root privileges required for service management

#### Systemd Service Management:
```bash
# Check service status
sudo systemctl status superfan

# View logs
sudo journalctl -u superfan

# Restart service (after config changes)
sudo systemctl restart superfan

# Stop service
sudo systemctl stop superfan

# Disable automatic start
sudo systemctl disable superfan
```

## Configuration

The default configuration file is installed at `/etc/superfan/config.yaml`. You can specify a different configuration file using the `--config` option.

### Temperature Sources

The system monitors temperatures from two sources:
1. IPMI Sensors - System and component temperatures via IPMI
2. NVMe Drives - Direct temperature monitoring of NVMe drives using nvme-cli

NVMe temperatures are automatically detected and monitored, with sensors named in the format `NVMe_nvme[X]n1` where X is the drive number.

### Sensor Pattern Configuration

The system supports flexible wildcard patterns for sensor names to handle variations across different systems:

1. Basic Patterns:
- `*` matches any sequence of characters
- Use quotes when pattern contains wildcards: `"CPU* Temp"`
- Examples:
  * `"CPU* Temp"` - Matches "CPU Temp", "CPU1 Temp", etc.
  * `"*VRM* Temp"` - Matches "CPU_VRM Temp", "SOC_VRM Temp", etc.
  * `"P1_DIMM*"` - Matches "P1_DIMMA~D", "P1_DIMME~H", etc.

2. Pattern Placement:
- Start pattern: `"CPU*"` - Matches names starting with "CPU"
- Middle pattern: `"*VRM*"` - Matches names containing "VRM"
- End pattern: `"*Temp"` - Matches names ending with "Temp"

3. Example Patterns:
```yaml
sensors:
  # CPU zone patterns
  - "CPU* Temp"         # CPU temperature sensors
  - "*CPU*VRM* Temp"    # CPU VRM temperature sensors
  - "*SOC*VRM* Temp"    # SOC VRM temperature sensors
  - "*VRM* Temp"        # Other VRM temperature sensors

  # Chassis zone patterns
  - "System Temp"       # System temperature
  - "Peripheral Temp"   # Peripheral temperature
  - "P1_DIMM*"         # Memory temperature sensors
  - "NVMe_*"           # NVMe drive temperatures
  - "M2_SSD*"          # M.2 SSD temperatures
```

### Example Configuration

```yaml
ipmi:
  # No configuration needed for local IPMI access
  # For remote systems, uncomment and configure:
  # host: 192.168.1.100
  # username: ADMIN
  # password: ADMIN

temperature:
  hysteresis: 3     # Temperature change required for speed adjustment

fans:
  polling_interval: 30      # Seconds between temperature checks
  monitor_interval: 5       # Faster polling in monitor mode
  min_speed: 5             # Minimum fan speed (matches IPMI validation)
  max_speed: 100           # Maximum fan speed percentage
  ramp_step: 5             # Maximum fan speed change per interval
  
  zones:
    chassis:  # Zone 0: Controls FAN1-5 as a group
      enabled: true
      # Temperature thresholds specific to chassis zone
      critical_max: 75  # Emergency threshold - will set fans to 100%
      warning_max: 65   # Warning threshold - will increase fan speed
      target: 55        # Desired operating temperature
      sensors: ["System Temp", "Peripheral Temp", "NVMe_*", "M2_SSD*"]
      curve:
        - [0, 5]     # [temp_delta, fan_speed]
        - [10, 30]
        - [20, 50]
        - [30, 70]
        - [40, 85]
        - [50, 100]

    cpu:  # Zone 1: Controls FANA (CPU fan)
      enabled: true
      # Temperature thresholds specific to CPU zone
      critical_max: 85  # Emergency threshold - will set fans to 100%
      warning_max: 75   # Warning threshold - will increase fan speed
      target: 65        # Desired operating temperature
      sensors: ["CPU1 Temp", "CPU2 Temp"]
      curve:
        - [0, 20]    # [temp_delta, fan_speed]
        - [10, 30]
        - [20, 50]
        - [30, 70]
        - [40, 100]
```

## Basic Usage

1. Start automatic fan control:
```bash
superfan
```

2. Monitor temperatures and fan speeds:
```bash
superfan --monitor
```

Note: Monitor mode automatically manages the superfan service:
- Stops the service when starting monitor mode
- Restarts the service when exiting monitor mode (Ctrl+C)
- This prevents conflicts between monitor mode and service fan control

3. Set a manual fan speed:
```bash
superfan --manual 50  # Set fans to 50%
```

4. Use a custom configuration file:
```bash
superfan -c /path/to/config.yaml
```

5. Learn minimum stable fan speeds:
```bash
superfan --learn
```

The learning mode will:
- Start from the current minimum speed
- Gradually decrease fan speeds
- Find the lowest stable speed for each zone
- Update the configuration file with learned values
- Ensure system safety during the learning process

Note: Learning mode requires about 5-10 minutes to complete as it carefully tests each speed level. During the learning process:
- The system will test progressively lower speeds
- Each speed is tested for stability over several seconds
- If unstable speeds are detected, it will revert to the last stable speed
- The configuration file is automatically updated with learned values
- BMC control is automatically restored after learning completes

For optimal results:
- Run learning mode when system is at idle
- Ensure ambient temperature is at typical operating levels
- Allow the learning process to complete without interruption

## Critical Safety Guidelines (Latest Update 2025-02-10)

1. Fan Speed Control
- Minimum fan speed validated at 5% through IPMI command verification
- Fan curves optimized to start at minimum speed for efficiency
- Gradual speed changes with 5% ramp step for stability
- Detailed debug logging available for monitoring fan behavior
- Allow 5 seconds between speed changes for proper RPM stabilization
- Fan speed changes now verified using duty cycle reading commands
- Proper handling of different RPM ranges for fan groups:
  * Group 1 (FAN1, FAN5): Higher RPM range
  * Group 2 (FAN2-4): Lower RPM range
  * FANA: CPU-specific RPM range

2. Temperature Monitoring
- Monitor NVMe and M.2 SSD temperatures closely
- Some drives may require additional cooling consideration
- M2_SSD temperatures above 60°C indicate potential cooling issues
- Use monitor mode (5-second polling) for real-time temperature tracking
- Improved sensor reading validation:
  * Proper filtering of "no reading" and "ns" sensor states
  * Validation of sensor response IDs for data consistency
  * Support for both standard and Kelvin temperature formats
  * Safe handling of non-responsive fans and sensors

3. Emergency Procedures
- System automatically restores BMC control if:
  * Fans stop completely
  * Emergency fan speed changes fail
  * Critical temperatures are detected
  * Sensor reading validation fails
  * Response ID validation fails
- Emergency recovery procedures:
  1. Immediate restoration of BMC control
  2. Setting all fans to 100% speed
  3. Verification of fan speed changes
  4. Temperature trend monitoring
  5. Logging of emergency state details
- Manual intervention required if:
  * Multiple recovery attempts fail
  * Critical temperatures persist
  * Fan speed verification fails
  * Sensor readings remain invalid

## Additional Safety Features (Latest Update 2025-02-10)

Superfan includes several built-in safety features:

1. Temperature Limits
- Zone-specific temperature thresholds:
  * Chassis zone:
    - Critical max: 75°C (triggers emergency mode)
    - Warning max: 65°C (increases fan speed)
    - Target: 55°C (optimal operating temperature)
  * CPU zone:
    - Critical max: 85°C (triggers emergency mode)
    - Warning max: 75°C (increases fan speed)
    - Target: 65°C (optimal operating temperature)
- Temperature trend analysis for predictive action
- Configurable hysteresis to prevent oscillation

2. Fail-safes
- Watchdog timer (90 seconds) ensures control loop reliability
- Automatic fallback to BMC control on:
  * Program exit
  * Control loop failure
  * Communication errors
  * Invalid sensor readings
- Emergency mode triggers on:
  * Critical temperatures
  * Sensor reading failures
  * Fan speed verification failures
  * Response validation errors

3. Hysteresis and Stability
- Prevents rapid fan speed oscillation
- Configurable temperature change threshold (default: 3°C)
- Minimum time between speed changes (5 seconds)
- Gradual speed ramping with 5% steps
- Fan speed verification after changes

## Monitoring Display

The monitoring display (`--monitor`) shows:
- Current system status
- Temperature readings for all sensors (IPMI and NVMe)
- Fan speeds for each zone
- Color-coded warnings for high temperatures
- NVMe drive temperatures and health status

## Fan Control Zones

The system implements zone-based fan control:

1. Zone 0 (Chassis Fans):
   - Controls all chassis fans (FAN1-5) as a group
   - Fan groups have different RPM ranges:
     * Group 1: FAN1, FAN5 (higher RPM range)
     * Group 2: FAN2-4 (lower RPM range)
   - Individual fan control is not supported

2. Zone 1 (CPU Fan):
   - Controls CPU fan (FANA) independently
   - Typically maintains higher RPM range for CPU cooling
   - FANB readings can be ignored (normal for unpopulated slot)

Note: When changing fan speeds, allow 5 seconds between changes for:
- Fan speeds to stabilize
- RPM readings to update
- System to properly register changes

## Troubleshooting

1. IPMI Connection Issues
```bash
# Test IPMI connection
ipmitool sdr list
```

2. Board Detection and Compatibility
```bash
# Check board information via DMI (primary detection method)
sudo dmidecode -t baseboard

# Check board information via IPMI (fallback detection)
ipmitool mc info

# Note: H12 Series Boards
# - H12 boards are detected via DMI info or IPMI board markers
# - Manual fan control is not supported on H12 boards
# - System will automatically use BMC automatic mode
# - Temperature monitoring and logging still available
# - Clear warnings will be logged about H12 limitations
```

3. Sensor Reading Issues
```bash
# Check sensor status
ipmitool sdr list

# Understanding sensor states:
# - "ok": Normal operation
# - "cr": Critical state
# - "ns" or "no reading": Sensor not providing data
#
# Note: "no reading" or "ns" states are normal for:
# - Unpopulated fan slots
# - Optional temperature sensors
# - Uninstalled components

# Temperature formats supported:
# - Standard Celsius: "45°C" or "45 degrees C"
# - Kelvin format: "45(318K)" - common in some IPMI implementations
# The system automatically handles both formats
```

3. Response ID Issues
```bash
# If you see "Received a response with unexpected ID" messages:
# This is normal behavior and is handled automatically by:
# - Tracking response IDs per sensor
# - Validating data consistency
# - Filtering out invalid readings
```

4. Manual Fan Control Test
```bash
# Test manual fan control
ipmitool raw 0x30 0x45 0x01 0x01  # Enter manual mode
sleep 5  # Wait for mode change to take effect

# Control chassis fans (Zone 0)
ipmitool raw 0x30 0x70 0x66 0x01 0x00 0x64  # Set chassis fans to 100%
sleep 5  # Wait for speed change to take effect

# Control CPU fan (Zone 1)
ipmitool raw 0x30 0x70 0x66 0x01 0x01 0x64  # Set CPU fan to 100%
sleep 5  # Wait for speed change to take effect

ipmitool raw 0x30 0x45 0x01 0x00  # Return to automatic mode
```

Note: The command structure for fan control is:
```bash
ipmitool raw 0x30 0x70 0x66 0x01 [zone] [speed]
# zone: 0 for chassis fans, 1 for CPU fan
# speed: 0x00-0x64 (0-100 in hex)
```

## Advanced Configuration

### Fan Curves

You can define custom fan curves for different temperature zones:

1. Linear Curve
- Define points as [temperature_delta, fan_speed] pairs
- Speed is interpolated between points

2. Step Curve
- Define discrete steps for more aggressive control
- No interpolation between steps

3. Hysteresis Curve
- Wraps another curve type
- Prevents oscillation by requiring minimum temperature change

### Multiple Zones

Configure different fan curves for different components:
- CPU zone for processor temperatures
- System zone for ambient temperatures
- Custom zones for specific components

## Logging

Logs are written to the configured log file (default: superfan.log):
- INFO level for normal operation
- WARNING for concerning conditions
- ERROR for critical issues
- DEBUG for detailed troubleshooting

## Development

1. Setup development environment:
```bash
# Install in development mode with all dependencies
pip install -e .
```

2. Run tests:
```bash
# Run all tests with coverage
python -m pytest

# Run specific test module
python -m pytest tests/test_curve.py
```

Current test coverage (as of 2025-02-11):
- Fan curve implementations: 93% coverage
- CLI interface: 100% coverage
  * Monitor mode functionality
  * Fan speed learning
  * Emergency state handling
  * Temperature thresholds
  * Fan speed mismatch detection
  * Signal handling and cleanup
- Control manager: 15% coverage (needs improvement)
- IPMI commander: 49% coverage
  * Board generation detection
  * Fan mode control
  * Fan speed control
  * Error handling
  * Command validation
- IPMI sensors: 28% coverage (needs improvement)

Latest Test Improvements:
- Complete CLI interface test coverage
- Comprehensive IPMI commander tests:
  * Board generation detection:
    - DMI info detection for H12
    - IPMI info detection for X9-X13
    - Firmware version fallback detection
    - Multiple detection methods and precedence
  * Fan control commands:
    - Fan mode control (manual/auto)
    - Fan speed percentage to hex conversion
    - Board-specific command formats
    - Zone-based control (chassis/CPU)
    - Minimum speed enforcement
  * Error handling:
    - Command retries
    - Device busy conditions
    - Connection failures
    - Unexpected errors
  * Command validation:
    - Blacklist checking
    - Format validation
    - Safety checks
- Robust mock implementations
- Consistent test patterns
- Proper cleanup in all test cases
- Signal handling verification

3. Format code:
```bash
black .
isort .
```

4. Type checking:
```bash
mypy src tests
```

The project uses a src-layout structure:
```
superfan/
├── src/superfan/         # Main package directory
│   ├── control/         # Fan control logic
│   ├── ipmi/           # IPMI communication
│   └── cli/            # Command-line interface
├── tests/              # Test suite
└── config/             # Configuration files
```
