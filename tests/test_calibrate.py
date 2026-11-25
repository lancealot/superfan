import os
import signal
import subprocess
import sys

import pytest
import yaml

from superfan.bmc import MODE_FULL, MODE_OPTIMAL, MODE_STANDARD
from superfan.calibrate import CalibrationAborted, Calibrator, Settings, load_calibration, summary
from superfan.config import ConfigError
from superfan.controller import Controller

from conftest import ENV, run_cli, wait_for

FAST = dict(settle_min=0, poll=0, settle_max=5)
FAST_FLAGS = ["--yes", "--settle-min", "0", "--poll", "0", "--settle-max", "5"]
CHASSIS = ["FAN1", "FAN2", "FAN3", "FAN4", "FAN5"]
MAX_RPM = {"FAN1": 1800, "FAN2": 1400, "FAN3": 1400, "FAN4": 1400, "FAN5": 1800, "FANA": 3640}


def calibrate(cfg, fake, **settings):
    said = []
    calibrator = Calibrator(cfg, fake.client(), Settings(**dict(FAST, **settings)), say=said.append)
    calibrator.session.retry_delay = 0
    return calibrator.run(), said


def test_every_row_holds_the_rpm_for_its_own_duty(fake, make_cfg):
    fake.update(lambda s: s.update(thresholds={name: [0, 0, 0] for name in MAX_RPM}))
    data, _ = calibrate(make_cfg(), fake)
    chassis, cpu = data["zones"]["chassis"], data["zones"]["cpu"]
    assert list(cpu["table"]) == [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]
    for duty, rpms in cpu["table"].items():
        assert rpms == {"FANA": round(3640 * duty / 100)}
    for duty, rpms in chassis["table"].items():
        assert rpms == {name: round(MAX_RPM[name] * duty / 100) for name in CHASSIS}
    assert cpu["min_duty"] == 10 and cpu["stopped"] is None


def test_each_fan_is_assigned_to_the_zone_it_follows(fake, make_cfg):
    data, _ = calibrate(make_cfg(), fake)
    assert data["zones"]["chassis"]["fans"] == CHASSIS and data["zones"]["chassis"]["bmc_zone"] == 0
    assert data["zones"]["cpu"]["fans"] == ["FANA"] and data["zones"]["cpu"]["bmc_zone"] == 1


def test_only_one_zone_moves_at_a_time_and_it_is_put_back(fake, make_cfg):
    calibrate(make_cfg(), fake, step=25, floor=25)
    writes = fake.duty_writes()
    assert writes == [(1, 100), (0, 100),                    # everything to full
                      (1, 75), (1, 50), (1, 25), (1, 100),   # cpu zone swept, then restored
                      (0, 75), (0, 50), (0, 25), (0, 100),   # chassis zone swept, then restored
                      (1, 100), (0, 100)]                    # full again before handing back


def test_slow_fans_are_read_after_they_settle_not_before(fake, make_cfg):
    fake.update(lambda s: s.update(lag=0.5, thresholds={name: [0, 0, 0] for name in MAX_RPM}))
    data, _ = calibrate(make_cfg(), fake, step=20, floor=20)
    table = data["zones"]["cpu"]["table"]
    for duty in (80, 60, 40, 20):
        assert table[duty]["FANA"] == pytest.approx(3640 * duty / 100, rel=0.05)


def test_sweep_stops_one_step_above_a_stall(fake, make_cfg):
    fake.update(lambda s: s["fans"]["FANA"].update(stall_below=30))
    data, _ = calibrate(make_cfg(), fake)
    cpu = data["zones"]["cpu"]
    assert cpu["min_duty"] == 30 and cpu["stopped"] == "FANA stopped at 20 %"
    assert list(cpu["table"]) == [100, 90, 80, 70, 60, 50, 40, 30, 20] and cpu["table"][20] == {"FANA": 0}
    assert (1, 10) not in fake.duty_writes()


