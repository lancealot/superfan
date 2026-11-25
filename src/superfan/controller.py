"""The control loop: read every source once, decide each zone's duty, send it.

Failure rules, in order of precedence:

* a critical temperature, or a fan that stopped or fell below the BMC's
  critical threshold, puts every zone at the failsafe duty and holds it there
  for `safety.fault_hold` seconds after the condition clears;
* a zone with a required input that has no reading runs at the failsafe duty;
* if the BMC cannot be commanded for `safety.max_failures` cycles in a row
  the loop gives up, and fan control goes back to the BMC on the way out.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from . import __version__
from .bmc import MODE_FULL, Bmc, BmcError, mode_name
from .config import Config, InputCfg
from .curve import Hysteresis, interpolate
from .session import Session
from .sources import Snapshot, collect
from .status import write_status

log = logging.getLogger(__name__)

Reason = Tuple[str, str]  # (stable key for change detection, text for people)


class FatalBmcError(Exception):
    """The BMC stopped accepting commands; nothing more can be done from here."""


@dataclass
class ZonePlan:
    name: str
    bmc_zone: int
    duty: int
    min_duty: int
    demand: Optional[float] = None
    driver: Optional[str] = None
    driver_temp: Optional[float] = None
    reason: Optional[str] = None


@dataclass
class Plan:
    zones: List[ZonePlan]
    state: str = "normal"
    reasons: List[Reason] = field(default_factory=list)
    sensors: List[dict] = field(default_factory=list)


def sensor_level(inp: InputCfg, value: float) -> str:
    """Where a reading sits on its curve: ok (below it), warm (on it), hot (at the top)."""
    if inp.critical is not None and value >= inp.critical:
        return "hot"
    if len(inp.curve) < 2 or value <= inp.curve[0][0]:
        return "ok"
    return "hot" if value >= inp.curve[-1][0] else "warm"


class Controller:
    def __init__(
        self,
        cfg: Config,
        bmc: Bmc,
        calibration: Optional[Dict[str, dict]] = None,
        clock: Callable[[], float] = time.monotonic,
        notify: Callable[[str], object] = lambda message: None,
        reassert_every: int = 6,
    ) -> None:
        self.cfg = cfg
        self.bmc = bmc
        self.clock = clock
        self.notify = notify
        self.reassert_every = reassert_every
        self.session = Session(bmc, cfg.state_dir, cfg.bmc.exit_mode)
        self.stop: Optional[threading.Event] = None
        self.min_duty: Dict[str, int] = {}
        self.fan_zone: Dict[str, str] = {}
        self.calibrated: Set[str] = set()
        self.last_status: Optional[dict] = None
        for zone in cfg.zones.values():
            learned = (calibration or {}).get(zone.name)
            floor = zone.min_duty
            if learned and learned.get("bmc_zone") != zone.bmc_zone:
                log.warning("calibration for zone %s was made for BMC zone %s, not %s; ignoring it",
                            zone.name, learned.get("bmc_zone"), zone.bmc_zone)
            elif learned:
                self.calibrated.add(zone.name)
                floor = max(floor, learned.get("min_duty") or 0)
                for fan in learned.get("fans") or []:
                    self.fan_zone[fan] = zone.name
            self.min_duty[zone.name] = floor
        self.hysteresis = {
            (zone.name, index): Hysteresis(zone.hysteresis)
            for zone in cfg.zones.values() for index in range(len(zone.inputs))
        }
        self.sent: Dict[str, int] = {}
        self.spinning: Set[str] = set()
        self.hold: Optional[Tuple[float, str, List[Reason]]] = None
        self.write_failures = 0
        self.cycles = 0
        self.bmc_mode: Optional[int] = None
        self._signature: Optional[tuple] = None

    # -- deciding -----------------------------------------------------------

    def decide(self, snap: Snapshot) -> Plan:
        plan = Plan(zones=[])
        emergencies: List[Reason] = [
            ("bmc:" + name, "the BMC reports %s as critical" % name) for name in snap.bmc_critical
        ]
        for zone in self.cfg.zones.values():
            zp = ZonePlan(zone.name, zone.bmc_zone, 0, self.min_duty[zone.name])
            missing = []
            for index, inp in enumerate(zone.inputs):
                matched = snap.matching(inp.sensors)
                if not matched:
                    if inp.required:
                        missing.append(inp.label)
                    continue
                for reading in matched:
                    plan.sensors.append({"name": reading.name, "value": reading.value,
                                         "zone": zone.name, "level": sensor_level(inp, reading.value)})
                    if inp.critical is not None and reading.value >= inp.critical:
                        emergencies.append(("critical:" + reading.name, "%s is at %.1f°C (critical is %g°C)"
                                            % (reading.name, reading.value, inp.critical)))
                hottest = max(matched, key=lambda r: r.value)
                wanted = interpolate(inp.curve, self.hysteresis[(zone.name, index)].update(hottest.value))
                if zp.demand is None or wanted > zp.demand:
                    zp.demand, zp.driver, zp.driver_temp = wanted, hottest.name, hottest.value
            if missing:
                zp.reason = "no reading for " + "; ".join(missing)
            elif zp.demand is None:
                zp.reason = "none of its sensors has a reading"
            if zp.reason:
                zp.duty = self.cfg.failsafe_duty
                plan.reasons.append(("missing:" + zone.name, "zone %s: %s" % (zone.name, zp.reason)))
            else:
                zp.duty = min(100, max(zp.min_duty, int(math.ceil(zp.demand - 1e-9))))
            plan.zones.append(zp)

        faults: List[Reason] = []
        for fan in snap.fans:
            if fan.rpm is not None and fan.rpm > 0:
                self.spinning.add(fan.name)
                if fan.status in ("cr", "nr"):
                    faults.append(("fan:" + fan.name, "fan %s is below the BMC's critical threshold (%d RPM)"
                                   % (fan.name, fan.rpm)))
            elif fan.name in self.spinning or fan.status in ("cr", "nr"):
                faults.append(("fan:" + fan.name, "fan %s has stopped" % fan.name))

        now = self.clock()
        override: Optional[Tuple[str, List[Reason]]] = None
        if emergencies or faults:
            override = ("emergency" if emergencies else "failsafe", emergencies + faults)
            self.hold = (now + self.cfg.fault_hold, override[0], override[1])
        elif self.hold and now < self.hold[0]:
            held = [(key, text + " (cleared; holding full speed)") for key, text in self.hold[2]]
            override = (self.hold[1], held)
        else:
            self.hold = None
        if override:
            plan.state = override[0]
            plan.reasons = override[1] + plan.reasons
            for zp in plan.zones:
                zp.duty = max(zp.duty, self.cfg.failsafe_duty)
                zp.reason = zp.reason or plan.state
        elif plan.reasons:
            plan.state = "failsafe"
        return plan

    # -- acting -------------------------------------------------------------

    def apply(self, plan: Plan) -> None:
        try:
            if not self.bmc.dry_run:
                mode = self.bmc.get_mode()
                if mode != MODE_FULL:
                    log.warning("the BMC left Full mode (now %s); taking control again", mode_name(mode))
                    self.session.takeover()
                    self.sent.clear()
                self.bmc_mode = MODE_FULL
            reassert = not self.bmc.dry_run and self.cycles % self.reassert_every == 0
            for zp in plan.zones:
                previous = self.sent.get(zp.name)
                if reassert or previous != zp.duty:
                    self.bmc.set_duty(zp.bmc_zone, zp.duty)
                    self.sent[zp.name] = zp.duty
                    if previous != zp.duty:
                        cause = zp.reason or "%s at %.1f°C" % (zp.driver, zp.driver_temp)
                        log.info("zone %s: duty %s -> %d%% (%s)", zp.name,
                                 "unset" if previous is None else "%d%%" % previous, zp.duty, cause)
            self.write_failures = 0
        except BmcError as exc:
            self.sent.clear()
            self.write_failures += 1
            plan.reasons.append(("bmc", "cannot command the BMC: %s" % exc))
            if plan.state == "normal":
                plan.state = "failsafe"
            if self.write_failures >= self.cfg.max_failures:
                raise FatalBmcError("the BMC rejected commands %d cycles in a row: %s"
                                    % (self.write_failures, exc)) from exc

    def _log_transition(self, plan: Plan) -> None:
        signature = (plan.state, tuple(sorted(key for key, _ in plan.reasons)))
        if signature == self._signature:
            return
        previous, self._signature = self._signature, signature
        text = "; ".join(text for _, text in plan.reasons)
        if plan.state == "emergency":
            log.error("EMERGENCY: %s", text)
        elif plan.state == "failsafe":
            log.warning("failsafe: %s", text)
        elif previous is not None:
            log.info("back to normal")

    def status(self, snap: Snapshot, plan: Plan, running: bool = True) -> dict:
        return {
            "version": __version__,
            "pid": os.getpid(),
            "time": time.time(),
            "interval": self.cfg.interval,
            "running": running,
            "dry_run": self.bmc.dry_run,
            "state": plan.state,
            "reasons": [text for _, text in plan.reasons],
            "bmc_mode": mode_name(self.bmc_mode),
            "zones": [
                {"name": zp.name, "bmc_zone": zp.bmc_zone, "duty": zp.duty, "min_duty": zp.min_duty,
                 "demand": None if zp.demand is None else round(zp.demand, 1),
                 "driver": zp.driver, "driver_temp": zp.driver_temp, "reason": zp.reason}
                for zp in plan.zones
            ],
            "sensors": plan.sensors,
            "fans": [{"name": fan.name, "rpm": fan.rpm, "status": fan.status,
                      "zone": self.fan_zone.get(fan.name)} for fan in snap.fans],
            "errors": dict(snap.errors),
            "calibrated": sorted(self.calibrated),
        }

    def cycle(self) -> Optional[dict]:
        snap = collect(self.cfg, self.bmc, self.spinning)
        self.notify("WATCHDOG=1")
        if self.stop is not None and self.stop.is_set():
            return None
        plan = self.decide(snap)
        try:
            self.apply(plan)
        finally:
            self._log_transition(plan)
            status = self.status(snap, plan)
            self._publish(status)
            self.cycles += 1
        return status

    def _publish(self, status: dict) -> None:
        self.last_status = status
        if self.bmc.dry_run:
            for zone in status["zones"]:
                cause = zone["reason"] or "%s at %.1f°C" % (zone["driver"], zone["driver_temp"])
                log.info("dry-run: zone %s would run at %d%% (%s)", zone["name"], zone["duty"], cause)
            return
        try:
            write_status(self.cfg.status_file, status)
        except OSError as exc:
            log.warning("cannot write %s: %s", self.cfg.status_file, exc)

    # -- running ------------------------------------------------------------

    def run(self, stop: threading.Event, once: bool = False) -> int:
        """Take control, loop until `stop` is set, and always hand control back."""
        self.stop = stop
        code = 0
        try:
            found = self.session.takeover()
            self.bmc_mode = found if self.bmc.dry_run else MODE_FULL
            log.info("%s (BMC was in %s mode); %d zone(s), every %gs",
                     "dry run, not touching the fans" if self.bmc.dry_run else "took fan control",
                     mode_name(found), len(self.cfg.zones), self.cfg.interval)
            self.notify("READY=1")
            while not stop.is_set():
                started = self.clock()
                self.cycle()
                self.notify("WATCHDOG=1")
                if once:
                    break
                stop.wait(max(0.0, self.cfg.interval - (self.clock() - started)))
        except (BmcError, FatalBmcError) as exc:
            log.error("%s", exc)
            code = 1
        finally:
            self.notify("STOPPING=1")
            released = self.session.release()
            if not released:
                code = 1
            if self.last_status is not None and not self.bmc.dry_run:
                self.last_status.update(running=False, time=time.time(),
                                        bmc_mode="BMC control" if released else "unknown")
                try:
                    write_status(self.cfg.status_file, self.last_status)
                except OSError:
                    pass
        return code
