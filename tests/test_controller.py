"""The control loop, run in-process against the simulated BMC."""
import json
import random
import threading

import pytest

from superfan import controller as controller_module
from superfan.bmc import MODE_FULL, MODE_OPTIMAL, MODE_STANDARD
from superfan.config import load_config
from superfan.controller import Controller, FatalBmcError

from conftest import Hwmon


def start(cfg, fake, **kwargs):
    ctl = Controller(cfg, fake.client(**kwargs.pop("bmc", {})), **kwargs)
    ctl.session.retry_delay = 0
    ctl.session.takeover()
    return ctl


def duties(fake):
    state = fake.state
    return {"cpu": state["duty"]["1"], "chassis": state["duty"]["0"]}


@pytest.fixture
def three_sources(write_config, hwmon, tmp_path):
    """cpu follows a BMC sensor; chassis follows an NVMe drive (hwmon) and a GPU (command)."""
    nvme = hwmon.add("nvme", "nvme0", {"Composite": 38.0})
    gpu = tmp_path / "gpu"
    gpu.write_text("50\n")

    def mutate(data):
        data["sources"]["command"] = {"gpu0": ["cat", str(gpu)]}
        data["zones"]["chassis"]["inputs"] = [
            {"sensors": ["hwmon:nvme/Composite"], "curve": [[40, 20], [60, 60], [70, 100]]},
            {"sensors": ["command:gpu0"], "curve": [[50, 30], [70, 60], [90, 100]]},
        ]
    return load_config(write_config(hwmon=hwmon, mutate=mutate)), nvme, gpu


def test_each_source_type_drives_fan_duty_through_its_curve(fake, three_sources):
    cfg, nvme, gpu = three_sources
    ctl = start(cfg, fake)
    ctl.cycle()
    assert duties(fake) == {"cpu": 25, "chassis": 30}     # CPU 45 C -> 25 %; GPU 50 C -> 30 % beats NVMe's 20 %

    Hwmon.write(nvme, 1, 55.0)                            # hwmon: 55 C -> 50 %
    ctl.cycle()
    assert duties(fake) == {"cpu": 25, "chassis": 50}

    gpu.write_text("80\n")                                # command: 80 C -> 80 %
    ctl.cycle()
    assert duties(fake) == {"cpu": 25, "chassis": 80}

    fake.set_temp("CPU Temp", 70)                         # ipmi: 70 C -> 70 %
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 70, "chassis": 80}
    assert status["state"] == "normal"
    zones = {z["name"]: z for z in status["zones"]}
    assert zones["chassis"]["driver"] == "command:gpu0" and zones["chassis"]["driver_temp"] == 80
    assert zones["cpu"]["driver"] == "ipmi:CPU Temp"