def test_sweep_stops_before_a_fan_reaches_the_bmcs_lower_threshold(fake, make_cfg):
    fake.update(lambda s: s.update(thresholds={"FAN2": [300, 500, 700]}))
    data, _ = calibrate(make_cfg(), fake)
    chassis = data["zones"]["chassis"]
    assert chassis["min_duty"] == 60          # 60 % is 840 RPM; 50 % would be 700, inside the 15 % margin
    assert "FAN2 fell to 700 RPM, too close to the BMC's lower threshold of 700 RPM at 50 %" == chassis["stopped"]
    assert data["thresholds"]["FAN2"] == {"lnr": 300.0, "lcr": 500.0, "lnc": 700.0}


def heat_rule(state):
    state["heat"] = [{"sensor": "CPU Temp", "zone": 1, "below": 60, "rise": 12, "base": 45}]


def test_heat_abandons_the_run_and_hands_the_fans_back(fake, make_cfg):
    fake.update(lambda s: (heat_rule(s), s.update(mode=MODE_OPTIMAL)))
    with pytest.raises(CalibrationAborted, match=r"ipmi:CPU Temp reached 8\d\.0°C \(limit 80°C\)"):
        calibrate(make_cfg(), fake)
    state = fake.state
    assert state["mode"] == MODE_OPTIMAL
    assert fake.duty_writes()[-2:] == [(1, 100), (0, 100)]   # full speed first, then back to the BMC
    assert min(duty for zone, duty in fake.duty_writes() if zone == 1) >= 40   # stopped well short of the floor
    assert not [duty for zone, duty in fake.duty_writes() if zone == 0 and duty < 100]   # never started the next zone


def test_max_temp_overrides_the_per_sensor_limit(fake, make_cfg):
    fake.update(heat_rule)
    with pytest.raises(CalibrationAborted, match=r"limit 60°C"):
        calibrate(make_cfg(), fake, max_temp=60)


def test_a_machine_that_is_already_hot_is_not_calibrated(fake, make_cfg):
    fake.set_temp("CPU Temp", 82)
    with pytest.raises(CalibrationAborted, match="reached 82.0°C"):
        calibrate(make_cfg(), fake)
    assert not [duty for _, duty in fake.duty_writes() if duty < 100]
    assert fake.state["mode"] == MODE_STANDARD


def test_losing_a_sensor_abandons_the_run(fake, make_cfg):
    fake.set_temp("CPU Temp", None)
    with pytest.raises(CalibrationAborted, match="lost the reading for ipmi:CPU Temp"):
        calibrate(make_cfg(), fake)
    assert fake.state["mode"] == MODE_STANDARD


def test_a_zone_no_fan_follows_gets_no_minimum(fake, make_cfg):
    fake.update(lambda s: s["fans"].__setitem__("FANA", None))
    data, said = calibrate(make_cfg(), fake)
    assert data["zones"]["cpu"] == {"bmc_zone": 1, "fans": [], "min_duty": None, "stopped": None,
                                    "table": {duty: {} for duty in range(100, 0, -10)}}
    assert any("no fan followed BMC zone 1" in line for line in said)


# -- the command -------------------------------------------------------------

def test_the_command_writes_its_own_file_and_leaves_the_config_alone(fake, write_config):
    config = write_config()
    with open(config, "rb") as handle:
        before = handle.read()
    result = run_cli("calibrate", "-c", config, *FAST_FLAGS)
    assert result.returncode == 0, result.stderr
    output = os.path.join(os.path.dirname(config), "calibration.yaml")
    assert "Wrote %s" % output in result.stdout
    assert "minimum safe duty: 30%" in result.stdout        # chassis: FAN2 at 20 % is 280 RPM, under 300 x 1.15
    with open(config, "rb") as handle:
        assert handle.read() == before
    with open(output) as handle:
        text = handle.read()
    assert text.startswith("# Written by `superfan calibrate`")
    data = yaml.safe_load(text)
    assert data["zones"]["chassis"]["min_duty"] == 30 and data["zones"]["chassis"]["table"][40]["FAN1"] == 720
    assert fake.state["mode"] == MODE_STANDARD
    assert load_calibration(output)["cpu"]["fans"] == ["FANA"]


