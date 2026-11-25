"""The remaining commands: sensors, check, set and release."""
import os

from superfan.bmc import MODE_FULL, MODE_OPTIMAL, MODE_STANDARD
from superfan.cli import build_parser

from conftest import ENV, REPO, run_cli


def with_gpu_and_nvme(write_config, hwmon, tmp_path):
    hwmon.add("nvme", "nvme0", {"Composite": 38.9})
    hwmon.add("k10temp", "0000:00:18.3", {"Tctl": 46.1})
    gpu = tmp_path / "gpu"
    gpu.write_text("61\n")

    def mutate(data):
        data["sources"]["command"] = {"gpu0": ["cat", str(gpu)]}
        data["zones"]["chassis"]["inputs"].append({"sensors": ["command:gpu0"], "curve": [[50, 25], [85, 100]]})
    return write_config(hwmon=hwmon, mutate=mutate)


def test_sensors_lists_every_source_and_who_uses_what(fake, write_config, hwmon, tmp_path):
    result = run_cli("sensors", "-c", with_gpu_and_nvme(write_config, hwmon, tmp_path))
    assert result.returncode == 0, result.stderr
    lines = {line.split()[0]: line for line in result.stdout.splitlines() if line.startswith("  ")}
    assert "45.0°C  cpu" in lines["ipmi:CPU"]
    assert "61.0°C  chassis" in lines["command:gpu0"]
    assert "38.9°C  -" in lines["hwmon:nvme@nvme0/Composite"]           # visible, but no zone uses it
    assert "46.1°C  -" in lines["hwmon:k10temp@0000:00:18.3/Tctl"]
    assert "700 RPM  ok" in lines["FAN2"]
    assert fake.state["mode"] == MODE_STANDARD and fake.duty_writes() == []   # looking changes nothing
    assert fake.log == [["sdr", "list", "full"]]


def test_sensors_reports_a_broken_source_and_carries_on(fake, write_config, hwmon, tmp_path):
    config = with_gpu_and_nvme(write_config, hwmon, tmp_path)
    fake.update(lambda s: s.update(fail={"sdr": -1}))
    result = run_cli("sensors", "-c", config)
    assert result.returncode == 0
    assert "PROBLEMS" in result.stdout and "ipmi: `ipmitool sdr list full` failed" in result.stdout
    assert "hwmon:nvme@nvme0/Composite" in result.stdout


def test_sensors_works_before_any_config_exists(tmp_path):
    result = run_cli("sensors", "-c", str(tmp_path / "missing.yaml"), env=dict(ENV, PATH="/nonexistent"))
    assert "using built-in defaults" in result.stderr
    assert "TEMPERATURES" in result.stdout and "ipmi: ipmitool: command not found" in result.stdout


def test_check_shows_what_each_input_matches(fake, write_config, hwmon, tmp_path):
    result = run_cli("check", "-c", with_gpu_and_nvme(write_config, hwmon, tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "syntax OK (2 zone(s), interval 1s)" in result.stdout
    assert "calibration: none yet" in result.stdout
    assert "ipmi:CPU Temp -> ipmi:CPU Temp 45.0°C" in result.stdout
    assert "command:gpu0 -> command:gpu0 61.0°C" in result.stdout


def test_check_fails_when_a_required_input_matches_nothing(fake, write_config):
    def mutate(data):
        data["zones"]["cpu"]["inputs"][0]["sensors"] = ["ipmi:CPU1 Temp"]
        data["zones"]["cpu"]["inputs"].append({"sensors": ["ipmi:GPU*"], "curve": [[40, 20]], "required": False})
    result = run_cli("check", "-c", write_config(mutate=mutate))
    assert result.returncode == 1
    assert "ipmi:CPU1 Temp -> NOTHING MATCHES; this zone would run at 100%" in result.stdout
    assert "ipmi:GPU* -> nothing matches (optional)" in result.stdout
    assert "1 problem(s)" in result.stdout


def test_check_offline_reads_no_sensor(fake, write_config):
    result = run_cli("check", "--offline", "-c", write_config())
    assert result.returncode == 0 and fake.log == []


def test_the_shipped_config_passes_an_offline_check():
    result = run_cli("check", "--offline", "-c", os.path.join(REPO, "config", "default.yaml"))
    assert result.returncode == 0, result.stderr
    assert "syntax OK (2 zone(s), interval 10s)" in result.stdout


def test_config_problems_are_listed_one_per_line(write_config):
    def mutate(data):
        data["intervall"] = 5
        data["zones"]["cpu"]["inputs"][0]["curve"] = [[60, 40], [40, 20]]
    result = run_cli("check", "-c", write_config(mutate=mutate))
    assert result.returncode == 78
    assert "  - unknown key 'intervall'" in result.stderr
    assert "  - zones.cpu.inputs[0]: curve temperatures must increase (60 then 40)" in result.stderr


def test_set_takes_manual_control_of_one_zone_and_release_ends_it(fake, write_config):
    config = write_config()
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))
    result = run_cli("set", "cpu", "40", "-c", config)
    assert result.returncode == 0, result.stderr
    assert "BMC zone 1 set to 40%" in result.stdout and "Nothing is watching temperatures" in result.stdout
    assert fake.state["mode"] == MODE_FULL and fake.state["duty"] == {"0": 100, "1": 40}
    assert fake.log[-1] == ["raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x28"]

    assert run_cli("set", "0", "55", "-c", config).returncode == 0       # a bare BMC zone number works too
    assert fake.state["duty"] == {"0": 55, "1": 40}

    released = run_cli("release", "-c", config)
    assert released.returncode == 0 and fake.state["mode"] == MODE_OPTIMAL


def test_set_rejects_bad_input_without_sending_anything(fake, write_config):
    config = write_config()
    assert run_cli("set", "cpu", "150", "-c", config).returncode == 2
    assert run_cli("set", "nosuchzone", "50", "-c", config).returncode == 2
    assert fake.log == [] and fake.state["mode"] == MODE_STANDARD


def test_set_is_refused_while_the_daemon_is_in_control(fake, daemon):
    d = daemon()
    d.wait_running()
    result = run_cli("set", "cpu", "10", "-c", d.config)
    assert result.returncode == 1 and "already controlling the fans" in result.stderr
    assert fake.state["duty"]["1"] == 25


def test_release_works_with_a_broken_config_as_long_as_ipmitool_does(fake, tmp_path):
    """ExecStopPost must be able to hand the fans back even if the config has since been broken."""
    fake.client().set_mode(MODE_FULL)
    broken = tmp_path / "config.yaml"
    broken.write_text("zones: [oops")
    shim = tmp_path / "bin" / "ipmitool"
    shim.parent.mkdir()
    shim.write_text('#!/bin/sh\nexec %s "$@"\n' % " ".join(fake.command))
    shim.chmod(0o755)
    env = dict(ENV, PATH="%s:%s" % (shim.parent, ENV["PATH"]))
    result = run_cli("release", "-c", str(broken), env=env)
    assert result.returncode == 0, result.stderr
    assert fake.state["mode"] == MODE_STANDARD


def test_release_reports_failure_when_the_bmc_is_unreachable(tmp_path):
    result = run_cli("release", "-c", str(tmp_path / "missing.yaml"), env=dict(ENV, PATH="/nonexistent"))
    assert result.returncode == 1 and "command not found" in result.stderr


def test_version_and_help():
    assert run_cli("--version").stdout.strip() == "superfan 0.2.0"
    text = run_cli("--help").stdout
    for command in ("run", "status", "sensors", "check", "calibrate", "set", "release"):
        assert command in text
    assert build_parser().parse_args(["status", "--watch"]).watch
