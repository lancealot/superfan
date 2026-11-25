# Hardware notes from the original H12 work (early 2025)

These are observations recorded while superfan 0.1 was developed on an
H12-series board in January and February 2025. They come from the old
`projectplan.md`, `CHANGELOG.md` and code comments. Nothing here has been
re-checked with 0.2; treat it as a starting point, not as fact.

## What was observed

Fan speeds with the BMC in its own automatic modes:

| Fan | RPM range seen |
|---|---|
| FAN1 | 1400-1820 |
| FAN2, FAN3, FAN4 | 1120-1400 |
| FAN5 | 1680-1960 |
| FANA (CPU cooler) | 3500-3780 |
| FANB | no reading (nothing connected) |

Other notes:

- BMC zone 0 drove FAN1-FAN5 and zone 1 drove FANA.
- Fan modes reported by `ipmitool raw 0x30 0x45 0x00`: `00` Standard, `01` Full,
  `02` Optimal, `04` Heavy IO.
- In Optimal mode the chassis fans were seen as low as 280 RPM.
- Speeds recorded as the lowest "stable" ones: 980 RPM for FAN1 and FAN5,
  700 RPM for FAN2-FAN4, 2520 RPM for FANA.
- `ipmitool raw 0x06 0x02` made sensor readings misbehave. In the IPMI
  specification that command is a BMC cold reset, which would explain it.
  `raw 0x06 0x01` is Get Device ID, which `ipmitool mc info` sends routinely.

## What to re-check, and why

1. **The duty-cycle scale.** 0.1 sent `0x10`, `0x20`, `0x40`, `0x60` and `0xff`
   and called them 12, 25, 50, 75 and 100 %. Its own usage notes described the
   range as `0x00`-`0x64`, and a code comment recorded `0xff` producing lower
   speeds than `0x60`. If the range is 0-100, those values were really 16, 32,
   64 and 96 % plus one out-of-range byte, and the board has no "discrete
   steps". `superfan calibrate` settles this: it sweeps 100 % down to 10 % in
   steps of 10 and prints the RPM at each.

2. **The BMC's lower fan thresholds.** 700 RPM, the "minimum stable" speed
   above, is a common Supermicro default for a fan's lower non-critical
   threshold. If the instability seen at low speeds was the BMC reacting to a
   fan crossing its threshold, the fix is to lower the thresholds rather than
   to avoid low speeds:

   ```bash
   ipmitool sensor | grep -i fan                     # the three numbers after the status are the lower thresholds
   ipmitool sensor thresh FAN2 lower 100 200 300     # example: move them below the fan's real minimum
   ```

   `superfan calibrate` reads these thresholds and stops lowering a zone before
   a fan gets within 15 % of one, so it will show which fan and which
   threshold limit each zone.

## Checking by hand

```bash
ipmitool raw 0x30 0x45 0x00                    # current fan mode
ipmitool raw 0x30 0x45 0x01 0x01               # Full mode
ipmitool raw 0x30 0x70 0x66 0x01 0x00 0x32     # zone 0 to 50 %
ipmitool sdr list full | grep -i fan           # read the result
ipmitool raw 0x30 0x45 0x01 0x00               # back to Standard when finished
```
