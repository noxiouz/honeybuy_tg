import base64
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from honeybuy_tg.app import main
from honeybuy_tg.migrations import CURRENT_SCHEMA_VERSION, migrate_database_path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "astral-sh/setup-uv": "d0cc045d04ccac9d8b7881df0226f9e82c39688e",
}
EXPECTED_SIGNER_FINGERPRINT = (
    "SHA256:mktJ6te9V48RCf8Aw5+ihHZ6wdUTvjQX++rWA6jxxbY"
)
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
EXPECTED_UV_SHA256 = "0c6bab77a67a445dc849ed5e8ee8d3cb333b6e2eba863643ce1e228075f27943"
BOOTSTRAP_PHASES = [
    "intent",
    "release_prepared",
    "current_linked",
    "state_written",
    "awaiting_service",
]
_SECRET_VALUES = ("telegram-secret", "openai-secret")


def _repo_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _yaml_block(source: str, key: str, indent: int) -> str:
    lines = source.splitlines()
    prefix = " " * indent
    matches = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(rf"{re.escape(prefix + key)}:\s*", line)
    ]
    assert len(matches) == 1, f"expected one {key!r} mapping at indent {indent}"
    start = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent:
            end = index
            break
    return "\n".join(lines[start + 1 : end])


def _yaml_scalar(value: str) -> str:
    value = value.split(" #", maxsplit=1)[0].strip()
    return value.strip("\"'")


def _workflow_steps(source: str, step_kind: str) -> list[tuple[str, str]]:
    lines = source.splitlines()
    steps: list[tuple[str, str]] = []
    pattern = re.compile(rf"^(?P<space>\s*)-\s+{step_kind}:\s*(?P<value>.*)$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        indent = len(match.group("space"))
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            next_line = lines[next_index]
            if not next_line.strip():
                continue
            next_indent = len(next_line) - len(next_line.lstrip())
            if next_indent <= indent:
                end = next_index
                break
        steps.append(
            (
                _yaml_scalar(match.group("value")),
                "\n".join(lines[index + 1 : end]),
            )
        )
    return steps


def _workflow_step_blocks(source: str) -> list[str]:
    lines = source.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^      -\s+(?:name|uses|run):", line)
    ]
    blocks: list[str] = []
    for start_index, start in enumerate(starts):
        end = starts[start_index + 1] if start_index + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]))
    return blocks


def _workflow_run_steps(source: str) -> list[tuple[str, str]]:
    steps: list[tuple[str, str]] = []
    for block in _workflow_step_blocks(source):
        match = re.search(r"(?m)^\s+(?:-\s+)?run:\s*(.*?)\s*$", block)
        if match is not None:
            steps.append((_yaml_scalar(match.group(1)), block))
    return steps


def _unit_sections(relative_path: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    section: str | None = None
    for raw_line in _repo_text(relative_path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        section_match = re.fullmatch(r"\[([^]]+)]", line)
        if section_match is not None:
            section = section_match.group(1)
            sections.setdefault(section, {})
            continue
        assert section is not None and "=" in line, f"invalid unit line: {raw_line}"
        key, value = line.split("=", maxsplit=1)
        sections[section].setdefault(key.strip(), []).append(value.strip())
    return sections


def _unit_value(
    sections: dict[str, dict[str, list[str]]],
    section: str,
    directive: str,
) -> str:
    values = sections.get(section, {}).get(directive, [])
    assert len(values) == 1, (
        f"expected exactly one [{section}] {directive}, found {values!r}"
    )
    return values[0]


def _assert_unit_boolean(
    sections: dict[str, dict[str, list[str]]],
    section: str,
    directive: str,
) -> None:
    assert _unit_value(sections, section, directive).casefold() in {"yes", "true"}


def _systemd_duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|sec|min|m|h)?", value)
    assert match is not None, f"expected a finite simple systemd duration, got {value!r}"
    multipliers = {
        None: 1.0,
        "ms": 0.001,
        "s": 1.0,
        "sec": 1.0,
        "m": 60.0,
        "min": 60.0,
        "h": 3600.0,
    }
    return float(match.group(1)) * multipliers[match.group(2)]


def _shell_logical_lines(source: str) -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw_line in source.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
            continue
        logical.append((pending + stripped).strip())
        pending = ""
    assert not pending, "installer ends with an incomplete line continuation"
    return logical


def _shell_assignment(source: str, name: str) -> str:
    matches = re.findall(rf"(?m)^{re.escape(name)}=([^\n]+)$", source)
    assert len(matches) == 1, f"expected one fixed {name} assignment"
    return _yaml_scalar(matches[0])


def _expand_shell_constants(
    source: str,
    value: str,
    *,
    preserve: frozenset[str] = frozenset(),
) -> str:
    assignments = {
        name: _yaml_scalar(raw_value)
        for name, raw_value in re.findall(
            r"(?m)^([A-Z][A-Z0-9_]*)=([^\n]+)$",
            source,
        )
    }
    expanded = value
    for _attempt in range(len(assignments) + 1):
        previous = expanded

        def substitute(match: re.Match[str]) -> str:
            name = match.group("braced") or match.group("plain")
            if name in preserve:
                return match.group(0)
            replacement = assignments.get(name)
            if replacement is None or "$(" in replacement:
                return match.group(0)
            return replacement

        expanded = re.sub(
            r"\$\{(?P<braced>[A-Z][A-Z0-9_]*)}|"
            r"\$(?P<plain>[A-Z][A-Z0-9_]*)",
            substitute,
            expanded,
        )
        if expanded == previous:
            break
    return expanded


def _shell_functions(source: str) -> dict[str, str]:
    return {
        match.group("name"): match.group("body")
        for match in re.finditer(
            r"(?ms)^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{\s*\n"
            r"(?P<body>.*?)^\}",
            source,
        )
    }


def _install_line_for(source: str, target: str, *, directory: bool) -> list[str]:
    matches: list[str] = []
    for line in _shell_logical_lines(source):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if "install" not in [Path(word).name for word in words]:
            continue
        is_directory = "-d" in words or "--directory" in words
        if is_directory == directory and target in words:
            matches.append(line)
    return matches


def _assert_install_ownership(
    source: str,
    target: str,
    *,
    owner: str,
    group: str,
    directory: bool,
    mode: int | None = None,
) -> None:
    matches = _install_line_for(source, target, directory=directory)
    assert matches, f"missing install declaration for {target}"
    for line in matches:
        words = shlex.split(line, comments=True)
        owner_matches = (
            any(
                word in {f"--owner={owner}", f"-o{owner}"}
                for word in words
            )
            or any(
                words[index] in {"-o", "--owner"}
                and words[index + 1] == owner
                for index in range(len(words) - 1)
            )
        )
        group_matches = (
            any(
                word in {f"--group={group}", f"-g{group}"}
                for word in words
            )
            or any(
                words[index] in {"-g", "--group"}
                and words[index + 1] == group
                for index in range(len(words) - 1)
            )
        )
        assert owner_matches and group_matches, line
        if mode is not None:
            mode_values = [
                words[index + 1]
                for index in range(len(words) - 1)
                if words[index] in {"-m", "--mode"}
            ] + [
                word.split("=", maxsplit=1)[1]
                for word in words
                if word.startswith("--mode=")
            ]
            assert len(mode_values) == 1 and int(mode_values[0], base=8) == mode


def _line_has_shell_target(line: str, alternatives: tuple[str, ...]) -> bool:
    try:
        words = shlex.split(line, comments=True)
    except ValueError:
        return False
    return any(target in words for target in alternatives)


def _install_targets(line: str) -> list[str]:
    words = shlex.split(line, comments=True)
    install_index = next(
        index for index, word in enumerate(words) if Path(word).name == "install"
    )
    targets: list[str] = []
    index = install_index + 1
    options_with_values = {"-g", "-m", "-o", "--group", "--mode", "--owner"}
    flag_options = {"-D", "-d", "-n", "--directory", "--no-clobber"}
    while index < len(words):
        word = words[index]
        if word in options_with_values:
            index += 2
            continue
        if word in flag_options or word.startswith(("--group=", "--mode=", "--owner=")):
            index += 1
            continue
        if word == "--":
            index += 1
            continue
        targets.append(word)
        index += 1
    return targets


def _coherence_function_names(source: str) -> set[str]:
    return {
        name
        for name, body in _shell_functions(source).items()
        if "coher" in name.casefold()
        or (
            ("deployed-sha" in body or "DEPLOYED_STATE" in body)
            and "current" in body
            and ".ready.json" in body
        )
    }


def _coherence_branch(source: str) -> tuple[list[str], int, int, int]:
    lines = _shell_logical_lines(source)
    coherence_names = _coherence_function_names(source)
    assert len(coherence_names) == 1
    coherence_name = coherence_names.pop()
    start = next(
        index
        for index, line in enumerate(lines)
        if re.match(rf"^if\s+{re.escape(coherence_name)}\b", line)
    )
    depth = 0
    else_index: int | None = None
    end: int | None = None
    for index in range(start, len(lines)):
        line = lines[index]
        if re.match(r"^if\b", line):
            depth += 1
        elif re.match(r"^else\b", line) and depth == 1:
            else_index = index
        elif re.match(r"^fi\b", line):
            depth -= 1
            if depth == 0:
                end = index
                break
    assert else_index is not None and end is not None
    return lines, start, else_index, end


def _bootstrap_marker_parser(source: str) -> str:
    commands: list[list[str]] = []
    for body in _shell_functions(source).values():
        for line in _shell_logical_lines(body):
            words = shlex.split(line, comments=True)
            if (
                words
                and Path(words[0]).name.startswith("python3")
                and "-c" in words
                and "$BOOTSTRAP_JOURNAL_FILE" in words
            ):
                commands.append(words)
    assert len(commands) == 1
    words = commands[0]
    script_index = words.index("-c") + 1
    assert words[script_index + 1 :] == [
        "$BOOTSTRAP_JOURNAL_FILE",
        "$deployed_sha",
    ]
    return words[script_index]


def _configure_healthcheck(
    monkeypatch,
    tmp_path: Path,
    database_path: Path,
    *,
    include_unrelated_secrets: bool = False,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    if include_unrelated_secrets:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _SECRET_VALUES[0])
        monkeypatch.setenv("OPENAI_API_KEY", _SECRET_VALUES[1])
    else:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)


def _run_failing_healthcheck(capsys) -> str:
    with pytest.raises(SystemExit) as exit_info:
        main(["healthcheck"])

    assert exit_info.value.code not in (None, 0)
    captured = capsys.readouterr()
    message = "\n".join(
        part for part in (captured.out, captured.err, str(exit_info.value)) if part
    )
    assert message
    for secret in _SECRET_VALUES:
        assert secret not in message
    return message


