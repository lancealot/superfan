# Superfan

A Python utility for intelligent control of Supermicro server fan speeds based on component temperatures and user preferences.

## Features

- Automated fan speed control based on temperature sensors
- Support for multiple Supermicro generations (X9/X10/X11/X13)
- Custom fan curves and temperature thresholds
- Safety-first approach with thermal protection
- Real-time monitoring and manual control options

## Requirements

- Python 3.8+
- ipmitool
- Supermicro server with IPMI support

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/superfan.git
cd superfan

# Install package in development mode
pip install -e .
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

The test suite includes tests for:
- Fan curve implementations (linear, step, and hysteresis) - 85% coverage
- Temperature-to-speed mapping
- Safety limits and validation
- IPMI communication
- Control loop logic

Current test coverage:
- Fan curve implementations: 85% coverage
- CLI interface: 0% coverage (needs improvement)
- Control manager: 15% coverage (needs improvement)
- IPMI commander: 27% coverage (needs improvement)
- IPMI sensors: 28% coverage (needs improvement)

Verified functionality through manual testing:
- IPMI sensor readings working correctly
- Manual fan speed control operational
- Fan speed changes confirmed via sensor readings
- Proper entry/exit of manual control mode

## Usage

1. Edit the configuration file in `config/default.yaml` to match your setup
2. Run the utility:

```bash
python -m superfan
```

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
