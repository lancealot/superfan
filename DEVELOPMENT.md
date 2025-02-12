# Superfan Development Guide

This guide provides comprehensive information for developers working on the Superfan project.

## Architecture Overview

### Core Components

1. Control Manager (`control/manager.py`):
- Central control loop for fan speed management
- Temperature monitoring and decision making
- Safety monitoring and emergency handling
- Zone-based fan control implementation

2. IPMI Commander (`ipmi/commander.py`):
- IPMI communication layer
- Board generation detection
- Fan speed control commands
- Command validation and safety checks

3. Temperature Sensors (`ipmi/sensors.py`):
- IPMI sensor monitoring
- NVMe temperature monitoring
- Sensor pattern matching
- Temperature statistics

4. Fan Control (`control/learner.py`):
- Fan speed learning algorithms
- Minimum speed detection
- Board configuration learning
- Safety-first approach

### Control Flow

1. Temperature Monitoring:
```
CombinedTemperatureReader
├── IPMI Sensors
│   ├── Read sensor data
│   ├── Pattern matching
│   └── Statistics calculation
└── NVMe Monitoring
    ├── Drive discovery
    ├── Temperature reading
    └── Statistics calculation
```

2. Fan Control:
```
ControlManager
├── Temperature Analysis
│   ├── Zone temperature calculation
│   └── Threshold checking
├── Fan Speed Decision
│   ├── Fan curve application
│   └── Hysteresis handling
└── Safety Monitoring
    ├── Emergency detection
    └── Auto mode fallback
```

3. IPMI Communication:
```
IPMICommander
├── Board Detection
│   ├── DMI detection
│   └── IPMI fallback
├── Command Execution
│   ├── Command validation
│   └── Retry handling
└── Fan Control
    ├── Mode control
    └── Speed control
```

## Development Guidelines

### Code Style and Documentation

1. Python Standards:
- Follow PEP 8 style guide
- Use type hints for all functions
- Keep functions focused and under 50 lines
- Follow consistent naming conventions

2. Documentation Standards:
- Package-level documentation:
  * Overview of package purpose
  * Key components and features
  * Example usage
  * System requirements
  * Dependencies

- Class documentation:
  * Detailed class description
  * Feature list and capabilities
  * Implementation notes
  * Usage examples with expected output
  * Notes about special cases

- Method documentation:
  * Step-by-step process description
  * Parameter descriptions with types
  * Return value details
  * Error handling information
  * Usage examples
  * Notes about edge cases
  * Safety considerations

Example from IPMI package:
```python
def get_sensor_stats(self, sensor_name: str) -> Optional[Dict[str, float]]:
    """Get statistics for a specific temperature sensor.

    Calculates statistics from valid readings within timeout period:
    - current: Most recent temperature
    - min: Lowest temperature
    - max: Highest temperature
    - avg: Average temperature
    - stdev: Standard deviation (if >1 reading)

    Args:
        sensor_name: Name of the temperature sensor (e.g., "CPU1 Temp")

    Returns:
        Dict[str, float]: Statistics dictionary with keys:
            - "current": Latest temperature (°C)
            - "min": Minimum temperature (°C)
            - "max": Maximum temperature (°C)
            - "avg": Average temperature (°C)
            - "stdev": Standard deviation (if >1 reading)
        None: If insufficient valid readings or sensor not found

    Example:
        >>> stats = reader.get_sensor_stats("CPU1 Temp")
        >>> if stats:
        ...     print(f"Current: {stats['current']}°C")
        ...     print(f"Range: {stats['min']}-{stats['max']}°C")
        Current: 45.0°C
        Range: 42.0-48.0°C
    """
```

2. Error Handling:
- Use custom exceptions for specific error cases
- Include context in error messages
- Log errors with appropriate levels
- Handle cleanup in error cases

3. Testing:
- Write tests for new features
- Update tests when modifying code
- Include unit, integration, and performance tests
- Mock external dependencies

