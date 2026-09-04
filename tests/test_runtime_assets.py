from configparser import ConfigParser
from pathlib import Path
import re
import shlex

import pytest


@pytest.fixture
def bot_unit():
    unit = ConfigParser(interpolation=None)
    unit.optionxform = str
    path = Path(__file__).resolve().parents[1] / "deploy/systemd/honeybuy-tg.service"
    with path.open(encoding="utf-8") as stream:
        unit.read_file(stream)
    return unit


def _duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|sec|min|m|h)?", value)
    assert match is not None, f"expected a finite systemd duration, got {value!r}"
    multiplier = {
        None: 1.0,
        "ms": 0.001,
        "s": 1.0,
        "sec": 1.0,
        "m": 60.0,
        "min": 60.0,
        "h": 3600.0,
    }[match.group(2)]
    return float(match.group(1)) * multiplier


def test_bot_unit_runs_release_local_health_and_app_with_a_strict_sandbox(bot_unit):
    service = bot_unit["Service"]

    assert service["User"] == service["Group"] == "honeybuy"
    assert service["EnvironmentFile"] == "/etc/honeybuy-tg/env"
    assert service["WorkingDirectory"] == "/opt/honeybuy-tg/current"
    assert shlex.split(service["ExecStartPre"]) == [
        "/opt/honeybuy-tg/current/.venv/bin/python",
        "-m",
        "honeybuy_tg",
        "healthcheck",
    ]
    assert shlex.split(service["ExecStart"]) == [
        "/opt/honeybuy-tg/current/.venv/bin/python",
        "-m",
        "honeybuy_tg",
    ]
    for directive in (
        "NoNewPrivileges",
        "PrivateTmp",
        "ProtectHome",
        "RestrictSUIDSGID",
    ):
        assert service.getboolean(directive)
    assert service["ProtectSystem"] == "strict"
    assert service["CapabilityBoundingSet"] == ""
    assert set(shlex.split(service["ReadWritePaths"])) == {"/var/lib/honeybuy-tg"}
    assert set(shlex.split(service["RestrictAddressFamilies"])) == {
        "AF_UNIX",
        "AF_INET",
        "AF_INET6",
    }


def test_bot_unit_has_bounded_start_stop_and_restart_behavior(bot_unit):
    service = bot_unit["Service"]

    assert 0 < _duration_seconds(service["TimeoutStartSec"]) <= 300
    assert 0 < _duration_seconds(service["TimeoutStopSec"]) <= 300
    assert 0 < _duration_seconds(service["RestartSec"]) <= 60
    assert service["Restart"] == "on-failure"
    assert 1 <= int(bot_unit["Unit"]["StartLimitBurst"]) <= 10
    assert 0 < _duration_seconds(bot_unit["Unit"]["StartLimitIntervalSec"]) <= 900