def test_ci_workflow_has_stable_read_only_main_and_pull_request_identity():
    workflow = _repo_text(".github/workflows/tests.yml")

    assert re.findall(r"(?m)^name:\s*([^#\n]+)", workflow) == ["Tests"]
    permissions = _yaml_block(workflow, "permissions", 0)
    permission_entries = re.findall(r"(?m)^  ([\w-]+):\s*([^#\n]+)", permissions)
    assert permission_entries == [("contents", "read")]

    triggers = _yaml_block(workflow, "on", 0)
    assert re.search(r"(?m)^  pull_request:\s*$", triggers)
    assert "pull_request_target:" not in triggers
    push = _yaml_block(workflow, "push", 2)
    assert re.search(
        r"(?m)^    branches:\s*(?:\[\s*main\s*]|\n\s+-\s+main\s*)$",
        push,
    )

    offline_job = _yaml_block(workflow, "offline", 2)
    timeout_match = re.search(r"(?m)^    timeout-minutes:\s*(\d+)\s*$", offline_job)
    assert timeout_match is not None
    assert 1 <= int(timeout_match.group(1)) <= 60
    assert "${{ secrets." not in offline_job


def test_ci_workflow_pins_actions_and_does_not_persist_checkout_credentials():
    workflow = _repo_text(".github/workflows/tests.yml")
    action_steps = _workflow_steps(workflow, "uses")
    actions: dict[str, tuple[str, str]] = {}
    for reference, block in action_steps:
        action, separator, revision = reference.partition("@")
        assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), reference
        actions[action] = (revision, block)

    assert len(action_steps) == len(EXPECTED_ACTIONS)
    assert set(actions) == set(EXPECTED_ACTIONS)
    for action, expected_revision in EXPECTED_ACTIONS.items():
        assert actions[action][0] == expected_revision

    checkout_block = actions["actions/checkout"][1]
    assert re.search(
        r"(?m)^\s+persist-credentials:\s*(?:false|\"false\"|'false')\s*$",
        checkout_block,
    )
    assert re.search(
        r"(?m)^\s+python-version:\s*(?:3\.13|\"3\.13\"|'3\.13')\s*$",
        actions["actions/setup-python"][1],
    )
    assert re.search(
        r"(?m)^\s+version:\s*(?:0\.11\.6|\"0\.11\.6\"|'0\.11\.6')\s*$",
        actions["astral-sh/setup-uv"][1],
    )


def test_ci_workflow_runs_full_frozen_gates_and_checks_committed_content():
    workflow = _repo_text(".github/workflows/tests.yml")
    run_steps = _workflow_run_steps(workflow)
    commands = [command for command, _block in run_steps]

    expected_without_containment = [
        "uv sync --frozen",
        "uv run pytest -q",
        "uv run ruff check .",
    ]
    if len(commands) == 4:
        # The dedicated workflow contract below owns the missing-step failure.
        # Keeping this branch makes that one failure diagnostic; once the step
        # exists, the five-step ordering becomes mandatory here.
        assert commands[:3] == expected_without_containment
    else:
        assert len(commands) == 5
        assert commands[:4] == [
            "uv sync --frozen",
            "uv run pytest -q",
            "|",
            "uv run ruff check .",
        ]
        assert (
            "test_linux_transient_runner_contains_setsid_descendants"
            in run_steps[2][1]
        )
    assert "git diff --check" not in commands
    committed_checks = [
        command for command in commands if command.startswith("git diff --check ")
    ]
    assert committed_checks == [commands[-1]]
    for command in committed_checks:
        words = shlex.split(command)
        assert words[:3] == ["git", "diff", "--check"]
        assert words[3:5] == [EMPTY_TREE_SHA, "HEAD"], command
        assert "HEAD^" not in words


def test_ci_workflow_runs_systemd_containment_as_root_only_for_trusted_events():
    workflow = _repo_text(".github/workflows/tests.yml")
    offline_job = _yaml_block(workflow, "offline", 2)
    assert re.search(r"(?m)^    runs-on:\s*ubuntu-latest\s*$", offline_job)
    assert not re.search(r"(?m)^    if:\s*", offline_job)

    step_blocks = _workflow_step_blocks(workflow)
    containment_name = "test_linux_transient_runner_contains_setsid_descendants"
    containment_steps = [
        block for block in step_blocks if containment_name in block
    ]
    assert len(containment_steps) == 1
    containment = containment_steps[0]
    conditional_steps = [
        block for block in step_blocks if re.search(r"(?m)^\s+if:\s*", block)
    ]
    assert conditional_steps == [containment]

    condition_match = re.search(r"(?m)^\s+if:\s*(.+?)\s*$", containment)
    assert condition_match is not None
    condition = condition_match.group(1).strip()
    if condition.startswith("${{") and condition.endswith("}}"):
        condition = condition[3:-2].strip()
    assert condition in {
        "github.event_name == 'push' || "
        "github.event.pull_request.head.repo.full_name == github.repository",
        "github.event_name == 'push' || (github.event_name == 'pull_request' && "
        "github.event.pull_request.head.repo.full_name == github.repository)",
    }

    assert re.search(r"(?m)^\s+(?:test|\[\[?).*uname\s+-s.*Linux.*$", containment)
    assert re.search(
        r"(?m)^\s+test\s+-[efr]\s+/sys/fs/cgroup/cgroup\.controllers\s*$",
        containment,
    )
    assert re.search(
        r"(?m)^\s+(?:test|\[\[?).*/proc/1/comm.*systemd.*$",
        containment,
    )
    assert re.search(r"(?m)^\s+command -v systemd-run\s*$", containment)
    assert re.search(r"(?m)^\s+command -v systemctl\s*$", containment)
    root_test = next(
        (
            line.strip()
            for line in containment.splitlines()
            if line.lstrip().startswith("sudo ") and containment_name in line
        ),
        None,
    )
    assert root_test is not None
    assert root_test.startswith("sudo .venv/bin/python -m pytest ")
    assert not re.search(rf"{containment_name}\[[^]]+]", root_test)

    triggers = _yaml_block(workflow, "on", 0)
    assert "pull_request_target:" not in triggers
    assert "${{ secrets." not in workflow


