"""Temperature sources: the BMC, the kernel's hwmon tree, and arbitrary commands.

Every reading gets a qualified name:

    ipmi:CPU Temp                       a BMC sensor
    hwmon:k10temp@0000:00:18.3/Tctl     chip @ device / label
    command:gpu0                        a command defined in the config

A hwmon pattern may leave out `@device` to match every device of that chip,
so `hwmon:nvme/Composite` covers all NVMe drives.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Collection, Dict, List, Optional, Sequence, Tuple

from .bmc import Bmc, BmcError
from .config import Config

COMMAND_TIMEOUT = 10.0
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_TEMP_INPUT = re.compile(r"^temp(\d+)_input$")


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class Reading:
    name: str
    value: float
    aliases: Tuple[str, ...] = ()

    def matches(self, pattern: str) -> bool:
        wanted = pattern.lower()
        return any(fnmatchcase(name.lower(), wanted) for name in (self.name, *self.aliases))


@dataclass(frozen=True)
class Fan:
    name: str
    rpm: Optional[float]
    status: str


@dataclass
class Snapshot:
    temps: List[Reading] = field(default_factory=list)
    fans: List[Fan] = field(default_factory=list)
    bmc_critical: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)

    def matching(self, patterns: Sequence[str]) -> List[Reading]:
        return [r for r in self.temps if any(r.matches(p) for p in patterns)]


def plausible(value: float) -> bool:
    """Reject the garbage unconnected or sleeping sensors report."""
    return 0.0 < value < 150.0


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read().strip()


def read_hwmon(root: str) -> List[Reading]:
    if not os.path.isdir(root):
        raise SourceError("%s does not exist" % root)
    readings = []
    for directory in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        try:
            chip = _read(os.path.join(directory, "name")) or os.path.basename(directory)
        except OSError:
            chip = os.path.basename(directory)
        device_link = os.path.join(directory, "device")
        device = os.path.basename(os.path.realpath(device_link if os.path.exists(device_link) else directory))
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for entry in entries:
            match = _TEMP_INPUT.match(entry)
            if not match:
                continue
            try:
                value = int(_read(os.path.join(directory, entry))) / 1000.0
            except (OSError, ValueError):
                continue  # a drive that is asleep, a sensor that is not wired up
            if not plausible(value):
                continue
            label = "temp" + match.group(1)
            try:
                label = _read(os.path.join(directory, "temp%s_label" % match.group(1))) or label
            except OSError:
                pass
            readings.append(Reading(
                "hwmon:%s@%s/%s" % (chip, device, label), value, ("hwmon:%s/%s" % (chip, label),)
            ))
    return readings


def read_command(name: str, argv: Sequence[str], timeout: float = COMMAND_TIMEOUT) -> List[Reading]:
    """Run a command that prints one temperature per line (for example nvidia-smi)."""
    try:
        proc = subprocess.run(
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SourceError("%s: command not found" % argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError("timed out after %gs" % timeout) from exc
    except OSError as exc:
        raise SourceError(str(exc)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise SourceError(detail[-1] if detail else "exit status %d" % proc.returncode)
    values = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        match = _NUMBER.search(line)
        if not match:
            raise SourceError("no number in output line %r" % line.strip())
        values.append(float(match.group()))
    if not values:
        raise SourceError("printed nothing")
    base = "command:%s" % name
    if len(values) == 1:
        found = [Reading(base, values[0])]
    else:
        found = [Reading("%s/%d" % (base, i), v, (base,)) for i, v in enumerate(values)]
    return [r for r in found if plausible(r.value)]


def collect(cfg: Config, bmc: Bmc, known_fans: Collection[str] = ()) -> Snapshot:
    """Read every enabled source once. A failing source never stops the others."""
    snap = Snapshot()
    if cfg.ipmi:
        try:
            rows = bmc.scan()
        except BmcError as exc:
            snap.errors["ipmi"] = str(exc)
        else:
            for row in rows:
                if row.unit == "C" and row.value is not None:
                    if plausible(row.value):
                        snap.temps.append(Reading("ipmi:" + row.name, row.value))
                    if row.status in ("cr", "nr"):
                        snap.bmc_critical.append(row.name)
                elif row.unit == "RPM":
                    snap.fans.append(Fan(row.name, row.value, row.status))
                elif row.value is None and row.name in known_fans:
                    snap.fans.append(Fan(row.name, None, row.status))
    if cfg.hwmon:
        try:
            snap.temps.extend(read_hwmon(cfg.hwmon_root))
        except SourceError as exc:
            snap.errors["hwmon"] = str(exc)
    for name, argv in cfg.commands.items():
        try:
            snap.temps.extend(read_command(name, argv))
        except SourceError as exc:
            snap.errors["command:" + name] = str(exc)
    return snap
