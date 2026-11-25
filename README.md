# Superfan

Superfan sets the fan speed of a Supermicro server from the temperatures of the
things those fans cool. It reads temperatures from the BMC, from any device the
Linux kernel exposes (CPUs, drives, graphics cards, add-in cards) and from any
command that prints a number, and drives each of the BMC's fan zones along
curves you define.

It is built to fail towards cooling. If a sensor disappears, a fan stops or the
BMC stops answering, fans go to full speed or control goes back to the BMC, and
whenever superfan stops for any reason the BMC gets the fans back.

> **Hardware status.** Superfan was developed on an H12-series board. Version
> 0.2 is a rewrite, and so far it has only been run against a simulated BMC (the
> test suite). Treat the first run on a real machine as a test: follow
> [Getting started](#getting-started) in order and do not skip the calibration
> or the dry run.

## How it works

A Supermicro BMC groups fan headers into zones and accepts a duty cycle
(0-100 %) per zone while it is in "Full" fan mode. On the H12 board this was
developed on, zone 0 drives the numbered headers (FAN1-FAN5) and zone 1 the
lettered ones (FANA, FANB); calibration works out the mapping on yours.
Superfan uses these BMC commands, through `ipmitool`:

| Purpose | Command |
|---|---|
| Read the fan mode | `raw 0x30 0x45 0x00` |
| Set the fan mode | `raw 0x30 0x45 0x01 <mode>` |
| Set a zone's duty cycle | `raw 0x30 0x70 0x66 0x01 <zone> <duty>`, duty `0x00`-`0x64` |
| Read sensors | `sdr list full` (once per cycle) |
| Read fan thresholds | `sensor` (calibration only) |

Older X9 boards use different commands and are not supported.

Every cycle superfan reads all its sources once, works out a duty for each
zone, and sends it only if it changed (and once every six cycles regardless).
A zone runs at the highest duty any of its inputs asks for. Rising
temperatures are followed immediately; a zone slows down again only after the
temperature has fallen by more than the hysteresis.

## Requirements

- Linux with systemd, and Python 3.9 or newer
- `ipmitool`, and the `ipmi_devintf` and `ipmi_si` kernel modules
- root, for access to the BMC

## Install

### RPM

```bash
./buildrpm.sh
sudo dnf install ~/rpmbuild/RPMS/noarch/superfan-*.noarch.rpm
```

`buildrpm.sh` builds from the last commit and lists the packages it needs if
`rpmbuild` is missing. The RPM installs the program, the systemd unit,
`/etc/superfan/config.yaml` and a `modules-load.d` entry for the IPMI modules.
It pulls in `ipmitool` and PyYAML. It does not enable or start the service, and
upgrading never replaces a config you have edited.

### From a checkout

```bash
sudo ./install.sh
```

This installs `ipmitool`, puts superfan in a virtual environment under
`/opt/superfan`, links `/usr/local/bin/superfan`, and installs the config and
the systemd unit. It does not enable or start the service. Run it again to
upgrade: an existing `/etc/superfan/config.yaml` is kept, and the current
default is written next to it as `config.yaml.new`.

`sudo ./uninstall.sh` removes all of that and keeps `/etc/superfan`;
`sudo ./uninstall.sh --purge` removes that too.

## Getting started

1. **See what the machine can measure.**

   ```bash
   sudo superfan sensors
   ```

   This lists every temperature from every source, every fan the BMC reports,
   and which zone (if any) uses each sensor. It changes nothing.

2. **Edit `/etc/superfan/config.yaml`** so that each zone lists the sensors its
   fans cool. See [Configuration](#configuration).

3. **Check it.**

   ```bash
   sudo superfan check
   ```

   This prints what each input matches right now and fails if a required input
   matches nothing.

4. **Calibrate.** With the machine idle:

   ```bash
   sudo superfan calibrate
   ```

   See [Calibration](#calibration).

5. **Dry run.** See what superfan would do without letting it do it:

   ```bash
   sudo superfan run --dry-run --once
   ```

6. **Enable the service** and watch it:

   ```bash
   sudo systemctl enable --now superfan
   superfan status --watch
   ```

## Configuration

`/etc/superfan/config.yaml`. Unknown keys are errors, so a typo cannot silently
turn a setting off; `superfan check` lists every problem it finds.

```yaml
interval: 10        # seconds between control cycles (at least 1)
hysteresis: 3       # degrees a temperature must fall before a zone slows down

sources:
  ipmi: true        # BMC sensors
  hwmon: true       # kernel sensors under /sys/class/hwmon
  command:          # any command that prints a temperature in degrees C
    gpu0: nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits -i 0

zones:
  cpu:
    bmc_zone: 1                     # the BMC fan zone this zone drives
    inputs:
      - sensors: ["ipmi:CPU*Temp", "hwmon:k10temp/*"]
        curve: [[50, 20], [65, 40], [75, 70], [80, 100]]   # [temperature, duty %]
        critical: 85                # at or above this, every zone goes to full speed
      - sensors: ["ipmi:*VRM*"]
        curve: [[60, 20], [80, 60], [95, 100]]
        required: false             # not every board has this sensor
  chassis:
    bmc_zone: 0
    min_duty: 20                    # never run this zone below 20 %
    inputs:
      - sensors: ["ipmi:System Temp", "ipmi:Peripheral Temp"]
        curve: [[40, 20], [55, 40], [65, 70], [70, 100]]
      - sensors: ["command:gpu0", "hwmon:amdgpu/*"]
        curve: [[50, 25], [70, 60], [85, 100]]
        required: false
      - sensors: ["hwmon:nvme/*"]
        curve: [[45, 20], [60, 50], [70, 100]]
        required: false

safety:
  failsafe_duty: 100   # duty used when something is wrong
  fault_hold: 60       # seconds to stay there after the problem clears
  max_failures: 3      # failed cycles in a row before giving up and handing back to the BMC

bmc:
  command: ipmitool
  timeout: 30          # seconds before an ipmitool call is abandoned (at most 60)
  exit_mode: previous  # fan mode to hand back: previous, standard, optimal or heavy_io
```

### Zones and inputs

| Key | Meaning |
|---|---|
| `bmc_zone` | Required. The BMC zone number. Two zones cannot share one. |
| `min_duty` | Lowest duty for the zone, default 0. Calibration can raise it, never lower it. |
| `hysteresis` | Overrides the global `hysteresis` for this zone. |
| `inputs` | One or more groups of sensors, each with its own curve. |
| `inputs[].sensors` | One pattern or a list. The hottest matching sensor is used. |
| `inputs[].curve` | `[temperature, duty]` points. Duty is interpolated between points and flat beyond the ends. Temperatures must increase and duty must not fall. |
| `inputs[].critical` | Optional. A matching sensor at or above this puts every zone at the failsafe duty. |
| `inputs[].required` | Default `true`. If no sensor matches a required input, the zone runs at the failsafe duty. Set `false` for sensors that may be absent. |

### Sensor names

Patterns look like `source:name`, accept `*` and `?` wildcards and ignore case.

| Pattern | Matches |
|---|---|
| `ipmi:CPU Temp` | The BMC sensor called "CPU Temp". |
| `hwmon:k10temp/Tctl` | The sensor labelled "Tctl" on every `k10temp` chip. |
| `hwmon:nvme/*` | Every sensor on every NVMe drive. |
| `hwmon:nvme@nvme0/Composite` | The same, pinned to one device. |
| `command:gpu0` | The command named `gpu0` under `sources.command`. |

`superfan sensors` prints the exact names, in the long `chip@device/label`
form. A hwmon sensor without a label is called `temp1`, `temp2`, and so on.

A command runs without a shell and must print one temperature per line. If it
prints several, they are named `command:gpu0/0`, `command:gpu0/1`, ..., and
`command:gpu0` matches all of them. A command that fails, hangs for more than
10 seconds or prints no number counts as a missing sensor.

Readings of 0 °C or below, or 150 °C or above, are treated as missing: that is
what unconnected and sleeping sensors report.

Because commands in the config run as root, superfan refuses a config that
defines commands unless the file is owned by root and not writable by group or
others.

### Other settings

| Key | Default | Meaning |
|---|---|---|
| `sources.hwmon` | `true` | `false` turns the source off. |
| `sources.ipmi` | `true` | `false` turns BMC sensors off; fan speeds are then not monitored either. |
| `bmc.command` | `ipmitool` | The command line used to reach the BMC. |
| `bmc.retries` | 2 | Extra attempts for a command the BMC rejects. |
| `status_file` | `/run/superfan/status.json` | Where the daemon publishes its state. |
| `calibration_file` | `calibration.yaml` beside the config | Where calibration results are kept. |

## Commands

| Command | What it does |
|---|---|
| `superfan run` | The daemon: takes control, loops until stopped, hands control back. |
| `superfan run --dry-run` | Reads sensors and logs what it would do; sends nothing and can run beside the live daemon. |
| `superfan run --once` | One cycle, then exit. |
| `superfan status` | Prints what the daemon last reported. |
| `superfan status --watch` | Full-screen colour dashboard. `q` quits. |
| `superfan status --json` | The raw report. |
| `superfan sensors` | Lists every temperature and fan superfan can see. |
| `superfan check` | Validates the config and shows what each input matches. `--offline` checks the file only. |
| `superfan calibrate` | Measures the fans; see below. |
| `superfan set ZONE DUTY` | Manual control: puts one zone at a fixed duty. Nothing watches temperatures until you run `release` or start the service. |
| `superfan release` | Returns fan control to the BMC. |

Every command except `status` takes `-c FILE` to use a config other than
`/etc/superfan/config.yaml`. `status` reads `/run/superfan/status.json` unless
given `--status-file FILE` or `-c FILE`.

Only one process may command the fans at a time: `calibrate`, `set` and a
second `run` are refused while the daemon is running.

`superfan status` exits 0 when the daemon is running, 1 when it has stopped or
is no longer updating, and 2 when it has not run since boot. `superfan run`
exits 78 on a configuration error, and the service does not restart on that.

## Calibration

```bash
sudo systemctl stop superfan
sudo superfan calibrate
sudo systemctl start superfan
```

Calibration measures what each duty cycle actually does on your machine:

1. It puts every zone at 100 % and waits for the fans to settle.
2. One zone at a time, it steps the duty down by 10 points. After each step
   it waits for the fans to settle and then reads them, so each row of the
   table holds the speed for its own duty.
3. It stops lowering a zone when a fan stops, or comes within 15 % of the BMC's
   lower fan threshold. The step before that becomes the zone's minimum duty.
4. It works out which fans belong to which zone from which ones slowed down.
5. It returns each zone to 100 % before touching the next, and hands the fans
   back to the BMC at the end.

Temperatures are checked at every reading. If any sensor reaches the top of
its curve (or its `critical`, if lower), or a required sensor disappears,
calibration is abandoned: fans go to 100 %, control returns to the BMC, and
nothing is written. Ctrl-C does the same.

Results go to `/etc/superfan/calibration.yaml`. Your config is never modified.
The daemon reads the file at start and will not run a zone below its measured
minimum. Run calibration again after changing fans or moving them between
headers.

Options: `--step N` (default 10), `--floor N` lowest duty to try (default 10),
`--max-temp C` one abort limit for every sensor, `--output FILE` to write
somewhere else, `--yes` to skip the confirmation, and `--settle-min`,
`--settle-max` and `--poll` to tune how long it waits for fans to settle.

## What happens when things go wrong

| Situation | Response |
|---|---|
| A required input has no reading (sensor gone, source failing, command erroring) | That zone runs at the failsafe duty until the reading returns. Other zones carry on. |
| The BMC sensor scan fails or times out | Zones that depend on BMC sensors run at the failsafe duty. |
| A sensor reaches its `critical` temperature, or the BMC itself flags a temperature sensor critical | Every zone goes to the failsafe duty and stays there for `fault_hold` seconds after it clears. |
| A fan that was turning stops, stops reporting, or falls below the BMC's critical threshold | The same: every zone to the failsafe duty, with the hold. |
| The BMC leaves Full mode while superfan is running | Superfan takes control again and re-sends the duties. |
| The BMC rejects commands for `max_failures` cycles in a row | Superfan hands control back to the BMC and exits with an error; the service restarts it. |
| An `ipmitool` call hangs | It is abandoned after `bmc.timeout` seconds and treated as a failure. |
| Superfan is stopped (SIGTERM, SIGINT, SIGHUP) or hits an internal error | Fan control goes back to the BMC before it exits. |
| Superfan is killed outright, or hangs | The service's `ExecStopPost` runs `superfan release`, which hands control back. The systemd watchdog kills a daemon that stops reporting. |

"Back to the BMC" means the fan mode the BMC was in before superfan started
(`exit_mode: previous`), or Standard if that is not known.

Outside systemd nothing can clean up after `kill -9`: run `superfan release`
yourself.

## The status dashboard

```bash
superfan status --watch
```

A full-screen view of the daemon's state: overall status, each zone's duty,
what it is asking for and which sensor is driving it, every fan's speed, and
every temperature in use. Temperatures are green below their curve, yellow on
it and red at the top; a stopped fan is red; the status line turns yellow in
failsafe and red in an emergency or when the daemon is not running.

The dashboard only reads the file the daemon publishes. It does not stop the
service, does not talk to the BMC, and does not need root.

## Service

```bash
sudo systemctl enable --now superfan    # start now and at boot
sudo systemctl restart superfan         # after editing the config or calibrating
journalctl -u superfan                  # logs: duty changes and state changes
```

## Upgrading from 0.1

The configuration format changed completely; 0.2 refuses a 0.1 config and says
so. Start from the new default config. The old flags are gone:
`--monitor` became `superfan status --watch`, `--learn` became
`superfan calibrate`, and `--manual N` became `superfan set ZONE N`.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest
```

The tests run the real program against `tests/fake_ipmitool.py`, a simulated
BMC that records every command it receives and rejects malformed ones, and
against a fake `/sys/class/hwmon` tree. CI runs them on several Python
versions, builds and installs the RPM in a Fedora container, and runs
`install.sh` and `uninstall.sh` on Ubuntu.

Notes from the original work on real hardware are in
[docs/hardware-notes.md](docs/hardware-notes.md).

## License

GPL-3.0. See [LICENSE](LICENSE).
