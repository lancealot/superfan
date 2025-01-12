# Superfan Usage Guide

## Installation

1. Install system dependencies:
```bash
sudo apt-get install ipmitool

# Ensure IPMI device access
sudo modprobe ipmi_devintf
sudo modprobe ipmi_si
```

Note: Root privileges (sudo) are required for IPMI operations.

2. Install the package:
```bash
pip install .
```

## Configuration

The default configuration file is installed at `/etc/superfan/config.yaml`. You can specify a different configuration file using the `--config` option.

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
- Temperature readings for all sensors
- Fan speeds for each zone
- Color-coded warnings for high temperatures

## Troubleshooting

1. IPMI Connection Issues
```bash
# Test IPMI connection
ipmitool sdr list
```

2. Sensor Discovery
```bash
# List available sensors
ipmitool sdr list
```

3. Manual Fan Control Test
```bash
# Test manual fan control
ipmitool raw 0x30 0x45 0x01 0x01  # Enter manual mode
ipmitool raw 0x30 0x70 0x66 0x01 0x00 0x64  # Set fans to 100%
ipmitool raw 0x30 0x45 0x01 0x00  # Return to automatic mode
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
