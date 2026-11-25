"""`superfan run` as a real process: signals, exit paths, systemd notifications."""
import os
import signal
import socket
import time

import pytest

from superfan.bmc import MODE_FULL, MODE_OPTIMAL, MODE_STANDARD

from conftest import ENV, run_cli, wait_for


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_signal_stops_the_daemon_and_returns_control_to_the_bmc(fake, daemon, signum):
    d = daemon()
    d.wait_running()
    assert fake.state["mode"] == MODE_FULL
    assert fake.state["duty"] == {"0": 40, "1": 25}
    d.signal(signum)
    code, err = d.wait()
    assert code == 0, err
    assert fake.state["mode"] == MODE_STANDARD
    assert "returned fan control to the BMC (Standard mode)" in err
    assert d.status()["running"] is False


def test_the_bmcs_own_mode_is_what_gets_restored(fake, daemon):
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))
    d = daemon()
    d.wait_running()
    d.signal(signal.SIGTERM)
    assert d.wait()[0] == 0
    assert fake.state["mode"] == MODE_OPTIMAL


def test_after_a_hard_kill_the_release_command_restores_control(fake, daemon):
    """What the unit's ExecStopPost does when the daemon dies without a chance to clean up."""
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))
    d = daemon()
    d.wait_running()
    d.signal(signal.SIGKILL)
    d.wait()
    assert fake.state["mode"] == MODE_FULL                 # nobody is in control at this point

    released = run_cli("release", "-c", d.config)
    assert released.returncode == 0, released.stderr
    assert "Fan control returned to the BMC (Optimal mode)" in released.stdout
    assert fake.state["mode"] == MODE_OPTIMAL

    again = run_cli("release", "-c", d.config)             # harmless when there is nothing to do
    assert again.returncode == 0 and "already controls the fans" in again.stdout
    assert fake.state["mode"] == MODE_OPTIMAL


def test_restarting_after_a_hard_kill_still_remembers_the_original_mode(fake, daemon):
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))
    first = daemon()
    first.wait_running()
    first.signal(signal.SIGKILL)
    first.wait()
    second = daemon(first.config)                          # finds the BMC in Full mode
    second.wait_running()
    second.signal(signal.SIGTERM)
    assert second.wait()[0] == 0
    assert fake.state["mode"] == MODE_OPTIMAL


def test_a_hung_sensor_scan_fails_safe_and_the_daemon_stays_responsive(fake, daemon):
    d = daemon(mutate=lambda data: data["bmc"].update(timeout=1))
    d.wait_running()
    fake.update(lambda s: s.update(hang={"sdr": 60}))
    wait_for(lambda: fake.state["duty"] == {"0": 100, "1": 100}, what="failsafe duty")
    status = wait_for(lambda: (d.status() or {}).get("state") == "failsafe" and d.status(), what="failsafe status")
    assert "timed out" in status["errors"]["ipmi"]
    started = time.monotonic()
    d.signal(signal.SIGTERM)
    code, err = d.wait()
    assert code == 0, err
    assert time.monotonic() - started < 10
    assert fake.state["mode"] == MODE_STANDARD


def test_systemd_gets_ready_watchdog_and_stopping_notifications(fake, daemon, tmp_path):
    address = str(tmp_path / "notify")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(address)
    sock.settimeout(15)
    d = daemon(env=dict(ENV, NOTIFY_SOCKET=address))
    received = [sock.recv(64)]
    assert received == [b"READY=1"]
    assert fake.state["mode"] == MODE_FULL                 # ready is only announced once in control
    d.wait_running(cycles=2)
    d.signal(signal.SIGTERM)
    d.wait()
    sock.settimeout(0.2)
    try:
        while True:
            received.append(sock.recv(64))
    except socket.timeout:
        pass
    assert received.count(b"WATCHDOG=1") >= 4              # twice per cycle
    assert received[-1] == b"STOPPING=1"


def test_a_second_daemon_is_refused_and_does_not_disturb_the_first(fake, daemon):
    d = daemon()
    d.wait_running()
    second = run_cli("run", "-c", d.config, "--once")
    assert second.returncode == 1
    assert "already controlling the fans (pid %d)" % d.proc.pid in second.stderr
    assert fake.state["mode"] == MODE_FULL and d.proc.poll() is None


def test_a_config_error_exits_78_without_touching_the_bmc(fake, write_config):
    path = write_config(mutate=lambda data: data.update(intervall=5))
    result = run_cli("run", "-c", path)
    assert result.returncode == 78
    assert "unknown key 'intervall'" in result.stderr
    assert fake.log == []


def test_a_broken_calibration_file_is_a_config_error(fake, write_config):
    path = write_config()
    with open(os.path.join(os.path.dirname(path), "calibration.yaml"), "w") as handle:
        handle.write("zones: [oops")
    result = run_cli("run", "-c", path, "--once")
    assert result.returncode == 78 and "calibration" in result.stderr
    assert fake.log == []


def test_dry_run_once_reports_decisions_and_leaves_the_bmc_alone(fake, write_config):
    result = run_cli("run", "-c", write_config(), "--dry-run", "--once")
    assert result.returncode == 0, result.stderr
    assert "dry run, not touching the fans" in result.stderr
    assert "zone cpu would run at 25% (ipmi:CPU Temp at 45.0°C)" in result.stderr
    assert fake.state["mode"] == MODE_STANDARD and fake.duty_writes() == []


def test_dry_run_can_run_beside_the_live_daemon(fake, daemon):
    d = daemon()
    before = d.wait_running()
    result = run_cli("run", "-c", d.config, "--dry-run", "--once")
    assert result.returncode == 0, result.stderr
    assert fake.state["mode"] == MODE_FULL and d.proc.poll() is None
    assert d.status()["pid"] == before["pid"]              # the live status file was not overwritten


def test_once_runs_a_single_cycle_and_releases(fake, write_config):
    result = run_cli("run", "-c", write_config(), "--once")
    assert result.returncode == 0, result.stderr
    assert fake.duty_writes() == [(1, 25), (0, 40)]
    assert fake.state["mode"] == MODE_STANDARD
