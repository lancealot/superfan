# Superfan

A Python utility for intelligent control of Supermicro server fan speeds based on component temperatures and user preferences.

## Features

- Automated fan speed control based on temperature sensors
- NVMe drive temperature monitoring via nvme-cli
- Support for multiple Supermicro generations:
  * X9/X10/X11/X13 series with full fan control
  * H12 series with automatic mode and monitoring
  * Robust board detection via DMI and IPMI info
- Zone-based fan control:
  * Chassis fan zone (FAN1-5 controlled as a group)
  * CPU fan zone (FANA controlled independently)
- Zone-specific fan curves and temperature thresholds:
  * Independent critical, warning, and target temperatures for each zone
  * Chassis zone optimized for system and storage cooling
  * CPU zone optimized for processor thermal management
- Safety-first approach with thermal protection
- Efficient dual-mode operation:
  * Normal mode: 30-second polling interval for reduced system overhead
  * Monitor mode: 5-second polling for responsive real-time monitoring
- Manual control options for direct fan speed adjustment
- Automatic learning of minimum stable fan speeds

## Requirements

- Python 3.8+
- python3-pip
- ipmitool
- nvme-cli
- Supermicro server with IPMI support

## Installation

### First Time Installation

```bash
sudo ./install.sh
```

This will:
1. Install required system packages (ipmitool, nvme-cli, python3-pip)
2. Load and configure IPMI kernel modules
3. Create configuration directory (/etc/superfan)
4. Install default configuration
5. Install Python package in development mode
6. Set appropriate file permissions
7. Install and start systemd service

### Updates and Reinstallation

When running install.sh on an existing installation:
1. Existing config file will be backed up to `/etc/superfan/config.yaml.bak`
2. New default config will be installed as `/etc/superfan/config.yaml.new`
3. You must manually merge any config changes:
   ```bash
   # Compare new config with existing
   diff /etc/superfan/config.yaml /etc/superfan/config.yaml.new
   
   # Merge changes as needed
   sudo nano /etc/superfan/config.yaml
   
   # Restart service to apply changes
   sudo systemctl restart superfan
   ```

### Configuration Files

- Main config: `/etc/superfan/config.yaml`
- Backup config: `/etc/superfan/config.yaml.bak` (created during updates)
- New config: `/etc/superfan/config.yaml.new` (created during updates)

### Service Management

```bash
# View service status
systemctl status superfan

# View logs
journalctl -u superfan

# Edit config
sudo nano /etc/superfan/config.yaml

# Restart service
sudo systemctl restart superfan
```

## Development

The project uses a src-layout with the following structure:

```
superfan/
├── src/superfan/         # Main package directory
│   ├── control/         # Fan control logic
│   ├── ipmi/           # IPMI communication
│   └── cli/            # Command-line interface
├── tests/              # Test suite
└── config/             # Configuration files
```

To run the tests:

```bash
# Run all tests with coverage
python -m pytest

# Run specific test file
python -m pytest tests/test_curve.py
```

### Current Test Coverage (as of 2025-02-10)

- CLI Interface: 100% coverage
  * Monitor mode functionality
  * Fan speed learning
  * Emergency state handling
  * Temperature thresholds
  * Fan speed mismatch detection
  * Signal handling and cleanup
  * Terminal resize handling
  * Color-coded temperature display

- IPMI Commander: 49% coverage
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

- Fan Curve Implementations: 93% coverage
  * Linear curve calculations
  * Step function behavior
  * Hysteresis handling
  * Temperature thresholds
  * Speed limits

- Control Manager: 15% coverage (needs improvement)
  * Basic control flow tested
  * Emergency state transitions needed
  * Fan curve implementations needed
  * Temperature management needed

- IPMI Sensors: 28% coverage (needs improvement)
  * Basic sensor reading tested
  * Temperature calculation needed
  * State tracking needed
  * Error handling needed

### Latest Test Improvements

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
- Terminal resize error handling

## Usage

See [USAGE.md](USAGE.md) for detailed usage instructions.

## Safety Features

- Hard-coded temperature limits for system protection
- Automatic fallback to full fan speed if temperature thresholds are exceeded
- Watchdog timer to ensure control loop reliability
- Automatic restoration of BMC control on program exit
- Intelligent handling of sensor readings:
  * Proper filtering of "no reading" and "ns" sensor states
  * Validation of sensor response IDs for data consistency
  * Safe handling of non-responsive fans and sensors
- Robust error handling:
  * Proper tracking of IPMI response IDs
  * Validation of sensor data before use in control decisions
  * Safe handling of communication errors and unexpected responses

## License

GPL-3.0 - See LICENSE file for details