def test_bot_unit_runs_release_local_health_and_app_with_a_strict_sandbox():
    unit = _unit_sections("deploy/systemd/honeybuy-tg.service")
    service = unit["Service"]

    assert _unit_value(unit, "Service", "User") == "honeybuy"
    assert _unit_value(unit, "Service", "Group") == "honeybuy"
    assert _unit_value(unit, "Service", "EnvironmentFile") == "/etc/honeybuy-tg/env"
    assert _unit_value(unit, "Service", "WorkingDirectory") == (
        "/opt/honeybuy-tg/current"
    )
    assert shlex.split(_unit_value(unit, "Service", "ExecStartPre")) == [
        "/opt/honeybuy-tg/current/.venv/bin/python",
        "-m",
        "honeybuy_tg",
        "healthcheck",
    ]
    assert shlex.split(_unit_value(unit, "Service", "ExecStart")) == [
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
        _assert_unit_boolean(unit, "Service", directive)
    assert _unit_value(unit, "Service", "ProtectSystem") == "strict"
    assert _unit_value(unit, "Service", "CapabilityBoundingSet") == ""
    assert set(shlex.split(_unit_value(unit, "Service", "ReadWritePaths"))) == {
        "/var/lib/honeybuy-tg"
    }
    assert set(
        shlex.split(_unit_value(unit, "Service", "RestrictAddressFamilies"))
    ) == {"AF_UNIX", "AF_INET", "AF_INET6"}
    assert "/opt/honeybuy-tg" not in service["ReadWritePaths"][0]


def test_bot_unit_has_bounded_start_stop_and_restart_behavior():
    unit = _unit_sections("deploy/systemd/honeybuy-tg.service")

    start_timeout = _systemd_duration_seconds(
        _unit_value(unit, "Service", "TimeoutStartSec")
    )
    stop_timeout = _systemd_duration_seconds(
        _unit_value(unit, "Service", "TimeoutStopSec")
    )
    restart_delay = _systemd_duration_seconds(
        _unit_value(unit, "Service", "RestartSec")
    )
    assert 0 < start_timeout <= 300
    assert 0 < stop_timeout <= 300
    assert 0 < restart_delay <= 60
    assert _unit_value(unit, "Service", "Restart") == "on-failure"
    assert 1 <= int(_unit_value(unit, "Unit", "StartLimitBurst")) <= 10
    assert 0 < _systemd_duration_seconds(
        _unit_value(unit, "Unit", "StartLimitIntervalSec")
    ) <= 900


def test_controller_unit_is_a_hardened_long_running_root_control_plane():
    unit = _unit_sections("deploy/systemd/honeybuy-release-controller.service")

    assert _unit_value(unit, "Service", "Type") == "exec"
    assert _unit_value(unit, "Service", "User") == "root"
    assert _unit_value(unit, "Service", "Group") == "root"
    assert shlex.split(_unit_value(unit, "Service", "ExecStart")) == [
        "/usr/bin/python3",
        "/usr/local/lib/honeybuy/release_controller.py",
        "deploy",
    ]
    assert _systemd_duration_seconds(
        _unit_value(unit, "Service", "TimeoutStartSec")
    ) >= 300
    stop_timeout = _systemd_duration_seconds(
        _unit_value(unit, "Service", "TimeoutStopSec")
    )
    assert 0 < stop_timeout <= 300
    assert _systemd_duration_seconds(
        _unit_value(unit, "Service", "RuntimeMaxSec")
    ) == 30 * 60
    assert _unit_value(unit, "Service", "KillMode") == "control-group"
    _assert_unit_boolean(unit, "Service", "SendSIGKILL")
    umask = int(_unit_value(unit, "Service", "UMask"), base=8)
    assert umask & 0o022 == 0o022

    for directive in (
        "NoNewPrivileges",
        "PrivateTmp",
        "ProtectHome",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectControlGroups",
        "LockPersonality",
        "RestrictSUIDSGID",
    ):
        _assert_unit_boolean(unit, "Service", directive)
    assert _unit_value(unit, "Service", "ProtectSystem") == "strict"
    assert set(
        shlex.split(_unit_value(unit, "Service", "RestrictAddressFamilies"))
    ) == {"AF_UNIX", "AF_INET", "AF_INET6"}

    capabilities = set(
        shlex.split(_unit_value(unit, "Service", "CapabilityBoundingSet"))
    )
    assert capabilities == {
        "CAP_CHOWN",
        "CAP_DAC_OVERRIDE",
        "CAP_FOWNER",
    }
    assert set(shlex.split(_unit_value(unit, "Service", "ReadWritePaths"))) == {
        "/opt/honeybuy-tg",
        "/var/lib/honeybuy-release-controller",
        "/var/lib/honeybuy-tg",
        "/var/backups/honeybuy-tg",
        "/var/cache/honeybuy-tg/uv",
        "/run/honeybuy-release-controller",
    }


def test_controller_unit_and_installer_share_a_private_runtime_lock_directory():
    unit = _unit_sections("deploy/systemd/honeybuy-release-controller.service")
    installer = _repo_text("deploy/ubuntu/install.sh")
    expected_directory = "/run/honeybuy-release-controller"

    assert _unit_value(unit, "Service", "RuntimeDirectory") == (
        "honeybuy-release-controller"
    )
    assert int(_unit_value(unit, "Service", "RuntimeDirectoryMode"), base=8) == 0o700
    _assert_unit_boolean(unit, "Service", "RuntimeDirectoryPreserve")
    writable_paths = set(
        shlex.split(_unit_value(unit, "Service", "ReadWritePaths"))
    )
    assert expected_directory in writable_paths
    assert not any(path.startswith("/run/lock/") for path in writable_paths)

    assert _shell_assignment(installer, "LOCK_DIR") == expected_directory
    lock_path = Path(_shell_assignment(installer, "LOCK_PATH"))
    assert lock_path.parent == Path(expected_directory)
    _assert_install_ownership(
        installer,
        "$LOCK_DIR",
        owner="root",
        group="root",
        directory=True,
        mode=0o700,
    )


def test_controller_timer_names_its_unit_and_uses_a_persistent_calendar():
    unit = _unit_sections("deploy/systemd/honeybuy-release-controller.timer")
    timer = unit["Timer"]

    assert _unit_value(unit, "Timer", "Unit") == (
        "honeybuy-release-controller.service"
    )
    assert _unit_value(unit, "Timer", "OnCalendar")
    assert "OnUnitActiveSec" not in timer
    _assert_unit_boolean(unit, "Timer", "Persistent")
    randomized_delay = _systemd_duration_seconds(
        _unit_value(unit, "Timer", "RandomizedDelaySec")
    )
    assert 0 < randomized_delay <= 300


def test_installer_uses_fixed_absolute_security_sensitive_paths():
    installer = _repo_text("deploy/ubuntu/install.sh")
    expected_paths = {
        "APP_DIR": "/opt/honeybuy-tg",
        "ENV_DIR": "/etc/honeybuy-tg",
        "DATA_DIR": "/var/lib/honeybuy-tg",
        "CACHE_DIR": "/var/cache/honeybuy-tg",
        "STATE_DIR": "/var/lib/honeybuy-release-controller",
        "BACKUP_DIR": "/var/backups/honeybuy-tg",
        "LOCK_DIR": "/run/honeybuy-release-controller",
        "LOCK_PATH": "/run/honeybuy-release-controller/controller.lock",
        "RELEASE_CONTROLLER_BIN": "/usr/local/lib/honeybuy/release_controller.py",
        "ALLOWED_SIGNERS_FILE": "/etc/honeybuy-tg/allowed_signers",
    }

    for name, expected_path in expected_paths.items():
        actual_path = _shell_assignment(installer, name)
        assert actual_path == expected_path
        assert Path(actual_path).is_absolute()
        assert ".." not in Path(actual_path).parts

    lock_path = Path(expected_paths["LOCK_PATH"])
    assert lock_path.is_absolute()
    assert lock_path.parent == Path(expected_paths["LOCK_DIR"])
    assert lock_path.suffix == ".lock"


def test_installer_stops_timer_and_holds_the_controller_lock_during_overwrite():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)
    timer_stop = next(
        index
        for index, line in enumerate(lines)
        if re.search(
            r"\bsystemctl\s+stop\s+(?:honeybuy-release-controller\.timer|"
            r"[\"']?\$TIMER_NAME[\"']?)",
            line,
        )
    )
    lock_open = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("exec ") and "$LOCK_PATH" in line and ">" in line
    )
    descriptor_match = re.search(
        r"exec\s+(?:\{(?P<variable>[A-Za-z_][A-Za-z0-9_]*)}|(?P<number>\d+))>",
        lines[lock_open],
    )
    assert descriptor_match is not None
    descriptor = descriptor_match.group("variable") or descriptor_match.group("number")
    descriptor_references = {descriptor, f"${descriptor}", f"${{{descriptor}}}"}
    flock_index = next(
        index
        for index, line in enumerate(lines)
        if index > lock_open
        and "flock" in [
            Path(word).name for word in shlex.split(line, comments=True)
        ]
        and descriptor_references.intersection(shlex.split(line, comments=True))
    )
    flock_words = shlex.split(lines[flock_index], comments=True)
    assert "-x" in flock_words or "--exclusive" in flock_words

    protected_installs = []
    for source in (
        "deploy/ubuntu/release_controller.py",
        "deploy/systemd/honeybuy-release-controller.service",
        "deploy/systemd/honeybuy-release-controller.timer",
    ):
        matches = [
            index
            for index, line in enumerate(lines)
            if "install"
            in [Path(word).name for word in shlex.split(line, comments=True)]
            and source in line
        ]
        assert len(matches) == 1
        protected_installs.append(matches[0])
    assert timer_stop < lock_open < flock_index < min(protected_installs)
    unlocks = [
        index
        for index, line in enumerate(lines)
        if re.search(r"\bflock\s+(?:-u|--unlock)\b", line)
    ]
    assert not unlocks or min(unlocks) > max(protected_installs)


def test_installer_rejects_unfinished_journal_immediately_under_lock():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)
    flock_index = next(
        index
        for index, line in enumerate(lines)
        if "flock" in [Path(word).name for word in shlex.split(line, comments=True)]
    )
    journal_guards = [
        index
        for index, line in enumerate(lines)
        if index > flock_index
        and re.match(r"^if\b", line)
        and "$JOURNAL_FILE" in line
        and re.search(r"(?:^|\s)-(?:e|f)(?:\s|\b)", line)
    ]
    assert len(journal_guards) == 1, (
        "installer must reject an existing journal immediately after locking"
    )
    journal_guard = journal_guards[0]
    journal_end = next(
        index
        for index in range(journal_guard + 1, len(lines))
        if re.match(r"^fi\b", lines[index])
    )
    rejection = "\n".join(lines[journal_guard : journal_end + 1])
    assert "fail " in rejection
    assert "recover" in rejection.casefold()
    expected_recovery = (
        "systemctl start honeybuy-release-controller.service"
    )
    expanded_rejection = _expand_shell_constants(installer, rejection)
    assert expected_recovery in expanded_rejection
    assert "$RELEASE_CONTROLLER_BIN" not in rejection
    assert "/usr/local/lib/honeybuy/release_controller.py" not in rejection
    assert not re.search(r"\bpython(?:3)?\b.*release_controller", rejection)

    intervening = lines[flock_index + 1 : journal_guard]
    assert all(
        "$LOCK_PATH" in line or "lock_fd" in line.casefold()
        for line in intervening
    ), intervening
    mutation_indexes = [
        index
        for index, line in enumerate(lines)
        if index > flock_index
        and {"curl", "install", "mv"}.intersection(
            Path(word).name for word in shlex.split(line, comments=True)
        )
    ]
    assert mutation_indexes
    assert journal_guard < min(mutation_indexes)


def test_installer_quiesces_and_rejects_journal_before_host_mutation():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)

    timer_stop_index = next(
        index
        for index, line in enumerate(lines)
        if (words := shlex.split(line, comments=True))
        and "systemctl" in [Path(word).name for word in words]
        and "stop" in words
        and any(
            word in {"$TIMER_NAME", "honeybuy-release-controller.timer"}
            for word in words
        )
    )
    lock_index = next(
        index
        for index, line in enumerate(lines)
        if (words := shlex.split(line, comments=True))
        and "flock" in [Path(word).name for word in words]
        and ({"-x", "--exclusive"} & set(words))
    )
    journal_guard_index = next(
        index
        for index, line in enumerate(lines)
        if re.match(r"^if\b", line)
        and "$JOURNAL_FILE" in line
        and re.search(r"(?:^|\s)-(?:e|f)(?:\s|\b)", line)
    )
    journal_guard_end = next(
        index
        for index in range(journal_guard_index + 1, len(lines))
        if re.match(r"^fi\b", lines[index])
    )

    host_mutation_indexes = [
        index
        for index, line in enumerate(lines)
        if (words := shlex.split(line, comments=True))
        and (
            "apt-get" in [Path(word).name for word in words]
            or {"groupadd", "useradd"}.intersection(
                Path(word).name for word in words
            )
        )
    ]
    assert host_mutation_indexes
    assert timer_stop_index < lock_index < journal_guard_index < journal_guard_end
    assert journal_guard_end < min(host_mutation_indexes)


def test_installer_creates_separate_users_and_narrow_directory_ownership():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)

    for identity in ("honeybuy", "honeybuy-build"):
        assert any(
            re.search(
                rf"\bgroupadd\b[^;]*\s{re.escape(identity)}(?:\s*;|\s*$)",
                line,
            )
            for line in lines
        )
        user_lines = [
            line
            for line in lines
            if "useradd" in line and re.search(rf"\b{identity}\s*$", line)
        ]
        assert len(user_lines) == 1
        user_words = shlex.split(user_lines[0], comments=True)
        explicit_gids = [
            user_words[index + 1]
            for index in range(len(user_words) - 1)
            if user_words[index] in {"-g", "--gid"}
        ] + [
            word.split("=", maxsplit=1)[1]
            for word in user_words
            if word.startswith("--gid=")
        ]
        assert explicit_gids == [identity]
        assert "/usr/sbin/nologin" in user_words

    assert not any(
        (
            "chown"
            in [Path(word).name for word in shlex.split(line, comments=True)]
        )
        and ({"-R", "--recursive"} & set(shlex.split(line, comments=True)))
        for line in lines
    )
    for target in (
        "$APP_DIR",
        "$APP_DIR/releases",
        "$ENV_DIR",
        "$STATE_DIR",
        "$STATE_DIR/repository",
        "$BACKUP_DIR",
        "$LOCK_DIR",
        "/usr/local/lib/honeybuy",
        "/var/empty/honeybuy-healthcheck",
    ):
        _assert_install_ownership(
            installer,
            target,
            owner="root",
            group="root",
            directory=True,
        )
    _assert_install_ownership(
        installer,
        "$DATA_DIR",
        owner="honeybuy",
        group="honeybuy",
        directory=True,
    )
    _assert_install_ownership(
        installer,
        "$CACHE_DIR/uv",
        owner="honeybuy-build",
        group="honeybuy-build",
        directory=True,
    )

    build_owned_lines = [
        line
        for line in lines
        if "install" in line and "honeybuy-build" in line
    ]
    assert build_owned_lines
    build_owned_targets = {
        target for line in build_owned_lines for target in _install_targets(line)
    }
    assert "$CACHE_DIR/uv" in build_owned_targets
    assert build_owned_targets <= {"$CACHE_DIR/uv", "$STAGING_DIR"}