def test_one_cycle_is_one_sensor_scan(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    fake.clear_log()
    for _ in range(3):
        ctl.cycle()
    assert len(fake.scans()) == 3


def test_duty_is_only_resent_when_it_changes_or_on_the_periodic_reassert(fake, make_cfg):
    ctl = start(make_cfg(), fake, reassert_every=4)
    fake.clear_log()
    for _ in range(4):
        ctl.cycle()
    assert fake.duty_writes() == [(1, 25), (0, 40)]       # cycle 0 only; cycles 1-3 had nothing new to say
    ctl.cycle()                                           # cycle 4: reassert
    assert fake.duty_writes() == [(1, 25), (0, 40), (1, 25), (0, 40)]


def test_by_default_duty_is_reasserted_every_sixth_cycle(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    fake.clear_log()
    for _ in range(7):
        ctl.cycle()
    assert fake.duty_writes() == [(1, 25), (0, 40)] * 2   # cycle 0 and cycle 6


def test_without_the_bmc_source_there_is_no_scan_and_no_fan_monitoring(fake, write_config, hwmon):
    hwmon.add("k10temp", "0000:00:18.3", {"Tctl": 60.0})

    def mutate(data):
        data["sources"]["ipmi"] = False
        data["zones"] = {"cpu": {"bmc_zone": 1, "inputs": [
            {"sensors": "hwmon:k10temp/Tctl", "curve": [[40, 20], [80, 100]]}]}}
    ctl = start(load_config(write_config(hwmon=hwmon, mutate=mutate)), fake)
    status = ctl.cycle()
    assert fake.scans() == [] and status["fans"] == [] and status["state"] == "normal"
    assert fake.state["duty"]["1"] == 60


def test_cooling_down_waits_for_the_hysteresis_band(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    fake.set_temp("CPU Temp", 60)
    ctl.cycle()
    assert duties(fake)["cpu"] == 40
    fake.set_temp("CPU Temp", 58)          # 2 C cooler: inside the 3 C band
    ctl.cycle()
    assert duties(fake)["cpu"] == 40
    fake.set_temp("CPU Temp", 62)          # warmer: followed at once
    ctl.cycle()
    assert duties(fake)["cpu"] == 46
    fake.set_temp("CPU Temp", 50)          # well below: followed
    ctl.cycle()
    assert duties(fake)["cpu"] == 30


def test_calibration_raises_a_zones_minimum_duty(fake, make_cfg):
    calibration = {"cpu": {"bmc_zone": 1, "min_duty": 35, "fans": ["FANA"]},
                   "chassis": {"bmc_zone": 5, "min_duty": 90, "fans": []}}   # made for another BMC zone: ignored
    ctl = start(make_cfg(), fake, calibration=calibration)
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 35, "chassis": 40}     # the curve alone would ask the CPU zone for 25 %
    assert {z["name"]: z["min_duty"] for z in status["zones"]} == {"cpu": 35, "chassis": 0}
    assert status["calibrated"] == ["cpu"]
    assert {f["name"]: f["zone"] for f in status["fans"]}["FANA"] == "cpu"


def test_config_min_duty_applies_without_calibration(fake, make_cfg):
    ctl = start(make_cfg(mutate=lambda d: d["zones"]["cpu"].update(min_duty=33)), fake)
    ctl.cycle()
    assert duties(fake)["cpu"] == 33


def test_a_zone_that_loses_a_required_sensor_runs_at_full_until_it_returns(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    ctl.cycle()
    fake.set_temp("CPU Temp", None)                       # the BMC now says "no reading"
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 100, "chassis": 40}    # only the blind zone fails safe
    assert status["state"] == "failsafe"
    assert status["reasons"] == ["zone cpu: no reading for ipmi:CPU Temp"]
    fake.set_temp("CPU Temp", 45)
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 25, "chassis": 40} and status["state"] == "normal"


def test_an_optional_input_may_be_absent(fake, make_cfg):
    def mutate(data):
        data["zones"]["cpu"]["inputs"].append(
            {"sensors": ["ipmi:GPU9 Temp"], "curve": [[40, 20], [80, 100]], "required": False})
    ctl = start(make_cfg(mutate=mutate), fake)
    assert ctl.cycle()["state"] == "normal"
    assert duties(fake)["cpu"] == 25


def test_a_failed_bmc_scan_fails_safe_only_where_the_bmc_was_needed(fake, write_config, hwmon):
    hwmon.add("nvme", "nvme0", {"Composite": 50.0})

    def mutate(data):
        data["zones"]["chassis"]["inputs"] = [{"sensors": ["hwmon:nvme/*"], "curve": [[40, 20], [60, 60]]}]
    ctl = start(load_config(write_config(hwmon=hwmon, mutate=mutate)), fake)
    fake.update(lambda s: s.update(fail={"sdr": -1}))
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 100, "chassis": 40}
    assert status["state"] == "failsafe" and "ipmi" in status["errors"]


def test_a_critical_temperature_puts_every_zone_at_full_and_holds(fake, make_cfg):
    now = [1000.0]
    cfg = make_cfg(mutate=lambda d: d["safety"].update(fault_hold=60))
    ctl = start(cfg, fake, clock=lambda: now[0])
    ctl.cycle()
    fake.set_temp("CPU Temp", 91)
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 100, "chassis": 100}
    assert status["state"] == "emergency"
    assert status["reasons"] == ["ipmi:CPU Temp is at 91.0°C (critical is 90°C)"]

    fake.set_temp("CPU Temp", 45)
    now[0] += 30                                          # cooled, but still inside the hold
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 100, "chassis": 100} and status["state"] == "emergency"
    assert "holding full speed" in status["reasons"][0]

    now[0] += 31                                          # hold over
    status = ctl.cycle()
    assert status["state"] == "normal" and duties(fake) == {"cpu": 25, "chassis": 40}


