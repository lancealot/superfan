import os
import sys

import pytest

from superfan.sources import Reading, SourceError, collect, read_command, read_hwmon


def names(readings):
    return sorted(r.name for r in readings)


def test_hwmon_names_are_chip_at_device_slash_label(hwmon):
    hwmon.add("k10temp", "0000:00:18.3", {"Tctl": 46.125, "Tccd1": 44.0})
    hwmon.add("nvme", "nvme0", {"Composite": 38.85, "Sensor 1": 40.0})
    hwmon.add("nvme", "nvme1", {"Composite": 51.0})
    hwmon.add("amdgpu", "0000:0a:00.0", {"edge": 61.0, "junction": 67.0})
    hwmon.add("acpitz", "thermal_zone0", {None: 27.8})
    readings = read_hwmon(hwmon.root)
    assert names(readings) == [
        "hwmon:acpitz@thermal_zone0/temp1",
        "hwmon:amdgpu@0000:0a:00.0/edge", "hwmon:amdgpu@0000:0a:00.0/junction",
        "hwmon:k10temp@0000:00:18.3/Tccd1", "hwmon:k10temp@0000:00:18.3/Tctl",
        "hwmon:nvme@nvme0/Composite", "hwmon:nvme@nvme0/Sensor 1", "hwmon:nvme@nvme1/Composite",
    ]
    values = {r.name: r.value for r in readings}
    assert values["hwmon:k10temp@0000:00:18.3/Tctl"] == pytest.approx(46.125)


def test_patterns_may_omit_the_device_and_use_wildcards(hwmon):
    hwmon.add("nvme", "nvme0", {"Composite": 38.0})
    hwmon.add("nvme", "nvme1", {"Composite": 51.0})
    hwmon.add("k10temp", "0000:00:18.3", {"Tctl": 46.0})
    readings = read_hwmon(hwmon.root)

    def match(pattern):
        return names(r for r in readings if r.matches(pattern))

    both = ["hwmon:nvme@nvme0/Composite", "hwmon:nvme@nvme1/Composite"]
    assert match("hwmon:nvme/Composite") == both
    assert match("hwmon:nvme/*") == both
    assert match("hwmon:nvme@nvme1/Composite") == ["hwmon:nvme@nvme1/Composite"]
    assert match("HWMON:K10TEMP/tctl") == ["hwmon:k10temp@0000:00:18.3/Tctl"]
    assert match("hwmon:k10temp/Tdie") == []
    assert match("ipmi:*") == []


def test_hwmon_skips_unreadable_and_implausible_sensors(hwmon):
    directory = hwmon.add("nct6798", "nct6775.656", {"SYSTIN": 34.0, "AUXTIN0": 0.0, "AUXTIN1": 215.0, "PECI": -40.0})
    with open(os.path.join(directory, "temp5_input"), "w") as handle:
        handle.write("not a number\n")
    os.mkdir(os.path.join(directory, "temp6_input"))   # reading it raises an OSError, like a sleeping drive
    assert names(read_hwmon(hwmon.root)) == ["hwmon:nct6798@nct6775.656/SYSTIN"]


def test_missing_hwmon_root_is_an_error(tmp_path):
    with pytest.raises(SourceError, match="does not exist"):
        read_hwmon(str(tmp_path / "nope"))


def py(code):
    return [sys.executable, "-c", code]


def test_command_printing_one_number():
    assert read_command("gpu0", py("print(61)")) == [Reading("command:gpu0", 61.0)]
    assert read_command("gpu0", py("print('GPU temperature: 58.5 C')")) == [Reading("command:gpu0", 58.5)]


def test_command_printing_one_number_per_line_gets_an_index_and_a_shared_alias():
    readings = read_command("gpu", py("print('61'); print(); print('72')"))
    assert [(r.name, r.value) for r in readings] == [("command:gpu/0", 61.0), ("command:gpu/1", 72.0)]
    assert all(r.matches("command:gpu") for r in readings)
    assert [r.name for r in readings if r.matches("command:gpu/1")] == ["command:gpu/1"]


@pytest.mark.parametrize("argv, message", [
    (["/nonexistent/nvidia-smi"], "command not found"),
    (py("import sys; print('NVIDIA-SMI has failed', file=sys.stderr); sys.exit(9)"), "NVIDIA-SMI has failed"),
    (py("import sys; sys.exit(3)"), "exit status 3"),
    (py("print('N/A')"), "no number in output"),
    (py("pass"), "printed nothing"),
])
def test_command_failures_are_errors(argv, message):
    with pytest.raises(SourceError, match=message):
        read_command("gpu0", argv)


def test_command_that_hangs_times_out():
    with pytest.raises(SourceError, match="timed out"):
        read_command("gpu0", py("import time; time.sleep(30)"), timeout=0.3)


def test_implausible_command_output_is_dropped():
    assert read_command("gpu0", py("print(0)")) == []


def test_collect_reads_all_three_sources(fake, hwmon, make_cfg, tmp_path):
    hwmon.add("k10temp", "0000:00:18.3", {"Tctl": 46.0})
    gpu = tmp_path / "gpu"
    gpu.write_text("63\n")
    cfg = make_cfg(hwmon=hwmon, mutate=lambda d: d["sources"].update(command={"gpu0": ["cat", str(gpu)]}))
    snap = collect(cfg, fake.client())
    temps = {r.name: r.value for r in snap.temps}
    assert temps["ipmi:CPU Temp"] == 45 and temps["command:gpu0"] == 63
    assert temps["hwmon:k10temp@0000:00:18.3/Tctl"] == 46
    assert "ipmi:M2NVMeSSD Temp1" not in temps          # "no reading" on the BMC
    assert "ipmi:12V" not in temps                      # not a temperature
    assert [f.name for f in snap.fans] == ["FAN1", "FAN2", "FAN3", "FAN4", "FAN5", "FANA"]
    assert snap.errors == {} and snap.bmc_critical == []
    assert fake.log == [["sdr", "list", "full"]]


def test_a_failing_source_does_not_hide_the_others(fake, hwmon, make_cfg):
    hwmon.add("k10temp", "0000:00:18.3", {"Tctl": 46.0})
    cfg = make_cfg(hwmon=hwmon, mutate=lambda d: d["sources"].update(command={"gpu0": ["/nonexistent/nvidia-smi"]}))
    fake.update(lambda s: s.update(fail={"sdr": -1}))
    snap = collect(cfg, fake.client())
    assert [r.name for r in snap.temps] == ["hwmon:k10temp@0000:00:18.3/Tctl"]
    assert set(snap.errors) == {"ipmi", "command:gpu0"}
    assert "Device or resource busy" in snap.errors["ipmi"]


def test_a_fan_that_goes_silent_is_only_reported_if_it_was_known(fake, make_cfg):
    cfg = make_cfg()
    fake.update(lambda s: s["fans"].__setitem__("FAN3", None))
    assert "FAN3" not in [f.name for f in collect(cfg, fake.client()).fans]
    silent = {f.name: f for f in collect(cfg, fake.client(), known_fans={"FAN3"}).fans}
    assert silent["FAN3"].rpm is None


def test_bmc_critical_flags_are_passed_on(fake, make_cfg):
    fake.update(lambda s: s.update(temp_status={"CPU Temp": "cr"}))
    assert collect(make_cfg(), fake.client()).bmc_critical == ["CPU Temp"]
