"""Fan calibration: measure what each duty cycle actually does on this machine.

For one zone at a time the duty is stepped down from 100 %. After each step
the fans are given time to settle and are then read, so every row of the table
holds the RPM for its own duty. The sweep of a zone ends at the first step
where a fan stops or gets close to the BMC's lower threshold; the step before
that is the zone's minimum safe duty.

Temperatures are checked on every sensor read. If one reaches its limit the
whole run is abandoned: fans go to 100 %, control returns to the BMC, and
nothing is written.
"""

from __future__ import annotations

import datetime
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

import yaml

from .bmc import Bmc, BmcError
from .config import Config, ConfigError
from .session import Session
from .sources import collect

HEADER = (
    "# Written by `superfan calibrate`. Do not edit by hand;\n"
    "# run calibrate again after changing fans or moving them between headers.\n"
)

Rpms = Dict[str, Optional[float]]


class CalibrationAborted(Exception):
    pass


@dataclass
class Settings:
    step: int = 10
    floor: int = 10
    settle_min: float = 6.0   # wait this long after a change before the first reading
    settle_max: float = 30.0  # give up waiting for a steady reading after this long
    poll: float = 3.0         # time between readings while settling
    margin: float = 1.15      # stop when a fan is within 15 % of the BMC's lower threshold
    max_temp: Optional[float] = None  # one limit for every sensor, instead of each curve's top


def _limit(inp, settings: Settings) -> float:
    if settings.max_temp is not None:
        return settings.max_temp
    top = inp.curve[-1][0]
    return min(top, inp.critical) if inp.critical is not None else top


def check_heat(cfg: Config, snap, settings: Settings) -> None:
    for zone in cfg.zones.values():
        for inp in zone.inputs:
            matched = snap.matching(inp.sensors)
            if not matched:
                if inp.required:
                    raise CalibrationAborted("lost the reading for %s, so temperatures cannot be watched" % inp.label)
                continue
            hottest = max(matched, key=lambda r: r.value)
            limit = _limit(inp, settings)
            if hottest.value >= limit:
                raise CalibrationAborted("%s reached %.1f°C (limit %g°C)" % (hottest.name, hottest.value, limit))


def _steady(before: Rpms, now: Rpms, earlier: Optional[Rpms]) -> bool:
    """True once no fan is still heading in one direction by more than noise."""
    if before.keys() != now.keys():
        return False
    for name, rpm in now.items():
        last = before[name]
        if rpm is None or last is None:
            if rpm is not last:
                return False
            continue
        change = rpm - last
        if abs(change) <= max(20.0, 0.02 * max(rpm, last)):
            continue
        prior = earlier.get(name) if earlier else None
        if prior is not None and (last - prior) * change < 0:
            continue  # reversed direction: the reading is flickering, not trending
        return False
    return True


def _lower_threshold(thresholds: Dict[str, float]) -> Optional[float]:
    for key in ("lnc", "lcr", "lnr"):
        if thresholds.get(key):
            return thresholds[key]
    return None


