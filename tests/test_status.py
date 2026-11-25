"""`superfan status`: plain output, and the full-screen dashboard in a real pseudo-terminal."""
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

import pytest

from superfan.bmc import MODE_FULL
from superfan.status import build_frame, frame_text, liveness, load_frame, read_status, write_status

from conftest import ENV, run_cli

NOW = 1_800_000_000.0


def sample(**changes):
    status = {
        "version": "0.2.0", "pid": os.getpid(), "time": NOW - 4, "interval": 10, "running": True, "dry_run": False,
        "state": "normal", "reasons": [], "bmc_mode": "Full", "errors": {}, "calibrated": ["cpu"],
        "zones": [
            {"name": "cpu", "bmc_zone": 1, "duty": 35, "min_duty": 20, "demand": 34.2,
             "driver": "ipmi:CPU Temp", "driver_temp": 52.0, "reason": None},
            {"name": "chassis", "bmc_zone": 0, "duty": 60, "min_duty": 30, "demand": 60.0,
             "driver": "command:gpu0", "driver_temp": 70.0, "reason": None},
        ],
        "sensors": [
            {"name": "ipmi:CPU Temp", "value": 52.0, "zone": "cpu", "level": "warm"},
            {"name": "command:gpu0", "value": 70.0, "zone": "chassis", "level": "warm"},
            {"name": "hwmon:nvme@nvme0/Composite", "value": 38.9, "zone": "chassis", "level": "ok"},
            {"name": "ipmi:System Temp", "value": 71.0, "zone": "chassis", "level": "hot"},
        ],
        "fans": [
            {"name": "FAN1", "rpm": 1080, "status": "ok", "zone": "chassis"},
            {"name": "FAN2", "rpm": 280, "status": "nc", "zone": "chassis"},
            {"name": "FAN3", "rpm": None, "status": "ns", "zone": "chassis"},
            {"name": "FANA", "rpm": 1274, "status": "ok", "zone": "cpu"},
        ],
    }
    status.update(changes)
    return status


def style_of(frame, text):
    """The style of the first span containing `text`."""
    for row in frame:
        for span_text, style in row:
            if text in span_text:
                return style
    raise AssertionError("%r is not in the frame" % text)


def test_the_frame_shows_status_zones_fans_and_temperatures():
    text = frame_text(build_frame(sample(), NOW, alive=True))
    assert "Superfan Monitor  v0.2.0" in text
    assert "Status: Normal    Fan control: Full    Updated: 4s ago" in text
    assert re.search(r"cpu\s+35%\s+34%\s+20%\s+ipmi:CPU Temp \(52\.0°C\)", text)
    assert re.search(r"chassis\s+60%\s+60%\s+30%\s+command:gpu0 \(70\.0°C\)", text)
    assert re.search(r"FAN1\s+1080\s+ok\s+chassis", text) and re.search(r"FAN3\s+no reading\s+ns", text)
    assert re.search(r"hwmon:nvme@nvme0/Composite\s+38\.9°C\s+chassis", text)
    assert max(len(line) for line in text.splitlines()) <= 100


def test_colours_follow_state_temperature_and_fan_health():
    frame = build_frame(sample(), NOW, alive=True)
    assert style_of(frame, "Normal") == "ok"
    assert style_of(frame, "38.9°C") == "ok"        # below its curve: green
    assert style_of(frame, "  52.0°C") == "warn"    # on its curve: yellow (the padded cell, not "Driven by")
    assert style_of(frame, "71.0°C") == "bad"       # at the top of its curve: red
    assert style_of(frame, "1080") == "ok" and style_of(frame, "280") == "warn"
    assert style_of(frame, "no reading") == "bad"
    assert style_of(frame, "Superfan Monitor") == "title"


