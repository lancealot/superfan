"""Everything superfan says to the BMC goes through this module.

Supermicro OEM commands used (X10 and later, H11/H12):

    raw 0x30 0x45 0x00                      get fan mode
    raw 0x30 0x45 0x01 <mode>               set fan mode
    raw 0x30 0x70 0x66 0x01 <zone> <duty>   set a zone's duty cycle, 0x00-0x64 (0-100 %)
    raw 0x30 0x70 0x66 0x00 <zone>          get a zone's duty cycle

In Full mode the BMC leaves the duty cycle alone, so that is the mode superfan
holds while it is in control. Every other mode means the BMC is in control.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

MODE_STANDARD = 0
MODE_FULL = 1
MODE_OPTIMAL = 2
MODE_HEAVY_IO = 4
MODE_NAMES = {0: "Standard", 1: "Full", 2: "Optimal", 3: "PUE2", 4: "Heavy IO"}
EXIT_MODES = {"standard": MODE_STANDARD, "optimal": MODE_OPTIMAL, "heavy_io": MODE_HEAVY_IO}

_NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(.*)$")


def mode_name(mode: Optional[int]) -> str:
    if mode is None:
        return "unknown"
    return MODE_NAMES.get(mode, "0x%02x" % mode)


class BmcError(Exception):
    """An ipmitool call failed, timed out, or returned something unusable."""


@dataclass(frozen=True)
class SensorRow:
    name: str
    value: Optional[float]
    unit: str  # "C", "RPM" or "" for anything else
    status: str


def parse_sdr(text: str) -> List[SensorRow]:
    """Parse `ipmitool sdr list` output: `name | value unit | status`."""
    rows = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3 or not parts[0]:
            continue
        name, raw, status = parts[0], parts[1], parts[2].lower()
        value, unit = None, ""
        match = _NUMBER.match(raw)
        if match and status not in ("ns", "na"):
            value = float(match.group(1))
            suffix = match.group(2).strip().lower()
            if suffix.startswith("degrees c"):
                unit = "C"
            elif suffix == "rpm":
                unit = "RPM"
        rows.append(SensorRow(name, value, unit, status))
    return rows


def parse_thresholds(text: str) -> Dict[str, Dict[str, float]]:
    """Lower thresholds of every RPM sensor from `ipmitool sensor` output."""
    result: Dict[str, Dict[str, float]] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 7 or parts[2].upper() != "RPM":
            continue
        lower = {}
        for key, raw in zip(("lnr", "lcr", "lnc"), parts[4:7]):
            try:
                lower[key] = float(raw)
            except ValueError:
                continue
        result[parts[0]] = lower
    return result


class Bmc:
    def __init__(
        self,
        command: Sequence[str],
        timeout: float = 30.0,
        retries: int = 2,
        dry_run: bool = False,
        retry_delay: float = 0.5,
    ) -> None:
        self.command = list(command)
        self.timeout = timeout
        self.retries = retries
        self.dry_run = dry_run
        self.retry_delay = retry_delay

    def _run(self, args: Sequence[str]) -> str:
        shown = "ipmitool " + " ".join(args)
        try:
            proc = subprocess.run(
                [*self.command, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise BmcError("%s: command not found" % self.command[0]) from exc
        except subprocess.TimeoutExpired as exc:
            raise BmcError("`%s` timed out after %gs" % (shown, self.timeout)) from exc
        except OSError as exc:
            raise BmcError("`%s` could not run: %s" % (shown, exc)) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            reason = detail[-1] if detail else "exit status %d" % proc.returncode
            raise BmcError("`%s` failed: %s" % (shown, reason))
        return proc.stdout

    def _raw(self, *data: int, write: bool = False) -> List[int]:
        args = ["raw"] + ["0x%02x" % byte for byte in data]
        if write and self.dry_run:
            log.info("dry-run: would send ipmitool %s", " ".join(args))
            return []
        for attempt in range(self.retries + 1):
            try:
                out = self._run(args)
                break
            except BmcError as exc:
                if attempt == self.retries:
                    raise
                log.debug("retrying after: %s", exc)
                time.sleep(self.retry_delay)
        try:
            return [int(token, 16) for token in out.split()]
        except ValueError as exc:
            raise BmcError("unexpected reply to `ipmitool %s`: %r" % (" ".join(args), out)) from exc

    def get_mode(self) -> int:
        reply = self._raw(0x30, 0x45, 0x00)
        if not reply:
            raise BmcError("empty reply when reading the fan mode")
        return reply[0]

    def set_mode(self, mode: int) -> None:
        """Set the fan mode and read it back to make sure it took."""
        self._raw(0x30, 0x45, 0x01, mode, write=True)
        if self.dry_run:
            return
        actual = self.get_mode()
        if actual != mode:
            raise BmcError("asked for %s mode but the BMC reports %s" % (mode_name(mode), mode_name(actual)))

    def set_duty(self, zone: int, duty: int) -> None:
        """Set a zone's duty cycle in percent. Only meaningful in Full mode."""
        if isinstance(duty, bool) or not isinstance(duty, int) or not 0 <= duty <= 100:
            raise ValueError("duty must be an integer from 0 to 100, got %r" % (duty,))
        if isinstance(zone, bool) or not isinstance(zone, int) or not 0 <= zone <= 0xFF:
            raise ValueError("zone must be an integer from 0 to 255, got %r" % (zone,))
        self._raw(0x30, 0x70, 0x66, 0x01, zone, duty, write=True)

    def get_duty(self, zone: int) -> int:
        reply = self._raw(0x30, 0x70, 0x66, 0x00, zone)
        if not reply:
            raise BmcError("empty reply when reading the duty of zone %d" % zone)
        return reply[0]

    def scan(self) -> List[SensorRow]:
        """One pass over the BMC's analog sensors (temperatures, fans, voltages)."""
        return parse_sdr(self._run(["sdr", "list", "full"]))

    def thresholds(self) -> Dict[str, Dict[str, float]]:
        return parse_thresholds(self._run(["sensor"]))
