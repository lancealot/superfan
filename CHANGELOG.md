# Changelog

## [0.2.0] - 2026-11-25

A rewrite. The configuration format, the command line and the packaging all
changed; nothing from a 0.1 install carries over except the idea.

### Added
- Temperatures from three kinds of source: BMC sensors (`ipmi:`), anything the
  kernel exposes under `/sys/class/hwmon` (`hwmon:`), and any command that
  prints a number (`command:`). CPUs, drives, GPUs and add-in cards can all
  drive fans.
- Per-zone inputs, each with its own curve in absolute degrees; a zone runs at
  the highest duty any input asks for.
- `superfan calibrate`: measures RPM at each duty per zone, finds which fans
  follow which zone and each zone's minimum safe duty, watches temperatures
  throughout, and writes to its own `calibration.yaml`.
- `superfan sensors`, `superfan check`, `superfan set`, `superfan release`.
- `superfan status` and `superfan status --watch`, which read a status file the
  daemon publishes instead of stopping the service and polling the BMC.
- `superfan run --dry-run`.
- A test suite that runs the real program against a simulated BMC and a fake
  hwmon tree, and CI that also builds and installs the RPM.

### Changed
- Duty cycles are sent to the BMC as 0-100 (`0x00`-`0x64`). 0.1 sent
  `0x10`/`0x20`/`0x40`/`0x60`/`0xff` and treated them as 12/25/50/75/100 %.
- One BMC sensor scan per control cycle, instead of twelve.
- Fan control returns to the BMC on SIGTERM, SIGINT, SIGHUP and on errors, and
  the systemd unit runs `superfan release` after any stop. 0.1 restored
  control only on Ctrl-C.
- Every `ipmitool` call has a timeout; the unit uses the systemd watchdog.
- The fan mode restored on exit is the one the BMC was in before, not always
  Standard.
- The RPM installs under the system Python path, requires PyYAML, and no
  longer enables or starts the service on install.
- `install.sh` installs into a virtual environment and never overwrites an
  existing config.

### Removed
- Hard-coded fan names, RPM tables and "speed steps" for one machine.
- The `--monitor`, `--learn` and `--manual` flags (see "Upgrading from 0.1" in
  the README for their replacements).
- The curses monitor's own control loop.
- Remote IPMI settings and X9 command handling, neither of which worked.
- `USAGE.md`, `DEVELOPMENT.md` and `projectplan.md`. Observations from real
  hardware that were recorded there are kept in `docs/hardware-notes.md`.

## [0.1.0] - 2025-02-10

The original version, written in January and February 2025. See the git
history for its changelog.
