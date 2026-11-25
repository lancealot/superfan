"""Configuration loading and validation.

Validation is strict: an unknown key is an error, so a typo cannot silently
turn a setting off.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .curve import Point

DEFAULT_CONFIG_PATH = "/etc/superfan/config.yaml"
DEFAULT_STATUS_FILE = "/run/superfan/status.json"
SOURCES = ("ipmi", "hwmon", "command")
EXIT_MODE_CHOICES = ("previous", "standard", "optimal", "heavy_io")

_LEGACY_KEYS = {"fans", "temperature", "ipmi", "logging"}
_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_GLOB = re.compile(r"[*?\[]")


class ConfigError(Exception):
    def __init__(self, problems: List[str], path: Optional[str] = None) -> None:
        self.problems = list(problems)
        self.path = path
        super().__init__((path + ": " if path else "") + "; ".join(self.problems))


@dataclass
class InputCfg:
    sensors: List[str]
    curve: List[Point]
    critical: Optional[float] = None
    required: bool = True

    @property
    def label(self) -> str:
        return ", ".join(self.sensors)


@dataclass
class ZoneCfg:
    name: str
    bmc_zone: int
    inputs: List[InputCfg]
    min_duty: int = 0
    hysteresis: float = 3.0


@dataclass
class BmcCfg:
    command: List[str] = field(default_factory=lambda: ["ipmitool"])
    timeout: float = 30.0
    retries: int = 2
    exit_mode: str = "previous"


@dataclass
class Config:
    path: str = DEFAULT_CONFIG_PATH
    interval: float = 10.0
    hysteresis: float = 3.0
    bmc: BmcCfg = field(default_factory=BmcCfg)
    ipmi: bool = True
    hwmon: bool = True
    hwmon_root: str = "/sys/class/hwmon"
    commands: Dict[str, List[str]] = field(default_factory=dict)
    zones: Dict[str, ZoneCfg] = field(default_factory=dict)
    failsafe_duty: int = 100
    max_failures: int = 3
    fault_hold: float = 60.0
    status_file: str = DEFAULT_STATUS_FILE
    calibration_file: str = "/etc/superfan/calibration.yaml"

    @property
    def state_dir(self) -> str:
        return os.path.dirname(self.status_file) or "."


def _at(where: str) -> str:
    return where + ": " if where else ""


def _unknown(data: dict, allowed: set, where: str, problems: List[str]) -> None:
    for key in data:
        if key not in allowed:
            problems.append("%sunknown key %r (expected one of: %s)" % (_at(where), key, ", ".join(sorted(allowed))))


def _section(data: dict, key: str, problems: List[str]) -> dict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.append("%s must be a mapping" % key)
        return {}
    return value


def _number(data, key, default, problems, where, lo=None, hi=None, integer=False):
    value = data.get(key)
    if value is None:
        return default
    kind = "a whole number" if integer else "a number"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and float(value) != int(value)):
        problems.append("%s%s must be %s" % (_at(where), key, kind))
        return default
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        limits = "at least %g" % lo if hi is None else "between %g and %g" % (lo, hi)
        problems.append("%s%s must be %s (got %g)" % (_at(where), key, limits, value))
        return default
    return int(value) if integer else float(value)


def _argv(value: Any, where: str, problems: List[str]) -> List[str]:
    if isinstance(value, str) and value.strip():
        try:
            return shlex.split(value)
        except ValueError as exc:
            problems.append("%s: cannot parse command (%s)" % (where, exc))
            return []
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return list(value)
    problems.append("%s: must be a command line (a string or a list of strings)" % where)
    return []


def _curve(raw: Any, where: str, problems: List[str]) -> List[Point]:
    fallback = [(0.0, 100.0)]
    if not isinstance(raw, list) or not raw:
        problems.append("%s: curve must be a list of [temperature, duty] points" % where)
        return fallback
    points: List[Point] = []
    for point in raw:
        ok = isinstance(point, (list, tuple)) and len(point) == 2
        ok = ok and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in point)
        if not ok:
            problems.append("%s: curve point %r must be [temperature, duty]" % (where, point))
            return fallback
        points.append((float(point[0]), float(point[1])))
    for temp, duty in points:
        if not 0 <= duty <= 100:
            problems.append("%s: curve duty %g is outside 0-100" % (where, duty))
    for (t1, d1), (t2, d2) in zip(points, points[1:]):
        if t2 <= t1:
            problems.append("%s: curve temperatures must increase (%g then %g)" % (where, t1, t2))
        if d2 < d1:
            problems.append("%s: curve duty must not fall as temperature rises (%g%% then %g%%)" % (where, d1, d2))
    return points


def _pattern(pattern: str, where: str, cfg: Config, problems: List[str]) -> None:
    source, sep, rest = pattern.partition(":")
    if not sep or source not in SOURCES or not rest:
        problems.append("%s: sensor pattern %r must start with ipmi:, hwmon: or command:" % (where, pattern))
        return
    if (source == "ipmi" and not cfg.ipmi) or (source == "hwmon" and not cfg.hwmon):
        problems.append("%s: %r uses the %s source, which is turned off under sources" % (where, pattern, source))
    if source == "command":
        name = rest.split("/")[0]
        if not _GLOB.search(name) and name not in cfg.commands:
            problems.append("%s: %r refers to command %r, which is not defined under sources.command"
                            % (where, pattern, name))


def _input(raw: Any, where: str, cfg: Config, problems: List[str]) -> Optional[InputCfg]:
    if not isinstance(raw, dict):
        problems.append("%s: must be a mapping with 'sensors' and 'curve'" % where)
        return None
    _unknown(raw, {"sensors", "curve", "critical", "required"}, where, problems)
    sensors = raw.get("sensors")
    if isinstance(sensors, str):
        sensors = [sensors]
    if not isinstance(sensors, list) or not sensors or not all(isinstance(s, str) and s for s in sensors):
        problems.append("%s: sensors must be a pattern or a non-empty list of patterns" % where)
        sensors = []
    for pattern in sensors:
        _pattern(pattern, where, cfg, problems)
    required = raw.get("required", True)
    if not isinstance(required, bool):
        problems.append("%s: required must be true or false" % where)
        required = True
    return InputCfg(
        sensors=list(sensors),
        curve=_curve(raw.get("curve"), where, problems),
        critical=_number(raw, "critical", None, problems, where, lo=1, hi=150),
        required=required,
    )


def parse_config(data: Any, path: str = "<config>") -> Config:
    if not isinstance(data, dict):
        raise ConfigError(["the file must contain a mapping of settings"], path)
    legacy = sorted(_LEGACY_KEYS & set(data))
    if legacy:
        raise ConfigError(
            ["found %s: this is a superfan 0.1 config, and the format changed in 0.2 "
             "(see 'Configuration' in the README)" % ", ".join(repr(k) for k in legacy)],
            path,
        )
    problems: List[str] = []
    _unknown(data, {"interval", "hysteresis", "bmc", "sources", "zones", "safety",
                    "status_file", "calibration_file"}, "", problems)
    cfg = Config(path=path)
    cfg.interval = _number(data, "interval", 10.0, problems, "", lo=1)
    cfg.hysteresis = _number(data, "hysteresis", 3.0, problems, "", lo=0)

    bmc = _section(data, "bmc", problems)
    _unknown(bmc, {"command", "timeout", "retries", "exit_mode"}, "bmc", problems)
    if bmc.get("command") is not None:
        cfg.bmc.command = _argv(bmc["command"], "bmc.command", problems) or ["ipmitool"]
    cfg.bmc.timeout = _number(bmc, "timeout", 30.0, problems, "bmc", lo=0.1)
    cfg.bmc.retries = _number(bmc, "retries", 2, problems, "bmc", lo=0, hi=10, integer=True)
    exit_mode = bmc.get("exit_mode", "previous")
    if exit_mode not in EXIT_MODE_CHOICES:
        problems.append("bmc: exit_mode must be one of: %s" % ", ".join(EXIT_MODE_CHOICES))
        exit_mode = "previous"
    cfg.bmc.exit_mode = exit_mode

    sources = _section(data, "sources", problems)
    _unknown(sources, set(SOURCES), "sources", problems)
    ipmi = sources.get("ipmi", True)
    if not isinstance(ipmi, bool):
        problems.append("sources: ipmi must be true or false")
        ipmi = True
    cfg.ipmi = ipmi
    hwmon = sources.get("hwmon", True)
    if isinstance(hwmon, dict):
        _unknown(hwmon, {"root"}, "sources.hwmon", problems)
        if isinstance(hwmon.get("root"), str) and hwmon["root"]:
            cfg.hwmon_root = hwmon["root"]
        elif "root" in hwmon:
            problems.append("sources.hwmon: root must be a directory path")
        cfg.hwmon = True
    elif isinstance(hwmon, bool):
        cfg.hwmon = hwmon
    else:
        problems.append("sources: hwmon must be true, false, or a mapping with 'root'")
    commands = sources.get("command") or {}
    if not isinstance(commands, dict):
        problems.append("sources: command must map a name to a command line")
        commands = {}
    for name, value in commands.items():
        where = "sources.command.%s" % name
        if not isinstance(name, str) or not _NAME.match(name):
            problems.append("%s: names may contain only letters, digits, '_', '.' and '-'" % where)
            continue
        cfg.commands[name] = _argv(value, where, problems)

    zones = data.get("zones")
    if not isinstance(zones, dict) or not zones:
        problems.append("zones: define at least one zone")
        zones = {}
    used: Dict[int, str] = {}
    for name, raw in zones.items():
        where = "zones.%s" % name
        if not isinstance(name, str) or not _NAME.match(name):
            problems.append("%s: zone names may contain only letters, digits, '_', '.' and '-'" % where)
            continue
        if not isinstance(raw, dict):
            problems.append("%s: must be a mapping" % where)
            continue
        _unknown(raw, {"bmc_zone", "inputs", "min_duty", "hysteresis"}, where, problems)
        bmc_zone = _number(raw, "bmc_zone", None, problems, where, lo=0, hi=255, integer=True)
        if "bmc_zone" not in raw:
            problems.append("%s: bmc_zone is required (on most boards 0 drives FAN1-FANn, 1 drives FANA-FANx)"
                            % where)
        elif bmc_zone is not None:
            if bmc_zone in used:
                problems.append("%s: bmc_zone %d is already used by zone %r" % (where, bmc_zone, used[bmc_zone]))
            used.setdefault(bmc_zone, name)
        inputs_raw = raw.get("inputs")
        inputs: List[InputCfg] = []
        if not isinstance(inputs_raw, list) or not inputs_raw:
            problems.append("%s: inputs must be a non-empty list" % where)
        else:
            for index, item in enumerate(inputs_raw):
                parsed = _input(item, "%s.inputs[%d]" % (where, index), cfg, problems)
                if parsed is not None:
                    inputs.append(parsed)
        cfg.zones[name] = ZoneCfg(
            name=name,
            bmc_zone=bmc_zone if bmc_zone is not None else 0,
            inputs=inputs,
            min_duty=_number(raw, "min_duty", 0, problems, where, lo=0, hi=100, integer=True),
            hysteresis=_number(raw, "hysteresis", cfg.hysteresis, problems, where, lo=0),
        )

    safety = _section(data, "safety", problems)
    _unknown(safety, {"failsafe_duty", "max_failures", "fault_hold"}, "safety", problems)
    cfg.failsafe_duty = _number(safety, "failsafe_duty", 100, problems, "safety", lo=1, hi=100, integer=True)
    cfg.max_failures = _number(safety, "max_failures", 3, problems, "safety", lo=1, integer=True)
    cfg.fault_hold = _number(safety, "fault_hold", 60.0, problems, "safety", lo=0)

    for key, default in (
        ("status_file", DEFAULT_STATUS_FILE),
        ("calibration_file", os.path.join(os.path.dirname(os.path.abspath(path)), "calibration.yaml")),
    ):
        value = data.get(key, default)
        if not isinstance(value, str) or not value:
            problems.append("%s must be a file path" % key)
            value = default
        setattr(cfg, key, value)

    if problems:
        raise ConfigError(problems, path)
    return cfg


def load_config(path: str) -> Config:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ConfigError(["cannot read the file (%s)" % exc.strerror], path) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(["not valid YAML (%s)" % " ".join(str(exc).split())], path) from exc
    cfg = parse_config(data, path)
    check_command_permissions(cfg)
    return cfg


def check_command_permissions(cfg: Config) -> None:
    """Refuse to run configured commands as root from a file others can edit."""
    if not cfg.commands or os.geteuid() != 0:
        return
    try:
        info = os.stat(cfg.path)
    except OSError:
        return
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ConfigError(
            ["it defines commands that would run as root, so it must be owned by root "
             "and not writable by group or others (chown root: and chmod 644)"],
            cfg.path,
        )