def test_a_sensor_the_bmc_itself_flags_critical_is_an_emergency(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    fake.update(lambda s: s.update(temp_status={"Peripheral Temp": "cr"}))
    status = ctl.cycle()
    assert status["state"] == "emergency" and duties(fake) == {"cpu": 100, "chassis": 100}
    assert status["reasons"] == ["the BMC reports Peripheral Temp as critical"]


def test_a_fan_that_stops_puts_every_zone_at_full(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    ctl.cycle()
    fake.update(lambda s: s["fans"]["FAN3"].update(dead=True))
    status = ctl.cycle()
    assert duties(fake) == {"cpu": 100, "chassis": 100}
    assert status["state"] == "failsafe" and status["reasons"] == ["fan FAN3 has stopped"]


def test_a_fan_that_stops_reporting_counts_as_stopped(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    ctl.cycle()
    fake.update(lambda s: s["fans"].__setitem__("FAN3", None))
    assert ctl.cycle()["reasons"] == ["fan FAN3 has stopped"]


def test_an_empty_fan_header_is_not_a_fault(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    status = ctl.cycle()                                  # FANB has never had a reading
    assert status["state"] == "normal" and "FANB" not in [f["name"] for f in status["fans"]]


def test_a_fan_under_the_bmcs_critical_threshold_is_a_fault(fake, make_cfg):
    fake.update(lambda s: s.update(thresholds={"FAN2": [300, 500, 700]}))
    ctl = start(make_cfg(), fake)
    ctl.cycle()                                           # chassis at 40 %: FAN2 at 560 RPM, above 500
    assert duties(fake)["chassis"] == 40
    fake.set_temp("Peripheral Temp", 32)
    fake.set_temp("System Temp", 31)                      # chassis wants 24 %: FAN2 would sit at 336 RPM
    ctl.cycle()
    status = ctl.cycle()                                  # the scan after the slowdown sees it
    assert status["state"] == "failsafe"
    assert status["reasons"] == ["fan FAN2 is below the BMC's critical threshold (336 RPM)"]
    assert duties(fake) == {"cpu": 100, "chassis": 100}


def test_control_is_retaken_if_the_bmc_leaves_full_mode(fake, make_cfg):
    ctl = start(make_cfg(), fake)
    ctl.cycle()
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))    # somebody changed it in the BMC's web UI
    ctl.cycle()
    assert fake.state["mode"] == MODE_FULL and duties(fake) == {"cpu": 25, "chassis": 40}
    ctl.session.release()
    assert fake.state["mode"] == MODE_OPTIMAL             # and that choice is what gets restored


def test_repeated_command_failures_are_fatal(fake, make_cfg):
    ctl = start(make_cfg(), fake, bmc={"retries": 0})
    fake.update(lambda s: s.update(fail={"duty_set": -1}))
    assert ctl.cycle()["state"] == "failsafe"
    assert "cannot command the BMC" in ctl.cycle()["reasons"][-1]
    with pytest.raises(FatalBmcError, match="3 cycles in a row"):
        ctl.cycle()


def test_one_failed_cycle_is_recovered_from(fake, make_cfg):
    ctl = start(make_cfg(), fake, bmc={"retries": 0})
    fake.update(lambda s: s.update(fail={"duty_set": 1}))
    ctl.cycle()
    assert ctl.cycle()["state"] == "normal" and ctl.write_failures == 0
    assert duties(fake) == {"cpu": 25, "chassis": 40}


def test_duty_never_leaves_0_to_100_whatever_the_temperatures(fake, make_cfg):
    rng = random.Random(7)
    ctl = start(make_cfg(), fake)
    fake.clear_log()
    for _ in range(40):
        fake.update(lambda s: s["temps"].update({"CPU Temp": rng.uniform(1, 149), "System Temp": rng.uniform(1, 149)}))
        ctl.cycle()
        assert ctl.write_failures == 0                    # the BMC model rejects any byte above 0x64
    writes = fake.duty_writes()
    assert len(writes) > 40 and all(0 <= duty <= 100 for _, duty in writes)
    assert all(len(command) == 6 for command in fake.raw_log() if command[:3] == [0x30, 0x70, 0x66])


# -- run(): taking control and always giving it back -------------------------

def run_in_thread(ctl, stop):
    result = {}
    thread = threading.Thread(target=lambda: result.update(code=ctl.run(stop)))
    thread.start()
    return thread, result


def test_run_takes_control_and_restores_the_previous_mode_on_stop(fake, make_cfg):
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))
    cfg = make_cfg()
    ctl = Controller(cfg, fake.client())
    stop = threading.Event()
    thread, result = run_in_thread(ctl, stop)
    try:
        from conftest import wait_for
        wait_for(lambda: ctl.cycles >= 1, what="first cycle")
        assert fake.state["mode"] == MODE_FULL
    finally:
        stop.set()
        thread.join(20)
    assert result == {"code": 0}
    assert fake.state["mode"] == MODE_OPTIMAL
    with open(cfg.status_file) as handle:
        final = json.load(handle)
    assert final["running"] is False and final["bmc_mode"] == "BMC control"


