# Superfan

A Python utility for intelligent control of Supermicro server fan speeds based on component temperatures and user preferences.

## Features

- Automated fan speed control based on temperature sensors
- NVMe drive temperature monitoring via nvme-cli
- Support for multiple Supermicro generations (X9/X10/X11/X13)
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

### Method 1: From Source

```bash
# Clone the repository
git clone https://github.com/yourusername/superfan.git
cd superfan

# Install package in development mode
pip install -e .
```

### Method 2: RPM Installation (Red Hat-based systems only)

#### Building the RPM

Prerequisites:
- rpm-build
- rpmdevtools

Install build dependencies:
```bash
sudo dnf install rpm-build rpmdevtools
```

Build the RPM:
```bash
# Run the build script
./buildrpm.sh
```

The built RPM files will be available in:
- Binary RPM: ~/rpmbuild/RPMS/$(uname -m)/
- Source RPM: ~/rpmbuild/SRPMS/

#### Installing the RPM

```bash
# Install the RPM (replace x86_64 with your architecture if different)
sudo rpm -i ~/rpmbuild/RPMS/x86_64/superfan-0.1.0-1.*.rpm

# Or using dnf
sudo dnf install ~/rpmbuild/RPMS/x86_64/superfan-0.1.0-1.*.rpm
```

The RPM installation will:
- Install all required dependencies
- Set up the systemd service
- Create necessary configuration files
- Load required IPMI kernel modules

After installation, the service will be automatically started and enabled at boot.

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

- IPMI Commander: 95% coverage
  * Board generation detection
  * Fan speed control commands
  * Sensor reading parsing
  * Fan speed verification
  * Error handling and retries
  * Command validation
  * Safety checks

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
  * Board generation detection
  * Fan control commands
  * Sensor reading parsing
  * Error handling
  * Safety validation
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