def test_emergency_and_failsafe_are_shown_with_their_reasons():
    emergency = build_frame(sample(state="emergency", reasons=["ipmi:CPU Temp is at 91.0°C (critical is 90°C)"]),
                            NOW, alive=True)
    assert style_of(emergency, "EMERGENCY") == "bad"
    assert style_of(emergency, "! ipmi:CPU Temp is at 91.0°C") == "bad"
    zones = sample()["zones"]
    zones[0].update(duty=100, demand=None, driver=None, driver_temp=None, reason="no reading for ipmi:CPU Temp")
    failsafe = build_frame(sample(state="failsafe", reasons=["zone cpu: no reading for ipmi:CPU Temp"], zones=zones,
                                  errors={"ipmi": "`ipmitool sdr list full` timed out after 30s"}), NOW, alive=True)
    assert style_of(failsafe, "FAILSAFE") == "warn"
    text = frame_text(failsafe)
    assert re.search(r"cpu\s+100%\s+-\s+20%\s+no reading for ipmi:CPU Temp", text)
    assert "! source ipmi: `ipmitool sdr list full` timed out after 30s" in text


@pytest.mark.parametrize("changes, alive, expected, label", [
    ({}, True, "running", "Normal"),
    ({"running": False}, True, "stopped", "STOPPED"),
    ({}, False, "dead", "NOT RUNNING"),
    ({"time": NOW - 500}, True, "stale", "NOT UPDATING"),
])
def test_a_daemon_that_is_not_reporting_is_called_out(changes, alive, expected, label):
    status = sample(**changes)
    assert liveness(status, NOW, alive) == expected
    frame = build_frame(status, NOW, alive=alive)
    assert style_of(frame, label) == ("ok" if expected == "running" else "bad")
    assert ("the daemon is not running" in frame_text(frame)) == (expected != "running")


def test_dry_run_and_footer():
    frame = build_frame(sample(dry_run=True), NOW, alive=True, footer="Press q to quit")
    assert style_of(frame, "DRY RUN") == "warn" and frame[-1] == [("Press q to quit", "dim")]


def test_status_file_round_trip_and_errors(tmp_path):
    path = str(tmp_path / "run" / "status.json")
    write_status(path, sample())
    assert read_status(path)["zones"][0]["name"] == "cpu"
    assert oct(os.stat(path).st_mode & 0o777) == "0o644" and os.listdir(os.path.dirname(path)) == ["status.json"]
    frame, live = load_frame(str(tmp_path / "missing.json"), NOW)
    assert live == "missing" and "the daemon has not run since boot" in frame_text(frame)
    (tmp_path / "junk.json").write_text("{not json")
    assert load_frame(str(tmp_path / "junk.json"), NOW)[1] == "missing"


# -- the command, against a file only -----------------------------------------

@pytest.fixture
def trapped_env(tmp_path):
    """An environment where running anything called ipmitool leaves evidence behind."""
    bin_dir = tmp_path / "trap"
    bin_dir.mkdir()
    trap = bin_dir / "ipmitool"
    trap.write_text("#!/bin/sh\ntouch %s\nexit 1\n" % (tmp_path / "BMC_WAS_POLLED"))
    trap.chmod(0o755)
    return dict(ENV, PATH="%s:%s" % (bin_dir, ENV["PATH"]), TERM="xterm-256color"), tmp_path / "BMC_WAS_POLLED"


def test_status_prints_the_report_without_running_ipmitool(tmp_path, trapped_env):
    env, evidence = trapped_env
    path = str(tmp_path / "status.json")
    write_status(path, sample(time=time.time()))
    result = run_cli("status", "--status-file", path, env=env)
    assert result.returncode == 0, result.stderr
    assert "Status: Normal" in result.stdout and "ipmi:CPU Temp (52.0°C)" in result.stdout
    assert "\x1b" not in result.stdout
    assert not evidence.exists()


def test_status_exit_code_tells_scripts_whether_the_daemon_is_alive(tmp_path):
    path = str(tmp_path / "status.json")
    assert run_cli("status", "--status-file", path).returncode == 2            # never ran
    write_status(path, sample(time=time.time(), running=False))
    stopped = run_cli("status", "--status-file", path)
    assert stopped.returncode == 1 and "STOPPED" in stopped.stdout
    write_status(path, sample(time=time.time(), pid=2 ** 22 + 12345))          # no such process
    assert "NOT RUNNING" in run_cli("status", "--status-file", path).stdout


