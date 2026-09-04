#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import io
import json
import math
import os
import pwd
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, Protocol


SHA_RE = re.compile(r"[0-9a-f]{40}")
RESERVED_RELEASE_PATHS = {".ready.json", ".venv"}
CONTROL_PLANE_PATH_FIELDS = {
    "deploy/systemd/honeybuy-release-controller.service": (
        "controller_service_unit_path"
    ),
    "deploy/systemd/honeybuy-release-controller.timer": (
        "controller_timer_unit_path"
    ),
    "deploy/systemd/honeybuy-tg.service": "app_service_unit_path",
    "deploy/ubuntu/allowed_signers": "allowed_signers_path",
    "deploy/ubuntu/release_controller.py": "installed_controller_path",
}
ACTIVATION_PHASES = (
    "prepared",
    "stopped",
    "backed_up",
    "migrated",
    "switched",
    "start_requested",
    "healthy",
)
BOOTSTRAP_PHASES = (
    "intent",
    "release_prepared",
    "current_linked",
    "state_written",
    "awaiting_service",
)
BOOTSTRAP_SCHEMA_VERSION = 2
BOOTSTRAP_REQUIRED_SCHEMA = {
    "authorized_chats": frozenset(
        {"chat_id", "chat_type", "title", "authorized_by", "created_at"}
    ),
    "bot_messages": frozenset(
        {"chat_id", "message_id", "kind", "item_ids", "created_at"}
    ),
    "category_cache": frozenset(
        {"normalized_name", "category", "expires_at", "updated_at"}
    ),
    "chat_settings": frozenset(
        {"chat_id", "text_parse_mode", "updated_by", "updated_at"}
    ),
    "events": frozenset(
        {
            "id",
            "chat_id",
            "user_id",
            "telegram_message_id",
            "input_type",
            "raw_text",
            "ai_result_json",
            "status",
            "error",
            "created_at",
        }
    ),
    "inline_capture_intents": frozenset(
        {
            "token_hash",
            "requester_id",
            "target_chat_id",
            "target_kind",
            "item_text",
            "created_at",
            "expires_at",
            "status",
        }
    ),
    "item_normalization_cache": frozenset(
        {
            "raw_normalized_name",
            "canonical_name",
            "canonical_key",
            "expires_at",
            "updated_at",
        }
    ),
    "pending_confirmations": frozenset(
        {
            "id",
            "chat_id",
            "user_id",
            "source_message_id",
            "items_json",
            "status",
            "created_at",
            "resolved_at",
        }
    ),
    "recipe_aliases": frozenset(
        {
            "id",
            "chat_id",
            "recipe_id",
            "alias",
            "normalized_alias",
            "created_by",
            "created_at",
        }
    ),
    "recipe_ingredients": frozenset(
        {
            "id",
            "recipe_id",
            "name",
            "quantity_text",
            "canonical_name",
            "canonical_key",
            "position",
        }
    ),
    "recipes": frozenset(
        {
            "id",
            "chat_id",
            "name",
            "normalized_name",
            "source_url",
            "created_by",
            "created_at",
            "updated_at",
        }
    ),
    "shop_sessions": frozenset(
        {
            "chat_id",
            "message_id",
            "item_id",
            "item_text",
            "category",
            "checked",
            "created_at",
            "updated_at",
        }
    ),
    "shopping_items": frozenset(
        {
            "id",
            "chat_id",
            "name",
            "normalized_name",
            "canonical_name",
            "canonical_key",
            "quantity",
            "unit",
            "note",
            "due_date",
            "status",
            "created_by",
            "created_at",
            "updated_at",
            "bought_at",
            "removed_at",
        }
    ),
}
MIGRATION_ENV = {
    "TELEGRAM_BOT_TOKEN": "deployment-placeholder",
    "OWNER_USER_ID": "1",
}
SQLITE_CLONE_HELPER = r"""
import pathlib
import sqlite3
import sys
import urllib.parse
from contextlib import closing

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
uri = f"file:{urllib.parse.quote(str(source), safe='/')}?mode=ro"
with (
    closing(sqlite3.connect(uri, uri=True)) as source_db,
    closing(sqlite3.connect(target)) as target_db,
):
    with source_db, target_db:
        source_db.backup(target_db)
with closing(sqlite3.connect(target)) as clone:
    with clone:
        mode = clone.execute("PRAGMA journal_mode").fetchone()
        if mode == ("wal",):
            checkpoint = clone.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                raise SystemExit("clone checkpoint failed")
            if clone.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise SystemExit("clone journal reset failed")
            if clone.execute("PRAGMA journal_mode=WAL").fetchone() != ("wal",):
                raise SystemExit("clone WAL restore failed")
for suffix in ("-wal", "-shm"):
    sidecar = target.with_name(target.name + suffix)
    try:
        sidecar.unlink()
    except FileNotFoundError:
        pass
"""


class ResultStatus(StrEnum):
    BUSY = "busy"
    BOOTSTRAPPED = "bootstrapped"
    DEPLOYED = "deployed"
    NOOP = "noop"
    PREPARED = "prepared"
    REJECTED = "rejected"
    TRANSIENT = "transient"
    INTERVENTION_REQUIRED = "intervention_required"


@dataclass(frozen=True)
class ControllerResult:
    status: ResultStatus
    reason: str
    sha: str | None = None


@dataclass(frozen=True)
class ServiceObservation:
    main_pid: int
    restart_count: int
    exit_status: int


@dataclass(frozen=True)
class GitTreeEntry:
    mode: int
    object_id: str


@dataclass(frozen=True)
class TransientUnitObservation:
    load_state: str
    active_state: str
    sub_state: str
    result: str
    exec_main_code: str
    exit_status: int
    control_group: str


class ContainmentFailure(RuntimeError):
    """A workload escaped, or the controller could not prove containment."""


@dataclass(frozen=True)
class ControllerConfig:
    repository: str = "noxiouz/honeybuy_tg"
    branch: str = "main"
    workflow_path: str = ".github/workflows/tests.yml"
    remote_url: str = "https://github.com/noxiouz/honeybuy_tg.git"
    source_repo: Path = Path("/var/lib/honeybuy-release-controller/repository")
    releases_dir: Path = Path("/opt/honeybuy-tg/releases")
    current_link: Path = Path("/opt/honeybuy-tg/current")
    previous_link: Path = Path("/opt/honeybuy-tg/previous")
    deployed_state_path: Path = Path("/var/lib/honeybuy-release-controller/deployed-sha")
    deployment_journal_path: Path = Path(
        "/var/lib/honeybuy-release-controller/deployment-journal.json"
    )
    bootstrap_journal_path: Path = Path(
        "/var/lib/honeybuy-release-controller/bootstrap-journal.json"
    )
    deployment_receipts_dir: Path = Path(
        "/var/lib/honeybuy-release-controller/receipts"
    )
    quarantine_dir: Path = Path("/var/lib/honeybuy-release-controller/quarantine")
    scratch_dir: Path = Path("/var/lib/honeybuy-release-controller/scratch")
    database_backup_dir: Path = Path("/var/backups/honeybuy-tg")
    lock_path: Path = Path("/run/honeybuy-release-controller/controller.lock")
    allowed_signers_path: Path = Path("/etc/honeybuy-tg/allowed_signers")
    control_plane_manifest_path: Path = Path(
        "/var/lib/honeybuy-release-controller/control-plane-manifest.json"
    )
    installed_controller_path: Path = Path(
        "/usr/local/lib/honeybuy/release_controller.py"
    )
    app_service_unit_path: Path = Path("/etc/systemd/system/honeybuy-tg.service")
    controller_service_unit_path: Path = Path(
        "/etc/systemd/system/honeybuy-release-controller.service"
    )
    controller_timer_unit_path: Path = Path(
        "/etc/systemd/system/honeybuy-release-controller.timer"
    )
    database_path: Path = Path("/var/lib/honeybuy-tg/honeybuy.sqlite3")
    uv_cache_dir: Path = Path("/var/cache/honeybuy-tg/uv")
    git_path: Path = Path("/usr/bin/git")
    uv_path: Path = Path("/usr/local/bin/uv")
    systemctl_path: Path = Path("/usr/bin/systemctl")
    systemd_run_path: Path = Path("/usr/bin/systemd-run")
    cgroup_root: Path = Path("/sys/fs/cgroup")
    system_python_path: Path = Path("/usr/bin/python3")
    service_name: str = "honeybuy-tg.service"
    controller_service_name: str = "honeybuy-release-controller.service"
    build_uid: int | None = None
    build_gid: int | None = None
    release_uid: int = 0
    release_gid: int = 0
    runtime_uid: int | None = None
    runtime_gid: int | None = None
    empty_work_dir: Path = Path("/var/empty/honeybuy-healthcheck")
    api_timeout_seconds: float = 5.0
    command_timeout_seconds: float = 120.0
    management_timeout_seconds: float = 10.0
    stable_health_checks: int = 3
    stable_health_interval_seconds: float = 2.0
    gnupg_home: Path | None = None
    test_mode: bool = False


class Runner(Protocol):
    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path | None = None,
        uid: int | None = None,
        gid: int | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        ...


class HttpClient(Protocol):
    def get_json(self, url: str, *, timeout: float) -> object:
        ...


