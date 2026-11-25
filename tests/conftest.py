"""Shared fixtures: a simulated BMC, a fake /sys/class/hwmon tree, and config builders."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time

import pytest
import yaml

from superfan.bmc import Bmc
from superfan.config import load_config

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(REPO, "tests", "fake_ipmitool.py")
ENV = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"), PYTHONUNBUFFERED="1")
ENV.pop("NOTIFY_SOCKET", None)


def default_state():
    fan = lambda zone, rpm: {"zone": zone, "max_rpm": rpm}  # noqa: E731
    return {
        "mode": 0,  # Standard: the BMC is in control
        "auto_duty": 50,
        "duty": {"0": 50, "1": 50},
        "temps": {"CPU Temp": 45, "System Temp": 35, "Peripheral Temp": 40, "VRMCpu Temp": 42,
                  "P1_DIMMA~D Temp": 38, "M2NVMeSSD Temp1": None},
        "fans": {"FAN1": fan(0, 1800), "FAN2": fan(0, 1400), "FAN3": fan(0, 1400), "FAN4": fan(0, 1400),
                 "FAN5": fan(0, 1800), "FANA": fan(1, 3640), "FANB": None},
        "log": [],
    }


class FakeBmc:
    """Handle on the simulated BMC's state file."""

    def __init__(self, path):
        self.path = str(path)
        with open(self.path, "w") as handle:
            json.dump(default_state(), handle)
        self.command = [sys.executable, "-S", FAKE, "--state", self.path]

    def update(self, change):
        """Atomically modify the state: `change` receives the state dict."""
        with open(self.path, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            state = json.load(handle)
            change(state)
            handle.seek(0)
            handle.truncate()
            json.dump(state, handle)

    @property
    def state(self):
        with open(self.path) as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            return json.load(handle)

    def set_temp(self, name, value):
        self.update(lambda s: s["temps"].__setitem__(name, value))

    @property
    def log(self):
        return self.state["log"]

    def clear_log(self):
        self.update(lambda s: s["log"].clear())

    def raw_log(self):
        """Every raw command received, as lists of ints."""
        return [[int(token, 0) for token in entry[1:]] for entry in self.log if entry[:1] == ["raw"]]

    def duty_writes(self):
        """(zone, duty) for every duty-set command received, in order."""
        return [(c[4], c[5]) for c in self.raw_log() if c[:4] == [0x30, 0x70, 0x66, 0x01] and len(c) == 6]

    def scans(self):
        return [entry for entry in self.log if entry[:1] in (["sdr"], ["sensor"])]

    def client(self, **kwargs):
        kwargs.setdefault("timeout", 10)
        kwargs.setdefault("retry_delay", 0)
        return Bmc(self.command, **kwargs)


@pytest.fixture
def fake(tmp_path):
    return FakeBmc(tmp_path / "bmc.json")


class Hwmon:
    """Builds a directory tree shaped like /sys/class/hwmon."""

    def __init__(self, root):
        self.root = str(root)
        os.makedirs(self.root)
        self._count = 0

    def add(self, chip, device, temps):
        """temps maps a label (or None for an unlabelled sensor) to degrees C. Returns the hwmon dir."""
        directory = os.path.join(self.root, "hwmon%d" % self._count)
        self._count += 1
        os.makedirs(directory)
        with open(os.path.join(directory, "name"), "w") as handle:
            handle.write(chip + "\n")
        target = os.path.join(os.path.dirname(self.root), "devices", device)
        os.makedirs(target, exist_ok=True)
        os.symlink(target, os.path.join(directory, "device"))
        for index, (label, value) in enumerate(temps.items(), start=1):
            self.write(directory, index, value)
            if label is not None:
                with open(os.path.join(directory, "temp%d_label" % index), "w") as handle:
                    handle.write(label + "\n")
        return directory

    @staticmethod
    def write(directory, index, value):
        with open(os.path.join(directory, "temp%d_input" % index), "w") as handle:
            handle.write("%d\n" % round(value * 1000))


@pytest.fixture
def hwmon(tmp_path):
    return Hwmon(tmp_path / "sys" / "hwmon")


CPU_CURVE = [[40, 20], [60, 40], [80, 100]]


def base_config(fake, hwmon=None, tmp_path=None, **overrides):
    """A two-zone config like the shipped one, pointed at the fakes."""
    data = {
        "interval": 1,
        "bmc": {"command": fake.command, "timeout": 10},
        "sources": {"ipmi": True, "hwmon": {"root": hwmon.root} if hwmon else False},
        "zones": {
            "cpu": {"bmc_zone": 1, "inputs": [{"sensors": ["ipmi:CPU Temp"], "curve": CPU_CURVE, "critical": 90}]},
            "chassis": {"bmc_zone": 0, "inputs": [
                {"sensors": ["ipmi:System Temp", "ipmi:Peripheral Temp"], "curve": [[30, 20], [50, 60], [60, 100]]},
            ]},
        },
        "safety": {"fault_hold": 0},
        "status_file": os.path.join(str(tmp_path), "run", "status.json"),
    }
    data.update(overrides)
    return data


@pytest.fixture
def write_config(tmp_path, fake):
    """write_config(hwmon=None, **overrides) -> path of a config file on disk."""
    def write(hwmon=None, mutate=None, **overrides):
        data = base_config(fake, hwmon, tmp_path, **overrides)
        if mutate:
            mutate(data)
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        return str(path)
    return write


@pytest.fixture
def make_cfg(write_config):
    def make(*args, **kwargs):
        return load_config(write_config(*args, **kwargs))
    return make


def run_cli(*args, timeout=60, env=None, **kwargs):
    """Run the real command line in a subprocess."""
    return subprocess.run([sys.executable, "-m", "superfan", *args], env=env or ENV, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)


def wait_for(predicate, timeout=20.0, interval=0.05, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("timed out waiting for %s" % what)


def read_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


class Daemon:
    """`superfan run` as a real subprocess, so real signals can be sent to it."""

    def __init__(self, config, *extra, env=None):
        self.config = config
        self.status_file = load_config(config).status_file
        self.proc = subprocess.Popen([sys.executable, "-m", "superfan", "run", "-c", config, *extra],
                                     env=env or ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def status(self):
        return read_json(self.status_file)

    def wait_running(self, cycles=1):
        """Block until the daemon has published at least `cycles` reports."""
        seen = set()

        def enough():
            status = self.status()
            if self.proc.poll() is not None:
                raise AssertionError("daemon exited early: %s" % self.proc.stderr.read())
            if status and status.get("running") and status.get("pid") == self.proc.pid:
                seen.add(status["time"])
            return len(seen) >= cycles
        wait_for(enough, what="daemon to report")
        return self.status()

    def signal(self, signum):
        self.proc.send_signal(signum)

    def wait(self, timeout=30):
        """Returns (exit status, stderr)."""
        try:
            _, err = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
            raise AssertionError("daemon did not exit in %ss:\n%s" % (timeout, err))
        return self.proc.returncode, err

    def kill(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.communicate()


@pytest.fixture
def daemon(write_config):
    started = []

    def start(config=None, *extra, env=None, **overrides):
        instance = Daemon(config or write_config(**overrides), *extra, env=env)
        started.append(instance)
        return instance
    yield start
    for instance in started:
        instance.kill()
