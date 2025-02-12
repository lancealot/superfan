# H12 Fan Control Investigation Results (2025-02-11)

## H12 Board Support Status (Updated 2025-02-11)

### Board Detection Implementation
1. Primary Method (DMI):
   - Uses dmidecode to get detailed board information
   - Specifically looks for "h12" markers
   - Most reliable detection method
   - Example: `sudo dmidecode -t baseboard`

2. Fallback Method (IPMI):
   - Uses IPMI MC info if DMI fails
   - Looks for "h12" or "b12" markers
   - Example: `ipmitool mc info`

3. Detection Reliability:
   - No longer attempts firmware version detection
   - Properly defaults to UNKNOWN if detection fails
   - Logs clear warnings about detection method used

### Current Limitations
1. Manual Fan Control:
   - Not supported on H12 boards
   - Command formats are unstable:
     * Basic format (0x30 0x70 0x66 0x01 [zone] [speed]) causes fans to stop
     * X10 format (0x30 0x70 0x66 0x01 [zone] 0x00 [speed]) also unstable
   - CPU fan (FANA) responds differently than chassis fans

2. Working Features:
   - Auto mode operation is stable
   - Temperature monitoring works correctly
   - Fan speed reporting is accurate
   - RPM ranges verified in auto mode:
     * FAN1: 1400-1820 RPM
     * FAN2-4: 1120-1400 RPM
     * FAN5: 1680-1960 RPM
     * FANA: 3500-3780 RPM
     * FANB: Non-responsive (expected)

### Current Implementation
1. H12 Board Handling:
   - Early detection via DMI/IPMI
   - Automatic mode enforcement
   - Passive monitoring only
   - Clear user warnings
   - Detailed logging

2. Safety Measures:
   - Prevents manual control attempts
   - Maintains BMC automatic control
   - Monitors temperatures safely
   - Logs all control attempts
   - Proper error handling

### Future Work
1. Short Term:
   - Improve detection logging
   - Add more H12 detection patterns
   - Enhance user warnings
   - Document thermal patterns

2. Long Term:
   - Monitor BMC firmware updates
   - Research vendor control methods
   - Consider H12-specific features
   - Explore BMC configuration options