def test_installer_installs_root_owned_manual_control_plane_files():
    installer = _repo_text("deploy/ubuntu/install.sh")
    expected_sources = {
        "$REPO_ROOT/deploy/ubuntu/release_controller.py": 0o755,
        "$REPO_ROOT/deploy/systemd/honeybuy-tg.service": 0o644,
        "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service": 0o644,
        "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer": 0o644,
    }
    lines = _shell_logical_lines(installer)
    for source, mode in expected_sources.items():
        installs = [
            line
            for line in lines
            if "install"
            in [Path(word).name for word in shlex.split(line, comments=True)]
            and source in shlex.split(line, comments=True)
        ]
        assert len(installs) == 1
        targets = _install_targets(installs[0])
        assert len(targets) == 2 and targets[0] == source
        _assert_install_ownership(
            installer,
            targets[1],
            owner="root",
            group="root",
            directory=False,
            mode=mode,
        )
    assert "release_controller.py deploy" not in installer


def test_installer_stages_validates_and_atomically_replaces_control_plane_files():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)
    expected_destinations = {
        "$REPO_ROOT/deploy/ubuntu/release_controller.py": (
            "/usr/local/lib/honeybuy/release_controller.py"
        ),
        "$REPO_ROOT/deploy/systemd/honeybuy-tg.service": (
            "/etc/systemd/system/honeybuy-tg.service"
        ),
        "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service": (
            "/etc/systemd/system/honeybuy-release-controller.service"
        ),
        "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer": (
            "/etc/systemd/system/honeybuy-release-controller.timer"
        ),
    }
    lock_index = next(
        index
        for index, line in enumerate(lines)
        if "flock" in [Path(word).name for word in shlex.split(line, comments=True)]
    )
    staged_targets: dict[str, str] = {}
    stage_indexes: dict[str, int] = {}
    for source in expected_destinations:
        installs = [
            (index, line)
            for index, line in enumerate(lines)
            if "install"
            in [Path(word).name for word in shlex.split(line, comments=True)]
            and source in shlex.split(line, comments=True)
        ]
        assert len(installs) == 1
        index, line = installs[0]
        targets = _install_targets(line)
        assert len(targets) == 2 and targets[0] == source
        staged_target = targets[1]
        expanded_stage = _expand_shell_constants(
            installer,
            staged_target,
            preserve=frozenset({"STAGING_DIR"}),
        )
        assert expanded_stage.startswith("$STAGING_DIR/")
        assert index > lock_index
        staged_targets[source] = staged_target
        stage_indexes[source] = index

    unit_stage_targets = {
        source: _expand_shell_constants(
            installer,
            staged_targets[source],
            preserve=frozenset({"STAGING_DIR"}),
        )
        for source in expected_destinations
        if source.endswith((".service", ".timer"))
    }
    staged_verifications: dict[str, list[int]] = {
        source: [] for source in unit_stage_targets
    }
    for index, line in enumerate(lines):
        words = shlex.split(line, comments=True)
        if "systemd-analyze" not in [Path(word).name for word in words]:
            continue
        if "verify" not in words:
            continue
        expanded_words = {
            _expand_shell_constants(
                installer,
                word,
                preserve=frozenset({"STAGING_DIR"}),
            )
            for word in words
        }
        for source, staged_target in unit_stage_targets.items():
            if staged_target in expanded_words:
                staged_verifications[source].append(index)

    control_unit_sources = {
        source
        for source in unit_stage_targets
        if "honeybuy-release-controller" in source
    }
    control_verify_indexes: set[int] = set()
    for source in control_unit_sources:
        assert len(staged_verifications[source]) == 1
        control_verify_indexes.update(staged_verifications[source])
    assert len(control_verify_indexes) == 1
    control_verify_index = control_verify_indexes.pop()
    assert control_verify_index > max(
        stage_indexes[source] for source in control_unit_sources
    )

    atomic_functions = {
        name: body
        for name, body in _shell_functions(installer).items()
        if re.search(r"\bmktemp\b", body)
        and re.search(r"(?m)^\s*install\b", body)
        and re.search(r"(?m)^\s*mv\b", body)
    }
    assert atomic_functions
    for body in atomic_functions.values():
        assert "dirname" in body
        assert re.search(r"\bmktemp\b[^\n]*(?:--tmpdir|destination)", body)
        move_line = next(
            line
            for line in _shell_logical_lines(body)
            if line.startswith("mv ")
        )
        assert "-T" in shlex.split(move_line, comments=True)

    replacement_indexes: dict[str, int] = {}
    for source, expected_destination in expected_destinations.items():
        expected_stage = _expand_shell_constants(
            installer,
            staged_targets[source],
            preserve=frozenset({"STAGING_DIR"}),
        )
        calls: list[int] = []
        for index, line in enumerate(lines):
            words = shlex.split(line, comments=True)
            if not words or words[0] not in atomic_functions:
                continue
            expanded_words = {
                _expand_shell_constants(
                    installer,
                    word,
                    preserve=frozenset({"STAGING_DIR"}),
                )
                for word in words[1:]
            }
            if expected_stage in expanded_words and expected_destination in expanded_words:
                calls.append(index)
        assert len(calls) == 1
        replacement_indexes[source] = calls[0]
        assert calls[0] > stage_indexes[source]

    for source in control_unit_sources:
        assert replacement_indexes[source] > control_verify_index


def test_installer_exposes_controller_before_validating_units_on_fresh_install():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)
    functions = _shell_functions(installer)
    atomic_functions = {
        name
        for name, body in functions.items()
        if re.search(r"\bmktemp\b", body)
        and re.search(r"(?m)^\s*install\b", body)
        and re.search(r"(?m)^\s*mv\b", body)
    }
    assert atomic_functions

    controller_source = "$REPO_ROOT/deploy/ubuntu/release_controller.py"
    controller_stage_index, controller_stage_line = next(
        (index, line)
        for index, line in enumerate(lines)
        if "install" in [Path(word).name for word in shlex.split(line, comments=True)]
        and controller_source in shlex.split(line, comments=True)
    )
    controller_stage_targets = _install_targets(controller_stage_line)
    assert len(controller_stage_targets) == 2
    staged_controller = _expand_shell_constants(
        installer,
        controller_stage_targets[1],
        preserve=frozenset({"STAGING_DIR"}),
    )

    controller_replace_index = next(
        index
        for index, line in enumerate(lines)
        if (words := shlex.split(line, comments=True))
        and words[0] in atomic_functions
        and staged_controller
        in {
            _expand_shell_constants(
                installer,
                word,
                preserve=frozenset({"STAGING_DIR"}),
            )
            for word in words[1:]
        }
        and "/usr/local/lib/honeybuy/release_controller.py"
        in {_expand_shell_constants(installer, word) for word in words[1:]}
    )
    verify_index = next(
        index
        for index, line in enumerate(lines)
        if (words := shlex.split(line, comments=True))
        and "systemd-analyze" in [Path(word).name for word in words]
        and "verify" in words
        and any("honeybuy-release-controller.service" in word for word in words)
    )

    journal_guard_index = next(
        index
        for index, line in enumerate(lines)
        if re.match(r"^if\b", line)
        and "$JOURNAL_FILE" in line
        and re.search(r"(?:^|\s)-(?:e|f)(?:\s|\b)", line)
    )
    journal_guard_end = next(
        index
        for index in range(journal_guard_index + 1, len(lines))
        if re.match(r"^fi\b", lines[index])
    )

    assert journal_guard_end < controller_stage_index
    assert controller_stage_index < controller_replace_index < verify_index


def test_installer_validates_app_unit_only_for_a_coherent_deployment():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)
    atomic_functions = {
        name
        for name, body in _shell_functions(installer).items()
        if re.search(r"\bmktemp\b", body)
        and re.search(r"(?m)^\s*install\b", body)
        and re.search(r"(?m)^\s*mv\b", body)
    }
    assert atomic_functions

    unit_sources = {
        "app": "$REPO_ROOT/deploy/systemd/honeybuy-tg.service",
        "controller": (
            "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service"
        ),
        "timer": "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer",
    }
    staged_units: dict[str, str] = {}
    for name, source in unit_sources.items():
        stage_lines = [
            line
            for line in lines
            if "install"
            in [Path(word).name for word in shlex.split(line, comments=True)]
            and source in shlex.split(line, comments=True)
        ]
        assert len(stage_lines) == 1
        stage_targets = _install_targets(stage_lines[0])
        assert len(stage_targets) == 2 and stage_targets[0] == source
        staged_units[name] = _expand_shell_constants(
            installer,
            stage_targets[1],
            preserve=frozenset({"STAGING_DIR"}),
        )

    verify_calls: list[tuple[int, set[str]]] = []
    for index, line in enumerate(lines):
        words = shlex.split(line, comments=True)
        if (
            "systemd-analyze" in [Path(word).name for word in words]
            and "verify" in words
        ):
            verify_calls.append(
                (
                    index,
                    {
                        _expand_shell_constants(
                            installer,
                            word,
                            preserve=frozenset({"STAGING_DIR"}),
                        )
                        for word in words
                    },
                )
            )
    assert verify_calls

    control_verifications = [
        (index, targets)
        for index, targets in verify_calls
        if {staged_units["controller"], staged_units["timer"]} <= targets
    ]
    assert len(control_verifications) == 1
    control_verify_index, control_verify_targets = control_verifications[0]
    assert staged_units["app"] not in control_verify_targets

    coherence_start = next(
        index
        for index, line in enumerate(lines)
        if re.match(r"^if\s+deployment_state_is_coherent\b", line)
    )
    depth = 0
    coherence_else: int | None = None
    coherence_end: int | None = None
    for index in range(coherence_start, len(lines)):
        line = lines[index]
        if re.match(r"^if\b", line):
            depth += 1
        elif re.match(r"^else\b", line) and depth == 1:
            coherence_else = index
        elif re.match(r"^fi\b", line):
            depth -= 1
            if depth == 0:
                coherence_end = index
                break
    assert coherence_else is not None and coherence_end is not None

    app_verify_indexes = [
        index
        for index, targets in verify_calls
        if staged_units["app"] in targets
    ]
    assert len(app_verify_indexes) == 1
    app_verify_index = app_verify_indexes[0]
    assert coherence_start < app_verify_index < coherence_else

    destinations = {
        "app": "/etc/systemd/system/honeybuy-tg.service",
        "controller": "/etc/systemd/system/honeybuy-release-controller.service",
        "timer": "/etc/systemd/system/honeybuy-release-controller.timer",
    }
    replacement_indexes: dict[str, int] = {}
    for name, destination in destinations.items():
        matches = []
        for index, line in enumerate(lines):
            words = shlex.split(line, comments=True)
            if not words or words[0] not in atomic_functions:
                continue
            expanded_words = {
                _expand_shell_constants(
                    installer,
                    word,
                    preserve=frozenset({"STAGING_DIR"}),
                )
                for word in words[1:]
            }
            if staged_units[name] in expanded_words and destination in {
                _expand_shell_constants(installer, word) for word in words[1:]
            }:
                matches.append(index)
        assert len(matches) == 1
        replacement_indexes[name] = matches[0]

    for name in ("controller", "timer"):
        assert control_verify_index < replacement_indexes[name] < coherence_start
    assert app_verify_index < replacement_indexes["app"] < coherence_else
    intervening = lines[app_verify_index + 1 : replacement_indexes["app"]]
    mutating_commands = {
        "chmod",
        "chown",
        "install",
        "ln",
        "mv",
        "rm",
        "systemctl",
    }
    assert not any(
        mutating_commands.intersection(
            Path(word).name for word in shlex.split(line, comments=True)
        )
        for line in intervening
    )

    incoherent_branch = "\n".join(lines[coherence_else + 1 : coherence_end])
    assert "bootstrap" in incoherent_branch.casefold()
    assert "systemd-analyze" not in incoherent_branch
    assert staged_units["app"] not in incoherent_branch


