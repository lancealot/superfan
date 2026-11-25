import time

import pytest

from superfan.bmc import MODE_FULL, MODE_OPTIMAL, MODE_STANDARD, Bmc, BmcError, parse_sdr, parse_thresholds


def test_duty_goes_out_as_a_percentage_in_one_byte(fake):
    bmc = fake.client()
    for zone, duty in [(0, 0), (0, 1), (1, 12), (0, 50), (1, 99), (1, 100)]:
        bmc.set_duty(zone, duty)
    assert fake.log == [
        ["raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x00"],
        ["raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x01"],
        ["raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x0c"],
        ["raw", "0x30", "0x70", "0x66", "0x01", "0x00", "0x32"],
        ["raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x63"],
        ["raw", "0x30", "0x70", "0x66", "0x01", "0x01", "0x64"],
    ]
    assert fake.state["duty"] == {"0": 50, "1": 100}


def test_every_percentage_maps_to_the_same_number_as_a_byte(monkeypatch):
    sent = []
    bmc = Bmc(["ipmitool"])
    monkeypatch.setattr(bmc, "_run", lambda args: sent.append(args) or "")
    for duty in range(101):
        bmc.set_duty(0, duty)
    assert [int(args[-1], 16) for args in sent] == list(range(101))
    assert max(int(args[-1], 16) for args in sent) == 0x64


@pytest.mark.parametrize("bad", [-1, 101, 255, 50.0, "50", True, None])
def test_an_invalid_duty_is_refused_before_anything_is_sent(fake, bad):
    with pytest.raises(ValueError):
        fake.client().set_duty(0, bad)
    assert fake.log == []


def test_the_simulated_bmc_rejects_what_superfan_0_1_used_to_send(fake):
    bmc = fake.client(retries=0)
    with pytest.raises(BmcError):   # 0xff was 0.1's "100 %"
        bmc._raw(0x30, 0x70, 0x66, 0x01, 0x00, 0xFF, write=True)
    with pytest.raises(BmcError):   # the extra byte 0.1 sent to X10/X11/X13 boards
        bmc._raw(0x30, 0x70, 0x66, 0x01, 0x00, 0x00, 0x40, write=True)
    assert fake.state["duty"] == {"0": 50, "1": 50}


def test_mode_is_set_then_read_back(fake):
    bmc = fake.client()
    assert bmc.get_mode() == MODE_STANDARD
    bmc.set_mode(MODE_FULL)
    assert fake.log == [["raw", "0x30", "0x45", "0x00"], ["raw", "0x30", "0x45", "0x01", "0x01"],
                        ["raw", "0x30", "0x45", "0x00"]]
    assert fake.state["mode"] == MODE_FULL
    assert bmc.get_duty(0) == 100  # the BMC puts fans at full on entering Full mode


def test_a_mode_change_the_bmc_ignored_is_an_error(fake):
    fake.update(lambda s: s.update(ignore_mode_set=True))
    with pytest.raises(BmcError, match="asked for Optimal mode but the BMC reports Standard"):
        fake.client().set_mode(MODE_OPTIMAL)


def test_a_hung_ipmitool_times_out_instead_of_blocking(fake):
    fake.update(lambda s: s.update(hang={"mode_get": 30}))
    started = time.monotonic()
    with pytest.raises(BmcError, match="timed out"):
        fake.client(timeout=0.5, retries=0).get_mode()
    assert time.monotonic() - started < 10


def test_transient_failures_are_retried(fake):
    fake.update(lambda s: s.update(fail={"duty_set": 2}))
    fake.client(retries=2).set_duty(0, 30)
    assert fake.state["duty"]["0"] == 30
    assert len(fake.log) == 3


def test_persistent_failure_raises_with_ipmitools_message(fake):
    fake.update(lambda s: s.update(fail={"duty_set": -1}))
    with pytest.raises(BmcError, match="Device or resource busy"):
        fake.client(retries=1).set_duty(0, 30)
    assert len(fake.log) == 2


def test_missing_ipmitool_is_reported_plainly():
    with pytest.raises(BmcError, match="command not found"):
        Bmc(["/nonexistent/ipmitool"], retries=0).get_mode()


def test_dry_run_reads_but_never_writes(fake):
    bmc = fake.client(dry_run=True)
    assert bmc.get_mode() == MODE_STANDARD
    bmc.set_mode(MODE_FULL)
    bmc.set_duty(0, 10)
    assert fake.log == [["raw", "0x30", "0x45", "0x00"]]
    assert fake.state["mode"] == MODE_STANDARD


def test_scan_is_a_single_ipmitool_call(fake):
    rows = fake.client().scan()
    assert fake.log == [["sdr", "list", "full"]]
    by_name = {row.name: row for row in rows}
    assert (by_name["CPU Temp"].value, by_name["CPU Temp"].unit) == (45.0, "C")
    assert (by_name["FAN2"].value, by_name["FAN2"].unit) == (700.0, "RPM")   # BMC's own 50 % of 1400
    assert by_name["FANB"].value is None and by_name["FANB"].status == "ns"
    assert by_name["12V"].unit == ""


def test_parse_sdr_copes_with_real_world_noise():
    text = """
CPU Temp         | 45 degrees C      | ok
P1_DIMMA~D Temp  | 38.500 degrees C  | ok
M2NVMeSSD Temp1  | no reading        | ns
FAN1             | 1400 RPM          | ok
FAN3             | 0 RPM             | cr
FANB             | no reading        | ns
12V              | 12.19 Volts       | ok
Chassis Intru    | 0x00              | ok
Received a response with unexpected ID 12 vs. 13
garbage without separators
"""
    rows = {row.name: row for row in parse_sdr(text)}
    assert len(rows) == 8
    assert rows["P1_DIMMA~D Temp"].value == 38.5
    assert rows["M2NVMeSSD Temp1"].value is None
    assert (rows["FAN3"].value, rows["FAN3"].unit, rows["FAN3"].status) == (0.0, "RPM", "cr")
    assert rows["Chassis Intru"].unit == ""


def test_fan_thresholds_come_from_ipmitool_sensor(fake):
    fake.update(lambda s: s.update(thresholds={"FAN1": [300, 500, 700]}))
    thresholds = fake.client().thresholds()
    assert thresholds["FAN1"] == {"lnr": 300.0, "lcr": 500.0, "lnc": 700.0}
    assert thresholds["FAN2"] == {"lnr": 100.0, "lcr": 200.0, "lnc": 300.0}
    assert "CPU Temp" not in thresholds and "FANB" not in thresholds


def test_parse_thresholds_skips_unset_values():
    text = "FAN1 | 1400.000 | RPM | ok | na | 500.000 | 700.000 | 25300.000 | 25400.000 | 25500.000\n"
    assert parse_thresholds(text) == {"FAN1": {"lcr": 500.0, "lnc": 700.0}}