class Calibrator:
    def __init__(
        self,
        cfg: Config,
        bmc: Bmc,
        settings: Settings,
        stop: Optional[threading.Event] = None,
        say: Callable[[str], None] = print,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg, self.bmc, self.settings = cfg, bmc, settings
        self.stop = stop or threading.Event()
        self.say = say
        self.clock = clock
        self.session = Session(bmc, cfg.state_dir, cfg.bmc.exit_mode)
        self.thresholds: Dict[str, Dict[str, float]] = {}

    def _pause(self, seconds: float) -> None:
        if self.stop.wait(seconds) or self.stop.is_set():
            raise CalibrationAborted("interrupted")

    def _read(self) -> Rpms:
        snap = collect(self.cfg, self.bmc)
        if "ipmi" in snap.errors:
            raise CalibrationAborted("cannot read the BMC's sensors: %s" % snap.errors["ipmi"])
        check_heat(self.cfg, snap, self.settings)
        return {fan.name: fan.rpm for fan in snap.fans}

    def _settled(self) -> Rpms:
        """Wait for the fans to stop changing, watching temperatures, then return their RPM."""
        deadline = self.clock() + self.settings.settle_max
        self._pause(self.settings.settle_min)
        earlier: Optional[Rpms] = None
        before = self._read()
        while True:
            self._pause(self.settings.poll)
            now = self._read()
            if _steady(before, now, earlier) or self.clock() >= deadline:
                return now
            earlier, before = before, now

    def _problem(self, baseline: Rpms, now: Rpms) -> Optional[str]:
        for name, rpm in sorted(now.items()):
            if not baseline.get(name):
                continue  # was not turning at 100 % either: an empty header
            if not rpm:
                return "%s stopped" % name
            lower = _lower_threshold(self.thresholds.get(name, {}))
            if lower and rpm <= lower * self.settings.margin:
                return "%s fell to %d RPM, too close to the BMC's lower threshold of %d RPM" % (name, rpm, lower)
        return None

    def run(self) -> dict:
        settings = self.settings
        zones = list(self.cfg.zones.values())
        try:
            self.thresholds = self.bmc.thresholds()
        except BmcError as exc:
            self.say("warning: cannot read the BMC's fan thresholds (%s); only stalls will be detected" % exc)
        result: Dict[str, dict] = {}
        try:
            self.session.takeover()
            for zone in zones:
                self.bmc.set_duty(zone.bmc_zone, 100)
            self.say("All zones at 100 %, waiting for the fans to settle...")
            baseline = self._settled()
            if not any(baseline.values()):
                raise CalibrationAborted("no fan reports a speed, so there is nothing to calibrate")
            for zone in zones:
                table: Dict[int, Rpms] = {100: dict(baseline)}
                minimum, stopped = 100, None
                duty = 100 - settings.step
                while duty >= settings.floor:
                    self.say("  zone %s: %d %%" % (zone.name, duty))
                    self.bmc.set_duty(zone.bmc_zone, duty)
                    table[duty] = self._settled()
                    problem = self._problem(baseline, table[duty])
                    if problem:
                        stopped = "%s at %d %%" % (problem, duty)
                        break
                    minimum = duty
                    duty -= settings.step
                self.bmc.set_duty(zone.bmc_zone, 100)
                lowest = min(table)
                needed = 0.5 * (100 - lowest) / 100.0
                members = sorted(
                    name for name, top in table[100].items()
                    if top and lowest < 100
                    and (table[lowest].get(name) is None or (top - table[lowest][name]) / top > needed)
                )
                if not members:
                    self.say("warning: no fan followed BMC zone %d (zone %s); leaving its minimum unset"
                             % (zone.bmc_zone, zone.name))
                result[zone.name] = {
                    "bmc_zone": zone.bmc_zone,
                    "fans": members,
                    "min_duty": minimum if members else None,
                    "stopped": stopped if members else None,
                    "table": {
                        step: {name: (None if rpms.get(name) is None else int(round(rpms[name]))) for name in members}
                        for step, rpms in sorted(table.items(), reverse=True)
                    },
                }
                baseline = self._settled()  # back at 100 % before the next zone is touched
        finally:
            try:
                for zone in zones:
                    self.bmc.set_duty(zone.bmc_zone, 100)
            except BmcError:
                pass
            self.session.release()
        return {
            "version": 1,
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "settings": {"step": settings.step, "floor": settings.floor},
            "thresholds": {name: values for name, values in sorted(self.thresholds.items())},
            "zones": result,
        }


def summary(data: dict) -> str:
    lines = []
    for name, zone in data["zones"].items():
        fans = zone["fans"]
        lines.append("Zone %s (BMC zone %d): %s" % (name, zone["bmc_zone"], ", ".join(fans) or "no fans responded"))
        if not fans:
            continue
        lines.append("  %6s" % "duty" + "".join("%9s" % fan for fan in fans))
        for duty, rpms in zone["table"].items():
            cells = "".join("%9s" % ("-" if rpms.get(fan) is None else rpms[fan]) for fan in fans)
            lines.append("  %5d%%%s" % (duty, cells))
        note = " (%s)" % zone["stopped"] if zone["stopped"] else " (reached the floor without trouble)"
        lines.append("  minimum safe duty: %d%%%s" % (zone["min_duty"], note))
    return "\n".join(lines)


def save_calibration(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(HEADER)
        yaml.safe_dump(data, handle, sort_keys=False, default_flow_style=False)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def load_calibration(path: str) -> Dict[str, dict]:
    """Per-zone calibration results, or {} when the machine has not been calibrated."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(["cannot read the calibration (%s); fix or delete it and run `superfan calibrate`"
                           % " ".join(str(exc).split())], path) from exc
    zones = data.get("zones") if isinstance(data, dict) else None
    if not isinstance(zones, dict):
        raise ConfigError(["not a superfan calibration file; delete it and run `superfan calibrate`"], path)
    for name, zone in zones.items():
        ok = isinstance(zone, dict) and isinstance(zone.get("bmc_zone"), int)
        minimum = zone.get("min_duty") if ok else None
        ok = ok and (minimum is None or (isinstance(minimum, int) and 0 <= minimum <= 100))
        ok = ok and isinstance(zone.get("fans", []), list)
        if not ok:
            raise ConfigError(["zone %r is malformed; delete the file and run `superfan calibrate`" % name], path)
    return zones
