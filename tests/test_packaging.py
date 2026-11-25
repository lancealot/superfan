"""Packaging and documentation stay in step with the program."""
import configparser
import os
import re
import shlex
import subprocess

import pytest
import yaml

import superfan
from superfan.cli import COMMANDS, build_parser
from superfan.config import parse_config
from superfan.sources import COMMAND_TIMEOUT

from conftest import REPO


def read(name):
    with open(os.path.join(REPO, name), encoding="utf-8") as handle:
        return handle.read()


def test_the_version_is_the_same_everywhere():
    pyproject = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M).group(1)
    spec = re.search(r"^Version:\s+(\S+)", read("superfan.spec"), re.M).group(1)
    changelog = re.search(r"^## \[([^\]]+)\]", read("CHANGELOG.md"), re.M).group(1)
    assert pyproject == spec == changelog == superfan.__version__


@pytest.fixture(scope="module")
def unit():
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(read("superfan.service"))
    return parser


def test_the_unit_hands_fans_back_after_any_stop(unit):
    service = unit["Service"]
    assert service["ExecStart"] == "/usr/bin/superfan run"
    assert service["ExecStopPost"] == "-/usr/bin/superfan release"
    assert service["Type"] == "notify"                       # the daemon reports READY once it has control
    assert int(service["WatchdogSec"]) >= 60                  # and pings the watchdog every cycle
    assert service["Restart"] == "on-failure" and service["RestartPreventExitStatus"] == "78"
    assert service["RuntimeDirectory"] == "superfan" and service["RuntimeDirectoryPreserve"] == "yes"
    assert unit["Install"]["WantedBy"] == "multi-user.target"


def test_the_unit_and_the_program_agree_on_the_config_error_status():
    from superfan.cli import EXIT_CONFIG
    assert EXIT_CONFIG == 78


def test_the_spec_does_not_start_anything_or_own_user_data():
    spec = read("superfan.spec").split("%changelog")[0]
    assert not re.search(r"systemctl\s+(--\S+\s+)*(start|enable)", spec)
    assert "%config(noreplace) %{_sysconfdir}/superfan/config.yaml" in spec
    assert "calibration.yaml" not in spec
    assert re.search(r"^Requires:\s+python3-pyyaml$", spec, re.M) and re.search(r"^Requires:\s+ipmitool$", spec, re.M)
    assert "python3-pip" not in re.findall(r"^Requires:\s+(\S+)", spec, re.M)
    assert not re.search(r"python3\.\d+", spec)               # no hard-coded interpreter version
    assert "/usr/local" not in spec
    assert "%systemd_post superfan.service" in spec and "%systemd_preun superfan.service" in spec


@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh", "buildrpm.sh"])
def test_shell_scripts_parse_and_are_executable(script):
    path = os.path.join(REPO, script)
    assert subprocess.run(["bash", "-n", path]).returncode == 0
    assert os.access(path, os.X_OK)


def test_install_script_never_overwrites_a_config_or_starts_the_service():
    script = read("install.sh")
    assert 'if [ ! -f "$ETC/config.yaml" ]' in script and "config.yaml.new" in script
    commands = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith(("echo", "#")))
    assert not re.search(r"systemctl\s+(start|enable)", commands)


# -- the README ----------------------------------------------------------------

def code_blocks(language):
    return re.findall(r"```%s\n(.*?)```" % language, read("README.md"), re.S)


def superfan_command_lines():
    lines = []
    for block in code_blocks("bash"):
        for line in block.splitlines():
            words = shlex.split(line, comments=True)
            if words[:1] == ["sudo"]:
                words = words[1:]
            if words[:1] == ["superfan"]:
                lines.append(words[1:])
    for inline in re.findall(r"`(superfan [^`]+)`", read("README.md")):
        lines.append(shlex.split(inline)[1:])
    return lines


def test_every_superfan_command_line_in_the_readme_is_accepted():
    lines = superfan_command_lines()
    assert len(lines) >= 15
    parser = build_parser()
    for words in lines:
        words = ["cpu" if w == "ZONE" else "40" if w in ("DUTY", "N") else w for w in words]
        try:
            parser.parse_args(words)
        except SystemExit:
            pytest.fail("the README shows `superfan %s`, which the program rejects" % " ".join(words))


def test_every_command_and_option_is_documented():
    text = read("README.md")
    for command in COMMANDS:
        assert "`superfan %s" % command in text, "the README does not mention `superfan %s`" % command
    options = set()
    for action in build_parser()._subparsers._group_actions[0].choices.values():
        options.update(opt for a in action._actions for opt in a.option_strings if opt.startswith("--"))
    for option in sorted(options - {"--help", "--verbose", "--config"}):
        assert option in text, "the README does not mention %s" % option


def test_the_readmes_example_config_is_valid_and_matches_the_documented_defaults():
    examples = [yaml.safe_load(block) for block in code_blocks("yaml")]
    full = [e for e in examples if isinstance(e, dict) and "zones" in e]
    assert len(full) == 1
    cfg = parse_config(full[0], "/etc/superfan/config.yaml")
    assert set(cfg.zones) == {"cpu", "chassis"} and cfg.commands["gpu0"][0] == "nvidia-smi"
    defaults = parse_config({"zones": full[0]["zones"], "sources": full[0]["sources"]}, "/etc/superfan/config.yaml")
    for field in ("interval", "hysteresis", "failsafe_duty", "fault_hold", "max_failures"):
        assert getattr(cfg, field) == getattr(defaults, field), field
    assert (cfg.bmc.command, cfg.bmc.timeout, cfg.bmc.exit_mode) == (
        defaults.bmc.command, defaults.bmc.timeout, defaults.bmc.exit_mode)


def test_numbers_quoted_in_the_readme_match_the_code():
    from superfan.calibrate import Settings
    from superfan.controller import Controller
    import inspect
    text = read("README.md")
    assert COMMAND_TIMEOUT == 10 and "more than\n10 seconds" in text
    assert Settings().step == 10 and Settings().floor == 10 and Settings().margin == 1.15
    assert "within 15 % of the BMC's" in text
    assert inspect.signature(Controller).parameters["reassert_every"].default == 6 and "every six cycles" in text


def test_files_the_readme_points_at_exist():
    for target in re.findall(r"\]\(([^)#]+)\)", read("README.md")):
        assert os.path.exists(os.path.join(REPO, target)), target
    assert not os.path.exists(os.path.join(REPO, "setup.py"))