def test_installer_validates_existing_control_plane_destinations_before_replace():
    installer = _repo_text("deploy/ubuntu/install.sh")
    functions = _shell_functions(installer)
    lines = _shell_logical_lines(installer)
    symlink_guards = {
        name
        for name, body in functions.items()
        if re.search(r"(?:\[\[|\btest\b)[^\n]*(?:-L|-h)(?:\s|\b)", body)
    }
    metadata_guards = {
        name
        for name, body in functions.items()
        if "stat" in body and re.search(r"%[uU]", body) and re.search(r"%[aA]", body)
    }
    atomic_functions = {
        name
        for name, body in functions.items()
        if re.search(r"\bmktemp\b", body)
        and re.search(r"(?m)^\s*install\b", body)
        and re.search(r"(?m)^\s*mv\b", body)
    }
    assert symlink_guards and metadata_guards and atomic_functions
    destinations = {
        "/usr/local/lib/honeybuy/release_controller.py",
        "/etc/systemd/system/honeybuy-tg.service",
        "/etc/systemd/system/honeybuy-release-controller.service",
        "/etc/systemd/system/honeybuy-release-controller.timer",
    }
    for destination in destinations:
        guard_indexes: dict[str, list[int]] = {"symlink": [], "metadata": []}
        replacement_indexes: list[int] = []
        for index, line in enumerate(lines):
            words = shlex.split(line, comments=True)
            if not words:
                continue
            expanded_words = {
                _expand_shell_constants(installer, word) for word in words[1:]
            }
            if destination not in expanded_words:
                continue
            if words[0] in symlink_guards:
                guard_indexes["symlink"].append(index)
            if words[0] in metadata_guards:
                guard_indexes["metadata"].append(index)
            if words[0] in atomic_functions:
                replacement_indexes.append(index)
        assert len(guard_indexes["symlink"]) == 1
        assert len(guard_indexes["metadata"]) == 1
        assert len(replacement_indexes) == 1
        assert max(
            guard_indexes["symlink"] + guard_indexes["metadata"]
        ) < replacement_indexes[0]


def test_installer_refuses_unsafe_existing_trust_environment_and_state():
    installer = _repo_text("deploy/ubuntu/install.sh")
    functions = _shell_functions(installer)
    symlink_guards = {
        name
        for name, body in functions.items()
        if re.search(r"(?:\[\[|\btest\b)[^\n]*(?:-L|-h)(?:\s|\b)", body)
    }
    metadata_guards = {
        name
        for name, body in functions.items()
        if "stat" in body and re.search(r"%[uU]", body) and re.search(r"%[aA]", body)
    }
    assert symlink_guards
    assert metadata_guards
    lines = _shell_logical_lines(installer)
    guarded_targets = (
        ("$APP_DIR",),
        ("$ENV_DIR",),
        ("$ENV_DIR/env", "$ENV_FILE"),
        ("$STATE_DIR",),
        ("$STATE_DIR/repository", "$REPOSITORY_DIR"),
        ("$STATE_DIR/deployed-sha", "$DEPLOYED_STATE_FILE"),
        ("$STATE_DIR/deployment-journal.json", "$JOURNAL_FILE"),
        ("$ALLOWED_SIGNERS_FILE",),
        ("$LOCK_DIR",),
        ("$LOCK_PATH",),
    )
    for alternatives in guarded_targets:
        for guard_names in (symlink_guards, metadata_guards):
            guard_indexes = [
                index
                for index, line in enumerate(lines)
                if any(
                    re.search(rf"\b{re.escape(name)}\b", line)
                    for name in guard_names
                )
                and _line_has_shell_target(line, alternatives)
            ]
            assert guard_indexes, (
                f"missing safe existing-path validation for {alternatives!r}"
            )
            mutation_indexes = [
                index
                for index, line in enumerate(lines)
                if _line_has_shell_target(line, alternatives)
                and any(
                    command
                    in [Path(word).name for word in shlex.split(line, comments=True)]
                    for command in ("chmod", "chown", "install", "ln", "mkdir", "touch")
                )
            ]
            if mutation_indexes:
                assert min(guard_indexes) < min(mutation_indexes)


def test_installer_never_bootstraps_from_cwd_and_fails_closed_without_state():
    installer = _repo_text("deploy/ubuntu/install.sh")
    forbidden_fragments = (
        "BOOTSTRAP_SHA",
        "git archive",
        "rev-parse --verify HEAD",
        "| tar",
        "tar -x",
        "ln -sfn",
    )
    for fragment in forbidden_fragments:
        assert fragment not in installer

    coherence_functions = _coherence_function_names(installer)
    assert coherence_functions
    assert "[0-9a-f]{40}" in installer
    assert ".ready.json" in installer
    fail_closed = False
    for name in coherence_functions:
        negated = re.search(
            rf"(?ms)if\s+!\s*{re.escape(name)}\b.*?then(?P<body>.*?)^fi\b",
            installer,
        )
        if negated is not None and re.search(
            r"\bexit\s+[1-9][0-9]*\b", negated.group("body")
        ):
            fail_closed = True
        positive = re.search(
            rf"(?ms)if\s+{re.escape(name)}\b.*?then.*?^else\b"
            r"(?P<failure>.*?)^fi\b",
            installer,
        )
        if positive is not None and re.search(
            r"\bexit\s+[1-9][0-9]*\b", positive.group("failure")
        ):
            fail_closed = True
    assert fail_closed
    assert not re.search(
        r"\bsystemctl\s+(?:start|stop|restart|try-restart)\s+"
        r"(?:[\"']?\$SERVICE_NAME[\"']?|honeybuy-tg(?:\.service)?)"
        r"(?:\s|;|$)",
        installer,
    )


def test_installer_validates_bootstrap_journal_namespace_without_creating_marker():
    installer = _repo_text("deploy/ubuntu/install.sh")
    expected_path = "/var/lib/honeybuy-release-controller/bootstrap-journal.json"

    assert _shell_assignment(installer, "BOOTSTRAP_JOURNAL_FILE") == expected_path
    preflight_bodies = [
        body
        for body in _shell_functions(installer).values()
        if "$BOOTSTRAP_JOURNAL_FILE" in body
        and "python3" in body
        and "awaiting_service" in body
    ]
    assert len(preflight_bodies) == 1
    marker_lines = _shell_logical_lines(preflight_bodies[0])
    assert marker_lines
    safety_lines = [
        line
        for line in marker_lines
        if "assert_not_symlink" in line or "assert_safe_existing" in line
    ]
    assert any("assert_not_symlink" in line for line in safety_lines)
    metadata_line = next(
        line for line in safety_lines if "assert_safe_existing" in line
    )
    metadata_words = shlex.split(metadata_line, comments=True)
    assert {"file", "root", "0600"} <= set(metadata_words) or {
        "file",
        "root",
        "600",
    } <= set(metadata_words)

    mutating_commands = {"install", "mv", "tee", "touch", "truncate"}
    for line in marker_lines:
        words = shlex.split(line, comments=True)
        assert not mutating_commands.intersection(Path(word).name for word in words)
        assert not re.search(r"(?:^|\s)(?:>|>>)[^\n]*\$BOOTSTRAP_JOURNAL_FILE", line)


def test_installer_preflights_bootstrap_journal_under_lock_before_host_mutation():
    installer = _repo_text("deploy/ubuntu/install.sh")
    functions = _shell_functions(installer)
    preflight_functions = {
        name: body
        for name, body in functions.items()
        if "$BOOTSTRAP_JOURNAL_FILE" in body
        and "python3" in body
        and "awaiting_service" in body
    }
    assert len(preflight_functions) == 1
    preflight_name, preflight_body = next(iter(preflight_functions.items()))

    assert "assert_not_symlink" in preflight_body
    assert "assert_safe_existing" in preflight_body
    assert re.search(
        r"assert_safe_existing[^\n]*\$BOOTSTRAP_JOURNAL_FILE[^\n]*"
        r"\bfile\b[^\n]*\broot\b[^\n]*\broot\b[^\n]*\bprivate\b[^\n]*"
        r"\b0?600\b",
        preflight_body,
    )
    assert "stat -c" in preflight_body and "%h" in preflight_body
    assert "$DEPLOYED_STATE_FILE" in preflight_body
    assert "[0-9a-f]{40}" in preflight_body

    lines = _shell_logical_lines(installer)
    calls = [
        index
        for index, line in enumerate(lines)
        if preflight_name in shlex.split(line, comments=True)
    ]
    assert len(calls) == 1
    preflight_index = calls[0]
    flock_index = next(
        index
        for index, line in enumerate(lines)
        if "flock" in [Path(word).name for word in shlex.split(line, comments=True)]
    )

    mutation_indexes = [
        index
        for index, line in enumerate(lines)
        if (
            (words := shlex.split(line, comments=True))
            and (
                Path(words[0]).name in {"apt-get", "groupadd", "useradd", "curl"}
                or Path(words[0]).name == "atomic_replace_file"
                or (
                    Path(words[0]).name == "install"
                    and any(
                        target.startswith(("$APP_DIR", "$ENV_DIR", "$DATA_DIR", "$CACHE_DIR", "$STATE_DIR", "$BACKUP_DIR"))
                        or target.startswith("/usr/local")
                        or target.startswith("/etc/systemd")
                        for target in _install_targets(line)
                    )
                )
                or (
                    Path(words[0]).name == "systemctl"
                    and any(action in words for action in ("daemon-reload", "enable"))
                )
            )
        )
    ]
    assert mutation_indexes
    assert flock_index < preflight_index < min(mutation_indexes)
    assert "fail" in preflight_body or "fail" in lines[preflight_index]