4. Documentation:
- Keep docstrings up to date
- Document complex algorithms
- Include examples in docstrings
- Update CHANGELOG.md for changes

### Safety Guidelines

1. Fan Control:
- Always validate fan speeds
- Include minimum speed checks
- Verify mode changes
- Handle cleanup properly

2. Temperature Monitoring:
- Validate sensor readings
- Handle missing sensors gracefully
- Include timeout checks
- Track reading history

3. IPMI Communication:
- Validate all commands
- Handle connection errors
- Include retry logic
- Verify responses

### Testing Strategy

1. Unit Tests:
- Test individual components
- Mock dependencies
- Test edge cases
- Verify error handling

2. Integration Tests:
- Test component interactions
- Test end-to-end scenarios
- Test error recovery
- Test cleanup procedures

3. Performance Tests:
- Test response times
- Test resource usage
- Test under load
- Test for memory leaks

### Common Development Tasks

1. Adding a New Feature:
```bash
# Create feature branch
git checkout -b feature/name

# Run tests
python -m pytest

# Format code
black .
isort .

# Run type checking
mypy src tests

# Update documentation
# - Update docstrings
# - Update CHANGELOG.md
# - Update README.md if needed
```

2. Fixing a Bug:
```bash
# Create bug fix branch
git checkout -b fix/name

# Add test case for bug
python -m pytest

# Fix bug and verify tests
python -m pytest

# Update CHANGELOG.md
```

3. Running Tests:
```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/test_manager.py

# Run with coverage
python -m pytest --cov=superfan

# Run performance tests
python -m pytest tests/test_performance.py
```

### Performance Guidelines

1. Response Times:
- Temperature updates < 100ms
- Fan speed changes < 50ms
- Emergency response < 200ms

2. Resource Usage:
- Memory usage < 50MB
- CPU usage < 5%
- File descriptors < 100

3. Scalability:
- Support 100+ sensors
- Handle rapid temperature changes
- Support concurrent operations

### Debugging

1. Logging Levels:
- DEBUG: Detailed information
- INFO: General operations
- WARNING: Concerning conditions
- ERROR: Error conditions
- CRITICAL: System failures

2. Common Issues:
- Fan speed verification failures
- Sensor reading timeouts
- IPMI communication errors
- Memory usage growth

3. Debugging Tools:
```bash
# View logs
journalctl -u superfan

# Check system status
systemctl status superfan

# Monitor resource usage
top -p $(pgrep superfan)

# Debug IPMI
ipmitool sdr list
```

### Release Process

1. Preparation:
- Update version number
- Update CHANGELOG.md
- Run full test suite
- Update documentation

2. Testing:
- Run unit tests
- Run integration tests
- Run performance tests
- Test installation

3. Release:
- Tag version
- Create release notes
- Build distribution
- Update package

### Configuration

1. Main Config (`/etc/superfan/config.yaml`):
- Fan control settings
- Temperature thresholds
- Safety parameters
- Logging settings

2. Development Config:
- Use local config for development
- Include test-specific settings
- Mock external services
- Enable debug logging

### Monitoring

1. Metrics:
- Fan speeds
- Temperatures
- Response times
- Resource usage

2. Alerts:
- Critical temperatures
- Fan failures
- System errors
- Resource limits

3. Dashboards:
- System overview
- Temperature trends
- Fan speed history
- Error tracking

## Contributing

1. Code Review Process:
- Create feature branch
- Write tests
- Update documentation
- Submit pull request
- Address review comments

2. Documentation:
- Keep README.md updated
- Maintain CHANGELOG.md
- Update DEVELOPMENT.md
- Include code comments

3. Testing:
- Maintain test coverage
- Add regression tests
- Include performance tests
- Document test cases

4. Communication:
- Use clear commit messages
- Document design decisions
- Update issue tracker
- Participate in reviews
