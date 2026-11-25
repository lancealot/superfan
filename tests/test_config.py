import os

import pytest
import yaml

from superfan.config import ConfigError, load_config, parse_config

from conftest import REPO


def minimal(**extra):
    data = {"zones": {"cpu": {"bmc_zone": 1, "inputs": [{"sensors": "ipmi:CPU Temp", "curve": [[40, 20], [80, 100]]}]}}}
    data.update(extra)
    return data


def problems(data):
    with pytest.raises(ConfigError) as caught:
        parse_config(data, "/etc/superfan/config.yaml")
    return " | ".join(caught.value.problems)


def test_the_shipped_config_is_valid():
    cfg = load_config(os.path.join(REPO, "config", "default.yaml"))
    assert set(cfg.zones) == {"cpu", "chassis"}
    assert cfg.zones["cpu"].bmc_zone == 1 and cfg.zones["chassis"].bmc_zone == 0
    assert cfg.interval == 10 and cfg.bmc.command == ["ipmitool"] and cfg.bmc.exit_mode == "previous"
    assert cfg.zones["cpu"].inputs[0].critical == 85
    assert [inp.required for inp in cfg.zones["chassis"].inputs] == [True, False, False]


def test_defaults():
    cfg = parse_config(minimal(), "/etc/superfan/config.yaml")
    assert (cfg.interval, cfg.hysteresis, cfg.failsafe_duty, cfg.max_failures) == (10, 3, 100, 3)
    assert (cfg.bmc.command, cfg.bmc.timeout, cfg.bmc.retries, cfg.fault_hold) == (["ipmitool"], 30, 2, 60)
    assert cfg.ipmi and cfg.hwmon and cfg.hwmon_root == "/sys/class/hwmon"
    assert cfg.status_file == "/run/superfan/status.json" and cfg.state_dir == "/run/superfan"
    assert cfg.calibration_file == "/etc/superfan/calibration.yaml"
    zone = cfg.zones["cpu"]
    assert zone.inputs[0].sensors == ["ipmi:CPU Temp"] and zone.inputs[0].required and zone.min_duty == 0


def test_command_sources_accept_a_string_or_a_list():
    data = minimal(sources={"command": {"gpu0": "nvidia-smi --query-gpu=temperature.gpu -i 0", "x": ["cat", "/a b"]}})
    cfg = parse_config(data)
    assert cfg.commands == {"gpu0": ["nvidia-smi", "--query-gpu=temperature.gpu", "-i", "0"], "x": ["cat", "/a b"]}


def test_a_0_1_config_is_recognised_and_explained():
    message = problems({"ipmi": {}, "temperature": {"hysteresis": 3}, "fans": {"polling_interval": 30}})
    assert "superfan 0.1 config" in message and "format changed in 0.2" in message


@pytest.mark.parametrize("change, expected", [
    (lambda d: d.update(intervall=5), "unknown key 'intervall'"),
    (lambda d: d.update(interval=0), "interval must be at least 1"),
    (lambda d: d.update(interval="fast"), "interval must be a number"),
    (lambda d: d.update(zones={}), "define at least one zone"),
    (lambda d: d["zones"]["cpu"].pop("bmc_zone"), "bmc_zone is required"),
    (lambda d: d["zones"]["cpu"].update(bmc_zone=1.5), "bmc_zone must be a whole number"),
    (lambda d: d["zones"]["cpu"].update(min_duty=120), "min_duty must be between 0 and 100"),
    (lambda d: d["zones"]["cpu"].update(fans=["FANA"]), "unknown key 'fans'"),
    (lambda d: d["zones"]["cpu"].update(inputs=[]), "inputs must be a non-empty list"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(sensors=["CPU Temp"]), "must start with ipmi:, hwmon: or"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(sensors=["command:gpu0"]), "not defined under sources.command"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(curve=[[60, 40], [40, 20]]), "temperatures must increase"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(curve=[[40, 50], [60, 30]]), "duty must not fall"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(curve=[[40, 255]]), "outside 0-100"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(curve="steep"), "curve must be a list"),
    (lambda d: d["zones"]["cpu"]["inputs"][0].update(required="yes"), "required must be true or false"),
    (lambda d: d.update(bmc={"exit_mode": "full"}), "exit_mode must be one of"),
    (lambda d: d.update(safety={"failsafe_duty": 0}), "failsafe_duty must be between 1 and 100"),
    (lambda d: d.update(sources={"hwmon": False}) or d["zones"]["cpu"]["inputs"][0].update(sensors=["hwmon:nvme/*"]),
     "uses the hwmon source, which is turned off"),
])
def test_mistakes_are_reported(change, expected):
    data = minimal()
    change(data)
    assert expected in problems(data)


def test_two_zones_cannot_share_a_bmc_zone():
    data = minimal()
    data["zones"]["other"] = dict(data["zones"]["cpu"])
    assert "bmc_zone 1 is already used by zone 'cpu'" in problems(data)


def test_every_problem_is_reported_at_once():
    data = minimal(interval=0, hysterisis=2)
    data["zones"]["cpu"]["inputs"][0]["curve"] = [[40, 300]]
    with pytest.raises(ConfigError) as caught:
        parse_config(data)
    assert len(caught.value.problems) == 3


def test_unreadable_and_malformed_files(tmp_path):
    with pytest.raises(ConfigError, match="cannot read the file"):
        load_config(str(tmp_path / "missing.yaml"))
    bad = tmp_path / "bad.yaml"
    bad.write_text("zones: [unclosed")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(str(bad))
    bad.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="must contain a mapping"):
        load_config(str(bad))


def test_calibration_file_defaults_to_beside_the_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(minimal()))
    assert load_config(str(path)).calibration_file == str(tmp_path / "calibration.yaml")


@pytest.mark.skipif(os.geteuid() != 0, reason="the check only applies when running as root")
def test_root_refuses_commands_from_a_config_others_can_write(tmp_path):
    path = tmp_path / "config.yaml"
    data = minimal(sources={"command": {"gpu0": "cat /tmp/x"}})
    path.write_text(yaml.safe_dump(data))
    os.chmod(path, 0o666)
    with pytest.raises(ConfigError, match="must be owned by root and not writable by group or others"):
        load_config(str(path))
    os.chmod(path, 0o644)
    assert load_config(str(path)).commands == {"gpu0": ["cat", "/tmp/x"]}
