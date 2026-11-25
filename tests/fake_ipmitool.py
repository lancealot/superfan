#!/usr/bin/env python3
"""A simulated Supermicro BMC behind an ipmitool-shaped command line.

Test double only. State lives in a JSON file so that separate invocations (and
the tests) see the same BMC. It models the documented behaviour the real code
relies on, and nothing more:

* fan mode get/set; entering Full mode puts every zone at 100 %
* zone duty get/set, rejecting anything above 0x64 or malformed
* fans whose RPM follows their zone's duty in Full mode and the BMC's own
  choice (`auto_duty`) otherwise
* `sdr list` and `sensor` output in ipmitool's formats
* injected failures, hangs and heating, and a log of every command received

Usage: fake_ipmitool.py --state STATE.json <ipmitool arguments...>
"""

import fcntl
import json
import sys
import time

VALID_MODES = (0, 1, 2, 4)
DEFAULT_THRESHOLDS = [100, 200, 300]  # lower non-recoverable, lower critical, lower non-critical


def zone_duty(state, zone):
    if state["mode"] == 1:
        return state["duty"].get(str(zone), 100)
    return state.get("auto_duty", 50)


def advance(state):
    """One sensor scan passes: fans move towards their target and heat rules apply."""
    lag = state.get("lag")
    for fan in state["fans"].values():
        if fan is None:
            continue
        duty = zone_duty(state, fan["zone"])
        target = 0.0 if fan.get("dead") or duty < fan.get("stall_below", 0) else fan["max_rpm"] * duty / 100.0
        current = fan.get("rpm", target)
        fan["rpm"] = current + (target - current) * lag if lag else target
    for rule in state.get("heat", []):
        temp = state["temps"][rule["sensor"]]
        if zone_duty(state, rule["zone"]) < rule["below"]:
            temp += rule["rise"]
        else:
            temp = max(rule["base"], temp - rule.get("cool", rule["rise"]))
        state["temps"][rule["sensor"]] = temp


def fan_status(state, name, rpm):
    lnr, lcr, lnc = state.get("thresholds", {}).get(name, DEFAULT_THRESHOLDS)
    if rpm <= lcr:
        return "cr"
    return "nc" if rpm <= lnc else "ok"


def sdr(state):
    advance(state)
    lines = []
    for name, value in state["temps"].items():
        if value is None:
            lines.append("%-16s | %-17s | ns" % (name, "no reading"))
        else:
            status = state.get("temp_status", {}).get(name, "ok")
            lines.append("%-16s | %-17s | %s" % (name, "%g degrees C" % value, status))
    for name, fan in state["fans"].items():
        if fan is None:
            lines.append("%-16s | %-17s | ns" % (name, "no reading"))
        else:
            rpm = int(round(fan["rpm"]))
            lines.append("%-16s | %-17s | %s" % (name, "%d RPM" % rpm, fan_status(state, name, rpm)))
    lines.extend(state.get("extra_sdr", ["12V              | 12.19 Volts       | ok",
                                         "Chassis Intru    | 0x00              | ok"]))
    return "\n".join(lines) + "\n"


def sensor(state):
    advance(state)
    lines = []
    for name, value in state["temps"].items():
        if value is None:
            lines.append("%-16s | na         | degrees C  | na    | na | na | na | na | na | na" % name)
        else:
            lines.append("%-16s | %-10.3f | degrees C  | ok    | 5.000 | 5.000 | 10.000 | 90.000 | 95.000 | 100.000"
                         % (name, value))
    for name, fan in state["fans"].items():
        if fan is None:
            lines.append("%-16s | na         |            | na    | na | na | na | na | na | na" % name)
        else:
            lnr, lcr, lnc = state.get("thresholds", {}).get(name, DEFAULT_THRESHOLDS)
            rpm = int(round(fan["rpm"]))
            lines.append("%-16s | %-10.3f | RPM        | %-5s | %.3f | %.3f | %.3f | 25300.000 | 25400.000 | 25500.000"
                         % (name, rpm, fan_status(state, name, rpm), lnr, lcr, lnc))
    return "\n".join(lines) + "\n"


def injected(state, kind):
    """Returns (should_fail, seconds_to_hang) for this kind of command."""
    hang = state.get("hang", {})
    seconds = hang.get(kind, 0) or hang.get("all", 0)
    fail = state.setdefault("fail", {})
    for key in (kind, "all"):
        remaining = fail.get(key, 0)
        if remaining:
            if remaining > 0:
                fail[key] = remaining - 1
            return True, seconds
    return False, seconds


def raw(state, data):
    """Returns (kind, exit status, stdout, stderr)."""
    invalid = (1, "", "Unable to send RAW command (rsp=0xcc): Invalid data field in request\n")
    if data[:2] == [0x30, 0x45]:
        if data[2:] == [0x00]:
            return ("mode_get", 0, " %02x\n" % state["mode"], "")
        if len(data) == 4 and data[2] == 0x01:
            if data[3] not in VALID_MODES:
                return ("mode_set",) + invalid
            if not state.get("ignore_mode_set"):
                state["mode"] = data[3]
                if data[3] == 1:
                    state["duty"] = {zone: 100 for zone in state["duty"]}
            return ("mode_set", 0, "\n", "")
    if data[:3] == [0x30, 0x70, 0x66]:
        if len(data) == 6 and data[3] == 0x01:
            zone, duty = data[4], data[5]
            if str(zone) not in state["duty"] or not 0 <= duty <= 0x64:
                return ("duty_set",) + invalid
            state["duty"][str(zone)] = duty
            return ("duty_set", 0, "\n", "")
        if len(data) == 5 and data[3] == 0x00 and str(data[4]) in state["duty"]:
            return ("duty_get", 0, " %02x\n" % state["duty"][str(data[4])], "")
    return ("raw", 1, "", "Unable to send RAW command (rsp=0xc7): Request data length invalid\n")


def handle(state, args):
    """Returns (exit status, stdout, stderr, seconds to hang before answering)."""
    state.setdefault("log", []).append(args)
    busy = "Unable to establish IPMI session: Device or resource busy\n"
    if args[:1] == ["raw"]:
        try:
            data = [int(token, 0) for token in args[1:]]
        except ValueError:
            return 1, "", "Given data is invalid.\n", 0
        probe = dict(state)
        probe["duty"] = dict(state["duty"])
        kind = raw(probe, data)[0]  # classify without side effects, so a failure changes nothing
        fail, hang = injected(state, kind)
        if fail:
            return 1, "", busy, hang
        _, status, out, err = raw(state, data)
        return status, out, err, hang
    if args[:2] == ["sdr", "list"]:
        fail, hang = injected(state, "sdr")
        return (1, "", busy, hang) if fail else (0, sdr(state), "", hang)
    if args == ["sensor"]:
        fail, hang = injected(state, "sensor")
        return (1, "", busy, hang) if fail else (0, sensor(state), "", hang)
    return 1, "", "Invalid command: %s\n" % " ".join(args), 0


def main(argv):
    if len(argv) < 3 or argv[1] != "--state":
        sys.stderr.write(__doc__)
        return 2
    with open(argv[2], "r+") as handle_:
        fcntl.flock(handle_, fcntl.LOCK_EX)
        state = json.load(handle_)
        status, out, err, hang = handle(state, argv[3:])
        handle_.seek(0)
        handle_.truncate()
        json.dump(state, handle_)
    if hang:
        time.sleep(hang)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