def test_the_daemon_uses_the_result(fake, make_cfg, write_config):
    config = write_config()
    assert run_cli("calibrate", "-c", config, *FAST_FLAGS).returncode == 0
    cfg = make_cfg()
    fake.set_temp("System Temp", 25)
    fake.set_temp("Peripheral Temp", 25)                     # the curve alone asks the chassis zone for 20 %
    ctl = Controller(cfg, fake.client(), load_calibration(cfg.calibration_file))
    ctl.session.takeover()
    ctl.cycle()
    assert fake.state["duty"]["0"] == 30


def test_heat_makes_the_command_fail_and_write_nothing(fake, write_config):
    fake.update(heat_rule)
    config = write_config()
    result = run_cli("calibrate", "-c", config, *FAST_FLAGS)
    assert result.returncode == 1
    assert "Calibration abandoned: ipmi:CPU Temp reached" in result.stderr and "Nothing was written" in result.stderr
    assert not os.path.exists(os.path.join(os.path.dirname(config), "calibration.yaml"))
    assert fake.state["mode"] == MODE_STANDARD


def test_output_can_be_redirected(fake, write_config, tmp_path):
    target = tmp_path / "elsewhere" / "cal.yaml"
    result = run_cli("calibrate", "-c", write_config(), "-o", str(target), "--step", "50", "--floor", "50", *FAST_FLAGS)
    assert result.returncode == 0, result.stderr
    assert list(yaml.safe_load(target.read_text())["zones"]["cpu"]["table"]) == [100, 50]


def test_it_will_not_start_unattended_without_yes(fake, write_config):
    result = run_cli("calibrate", "-c", write_config(), stdin=subprocess.DEVNULL)
    assert result.returncode == 2 and "pass --yes" in result.stderr
    assert fake.log == []


def test_it_refuses_while_the_daemon_is_in_control(fake, daemon):
    d = daemon()
    d.wait_running()
    fake.clear_log()
    result = run_cli("calibrate", "-c", d.config, *FAST_FLAGS)
    assert result.returncode == 1 and "already controlling the fans" in result.stderr
    assert not [duty for _, duty in fake.duty_writes() if duty == 100]   # it never touched the fans
    assert d.proc.poll() is None


def test_interrupting_it_hands_the_fans_back(fake, write_config):
    config = write_config()
    proc = subprocess.Popen([sys.executable, "-m", "superfan", "calibrate", "-c", config, "--yes",
                             "--settle-min", "60"], env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(lambda: fake.state["mode"] == MODE_FULL, what="calibration to take control")
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=20)
    finally:
        proc.kill()
    assert proc.returncode == 1 and "Calibration abandoned: interrupted" in err
    assert fake.state["mode"] == MODE_STANDARD
    assert not os.path.exists(os.path.join(os.path.dirname(config), "calibration.yaml"))


def test_summary_is_a_readable_table(fake, make_cfg):
    data, _ = calibrate(make_cfg(), fake, step=50, floor=50)
    text = summary(data)
    assert "Zone cpu (BMC zone 1): FANA" in text
    assert "  100%     3640" in text and "   50%     1820" in text
    assert "minimum safe duty: 50% (reached the floor without trouble)" in text


@pytest.mark.parametrize("content, message", [
    ("zones: [oops", "cannot read the calibration"),
    ("just: a mapping\n", "not a superfan calibration file"),
    ("zones:\n  cpu:\n    min_duty: 20\n", "zone 'cpu' is malformed"),
    ("zones:\n  cpu:\n    bmc_zone: 1\n    min_duty: 250\n", "zone 'cpu' is malformed"),
])
def test_a_damaged_calibration_file_is_rejected(tmp_path, content, message):
    path = tmp_path / "calibration.yaml"
    path.write_text(content)
    with pytest.raises(ConfigError, match=message):
        load_calibration(str(path))


def test_no_calibration_file_means_uncalibrated(tmp_path):
    assert load_calibration(str(tmp_path / "calibration.yaml")) == {}