def test_awaiting_service_preflight_requires_exact_inactive_managed_unit():
    installer = _repo_text("deploy/ubuntu/install.sh")
    functions = _shell_functions(installer)
    service_guards = {
        name: body
        for name, body in functions.items()
        if "systemctl" in body
        and "show" in body
        and "$SERVICE_NAME" in body
        and all(field in body for field in ("LoadState", "ActiveState", "SubState"))
    }
    assert len(service_guards) == 1
    guard_name, guard_body = next(iter(service_guards.items()))
    for exact_value in ("loaded", "inactive", "dead"):
        assert re.search(rf"(?:^|[^A-Za-z_]){exact_value}(?:$|[^A-Za-z_])", guard_body)
    assert "fail" in guard_body

    lines = _shell_logical_lines(installer)
    guard_calls = [
        index
        for index, line in enumerate(lines)
        if guard_name in shlex.split(line, comments=True)
    ]
    assert len(guard_calls) == 1
    guard_index = guard_calls[0]
    flock_index = next(index for index, line in enumerate(lines) if line.startswith("flock "))
    apt_index = next(index for index, line in enumerate(lines) if line.startswith("apt-get "))
    coherence_lines, coherence_start, _coherence_else, _coherence_end = (
        _coherence_branch(installer)
    )
    assert coherence_lines == lines
    assert flock_index < guard_index < apt_index < coherence_start

    guarded_context = "\n".join(
        lines[max(flock_index, guard_index - 3) : min(coherence_start, guard_index + 4)]
    )
    assert (
        "$BOOTSTRAP_JOURNAL_FILE" in guarded_context
        or "bootstrap" in guarded_context.casefold()
    )


def test_installer_persists_versioned_control_plane_manifest_atomically():
    installer = _repo_text("deploy/ubuntu/install.sh")
    manifest_path = _shell_assignment(installer, "CONTROL_PLANE_MANIFEST_FILE")
    assert manifest_path == (
        "/var/lib/honeybuy-release-controller/control-plane-manifest.json"
    )

    functions = _shell_functions(installer)
    writers = {
        name: body
        for name, body in functions.items()
        if "$CONTROL_PLANE_MANIFEST_FILE" in body
        and ("sha256sum" in body or "hashlib" in body)
        and "bootstrap_pending" in body
        and "installed" in body
    }
    assert len(writers) == 1
    _writer_name, writer_body = next(iter(writers.items()))
    assert "version" in writer_body
    assert "json" in writer_body.casefold()
    assert "sort_keys" in writer_body or "LC_ALL=C" in writer_body
    assert "atomic_replace_file" in writer_body
    atomic_line = next(
        line
        for line in _shell_logical_lines(writer_body)
        if line.startswith("atomic_replace_file ")
        and "$CONTROL_PLANE_MANIFEST_FILE" in line
    )
    atomic_words = shlex.split(atomic_line, comments=True)
    assert "0600" in atomic_words or "600" in atomic_words
    assert atomic_words.count("root") >= 2
    assert "-n" not in atomic_words and "--no-clobber" not in atomic_words

    required_pairs = (
        (
            "$REPO_ROOT/deploy/ubuntu/release_controller.py",
            "$RELEASE_CONTROLLER_BIN",
        ),
        (
            "$REPO_ROOT/deploy/ubuntu/allowed_signers",
            "$ALLOWED_SIGNERS_FILE",
        ),
        (
            "$REPO_ROOT/deploy/systemd/honeybuy-tg.service",
            "/etc/systemd/system/$SERVICE_NAME.service",
        ),
        (
            "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.service",
            "/etc/systemd/system/honeybuy-release-controller.service",
        ),
        (
            "$REPO_ROOT/deploy/systemd/honeybuy-release-controller.timer",
            "/etc/systemd/system/honeybuy-release-controller.timer",
        ),
    )
    for source, destination in required_pairs:
        assert source in writer_body
        assert destination in writer_body
    assert re.search(r"(?:==|\bcmp\b)[^\n]*(?:sha|digest)", writer_body, re.IGNORECASE)

    assert re.search(
        r"assert_not_symlink[^\n]*\$CONTROL_PLANE_MANIFEST_FILE",
        installer,
    )
    assert re.search(
        r"assert_safe_existing[^\n]*\$CONTROL_PLANE_MANIFEST_FILE[^\n]*"
        r"\bfile\b[^\n]*\broot\b[^\n]*\broot\b[^\n]*\bprivate\b[^\n]*"
        r"\b0?600\b",
        installer,
    )


def test_installer_control_plane_manifest_matches_controller_schema(tmp_path):
    installer = _repo_text("deploy/ubuntu/install.sh")
    writer_body = next(
        body
        for body in _shell_functions(installer).values()
        if "$CONTROL_PLANE_MANIFEST_FILE" in body
        and "bootstrap_pending" in body
        and "installed" in body
    )
    command = next(
        shlex.split(line, comments=True)
        for line in _shell_logical_lines(writer_body)
        if "-c" in shlex.split(line, comments=True)
        and "$manifest_tmp" in line
    )
    script_index = command.index("-c") + 1
    script = command[script_index]
    raw_arguments = command[script_index + 1 :]
    destination = tmp_path / "control-plane-manifest.json"
    arguments = [
        "installed" if value == "$state" else str(destination)
        if value == "$manifest_tmp"
        else "a" * 64
        if value.startswith("$")
        else value
        for value in raw_arguments
    ]

    completed = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "version": 1,
        "phase": "installed",
        "files": {
            "deploy/ubuntu/release_controller.py": "a" * 64,
            "deploy/ubuntu/allowed_signers": "a" * 64,
            "deploy/systemd/honeybuy-tg.service": "a" * 64,
            "deploy/systemd/honeybuy-release-controller.service": "a" * 64,
            "deploy/systemd/honeybuy-release-controller.timer": "a" * 64,
        },
    }


def test_control_plane_manifest_states_are_committed_after_matching_units():
    installer = _repo_text("deploy/ubuntu/install.sh")
    functions = _shell_functions(installer)
    writers = {
        name
        for name, body in functions.items()
        if "$CONTROL_PLANE_MANIFEST_FILE" in body
        and "bootstrap_pending" in body
        and "installed" in body
    }
    assert len(writers) == 1
    writer_name = writers.pop()
    lines, coherence_start, coherence_else, coherence_end = _coherence_branch(installer)

    calls: dict[str, list[int]] = {"bootstrap_pending": [], "installed": []}
    for index, line in enumerate(lines):
        words = shlex.split(line, comments=True)
        if writer_name not in words:
            continue
        for state in calls:
            if state in words:
                calls[state].append(index)
    assert len(calls["bootstrap_pending"]) == 1
    assert len(calls["installed"]) == 1
    pending_index = calls["bootstrap_pending"][0]
    installed_index = calls["installed"][0]
    assert coherence_else < pending_index < coherence_end
    assert coherence_start < installed_index < coherence_else

    control_reload_indexes = [
        index
        for index, line in enumerate(lines[:coherence_start])
        if line == "systemctl daemon-reload"
    ]
    assert control_reload_indexes and max(control_reload_indexes) < pending_index

    app_replace_indexes = [
        index
        for index, line in enumerate(lines[coherence_start:coherence_else], coherence_start)
        if "atomic_replace_file" in shlex.split(line, comments=True)
        and any("honeybuy-tg.service" in word for word in shlex.split(line, comments=True))
    ]
    app_reload_indexes = [
        index
        for index, line in enumerate(lines[coherence_start:coherence_else], coherence_start)
        if line == "systemctl daemon-reload"
    ]
    assert app_replace_indexes and app_reload_indexes
    assert max(app_replace_indexes) < max(app_reload_indexes) < installed_index
    assert not any(
        writer_name in shlex.split(line, comments=True)
        for line in lines[:coherence_start]
    )


def test_installer_second_pass_accepts_exact_awaiting_service_bootstrap_marker(
    tmp_path,
):
    installer = _repo_text("deploy/ubuntu/install.sh")
    parser = _bootstrap_marker_parser(installer)
    candidate_sha = "a" * 40
    marker = tmp_path / "bootstrap-journal.json"
    marker.write_text(
        json.dumps(
            {
                "version": 1,
                "candidate_sha": candidate_sha,
                "phase": "awaiting_service",
                "phases": BOOTSTRAP_PHASES,
            }
        ),
        encoding="utf-8",
    )
    marker.chmod(0o600)

    completed = subprocess.run(
        [sys.executable, "-c", parser, str(marker), candidate_sha],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "marker_update",
    [
        pytest.param(
            {"phase": "intent", "phases": BOOTSTRAP_PHASES[:1]},
            id="unfinished-intent",
        ),
        pytest.param(
            {"phase": "release_prepared", "phases": BOOTSTRAP_PHASES[:2]},
            id="unfinished-release-prepared",
        ),
        pytest.param(
            {"phase": "current_linked", "phases": BOOTSTRAP_PHASES[:3]},
            id="unfinished-current-linked",
        ),
        pytest.param(
            {"phase": "state_written", "phases": BOOTSTRAP_PHASES[:4]},
            id="unfinished-state-written",
        ),
        pytest.param(
            {"phases": BOOTSTRAP_PHASES[:-1]},
            id="incomplete-history",
        ),
        pytest.param(
            {
                "phases": [
                    "intent",
                    "current_linked",
                    "release_prepared",
                    "state_written",
                    "awaiting_service",
                ]
            },
            id="misordered-history",
        ),
        pytest.param({"candidate_sha": "A" * 40}, id="uppercase-sha"),
        pytest.param({"candidate_sha": "b" * 40}, id="different-sha"),
        pytest.param({"version": 2}, id="wrong-version"),
        pytest.param({"unexpected": True}, id="unexpected-key"),
    ],
)
def test_installer_rejects_malformed_bootstrap_marker_and_keeps_timer_disabled(
    tmp_path,
    marker_update,
):
    installer = _repo_text("deploy/ubuntu/install.sh")
    parser = _bootstrap_marker_parser(installer)
    candidate_sha = "a" * 40
    payload = {
        "version": 1,
        "candidate_sha": candidate_sha,
        "phase": "awaiting_service",
        "phases": BOOTSTRAP_PHASES,
    }
    payload.update(marker_update)
    marker = tmp_path / "bootstrap-journal.json"
    marker.write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-c", parser, str(marker), candidate_sha],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    lines, _start, coherence_else, coherence_end = _coherence_branch(installer)
    failure_lines = lines[coherence_else + 1 : coherence_end]
    assert any(
        re.search(r"\bsystemctl\s+disable\b", line) and "$TIMER_NAME" in line
        for line in failure_lines
    )
    assert not any(
        re.search(r"\bsystemctl\s+(?:enable|start)\b", line)
        and "$TIMER_NAME" in line
        for line in failure_lines
    )