def test_exit_mode_can_be_fixed_in_the_config(fake, make_cfg):
    fake.update(lambda s: s.update(mode=MODE_OPTIMAL))
    ctl = Controller(make_cfg(mutate=lambda d: d["bmc"].update(exit_mode="standard")), fake.client())
    stop = threading.Event()
    stop.set()
    assert ctl.run(stop) == 0
    assert fake.state["mode"] == MODE_STANDARD


def test_run_gives_up_and_hands_back_when_the_bmc_stops_obeying(fake, make_cfg):
    cfg = make_cfg()
    cfg.interval = 0.01
    ctl = Controller(cfg, fake.client(retries=0))
    fake.update(lambda s: s.update(fail={"duty_set": -1}))
    assert ctl.run(threading.Event()) == 1
    assert fake.state["mode"] == MODE_STANDARD            # mode changes still worked, so control went back
    assert ctl.cycles == 3


def test_an_unexpected_exception_still_hands_control_back(fake, make_cfg, monkeypatch):
    ctl = Controller(make_cfg(), fake.client())

    def explode(*args, **kwargs):
        assert fake.state["mode"] == MODE_FULL
        raise RuntimeError("bug")
    monkeypatch.setattr(controller_module, "collect", explode)
    with pytest.raises(RuntimeError, match="bug"):
        ctl.run(threading.Event())
    assert fake.state["mode"] == MODE_STANDARD


def test_failing_to_take_control_is_an_error_not_a_crash(fake, make_cfg):
    fake.update(lambda s: s.update(ignore_mode_set=True))
    assert Controller(make_cfg(), fake.client()).run(threading.Event()) == 1
    assert fake.duty_writes() == []


def test_dry_run_decides_but_sends_nothing_and_publishes_nothing(fake, make_cfg, caplog):
    cfg = make_cfg()
    ctl = Controller(cfg, fake.client(dry_run=True))
    with caplog.at_level("INFO"):
        assert ctl.run(threading.Event(), once=True) == 0
    assert fake.state["mode"] == MODE_STANDARD and fake.duty_writes() == []
    assert all(entry[:1] == ["sdr"] or entry == ["raw", "0x30", "0x45", "0x00"] for entry in fake.log)
    assert "zone cpu would run at 25%" in caplog.text and "zone chassis would run at 40%" in caplog.text
    import os
    assert not os.path.exists(cfg.status_file)


def test_published_status_describes_zones_sensors_and_fans(fake, make_cfg):
    cfg = make_cfg()
    ctl = start(cfg, fake)
    ctl.cycle()
    fake.set_temp("CPU Temp", 85)
    ctl.cycle()
    with open(cfg.status_file) as handle:
        status = json.load(handle)
    assert status["running"] and status["state"] == "normal" and status["bmc_mode"] == "Full"
    assert status["interval"] == 1 and status["dry_run"] is False
    sensors = {s["name"]: s for s in status["sensors"]}
    assert sensors["ipmi:CPU Temp"] == {"name": "ipmi:CPU Temp", "value": 85, "zone": "cpu", "level": "hot"}
    assert sensors["ipmi:System Temp"]["level"] == "warm" and sensors["ipmi:System Temp"]["zone"] == "chassis"
    fans = {f["name"]: f for f in status["fans"]}
    assert fans["FANA"]["rpm"] == 910 and fans["FANA"]["status"] == "ok"