class SubprocessRunner:
    _CLEAN_PATH = "/usr/local/bin:/usr/bin:/bin"

    def __init__(
        self,
        *,
        timeout_seconds: float,
        management_timeout_seconds: float = 10.0,
        systemd_run_path: Path = Path("/usr/bin/systemd-run"),
        systemctl_path: Path = Path("/usr/bin/systemctl"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        controller_unit: str = "honeybuy-release-controller.service",
        managed_service: str = "honeybuy-tg.service",
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._management_timeout_seconds = management_timeout_seconds
        self._systemd_run_path = systemd_run_path
        self._systemctl_path = systemctl_path
        self._cgroup_root = cgroup_root
        self._controller_unit = controller_unit
        self._managed_service = managed_service
        self._nonce = secrets.token_hex(16)
        self._unit_counter = 0

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path | None = None,
        uid: int | None = None,
        gid: int | None = None,
        env: dict[str, str] | None = None,
        writable_paths: tuple[Path, ...] = (),
        network_access: bool = False,
    ) -> subprocess.CompletedProcess[Any]:
        payload = tuple(str(argument) for argument in argv)
        if not payload or not Path(payload[0]).is_absolute():
            raise ContainmentFailure("workload executable is not an absolute path")
        if (uid is None) != (gid is None):
            raise ContainmentFailure("workload identity is incomplete")
        effective_uid = 0 if uid is None else uid
        effective_gid = 0 if gid is None else gid
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (effective_uid, effective_gid)
        ):
            raise ContainmentFailure("workload identity is invalid")
        working_directory = Path("/") if cwd is None else cwd
        if not working_directory.is_absolute() or "\0" in str(working_directory):
            raise ContainmentFailure("workload directory is invalid")
        authorized_paths = tuple(dict.fromkeys(writable_paths))
        if any(
            not path.is_absolute() or "\0" in str(path) or "\n" in str(path)
            for path in authorized_paths
        ):
            raise ContainmentFailure("workload writable path is invalid")
        clean_env = {"PATH": self._CLEAN_PATH}
        for name, value in (env or {}).items():
            if (
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
                or not isinstance(value, str)
                or "\0" in value
                or "\n" in value
            ):
                raise ContainmentFailure("workload environment is invalid")
            if name == "PATH" and value != self._CLEAN_PATH:
                raise ContainmentFailure("workload PATH override is forbidden")
            clean_env[name] = value

        self.validate_controller_context()
        unit = self._next_unit_name()
        launch = self._transient_argv(
            unit,
            payload,
            cwd=working_directory,
            uid=effective_uid,
            gid=effective_gid,
            env=clean_env,
            writable_paths=authorized_paths,
            network_access=network_access,
        )
        process = subprocess.Popen(
            launch,
            cwd="/",
            env={"PATH": self._CLEAN_PATH},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        try:
            stdout, stderr = process.communicate(
                timeout=self._timeout_seconds + self._management_timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate_transient(unit)
            try:
                process.communicate(timeout=self._management_timeout_seconds)
            except subprocess.TimeoutExpired as cleanup_error:
                raise ContainmentFailure(
                    "systemd-run client did not exit after workload termination"
                ) from cleanup_error
            except (OSError, subprocess.SubprocessError) as cleanup_error:
                raise ContainmentFailure(
                    "systemd-run client failed while reaping a terminated workload"
                ) from cleanup_error
            population = self._cgroup_population(unit)
            if population == 0:
                self._reset_failed_unit(unit)
            elif not (
                population is None
                and self._unit_has_released_terminal_without_cgroup(unit)
            ):
                raise ContainmentFailure("timed-out workload cgroup did not empty")
            raise subprocess.TimeoutExpired(
                payload,
                self._timeout_seconds,
                output=exc.output,
                stderr=exc.stderr,
            ) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            self._fail_after_launch(
                unit,
                "systemd-run client failed after transient workload launch",
                cause=exc,
            )

        try:
            observation = self._observe_transient(unit)
        except ContainmentFailure as exc:
            self._fail_after_launch(
                unit,
                "transient unit observation failed",
                cause=exc,
            )
        if observation is None:
            self._fail_after_launch(unit, "transient unit status is unavailable")
        released_without_cgroup = self._released_terminal_without_cgroup(
            unit,
            observation,
        )
        loaded_with_cgroup = self._loaded_terminal_with_expected_cgroup(
            unit,
            observation,
        )
        if not released_without_cgroup and not loaded_with_cgroup:
            self._fail_after_launch(
                unit,
                "transient unit did not reach an exact terminal state",
            )
        if loaded_with_cgroup:
            self._require_empty_cgroup(unit)

        returncode = process.returncode
        if returncode is None:
            self._fail_after_launch(
                unit,
                "systemd-run did not report a return code",
            )
        if observation.load_state == "not-found":
            # Exact unloaded+missing is the only safe state in which systemd
            # has already discarded the execution properties.  The bounded
            # `systemd-run --wait` status is then the remaining exit evidence.
            pass
        elif observation.result == "success" and observation.exit_status == 0:
            if observation.exec_main_code not in {"1", "exited"}:
                self._fail_after_launch(
                    unit,
                    "successful workload has invalid exit provenance",
                )
            if returncode != 0:
                self._fail_after_launch(
                    unit,
                    "systemd manager and workload status disagree",
                )
        elif observation.result == "timeout":
            self._reset_failed_unit(unit)
            if returncode == 0:
                raise ContainmentFailure(
                    "systemd manager and workload timeout status disagree"
                )
            raise subprocess.TimeoutExpired(
                payload,
                self._timeout_seconds,
                output=stdout,
                stderr=stderr,
            )
        else:
            if observation.exit_status < 0:
                self._fail_after_launch(
                    unit,
                    "transient workload exit status is invalid",
                )
            if returncode == 0:
                returncode = observation.exit_status or 1
            self._reset_failed_unit(unit)
        return subprocess.CompletedProcess(
            args=payload,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _fail_after_launch(
        self,
        unit: str,
        message: str,
        *,
        cause: BaseException | None = None,
    ) -> NoReturn:
        try:
            self._terminate_transient(unit)
        except ContainmentFailure as cleanup_error:
            failure = ContainmentFailure(
                f"{message}; exact transient-unit cleanup also failed"
            )
            if cause is not None:
                failure.add_note(f"original uncertainty: {cause!r}")
            raise failure from cleanup_error
        failure = ContainmentFailure(message)
        if cause is not None:
            raise failure from cause
        raise failure

    def run_control(
        self,
        argv: tuple[str, ...],
    ) -> subprocess.CompletedProcess[Any]:
        normalized = tuple(str(argument) for argument in argv)
        if not self._valid_service_control_argv(normalized):
            raise OSError("refusing unexpected systemd control command")
        action_timeout = self._management_timeout_seconds
        if len(normalized) >= 2 and normalized[1] == "start":
            action_timeout = max(
                action_timeout,
                120.0 + max(1.0, self._management_timeout_seconds),
            )
        elif len(normalized) >= 2 and normalized[1] == "stop":
            action_timeout = max(
                action_timeout,
                30.0 + max(1.0, self._management_timeout_seconds),
            )
        return self._host_command(normalized, timeout=action_timeout)

    def validate_controller_context(self) -> None:
        # Tests use a synthetic cgroup root.  A production runner must be the
        # actual Type=exec MainPID, so manually invoking this root controller
        # cannot create sibling transient services outside its unit lifetime.
        if self._cgroup_root != Path("/sys/fs/cgroup"):
            return
        if not (self._cgroup_root / "cgroup.controllers").is_file():
            raise ContainmentFailure("a unified cgroup v2 hierarchy is required")
        shown = self._host_command(
            (
                str(self._systemctl_path),
                "show",
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=MainPID",
                self._controller_unit,
            ),
            timeout=self._management_timeout_seconds,
        )
        if shown.returncode != 0:
            raise ContainmentFailure("controller systemd unit is unavailable")
        fields = _parse_exact_fields(
            _stdout_text(shown),
            frozenset({"LoadState", "ActiveState", "MainPID"}),
        )
        if fields is None:
            raise ContainmentFailure("controller systemd identity is malformed")
        try:
            main_pid = int(fields["MainPID"], 10)
        except ValueError as exc:
            raise ContainmentFailure("controller systemd MainPID is malformed") from exc
        if (
            fields["LoadState"] != "loaded"
            or fields["ActiveState"] != "active"
            or main_pid != os.getpid()
        ):
            raise ContainmentFailure("controller is not its systemd unit MainPID")

    def _next_unit_name(self) -> str:
        self._unit_counter += 1
        return (
            f"honeybuy-release-workload-{self._nonce}-"
            f"{self._unit_counter}.service"
        )

    def _transient_argv(
        self,
        unit: str,
        payload: tuple[str, ...],
        *,
        cwd: Path,
        uid: int,
        gid: int,
        env: dict[str, str],
        writable_paths: tuple[Path, ...],
        network_access: bool,
    ) -> tuple[str, ...]:
        properties = [
            "Slice=system.slice",
            "Restart=no",
            "ExitType=main",
            "KillMode=control-group",
            "SendSIGKILL=yes",
            "FinalKillSignal=SIGKILL",
            f"TimeoutStartSec={self._management_timeout_seconds}s",
            f"TimeoutStopSec={self._management_timeout_seconds}s",
            f"RuntimeMaxSec={self._timeout_seconds}s",
            f"User={uid}",
            f"Group={gid}",
            "SupplementaryGroups=",
            f"WorkingDirectory={cwd}",
            "UMask=0027",
            "NoNewPrivileges=yes",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes",
            "ProtectKernelLogs=yes",
            "ProtectControlGroups=yes",
            "ProtectHostname=yes",
            "ProtectClock=yes",
            "LockPersonality=yes",
            "RestrictSUIDSGID=yes",
            "RestrictNamespaces=yes",
            "RestrictRealtime=yes",
            "Delegate=no",
            "TasksMax=256",
            "Environment=",
            "PassEnvironment=",
            f"BindsTo={self._controller_unit}",
            f"After={self._controller_unit}",
        ]
        if network_access:
            properties.extend(
                (
                    "PrivateNetwork=no",
                    "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
                )
            )
        else:
            properties.extend(
                (
                    "PrivateNetwork=yes",
                    "RestrictAddressFamilies=AF_UNIX",
                )
            )
        properties.extend(f"ReadWritePaths={path}" for path in writable_paths)
        arguments: list[str] = [
            str(self._systemd_run_path),
            "--quiet",
            "--wait",
            "--pipe",
            f"--unit={unit}",
            "--service-type=exec",
        ]
        arguments.extend(f"--property={property_value}" for property_value in properties)
        arguments.extend(f"--setenv={name}={value}" for name, value in sorted(env.items()))
        arguments.extend(("--", *payload))
        return tuple(arguments)

    def _observe_transient(self, unit: str) -> TransientUnitObservation | None:
        shown = self._host_command(
            (
                str(self._systemctl_path),
                "show",
                "--no-pager",
                "--property=ControlGroup",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--property=ExecMainCode",
                "--property=ExecMainStatus",
                unit,
            ),
            timeout=self._management_timeout_seconds,
        )
        required_fields = {
            "ControlGroup",
            "LoadState",
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainCode",
            "ExecMainStatus",
        }
        allow_missing_code = self._cgroup_root != Path("/sys/fs/cgroup")
        fields = _parse_exact_fields(
            _stdout_text(shown),
            frozenset(
                required_fields - ({"ExecMainCode"} if allow_missing_code else set())
            ),
        )
        if fields is None and allow_missing_code:
            fields = _parse_exact_fields(
                _stdout_text(shown),
                frozenset(required_fields),
            )
        if fields is None:
            return None
        if shown.returncode != 0 and fields["LoadState"] != "not-found":
            return None
        try:
            exit_status = int(fields["ExecMainStatus"], 10)
        except ValueError:
            return None
        return TransientUnitObservation(
            load_state=fields["LoadState"],
            active_state=fields["ActiveState"],
            sub_state=fields["SubState"],
            result=fields["Result"],
            exec_main_code=fields.get("ExecMainCode", "exited"),
            exit_status=exit_status,
            control_group=fields["ControlGroup"],
        )

    def _cgroup_path(self, unit: str) -> Path:
        return self._cgroup_root / "system.slice" / unit

    def _require_empty_cgroup(self, unit: str) -> None:
        populated = self._cgroup_population(unit)
        if populated is None:
            raise ContainmentFailure(
                "transient cgroup disappeared before emptiness was proven"
            )
        if populated != 0:
            self._terminate_transient(unit)
            raise ContainmentFailure("workload cgroup did not empty")

    def _cgroup_population(self, unit: str) -> int | None:
        events = self._cgroup_path(unit) / "cgroup.events"
        try:
            descriptor = os.open(
                events,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ContainmentFailure("transient cgroup events are unavailable") from exc
        try:
            payload = bytearray()
            while chunk := os.read(descriptor, 4096):
                payload.extend(chunk)
                if len(payload) > 64 * 1024:
                    raise ContainmentFailure("transient cgroup events are oversized")
        finally:
            os.close(descriptor)
        try:
            lines = bytes(payload).decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise ContainmentFailure("transient cgroup events are malformed") from exc
        populated = [line for line in lines if line.startswith("populated ")]
        if populated == ["populated 0"]:
            return 0
        if populated == ["populated 1"]:
            return 1
        raise ContainmentFailure("transient cgroup population is malformed")

    def _terminate_transient(self, unit: str) -> None:
        stop = self._host_command(
            (str(self._systemctl_path), "stop", "--no-block", unit),
            timeout=self._management_timeout_seconds,
        )
        if stop.returncode != 0:
            if (
                self._cgroup_population(unit) is None
                and self._unit_has_released_terminal_without_cgroup(unit)
            ):
                return
            raise ContainmentFailure("failed to stop transient unit")
        if self._wait_for_empty_or_unloaded(unit):
            return

        killed = self._host_command(
            (
                str(self._systemctl_path),
                "kill",
                "--kill-whom=all",
                "--signal=SIGKILL",
                unit,
            ),
            timeout=self._management_timeout_seconds,
        )
        if killed.returncode != 0:
            raise ContainmentFailure("failed to kill transient unit")
        if self._wait_for_empty_or_unloaded(unit):
            return
        raise ContainmentFailure("terminated workload cgroup did not empty")

    def _wait_for_empty_or_unloaded(self, unit: str) -> bool:
        deadline = time.monotonic() + self._containment_poll_window()
        while True:
            population = self._cgroup_population(unit)
            if population == 0 or (
                population is None
                and self._unit_has_released_terminal_without_cgroup(unit)
            ):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.025, remaining))

    def _containment_poll_window(self) -> float:
        # A synthetic hierarchy is used only by the deterministic unit tests;
        # production waits for the complete bounded systemd stop interval.
        if self._cgroup_root != Path("/sys/fs/cgroup"):
            return 0.0
        return self._management_timeout_seconds

    def _unit_is_exactly_unloaded(self, unit: str) -> bool:
        observation = self._observe_transient(unit)
        return bool(
            observation is not None
            and self._released_terminal_without_cgroup(unit, observation)
            and observation.load_state == "not-found"
        )

    def _unit_has_released_terminal_without_cgroup(self, unit: str) -> bool:
        observation = self._observe_transient(unit)
        return bool(
            observation is not None
            and self._released_terminal_without_cgroup(unit, observation)
        )

    def _released_terminal_without_cgroup(
        self,
        unit: str,
        observation: TransientUnitObservation,
        *,
        allow_failed_loaded: bool = True,
    ) -> bool:
        if observation.control_group or self._cgroup_path(unit).exists():
            return False
        if observation.load_state == "not-found":
            return (
                observation.active_state == "inactive"
                and observation.sub_state == "dead"
            )
        if observation.load_state != "loaded":
            return False
        if (
            observation.active_state == "inactive"
            and observation.sub_state == "dead"
        ):
            return True
        return bool(
            allow_failed_loaded
            and observation.active_state == "failed"
            and observation.sub_state == "failed"
        )

    def _loaded_terminal_with_expected_cgroup(
        self,
        unit: str,
        observation: TransientUnitObservation,
    ) -> bool:
        if (
            observation.load_state != "loaded"
            or observation.control_group != f"/system.slice/{unit}"
            or not self._cgroup_path(unit).exists()
        ):
            return False
        return (
            (
                observation.active_state == "inactive"
                and observation.sub_state == "dead"
            )
            or (
                observation.active_state == "failed"
                and observation.sub_state == "failed"
            )
        )

    def _reset_failed_unit(self, unit: str) -> None:
        try:
            reset = self._host_command(
                (str(self._systemctl_path), "reset-failed", unit),
                timeout=self._management_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainmentFailure("failed to reset transient unit") from exc
        if reset.returncode != 0:
            raise ContainmentFailure("failed to reset transient unit")
        deadline = time.monotonic() + self._containment_poll_window()
        while True:
            observation = self._observe_transient(unit)
            if observation is not None and self._released_terminal_without_cgroup(
                unit,
                observation,
                allow_failed_loaded=False,
            ):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ContainmentFailure("reset transient unit did not unload")
            time.sleep(min(0.025, remaining))

    def _host_command(
        self,
        argv: tuple[str, ...],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[Any]:
        try:
            process = subprocess.Popen(
                argv,
                cwd="/",
                env={"PATH": self._CLEAN_PATH},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
        except OSError as exc:
            raise ContainmentFailure("failed to execute systemd management command") from exc
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            # The client remains in the controller's own KillMode=control-group
            # unit.  Treat an unresponsive management boundary as fatal; the
            # controller must exit instead of performing further state changes.
            raise ContainmentFailure("bounded systemd management call timed out") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContainmentFailure(
                "systemd management failed after command launch"
            ) from exc
        if process.returncode is None:
            raise ContainmentFailure("systemd management command did not report exit")
        return subprocess.CompletedProcess(
            args=argv,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def _valid_service_control_argv(self, argv: tuple[str, ...]) -> bool:
        if not argv or argv[0] != str(self._systemctl_path):
            return False
        allowed = {
            (str(self._systemctl_path), "start", self._managed_service),
            (str(self._systemctl_path), "stop", self._managed_service),
            (
                str(self._systemctl_path),
                "is-active",
                "--quiet",
                self._managed_service,
            ),
            (
                str(self._systemctl_path),
                "show",
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                self._managed_service,
            ),
            (
                str(self._systemctl_path),
                "show",
                "--no-pager",
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
                "--property=NRestarts",
                "--property=ExecMainStatus",
                self._managed_service,
            ),
        }
        return argv in allowed


class UrllibHttpClient:
    def get_json(self, url: str, *, timeout: float) -> object:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "honeybuy-release-controller",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


class ReleaseController:
    def __init__(
        self,
        *,
        config: ControllerConfig,
        runner: Runner | None = None,
        http: HttpClient | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessRunner(
            timeout_seconds=config.command_timeout_seconds,
            management_timeout_seconds=config.management_timeout_seconds,
            systemd_run_path=config.systemd_run_path,
            systemctl_path=config.systemctl_path,
            cgroup_root=config.cgroup_root,
            controller_unit=config.controller_service_name,
            managed_service=config.service_name,
        )
        self.http = http or UrllibHttpClient()
        self.sleeper = sleeper or time.sleep

    def reconcile(self) -> ControllerResult:
        return self._with_lock(self._reconcile_locked)

    def deploy(self) -> ControllerResult:
        return self._with_lock(self._deploy_locked)

    def _with_lock(
        self,
        operation: Callable[[], ControllerResult],
    ) -> ControllerResult:
        config_error = self._config_error()
        if config_error is not None:
            return _result(ResultStatus.REJECTED, config_error)
        context_validator = getattr(self.runner, "validate_controller_context", None)
        if context_validator is not None:
            try:
                context_validator()
            except (ContainmentFailure, OSError, subprocess.SubprocessError):
                return _result(
                    ResultStatus.REJECTED,
                    "release controller is not running in its systemd unit",
                )

        descriptor: int | None = None
        try:
            lock_directory = self.config.lock_path.parent
            _ensure_directory(lock_directory, mode=0o700)
            if not _trusted_path_metadata(
                lock_directory,
                expected_uid=self._control_plane_uid(),
                expected_gid=self._control_plane_gid(),
                kind="directory",
                require_private=not self.config.test_mode,
            ):
                return _result(ResultStatus.REJECTED, "release lock directory is unsafe")
            if (
                not self.config.test_mode
                and not _root_ancestor_metadata_is_safe(lock_directory)
            ):
                return _result(ResultStatus.REJECTED, "release lock ancestor is unsafe")
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.config.lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            path_metadata = os.lstat(self.config.lock_path)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self._control_plane_uid()
                or metadata.st_gid != self._control_plane_gid()
                or metadata.st_mode & 0o022
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                return _result(ResultStatus.REJECTED, "release lock is unsafe")
            os.fchmod(descriptor, 0o600)
        except OSError:
            if descriptor is not None:
                os.close(descriptor)
            return _result(ResultStatus.REJECTED, "release lock is unavailable")
        assert descriptor is not None
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    return _result(ResultStatus.BUSY, "another reconcile is running")
                return _result(ResultStatus.REJECTED, "release lock failed")
            try:
                metadata = os.fstat(lock_file.fileno())
                path_metadata = os.lstat(self.config.lock_path)
            except OSError:
                return _result(ResultStatus.REJECTED, "release lock path changed")
            if (
                metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                return _result(ResultStatus.REJECTED, "release lock path changed")
            try:
                return operation()
            except (OSError, sqlite3.Error, ValueError):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "release controller encountered a local state error",
                )

    def _deploy_locked(self) -> ControllerResult:
        try:
            deployed_state_exists = _path_exists_lstat(
                self.config.deployed_state_path
            )
            bootstrap_journal_exists = _path_exists_lstat(
                self.config.bootstrap_journal_path
            )
        except OSError:
            return _result(
                ResultStatus.REJECTED,
                "bootstrap or deployed state is unavailable",
            )
        if (
            (bootstrap_journal_exists or not deployed_state_exists)
            and self.config.bootstrap_journal_path.parent
            != self.config.deployed_state_path.parent
        ):
            return _result(
                ResultStatus.REJECTED,
                "bootstrap journal is outside the trusted state directory",
            )
        if deployed_state_exists and self._read_deployed_sha() is None:
            return _result(
                ResultStatus.REJECTED,
                "invalid existing deployed state prevents bootstrap",
            )
        manifest_phases = {"installed"}
        if not deployed_state_exists and not bootstrap_journal_exists:
            manifest_phases = {"bootstrap_pending"}
        elif bootstrap_journal_exists:
            bootstrap_hint = _read_small_json_object(
                self.config.bootstrap_journal_path
            )
            if (
                bootstrap_hint is not None
                and bootstrap_hint.get("phase") == "awaiting_service"
            ):
                manifest_phases = {"bootstrap_pending", "installed"}
            else:
                manifest_phases = {"bootstrap_pending"}
        manifest_payload = self._load_control_plane_manifest()
        manifest_error = self._control_plane_manifest_error(
            allowed_phases=manifest_phases,
            payload=manifest_payload,
        )
        if manifest_error is not None:
            return _result(ResultStatus.REJECTED, manifest_error)
        allow_missing_deployed_state = not deployed_state_exists
        trust_error = self._trusted_control_plane_error(
            allow_missing_deployed_state=allow_missing_deployed_state,
        )
        if trust_error is not None:
            return _result(ResultStatus.REJECTED, trust_error)
        staging_error = self._release_staging_error()
        if staging_error is not None:
            return _result(ResultStatus.REJECTED, staging_error)
        if not self._discard_trusted_release_staging():
            return _result(ResultStatus.REJECTED, "release staging cleanup failed")
        atomic_recovery_error = self._recover_atomic_temps()
        if atomic_recovery_error is not None:
            return _result(ResultStatus.REJECTED, atomic_recovery_error)
        trust_error = self._trusted_control_plane_error(
            include_activation=True,
            allow_missing_generated_namespaces=True,
            allow_missing_deployed_state=allow_missing_deployed_state,
        )
        if trust_error is not None:
            return _result(ResultStatus.REJECTED, trust_error)
        bootstrap_journal = None
        if bootstrap_journal_exists:
            bootstrap_journal = self._load_valid_bootstrap_journal()
            if bootstrap_journal is None:
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "bootstrap journal is corrupt or inconsistent",
                )
        namespace_error = self._prepare_activation_namespaces()
        if namespace_error is not None:
            return _result(ResultStatus.REJECTED, namespace_error)
        trust_error = self._trusted_control_plane_error(
            include_activation=True,
            allow_missing_deployed_state=allow_missing_deployed_state,
        )
        if trust_error is not None:
            return _result(ResultStatus.REJECTED, trust_error)

        if bootstrap_journal is not None:
            return self._recover_interrupted_bootstrap(bootstrap_journal)
        if not deployed_state_exists:
            return self._bootstrap_pristine_host()
        if self._read_deployed_sha() is None:
            return _result(
                ResultStatus.REJECTED,
                "invalid existing deployed state prevents bootstrap",
            )

        recovery = self._recover_interrupted_activation()
        if recovery is not None:
            return recovery

        result = self._reconcile_locked()
        if result.status != ResultStatus.PREPARED or result.sha is None:
            return result
        return self._activate_release_locked(result.sha)

    def _prepare_activation_namespaces(self) -> str | None:
        expected_uid = self._control_plane_uid()
        expected_gid = self._control_plane_gid()
        for directory, label in (
            (self.config.deployment_receipts_dir, "deployment receipts directory"),
            (self.config.quarantine_dir, "quarantine directory"),
        ):
            try:
                os.lstat(directory)
            except FileNotFoundError:
                if not _trusted_path_metadata(
                    directory.parent,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                    kind="directory",
                ):
                    return f"{label} parent is unsafe"
                if (
                    not self.config.test_mode
                    and not _root_ancestor_metadata_is_safe(directory.parent)
                ):
                    return f"{label} ancestor is unsafe"
                try:
                    os.mkdir(directory, 0o700)
                    _fsync_directory(directory.parent)
                except OSError:
                    return f"{label} could not be created safely"
            except OSError:
                return f"{label} is unsafe"
        return None

    def _bootstrap_pristine_host(self) -> ControllerResult:
        if (
            self.config.bootstrap_journal_path.parent
            != self.config.deployed_state_path.parent
        ):
            return _result(
                ResultStatus.REJECTED,
                "bootstrap journal is outside the trusted state directory",
            )
        evidence_error = self._pristine_bootstrap_evidence_error()
        if evidence_error is not None:
            return evidence_error
        database_error = self._bootstrap_database_error()
        if database_error is not None:
            return _result(ResultStatus.REJECTED, database_error)
        service_state = self._bootstrap_service_state()
        if service_state is None:
            return _result(
                ResultStatus.REJECTED,
                "bootstrap managed service state is unavailable",
            )
        load_state, active_state, sub_state = service_state
        if load_state != "loaded":
            return _result(
                ResultStatus.REJECTED,
                "bootstrap requires the managed unit to be loaded",
            )
        if active_state != "inactive" or sub_state != "dead":
            return _result(
                ResultStatus.REJECTED,
                "bootstrap requires the managed service to be inactive",
            )

        repository_error = self._initialize_source_repository()
        if repository_error is not None:
            return repository_error
        remote_sha = self._fetch_and_resolve_main()
        if isinstance(remote_sha, ControllerResult):
            return remote_sha
        if not _is_canonical_sha(remote_sha):
            return _result(ResultStatus.REJECTED, "remote main is not canonical sha")
        workflow_result = self._require_green_workflow(remote_sha)
        if workflow_result is not None:
            return workflow_result
        verification_result = self._verify_candidate_authentication(remote_sha)
        if verification_result is not None:
            return verification_result

        journal: dict[str, Any] = {
            "version": 1,
            "candidate_sha": remote_sha,
            "phase": "intent",
            "phases": ["intent"],
        }
        if not self._write_bootstrap_journal(journal):
            if _path_exists_lstat(self.config.bootstrap_journal_path):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "bootstrap journal could not be validated after creation",
                    remote_sha,
                )
            return _result(
                ResultStatus.REJECTED,
                "failed to create bootstrap journal",
                remote_sha,
            )
        return self._continue_bootstrap(journal)

    def _pristine_bootstrap_evidence_error(self) -> ControllerResult | None:
        if _path_exists_lstat(self.config.deployment_journal_path):
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "activation journal prevents safe bootstrap",
            )
        if _path_exists_lstat(self.config.deployed_state_path):
            return _result(
                ResultStatus.REJECTED,
                "existing deployed state prevents bootstrap",
            )
        for link in (self.config.current_link, self.config.previous_link):
            if _path_exists_lstat(link):
                return _result(
                    ResultStatus.REJECTED,
                    "existing release link prevents bootstrap",
                )
        if not _directory_is_empty(self.config.releases_dir):
            return _result(
                ResultStatus.REJECTED,
                "nonempty releases directory prevents bootstrap",
            )
        if not _directory_is_empty(self.config.deployment_receipts_dir):
            return _result(
                ResultStatus.REJECTED,
                "existing deployment receipt prevents bootstrap",
            )
        if not _directory_is_empty(self.config.quarantine_dir):
            return _result(
                ResultStatus.REJECTED,
                "existing quarantine record prevents bootstrap",
            )
        return None

    def _bootstrap_database_error(self) -> str | None:
        try:
            _validate_bootstrap_database(
                self.config.database_path,
                expected_uid=(
                    None if self.config.test_mode else self.config.runtime_uid
                ),
                expected_gid=(
                    None if self.config.test_mode else self.config.runtime_gid
                ),
                require_root_ancestors=not self.config.test_mode,
            )
        except (OSError, sqlite3.Error, ValueError):
            return "bootstrap database is missing, unsafe, or schema-incompatible"
        return None

    def _bootstrap_service_state(self) -> tuple[str, str, str] | None:
        shown = self._run_control_command(
            (
                str(self.config.systemctl_path),
                "show",
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                self.config.service_name,
            )
        )
        if shown is None or shown.returncode != 0:
            return None
        fields = _parse_exact_fields(
            _stdout_text(shown),
            frozenset({"LoadState", "ActiveState", "SubState"}),
        )
        if fields is None:
            return None
        return fields["LoadState"], fields["ActiveState"], fields["SubState"]

    def _recover_interrupted_bootstrap(
        self,
        journal: dict[str, Any],
    ) -> ControllerResult:
        sha = journal["candidate_sha"]
        release = self.config.releases_dir / sha
        staging = self.config.releases_dir / f".{sha}.tmp"
        if (
            journal.get("phase") == "release_prepared"
            and not _path_exists_lstat(release)
            and not _path_exists_lstat(staging)
            and not _path_exists_lstat(self.config.current_link)
            and not _path_exists_lstat(self.config.deployed_state_path)
        ):
            if self._clear_bootstrap_journal():
                return _result(
                    ResultStatus.TRANSIENT,
                    "aborted bootstrap preparation was recovered",
                    sha,
                )
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "aborted bootstrap journal could not be cleared",
                sha,
            )
        state_error = self._bootstrap_recovery_state_error(journal)
        if state_error is not None:
            return _result(ResultStatus.INTERVENTION_REQUIRED, state_error, sha)
        database_error = self._bootstrap_database_error()
        if database_error is not None:
            return _result(ResultStatus.INTERVENTION_REQUIRED, database_error, sha)
        if journal["phase"] == "awaiting_service":
            return self._finalize_pending_bootstrap(journal)

        service_state = self._bootstrap_service_state()
        if service_state is None:
            return _result(
                ResultStatus.REJECTED,
                "bootstrap managed service state is unavailable",
                sha,
            )
        load_state, active_state, sub_state = service_state
        if load_state != "loaded":
            return _result(
                ResultStatus.REJECTED,
                "bootstrap requires the managed unit to be loaded",
                sha,
            )
        if active_state != "inactive" or sub_state != "dead":
            return _result(
                ResultStatus.REJECTED,
                "bootstrap requires the managed service to remain inactive",
                sha,
            )
        return self._continue_bootstrap(journal)

    def _continue_bootstrap(self, journal: dict[str, Any]) -> ControllerResult:
        sha = journal["candidate_sha"]
        release = self.config.releases_dir / sha
        phase = journal["phase"]
        state_error = self._bootstrap_recovery_state_error(journal)
        if state_error is not None:
            return _result(ResultStatus.INTERVENTION_REQUIRED, state_error, sha)

        if phase == "intent":
            if _path_exists_lstat(release):
                if not self._ready_release_is_safe(release, sha):
                    return _result(
                        ResultStatus.INTERVENTION_REQUIRED,
                        "bootstrap release side effect is unsafe",
                        sha,
                    )
            else:
                prepared = self._prepare_release(sha, release)
                if prepared is not None:
                    if not _path_exists_lstat(release) and not self._clear_bootstrap_journal():
                        return _result(
                            ResultStatus.INTERVENTION_REQUIRED,
                            "failed bootstrap preparation left a recovery journal",
                            sha,
                        )
                    return prepared
            if not self._advance_bootstrap_journal(journal, "release_prepared"):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "failed to journal prepared bootstrap release",
                    sha,
                )
            phase = "release_prepared"

        if phase == "release_prepared":
            if not self._ready_release_is_safe(release, sha):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "prepared bootstrap release failed validation",
                    sha,
                )
            health = self._healthcheck(release, self.config.database_path)
            if health is None or health.returncode != 0:
                return _result(
                    ResultStatus.REJECTED,
                    "bootstrap release is incompatible with the existing database",
                    sha,
                )

            current_exists = _path_exists_lstat(self.config.current_link)
            if not current_exists:
                final_sha = self._fetch_and_resolve_main()
                if isinstance(final_sha, ControllerResult):
                    return final_sha
                if final_sha != sha:
                    if not self._abort_uncommitted_bootstrap(journal, release):
                        return _result(
                            ResultStatus.INTERVENTION_REQUIRED,
                            "changed remote main left bootstrap recovery state",
                            sha,
                        )
                    return _result(
                        ResultStatus.TRANSIENT,
                        "remote main changed before bootstrap state commit",
                        sha,
                    )
                manifest_error = self._control_plane_manifest_error(
                    allowed_phases={"bootstrap_pending"},
                    candidate_release=release,
                )
                if manifest_error is not None:
                    return _result(ResultStatus.REJECTED, manifest_error, sha)
                service_state = self._bootstrap_service_state()
                if service_state != ("loaded", "inactive", "dead"):
                    return _result(
                        ResultStatus.REJECTED,
                        "bootstrap service did not remain loaded and inactive",
                        sha,
                    )
                database_error = self._bootstrap_database_error()
                if database_error is not None:
                    return _result(ResultStatus.REJECTED, database_error, sha)
                try:
                    self._switch_link(self.config.current_link, release)
                except OSError:
                    return _result(
                        ResultStatus.INTERVENTION_REQUIRED,
                        "failed to create bootstrap current link",
                        sha,
                    )
            elif self._release_link_target(self.config.current_link) != release:
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "bootstrap current-link side effect is inconsistent",
                    sha,
                )
            if not self._advance_bootstrap_journal(journal, "current_linked"):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "failed to journal bootstrap current link",
                    sha,
                )
            phase = "current_linked"

        if phase == "current_linked":
            deployed_state = self._read_deployed_sha()
            if deployed_state is None:
                if _path_exists_lstat(self.config.deployed_state_path):
                    return _result(
                        ResultStatus.INTERVENTION_REQUIRED,
                        "bootstrap deployed-state side effect is invalid",
                        sha,
                    )
                try:
                    self._write_deployed_sha(sha)
                except OSError:
                    return _result(
                        ResultStatus.INTERVENTION_REQUIRED,
                        "failed to write bootstrap deployed state",
                        sha,
                    )
            elif deployed_state != sha:
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "bootstrap deployed-state side effect is inconsistent",
                    sha,
                )
            if not self._advance_bootstrap_journal(journal, "state_written"):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "failed to journal bootstrap deployed state",
                    sha,
                )
            phase = "state_written"

        if phase == "state_written":
            if not self._advance_bootstrap_journal(journal, "awaiting_service"):
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "failed to journal pending bootstrap service confirmation",
                    sha,
                )
            phase = "awaiting_service"

        if phase != "awaiting_service":
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "bootstrap phase could not converge",
                sha,
            )
        return _result(
            ResultStatus.BOOTSTRAPPED,
            "bootstrap baseline prepared and awaiting service confirmation",
            sha,
        )

    def _bootstrap_recovery_state_error(
        self,
        journal: dict[str, Any],
    ) -> str | None:
        sha = journal["candidate_sha"]
        phase = journal["phase"]
        release = self.config.releases_dir / sha
        staging = self.config.releases_dir / f".{sha}.tmp"
        try:
            release_entries = tuple(self.config.releases_dir.iterdir())
            receipts = tuple(self.config.deployment_receipts_dir.iterdir())
            quarantine = tuple(self.config.quarantine_dir.iterdir())
        except OSError:
            return "bootstrap managed namespaces are unavailable"
        allowed_release_entries = {release}
        if phase == "intent":
            allowed_release_entries.add(staging)
        if any(path not in allowed_release_entries for path in release_entries) or quarantine:
            return "bootstrap managed namespaces contain inconsistent state"
        receipt_path = self.config.deployment_receipts_dir / f"{sha}.json"
        if any(path != receipt_path for path in receipts):
            return "bootstrap receipt namespace is inconsistent"
        if receipts and phase != "awaiting_service":
            return "bootstrap receipt exists before service confirmation"
        if receipts and _read_small_json_object(receipt_path) != self._bootstrap_receipt(
            journal
        ):
            return "bootstrap receipt is corrupt or inconsistent"
        if _path_exists_lstat(self.config.deployment_journal_path):
            return "activation journal conflicts with bootstrap recovery"
        if _path_exists_lstat(self.config.previous_link):
            return "previous release link conflicts with bootstrap recovery"

        release_exists = _path_exists_lstat(release)
        current_exists = _path_exists_lstat(self.config.current_link)
        state_exists = _path_exists_lstat(self.config.deployed_state_path)
        phase_index = BOOTSTRAP_PHASES.index(phase)
        if phase_index >= 1 and not release_exists:
            return "bootstrap journal requires a prepared release"
        if release_exists and not self._ready_release_is_safe(release, sha):
            return "bootstrap prepared release is unsafe"
        if phase_index == 0 and (current_exists or state_exists):
            return "bootstrap side effects are ahead of the journal"
        if phase_index == 1 and state_exists:
            return "bootstrap deployed state is ahead of the journal"
        if phase_index >= 2 and not current_exists:
            return "bootstrap journal requires the current release link"
        if current_exists and self._release_link_target(self.config.current_link) != release:
            return "bootstrap current release link is inconsistent"
        if phase_index >= 3 and not state_exists:
            return "bootstrap journal requires deployed state"
        if state_exists and self._read_deployed_sha() != sha:
            return "bootstrap deployed state is inconsistent"
        return None

    def _finalize_pending_bootstrap(
        self,
        journal: dict[str, Any],
    ) -> ControllerResult:
        sha = journal["candidate_sha"]
        release = self.config.releases_dir / sha
        manifest_payload = self._load_control_plane_manifest()
        manifest_error = self._control_plane_manifest_error(
            allowed_phases={"bootstrap_pending", "installed"},
            candidate_release=release,
            payload=manifest_payload,
        )
        if manifest_error is not None:
            return _result(ResultStatus.REJECTED, manifest_error, sha)
        assert manifest_payload is not None
        if manifest_payload["phase"] == "bootstrap_pending":
            return _result(
                ResultStatus.BOOTSTRAPPED,
                "bootstrap baseline is awaiting control-plane installation",
                sha,
            )
        service_state = self._bootstrap_service_state()
        if service_state is None:
            return _result(
                ResultStatus.REJECTED,
                "bootstrap managed service state is unavailable",
                sha,
            )
        if service_state[0] != "loaded":
            return _result(
                ResultStatus.REJECTED,
                "bootstrap managed unit is not loaded",
                sha,
            )
        if service_state == ("loaded", "inactive", "dead"):
            return _result(
                ResultStatus.BOOTSTRAPPED,
                "bootstrap baseline is still awaiting service start",
                sha,
            )
        if service_state != ("loaded", "active", "running"):
            return _result(
                ResultStatus.REJECTED,
                "bootstrap managed service is not safely active",
                sha,
            )
        if not self._release_is_stably_healthy(release):
            return _result(
                ResultStatus.REJECTED,
                "bootstrap service failed stable health checks",
                sha,
            )
        try:
            self._write_bootstrap_receipt(journal)
            self._clear_bootstrap_journal(required=True)
        except OSError:
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "healthy bootstrap could not be finalized durably",
                sha,
            )
        return _result(
            ResultStatus.BOOTSTRAPPED,
            "bootstrap service confirmed healthy",
            sha,
        )

    def _load_valid_bootstrap_journal(self) -> dict[str, Any] | None:
        payload = _read_small_json_object(self.config.bootstrap_journal_path)
        if payload is None or set(payload) != {
            "version",
            "candidate_sha",
            "phase",
            "phases",
        }:
            return None
        phase = payload.get("phase")
        phases = payload.get("phases")
        if (
            payload.get("version") != 1
            or not _is_canonical_sha(payload.get("candidate_sha"))
            or phase not in BOOTSTRAP_PHASES
            or not isinstance(phases, list)
        ):
            return None
        expected = list(BOOTSTRAP_PHASES[: BOOTSTRAP_PHASES.index(phase) + 1])
        return payload if phases == expected else None

    def _write_bootstrap_journal(self, journal: dict[str, Any]) -> bool:
        try:
            _atomic_write_json(self.config.bootstrap_journal_path, journal, mode=0o600)
            metadata = os.lstat(self.config.bootstrap_journal_path)
        except OSError:
            return False
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_uid == self._control_plane_uid()
            and metadata.st_gid == self._control_plane_gid()
        )

    def _advance_bootstrap_journal(
        self,
        journal: dict[str, Any],
        phase: str,
    ) -> bool:
        current = journal.get("phase")
        if current not in BOOTSTRAP_PHASES or phase not in BOOTSTRAP_PHASES:
            return False
        if BOOTSTRAP_PHASES.index(phase) != BOOTSTRAP_PHASES.index(current) + 1:
            return False
        journal["phase"] = phase
        journal["phases"].append(phase)
        return self._write_bootstrap_journal(journal)

    def _write_bootstrap_receipt(self, journal: dict[str, Any]) -> None:
        receipt = self._bootstrap_receipt(journal)
        destination = self.config.deployment_receipts_dir / (
            f"{journal['candidate_sha']}.json"
        )
        if _path_exists_lstat(destination):
            existing = _read_small_json_object(destination)
            if existing != receipt:
                raise OSError("existing bootstrap receipt is inconsistent")
            return
        _atomic_write_json(destination, receipt, mode=0o600)

    def _bootstrap_receipt(self, journal: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "candidate_sha": journal["candidate_sha"],
            "phase": "healthy",
            "phases": list(journal["phases"]),
            "bootstrap": True,
        }

    def _clear_bootstrap_journal(self, *, required: bool = False) -> bool:
        try:
            _unlink_regular_or_missing(self.config.bootstrap_journal_path)
            _fsync_directory(self.config.bootstrap_journal_path.parent)
        except FileNotFoundError:
            return True
        except OSError:
            if required:
                raise
            return False
        return True

    def _abort_uncommitted_bootstrap(
        self,
        journal: dict[str, Any],
        release: Path,
    ) -> bool:
        if journal.get("phase") not in {"intent", "release_prepared"}:
            return False
        if _path_exists_lstat(self.config.current_link) or _path_exists_lstat(
            self.config.deployed_state_path
        ):
            return False
        self._discard_partial_release(release, journal["candidate_sha"])
        if _path_exists_lstat(release):
            return False
        return self._clear_bootstrap_journal()

    def _reconcile_locked(self) -> ControllerResult:
        manifest_error = self._control_plane_manifest_error(
            allowed_phases={"installed"},
        )
        if manifest_error is not None:
            return _result(ResultStatus.REJECTED, manifest_error)
        if self.config.deployment_journal_path.exists() or (
            self.config.deployment_journal_path.is_symlink()
        ):
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "an unfinished activation journal requires recovery",
            )
        try:
            _ensure_directory(self.config.source_repo, mode=0o755)
            _ensure_directory(self.config.releases_dir, mode=0o755)
            _ensure_directory(self.config.empty_work_dir, mode=0o755)
        except OSError:
            return _result(ResultStatus.REJECTED, "release directories are unsafe")

        trust_error = self._trusted_control_plane_error()
        if trust_error is not None:
            return _result(ResultStatus.REJECTED, trust_error)
        staging_error = self._release_staging_error()
        if staging_error is not None:
            return _result(ResultStatus.REJECTED, staging_error)
        if not self._discard_trusted_release_staging():
            return _result(ResultStatus.REJECTED, "release staging cleanup failed")
        atomic_recovery_error = self._recover_atomic_temps()
        if atomic_recovery_error is not None:
            return _result(ResultStatus.REJECTED, atomic_recovery_error)
        if _path_exists_lstat(self.config.deployment_journal_path):
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "an unfinished activation journal requires recovery",
            )

        deployed_sha = self._read_deployed_sha()
        if deployed_sha is None:
            return _result(ResultStatus.REJECTED, "missing or invalid deployed sha")

        repository_error = self._initialize_source_repository()
        if repository_error is not None:
            return repository_error

        remote_sha = self._fetch_and_resolve_main()
        if isinstance(remote_sha, ControllerResult):
            return remote_sha
        if not _is_canonical_sha(remote_sha):
            return _result(ResultStatus.REJECTED, "remote main is not canonical sha")

        if remote_sha == deployed_sha:
            return self._check_noop_release(remote_sha)
        if self._candidate_is_quarantined(remote_sha):
            return _result(ResultStatus.REJECTED, "candidate release is quarantined", remote_sha)

        workflow_result = self._require_green_workflow(remote_sha)
        if workflow_result is not None:
            return workflow_result

        verification_result = self._verify_candidate(remote_sha, deployed_sha)
        if verification_result is not None:
            return verification_result

        release = self.config.releases_dir / remote_sha
        if release.exists() or release.is_symlink():
            if not self._ready_release_is_safe(release, remote_sha):
                return _result(
                    ResultStatus.REJECTED,
                    "existing release is missing a valid ready manifest",
                    remote_sha,
                )
        else:
            prepared = self._prepare_release(remote_sha, release)
            if prepared is not None:
                return prepared

        final_sha = self._fetch_and_resolve_main()
        if isinstance(final_sha, ControllerResult):
            return final_sha
        if final_sha != remote_sha:
            return _result(
                ResultStatus.TRANSIENT,
                "remote main changed while preparing release",
                remote_sha,
            )

        return _result(ResultStatus.PREPARED, "release prepared", remote_sha)

    def _config_error(self) -> str | None:
        if not isinstance(self.config.test_mode, bool):
            return "test mode flag is invalid"
        identity_values = (
            self.config.build_uid,
            self.config.build_gid,
            self.config.runtime_uid,
            self.config.runtime_gid,
            self.config.release_uid,
            self.config.release_gid,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in identity_values
        ):
            return "required deployment identity is missing or invalid"
        if not self.config.test_mode:
            assert self.config.build_uid is not None
            assert self.config.build_gid is not None
            assert self.config.runtime_uid is not None
            assert self.config.runtime_gid is not None
            if os.geteuid() != 0:
                return "production release controller must run as root"
            if self.config.build_uid == 0 or self.config.runtime_uid == 0:
                return "build and runtime identities must be non-root"
            if (
                self.config.build_uid == self.config.runtime_uid
                or self.config.build_gid == self.config.runtime_gid
            ):
                return "build and runtime identities must be distinct"
            if self.config.release_uid != 0 or self.config.release_gid != 0:
                return "production releases must be owned by root"
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.config.repository):
            return "repository identity is invalid"
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.config.branch):
            return "deployment branch is invalid"
        parsed_remote = urllib.parse.urlsplit(self.config.remote_url)
        if (
            parsed_remote.scheme != "https"
            or parsed_remote.netloc != "github.com"
            or parsed_remote.query
            or parsed_remote.fragment
            or parsed_remote.path != f"/{self.config.repository}.git"
        ):
            return "remote repository URL is invalid"
        if (
            isinstance(self.config.stable_health_checks, bool)
            or not isinstance(self.config.stable_health_checks, int)
            or self.config.stable_health_checks < 1
        ):
            return "stable health check count is invalid"
        durations = (
            ("api timeout", self.config.api_timeout_seconds, False),
            ("command timeout", self.config.command_timeout_seconds, False),
            (
                "management timeout",
                self.config.management_timeout_seconds,
                False,
            ),
            (
                "stable health interval",
                self.config.stable_health_interval_seconds,
                True,
            ),
        )
        for label, value, zero_allowed in durations:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or (not zero_allowed and value == 0)
            ):
                return f"{label} is invalid"
        if not _safe_relative_name(self.config.workflow_path):
            return "workflow path is invalid"
        workflow = PurePosixPath(self.config.workflow_path)
        if (
            workflow.parent != PurePosixPath(".github/workflows")
            or workflow.suffix not in {".yml", ".yaml"}
        ):
            return "workflow path is invalid"
        if not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", self.config.service_name):
            return "service name is invalid"
        if not re.fullmatch(
            r"[A-Za-z0-9_.@:-]+\.service",
            self.config.controller_service_name,
        ):
            return "controller service name is invalid"
        executable_paths = (
            self.config.git_path,
            self.config.uv_path,
            self.config.systemctl_path,
            self.config.systemd_run_path,
            self.config.system_python_path,
        )
        if any(not path.is_absolute() or "\0" in str(path) for path in executable_paths):
            return "deployment executable path is invalid"
        if not self.config.cgroup_root.is_absolute():
            return "cgroup root is invalid"
        if not self.config.test_mode and (
            self.config.systemctl_path != Path("/usr/bin/systemctl")
            or self.config.systemd_run_path != Path("/usr/bin/systemd-run")
            or self.config.cgroup_root != Path("/sys/fs/cgroup")
        ):
            return "production systemd control paths are invalid"
        return None

    def _control_plane_uid(self) -> int:
        return self.config.release_uid if self.config.test_mode else 0

    def _control_plane_gid(self) -> int:
        return self.config.release_gid if self.config.test_mode else 0

    def _load_control_plane_manifest(self) -> dict[str, Any] | None:
        path = self.config.control_plane_manifest_path
        expected_uid = self._control_plane_uid()
        expected_gid = self._control_plane_gid()
        if not _trusted_path_metadata(
            path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            kind="file",
            require_private=True,
        ):
            return None
        try:
            metadata = os.lstat(path)
        except OSError:
            return None
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            return None
        if not self.config.test_mode and not _root_ancestor_metadata_is_safe(path):
            return None
        payload = _read_small_json_object(path)
        if payload is None or set(payload) != {"version", "phase", "files"}:
            return None
        files = payload.get("files")
        if (
            payload.get("version") != 1
            or not isinstance(payload.get("phase"), str)
            or not isinstance(files, dict)
            or set(files) != set(CONTROL_PLANE_PATH_FIELDS)
            or any(
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for digest in files.values()
            )
        ):
            return None
        return payload

    def _control_plane_manifest_error(
        self,
        *,
        allowed_phases: set[str],
        candidate_release: Path | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str | None:
        if payload is None:
            payload = self._load_control_plane_manifest()
        if payload is None:
            return "control-plane manifest is missing or unsafe"
        if payload["phase"] not in allowed_phases:
            return "control-plane manifest phase is invalid"
        files = payload["files"]
        assert isinstance(files, dict)
        expected_uid = self._control_plane_uid()
        expected_gid = self._control_plane_gid()
        for relative, field_name in CONTROL_PLANE_PATH_FIELDS.items():
            installed = getattr(self.config, field_name)
            try:
                installed_exists = _path_exists_lstat(installed)
            except OSError:
                return f"installed control-plane artifact is unavailable: {relative}"
            if (
                not installed_exists
                and payload["phase"] == "bootstrap_pending"
                and relative == "deploy/systemd/honeybuy-tg.service"
            ):
                continue
            bootstrap_legacy_app_unit = (
                payload["phase"] == "bootstrap_pending"
                and relative == "deploy/systemd/honeybuy-tg.service"
            )
            if not _trusted_path_metadata(
                installed,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
                kind="file",
            ):
                return f"installed control-plane artifact is unsafe: {relative}"
            if (
                not self.config.test_mode
                and not _root_ancestor_metadata_is_safe(installed)
            ):
                return f"installed control-plane ancestor is unsafe: {relative}"
            if bootstrap_legacy_app_unit:
                continue
            try:
                digest = _file_sha256(installed)
            except (OSError, ValueError):
                return f"installed control-plane artifact is unreadable: {relative}"
            if digest != files[relative]:
                return f"installed control-plane artifact differs: {relative}"
        if candidate_release is not None:
            for relative, expected_digest in files.items():
                candidate = candidate_release / relative
                try:
                    digest = _file_sha256(candidate)
                except (OSError, ValueError):
                    return f"candidate control-plane artifact is unsafe: {relative}"
                if digest != expected_digest:
                    return f"candidate control-plane artifact differs: {relative}"
        return None

    def _release_staging_error(self) -> str | None:
        try:
            entries = tuple(self.config.releases_dir.iterdir())
        except OSError:
            return "release staging namespace is unavailable"
        staging_entries = [entry for entry in entries if entry.name.startswith(".")]
        if len(staging_entries) > 1:
            return "release staging namespace is ambiguous"
        for staging in staging_entries:
            if re.fullmatch(r"\.[0-9a-f]{40}\.tmp", staging.name) is None:
                return "release staging namespace contains an unexpected entry"
            if not _trusted_path_metadata(
                staging,
                expected_uid=self._control_plane_uid(),
                expected_gid=self._control_plane_gid(),
                kind="directory",
            ):
                return "release staging directory is unsafe"
        return None

    def _discard_trusted_release_staging(self) -> bool:
        try:
            staging_entries = tuple(
                entry
                for entry in self.config.releases_dir.iterdir()
                if entry.name.startswith(".")
            )
            for staging in staging_entries:
                _remove_tree_nofollow(staging)
            if staging_entries:
                _fsync_directory(self.config.releases_dir)
        except OSError:
            return False
        return True

    def _recover_atomic_temps(self) -> str | None:
        expected_uid = self._control_plane_uid()
        expected_gid = self._control_plane_gid()
        candidates: list[tuple[Path, Path, int, str]] = []
        fixed_destinations = (
            (self.config.deployed_state_path, 0o644, "deployed_state"),
            (self.config.deployment_journal_path, 0o600, "activation_journal"),
            (self.config.bootstrap_journal_path, 0o600, "bootstrap_journal"),
        )
        for destination, mode, kind in fixed_destinations:
            temporary = destination.with_name(f".{destination.name}.tmp")
            try:
                exists = _path_exists_lstat(temporary)
            except OSError:
                return "atomic state temporary is unavailable"
            if exists:
                candidates.append((temporary, destination, mode, kind))

        for directory, kind in (
            (self.config.deployment_receipts_dir, "receipt"),
            (self.config.quarantine_dir, "quarantine"),
        ):
            try:
                metadata = os.lstat(directory)
            except FileNotFoundError:
                continue
            except OSError:
                return f"atomic {kind} namespace is unavailable"
            if not (
                stat.S_ISDIR(metadata.st_mode)
                and not stat.S_ISLNK(metadata.st_mode)
                and metadata.st_uid == expected_uid
                and metadata.st_gid == expected_gid
                and not metadata.st_mode & 0o022
            ):
                return f"atomic {kind} namespace is unsafe"
            try:
                entries = tuple(directory.iterdir())
            except OSError:
                return f"atomic {kind} namespace is unavailable"
            for entry in entries:
                if re.fullmatch(r"[0-9a-f]{40}\.json", entry.name):
                    continue
                matched = re.fullmatch(r"\.([0-9a-f]{40}\.json)\.tmp", entry.name)
                if matched is None:
                    return f"atomic {kind} namespace contains an unexpected entry"
                candidates.append(
                    (entry, directory / matched.group(1), 0o600, kind)
                )

        # Validate the entire inventory before changing any directory entry.
        for temporary, destination, mode, kind in candidates:
            if not _trusted_path_metadata(
                temporary,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
                kind="file",
                require_private=mode == 0o600,
            ):
                return f"atomic {kind} temporary is unsafe"
            try:
                if stat.S_IMODE(os.lstat(temporary).st_mode) != mode:
                    return f"atomic {kind} temporary mode is unsafe"
            except OSError:
                return f"atomic {kind} temporary is unavailable"
            payload = self._atomic_temp_payload(temporary, kind)
            if payload is None:
                return f"atomic {kind} temporary content is invalid"
            try:
                destination_exists = _path_exists_lstat(destination)
            except OSError:
                return f"atomic {kind} destination is unavailable"
            if destination_exists and not self._atomic_destination_is_valid(
                destination,
                kind,
            ):
                return f"atomic {kind} destination is unsafe"

        try:
            for temporary, destination, _mode, _kind in candidates:
                if _path_exists_lstat(destination):
                    _unlink_regular_or_missing(temporary)
                else:
                    os.replace(temporary, destination)
                _fsync_directory(destination.parent)
        except OSError:
            return "atomic state recovery failed"
        return None

    def _atomic_temp_payload(self, path: Path, kind: str) -> object | None:
        if kind == "deployed_state":
            try:
                descriptor, metadata = _open_pinned_regular(path)
                try:
                    if metadata.st_size > 128:
                        return None
                    payload = os.read(descriptor, 129)
                    if os.read(descriptor, 1):
                        return None
                    if not _path_matches_metadata(path, metadata):
                        return None
                finally:
                    os.close(descriptor)
                value = payload.decode("ascii")
            except (OSError, UnicodeError, ValueError):
                return None
            return value if value == f"{value.strip()}\n" and _is_canonical_sha(value.strip()) else None

        payload = _read_small_json_object(path)
        if payload is None:
            return None
        if kind == "bootstrap_journal":
            return payload if self._bootstrap_journal_shape_is_valid(payload) else None
        if kind == "activation_journal":
            return payload if self._activation_journal_shape_is_valid(payload) else None
        if kind == "receipt":
            expected = self._receipt_recovery_payload()
            return payload if expected is not None and payload == expected else None
        if kind == "quarantine":
            journal = _read_small_json_object(self.config.deployment_journal_path)
            if journal is None or not self._activation_journal_shape_is_valid(journal):
                return None
            expected = {
                "version": 1,
                "candidate_sha": journal["candidate_sha"],
                "reason": self._rollback_reason(journal),
            }
            return payload if payload == expected else None
        return None

    def _atomic_destination_is_valid(self, path: Path, kind: str) -> bool:
        mode = 0o644 if kind == "deployed_state" else 0o600
        if not _trusted_path_metadata(
            path,
            expected_uid=self._control_plane_uid(),
            expected_gid=self._control_plane_gid(),
            kind="file",
            require_private=mode == 0o600,
        ):
            return False
        try:
            if stat.S_IMODE(os.lstat(path).st_mode) != mode:
                return False
        except OSError:
            return False
        if kind == "deployed_state":
            return self._read_deployed_sha() is not None
        if kind == "bootstrap_journal":
            return self._load_valid_bootstrap_journal() is not None
        if kind == "activation_journal":
            return self._load_valid_journal() is not None
        if kind == "receipt":
            payload = _read_small_json_object(path)
            expected = self._receipt_recovery_payload()
            return payload is not None and expected is not None and payload == expected
        if kind == "quarantine":
            return self._quarantine_record_is_valid(path)
        return False

    def _receipt_recovery_payload(self) -> dict[str, Any] | None:
        bootstrap_payload = self._bootstrap_receipt_recovery_payload()
        activation_payload = self._activation_receipt_recovery_payload()
        if bootstrap_payload is not None and activation_payload is not None:
            return None
        return bootstrap_payload if bootstrap_payload is not None else activation_payload

    def _bootstrap_receipt_recovery_payload(self) -> dict[str, Any] | None:
        journal = _read_small_json_object(self.config.bootstrap_journal_path)
        if (
            journal is None
            or not self._bootstrap_journal_shape_is_valid(journal)
            or journal.get("phase") != "awaiting_service"
        ):
            return None
        return self._bootstrap_receipt(journal)

    def _activation_receipt_recovery_payload(self) -> dict[str, Any] | None:
        journal = self._load_valid_journal()
        if journal is None or journal.get("phase") != "healthy":
            return None
        return self._activation_receipt(journal)

    def _bootstrap_journal_shape_is_valid(self, payload: dict[str, Any]) -> bool:
        if set(payload) != {"version", "candidate_sha", "phase", "phases"}:
            return False
        phase = payload.get("phase")
        return bool(
            payload.get("version") == 1
            and _is_canonical_sha(payload.get("candidate_sha"))
            and phase in BOOTSTRAP_PHASES
            and payload.get("phases")
            == list(BOOTSTRAP_PHASES[: BOOTSTRAP_PHASES.index(phase) + 1])
        )

    def _activation_journal_shape_is_valid(self, payload: dict[str, Any]) -> bool:
        phase = payload.get("phase")
        candidate = payload.get("candidate_sha")
        previous = payload.get("previous_sha")
        phases = payload.get("phases")
        if (
            payload.get("version") != 1
            or not _is_canonical_sha(candidate)
            or not _is_canonical_sha(previous)
            or not isinstance(phases, list)
        ):
            return False
        if phase in ACTIVATION_PHASES:
            return phases == list(ACTIVATION_PHASES[: ACTIVATION_PHASES.index(phase) + 1])
        rollback_from = payload.get("rollback_from_phase")
        return bool(
            phase in {"rollback_pending", "rollback_start_requested", "intervention_required"}
            and rollback_from in ACTIVATION_PHASES
            and phases
            == list(
                ACTIVATION_PHASES[: ACTIVATION_PHASES.index(rollback_from) + 1]
            )
        )

    def _rollback_reason(self, journal: dict[str, Any]) -> str:
        reason = journal.get("rollback_reason")
        if isinstance(reason, str) and reason:
            return reason
        if journal.get("phase") in {"start_requested", "health_checking"}:
            return "recovered interrupted post-start activation"
        return "interrupted activation was rolled back"

    def _trusted_control_plane_error(
        self,
        *,
        include_activation: bool = False,
        allow_missing_generated_namespaces: bool = False,
        allow_missing_deployed_state: bool = False,
    ) -> str | None:
        expected_uid = self._control_plane_uid()
        expected_gid = self._control_plane_gid()
        paths: tuple[tuple[Path, str, str], ...] = (
            (self.config.source_repo, "directory", "source repository"),
            (self.config.releases_dir, "directory", "releases directory"),
            (self.config.current_link.parent, "directory", "release link directory"),
            (self.config.deployed_state_path.parent, "directory", "state directory"),
            (self.config.deployed_state_path, "file", "deployed state"),
            (self.config.allowed_signers_path.parent, "directory", "trust directory"),
            (self.config.allowed_signers_path, "file", "allowed signers file"),
        )
        if include_activation:
            paths += (
                (
                    self.config.previous_link.parent,
                    "directory",
                    "previous release link directory",
                ),
                (
                    self.config.deployment_journal_path.parent,
                    "directory",
                    "deployment journal directory",
                ),
                (
                    self.config.database_backup_dir,
                    "directory",
                    "database backup directory",
                ),
                (self.config.scratch_dir, "directory", "database scratch directory"),
                (self.config.empty_work_dir, "directory", "empty work directory"),
                (
                    self.config.deployment_receipts_dir,
                    "directory",
                    "deployment receipts directory",
                ),
                (self.config.quarantine_dir, "directory", "quarantine directory"),
            )
        generated_namespaces = {
            self.config.deployment_receipts_dir,
            self.config.quarantine_dir,
        }
        for path, kind, label in paths:
            if allow_missing_deployed_state and path == self.config.deployed_state_path:
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                except OSError:
                    return f"{label} is unsafe"
            if allow_missing_generated_namespaces and path in generated_namespaces:
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                except OSError:
                    return f"{label} is unsafe"
            if not _trusted_path_metadata(
                path,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
                kind=kind,
            ):
                return f"{label} is unsafe"
            if not self.config.test_mode and not _root_ancestor_metadata_is_safe(path):
                return f"{label} ancestor is unsafe"

        if include_activation:
            journal = self.config.deployment_journal_path
            try:
                journal_exists = journal.exists() or journal.is_symlink()
            except OSError:
                return "deployment journal is unsafe"
            if journal_exists and not _trusted_path_metadata(
                journal,
                expected_uid=expected_uid,
                expected_gid=expected_gid,
                kind="file",
            ):
                return "deployment journal is unsafe"
            for link, label in (
                (self.config.current_link, "current release link"),
                (self.config.previous_link, "previous release link"),
            ):
                if not _trusted_optional_symlink(
                    link,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                ):
                    return f"{label} is unsafe"
                try:
                    link_exists = link.is_symlink()
                except OSError:
                    return f"{label} is unsafe"
                if link_exists:
                    target = self._release_link_target(link)
                    if (
                        target is None
                        or target.parent != self.config.releases_dir
                        or not _is_canonical_sha(target.name)
                    ):
                        return f"{label} target is unsafe"
            for directory, label in (
                (self.config.deployment_receipts_dir, "deployment receipt"),
                (self.config.quarantine_dir, "quarantine record"),
            ):
                if allow_missing_generated_namespaces:
                    try:
                        os.lstat(directory)
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return f"{label} namespace is unsafe"
                if not _trusted_json_namespace(
                    directory,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                ):
                    return f"{label} namespace is unsafe"

        bootstrap_journal = self.config.bootstrap_journal_path
        try:
            bootstrap_metadata = os.lstat(bootstrap_journal)
        except FileNotFoundError:
            bootstrap_metadata = None
        except OSError:
            return "bootstrap journal is unsafe"
        if bootstrap_metadata is not None:
            if (
                not _trusted_path_metadata(
                    bootstrap_journal,
                    expected_uid=expected_uid,
                    expected_gid=expected_gid,
                    kind="file",
                    require_private=True,
                )
                or stat.S_IMODE(bootstrap_metadata.st_mode) != 0o600
            ):
                return "bootstrap journal is unsafe"
            if (
                not self.config.test_mode
                and not _root_ancestor_metadata_is_safe(bootstrap_journal)
            ):
                return "bootstrap journal ancestor is unsafe"

        # A bare repository is writable by the controller, but every existing
        # entry must remain root-controlled.  This prevents repository config,
        # hooks, object alternates, or helpers from being supplied by another
        # local identity before Git's command-line policy is applied.
        if not self.config.test_mode:
            try:
                for path in self.config.source_repo.rglob("*"):
                    metadata = os.lstat(path)
                    kind = "directory" if stat.S_ISDIR(metadata.st_mode) else "file"
                    if not _trusted_path_metadata(
                        path,
                        expected_uid=expected_uid,
                        expected_gid=expected_gid,
                        kind=kind,
                    ):
                        return "source repository contains unsafe metadata"
            except OSError:
                return "source repository is unavailable"
        return None

    def _initialize_source_repository(self) -> ControllerResult | None:
        source = self.config.source_repo
        if (source / "HEAD").is_file() or (source / ".git/HEAD").is_file():
            return None
        # Test runners deliberately use a non-existent tool path.  On a real host,
        # create the repository before the first fetch instead of relying on an
        # accidental working tree or implicit Git behavior.
        if not self.config.git_path.is_file():
            return None
        try:
            if any(source.iterdir()):
                return _result(ResultStatus.REJECTED, "source repository is not initialized")
        except OSError:
            return _result(ResultStatus.REJECTED, "source repository is unavailable")
        initialized = self._run_command(
            self._git_argv(
                "init",
                "--bare",
                "--quiet",
                str(source),
            ),
            env=self._git_env(),
            writable_paths=(source,),
        )
        if initialized is None or initialized.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to initialize source repository")
        return None

    def _git_argv(self, *arguments: str, verify: bool = False) -> tuple[str, ...]:
        overrides = [
            "core.hooksPath=/dev/null",
            "credential.helper=",
            "protocol.allow=never",
            "protocol.https.allow=always",
        ]
        if verify:
            overrides.extend(
                (
                    "gpg.ssh.program=/usr/bin/ssh-keygen",
                    f"gpg.ssh.allowedSignersFile={self.config.allowed_signers_path}",
                )
            )
        prefix: list[str] = [str(self.config.git_path)]
        for override in overrides:
            prefix.extend(("-c", override))
        return (*prefix, *arguments)

    def _git_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        environment = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false",
            "GIT_SSH_COMMAND": "/bin/false",
        }
        if extra is not None:
            environment.update(extra)
        return environment

    def _run_command(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        uid: int | None = None,
        gid: int | None = None,
        env: dict[str, str] | None = None,
        writable_paths: tuple[Path, ...] = (),
        network_access: bool = False,
    ) -> subprocess.CompletedProcess[Any] | None:
        try:
            if isinstance(self.runner, SubprocessRunner):
                return self.runner.run(
                    argv,
                    cwd=cwd,
                    uid=uid,
                    gid=gid,
                    env=env,
                    writable_paths=writable_paths,
                    network_access=network_access,
                )
            return self.runner.run(
                argv,
                cwd=cwd,
                uid=uid,
                gid=gid,
                env=env,
            )
        except (OSError, TimeoutError, subprocess.SubprocessError):
            return None

    def _run_control_command(
        self,
        argv: tuple[str, ...],
    ) -> subprocess.CompletedProcess[Any] | None:
        try:
            direct = getattr(self.runner, "run_control", None)
            if direct is not None:
                return direct(argv)
            return self.runner.run(argv)
        except (OSError, TimeoutError, subprocess.SubprocessError):
            return None

    def _read_deployed_sha(self) -> str | None:
        path = self.config.deployed_state_path
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                return None
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        return value if _is_canonical_sha(value) else None

    def _fetch_and_resolve_main(self) -> str | ControllerResult:
        fetch = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "fetch",
                "--quiet",
                "--force",
                "--no-tags",
                self.config.remote_url,
                f"refs/heads/{self.config.branch}",
            ),
            env=self._git_env(),
            writable_paths=(self.config.source_repo,),
            network_access=True,
        )
        if fetch is None or fetch.returncode != 0:
            return _result(ResultStatus.TRANSIENT, "failed to fetch remote main")

        resolved = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "rev-parse",
                "--verify",
                "FETCH_HEAD^{commit}",
            ),
            env=self._git_env(),
        )
        if resolved is None or resolved.returncode != 0:
            return _result(ResultStatus.TRANSIENT, "failed to resolve remote main")
        return _stdout_text(resolved).strip()

    def _get_github_json(
        self,
        url: str,
        *,
        sha: str,
        purpose: str,
    ) -> object | ControllerResult:
        try:
            return self.http.get_json(url, timeout=self.config.api_timeout_seconds)
        except TimeoutError:
            return _result(ResultStatus.TRANSIENT, f"github {purpose} api timed out", sha)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code <= 599:
                return _result(
                    ResultStatus.TRANSIENT,
                    f"github {purpose} api returned a transient error",
                    sha,
                )
            return _result(ResultStatus.REJECTED, f"github {purpose} api rejected request", sha)
        except urllib.error.URLError:
            return _result(ResultStatus.TRANSIENT, f"github {purpose} api unavailable", sha)
        except (json.JSONDecodeError, UnicodeError, ValueError):
            return _result(ResultStatus.REJECTED, f"github {purpose} api returned bad data", sha)

    def _require_green_workflow(self, sha: str) -> ControllerResult | None:
        payload = self._get_github_json(
            self._workflow_runs_url(sha),
            sha=sha,
            purpose="workflow",
        )
        if isinstance(payload, ControllerResult):
            return payload
        if not isinstance(payload, dict):
            return _result(ResultStatus.REJECTED, "github workflow api returned non-object", sha)
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list) or not runs:
            return _result(ResultStatus.REJECTED, "github workflow runs are malformed", sha)

        matching: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
        for run in runs:
            if not isinstance(run, dict):
                return _result(
                    ResultStatus.REJECTED,
                    "github workflow runs are malformed",
                    sha,
                )
            ordering_values = tuple(
                run.get(key) for key in ("run_number", "run_attempt", "id")
            )
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                for value in ordering_values
            ):
                return _result(
                    ResultStatus.REJECTED,
                    "workflow run ordering evidence is malformed",
                    sha,
                )
            identity_error = self._workflow_identity_error(run, sha)
            if identity_error is None:
                run_number, run_attempt, run_id = ordering_values
                matching.append(((run_number, run_attempt, run_id), run))
            elif run.get("head_sha") == sha:
                return _result(ResultStatus.REJECTED, identity_error, sha)
        if not matching:
            return _result(
                ResultStatus.REJECTED,
                "no exact workflow run matches candidate",
                sha,
            )
        orderings = [ordering for ordering, _run in matching]
        if len(orderings) != len(set(orderings)):
            return _result(
                ResultStatus.REJECTED,
                "workflow run ordering evidence is ambiguous",
                sha,
            )
        current_run = max(matching, key=lambda item: item[0])[1]
        if current_run.get("status") != "completed":
            return _result(ResultStatus.TRANSIENT, "current main workflow is not complete", sha)
        if current_run.get("conclusion") != "success":
            return _result(ResultStatus.REJECTED, "current main workflow is not green", sha)
        return None

    def _workflow_runs_url(self, sha: str) -> str:
        query = urllib.parse.urlencode(
            {
                "branch": self.config.branch,
                "event": "push",
                "head_sha": sha,
                "per_page": "100",
            }
        )
        workflow_name = urllib.parse.quote(Path(self.config.workflow_path).name, safe="")
        return (
            f"https://api.github.com/repos/{self.config.repository}/actions/workflows/"
            f"{workflow_name}/runs?{query}"
        )

    def _workflow_identity_error(self, run: dict[str, Any], sha: str) -> str | None:
        checks = {
            "head_sha": sha,
            "head_branch": self.config.branch,
            "event": "push",
            "path": self.config.workflow_path,
        }
        for key, expected in checks.items():
            if run.get(key) != expected:
                return f"workflow run has wrong {key}"
        for key in ("repository", "head_repository"):
            value = run.get(key)
            if not isinstance(value, dict) or value.get("full_name") != self.config.repository:
                return f"workflow run has wrong {key}"
        return None

    def _verify_candidate(self, sha: str, deployed_sha: str) -> ControllerResult | None:
        authentication_result = self._verify_candidate_authentication(sha)
        if authentication_result is not None:
            return authentication_result

        ancestor = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "merge-base",
                "--is-ancestor",
                deployed_sha,
                sha,
            ),
            env=self._git_env(),
        )
        if ancestor is None or ancestor.returncode != 0:
            return _result(ResultStatus.REJECTED, "deployed sha is not an ancestor", sha)
        return None

    def _verify_candidate_authentication(self, sha: str) -> ControllerResult | None:
        if not _regular_file_without_symlink(self.config.allowed_signers_path):
            return _result(ResultStatus.REJECTED, "allowed signers file is missing", sha)

        signature_is_ssh = self._commit_has_single_ssh_signature(sha)
        if signature_is_ssh is None:
            return _result(ResultStatus.REJECTED, "commit signature verification failed", sha)
        if signature_is_ssh and self._verify_commit(sha) is None:
            return _result(ResultStatus.REJECTED, "commit signature verification failed", sha)
        return self._verify_rebased_pull_request(sha)

    def _commit_has_single_ssh_signature(self, sha: str) -> bool | None:
        inspected = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "cat-file",
                "commit",
                sha,
            ),
            env=self._git_env(),
        )
        if inspected is None or inspected.returncode != 0:
            return None
        return _raw_commit_has_single_ssh_signature(_stdout_bytes(inspected))

    def _verify_commit(self, sha: str) -> bool | None:
        verify_extra = None
        if self.config.gnupg_home is not None:
            verify_extra = {"GNUPGHOME": str(self.config.gnupg_home)}
        verified = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "verify-commit",
                sha,
                verify=True,
            ),
            env=self._git_env(verify_extra),
        )
        if verified is None:
            return None
        return verified.returncode == 0

    def _verify_rebased_pull_request(self, sha: str) -> ControllerResult | None:
        pulls_url = (
            f"https://api.github.com/repos/{self.config.repository}/commits/{sha}/pulls"
        )
        payload = self._get_github_json(
            pulls_url,
            sha=sha,
            purpose="associated pulls",
        )
        if isinstance(payload, ControllerResult):
            return payload
        if not isinstance(payload, list):
            return _result(ResultStatus.REJECTED, "rebase signature evidence is ambiguous", sha)
        matching_pulls = [
            pull
            for pull in payload
            if isinstance(pull, dict)
            and not isinstance(pull.get("number"), bool)
            and isinstance(pull.get("number"), int)
            and pull["number"] > 0
            and isinstance(pull.get("merged_at"), str)
            and bool(pull.get("merged_at"))
            and pull.get("merge_commit_sha") == sha
            and _pull_side_matches(
                pull.get("base"),
                self.config.repository,
                ref=self.config.branch,
            )
            and _pull_side_matches(pull.get("head"), self.config.repository)
            and _is_canonical_sha(pull["head"].get("sha"))
        ]
        if len(matching_pulls) != 1:
            return _result(ResultStatus.REJECTED, "rebase signature evidence is ambiguous", sha)
        pull = matching_pulls[0]
        number = pull.get("number")
        base = pull.get("base")
        head = pull.get("head")
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or number < 1
            or not isinstance(pull.get("merged_at"), str)
            or not pull.get("merged_at")
            or pull.get("merge_commit_sha") != sha
            or not _pull_side_matches(base, self.config.repository, ref=self.config.branch)
            or not _pull_side_matches(head, self.config.repository)
        ):
            return _result(ResultStatus.REJECTED, "rebase pull request identity is invalid", sha)
        head_sha = head.get("sha")
        if not _is_canonical_sha(head_sha):
            return _result(ResultStatus.REJECTED, "rebase pull request head is invalid", sha)

        pull_ref = f"refs/pull/{number}/head"
        fetched = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "fetch",
                "--quiet",
                "--force",
                "--no-tags",
                self.config.remote_url,
                pull_ref,
            ),
            env=self._git_env(),
            writable_paths=(self.config.source_repo,),
            network_access=True,
        )
        if fetched is None or fetched.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to fetch pull request head", sha)
        resolved = self._rev_parse("FETCH_HEAD^{commit}")
        if resolved != head_sha:
            return _result(ResultStatus.REJECTED, "pull request head changed", sha)
        if self._commit_has_single_ssh_signature(head_sha) is not True:
            return _result(
                ResultStatus.REJECTED,
                "pull request head is not singly SSH-signed",
                sha,
            )
        if self._verify_commit(head_sha) is not True:
            return _result(ResultStatus.REJECTED, "pull request head signature is invalid", sha)

        candidate_tree = self._rev_parse(f"{sha}^{{tree}}")
        head_tree = self._rev_parse(f"{head_sha}^{{tree}}")
        if (
            not _is_canonical_sha(candidate_tree)
            or not _is_canonical_sha(head_tree)
            or candidate_tree != head_tree
        ):
            return _result(ResultStatus.REJECTED, "rebased commit tree differs from signed head", sha)
        return None

    def _rev_parse(self, revision: str) -> str | None:
        resolved = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "rev-parse",
                "--verify",
                revision,
            ),
            env=self._git_env(),
        )
        if resolved is None or resolved.returncode != 0:
            return None
        value = _stdout_text(resolved).strip()
        return value if _is_canonical_sha(value) else None

    def _check_noop_release(self, sha: str) -> ControllerResult:
        release = self.config.releases_dir / sha
        if not self._ready_release_is_safe(release, sha):
            return _result(
                ResultStatus.REJECTED,
                "deployed release is missing a valid ready manifest",
                sha,
            )
        if self._release_link_target(self.config.current_link) != release:
            return _result(
                ResultStatus.REJECTED,
                "current release and deployed state disagree",
                sha,
            )
        if self._service_observation() is None:
            return _result(ResultStatus.REJECTED, "deployed service is not active", sha)
        health = self._healthcheck(release, self.config.database_path)
        if health is None or health.returncode != 0:
            return _result(ResultStatus.REJECTED, "deployed release healthcheck failed", sha)
        return _result(ResultStatus.NOOP, "remote main is already deployed and healthy", sha)

    def _prepare_release(self, sha: str, release: Path) -> ControllerResult | None:
        if not _is_canonical_sha(sha) or release != self.config.releases_dir / sha:
            return _result(ResultStatus.REJECTED, "release destination is unsafe", sha)
        staging = self.config.releases_dir / f".{sha}.tmp"
        try:
            if _path_exists_lstat(staging):
                if self._release_staging_error() is not None:
                    return _result(
                        ResultStatus.REJECTED,
                        "existing release staging is unsafe",
                        sha,
                    )
                _remove_tree_nofollow(staging)
                _fsync_directory(staging.parent)
        except OSError:
            return _result(ResultStatus.REJECTED, "release staging could not be reset", sha)
        tree = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "ls-tree",
                "-rz",
                "-r",
                "--full-tree",
                sha,
            ),
            env=self._git_env(),
        )
        if tree is None or tree.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to inspect candidate tree", sha)
        raw_tree = _stdout_bytes(tree)
        tree_entries = _parse_git_tree(raw_tree)
        if tree_entries is None:
            return _result(ResultStatus.REJECTED, "candidate tree contains unsafe entries", sha)

        archive = self._run_command(
            self._git_argv(
                "-C",
                str(self.config.source_repo),
                "archive",
                "--format=tar",
                sha,
            ),
            env=self._git_env(),
        )
        if archive is None or archive.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to archive candidate tree", sha)
        try:
            members = _validated_tar_members(_stdout_bytes(archive))
            if not _archive_matches_tree(members, tree_entries):
                raise ValueError("candidate archive differs from authenticated tree")
            staging.mkdir(mode=0o755)
            _extract_validated_tar(members, staging)
            _set_authenticated_source_metadata(
                staging,
                tree_entries,
                uid=self.config.release_uid,
                gid=self.config.release_gid,
            )
            build_venv = staging / ".venv"
            build_venv.mkdir(mode=0o700)
            _set_path_metadata(
                build_venv,
                mode=0o700,
                uid=self.config.build_uid,
                gid=self.config.build_gid,
            )
        except (OSError, ValueError, tarfile.TarError):
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "candidate archive is unsafe", sha)

        sync = self._run_command(
            (
                str(self.config.uv_path),
                "sync",
                "--frozen",
                "--no-dev",
                "--no-editable",
            ),
            cwd=staging,
            uid=self.config.build_uid,
            gid=self.config.build_gid,
            env={"UV_CACHE_DIR": str(self.config.uv_cache_dir)},
            writable_paths=(build_venv, self.config.uv_cache_dir),
            network_access=True,
        )
        if sync is None or sync.returncode != 0:
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "dependency sync failed", sha)
        if not _authenticated_source_matches(
            staging,
            tree_entries,
            uid=self.config.release_uid,
            gid=self.config.release_gid,
        ):
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "build modified authenticated source", sha)
        try:
            _seal_build_venv(
                staging,
                uid=self.config.release_uid,
                gid=self.config.release_gid,
            )
        except (OSError, ValueError):
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "failed to seal build output", sha)
        smoke = self._run_command(
            (
                str(staging / ".venv/bin/python"),
                "-I",
                "-c",
                "import honeybuy_tg.app",
            ),
            cwd=staging,
            uid=self.config.build_uid,
            gid=self.config.build_gid,
        )
        if smoke is None or smoke.returncode != 0:
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "import smoke failed", sha)
        if not _authenticated_source_matches(
            staging,
            tree_entries,
            uid=self.config.release_uid,
            gid=self.config.release_gid,
        ):
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "build modified authenticated source", sha)
        try:
            _finalize_release_tree(
                staging,
                sha,
                self.config.release_uid,
                self.config.release_gid,
                source_tree_sha256=hashlib.sha256(raw_tree).hexdigest(),
                archive_sha256=hashlib.sha256(_stdout_bytes(archive)).hexdigest(),
                legacy_manifest=self.config.test_mode,
            )
        except OSError:
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "failed to finalize release", sha)
        if not self._ready_release_tree_is_safe(staging, sha):
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "prepared release failed validation", sha)
        try:
            if _path_exists_lstat(release):
                raise OSError("canonical release appeared before promotion")
            os.replace(staging, release)
            _fsync_directory(release.parent)
        except OSError:
            self._discard_release_staging(staging, sha)
            return _result(ResultStatus.REJECTED, "release promotion failed", sha)
        if not self._ready_release_is_safe(release, sha):
            return _result(ResultStatus.REJECTED, "promoted release failed validation", sha)
        return None

    def _discard_release_staging(self, staging: Path, sha: str) -> None:
        expected = self.config.releases_dir / f".{sha}.tmp"
        if not _is_canonical_sha(sha) or staging != expected:
            return
        try:
            metadata = os.lstat(staging)
        except FileNotFoundError:
            return
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != self._control_plane_uid()
            or metadata.st_gid != self._control_plane_gid()
            or metadata.st_mode & 0o022
        ):
            return
        try:
            _remove_tree_nofollow(staging)
            _fsync_directory(staging.parent)
        except OSError:
            return

    def _discard_partial_release(self, release: Path, sha: str) -> None:
        if not _is_canonical_sha(sha) or release != self.config.releases_dir / sha:
            return
        try:
            metadata = release.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(metadata.st_mode) or release.is_symlink():
            return
        try:
            shutil.rmtree(release)
            _fsync_directory(release.parent)
        except OSError:
            # A non-ready directory remains fail-closed.  The controller never
            # reuses it and an operator can inspect/remove it deliberately.
            return

    def _activate_release_locked(self, sha: str) -> ControllerResult:
        release = self.config.releases_dir / sha
        previous_sha = self._read_deployed_sha()
        if previous_sha is None:
            return _result(ResultStatus.REJECTED, "deployed state changed before activation", sha)
        previous_release = self.config.releases_dir / previous_sha
        state_error = self._activation_state_error(
            candidate_sha=sha,
            previous_sha=previous_sha,
        )
        if state_error is not None:
            return _result(ResultStatus.REJECTED, state_error, sha)

        dry_run = self._dry_run_candidate(release, sha)
        if dry_run is not None:
            return dry_run

        final_sha = self._fetch_and_resolve_main()
        if isinstance(final_sha, ControllerResult):
            return final_sha
        if final_sha != sha:
            return _result(ResultStatus.TRANSIENT, "remote main changed before activation", sha)
        state_error = self._activation_state_error(
            candidate_sha=sha,
            previous_sha=previous_sha,
        )
        if state_error is not None:
            return _result(ResultStatus.REJECTED, state_error, sha)

        journal = {
            "version": 1,
            "candidate_sha": sha,
            "previous_sha": previous_sha,
            "phase": "prepared",
            "phases": ["prepared"],
            "backup": None,
            "database_existed": _regular_file_without_symlink(
                self.config.database_path
            ),
        }
        if not self._write_journal(journal):
            return _result(ResultStatus.REJECTED, "failed to create activation journal", sha)

        stopped = self._systemctl("stop")
        if stopped is None or stopped.returncode != 0:
            if not self._clear_journal():
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "failed stop left an activation journal",
                    sha,
                )
            return _result(ResultStatus.REJECTED, "failed to stop service", sha)
        if not self._advance_journal(journal, "stopped"):
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=False,
                reason="failed to journal service stop",
            )

        try:
            backup = self._backup_database(sha)
        except (OSError, sqlite3.Error, ValueError):
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=False,
                reason="database backup validation failed",
            )
        journal["backup"] = backup
        if not self._advance_journal(journal, "backed_up"):
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=False,
                reason="failed to journal database backup",
            )

        migrated = self._migrate(release, self.config.database_path)
        if migrated is None or migrated.returncode != 0:
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=True,
                reason="migration failed",
            )
        if not self._advance_journal(journal, "migrated"):
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=True,
                reason="failed to journal migration",
            )

        pre_switch_health = self._healthcheck(release, self.config.database_path)
        if pre_switch_health is None or pre_switch_health.returncode != 0:
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=True,
                reason="migrated candidate healthcheck failed",
            )

        try:
            self._switch_link(self.config.previous_link, previous_release)
            self._switch_link(self.config.current_link, release)
        except OSError:
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=True,
                reason="failed to switch current release",
            )
        if not self._advance_journal(journal, "switched"):
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=True,
                reason="failed to journal release switch",
            )
        if not self._advance_journal(journal, "start_requested"):
            return self._rollback_before_start(
                journal,
                previous_release,
                restore_database=True,
                reason="failed to journal start boundary",
            )

        started = self._systemctl("start")
        if started is None or started.returncode != 0:
            return self._rollback_after_start_requested(
                journal,
                previous_release,
                reason="failed to start candidate service",
            )
        service_baseline: ServiceObservation | None = None
        for index in range(self.config.stable_health_checks):
            service_observation = self._service_observation()
            if service_observation is None or (
                service_baseline is not None
                and service_observation != service_baseline
            ):
                return self._rollback_after_start_requested(
                    journal,
                    previous_release,
                    reason="candidate service was not stable",
                )
            service_baseline = service_observation
            healthy = self._healthcheck(release, self.config.database_path)
            if healthy is None or healthy.returncode != 0:
                return self._rollback_after_start_requested(
                    journal,
                    previous_release,
                    reason="candidate failed stable health checks",
                )
            if index + 1 < self.config.stable_health_checks:
                try:
                    self.sleeper(self.config.stable_health_interval_seconds)
                except Exception:
                    return self._rollback_after_start_requested(
                        journal,
                        previous_release,
                        reason="stable health window was interrupted",
                    )

        if not self._advance_journal(journal, "healthy"):
            return self._rollback_after_start_requested(
                journal,
                previous_release,
                reason="failed to journal healthy release",
            )
        try:
            self._write_deployed_sha(sha)
            self._write_receipt(journal)
            self._clear_journal(required=True)
        except OSError:
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "healthy release could not be committed durably",
                sha,
            )
        return _result(ResultStatus.DEPLOYED, "release deployed", sha)

    def _activation_state_error(self, *, candidate_sha: str, previous_sha: str) -> str | None:
        candidate = self.config.releases_dir / candidate_sha
        previous = self.config.releases_dir / previous_sha
        manifest_error = self._control_plane_manifest_error(
            allowed_phases={"installed"},
            candidate_release=candidate,
        )
        if manifest_error is not None:
            return manifest_error
        if self._candidate_is_quarantined(candidate_sha):
            return "candidate release is quarantined"
        if not self._ready_release_is_safe(candidate, candidate_sha):
            return "prepared release failed validation"
        if not self._ready_release_is_safe(previous, previous_sha):
            return "previous release failed validation"
        if self._release_link_target(self.config.current_link) != previous:
            return "current release and deployed state disagree"
        if self._read_deployed_sha() != previous_sha:
            return "deployed state changed before activation"
        if self.config.previous_link.exists() and not self.config.previous_link.is_symlink():
            return "previous release path is not a symlink"
        return None

    def _dry_run_candidate(self, release: Path, sha: str) -> ControllerResult | None:
        try:
            _ensure_directory(self.config.scratch_dir, mode=0o711)
            with tempfile.TemporaryDirectory(
                prefix=f"preflight-{sha[:12]}-",
                dir=self.config.scratch_dir,
            ) as temporary:
                scratch_root = Path(temporary)
                scratch_database = scratch_root / self.config.database_path.name
                self._clone_database(self.config.database_path, scratch_database)
                _make_runtime_accessible(
                    scratch_root,
                    scratch_database,
                    self.config.runtime_uid,
                    self.config.runtime_gid,
                )
                migrated = self._migrate(release, scratch_database)
                if migrated is None or migrated.returncode != 0:
                    return _result(ResultStatus.REJECTED, "candidate dry-run migration failed", sha)
                healthy = self._healthcheck(release, scratch_database)
                if healthy is None or healthy.returncode != 0:
                    return _result(ResultStatus.REJECTED, "candidate dry-run healthcheck failed", sha)
        except (OSError, sqlite3.Error, ValueError):
            return _result(ResultStatus.REJECTED, "candidate dry-run setup failed", sha)
        return None

    def _migrate(
        self,
        release: Path,
        database_path: Path,
    ) -> subprocess.CompletedProcess[Any] | None:
        return self._run_command(
            (str(release / ".venv/bin/python"), "-m", "honeybuy_tg", "migrate"),
            cwd=self.config.empty_work_dir,
            uid=self.config.runtime_uid,
            gid=self.config.runtime_gid,
            env={**MIGRATION_ENV, "DATABASE_PATH": str(database_path)},
            writable_paths=(database_path.parent,),
        )

    def _healthcheck(
        self,
        release: Path,
        database_path: Path,
    ) -> subprocess.CompletedProcess[Any] | None:
        return self._run_command(
            (str(release / ".venv/bin/python"), "-m", "honeybuy_tg", "healthcheck"),
            cwd=self.config.empty_work_dir,
            uid=self.config.runtime_uid,
            gid=self.config.runtime_gid,
            env={"DATABASE_PATH": str(database_path)},
        )

    def _clone_database(self, source_path: Path, target_path: Path) -> None:
        if (
            not self.config.test_mode
            and os.geteuid() == 0
            and source_path == self.config.database_path
        ):
            self._clone_database_as_runtime(source_path, target_path)
            return
        self._clone_database_in_process(source_path, target_path)

    def _clone_database_as_runtime(
        self,
        source_path: Path,
        target_path: Path,
    ) -> None:
        try:
            source_descriptor, source_metadata = _open_pinned_regular(source_path)
        except FileNotFoundError:
            self._clone_database_in_process(source_path, target_path)
            return
        assert self.config.runtime_uid is not None
        assert self.config.runtime_gid is not None
        if (
            source_metadata.st_uid != self.config.runtime_uid
            or source_metadata.st_gid != self.config.runtime_gid
            or source_metadata.st_mode & 0o022
        ):
            os.close(source_descriptor)
            raise ValueError("live database ownership is unsafe")

        _ensure_directory(self.config.scratch_dir, mode=0o711)
        if not _trusted_path_metadata(
            self.config.scratch_dir,
            expected_uid=0,
            expected_gid=0,
            kind="directory",
        ):
            os.close(source_descriptor)
            raise ValueError("database staging directory is unsafe")
        if not _trusted_path_metadata(
            target_path.parent,
            expected_uid=0,
            expected_gid=0,
            kind="directory",
        ):
            os.close(source_descriptor)
            raise ValueError("database clone destination directory is unsafe")

        staging_root = Path(
            tempfile.mkdtemp(prefix="sqlite-clone-", dir=self.config.scratch_dir)
        )
        staging_database = staging_root / "clone.sqlite3"
        try:
            _set_path_metadata(
                staging_root,
                mode=0o700,
                uid=self.config.runtime_uid,
                gid=self.config.runtime_gid,
            )
            cloned = self._run_command(
                (
                    str(self.config.system_python_path),
                    "-I",
                    "-c",
                    SQLITE_CLONE_HELPER,
                    str(source_path),
                    str(staging_database),
                ),
                cwd=self.config.empty_work_dir,
                uid=self.config.runtime_uid,
                gid=self.config.runtime_gid,
                env={},
                writable_paths=(staging_root,),
            )
            if cloned is None or cloned.returncode != 0:
                raise ValueError("runtime database clone failed")
            if not _path_matches_metadata(source_path, source_metadata):
                raise ValueError("live database changed while cloning")
            try:
                staging_entries = set(os.listdir(staging_root))
            except OSError as exc:
                raise ValueError("database clone staging is unreadable") from exc
            if staging_entries != {staging_database.name}:
                raise ValueError("database clone staging contains unexpected output")
            _copy_pinned_regular(
                staging_database,
                target_path,
                mode=0o600,
                uid=0,
                gid=0,
            )
            _validate_sqlite_database(target_path)
        finally:
            os.close(source_descriptor)
            try:
                _remove_tree_nofollow(staging_root)
            except OSError:
                pass

    def _clone_database_in_process(
        self,
        source_path: Path,
        target_path: Path,
    ) -> None:
        try:
            source_descriptor, source_metadata = _open_pinned_regular(source_path)
        except FileNotFoundError:
            source_descriptor = None
            source_metadata = None

        target_descriptor = _create_pinned_regular(target_path, mode=0o600)
        target_metadata = os.fstat(target_descriptor)
        try:
            if source_metadata is None:
                with closing(sqlite3.connect(target_path)) as target:
                    with target:
                        if not _path_matches_metadata(target_path, target_metadata):
                            raise ValueError("database target changed while opening")
                        target.execute("PRAGMA user_version")
            else:
                assert source_descriptor is not None
                source_uri = _sqlite_readonly_uri(source_path)
                with closing(sqlite3.connect(source_uri, uri=True)) as source:
                    with source:
                        if not _path_matches_metadata(source_path, source_metadata):
                            raise ValueError("database source changed while opening")
                        with closing(sqlite3.connect(target_path)) as target:
                            with target:
                                if not _path_matches_metadata(
                                    target_path,
                                    target_metadata,
                                ):
                                    raise ValueError(
                                        "database target changed while opening"
                                    )
                                source.backup(target)
                                if not _path_matches_metadata(
                                    target_path,
                                    target_metadata,
                                ):
                                    raise ValueError(
                                        "database target changed while cloning"
                                    )
                        if not _path_matches_metadata(source_path, source_metadata):
                            raise ValueError("database source changed while cloning")
            with closing(sqlite3.connect(target_path)) as checkpoint:
                with checkpoint:
                    if not _path_matches_metadata(target_path, target_metadata):
                        raise ValueError("database target changed before checkpoint")
                    journal_mode = checkpoint.execute("PRAGMA journal_mode").fetchone()
                    if journal_mode == ("wal",):
                        checkpoint_result = checkpoint.execute(
                            "PRAGMA wal_checkpoint(TRUNCATE)"
                        ).fetchone()
                        if checkpoint_result is None or checkpoint_result[0] != 0:
                            raise ValueError("database clone checkpoint failed")
                        # A SQLite backup of a WAL database can retain header state
                        # that recreates an orphan zero-length WAL after every
                        # read when its temporary sidecars are discarded. Cycle
                        # through DELETE and back to WAL while the clone is
                        # private, producing a self-contained WAL-mode image.
                        if checkpoint.execute(
                            "PRAGMA journal_mode=DELETE"
                        ).fetchone() != ("delete",):
                            raise ValueError("database clone journal reset failed")
                        if checkpoint.execute("PRAGMA journal_mode=WAL").fetchone() != (
                            "wal",
                        ):
                            raise ValueError("database clone WAL restore failed")
            _remove_database_sidecars(target_path)
            try:
                os.fchmod(target_descriptor, 0o600)
                if target_metadata.st_nlink != 1:
                    raise ValueError("database clone has unsafe links")
                os.fsync(target_descriptor)
            finally:
                os.close(target_descriptor)
                target_descriptor = -1
        except Exception:
            try:
                _unlink_regular_or_missing(target_path)
            except OSError:
                pass
            raise
        finally:
            if target_descriptor >= 0:
                os.close(target_descriptor)
            if source_descriptor is not None:
                os.close(source_descriptor)

    def _backup_database(self, sha: str) -> dict[str, Any] | None:
        database = self.config.database_path
        if not database.exists():
            return None
        if not _regular_file_without_symlink(database):
            raise ValueError("database path is unsafe")
        _ensure_directory(self.config.database_backup_dir, mode=0o700)
        destination = self.config.database_backup_dir / f"{database.name}.{sha}.bak"
        temporary = destination.with_name(f".{destination.name}.tmp")
        _unlink_regular_or_missing(temporary)
        source_descriptor, source_metadata = _open_pinned_regular(database)
        os.close(source_descriptor)
        try:
            self._clone_database(database, temporary)
            temporary_descriptor, _ = _open_pinned_regular(temporary)
            try:
                os.fchmod(temporary_descriptor, 0o600)
                if os.geteuid() == 0:
                    os.fchown(
                        temporary_descriptor,
                        source_metadata.st_uid,
                        source_metadata.st_gid,
                    )
                os.fsync(temporary_descriptor)
            finally:
                os.close(temporary_descriptor)
            schema_version = _validate_sqlite_database(temporary)
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        except Exception:
            try:
                _unlink_regular_or_missing(temporary)
            except OSError:
                pass
            raise
        metadata = destination.lstat()
        record = {
            "name": destination.name,
            "sha256": _file_sha256(destination),
            "schema_version": schema_version,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
        if not self._backup_record_is_valid(record, sha):
            raise ValueError("backup failed validation")
        return record

    def _backup_record_is_valid(self, record: object, sha: str) -> bool:
        if not isinstance(record, dict):
            return False
        expected_name = f"{self.config.database_path.name}.{sha}.bak"
        if set(record) != {"name", "sha256", "schema_version", "uid", "gid", "mode"}:
            return False
        if record.get("name") != expected_name:
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))):
            return False
        if any(
            isinstance(record.get(key), bool) or not isinstance(record.get(key), int)
            for key in ("schema_version", "uid", "gid", "mode")
        ):
            return False
        path = self.config.database_backup_dir / expected_name
        if not _regular_file_without_symlink(path):
            return False
        try:
            metadata = path.lstat()
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                return False
            if metadata.st_uid != record["uid"] or metadata.st_gid != record["gid"]:
                return False
            if record["mode"] != 0o600 or _file_sha256(path) != record["sha256"]:
                return False
            return _validate_sqlite_database(path) == record["schema_version"]
        except (OSError, sqlite3.Error, ValueError):
            return False

    def _restore_database(self, journal: dict[str, Any]) -> None:
        record = journal.get("backup")
        if record is None:
            if journal.get("database_existed", True):
                raise ValueError("required database backup is missing")
            _remove_database_files(self.config.database_path)
            _fsync_directory(self.config.database_path.parent)
            return
        sha = journal["candidate_sha"]
        if not self._backup_record_is_valid(record, sha):
            raise ValueError("database backup is invalid")
        backup = self.config.database_backup_dir / record["name"]
        database = self.config.database_path
        _ensure_directory(database.parent, mode=0o700)
        _ensure_directory(self.config.scratch_dir, mode=0o711)
        if not self.config.test_mode and not _trusted_path_metadata(
            self.config.scratch_dir,
            expected_uid=0,
            expected_gid=0,
            kind="directory",
        ):
            raise ValueError("database restore staging directory is unsafe")
        # Build and validate the replacement under the root-controlled
        # controller state, not in the runtime-writable database directory.
        # The final rename is the first operation that exposes this inode at
        # the live pathname and replaces (rather than follows) any final link.
        temporary = self.config.scratch_dir / (
            f".{database.name}.restore-{journal['candidate_sha']}"
        )
        _unlink_regular_or_missing(temporary)
        try:
            self._clone_database(backup, temporary)
            if _validate_sqlite_database(temporary) != record["schema_version"]:
                raise ValueError("restored database failed validation")
            # Read-only validation of a WAL-mode image can leave empty
            # sidecars under the temporary basename. They are not part of the
            # restored artifact and must not survive its rename.
            _remove_database_sidecars(temporary)
            descriptor, temporary_metadata = _open_pinned_regular(temporary)
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                _remove_database_sidecars(database)
                try:
                    destination_metadata = os.lstat(database)
                except FileNotFoundError:
                    destination_metadata = None
                if destination_metadata is not None and not (
                    stat.S_ISREG(destination_metadata.st_mode)
                    or stat.S_ISLNK(destination_metadata.st_mode)
                ):
                    raise ValueError("live database destination is unsafe")
                # rename(2) replaces the directory entry itself and never
                # follows a final symlink. The validated, still-open inode
                # becomes live atomically; ownership is changed through that
                # descriptor only after it is no longer replaceable in the
                # runtime-inaccessible staging directory.
                os.replace(temporary, database)
                if not _path_matches_metadata(database, temporary_metadata):
                    raise ValueError("restored database changed during replacement")
                if os.geteuid() == 0:
                    os.fchown(descriptor, record["uid"], record["gid"])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _remove_database_sidecars(database)
            _fsync_directory(database.parent)
        except Exception:
            try:
                _unlink_regular_or_missing(temporary)
                _remove_database_sidecars(temporary)
            except OSError:
                pass
            raise

    def _rollback_before_start(
        self,
        journal: dict[str, Any],
        previous_release: Path,
        *,
        restore_database: bool,
        reason: str,
        service_already_stopped: bool = True,
    ) -> ControllerResult:
        if not self._mark_rollback_pending(
            journal,
            restore_database=restore_database,
            reason=reason,
            quarantine_required=False,
        ):
            self._mark_intervention(journal)
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "pre-start rollback intent could not be persisted",
                journal["candidate_sha"],
            )
        return self._resume_rollback(
            journal,
            previous_release,
            service_already_stopped=service_already_stopped,
        )

    def _mark_rollback_pending(
        self,
        journal: dict[str, Any],
        *,
        restore_database: bool,
        reason: str,
        quarantine_required: bool,
    ) -> bool:
        current = journal.get("phase")
        if current == "rollback_pending":
            return (
                journal.get("rollback_restore_database") is restore_database
                and journal.get("rollback_reason") == reason
                and journal.get("quarantine_required") is quarantine_required
            )
        if current not in ACTIVATION_PHASES or not isinstance(reason, str):
            return False
        if not reason or len(reason.encode("utf-8")) > 4096:
            return False
        journal["rollback_from_phase"] = current
        journal["rollback_restore_database"] = restore_database
        journal["rollback_reason"] = reason
        journal["quarantine_required"] = quarantine_required
        journal["no_database_restore"] = not restore_database
        journal["phase"] = "rollback_pending"
        return self._write_journal(journal)

    def _ensure_previous_deployed_state(self, journal: dict[str, Any]) -> None:
        previous = journal["previous_sha"]
        deployed = self._read_deployed_sha()
        if deployed == previous:
            return
        if deployed != journal["candidate_sha"]:
            raise ValueError("deployed state cannot be converged during rollback")
        self._write_deployed_sha(previous)
        if self._read_deployed_sha() != previous:
            raise ValueError("deployed state rollback was not durable")

    def _resume_rollback(
        self,
        journal: dict[str, Any],
        previous_release: Path,
        *,
        service_already_stopped: bool = True,
    ) -> ControllerResult:
        sha = journal["candidate_sha"]
        reason = journal.get("rollback_reason")
        if not isinstance(reason, str) or not reason:
            reason = "interrupted activation was rolled back"
        recovered = True
        quarantine_required = journal.get("quarantine_required") is True
        if not service_already_stopped:
            stopped = self._systemctl("stop")
            recovered = stopped is not None and stopped.returncode == 0
        if (
            recovered
            and quarantine_required
            and not self._previous_release_is_compatible(previous_release, sha)
        ):
            recovered = False
        try:
            if recovered and journal.get("rollback_restore_database") is True:
                self._restore_database(journal)
            if recovered:
                self._switch_link(self.config.current_link, previous_release)
                self._ensure_previous_deployed_state(journal)
        except (OSError, sqlite3.Error, ValueError):
            recovered = False
        if recovered and not self._mark_rollback_start_requested(journal):
            recovered = False
        if recovered and quarantine_required and not self._write_quarantine(sha, reason):
            recovered = False
        if recovered:
            started = self._systemctl("start")
            if started is None or started.returncode != 0:
                recovered = False
            elif not self._release_is_stably_healthy(previous_release):
                recovered = False
        if recovered:
            if self._clear_journal():
                return _result(ResultStatus.REJECTED, reason, sha)
            recovered = False
        self._mark_intervention(journal)
        return _result(
            ResultStatus.INTERVENTION_REQUIRED,
            "rollback recovery failed",
            sha,
        )

    def _mark_rollback_start_requested(self, journal: dict[str, Any]) -> bool:
        current = journal.get("phase")
        if current == "rollback_start_requested":
            return True
        if current in ACTIVATION_PHASES:
            journal["rollback_from_phase"] = current
            journal.setdefault("rollback_reason", "interrupted activation was rolled back")
            journal.setdefault("quarantine_required", False)
            journal.setdefault("rollback_restore_database", False)
        elif current != "rollback_pending":
            return False
        journal["no_database_restore"] = True
        journal["phase"] = "rollback_start_requested"
        return self._write_journal(journal)

    def _rollback_after_start_requested(
        self,
        journal: dict[str, Any],
        previous_release: Path,
        *,
        reason: str,
    ) -> ControllerResult:
        sha = journal["candidate_sha"]
        if not self._mark_rollback_pending(
            journal,
            restore_database=False,
            reason=reason,
            quarantine_required=True,
        ):
            self._mark_intervention(journal)
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "post-start rollback intent could not be persisted",
                sha,
            )
        self._write_quarantine(sha, reason)
        stopped = self._systemctl("stop")
        if stopped is None or stopped.returncode != 0:
            self._mark_intervention(journal)
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "failed candidate could not be stopped",
                sha,
            )
        compatible = self._previous_release_is_compatible(previous_release, sha)
        if compatible:
            try:
                self._switch_link(self.config.current_link, previous_release)
                self._ensure_previous_deployed_state(journal)
            except (OSError, ValueError):
                compatible = False
        if compatible:
            if not self._mark_rollback_start_requested(journal):
                compatible = False
            else:
                quarantined = self._write_quarantine(sha, reason)
                started = self._systemctl("start")
                if (
                    started is not None
                    and started.returncode == 0
                    and self._release_is_stably_healthy(previous_release)
                ):
                    if quarantined and self._clear_journal():
                        return _result(ResultStatus.REJECTED, reason, sha)
                    self._mark_intervention(journal)
                    return _result(
                        ResultStatus.INTERVENTION_REQUIRED,
                        "failed candidate could not be quarantined durably",
                        sha,
                    )
        self._mark_intervention(journal)
        return _result(
            ResultStatus.INTERVENTION_REQUIRED,
            "previous release is incompatible with migrated database",
            sha,
        )

    def _previous_release_is_compatible(self, previous_release: Path, sha: str) -> bool:
        try:
            _ensure_directory(self.config.scratch_dir, mode=0o711)
            with tempfile.TemporaryDirectory(
                prefix=f"rollback-{sha[:12]}-",
                dir=self.config.scratch_dir,
            ) as temporary:
                scratch_root = Path(temporary)
                database = scratch_root / self.config.database_path.name
                self._clone_database(self.config.database_path, database)
                _make_runtime_accessible(
                    scratch_root,
                    database,
                    self.config.runtime_uid,
                    self.config.runtime_gid,
                )
                health = self._healthcheck(previous_release, database)
                return health is not None and health.returncode == 0
        except (OSError, sqlite3.Error, ValueError):
            return False

    def _recover_interrupted_activation(self) -> ControllerResult | None:
        path = self.config.deployment_journal_path
        if not path.exists() and not path.is_symlink():
            return None
        journal = self._load_valid_journal()
        if journal is None:
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "activation journal is corrupt or inconsistent",
            )
        phase = journal["phase"]
        sha = journal["candidate_sha"]
        previous_release = self.config.releases_dir / journal["previous_sha"]
        if phase == "intervention_required":
            return _result(ResultStatus.INTERVENTION_REQUIRED, "manual recovery is required", sha)
        if phase == "rollback_pending":
            return self._resume_rollback(
                journal,
                previous_release,
                service_already_stopped=False,
            )
        if phase == "rollback_start_requested":
            # The old code and verified backup have already been restored and
            # this durable boundary was written before systemctl start.  A
            # prior start may have accepted writes, so recovery must never
            # restore the database again.
            reason = journal.get("rollback_reason")
            if not isinstance(reason, str) or not reason:
                reason = "interrupted rollback completed"
            if journal.get("quarantine_required") is True and not self._write_quarantine(
                sha,
                reason,
            ):
                self._mark_intervention(journal)
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "rollback quarantine could not be recovered durably",
                    sha,
                )
            started = self._systemctl("start")
            if started is None or started.returncode != 0:
                self._mark_intervention(journal)
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "recovery restart failed after the no-restore boundary",
                    sha,
                )
            if not self._release_is_stably_healthy(previous_release):
                self._mark_intervention(journal)
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "recovered previous release was not stable",
                    sha,
                )
            if self._clear_journal():
                return _result(ResultStatus.REJECTED, reason, sha)
            self._mark_intervention(journal)
            return _result(
                ResultStatus.INTERVENTION_REQUIRED,
                "completed rollback journal could not be cleared",
                sha,
            )
        if phase == "healthy":
            release = self.config.releases_dir / sha
            service_baseline: ServiceObservation | None = None
            for index in range(self.config.stable_health_checks):
                service_observation = self._service_observation()
                if service_observation is None or (
                    service_baseline is not None
                    and service_observation != service_baseline
                ):
                    return self._rollback_after_start_requested(
                        journal,
                        previous_release,
                        reason="recovered candidate service was not stable",
                    )
                service_baseline = service_observation
                healthy = self._healthcheck(release, self.config.database_path)
                if healthy is None or healthy.returncode != 0:
                    return self._rollback_after_start_requested(
                        journal,
                        previous_release,
                        reason="recovered candidate failed health checks",
                    )
                if index + 1 < self.config.stable_health_checks:
                    try:
                        self.sleeper(self.config.stable_health_interval_seconds)
                    except Exception:
                        return self._rollback_after_start_requested(
                            journal,
                            previous_release,
                            reason="recovered stable health window was interrupted",
                        )
            try:
                self._write_deployed_sha(sha)
                self._write_receipt(journal)
                self._clear_journal(required=True)
            except OSError:
                return _result(
                    ResultStatus.INTERVENTION_REQUIRED,
                    "healthy recovery could not be committed",
                    sha,
                )
            return _result(ResultStatus.DEPLOYED, "recovered healthy release", sha)
        if phase in {"start_requested", "health_checking"}:
            return self._rollback_after_start_requested(
                journal,
                previous_release,
                reason="recovered interrupted post-start activation",
            )

        return self._rollback_before_start(
            journal,
            previous_release,
            restore_database=phase in {"backed_up", "migrated", "switched"},
            reason="interrupted activation was rolled back",
            service_already_stopped=False,
        )

    def _load_valid_journal(self) -> dict[str, Any] | None:
        path = self.config.deployment_journal_path
        if not _regular_file_without_symlink(path):
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        candidate = payload.get("candidate_sha")
        previous = payload.get("previous_sha")
        phase = payload.get("phase")
        phases = payload.get("phases")
        if not _is_canonical_sha(candidate) or not _is_canonical_sha(previous):
            return None
        allowed_phases = {
            *ACTIVATION_PHASES,
            "health_checking",
            "rollback_pending",
            "rollback_start_requested",
            "intervention_required",
        }
        if phase not in allowed_phases or not isinstance(phases, list):
            return None
        rollback_from = payload.get("rollback_from_phase")
        rollback_pending = phase == "rollback_pending"
        if rollback_pending:
            reason = payload.get("rollback_reason")
            if (
                rollback_from not in ACTIVATION_PHASES
                or not isinstance(reason, str)
                or not reason
                or len(reason.encode("utf-8")) > 4096
                or not isinstance(payload.get("rollback_restore_database"), bool)
                or not isinstance(payload.get("quarantine_required"), bool)
                or not isinstance(payload.get("no_database_restore"), bool)
            ):
                return None
        no_restore_boundary = phase == "rollback_start_requested" or (
            phase == "intervention_required"
            and payload.get("no_database_restore") is True
        )
        if no_restore_boundary:
            if rollback_from not in ACTIVATION_PHASES:
                return None
            expected = list(
                ACTIVATION_PHASES[: ACTIVATION_PHASES.index(rollback_from) + 1]
            )
            if payload.get("no_database_restore") is not True:
                return None
            effective_phase = rollback_from
        elif rollback_pending or (
            phase == "intervention_required" and rollback_from in ACTIVATION_PHASES
        ):
            assert rollback_from in ACTIVATION_PHASES
            expected = list(
                ACTIVATION_PHASES[: ACTIVATION_PHASES.index(rollback_from) + 1]
            )
            effective_phase = rollback_from
        else:
            base_phase = (
                "start_requested"
                if phase in {"health_checking", "intervention_required"}
                else phase
            )
            expected = list(ACTIVATION_PHASES[: ACTIVATION_PHASES.index(base_phase) + 1])
            effective_phase = base_phase
        if phases != expected:
            return None
        deployed = self._read_deployed_sha()
        if phase in {"healthy", "rollback_pending"} or (
            phase == "intervention_required"
            and rollback_from in ACTIVATION_PHASES
        ):
            if deployed not in {previous, candidate}:
                return None
        elif deployed != previous:
            return None
        previous_release = self.config.releases_dir / previous
        candidate_release = self.config.releases_dir / candidate
        if not self._ready_release_is_safe(previous_release, previous):
            return None
        if not self._ready_release_is_safe(candidate_release, candidate):
            return None
        current_target = self._release_link_target(self.config.current_link)
        if rollback_pending:
            target_is_valid = current_target in {previous_release, candidate_release}
        elif no_restore_boundary:
            target_is_valid = current_target == previous_release
        elif phase == "migrated":
            # The link replacement is atomic but its following journal write
            # cannot be.  Either target is a valid pre-start recovery state.
            target_is_valid = current_target in {previous_release, candidate_release}
        else:
            expected_target = (
                candidate_release
                if phase
                in {
                    "switched",
                    "start_requested",
                    "health_checking",
                    "healthy",
                    "intervention_required",
                }
                else previous_release
            )
            target_is_valid = current_target == expected_target
        if not target_is_valid:
            return None
        database_existed = payload.get("database_existed", True)
        if not isinstance(database_existed, bool):
            return None
        if effective_phase in {
            "backed_up",
            "migrated",
            "switched",
            "start_requested",
            "healthy",
        }:
            if database_existed:
                if not self._backup_record_is_valid(payload.get("backup"), candidate):
                    return None
            elif payload.get("backup") is not None:
                return None
        elif payload.get("backup") is not None:
            return None
        return payload

    def _write_journal(self, journal: dict[str, Any]) -> bool:
        try:
            _atomic_write_json(self.config.deployment_journal_path, journal, mode=0o600)
        except OSError:
            return False
        return True

    def _advance_journal(self, journal: dict[str, Any], phase: str) -> bool:
        current = journal.get("phase")
        if current not in ACTIVATION_PHASES or phase not in ACTIVATION_PHASES:
            return False
        if ACTIVATION_PHASES.index(phase) != ACTIVATION_PHASES.index(current) + 1:
            return False
        journal["phase"] = phase
        journal["phases"].append(phase)
        return self._write_journal(journal)

    def _mark_intervention(self, journal: dict[str, Any]) -> None:
        journal["phase"] = "intervention_required"
        self._write_journal(journal)

    def _write_receipt(self, journal: dict[str, Any]) -> None:
        _ensure_directory(self.config.deployment_receipts_dir, mode=0o700)
        receipt = self._activation_receipt(journal)
        destination = self.config.deployment_receipts_dir / (
            f"{journal['candidate_sha']}.json"
        )
        if _path_exists_lstat(destination):
            existing = _read_small_json_object(destination)
            if existing != receipt:
                raise OSError("existing activation receipt is inconsistent")
            return
        _atomic_write_json(destination, receipt, mode=0o600)

    def _activation_receipt(self, journal: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "candidate_sha": journal["candidate_sha"],
            "previous_sha": journal["previous_sha"],
            "phase": "healthy",
            "phases": list(journal["phases"]),
            "backup": journal.get("backup"),
        }

    def _write_quarantine(self, sha: str, reason: str) -> bool:
        if (
            not _is_canonical_sha(sha)
            or not isinstance(reason, str)
            or not reason
            or len(reason.encode("utf-8")) > 4096
        ):
            return False
        payload = {"version": 1, "candidate_sha": sha, "reason": reason}
        try:
            _ensure_directory(self.config.quarantine_dir, mode=0o700)
            destination = self.config.quarantine_dir / f"{sha}.json"
            if _path_exists_lstat(destination):
                return self._quarantine_record_is_valid(
                    destination,
                    expected=payload,
                )
            _atomic_write_json(
                destination,
                payload,
                mode=0o600,
            )
        except OSError:
            return False
        return self._quarantine_record_is_valid(destination, expected=payload)

    def _quarantine_record_is_valid(
        self,
        path: Path,
        *,
        expected: dict[str, Any] | None = None,
    ) -> bool:
        if not _trusted_path_metadata(
            path,
            expected_uid=self._control_plane_uid(),
            expected_gid=self._control_plane_gid(),
            kind="file",
            require_private=True,
        ):
            return False
        try:
            if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
                return False
        except OSError:
            return False
        payload = _read_small_json_object(path)
        if payload is None or set(payload) != {"version", "candidate_sha", "reason"}:
            return False
        if (
            payload.get("version") != 1
            or not _is_canonical_sha(payload.get("candidate_sha"))
            or not isinstance(payload.get("reason"), str)
            or not payload["reason"]
            or len(payload["reason"].encode("utf-8")) > 4096
        ):
            return False
        return expected is None or payload == expected

    def _candidate_is_quarantined(self, sha: str) -> bool:
        if not _is_canonical_sha(sha):
            return True
        try:
            metadata = self.config.quarantine_dir.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if not stat.S_ISDIR(metadata.st_mode) or self.config.quarantine_dir.is_symlink():
            return True
        try:
            marker = self.config.quarantine_dir / f"{sha}.json"
            if not _path_exists_lstat(marker):
                return False
            # Invalid quarantine evidence still blocks a candidate; only an
            # exact record is eligible for automatic rollback completion.
            return True
        except OSError:
            return True

    def _clear_journal(self, *, required: bool = False) -> bool:
        try:
            self.config.deployment_journal_path.unlink()
            _fsync_directory(self.config.deployment_journal_path.parent)
        except FileNotFoundError:
            return True
        except OSError:
            if required:
                raise
            return False
        return True

    def _ready_release_is_safe(self, release: Path, sha: str) -> bool:
        if not _is_canonical_sha(sha) or release != self.config.releases_dir / sha:
            return False
        return self._ready_release_tree_is_safe(release, sha)

    def _ready_release_tree_is_safe(self, release: Path, sha: str) -> bool:
        if not _is_canonical_sha(sha):
            return False
        try:
            root_metadata = release.lstat()
        except OSError:
            return False
        if not stat.S_ISDIR(root_metadata.st_mode) or release.is_symlink():
            return False
        if not _tree_metadata_is_safe(
            release,
            expected_uid=self.config.release_uid,
            expected_gid=self.config.release_gid,
        ):
            return False
        manifest_path = release / ".ready.json"
        uv_lock_path = release / "uv.lock"
        python_path = release / ".venv/bin/python"
        if not all(_regular_file_without_symlink(path) for path in (manifest_path, uv_lock_path)):
            return False
        if not _valid_python_entrypoint(release, python_path):
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actual_digest = _file_sha256(uv_lock_path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(manifest, dict):
            return False
        if self.config.test_mode and set(manifest) == {"sha", "uv_lock_sha256"}:
            return (
                manifest.get("sha") == sha
                and manifest.get("uv_lock_sha256") == actual_digest
            )
        required = {
            "version",
            "sha",
            "source_tree_sha256",
            "archive_sha256",
            "uv_lock_sha256",
            "venv_sha256",
            "artifact_sha256",
            "provenance_sha256",
        }
        if set(manifest) != required or manifest.get("version") != 2:
            return False
        digest_fields = required - {"version", "sha"}
        if manifest.get("sha") != sha or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get(field, "")))
            for field in digest_fields
        ):
            return False
        try:
            venv_digest = _venv_tree_digest(release / ".venv")
            artifact_digest = _release_tree_digest(release)
        except (OSError, ValueError):
            return False
        if (
            manifest.get("uv_lock_sha256") != actual_digest
            or manifest.get("venv_sha256") != venv_digest
            or manifest.get("artifact_sha256") != artifact_digest
        ):
            return False
        provenance = {
            key: manifest[key]
            for key in (
                "sha",
                "source_tree_sha256",
                "archive_sha256",
                "uv_lock_sha256",
                "venv_sha256",
                "artifact_sha256",
            )
        }
        provenance_digest = hashlib.sha256(
            json.dumps(
                provenance,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return manifest.get("provenance_sha256") == provenance_digest

    def _release_link_target(self, link: Path) -> Path | None:
        if not link.is_symlink():
            return None
        try:
            target = link.resolve(strict=True)
            target.relative_to(self.config.releases_dir)
        except (OSError, ValueError):
            return None
        return target

    def _switch_link(self, link: Path, release: Path) -> None:
        if not self._ready_release_is_safe(release, release.name):
            raise OSError("release link target is unsafe")
        _ensure_directory(link.parent, mode=0o755)
        if link.exists() and not link.is_symlink():
            raise OSError("release link path is not a symlink")
        temporary = link.parent / f".{link.name}.{release.name}.tmp"
        if temporary.exists() and not temporary.is_symlink():
            raise OSError("temporary release link path is unsafe")
        if temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(release)
        os.replace(temporary, link)
        _fsync_directory(link.parent)

    def _systemctl(self, action: str) -> subprocess.CompletedProcess[Any] | None:
        return self._run_control_command(
            (str(self.config.systemctl_path), action, self.config.service_name)
        )

    def _release_is_stably_healthy(self, release: Path) -> bool:
        baseline: ServiceObservation | None = None
        for index in range(self.config.stable_health_checks):
            observation = self._service_observation()
            if observation is None or (
                baseline is not None and observation != baseline
            ):
                return False
            baseline = observation
            health = self._healthcheck(release, self.config.database_path)
            if health is None or health.returncode != 0:
                return False
            if index + 1 < self.config.stable_health_checks:
                try:
                    self.sleeper(self.config.stable_health_interval_seconds)
                except Exception:
                    return False
        return True

    def _service_observation(self) -> ServiceObservation | None:
        active = self._run_control_command(
            (
                str(self.config.systemctl_path),
                "is-active",
                "--quiet",
                self.config.service_name,
            )
        )
        if active is None or active.returncode != 0:
            return None
        if self.config.test_mode:
            # Unit fakes expose only is-active. Production additionally pins
            # the process identity and restart/exit counters across the whole
            # stable-health window below.
            return ServiceObservation(main_pid=1, restart_count=0, exit_status=0)

        shown = self._run_control_command(
            (
                str(self.config.systemctl_path),
                "show",
                "--no-pager",
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
                "--property=NRestarts",
                "--property=ExecMainStatus",
                self.config.service_name,
            )
        )
        if shown is None or shown.returncode != 0:
            return None
        fields: dict[str, str] = {}
        for line in _stdout_text(shown).splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in fields:
                return None
            fields[key] = value
        if set(fields) != {
            "ActiveState",
            "SubState",
            "MainPID",
            "NRestarts",
            "ExecMainStatus",
        }:
            return None
        if fields["ActiveState"] != "active" or fields["SubState"] != "running":
            return None
        try:
            observation = ServiceObservation(
                main_pid=int(fields["MainPID"], 10),
                restart_count=int(fields["NRestarts"], 10),
                exit_status=int(fields["ExecMainStatus"], 10),
            )
        except ValueError:
            return None
        if (
            observation.main_pid <= 0
            or observation.restart_count < 0
            or observation.exit_status != 0
        ):
            return None
        return observation

    def _write_deployed_sha(self, sha: str) -> None:
        if not _is_canonical_sha(sha):
            raise OSError("invalid deployed sha")
        _atomic_write_bytes(
            self.config.deployed_state_path,
            f"{sha}\n".encode("ascii"),
            mode=0o644,
        )


def _ensure_directory(path: Path, *, mode: int) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        path.mkdir(parents=True, mode=mode, exist_ok=False)
        metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise OSError(f"unsafe directory: {path}")


def _path_exists_lstat(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _directory_is_empty(path: Path) -> bool:
    with os.scandir(path) as entries:
        return next(entries, None) is None


def _read_small_json_object(path: Path) -> dict[str, Any] | None:
    try:
        descriptor, metadata = _open_pinned_regular(path)
    except (OSError, ValueError):
        return None
    try:
        opened_snapshot = _metadata_snapshot(metadata)
        if metadata.st_size > 64 * 1024:
            return None
        payload = bytearray()
        while chunk := os.read(descriptor, 4096):
            payload.extend(chunk)
            if len(payload) > 64 * 1024:
                return None
        if (
            _metadata_snapshot(os.fstat(descriptor)) != opened_snapshot
            or not _path_matches_metadata(path, metadata)
        ):
            return None
    finally:
        os.close(descriptor)
    try:
        decoded = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _validate_bootstrap_database(
    path: Path,
    *,
    expected_uid: int | None,
    expected_gid: int | None,
    require_root_ancestors: bool,
) -> None:
    if (expected_uid is None) != (expected_gid is None):
        raise ValueError("bootstrap database identity is incomplete")
    if require_root_ancestors:
        assert expected_uid is not None
        assert expected_gid is not None
        if not _trusted_path_metadata(
            path.parent,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            kind="directory",
        ):
            raise ValueError("bootstrap database directory is unsafe")
        if not _root_ancestor_metadata_is_safe(path.parent):
            raise ValueError("bootstrap database ancestor is unsafe")
    sidecars = tuple(
        path.with_name(f"{path.name}{suffix}")
        for suffix in ("-wal", "-shm", "-journal")
    )
    if any(_path_exists_lstat(sidecar) for sidecar in sidecars):
        raise ValueError("bootstrap database has unresolved journal sidecars")

    descriptor, metadata = _open_pinned_regular(path)
    try:
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("bootstrap database mode is unsafe")
        if expected_uid is not None and (
            metadata.st_uid != expected_uid or metadata.st_gid != expected_gid
        ):
            raise ValueError("bootstrap database owner is unsafe")
        opened_snapshot = _metadata_snapshot(metadata)
        if require_root_ancestors:
            descriptor_path = Path(f"/proc/self/fd/{descriptor}")
            if not descriptor_path.exists():
                raise ValueError("pinned bootstrap database descriptor is unavailable")
            connection_path = descriptor_path
        else:
            connection_path = path
        with closing(
            sqlite3.connect(
                _sqlite_readonly_uri(connection_path, immutable=True),
                uri=True,
            )
        ) as database:
            database.execute("PRAGMA query_only = ON")
            version_row = database.execute("PRAGMA user_version").fetchone()
            if version_row != (BOOTSTRAP_SCHEMA_VERSION,):
                raise ValueError("bootstrap database schema version is incompatible")
            integrity = database.execute("PRAGMA integrity_check").fetchall()
            if integrity != [("ok",)]:
                raise ValueError("bootstrap database integrity check failed")
            if database.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("bootstrap database foreign keys are invalid")
            table_rows = database.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
            table_names = {
                row[0]
                for row in table_rows
                if isinstance(row, tuple)
                and len(row) == 1
                and isinstance(row[0], str)
            }
            for table_name, required_columns in BOOTSTRAP_REQUIRED_SCHEMA.items():
                if table_name not in table_names:
                    raise ValueError("bootstrap database is missing required tables")
                quoted_table = table_name.replace('"', '""')
                column_rows = database.execute(
                    f'PRAGMA table_info("{quoted_table}")'
                ).fetchall()
                actual_columns = {
                    row[1]
                    for row in column_rows
                    if isinstance(row, tuple)
                    and len(row) >= 2
                    and isinstance(row[1], str)
                }
                if not required_columns.issubset(actual_columns):
                    raise ValueError("bootstrap database is missing required columns")
        if _metadata_snapshot(os.fstat(descriptor)) != opened_snapshot:
            raise ValueError("bootstrap database changed during validation")
        if not _path_matches_metadata(path, metadata):
            raise ValueError("bootstrap database path changed during validation")
    finally:
        os.close(descriptor)
    if any(_path_exists_lstat(sidecar) for sidecar in sidecars):
        raise ValueError("bootstrap database sidecar appeared during validation")


def _trusted_path_metadata(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    kind: str,
    require_private: bool = False,
) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    expected_type = stat.S_ISDIR if kind == "directory" else stat.S_ISREG
    return (
        expected_type(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == expected_uid
        and metadata.st_gid == expected_gid
        and not metadata.st_mode & 0o022
        and (kind == "directory" or metadata.st_nlink == 1)
        and (not require_private or not metadata.st_mode & 0o077)
    )


def _root_ancestor_metadata_is_safe(path: Path) -> bool:
    if not path.is_absolute():
        return False
    for ancestor in path.parents:
        try:
            metadata = os.lstat(ancestor)
        except OSError:
            return False
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_mode & 0o022
        ):
            return False
    return True


def _trusted_optional_symlink(
    path: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return (
        stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == expected_uid
        and metadata.st_gid == expected_gid
    )


def _trusted_json_namespace(
    directory: Path,
    *,
    expected_uid: int,
    expected_gid: int,
) -> bool:
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return False
    for entry in entries:
        name = entry.name
        if not re.fullmatch(r"[0-9a-f]{40}\.json", name):
            return False
        if not _trusted_path_metadata(
            directory / name,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            kind="file",
        ):
            return False
    return True


def _regular_file_without_symlink(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink()
    except OSError:
        return False


def _pull_side_matches(side: object, repository: str, *, ref: str | None = None) -> bool:
    if not isinstance(side, dict):
        return False
    repo = side.get("repo")
    if not isinstance(repo, dict) or repo.get("full_name") != repository:
        return False
    return ref is None or side.get("ref") == ref


def _raw_commit_has_single_ssh_signature(raw_commit: bytes) -> bool:
    if len(raw_commit) > 1024 * 1024 or b"\0" in raw_commit or b"\r" in raw_commit:
        return False
    raw_headers, separator, _message = raw_commit.partition(b"\n\n")
    if not separator or not raw_headers:
        return False

    signatures: list[list[bytes]] = []
    current_key: bytes | None = None
    current_value: list[bytes] = []

    def finish_header() -> None:
        nonlocal current_key, current_value
        if current_key == b"gpgsig":
            signatures.append(current_value)
        current_key = None
        current_value = []

    for line in raw_headers.split(b"\n"):
        if line.startswith(b" "):
            if current_key is None:
                return False
            current_value.append(line[1:])
            continue
        finish_header()
        key, delimiter, value = line.partition(b" ")
        if not delimiter or not key or any(byte <= 0x20 or byte >= 0x7F for byte in key):
            return False
        current_key = key
        current_value = [value]
    finish_header()

    if len(signatures) != 1:
        return False
    signature = signatures[0]
    if len(signature) < 3:
        return False
    if (
        signature[0] != b"-----BEGIN SSH SIGNATURE-----"
        or signature[-1] != b"-----END SSH SIGNATURE-----"
    ):
        return False
    return all(
        line
        and b"-----BEGIN " not in line
        and b"-----END " not in line
        for line in signature[1:-1]
    )


def _make_runtime_accessible(
    root: Path,
    database: Path,
    uid: int | None,
    gid: int | None,
) -> None:
    root.chmod(0o700)
    database.chmod(0o600)
    if os.geteuid() != 0:
        return
    if uid is None or gid is None:
        raise OSError("runtime identity is missing")
    os.chown(database, uid, gid)
    os.chown(root, uid, gid)


def _validate_sqlite_database(path: Path) -> int:
    descriptor, metadata = _open_pinned_regular(path)
    try:
        with closing(
            sqlite3.connect(
                _sqlite_readonly_uri(path, immutable=True),
                uri=True,
            )
        ) as database:
            with database:
                if not _path_matches_metadata(path, metadata):
                    raise ValueError("SQLite path changed while opening")
                integrity = database.execute("PRAGMA integrity_check").fetchall()
                if integrity != [("ok",)]:
                    raise ValueError("SQLite integrity check failed")
                if database.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("SQLite foreign key check failed")
                schema_version = database.execute("PRAGMA user_version").fetchone()
                if not _path_matches_metadata(path, metadata):
                    raise ValueError("SQLite path changed while validating")
    finally:
        os.close(descriptor)
    if (
        schema_version is None
        or isinstance(schema_version[0], bool)
        or not isinstance(schema_version[0], int)
        or schema_version[0] < 0
    ):
        raise ValueError("SQLite schema version is invalid")
    return schema_version[0]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor, metadata = _open_pinned_regular(path)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if not _path_matches_metadata(path, metadata):
            raise OSError("file changed while hashing")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _sqlite_readonly_uri(path: Path, *, immutable: bool = False) -> str:
    immutable_query = "&immutable=1" if immutable else ""
    return (
        f"file:{urllib.parse.quote(str(path), safe='/')}?mode=ro"
        f"{immutable_query}"
    )


def _open_pinned_regular(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError(f"unsafe regular file: {path}")
        if not _path_matches_metadata(path, metadata):
            raise ValueError(f"path changed while opening: {path}")
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _create_pinned_regular(path: Path, *, mode: int) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not _path_matches_metadata(path, metadata)
        ):
            raise ValueError(f"unsafe created file: {path}")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _copy_pinned_regular(
    source: Path,
    destination: Path,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    source_descriptor, source_metadata = _open_pinned_regular(source)
    destination_descriptor: int | None = None
    try:
        destination_descriptor = _create_pinned_regular(destination, mode=mode)
        while chunk := os.read(source_descriptor, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise OSError("short database clone write")
                view = view[written:]
        if not _path_matches_metadata(source, source_metadata):
            raise ValueError("database clone output changed while copying")
        os.fchmod(destination_descriptor, mode)
        os.fchown(destination_descriptor, uid, gid)
        os.fsync(destination_descriptor)
    except Exception:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
            destination_descriptor = None
        try:
            _unlink_regular_or_missing(destination)
        except OSError:
            pass
        raise
    finally:
        os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _path_matches_metadata(path: Path, metadata: os.stat_result) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_nlink == 1
        and (current.st_dev, current.st_ino) == (metadata.st_dev, metadata.st_ino)
    )


def _unlink_regular_or_missing(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing to remove unsafe path: {path}")
    path.unlink()


def _remove_database_files(database: Path) -> None:
    for path in (
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        database.with_name(f"{database.name}-journal"),
    ):
        _unlink_regular_or_missing(path)


def _remove_database_sidecars(database: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        _unlink_regular_or_missing(database.with_name(f"{database.name}{suffix}"))


def _atomic_write_json(path: Path, payload: object, *, mode: int) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    _atomic_write_bytes(path, encoded, mode=mode)


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    _ensure_directory(path.parent, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    _unlink_regular_or_missing(temporary)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(payload)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            _unlink_regular_or_missing(temporary)
        except OSError:
            pass
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, getattr(errno, "ENOTSUP", -1)}:
                raise
    finally:
        os.close(descriptor)


def _valid_python_entrypoint(root: Path, path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISREG(metadata.st_mode):
        return bool(metadata.st_mode & 0o111)
    if not stat.S_ISLNK(metadata.st_mode) or not _is_allowed_venv_symlink(root, path):
        return False
    try:
        resolved = path.resolve(strict=True)
        resolved_metadata = resolved.stat()
    except OSError:
        return False
    if not stat.S_ISREG(resolved_metadata.st_mode) or not resolved_metadata.st_mode & 0o111:
        return False
    allowed_internal = resolved.parent == root / ".venv/bin"
    allowed_system = _is_trusted_external_python(resolved)
    return (allowed_internal or allowed_system) and _is_python_executable_name(resolved.name)


def _result(status: ResultStatus, reason: str, sha: str | None = None) -> ControllerResult:
    return ControllerResult(status=status, reason=reason, sha=sha)


def _is_canonical_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _stdout_text(result: subprocess.CompletedProcess[Any]) -> str:
    output = result.stdout
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def _stdout_bytes(result: subprocess.CompletedProcess[Any]) -> bytes:
    output = result.stdout
    if isinstance(output, bytes):
        return output
    return str(output).encode("utf-8")


def _parse_exact_fields(
    payload: str,
    expected: frozenset[str],
) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    for line in payload.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in fields:
            return None
        fields[key] = value
    return fields if set(fields) == expected else None


def _tree_is_safe(raw: bytes) -> bool:
    return _parse_git_tree(raw) is not None


def _parse_git_tree(raw: bytes) -> dict[str, GitTreeEntry] | None:
    if not raw or not raw.endswith(b"\0"):
        return None
    entries = raw[:-1].split(b"\0")
    if not entries or any(not entry for entry in entries):
        return None
    parsed: dict[str, GitTreeEntry] = {}
    for entry in entries:
        try:
            meta, raw_name = entry.split(b"\t", 1)
            mode, kind, object_sha = meta.decode("ascii").split(" ", 2)
            name = raw_name.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        if mode not in {"100644", "100755"} or kind != "blob":
            return None
        if not _is_canonical_sha(object_sha) or not _safe_relative_name(name):
            return None
        if (
            name in parsed
            or _path_conflicts_with_files(name, set(parsed))
            or any(existing.startswith(f"{name}/") for existing in parsed)
        ):
            return None
        parsed[name] = GitTreeEntry(
            mode=0o755 if mode == "100755" else 0o644,
            object_id=object_sha,
        )
    return parsed


def _git_blob_object_id(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def _safe_relative_name(name: str) -> bool:
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name:
        return False
    if any(part in ("", ".", "..") for part in path.parts):
        return False
    if path.parts[0] in RESERVED_RELEASE_PATHS:
        return False
    return True


def _validated_tar_members(raw: bytes) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    seen: set[str] = set()
    files: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive.getmembers():
            if not _safe_tar_member(member):
                raise ValueError(f"unsafe archive member: {member.name}")
            if member.name in seen or _path_conflicts_with_files(member.name, files):
                raise ValueError(f"conflicting archive member: {member.name}")
            if member.isfile() and any(
                existing.startswith(f"{member.name}/") for existing in seen
            ):
                raise ValueError(f"conflicting archive member: {member.name}")
            payload = None
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"missing archive payload: {member.name}")
                payload = source.read()
                files.add(member.name)
            seen.add(member.name)
            members.append((member, payload))
    return members


def _archive_matches_tree(
    members: list[tuple[tarfile.TarInfo, bytes | None]],
    tree: dict[str, GitTreeEntry],
) -> bool:
    expected_directories = {
        "/".join(PurePosixPath(name).parts[:index])
        for name in tree
        for index in range(1, len(PurePosixPath(name).parts))
    }
    archived_files: set[str] = set()
    for member, payload in members:
        if member.isdir():
            if member.name not in expected_directories:
                return False
            continue
        expected = tree.get(member.name)
        if expected is None or payload is None:
            return False
        actual_mode = 0o755 if member.mode & 0o111 else 0o644
        if (
            member.name in archived_files
            or actual_mode != expected.mode
            or _git_blob_object_id(payload) != expected.object_id
        ):
            return False
        archived_files.add(member.name)
    return archived_files == set(tree)


def _extract_validated_tar(
    members: list[tuple[tarfile.TarInfo, bytes | None]],
    destination: Path,
) -> None:
    for member, payload in members:
        target = destination / member.name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
            continue
        if target.parent != destination:
            target.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            raise ValueError(f"missing archive payload: {member.name}")
        with target.open("xb") as output:
            output.write(payload)
        target.chmod(0o755 if member.mode & 0o111 else 0o644)


def _safe_tar_member(member: tarfile.TarInfo) -> bool:
    if not _safe_relative_name(member.name):
        return False
    return member.isfile() or member.isdir()


def _path_conflicts_with_files(name: str, files: set[str]) -> bool:
    parts = PurePosixPath(name).parts
    return any("/".join(parts[:index]) in files for index in range(1, len(parts)))


def _expected_source_directories(tree: dict[str, GitTreeEntry]) -> set[str]:
    return {
        "/".join(PurePosixPath(name).parts[:index])
        for name in tree
        for index in range(1, len(PurePosixPath(name).parts))
    }


def _set_authenticated_source_metadata(
    release: Path,
    tree: dict[str, GitTreeEntry],
    *,
    uid: int,
    gid: int,
) -> None:
    for name in sorted(_expected_source_directories(tree), key=lambda item: item.count("/")):
        _set_path_metadata(release / name, mode=0o755, uid=uid, gid=gid)
    for name, entry in tree.items():
        _set_path_metadata(release / name, mode=entry.mode, uid=uid, gid=gid)
    _set_path_metadata(release, mode=0o755, uid=uid, gid=gid)


def _set_path_metadata(path: Path, *, mode: int, uid: int | None, gid: int | None) -> None:
    if uid is None or gid is None:
        raise OSError("deployment identity is missing")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    metadata = os.lstat(path)
    if stat.S_ISDIR(metadata.st_mode):
        flags |= getattr(os, "O_DIRECTORY", 0)
    elif not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"unsafe metadata target: {path}")
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise OSError(f"metadata target changed: {path}")
        if opened.st_uid != uid or opened.st_gid != gid:
            os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        if not _path_matches_inode(path, opened):
            raise OSError(f"metadata target changed: {path}")
    finally:
        os.close(descriptor)


def _path_matches_inode(path: Path, metadata: os.stat_result) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return (
        not stat.S_ISLNK(current.st_mode)
        and (current.st_dev, current.st_ino) == (metadata.st_dev, metadata.st_ino)
    )


def _authenticated_source_matches(
    release: Path,
    tree: dict[str, GitTreeEntry],
    *,
    uid: int,
    gid: int,
) -> bool:
    expected_directories = _expected_source_directories(tree)
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    try:
        root_metadata = os.lstat(release)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != uid
            or root_metadata.st_gid != gid
            or root_metadata.st_mode & 0o022
        ):
            return False
        for path in release.rglob("*"):
            relative = path.relative_to(release)
            if relative.parts[0] in {".venv", ".ready.json"}:
                continue
            name = relative.as_posix()
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                return False
            if metadata.st_uid != uid or metadata.st_gid != gid:
                return False
            if metadata.st_mode & 0o022:
                return False
            if stat.S_ISDIR(metadata.st_mode):
                actual_directories.add(name)
                if stat.S_IMODE(metadata.st_mode) != 0o755:
                    return False
                continue
            if not stat.S_ISREG(metadata.st_mode):
                return False
            expected = tree.get(name)
            if expected is None or stat.S_IMODE(metadata.st_mode) != expected.mode:
                return False
            descriptor, opened = _open_pinned_regular(path)
            try:
                payload = bytearray()
                while chunk := os.read(descriptor, 1024 * 1024):
                    payload.extend(chunk)
            finally:
                os.close(descriptor)
            if (
                opened.st_uid != uid
                or opened.st_gid != gid
                or _git_blob_object_id(bytes(payload)) != expected.object_id
            ):
                return False
            actual_files.add(name)
    except (OSError, ValueError):
        return False
    return actual_files == set(tree) and actual_directories == expected_directories


def _seal_build_venv(release: Path, *, uid: int, gid: int) -> None:
    build_tree = release / ".venv"
    nonce = f"{os.getpid()}-{time.time_ns()}"
    sealed_tree = release / f".venv-sealed-{nonce}"
    retired_tree = release / f".venv-build-{nonce}"
    for path in (sealed_tree, retired_tree):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        raise OSError(f"virtualenv staging path already exists: {path}")

    before = _venv_tree_digest(build_tree)
    try:
        copied = _copy_venv_tree_nofollow(
            build_tree,
            sealed_tree,
            uid=uid,
            gid=gid,
        )
        after = _venv_tree_digest(build_tree)
        sealed = _venv_tree_digest(sealed_tree)
        if not (before == copied == after == sealed):
            raise OSError("build output changed while it was sealed")

        os.replace(build_tree, retired_tree)
        try:
            os.replace(sealed_tree, build_tree)
        except Exception:
            os.replace(retired_tree, build_tree)
            raise
        _fsync_directory(release)
        _remove_tree_nofollow(retired_tree)
    except Exception:
        for path in (sealed_tree, retired_tree):
            try:
                _remove_tree_nofollow(path)
            except (FileNotFoundError, OSError):
                pass
        raise


def _open_directory_nofollow(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or not _path_matches_inode(path, metadata):
            raise OSError(f"unsafe directory: {path}")
        return descriptor, metadata
    except Exception:
        os.close(descriptor)
        raise


def _normalized_artifact_mode(metadata: os.stat_result) -> int:
    return 0o755 if metadata.st_mode & 0o111 else 0o644


def _digest_record(
    digest: Any,
    *,
    kind: str,
    relative: tuple[str, ...],
    mode: int,
    size: int,
) -> None:
    encoded = json.dumps(
        [kind, "/".join(relative), mode, size],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _metadata_snapshot(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _safe_venv_symlink(
    relative: tuple[str, ...],
    target: str,
    *,
    parent_descriptor: int,
) -> bool:
    if relative == ("lib64",):
        if target != "lib":
            return False
        try:
            library = os.stat(
                "lib",
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            return False
        return stat.S_ISDIR(library.st_mode)
    if len(relative) != 2 or relative[0] != "bin":
        return False
    if not _is_python_executable_name(relative[1]):
        return False
    target_path = PurePosixPath(target)
    if target_path.is_absolute():
        return _is_python_executable_name(target_path.name) and str(
            target_path.parent
        ) in {"/usr/bin", "/usr/local/bin"}
    return (
        len(target_path.parts) == 1
        and target_path.parts[0] not in {"", ".", ".."}
        and _is_python_executable_name(target_path.name)
    )


def _venv_tree_digest(root: Path) -> str:
    descriptor, before = _open_directory_nofollow(root)
    digest = hashlib.sha256()
    try:
        _digest_directory_nofollow(
            descriptor,
            (),
            digest,
            venv_prefix=(),
            excluded_root_names=frozenset(),
        )
        after = os.fstat(descriptor)
        if _metadata_snapshot(before) != _metadata_snapshot(after):
            raise OSError("virtualenv root changed while hashing")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _digest_directory_nofollow(
    descriptor: int,
    relative: tuple[str, ...],
    digest: Any,
    *,
    venv_prefix: tuple[str, ...],
    excluded_root_names: frozenset[str],
) -> None:
    for name in sorted(os.listdir(descriptor)):
        if not relative and name in excluded_root_names:
            continue
        if not name or name in {".", ".."} or "/" in name or "\0" in name:
            raise OSError("unsafe virtualenv entry name")
        child_relative = (*relative, name)
        before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            _digest_record(
                digest,
                kind="directory",
                relative=child_relative,
                mode=0o755,
                size=0,
            )
            child_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            child_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            child = os.open(name, child_flags, dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if _metadata_snapshot(before) != _metadata_snapshot(opened):
                    raise OSError("virtualenv directory changed while opening")
                _digest_directory_nofollow(
                    child,
                    child_relative,
                    digest,
                    venv_prefix=venv_prefix,
                    excluded_root_names=excluded_root_names,
                )
                if _metadata_snapshot(opened) != _metadata_snapshot(os.fstat(child)):
                    raise OSError("virtualenv directory changed while hashing")
            finally:
                os.close(child)
        elif stat.S_ISREG(before.st_mode):
            mode = _normalized_artifact_mode(before)
            _digest_record(
                digest,
                kind="file",
                relative=child_relative,
                mode=mode,
                size=before.st_size,
            )
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            child = os.open(name, flags, dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if _metadata_snapshot(before) != _metadata_snapshot(opened):
                    raise OSError("virtualenv file changed while opening")
                while chunk := os.read(child, 1024 * 1024):
                    digest.update(chunk)
                if _metadata_snapshot(opened) != _metadata_snapshot(os.fstat(child)):
                    raise OSError("virtualenv file changed while hashing")
            finally:
                os.close(child)
        elif stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=descriptor)
            if (
                child_relative[: len(venv_prefix)] != venv_prefix
                or not _safe_venv_symlink(
                    child_relative[len(venv_prefix) :],
                    target,
                    parent_descriptor=descriptor,
                )
            ):
                raise OSError("unsafe virtualenv symlink")
            encoded_target = target.encode("utf-8", errors="surrogateescape")
            _digest_record(
                digest,
                kind="symlink",
                relative=child_relative,
                mode=0,
                size=len(encoded_target),
            )
            digest.update(encoded_target)
        else:
            raise OSError("unsafe virtualenv file type")
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if _metadata_snapshot(before) != _metadata_snapshot(current):
            raise OSError("virtualenv entry changed while hashing")


def _release_tree_digest(release: Path) -> str:
    descriptor, before = _open_directory_nofollow(release)
    digest = hashlib.sha256()
    try:
        _digest_directory_nofollow(
            descriptor,
            (),
            digest,
            venv_prefix=(".venv",),
            excluded_root_names=frozenset({".ready.json"}),
        )
        if _metadata_snapshot(before) != _metadata_snapshot(os.fstat(descriptor)):
            raise OSError("release root changed while hashing")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _copy_venv_tree_nofollow(
    source: Path,
    destination: Path,
    *,
    uid: int,
    gid: int,
) -> str:
    source_descriptor, source_before = _open_directory_nofollow(source)
    destination.mkdir(mode=0o700)
    _set_path_metadata(destination, mode=0o700, uid=uid, gid=gid)
    destination_descriptor, _ = _open_directory_nofollow(destination)
    digest = hashlib.sha256()
    try:
        _copy_directory_nofollow(
            source_descriptor,
            destination_descriptor,
            (),
            digest,
            uid=uid,
            gid=gid,
        )
        if _metadata_snapshot(source_before) != _metadata_snapshot(
            os.fstat(source_descriptor)
        ):
            raise OSError("virtualenv root changed while copying")
        os.fchmod(destination_descriptor, 0o755)
        os.fchown(destination_descriptor, uid, gid)
        os.fsync(destination_descriptor)
    finally:
        os.close(destination_descriptor)
        os.close(source_descriptor)
    _fsync_directory(destination.parent)
    return digest.hexdigest()


def _copy_directory_nofollow(
    source: int,
    destination: int,
    relative: tuple[str, ...],
    digest: Any,
    *,
    uid: int,
    gid: int,
) -> None:
    for name in sorted(os.listdir(source)):
        if not name or name in {".", ".."} or "/" in name or "\0" in name:
            raise OSError("unsafe virtualenv entry name")
        child_relative = (*relative, name)
        before = os.stat(name, dir_fd=source, follow_symlinks=False)
        if stat.S_ISDIR(before.st_mode):
            _digest_record(
                digest,
                kind="directory",
                relative=child_relative,
                mode=0o755,
                size=0,
            )
            os.mkdir(name, mode=0o700, dir_fd=destination)
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            source_child = os.open(name, flags, dir_fd=source)
            destination_child = os.open(name, flags, dir_fd=destination)
            try:
                opened = os.fstat(source_child)
                if _metadata_snapshot(before) != _metadata_snapshot(opened):
                    raise OSError("virtualenv directory changed while opening")
                _copy_directory_nofollow(
                    source_child,
                    destination_child,
                    child_relative,
                    digest,
                    uid=uid,
                    gid=gid,
                )
                if _metadata_snapshot(opened) != _metadata_snapshot(
                    os.fstat(source_child)
                ):
                    raise OSError("virtualenv directory changed while copying")
                os.fchmod(destination_child, 0o755)
                os.fchown(destination_child, uid, gid)
                os.fsync(destination_child)
            finally:
                os.close(destination_child)
                os.close(source_child)
        elif stat.S_ISREG(before.st_mode):
            mode = _normalized_artifact_mode(before)
            _digest_record(
                digest,
                kind="file",
                relative=child_relative,
                mode=mode,
                size=before.st_size,
            )
            source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            source_flags |= getattr(os, "O_NOFOLLOW", 0)
            source_child = os.open(name, source_flags, dir_fd=source)
            destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            destination_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(
                os, "O_NOFOLLOW", 0
            )
            destination_child = os.open(
                name,
                destination_flags,
                mode,
                dir_fd=destination,
            )
            try:
                opened = os.fstat(source_child)
                if _metadata_snapshot(before) != _metadata_snapshot(opened):
                    raise OSError("virtualenv file changed while opening")
                while chunk := os.read(source_child, 1024 * 1024):
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(destination_child, view)
                        if written <= 0:
                            raise OSError("short virtualenv write")
                        view = view[written:]
                if _metadata_snapshot(opened) != _metadata_snapshot(
                    os.fstat(source_child)
                ):
                    raise OSError("virtualenv file changed while copying")
                os.fchmod(destination_child, mode)
                os.fchown(destination_child, uid, gid)
                os.fsync(destination_child)
            finally:
                os.close(destination_child)
                os.close(source_child)
        elif stat.S_ISLNK(before.st_mode):
            target = os.readlink(name, dir_fd=source)
            if not _safe_venv_symlink(
                child_relative,
                target,
                parent_descriptor=source,
            ):
                raise OSError("unsafe virtualenv symlink")
            encoded_target = target.encode("utf-8", errors="surrogateescape")
            _digest_record(
                digest,
                kind="symlink",
                relative=child_relative,
                mode=0,
                size=len(encoded_target),
            )
            digest.update(encoded_target)
            os.symlink(target, name, dir_fd=destination)
        else:
            raise OSError("unsafe virtualenv file type")
        current = os.stat(name, dir_fd=source, follow_symlinks=False)
        if _metadata_snapshot(before) != _metadata_snapshot(current):
            raise OSError("virtualenv entry changed while copying")


def _remove_tree_nofollow(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError(f"unsafe tree removal target: {path}")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise OSError("platform lacks symlink-safe recursive removal")
    shutil.rmtree(path)


def _finalize_release_tree(
    release: Path,
    sha: str,
    uid: int,
    gid: int,
    *,
    source_tree_sha256: str,
    archive_sha256: str,
    legacy_manifest: bool,
) -> None:
    uv_lock = release / "uv.lock"
    if not _is_canonical_sha(sha) or not _regular_file_without_symlink(uv_lock):
        raise OSError("release metadata is unsafe")
    uv_lock_digest = _file_sha256(uv_lock)

    for path in sorted(release.rglob("*"), key=lambda candidate: len(candidate.parts)):
        if path == release / ".ready.json":
            continue
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            if not _is_allowed_venv_symlink(release, path):
                raise OSError(f"release contains symlink outside .venv: {path}")
            os.lchown(path, uid, gid)
            continue
        if stat.S_ISDIR(metadata.st_mode):
            mode = 0o755
        elif stat.S_ISREG(metadata.st_mode):
            mode = _normalized_artifact_mode(metadata)
        else:
            raise OSError(f"release contains unsafe file type: {path}")
        _set_path_metadata(path, mode=mode, uid=uid, gid=gid)
    _set_path_metadata(release, mode=0o755, uid=uid, gid=gid)

    if legacy_manifest:
        manifest: dict[str, object] = {
            "sha": sha,
            "uv_lock_sha256": uv_lock_digest,
        }
    else:
        venv_digest = _venv_tree_digest(release / ".venv")
        artifact_digest = _release_tree_digest(release)
        provenance = {
            "sha": sha,
            "source_tree_sha256": source_tree_sha256,
            "archive_sha256": archive_sha256,
            "uv_lock_sha256": uv_lock_digest,
            "venv_sha256": venv_digest,
            "artifact_sha256": artifact_digest,
        }
        provenance_digest = hashlib.sha256(
            json.dumps(
                provenance,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        manifest = {
            "version": 2,
            **provenance,
            "provenance_sha256": provenance_digest,
        }
    manifest_path = release / ".ready.json"
    _atomic_write_json(manifest_path, manifest, mode=0o644)
    _set_path_metadata(manifest_path, mode=0o644, uid=uid, gid=gid)
    _set_path_metadata(release, mode=0o755, uid=uid, gid=gid)


def _chown_release_tree(release: Path, uid: int | None, gid: int | None) -> None:
    if uid is None and gid is None:
        return
    owner_uid = -1 if uid is None else uid
    owner_gid = -1 if gid is None else gid
    for path in (release, *release.rglob("*")):
        _chown_path(path, owner_uid, owner_gid)


def _chown_path(path: Path, uid: int, gid: int) -> None:
    if path.is_symlink():
        os.lchown(path, uid, gid)
    else:
        os.chown(path, uid, gid)


def _tree_metadata_is_safe(root: Path, *, expected_uid: int, expected_gid: int) -> bool:
    for path in (root, *root.rglob("*")):
        try:
            metadata = path.lstat()
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode):
            if not _is_allowed_venv_symlink(root, path):
                return False
            if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
                return False
            continue
        if stat.S_ISDIR(metadata.st_mode):
            expected_modes = {0o755}
        elif stat.S_ISREG(metadata.st_mode):
            expected_modes = {0o644, 0o755}
        else:
            return False
        if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
            return False
        if stat.S_IMODE(metadata.st_mode) not in expected_modes:
            return False
    return True


def _is_allowed_venv_symlink(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if relative.parts == (".venv", "lib64"):
        try:
            target = os.readlink(path)
            library = root / ".venv/lib"
            metadata = os.lstat(library)
            resolved = path.resolve(strict=True)
        except OSError:
            return False
        return (
            target == "lib"
            and stat.S_ISDIR(metadata.st_mode)
            and resolved == library
        )
    if len(relative.parts) != 3 or relative.parts[:2] != (".venv", "bin"):
        return False
    if not _is_python_executable_name(relative.name):
        return False
    target = os.readlink(path)
    if target.startswith("/"):
        target_path = PurePosixPath(target)
        target_shape_is_safe = _is_python_executable_name(target_path.name)
    else:
        target_path = PurePosixPath(target)
        target_shape_is_safe = (
            not target_path.is_absolute()
            and len(target_path.parts) == 1
            and _is_python_executable_name(target_path.name)
        )
    if not target_shape_is_safe:
        return False
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and bool(metadata.st_mode & 0o111)
        and _is_python_executable_name(resolved.name)
        and (
            resolved.parent == root / ".venv/bin"
            or _is_trusted_external_python(resolved)
        )
    )


def _is_python_executable_name(name: str) -> bool:
    return re.fullmatch(r"python(?:3(?:\.\d+)?)?", name) is not None


def _is_trusted_external_python(path: Path) -> bool:
    if str(path.parent) in {"/usr/bin", "/usr/local/bin"}:
        return True
    try:
        return path == Path(sys.executable).resolve(strict=True)
    except OSError:
        return False


def _build_default_config() -> ControllerConfig:
    build_uid, build_gid = _identity_for_user("honeybuy-build")
    runtime_uid, runtime_gid = _identity_for_user("honeybuy")
    return ControllerConfig(
        build_uid=build_uid,
        build_gid=build_gid,
        runtime_uid=runtime_uid,
        runtime_gid=runtime_gid,
    )


def _identity_for_user(name: str) -> tuple[int, int]:
    try:
        user = pwd.getpwnam(name)
    except KeyError:
        raise RuntimeError(f"required deployment user does not exist: {name}") from None
    return user.pw_uid, user.pw_gid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Honeybuy release controller")
    parser.add_argument("command", choices=("deploy", "reconcile"))
    args = parser.parse_args(argv)
    try:
        controller = ReleaseController(config=_build_default_config())
    except RuntimeError as exc:
        print(f"rejected: {exc}")
        return 1
    if args.command == "deploy":
        result = controller.deploy()
    elif args.command == "reconcile":
        result = controller.reconcile()
    else:
        raise AssertionError(args.command)
    if args.command in {"deploy", "reconcile"}:
        print(f"{result.status.value}: {result.reason}")
        return (
            0
            if result.status
            in (
                ResultStatus.BOOTSTRAPPED,
                ResultStatus.DEPLOYED,
                ResultStatus.NOOP,
                ResultStatus.PREPARED,
            )
            else 1
        )


if __name__ == "__main__":
    raise SystemExit(main())