def test_installer_coherence_accepts_only_production_v2_release_manifest(tmp_path):
    installer = _repo_text("deploy/ubuntu/install.sh")
    coherence_functions = _coherence_function_names(installer)
    assert len(coherence_functions) == 1
    coherence_body = _shell_functions(installer)[coherence_functions.pop()]

    parser_commands: list[list[str]] = []
    for line in _shell_logical_lines(coherence_body):
        words = shlex.split(line, comments=True)
        if (
            words
            and Path(words[0]).name.startswith("python3")
            and "-c" in words
            and "$ready_manifest" in words
        ):
            parser_commands.append(words)
    assert len(parser_commands) == 1
    parser_words = parser_commands[0]
    parser_script = parser_words[parser_words.index("-c") + 1]

    release_sha = "a" * 40
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_bytes(b"version = 1\n")
    provenance = {
        "sha": release_sha,
        "source_tree_sha256": hashlib.sha256(b"source tree").hexdigest(),
        "archive_sha256": hashlib.sha256(b"archive").hexdigest(),
        "uv_lock_sha256": hashlib.sha256(uv_lock.read_bytes()).hexdigest(),
        "venv_sha256": hashlib.sha256(b"venv").hexdigest(),
        "artifact_sha256": hashlib.sha256(b"artifact").hexdigest(),
    }
    manifest = {
        "version": 2,
        **provenance,
        "provenance_sha256": hashlib.sha256(
            json.dumps(
                provenance,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    manifest_path = tmp_path / ".ready.json"

    def parse(candidate: dict[str, object]) -> subprocess.CompletedProcess[str]:
        manifest_path.write_text(json.dumps(candidate), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-c", parser_script, str(manifest_path), str(uv_lock)],
            check=False,
            capture_output=True,
            text=True,
        )

    valid = parse(manifest)
    assert valid.returncode == 0, valid.stderr
    assert release_sha in valid.stdout.split()
    assert provenance["uv_lock_sha256"] in valid.stdout.split()

    digest_fields = {
        "source_tree_sha256",
        "archive_sha256",
        "uv_lock_sha256",
        "venv_sha256",
        "artifact_sha256",
        "provenance_sha256",
    }
    for field in digest_fields:
        noncanonical = dict(manifest)
        noncanonical[field] = "A" * 64
        assert parse(noncanonical).returncode != 0, field

    wrong_provenance = dict(manifest)
    wrong_provenance["provenance_sha256"] = hashlib.sha256(
        b"not the six provenance fields"
    ).hexdigest()
    assert parse(wrong_provenance).returncode != 0

    legacy_manifest = {
        "sha": release_sha,
        "uv_lock_sha256": provenance["uv_lock_sha256"],
    }
    assert parse(legacy_manifest).returncode != 0

    wrong_lock_digest = dict(manifest)
    wrong_lock_digest["uv_lock_sha256"] = hashlib.sha256(b"other lock").hexdigest()
    parser_rejects_wrong_lock = parse(wrong_lock_digest).returncode != 0
    shell_compares_lock_digest = bool(
        re.search(
            r"\[\[\s*\"\$(?:actual_digest|manifest_digest)\"\s*==\s*"
            r"\"\$(?:manifest_digest|actual_digest)\"\s*]]",
            coherence_body,
        )
    )
    assert parser_rejects_wrong_lock or shell_compares_lock_digest


def test_installer_coherence_accepts_only_exact_linux_lib64_symlink():
    installer = _repo_text("deploy/ubuntu/install.sh")
    coherence_functions = _coherence_function_names(installer)
    assert len(coherence_functions) == 1
    coherence_body = _shell_functions(installer)[coherence_functions.pop()]
    lines = _shell_logical_lines(coherence_body)

    assert any(
        re.search(
            r'\[\[\s+"\$link_relative"\s+==\s+"?\.venv/lib64"?\s+]]',
            line,
        )
        for line in lines
    )
    assert any(
        "readlink -- \"$link\"" in line
        and re.search(r'==\s+"?lib"?\s+]]', line)
        for line in lines
    )
    assert any(
        '-d "$release/.venv/lib"' in line
        and '! -L "$release/.venv/lib"' in line
        for line in lines
    )
    assert any(
        re.search(
            r'\[\[\s+"\$link_target"\s+==\s+"\$release/\.venv/lib"\s+]]',
            line,
        )
        for line in lines
    )
    assert any(
        "link_relative" in line and r"\.venv/bin/python(3(\.[0-9]+)?)?" in line
        for line in lines
    )


def test_incoherent_install_preserves_bot_and_directs_to_explicit_bootstrap():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)
    coherence_start = next(
        index
        for index, line in enumerate(lines)
        if re.match(r"^if\s+deployment_state_is_coherent\b", line)
    )
    depth = 0
    coherence_else: int | None = None
    coherence_end: int | None = None
    for index in range(coherence_start, len(lines)):
        line = lines[index]
        if re.match(r"^if\b", line):
            depth += 1
        elif re.match(r"^else\b", line) and depth == 1:
            coherence_else = index
        elif re.match(r"^fi\b", line):
            depth -= 1
            if depth == 0:
                coherence_end = index
                break
    assert coherence_else is not None and coherence_end is not None
    failure_lines = lines[coherence_else + 1 : coherence_end]
    failure_text = "\n".join(failure_lines)

    assert any(
        re.search(r"\bsystemctl\s+disable\b", line) and "$TIMER_NAME" in line
        for line in failure_lines
    )
    assert re.search(r"\bexit\s+[1-9][0-9]*\b", failure_text)
    assert "bootstrap" in failure_text.casefold()
    assert "docs/operations-and-testing.md" in failure_text
    assert "Provision a reviewed release" not in installer
    assert "matching deployed SHA" not in installer
    assert not any(
        "/etc/systemd/system/$SERVICE_NAME.service" in line
        or "$RELEASE_CONTROLLER_BIN" in line
        or "$UV_BIN" in line
        for line in failure_lines
    )
    assert not any(
        "$DEPLOYED_STATE_FILE" in line
        and any(operator in line for operator in (">", "install", "mv", "touch"))
        for line in lines
    )


def test_first_installer_pass_preserves_legacy_bot_and_prints_exact_bootstrap_order():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines, coherence_start, coherence_else, coherence_end = _coherence_branch(
        installer
    )
    failure_lines = lines[coherence_else + 1 : coherence_end]
    expanded_failure = [
        _expand_shell_constants(installer, line) for line in failure_lines
    ]
    required_guidance = (
        "systemctl stop honeybuy-tg.service",
        "systemctl start honeybuy-release-controller.service",
        "rerun this installer",
        "systemctl start honeybuy-tg.service",
        "systemctl is-active --quiet honeybuy-tg.service",
    )
    guidance_indexes = []
    for guidance in required_guidance:
        matches = [
            index
            for index, line in enumerate(expanded_failure)
            if guidance in line.casefold()
        ]
        assert len(matches) == 1, f"missing exact bootstrap guidance: {guidance}"
        guidance_indexes.append(matches[0])
    assert guidance_indexes == sorted(guidance_indexes)
    assert len(set(guidance_indexes)) == len(guidance_indexes)

    for index in guidance_indexes:
        words = shlex.split(failure_lines[index], comments=True)
        assert words and Path(words[0]).name == "printf"

    mutating_bot_commands = []
    for index, line in enumerate(failure_lines):
        words = shlex.split(line, comments=True)
        if not words or Path(words[0]).name != "systemctl":
            continue
        if any(
            word in {"$SERVICE_NAME", "$SERVICE_NAME.service", "honeybuy-tg.service"}
            for word in words
        ):
            mutating_bot_commands.append((index, words))
    assert mutating_bot_commands == []
    assert any(
        re.search(r"\bsystemctl\s+disable\b", line) and "$TIMER_NAME" in line
        for line in failure_lines
    )
    assert re.search(r"\bexit\s+[1-9][0-9]*\b", "\n".join(failure_lines))

    control_plane_sources = (
        "deploy/ubuntu/allowed_signers",
        "deploy/ubuntu/release_controller.py",
        "deploy/systemd/honeybuy-release-controller.service",
        "deploy/systemd/honeybuy-release-controller.timer",
    )
    for source in control_plane_sources:
        assert any(source in line for line in lines[:coherence_start]), source
    failure_text = "\n".join(failure_lines)
    assert ".ready.json" not in failure_text
    assert "deployed-sha" not in failure_text
    assert "bootstrap_pending" in failure_text


def test_coherent_installer_enables_bot_without_starting_it():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines, coherence_start, coherence_else, coherence_end = _coherence_branch(
        installer
    )

    def bot_systemctl_commands(branch: list[str]) -> list[list[str]]:
        commands = []
        for line in branch:
            expanded = _expand_shell_constants(installer, line)
            words = shlex.split(expanded, comments=True)
            if (
                words
                and Path(words[0]).name == "systemctl"
                and any(
                    word.removesuffix(".service") == "honeybuy-tg"
                    for word in words[1:]
                )
            ):
                commands.append(words)
        return commands

    coherent_commands = bot_systemctl_commands(
        lines[coherence_start + 1 : coherence_else]
    )
    enable_commands = [words for words in coherent_commands if "enable" in words]
    assert len(enable_commands) == 1
    assert "--now" not in enable_commands[0]
    assert not any(
        {"start", "restart", "try-restart"}.intersection(words)
        for words in coherent_commands
    )

    incoherent_commands = bot_systemctl_commands(
        lines[coherence_else + 1 : coherence_end]
    )
    assert not any(
        {"enable", "disable", "reenable", "preset", "mask", "unmask"}.intersection(
            words
        )
        for words in incoherent_commands
    )


def test_timer_activation_is_absent_or_guarded_by_coherent_deployed_state():
    installer = _repo_text("deploy/ubuntu/install.sh")
    coherence_names = _coherence_function_names(installer)
    lines = _shell_logical_lines(installer)
    conditions: list[str] = []
    timer_activation_seen = False
    for line in lines:
        if re.match(r"^if\b", line):
            conditions.append(line)
        if re.search(
            r"\bsystemctl\s+(?:enable|start)\b.*"
            r"(?:honeybuy-release-controller\.timer|[\"']?\$TIMER_NAME[\"']?)",
            line,
        ):
            timer_activation_seen = True
            assert any(
                any(re.search(rf"\b{re.escape(name)}\b", condition) for name in coherence_names)
                for condition in conditions
            )
        if re.match(r"^fi\b", line):
            assert conditions, "unbalanced installer conditional"
            conditions.pop()
    assert not timer_activation_seen or coherence_names


def test_installer_pins_verifies_and_atomically_root_installs_uv():
    installer = _repo_text("deploy/ubuntu/install.sh")
    lines = _shell_logical_lines(installer)

    assert _shell_assignment(installer, "UV_BIN") == "/usr/local/bin/uv"
    assert _shell_assignment(installer, "UV_VERSION") == "0.11.6"
    uv_sha256 = _shell_assignment(installer, "UV_SHA256")
    assert uv_sha256 == EXPECTED_UV_SHA256
    assert "astral.sh/uv/install.sh" not in installer
    assert "command -v uv" not in installer
    assert "https://github.com/astral-sh/uv/releases/download/" in installer
    assert "$UV_VERSION" in installer or "${UV_VERSION}" in installer

    checksum_index = next(
        index
        for index, line in enumerate(lines)
        if "sha256sum" in line and ("-c" in line or "--check" in line)
    )
    layout_validation_index = next(
        index
        for index, line in enumerate(lines)
        if "$UV_EXTRACTED_BIN" in line
        and re.search(r"(?:^|\s)-f(?:\s|\b)", line)
        and re.search(r"(?:^|\s)-x(?:\s|\b)", line)
    )
    atomic_functions = {
        name
        for name, body in _shell_functions(installer).items()
        if re.search(r"\bmktemp\b", body)
        and re.search(r"(?m)^\s*install\b", body)
        and re.search(r"(?m)^\s*mv\b", body)
    }
    assert atomic_functions
    expected_source = _expand_shell_constants(
        installer,
        "$UV_EXTRACTED_BIN",
        preserve=frozenset({"STAGING_DIR"}),
    )
    uv_replacements: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines):
        words = shlex.split(line, comments=True)
        if not words or words[0] not in atomic_functions:
            continue
        expanded_words = [
            _expand_shell_constants(
                installer,
                word,
                preserve=frozenset({"STAGING_DIR"}),
            )
            for word in words[1:]
        ]
        if expected_source in expanded_words and "/usr/local/bin/uv" in expanded_words:
            uv_replacements.append((index, expanded_words))
    assert len(uv_replacements) == 1
    replacement_index, replacement_arguments = uv_replacements[0]
    assert "0755" in replacement_arguments
    assert replacement_arguments.count("root") >= 2
    assert checksum_index < layout_validation_index < replacement_index
    assert not _install_line_for(installer, "$UV_BIN", directory=False)
    version_checks = [
        index
        for index, line in enumerate(lines)
        if "$UV_BIN" in line and "--version" in line and "UV_VERSION" in line
    ]
    assert version_checks and max(version_checks) > replacement_index


def test_allowed_signer_is_git_scoped_and_matches_expected_fingerprint():
    signer_lines = [
        line
        for line in _repo_text("deploy/ubuntu/allowed_signers").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(signer_lines) == 1
    words = shlex.split(signer_lines[0])
    assert words[0] == "atiurin@proton.me"
    key_index = next(
        index for index, word in enumerate(words) if word.startswith("ssh-")
    )
    assert "namespaces=git" in words[1:key_index]
    key_bytes = base64.b64decode(words[key_index + 1], validate=True)
    fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(key_bytes).digest()
    ).decode("ascii").rstrip("=")
    assert fingerprint == EXPECTED_SIGNER_FINGERPRINT


def test_installer_validates_signer_and_never_replaces_existing_trust(tmp_path):
    installer = _repo_text("deploy/ubuntu/install.sh")
    assert EXPECTED_SIGNER_FINGERPRINT in installer
    assert 'namespaces="git"' in installer
    fingerprint_functions = {
        name for name, body in _shell_functions(installer).items() if "ssh-keygen" in body
    }
    assert fingerprint_functions
    assert any(
        any(re.search(rf"\b{re.escape(name)}\b", line) for name in fingerprint_functions)
        and _line_has_shell_target(line, ("$ALLOWED_SIGNERS_FILE",))
        for line in _shell_logical_lines(installer)
    )
    install_commands = [
        shlex.split(line, comments=True)
        for line in _shell_logical_lines(installer)
        if "install"
        in [Path(word).name for word in shlex.split(line, comments=True)]
    ]
    assert not any(
        {"-n", "--no-clobber"}.intersection(words) for words in install_commands
    )

    install_once_name = "install_file_if_absent"
    install_once_body = _shell_functions(installer)[install_once_name]
    assert re.search(r"(?m)^\s*temporary=.*\bmktemp\b", install_once_body)
    assert re.search(r"(?m)^\s*if\s+ln\s+-T\s+--\s+", install_once_body)
    assert re.search(
        r'\[\[\s+-e\s+"\$destination"\s+\|\|\s+-L\s+"\$destination"\s+\]\]',
        install_once_body,
    )

    signer_installs = [
        line
        for line in _shell_logical_lines(installer)
        if shlex.split(line, comments=True)
        and shlex.split(line, comments=True)[0] == install_once_name
    ]
    assert len(signer_installs) == 1
    words = shlex.split(signer_installs[0], comments=True)
    assert words == [
        install_once_name,
        "$REPO_ROOT/deploy/ubuntu/allowed_signers",
        "$ALLOWED_SIGNERS_FILE",
        "0644",
        "root",
        "root",
    ]
    assert not re.search(
        r"\bmv\b[^\n]*(?:\$ALLOWED_SIGNERS_FILE|allowed_signers)",
        installer,
    )

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_text("trusted signer\n", encoding="utf-8")
    invoke_install_once = f"""
set -euo pipefail
fail() {{
  printf '%s\n' "$1" >&2
  exit 1
}}
ln() {{
  if [[ "${{1:-}}" == -T ]]; then shift; fi
  if [[ "${{1:-}}" == -- ]]; then shift; fi
  command ln "$@"
}}
{install_once_name}() {{
{install_once_body}
}}
{install_once_name} "$1" "$2" 0644 "$3" "$4"
"""
    command = [
        "bash",
        "-c",
        invoke_install_once,
        "--",
        str(source),
        str(destination),
        str(source.stat().st_uid),
        str(source.stat().st_gid),
    ]

    subprocess.run(command, check=True, capture_output=True, text=True)
    original_inode = destination.stat().st_ino
    assert destination.read_text(encoding="utf-8") == "trusted signer\n"
    assert destination.stat().st_mode & 0o777 == 0o644

    source.write_text("rotated signer\n", encoding="utf-8")
    subprocess.run(command, check=True, capture_output=True, text=True)

    assert destination.stat().st_ino == original_inode
    assert destination.read_text(encoding="utf-8") == "trusted signer\n"


def test_migrate_cli_runs_without_starting_bot(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("OWNER_USER_ID", "1")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    main(["migrate"])

    output = capsys.readouterr().out
    assert "Migrated" in output
    assert f"user_version 0 -> {CURRENT_SCHEMA_VERSION}" in output
    assert "integrity ok" in output
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_migrate_cli_still_requires_runtime_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)
    database_path = tmp_path / "must-not-be-created.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    with pytest.raises(SystemExit, match="Invalid configuration"):
        main(["migrate"])

    assert not database_path.exists()


def test_normal_startup_still_requires_runtime_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)

    with pytest.raises(SystemExit, match="Invalid configuration"):
        main([])


