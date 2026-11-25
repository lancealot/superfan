"""Command line: run, status, sensors, check, calibrate, set, release."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from typing import List, Optional

from . import __version__
from .bmc import MODE_FULL, Bmc, BmcError, mode_name
from .calibrate import (CalibrationAborted, Calibrator, Settings, load_calibration, save_calibration,
                        summary)
from .config import DEFAULT_CONFIG_PATH, DEFAULT_STATUS_FILE, Config, ConfigError, load_config
from .controller import Controller
from .sdnotify import notify
from .session import Busy, Session, acquire_lock
from .sources import collect
from .status import StatusError, frame_text, load_frame, read_status

log = logging.getLogger("superfan")

EXIT_CONFIG = 78  # sysexits EX_CONFIG; the unit file tells systemd not to restart on it


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="superfan", description="Temperature-driven fan control for Supermicro servers.")
    parser.add_argument("--version", action="version", version="superfan " + __version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default=os.environ.get("SUPERFAN_CONFIG", DEFAULT_CONFIG_PATH),
                        help="configuration file (default: %(default)s)")
    common.add_argument("-v", "--verbose", action="store_true", help="log debugging detail")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    run = sub.add_parser("run", parents=[common], help="control the fans until stopped (the daemon)")
    run.add_argument("--dry-run", action="store_true",
                     help="read sensors and log decisions, but send nothing to the BMC")
    run.add_argument("--once", action="store_true", help="run a single cycle and exit")

    status = sub.add_parser("status", help="show what the running daemon last reported")
    status.add_argument("-c", "--config", default=None, help="take the status file location from this config")
    status.add_argument("--status-file", default=None, help="status file to read (default: %s)" % DEFAULT_STATUS_FILE)
    status.add_argument("--watch", action="store_true", help="full-screen dashboard that refreshes itself")
    status.add_argument("--json", action="store_true", help="print the raw status as JSON")

    sub.add_parser("sensors", parents=[common], help="list every temperature and fan superfan can see")

    check = sub.add_parser("check", parents=[common], help="validate the configuration")
    check.add_argument("--offline", action="store_true", help="only check the file; do not read any sensor")

    cal = sub.add_parser("calibrate", parents=[common], help="measure fan speeds and find each zone's minimum duty")
    cal.add_argument("-o", "--output", default=None, help="where to write the result (default: calibration_file)")
    cal.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    cal.add_argument("--step", type=int, default=10, help="duty decrement per step (default: %(default)s)")
    cal.add_argument("--floor", type=int, default=10, help="lowest duty to try (default: %(default)s)")
    cal.add_argument("--max-temp", type=float, default=None,
                     help="abort above this temperature (default: the top of each sensor's curve)")
    cal.add_argument("--settle-min", type=float, default=6.0, help="seconds to wait before reading a step")
    cal.add_argument("--settle-max", type=float, default=30.0, help="longest wait for steady fans, in seconds")
    cal.add_argument("--poll", type=float, default=3.0, help="seconds between readings while settling")

    manual = sub.add_parser("set", parents=[common], help="take manual control and set one zone's duty")
    manual.add_argument("zone", help="zone name from the config, or a BMC zone number")
    manual.add_argument("duty", type=int, help="duty cycle in percent, 0-100")

    sub.add_parser("release", parents=[common], help="return fan control to the BMC")
    return parser


def _make_bmc(cfg: Config, dry_run: bool = False) -> Bmc:
    return Bmc(cfg.bmc.command, timeout=cfg.bmc.timeout, retries=cfg.bmc.retries, dry_run=dry_run)


def _install_signal_handlers(stop: threading.Event) -> None:
    def handler(signum, frame):
        log.info("received %s, stopping", signal.Signals(signum).name)
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, handler)


def _lock_or_explain(cfg: Config):
    try:
        return acquire_lock(cfg.state_dir)
    except Busy as exc:
        print("superfan is already controlling the fans (%s). Stop it first: systemctl stop superfan" % exc,
              file=sys.stderr)
    except OSError as exc:
        print("cannot use %s: %s (are you root?)" % (cfg.state_dir, exc.strerror), file=sys.stderr)
    return None


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    calibration = load_calibration(cfg.calibration_file)
    lock = None
    if not args.dry_run:
        lock = _lock_or_explain(cfg)
        if lock is None:
            return 1
    if not calibration:
        log.warning("not calibrated: zone minimums come from the config alone (run `superfan calibrate`)")
    stop = threading.Event()
    _install_signal_handlers(stop)
    controller = Controller(cfg, _make_bmc(cfg, args.dry_run), calibration, notify=notify)
    try:
        return controller.run(stop, once=args.once)
    finally:
        if lock is not None:
            lock.close()


def cmd_status(args) -> int:
    path = args.status_file
    if path is None and args.config:
        path = load_config(args.config).status_file
    path = path or DEFAULT_STATUS_FILE
    if args.watch:
        from .tui import watch
        return watch(path)
    if args.json:
        try:
            print(json.dumps(read_status(path), indent=2))
        except StatusError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    frame, live = load_frame(path, time.time())
    print(frame_text(frame))
    return {"running": 0, "missing": 2}.get(live, 1)


def _load_or_default(path: str) -> Config:
    """sensors and release must work before a config exists, or while it is broken."""
    try:
        return load_config(path)
    except ConfigError as exc:
        print("note: %s\n      using built-in defaults (ipmitool, hwmon)\n" % exc, file=sys.stderr)
        return Config(path=path)


def cmd_sensors(args) -> int:
    cfg = _load_or_default(args.config)
    snap = collect(cfg, _make_bmc(cfg))
    used = {}
    for zone in cfg.zones.values():
        for inp in zone.inputs:
            for reading in snap.matching(inp.sensors):
                used.setdefault(reading.name, []).append(zone.name)
    width = max([len(r.name) for r in snap.temps] + [20])
    print("TEMPERATURES%s  USED BY" % (" " * (width - 2)))
    for reading in sorted(snap.temps, key=lambda r: r.name):
        print("  %-*s %7.1f°C  %s" % (width, reading.name, reading.value, ", ".join(used.get(reading.name, [])) or "-"))
    if not snap.temps:
        print("  (none)")
    print("\nFANS")
    for fan in snap.fans:
        print("  %-*s %7s RPM  %s" % (width, fan.name, "-" if fan.rpm is None else "%d" % fan.rpm, fan.status))
    if not snap.fans:
        print("  (none)")
    if snap.errors:
        print("\nPROBLEMS")
        for source, error in sorted(snap.errors.items()):
            print("  %s: %s" % (source, error))
    print("\nIn the config, a hwmon pattern may leave out @device to match every device of that chip,")
    print("for example hwmon:nvme/Composite. Patterns accept * and ? wildcards and ignore case.")
    return 0 if snap.temps else 1


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    print("%s: syntax OK (%d zone(s), interval %gs)" % (cfg.path, len(cfg.zones), cfg.interval))
    try:
        calibration = load_calibration(cfg.calibration_file)
    except ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("calibration: %s" % ("%s (%s)" % (cfg.calibration_file, ", ".join(sorted(calibration)))
                              if calibration else "none yet (run `superfan calibrate`)"))
    if args.offline:
        return 0
    snap = collect(cfg, _make_bmc(cfg))
    problems = 0
    for source, error in sorted(snap.errors.items()):
        print("problem: source %s: %s" % (source, error))
        problems += 1
    for zone in cfg.zones.values():
        print("zone %s (BMC zone %d):" % (zone.name, zone.bmc_zone))
        for inp in zone.inputs:
            matched = snap.matching(inp.sensors)
            if matched:
                print("  %s -> %s" % (inp.label, ", ".join("%s %.1f°C" % (r.name, r.value) for r in matched)))
            elif inp.required:
                print("  %s -> NOTHING MATCHES; this zone would run at %d%%" % (inp.label, cfg.failsafe_duty))
                problems += 1
            else:
                print("  %s -> nothing matches (optional)" % inp.label)
    if problems:
        print("\n%d problem(s). `superfan sensors` lists the names that exist on this machine." % problems)
    return 1 if problems else 0


def cmd_calibrate(args) -> int:
    cfg = load_config(args.config)
    output = args.output or cfg.calibration_file
    if not 1 <= args.step <= 50 or not 0 <= args.floor < 100:
        print("--step must be 1-50 and --floor 0-99", file=sys.stderr)
        return 2
    if not args.yes:
        print("Calibration steps each zone's fans down from 100%% towards %d%% while watching temperatures.\n"
              "Run it with the machine idle. It takes a few minutes." % args.floor)
        if not sys.stdin.isatty():
            print("Refusing to start without confirmation; pass --yes.", file=sys.stderr)
            return 2
        if input("Start? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1
    lock = _lock_or_explain(cfg)
    if lock is None:
        return 1
    stop = threading.Event()
    _install_signal_handlers(stop)
    settings = Settings(step=args.step, floor=args.floor, settle_min=args.settle_min,
                        settle_max=args.settle_max, poll=args.poll, max_temp=args.max_temp)
    try:
        data = Calibrator(cfg, _make_bmc(cfg), settings, stop).run()
    except (CalibrationAborted, BmcError) as exc:
        print("\nCalibration abandoned: %s\nFans were sent to 100%% and control returned to the BMC. "
              "Nothing was written." % exc, file=sys.stderr)
        return 1
    finally:
        lock.close()
    save_calibration(output, data)
    print("\n" + summary(data))
    print("\nWrote %s. Restart the daemon to use it: systemctl restart superfan" % output)
    return 0


def cmd_set(args) -> int:
    cfg = _load_or_default(args.config)
    if args.zone in cfg.zones:
        bmc_zone = cfg.zones[args.zone].bmc_zone
    elif args.zone.isdigit():
        bmc_zone = int(args.zone)
    else:
        print("unknown zone %r (config zones: %s)" % (args.zone, ", ".join(cfg.zones) or "none"), file=sys.stderr)
        return 2
    if not 0 <= args.duty <= 100:
        print("duty must be 0-100", file=sys.stderr)
        return 2
    lock = _lock_or_explain(cfg)
    if lock is None:
        return 1
    try:
        bmc = _make_bmc(cfg)
        Session(bmc, cfg.state_dir, cfg.bmc.exit_mode).takeover()
        bmc.set_duty(bmc_zone, args.duty)
    finally:
        lock.close()
    print("BMC zone %d set to %d%%. Nothing is watching temperatures now.\n"
          "Run `superfan release` (or start the service) to end manual control." % (bmc_zone, args.duty))
    return 0


def cmd_release(args) -> int:
    cfg = _load_or_default(args.config)
    bmc = _make_bmc(cfg)
    before = bmc.get_mode()
    if not Session(bmc, cfg.state_dir, cfg.bmc.exit_mode).release():
        return 1
    after = bmc.get_mode()
    if before == MODE_FULL:
        print("Fan control returned to the BMC (%s mode)." % mode_name(after))
    else:
        print("The BMC already controls the fans (%s mode)." % mode_name(after))
    return 0


COMMANDS = {"run": cmd_run, "status": cmd_status, "sensors": cmd_sensors, "check": cmd_check,
            "calibrate": cmd_calibrate, "set": cmd_set, "release": cmd_release}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        format="%(levelname)s: %(message)s", stream=sys.stderr)
    try:
        return COMMANDS[args.command](args)
    except ConfigError as exc:
        print("configuration error in %s:" % (exc.path or "config"), file=sys.stderr)
        for problem in exc.problems:
            print("  - " + problem, file=sys.stderr)
        return EXIT_CONFIG
    except BmcError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
