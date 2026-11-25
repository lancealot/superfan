"""The status file the daemon publishes, and how it is laid out for people.

`build_frame` turns a status dict into rows of (text, style) spans. The plain
`superfan status` output and the full-screen dashboard both draw that frame,
so neither ever talks to the BMC.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

Span = Tuple[str, str]
Frame = List[List[Span]]
WIDTH = 78


class StatusError(Exception):
    pass


def write_status(path: str, data: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def read_status(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise StatusError("%s does not exist; the daemon has not run since boot" % path) from exc
    except (OSError, ValueError) as exc:
        raise StatusError("cannot read %s: %s" % (path, exc)) from exc
    if not isinstance(data, dict) or "time" not in data:
        raise StatusError("%s is not a superfan status file" % path)
    return data


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def liveness(status: dict, now: float, alive: Optional[bool] = None) -> str:
    """running, stopped (clean exit), dead (process gone) or stale (not updating)."""
    if not status.get("running"):
        return "stopped"
    if alive is None:
        alive = pid_alive(status.get("pid"))
    if not alive:
        return "dead"
    if now - status["time"] > max(3 * float(status.get("interval", 10)), 90):
        return "stale"
    return "running"


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return "%ds ago" % seconds
    if seconds < 7200:
        return "%dm ago" % (seconds // 60)
    return "%dh ago" % (seconds // 3600)


def build_frame(status: dict, now: float, alive: Optional[bool] = None, footer: Optional[str] = None) -> Frame:
    live = liveness(status, now, alive)
    rows: Frame = []
    title: List[Span] = [("Superfan Monitor", "title"), ("  v%s" % status.get("version", "?"), "dim")]
    if status.get("dry_run"):
        title.append(("  DRY RUN", "warn"))
    rows.append(title)
    rows.append([("=" * WIDTH, "dim")])

    state = status.get("state", "unknown")
    if live == "running":
        label, style = {"normal": ("Normal", "ok"), "failsafe": ("FAILSAFE", "warn"),
                        "emergency": ("EMERGENCY", "bad")}.get(state, (state.upper(), "warn"))
    else:
        label, style = {"stopped": "STOPPED", "dead": "NOT RUNNING", "stale": "NOT UPDATING"}[live], "bad"
    rows.append([("Status: ", "bold"), (label, style),
                 ("    Fan control: %s" % status.get("bmc_mode", "unknown"), ""),
                 ("    Updated: ", ""), (_age(now - status["time"]), "" if live == "running" else "bad")])
    if live != "running":
        rows.append([("  ! the daemon is not running; the values below are its last report", "bad")])
    for reason in status.get("reasons", []):
        rows.append([("  ! " + reason, "bad" if state == "emergency" else "warn")])
    for source, error in sorted(status.get("errors", {}).items()):
        rows.append([("  ! source %s: %s" % (source, error), "warn")])

    rows.append([])
    rows.append([("%-16s%6s%8s%6s   %s" % ("Fan zones", "Duty", "Demand", "Min", "Driven by"), "bold")])
    for zone in status.get("zones", []):
        demand = "-" if zone.get("demand") is None else "%d%%" % round(zone["demand"])
        if zone.get("reason"):
            driver, style = zone["reason"], "warn"
        else:
            driver, style = "%s (%.1f°C)" % (zone.get("driver"), zone.get("driver_temp") or 0), ""
        rows.append([("  %-14s" % zone["name"], ""), ("%5d%%" % zone["duty"], "bold"),
                     ("%8s%5d%%   " % (demand, zone.get("min_duty", 0)), ""), (driver, style)])

    fans = status.get("fans", [])
    if fans:
        rows.append([])
        rows.append([("%-16s%10s   %-8s%s" % ("Fans", "RPM", "Status", "Zone"), "bold")])
        for fan in fans:
            rpm = fan.get("rpm")
            state_text = fan.get("status") or "?"
            style = "ok" if state_text == "ok" and rpm else ("warn" if state_text == "nc" and rpm else "bad")
            rows.append([("  %-14s" % fan["name"], ""),
                         ("%10s" % ("no reading" if rpm is None else "%d" % rpm), style),
                         ("   %-8s" % state_text, style), (fan.get("zone") or "", "dim")])

    sensors = status.get("sensors", [])
    if sensors:
        rows.append([])
        width = min(46, max(len(s["name"]) for s in sensors))
        rows.append([("%-*s%10s   %s" % (width + 2, "Temperatures", "Now", "Zone"), "bold")])
        styles = {"ok": "ok", "warm": "warn", "hot": "bad"}
        for sensor in sorted(sensors, key=lambda s: (s["zone"], s["name"])):
            rows.append([("  %-*s" % (width, sensor["name"][:width]), ""),
                         ("%8.1f°C" % sensor["value"], styles.get(sensor.get("level"), "")),
                         ("   %s" % sensor["zone"], "dim")])

    rows.append([("=" * WIDTH, "dim")])
    if footer:
        rows.append([(footer, "dim")])
    return rows


def message_frame(message: str, footer: Optional[str] = None) -> Frame:
    rows: Frame = [[("Superfan Monitor", "title")], [("=" * WIDTH, "dim")], [(message, "bad")],
                   [("=" * WIDTH, "dim")]]
    if footer:
        rows.append([(footer, "dim")])
    return rows


def load_frame(path: str, now: float, footer: Optional[str] = None) -> Tuple[Frame, str]:
    """Read the status file and lay it out. Returns the frame and running/stopped/dead/stale/missing."""
    try:
        status = read_status(path)
    except StatusError as exc:
        return message_frame(str(exc), footer), "missing"
    return build_frame(status, now, footer=footer), liveness(status, now)


def frame_text(frame: Frame) -> str:
    return "\n".join("".join(text for text, _ in row).rstrip() for row in frame)