def test_healthcheck_requires_only_database_path_and_does_not_modify_database(
    tmp_path,
    monkeypatch,
    capsys,
):
    database_path = tmp_path / "current.sqlite3"
    migrate_database_path(database_path)
    before = database_path.read_bytes()
    before_entries = sorted(path.name for path in tmp_path.iterdir())
    database_path.chmod(0o444)
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    main(["healthcheck"])

    assert capsys.readouterr().out == (
        f"healthcheck ok: schema={CURRENT_SCHEMA_VERSION} "
        "integrity=ok foreign_keys=ok\n"
    )
    assert database_path.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == before_entries


def test_healthcheck_requires_database_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)

    message = _run_failing_healthcheck(capsys)

    assert "DATABASE_PATH" in message


def test_healthcheck_rejects_missing_database_without_creating_it(
    tmp_path,
    monkeypatch,
    capsys,
):
    database_path = tmp_path / "missing.sqlite3"
    _configure_healthcheck(
        monkeypatch,
        tmp_path,
        database_path,
        include_unrelated_secrets=True,
    )

    message = _run_failing_healthcheck(capsys)

    assert str(database_path) in message
    assert "exist" in message.casefold() or "missing" in message.casefold()
    assert not database_path.exists()


@pytest.mark.parametrize(
    ("database_version", "expected_word"),
    [
        pytest.param(CURRENT_SCHEMA_VERSION - 1, "migrate", id="older"),
        pytest.param(CURRENT_SCHEMA_VERSION + 1, "newer", id="newer"),
    ],
)
def test_healthcheck_rejects_schema_mismatch_without_migrating(
    tmp_path,
    monkeypatch,
    capsys,
    database_version,
    expected_word,
):
    database_path = tmp_path / "schema-mismatch.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        db.execute("INSERT INTO sentinel VALUES ('preserved')")
        db.execute(f"PRAGMA user_version = {database_version}")
    before = database_path.read_bytes()
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert str(database_version) in message
    assert str(CURRENT_SCHEMA_VERSION) in message
    assert expected_word in message.casefold()
    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == database_version
        assert db.execute("SELECT value FROM sentinel").fetchone()[0] == "preserved"


@pytest.mark.parametrize(
    ("damage_sql", "missing_object"),
    [
        pytest.param(
            "DROP TABLE shopping_items",
            "shopping_items",
            id="missing-table",
        ),
        pytest.param(
            "ALTER TABLE authorized_chats DROP COLUMN title",
            "title",
            id="missing-column",
        ),
    ],
)
def test_healthcheck_rejects_current_version_with_missing_required_schema(
    tmp_path,
    monkeypatch,
    capsys,
    damage_sql,
    missing_object,
):
    database_path = tmp_path / "missing-required-schema.sqlite3"
    migrate_database_path(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(damage_sql)
    before = database_path.read_bytes()
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert "schema" in message.casefold() or "missing" in message.casefold()
    assert missing_object in message
    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_healthcheck_rejects_integrity_failure(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "bad-integrity.sqlite3"
    migrate_database_path(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(
            "CREATE TABLE integrity_probe "
            "(value INTEGER NOT NULL CHECK (value > 0))"
        )
        db.execute("PRAGMA ignore_check_constraints = ON")
        db.execute("INSERT INTO integrity_probe VALUES (-1)")
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert "integrity" in message.casefold()
    assert "integrity_probe" in message


def test_healthcheck_rejects_foreign_key_failure(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "bad-foreign-key.sqlite3"
    migrate_database_path(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)")
        db.execute(
            "CREATE TABLE fk_child ("
            "id INTEGER PRIMARY KEY, "
            "parent_id INTEGER NOT NULL REFERENCES fk_parent(id)"
            ")"
        )
        db.execute("INSERT INTO fk_child VALUES (1, 999)")
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert "foreign" in message.casefold()
    assert "fk_child" in message
