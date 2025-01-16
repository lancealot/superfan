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

### Example Configuration

```yaml
ipmi:
  # No configuration needed for local IPMI access
  # For remote systems, uncomment and configure:
  # host: 192.168.1.100
  # username: ADMIN
  # password: ADMIN

temperature:
  critical_max: 85
  warning_max: 75
  target: 65
  hysteresis: 3

fans:
  polling_interval: 5
  min_speed: 20
  max_speed: 100
  ramp_step: 5
  
  zones:
    cpu:
      enabled: true
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

## Safety Features

Superfan includes several safety features:

1. Temperature Limits
- If temperature exceeds `critical_max`, fans are set to 100%
- If temperature exceeds `warning_max`, fan speed is increased
- Minimum fan speed prevents complete fan stoppage

2. Fail-safes
- Watchdog timer ensures regular temperature readings
- Automatic fallback to BMC control on exit
- Emergency mode on sensor reading failures

3. Hysteresis
- Prevents rapid fan speed oscillation
- Configurable temperature change threshold
- Minimum time between speed changes

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

2. Sensor Reading Issues
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

Current test coverage:
- Fan curve implementations: 93% coverage
- Linear, step, and hysteresis curves fully tested
- Temperature-to-speed mapping validated
- Safety limits and validation tested

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
