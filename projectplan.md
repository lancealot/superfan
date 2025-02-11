# H12 Fan Control Investigation Results (2025-02-11)

## Current Status
- Manual fan control on H12 boards is not working reliably:
  * Documented command formats are not stable:
    - Basic format (0x30 0x70 0x66 0x01 [zone] [speed]) causes fans to stop
    - X10 format (0x30 0x70 0x66 0x01 [zone] 0x00 [speed]) also unstable
  * Auto mode works correctly, suggesting BMC has special handling
  * Fan behavior is inconsistent:
    - Some fans stop completely
    - Others run at different speeds than commanded
    - CPU fan (FANA) responds differently than chassis fans

## Verified Working Commands
1. Mode Control:
   - Get Mode: 0x30 0x45 0x00 (returns "01" for manual, "00" for auto)
   - Set Auto: 0x30 0x45 0x01 0x00 (works reliably)
   - Set Manual: 0x30 0x45 0x01 0x01 (works but fan control unreliable)

2. Fan Status:
   - Auto mode RPM ranges verified:
     * FAN1: 1400-1820 RPM
     * FAN2-4: 1120-1400 RPM
     * FAN5: 1680-1960 RPM
     * FANA: 3500-3780 RPM
     * FANB: Non-responsive (expected)

## Recommended Approach
1. H12 Board Handling:
   - Detect H12 boards early via DMI information
   - Prevent manual fan control attempts
   - Keep system in automatic mode for stability
   - Monitor temperatures and fan speeds passively
   - Log clear warnings about H12 limitations

2. Auto Mode Operation:
   - Verified working RPM ranges:
     * FAN1: 1400-1820 RPM
     * FAN2-4: 1120-1400 RPM
     * FAN5: 1680-1960 RPM
     * FANA: 3500-3780 RPM
   - BMC handles fan curves automatically
   - Provides stable and safe operation
   - Prevents fan stalling issues

3. Future Considerations:
   - Monitor BMC firmware updates
   - Document thermal behavior patterns
   - Consider temperature monitoring only mode
   - Research vendor-specific control methods