def test_status_json_is_the_raw_report(tmp_path):
    path = str(tmp_path / "status.json")
    write_status(path, sample())
    result = run_cli("status", "--status-file", path, "--json")
    assert result.returncode == 0 and json.loads(result.stdout)["zones"][1]["driver"] == "command:gpu0"


def test_status_finds_the_file_through_the_config(fake, daemon):
    d = daemon()
    d.wait_running()
    result = run_cli("status", "-c", d.config)
    assert result.returncode == 0, result.stderr
    assert re.search(r"cpu\s+25%", result.stdout) and re.search(r"chassis\s+40%", result.stdout)


# -- the full-screen dashboard in a pseudo-terminal -----------------------------

class Terminal:
    """Runs a command attached to a pseudo-terminal of a fixed size and collects what it draws."""

    def __init__(self, argv, env, rows=40, cols=110):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
        os.close(slave)
        self.output = b""

    def read_until(self, *needles, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(needle in self.output for needle in needles):
                return self.output
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    break
        raise AssertionError("never drew %r; got %r" % (needles, self.output[-600:]))

    def send(self, data):
        os.write(self.master, data)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        os.close(self.master)


@pytest.fixture
def terminal():
    opened = []

    def open_terminal(*args, env):
        term = Terminal([sys.executable, "-m", "superfan", *args], env)
        opened.append(term)
        return term
    yield open_terminal
    for term in opened:
        term.close()


GREEN, YELLOW, RED = rb"\x1b\[[0-9;]*32m", rb"\x1b\[[0-9;]*33m", rb"\x1b\[[0-9;]*31m"


def test_watch_draws_a_full_screen_colour_dashboard_and_quits_on_q(tmp_path, trapped_env, terminal):
    env, evidence = trapped_env
    path = str(tmp_path / "status.json")
    write_status(path, sample(time=time.time()))
    term = terminal("status", "--watch", "--status-file", path, env=env)
    screen = term.read_until(b"Superfan Monitor", b"Press q to quit", b"FANA", b"ipmi:System Temp")
    assert b"\x1b[?1049h" in screen                       # switched to the alternate (full) screen
    assert re.search(GREEN, screen) and re.search(YELLOW, screen) and re.search(RED, screen)
    assert b"Normal" in screen and b"command:gpu0" in screen
    term.send(b"q")
    assert term.proc.wait(timeout=10) == 0
    assert not evidence.exists()                          # it never ran ipmitool


def test_watch_picks_up_new_reports_while_open(tmp_path, trapped_env, terminal):
    env, _ = trapped_env
    path = str(tmp_path / "status.json")
    write_status(path, sample(time=time.time()))
    term = terminal("status", "--watch", "--status-file", path, env=env)
    term.read_until(b"Normal")
    write_status(path, sample(time=time.time(), state="emergency", reasons=["ipmi:CPU Temp is at 91.0C"]))
    term.read_until(b"EMERGENCY", b"ipmi:CPU Temp is at 91.0C")
    term.send(b"q")
    assert term.proc.wait(timeout=10) == 0


def test_watch_survives_a_missing_status_file(tmp_path, trapped_env, terminal):
    env, _ = trapped_env
    term = terminal("status", "--watch", "--status-file", str(tmp_path / "nope.json"), env=env)
    term.read_until(b"nope.json does not exist")
    term.send(b"q")
    assert term.proc.wait(timeout=10) == 0


def test_watching_does_not_stop_or_disturb_the_running_daemon(fake, daemon, trapped_env, terminal):
    env, evidence = trapped_env
    d = daemon()
    d.wait_running()
    term = terminal("status", "--watch", "-c", d.config, env=env)
    term.read_until(b"Normal", b"cpu", b"chassis", b"ipmi:CPU Temp")
    scans_before = len(fake.scans())
    reports_before = d.wait_running()["time"]
    d.wait_running(cycles=2)                              # the daemon keeps cycling while being watched
    term.send(b"q")
    assert term.proc.wait(timeout=10) == 0
    assert d.proc.poll() is None and fake.state["mode"] == MODE_FULL
    assert d.status()["time"] > reports_before and len(fake.scans()) > scans_before
    assert not evidence.exists()
    d.signal(signal.SIGTERM)
    assert d.wait()[0] == 0
