from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import io
import json
import os
import pwd
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from honeybuy_tg.migrations import CURRENT_SCHEMA_VERSION, migrate_database_path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PATH = ROOT / "deploy/ubuntu/release_controller.py"
REPOSITORY = "noxiouz/honeybuy_tg"
BRANCH = "main"
WORKFLOW_PATH = ".github/workflows/tests.yml"
REMOTE_URL = "https://github.com/noxiouz/honeybuy_tg.git"
WORKFLOW_URL = (
    "https://api.github.com/repos/noxiouz/honeybuy_tg/"
    "actions/workflows/tests.yml/runs?branch=main&event=push&per_page=2"
)
LAST_SHA = "1" * 40
TARGET_SHA = "2" * 40
OTHER_SHA = "3" * 40
OBJECT_SHA = "4" * 40
PR_HEAD_SHA = "a" * 40
TREE_SHA = "b" * 40
OTHER_TREE_SHA = "c" * 40
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
DUMMY_MIGRATION_ENV = {
    "TELEGRAM_BOT_TOKEN": "deployment-placeholder",
    "OWNER_USER_ID": "1",
}
CONTROL_PLANE_ASSETS = {
    "deploy/systemd/honeybuy-release-controller.service": (
        b"[Service]\nType=exec\nExecStart=/usr/local/lib/honeybuy-tg/"
        b"release_controller.py\n"
    ),
    "deploy/systemd/honeybuy-release-controller.timer": (
        b"[Timer]\nOnCalendar=*:0/5\n"
    ),
    "deploy/systemd/honeybuy-tg.service": (
        b"[Service]\nType=exec\nExecStart=/opt/honeybuy-tg/current/"
        b".venv/bin/python -m honeybuy_tg\n"
    ),
    "deploy/ubuntu/allowed_signers": (
        b"atiurin@proton.me ssh-ed25519 AAAATEST\n"
    ),
    "deploy/ubuntu/release_controller.py": (
        b"#!/usr/bin/python3\n# authenticated controller fixture\n"
    ),
}
CONTROL_PLANE_CONFIG_PATHS = {
    "deploy/systemd/honeybuy-release-controller.service": (
        "controller_service_unit_path"
    ),
    "deploy/systemd/honeybuy-release-controller.timer": "controller_timer_unit_path",
    "deploy/systemd/honeybuy-tg.service": "app_service_unit_path",
    "deploy/ubuntu/allowed_signers": "allowed_signers_path",
    "deploy/ubuntu/release_controller.py": "installed_controller_path",
}


class SimulatedControllerCrash(BaseException):
    pass


class _StatProxy:
    def __init__(self, metadata: os.stat_result, **overrides: int) -> None:
        self._metadata = metadata
        self._overrides = overrides

    def __getattr__(self, name: str):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._metadata, name)


@pytest.fixture(scope="module")
def controller_module() -> ModuleType:
    if not CONTROLLER_PATH.is_file():
        pytest.fail(
            "deploy/ubuntu/release_controller.py is not implemented yet",
            pytrace=False,
        )

    module_name = "honeybuy_release_controller_under_test"
    spec = importlib.util.spec_from_file_location(module_name, CONTROLLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class RunCall:
    argv: tuple[str, ...]
    cwd: Path | None
    uid: int | None
    gid: int | None
    env: dict[str, str] | None


class FakeRunner:
    def __init__(self, scenario: RunnerScenario, events: list[str]) -> None:
        self.scenario = scenario
        self.events = events
        self.calls: list[RunCall] = []

    def run(
        self,
        argv,
        *,
        cwd=None,
        uid=None,
        gid=None,
        env=None,
    ) -> subprocess.CompletedProcess:
        assert not isinstance(argv, (str, bytes))
        normalized_argv = tuple(str(argument) for argument in argv)
        assert normalized_argv
        call = RunCall(
            argv=normalized_argv,
            cwd=None if cwd is None else Path(cwd),
            uid=uid,
            gid=gid,
            env=None if env is None else dict(env),
        )
        self.calls.append(call)
        self.events.append(f"run:{_command_kind(call)}")
        return self.scenario.respond(call)


class FakeHttp:
    def __init__(
        self,
        payload: object | None,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.events = events
        self.calls: list[tuple[str, float]] = []

    def get_json(self, url: str, *, timeout: float) -> object:
        self.calls.append((url, timeout))
        self.events.append("http:workflow")
        if "/commits/" in url and url.endswith("/pulls"):
            raise AssertionError(
                "associated-PR checks require an explicit RebaseHttp fixture"
            )
        if self.error is not None:
            raise self.error
        return self.payload


class RebaseHttp:
    def __init__(
        self,
        workflow_payload: object,
        pull_payload: object,
        events: list[str],
    ) -> None:
        self.workflow_payload = workflow_payload
        self.pull_payload = pull_payload
        self.events = events
        self.calls: list[tuple[str, float]] = []

    def get_json(self, url: str, *, timeout: float) -> object:
        self.calls.append((url, timeout))
        if "/commits/" in url and url.endswith("/pulls"):
            self.events.append("http:associated-pulls")
            return self.pull_payload
        self.events.append("http:workflow")
        return self.workflow_payload


class FakeSleeper:
    def __init__(self, lock_path: Path | None = None) -> None:
        self.calls: list[float] = []
        self.lock_path = lock_path
        self.lock_held_during_calls: list[bool] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.lock_path is not None:
            self.lock_held_during_calls.append(_lock_is_held(self.lock_path))


@dataclass
class RunnerScenario:
    git_path: Path
    uv_path: Path
    remote_heads: list[str]
    tree_output: bytes
    archive_output: bytes
    raw_commits: dict[str, bytes] = field(default_factory=dict)
    verify_returncode: int = 0
    ancestor_returncode: int = 0
    sync_returncode: int = 0
    smoke_returncode: int = 0
    healthcheck_returncode: int = 0
    migrate_returncode: int = 0
    systemctl_returncode: int = 0
    service_active_returncode: int = 0
    call_hook: Callable[[RunCall, int], subprocess.CompletedProcess | None] | None = None
    _head_index: int = 0
    _kind_counts: dict[str, int] = field(default_factory=dict)

    def respond(self, call: RunCall) -> subprocess.CompletedProcess:
        kind = _command_kind(call)
        occurrence = self._kind_counts.get(kind, 0) + 1
        self._kind_counts[kind] = occurrence
        if self.call_hook is not None:
            hooked = self.call_hook(call, occurrence)
            if hooked is not None:
                return hooked
        if kind == "fetch":
            return _completed(call)
        if kind == "rev-parse":
            index = min(self._head_index, len(self.remote_heads) - 1)
            self._head_index += 1
            return _completed(call, stdout=f"{self.remote_heads[index]}\n")
        if kind == "verify-commit":
            return _completed(
                call,
                returncode=self.verify_returncode,
                stderr="signature verification failed"
                if self.verify_returncode
                else "",
            )
        if kind == "cat-file":
            commit = call.argv[-1]
            raw_commit = self.raw_commits.get(commit)
            return _completed(
                call,
                returncode=0 if raw_commit is not None else 128,
                stdout=raw_commit or b"",
                stderr="missing commit object" if raw_commit is None else b"",
            )
        if kind == "merge-base":
            return _completed(call, returncode=self.ancestor_returncode)
        if kind == "ls-tree":
            return _completed(call, stdout=self.tree_output)
        if kind == "archive":
            return _completed(call, stdout=self.archive_output)
        if kind == "uv-sync":
            if self.sync_returncode == 0:
                assert call.cwd is not None
                python_path = call.cwd / ".venv/bin/python"
                python_path.parent.mkdir(parents=True, exist_ok=True)
                python_path.write_bytes(b"test python shim")
                python_path.chmod(0o755)
            return _completed(
                call,
                returncode=self.sync_returncode,
                stderr="dependency sync failed" if self.sync_returncode else "",
            )
        if kind == "import-smoke":
            return _completed(
                call,
                returncode=self.smoke_returncode,
                stderr="import failed" if self.smoke_returncode else "",
            )
        if kind == "healthcheck":
            return _completed(
                call,
                returncode=self.healthcheck_returncode,
                stderr="healthcheck failed" if self.healthcheck_returncode else "",
            )
        if kind == "migrate":
            return _completed(
                call,
                returncode=self.migrate_returncode,
                stderr="migration failed" if self.migrate_returncode else "",
            )
        if kind == "systemctl":
            return _completed(
                call,
                returncode=self.systemctl_returncode,
                stderr="systemctl failed" if self.systemctl_returncode else "",
            )
        if kind == "service-active":
            return _completed(
                call,
                returncode=self.service_active_returncode,
                stderr="service is not active"
                if self.service_active_returncode
                else "",
            )
        raise AssertionError(f"unexpected command: {call.argv!r}")


def _completed(
    call: RunCall,
    *,
    returncode: int = 0,
    stdout: str | bytes = "",
    stderr: str | bytes = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=call.argv,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _command_kind(call: RunCall) -> str:
    argv = call.argv
    if "fetch" in argv:
        return "fetch"
    if "rev-parse" in argv:
        return "rev-parse"
    if "verify-commit" in argv:
        return "verify-commit"
    if "cat-file" in argv:
        return "cat-file"
    if "merge-base" in argv:
        return "merge-base"
    if "ls-tree" in argv:
        return "ls-tree"
    if "archive" in argv:
        return "archive"
    if len(argv) >= 2 and argv[1] == "sync":
        return "uv-sync"
    if argv[-2:] == ("-c", "import honeybuy_tg"):
        return "import-smoke"
    if argv[-2:] == ("honeybuy_tg", "migrate"):
        return "migrate"
    if argv[-2:] == ("honeybuy_tg", "healthcheck"):
        return "healthcheck"
    if "systemctl" in Path(argv[0]).name and len(argv) >= 2:
        if argv[1] == "is-active":
            return "service-active"
        return "systemctl"
    return "unknown"


def _safe_tree() -> bytes:
    payloads = {
        "pyproject.toml": b"[project]\nname='honeybuy-tg'\n",
        "uv.lock": b"version = 1\n",
        "src/honeybuy_tg/__init__.py": b"__all__ = []\n",
        **CONTROL_PLANE_ASSETS,
    }
    return b"".join(
        f"{mode} blob {_git_blob_oid(payload)}\t{name}\0".encode()
        for name, payload in sorted(payloads.items())
        for mode in ("100644",)
    )


def _git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def _raw_commit(*signature_kinds: str) -> bytes:
    signature_armor = {
        "ssh": (
            "-----BEGIN SSH SIGNATURE-----",
            "U1NIU0lHAAAAAQAAADMAAAALc3NoLWVkMjU1MTkAAAAgaGFy",
            "-----END SSH SIGNATURE-----",
        ),
        "gpg": (
            "-----BEGIN PGP SIGNATURE-----",
            "iQEzBAABCgAdFiEEtest-only-fixture",
            "-----END PGP SIGNATURE-----",
        ),
        "x509": (
            "-----BEGIN SIGNED MESSAGE-----",
            "MIIBtest-only-x509-fixture",
            "-----END SIGNED MESSAGE-----",
        ),
    }
    headers = [
        f"tree {TREE_SHA}",
        f"parent {LAST_SHA}",
        "author Test Author <test@example.invalid> 1700000000 +0000",
        "committer Test Author <test@example.invalid> 1700000000 +0000",
    ]
    for kind in signature_kinds:
        begin, payload, end = signature_armor[kind]
        headers.extend((f"gpgsig {begin}", f" {payload}", f" {end}"))
    return ("\n".join(headers) + "\n\ntest commit\n").encode("ascii")


def _archive(
    entries: list[tuple[str, bytes, bytes | None]],
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, payload, member_type in entries:
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            if member_type is None:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            else:
                member.type = member_type
                if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = payload.decode()
                archive.addfile(member)
    return output.getvalue()


def _safe_archive() -> bytes:
    payloads = {
        "pyproject.toml": b"[project]\nname='honeybuy-tg'\n",
        "uv.lock": b"version = 1\n",
        "src/honeybuy_tg/__init__.py": b"__all__ = []\n",
        **CONTROL_PLANE_ASSETS,
    }
    return _archive(
        [(name, payload, None) for name, payload in sorted(payloads.items())]
    )


def _workflow_run(
    sha: str,
    *,
    run_id: object = 100,
    run_number: object = 10,
    run_attempt: object = 1,
    status: str = "completed",
    conclusion: str | None = "success",
    branch: str = BRANCH,
    event: str = "push",
    path: str = WORKFLOW_PATH,
    repository: str = REPOSITORY,
) -> dict[str, object]:
    return {
        "id": run_id,
        "run_number": run_number,
        "run_attempt": run_attempt,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "head_branch": branch,
        "event": event,
        "path": path,
        "repository": {"full_name": repository},
        "head_repository": {"full_name": repository},
    }


def _workflow_payload(*runs: dict[str, object]) -> dict[str, object]:
    return {"total_count": len(runs), "workflow_runs": list(runs)}


def _associated_pull(
    *,
    number: object = 17,
    merged_at: object = "2026-09-03T12:00:00Z",
    merge_sha: object = TARGET_SHA,
    base_repository: object = REPOSITORY,
    base_branch: object = BRANCH,
    head_repository: object = REPOSITORY,
    head_sha: object = PR_HEAD_SHA,
) -> dict[str, object]:
    return {
        "number": number,
        "merged_at": merged_at,
        "merge_commit_sha": merge_sha,
        "base": {
            "ref": base_branch,
            "repo": {"full_name": base_repository},
        },
        "head": {
            "sha": head_sha,
            "repo": {"full_name": head_repository},
        },
    }


def _trusted_http(
    workflow_payload: object,
    events: list[str],
) -> RebaseHttp:
    return RebaseHttp(workflow_payload, [_associated_pull()], events)


def _rebase_verification_hook(
    *,
    fetched_head: str = PR_HEAD_SHA,
    signed_head: bool = True,
    matching_tree: bool = True,
    candidate_verify_returncode: int = 1,
) -> Callable[[RunCall, int], subprocess.CompletedProcess | None]:
    pull_fetch_pending = False

    def hook(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        nonlocal pull_fetch_pending
        kind = _command_kind(call)
        if kind == "fetch" and call.argv[-1] == "refs/pull/17/head":
            pull_fetch_pending = True
            return _completed(call)
        if kind == "rev-parse":
            revision = call.argv[-1]
            if revision == "FETCH_HEAD^{commit}" and pull_fetch_pending:
                pull_fetch_pending = False
                return _completed(call, stdout=f"{fetched_head}\n")
            if revision == f"{TARGET_SHA}^{{tree}}":
                return _completed(call, stdout=f"{TREE_SHA}\n")
            if revision == f"{PR_HEAD_SHA}^{{tree}}":
                tree = TREE_SHA if matching_tree else OTHER_TREE_SHA
                return _completed(call, stdout=f"{tree}\n")
        if kind == "verify-commit":
            commit = call.argv[-1]
            if commit == TARGET_SHA:
                return _completed(
                    call,
                    returncode=candidate_verify_returncode,
                    stderr=(
                        "unsigned rebase commit"
                        if candidate_verify_returncode
                        else ""
                    ),
                )
            if commit == PR_HEAD_SHA:
                return _completed(
                    call,
                    returncode=0 if signed_head else 1,
                    stderr="unsigned pull request head" if not signed_head else "",
                )
        return None

    return hook


def _trusted_rebase_verification_hook(
    extra_hook: Callable[[RunCall, int], subprocess.CompletedProcess | None] | None = None,
) -> Callable[[RunCall, int], subprocess.CompletedProcess | None]:
    trusted_hook = _rebase_verification_hook(candidate_verify_returncode=0)
    extra_counts: dict[str, int] = {}

    def hook(
        call: RunCall,
        occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        trusted = trusted_hook(call, occurrence)
        if trusted is not None:
            return trusted
        if extra_hook is None:
            return None
        kind = _command_kind(call)
        extra_occurrence = extra_counts.get(kind, 0) + 1
        extra_counts[kind] = extra_occurrence
        return extra_hook(call, extra_occurrence)

    return hook


def _control_plane_manifest_payload(*, phase: str) -> dict[str, object]:
    return {
        "version": 1,
        "phase": phase,
        "files": {
            path: hashlib.sha256(payload).hexdigest()
            for path, payload in sorted(CONTROL_PLANE_ASSETS.items())
        },
    }


def _write_control_plane_manifest(config, *, phase: str) -> None:
    config.control_plane_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    config.control_plane_manifest_path.write_text(
        json.dumps(
            _control_plane_manifest_payload(phase=phase),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    config.control_plane_manifest_path.chmod(0o600)


def _config(controller_module: ModuleType, tmp_path: Path):
    source_repo = tmp_path / "var/lib/honeybuy-release-controller/repository"
    releases_dir = tmp_path / "opt/honeybuy-tg/releases"
    state_dir = tmp_path / "var/lib/honeybuy-release-controller"
    backup_dir = tmp_path / "var/backups/honeybuy-tg"
    trust_file = tmp_path / "etc/honeybuy-tg/allowed_signers"
    database_path = tmp_path / "var/lib/honeybuy-tg/honeybuy.sqlite3"
    empty_work_dir = tmp_path / "var/empty/honeybuy-healthcheck"
    for directory in (
        source_repo,
        releases_dir,
        state_dir,
        trust_file.parent,
        database_path.parent,
        backup_dir,
        empty_work_dir,
        tmp_path / "var/cache/honeybuy-tg/uv",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    trust_file.write_text("atiurin@proton.me ssh-ed25519 AAAATEST\n")
    trust_file.chmod(0o644)

    config = controller_module.ControllerConfig(
        repository=REPOSITORY,
        branch=BRANCH,
        workflow_path=WORKFLOW_PATH,
        remote_url=REMOTE_URL,
        source_repo=source_repo,
        releases_dir=releases_dir,
        current_link=tmp_path / "opt/honeybuy-tg/current",
        deployed_state_path=state_dir / "deployed-sha",
        database_backup_dir=backup_dir,
        lock_path=tmp_path / "run/lock/honeybuy-release-controller.lock",
        allowed_signers_path=trust_file,
        database_path=database_path,
        uv_cache_dir=tmp_path / "var/cache/honeybuy-tg/uv",
        git_path=Path("/test-tools/git"),
        uv_path=Path("/test-tools/uv"),
        systemctl_path=Path("/test-tools/systemctl"),
        service_name="honeybuy-tg.service",
        build_uid=os.getuid(),
        build_gid=os.getgid(),
        release_uid=os.getuid(),
        release_gid=os.getgid(),
        runtime_uid=os.getuid() + 10,
        runtime_gid=os.getgid() + 10,
        empty_work_dir=empty_work_dir,
        api_timeout_seconds=3.0,
    )
    object.__setattr__(config, "test_mode", True)
    installed_paths = {
        "installed_controller_path": (
            tmp_path / "usr/local/lib/honeybuy/release_controller.py"
        ),
        "app_service_unit_path": tmp_path / "etc/systemd/system/honeybuy-tg.service",
        "controller_service_unit_path": (
            tmp_path / "etc/systemd/system/honeybuy-release-controller.service"
        ),
        "controller_timer_unit_path": (
            tmp_path / "etc/systemd/system/honeybuy-release-controller.timer"
        ),
        "control_plane_manifest_path": state_dir / "control-plane-manifest.json",
    }
    for name, path in installed_paths.items():
        object.__setattr__(config, name, path)
    for candidate_path, field_name in CONTROL_PLANE_CONFIG_PATHS.items():
        installed_path = getattr(config, field_name)
        installed_path.parent.mkdir(parents=True, exist_ok=True)
        installed_path.write_bytes(CONTROL_PLANE_ASSETS[candidate_path])
        installed_path.chmod(0o644)
    _write_control_plane_manifest(config, phase="installed")
    return config


def _activation_config(controller_module: ModuleType, tmp_path: Path):
    config = _config(controller_module, tmp_path)
    additions = {
        "previous_link": tmp_path / "opt/honeybuy-tg/previous",
        "deployment_journal_path": (
            tmp_path / "var/lib/honeybuy-release-controller/deployment-journal.json"
        ),
        "deployment_receipts_dir": (
            tmp_path / "var/lib/honeybuy-release-controller/receipts"
        ),
        "quarantine_dir": (
            tmp_path / "var/lib/honeybuy-release-controller/quarantine"
        ),
        "scratch_dir": tmp_path / "var/lib/honeybuy-release-controller/scratch",
        "stable_health_checks": 3,
        "stable_health_interval_seconds": 0.25,
    }
    for name, value in additions.items():
        object.__setattr__(config, name, value)
    for directory_name in (
        "deployment_receipts_dir",
        "quarantine_dir",
        "scratch_dir",
    ):
        getattr(config, directory_name).mkdir(parents=True, exist_ok=True)
    return config


def _bootstrap_config(controller_module: ModuleType, tmp_path: Path):
    config = _activation_config(controller_module, tmp_path)
    object.__setattr__(
        config,
        "bootstrap_journal_path",
        config.deployed_state_path.parent / "bootstrap-journal.json",
    )
    _write_control_plane_manifest(config, phase="bootstrap_pending")
    return config


def _write_deployed_state(
    config,
    sha: str = LAST_SHA,
    *,
    install_control_plane: bool = True,
) -> None:
    config.deployed_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.deployed_state_path.write_text(f"{sha}\n")
    config.deployed_state_path.chmod(0o644)
    if install_control_plane and hasattr(config, "control_plane_manifest_path"):
        _write_control_plane_manifest(config, phase="installed")


def _create_ready_release(config, sha: str, *, make_current: bool = False) -> Path:
    release = config.releases_dir / sha
    release.mkdir(parents=True)
    uv_lock = release / "uv.lock"
    uv_lock.write_bytes(b"version = 1\n")
    python_path = release / ".venv/bin/python"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"test python shim")
    python_path.chmod(0o755)
    for relative_name, payload in CONTROL_PLANE_ASSETS.items():
        asset = release / relative_name
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(payload)
        asset.chmod(0o644)
    digest = hashlib.sha256(uv_lock.read_bytes()).hexdigest()
    (release / ".ready.json").write_text(
        json.dumps({"sha": sha, "uv_lock_sha256": digest}) + "\n"
    )
    for directory in (release, release / ".venv", release / ".venv/bin"):
        directory.chmod(0o755)
    uv_lock.chmod(0o644)
    (release / ".ready.json").chmod(0o644)
    if make_current:
        config.current_link.parent.mkdir(parents=True, exist_ok=True)
        config.current_link.symlink_to(release)
    return release


def _create_wal_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(
        "CREATE TABLE deployment_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("INSERT INTO deployment_probe(value) VALUES ('committed-in-wal')")
    connection.commit()
    assert (path.parent / f"{path.name}-wal").exists()
    return connection


def _database_values(path: Path) -> list[str]:
    with sqlite3.connect(path) as database:
        return [
            row[0]
            for row in database.execute(
                "SELECT value FROM deployment_probe ORDER BY id"
            ).fetchall()
        ]


def _database_has_migration_marker(path: Path) -> bool:
    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'candidate_migration'"
        ).fetchone() is not None


def _apply_candidate_migration(path: Path) -> None:
    with closing(sqlite3.connect(path)) as database:
        with database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS candidate_migration "
                "(marker TEXT NOT NULL)"
            )
            if (
                database.execute(
                    "SELECT COUNT(*) FROM candidate_migration"
                ).fetchone()[0]
                == 0
            ):
                database.execute("INSERT INTO candidate_migration VALUES ('migrated')")
            database.execute("PRAGMA user_version = 3")


def _lock_is_held(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as candidate:
        try:
            fcntl.flock(candidate.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(candidate.fileno(), fcntl.LOCK_UN)
        return False


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_recovery_backup(config) -> tuple[Path, dict[str, Any]]:
    backup = config.database_backup_dir / f"{config.database_path.name}.{TARGET_SHA}.bak"
    with (
        closing(sqlite3.connect(config.database_path)) as source,
        closing(sqlite3.connect(backup)) as target,
    ):
        source.backup(target)
    backup.chmod(0o600)
    metadata = backup.stat()
    record = {
        "name": backup.name,
        "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        "schema_version": 2,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }
    return backup, record


def _write_journal(
    config,
    *,
    phase: str,
    backup: dict[str, Any] | None,
) -> None:
    phases = list(ACTIVATION_PHASES[: ACTIVATION_PHASES.index(phase) + 1])
    payload = {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "previous_sha": LAST_SHA,
        "phase": phase,
        "phases": phases,
        "backup": backup,
    }
    config.deployment_journal_path.parent.mkdir(parents=True, exist_ok=True)
    config.deployment_journal_path.write_text(json.dumps(payload) + "\n")
    config.deployment_journal_path.chmod(0o600)


def _activation_receipt_payload(journal: dict[str, Any]) -> dict[str, Any]:
    assert journal["phase"] == "healthy"
    assert journal["phases"] == list(ACTIVATION_PHASES)
    return {
        "version": 1,
        "candidate_sha": journal["candidate_sha"],
        "previous_sha": journal["previous_sha"],
        "phase": "healthy",
        "phases": list(journal["phases"]),
        "backup": journal.get("backup"),
    }


def _write_bootstrap_journal(config, *, phase: str = "awaiting_service") -> None:
    phases = list(BOOTSTRAP_PHASES[: BOOTSTRAP_PHASES.index(phase) + 1])
    payload = {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "phase": phase,
        "phases": phases,
    }
    config.bootstrap_journal_path.write_text(json.dumps(payload) + "\n")
    config.bootstrap_journal_path.chmod(0o600)


def _create_bootstrap_database(
    path: Path,
    *,
    schema_version: int = CURRENT_SCHEMA_VERSION,
) -> None:
    migration = migrate_database_path(path)
    assert migration.new_version == CURRENT_SCHEMA_VERSION
    with closing(sqlite3.connect(path)) as database:
        with database:
            database.execute(
                "CREATE TABLE bootstrap_probe "
                "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
            )
            database.execute(
                "INSERT INTO bootstrap_probe(value) VALUES ('legacy-data')"
            )
            if schema_version != CURRENT_SCHEMA_VERSION:
                database.execute(f"PRAGMA user_version = {schema_version}")
    path.chmod(0o600)


def _bootstrap_database_snapshot(path: Path) -> tuple[tuple[int, int], bytes, list[str]]:
    metadata = path.stat()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as database:
        values = [
            row[0]
            for row in database.execute(
                "SELECT value FROM bootstrap_probe ORDER BY id"
            ).fetchall()
        ]
    return (metadata.st_dev, metadata.st_ino), path.read_bytes(), values


class BootstrapProbe:
    def __init__(
        self,
        config,
        *,
        load_state: str = "loaded",
        active_state: str = "inactive",
        sub_state: str | None = None,
        healthy: bool = True,
    ) -> None:
        self.config = config
        self.load_state = load_state
        self.active_state = active_state
        self.sub_state = sub_state
        self.healthy = healthy
        self.service_state_checks = 0
        self.service_active_checks = 0
        self.healthchecks = 0
        self.health_journal_phases: list[str | None] = []

    def __call__(
        self,
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        kind = _command_kind(call)
        if kind == "systemctl" and len(call.argv) > 1 and call.argv[1] == "show":
            assert call.argv == (
                str(self.config.systemctl_path),
                "show",
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                self.config.service_name,
            )
            self.service_state_checks += 1
            sub_state = self.sub_state
            if sub_state is None:
                sub_state = "running" if self.active_state == "active" else "dead"
            return _completed(
                call,
                stdout=(
                    f"LoadState={self.load_state}\n"
                    f"ActiveState={self.active_state}\n"
                    f"SubState={sub_state}\n"
                ),
            )
        if kind == "service-active":
            self.service_active_checks += 1
            return _completed(
                call,
                returncode=0 if self.active_state == "active" else 3,
                stdout=f"{self.active_state}\n",
            )
        if kind == "systemctl":
            raise AssertionError("bootstrap must never start or stop the bot service")
        if kind == "migrate":
            raise AssertionError("bootstrap must never migrate the live database")
        if kind != "healthcheck":
            return None

        self.healthchecks += 1
        assert Path(call.argv[0]) == (
            self.config.releases_dir / TARGET_SHA / ".venv/bin/python"
        )
        assert call.cwd == self.config.empty_work_dir
        assert call.uid == self.config.runtime_uid
        assert call.gid == self.config.runtime_gid
        assert call.env == {"DATABASE_PATH": str(self.config.database_path)}
        phase = None
        if self.config.bootstrap_journal_path.is_file():
            phase = _read_json(self.config.bootstrap_journal_path).get("phase")
        self.health_journal_phases.append(phase)
        return _completed(
            call,
            returncode=0 if self.healthy else 1,
            stderr="bootstrap healthcheck failed" if not self.healthy else "",
        )


class ActivationProbe:
    def __init__(
        self,
        config,
        *,
        fail_stage: str | None = None,
        previous_compatible: bool = True,
        service_active_states: list[str] | None = None,
        previous_live_health_states: list[bool] | None = None,
    ) -> None:
        self.config = config
        self.fail_stage = fail_stage
        self.previous_compatible = previous_compatible
        self.service_active_states = list(service_active_states or [])
        self.previous_live_health_states = list(previous_live_health_states or [])
        self.service_running = True
        self.calls: list[tuple[str, Path | None, bool]] = []
        self.journal_phases_at_boundaries: list[tuple[str, str]] = []
        self.lock_held_at_service_calls: list[bool] = []
        self.post_start_candidate_healthchecks = 0
        self.live_digest_after_migration: str | None = None
        self.service_active_checks = 0
        self.previous_live_healthchecks = 0

    def __call__(
        self,
        call: RunCall,
        occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        kind = _command_kind(call)
        if kind == "service-active":
            assert call.argv == (
                str(self.config.systemctl_path),
                "is-active",
                "--quiet",
                self.config.service_name,
            )
            assert call.cwd is None
            assert call.uid is None
            assert call.gid is None
            assert call.env is None
            self.service_active_checks += 1
            state = (
                self.service_active_states.pop(0)
                if self.service_active_states
                else ("active" if self.service_running else "inactive")
            )
            return _completed(
                call,
                returncode=0 if state == "active" else 3,
                stdout=f"{state}\n",
            )

        if kind not in {"migrate", "healthcheck", "systemctl"}:
            return None

        database_path = (
            Path(call.env["DATABASE_PATH"])
            if call.env is not None and "DATABASE_PATH" in call.env
            else None
        )
        self.calls.append((kind, database_path, self.service_running))

        if kind == "systemctl":
            action = call.argv[-2]
            self.lock_held_at_service_calls.append(_lock_is_held(self.config.lock_path))
            self._record_journal_phase(f"systemctl:{action}")
            if action == "stop":
                if occurrence == 1:
                    phase = None
                    if self.config.deployment_journal_path.exists():
                        phase = _read_json(self.config.deployment_journal_path).get("phase")
                    if phase in {None, "prepared"}:
                        assert list(self.config.database_backup_dir.glob("*.bak")) == []
                if self.fail_stage == "stop" and occurrence == 1:
                    return _completed(call, returncode=1, stderr="stop failed")
                self.service_running = False
                return _completed(call)
            if action == "start":
                if self.fail_stage == "start" and occurrence == 2:
                    return _completed(call, returncode=1, stderr="start failed")
                self.service_running = True
                return _completed(call)

        assert database_path is not None
        if kind == "migrate":
            assert call.cwd == self.config.empty_work_dir
            assert call.uid == self.config.runtime_uid
            assert call.gid == self.config.runtime_gid
            assert call.env == {
                **DUMMY_MIGRATION_ENV,
                "DATABASE_PATH": str(database_path),
            }
            is_live = database_path == self.config.database_path
            if is_live:
                assert not self.service_running
                self._record_journal_phase("migrate:live")
                if self.fail_stage == "live-migrate":
                    _apply_candidate_migration(database_path)
                    return _completed(call, returncode=1, stderr="migration failed")
            else:
                assert self.service_running
                if self.fail_stage == "dry-migrate":
                    return _completed(call, returncode=1, stderr="dry migration failed")
            _apply_candidate_migration(database_path)
            if is_live:
                self.live_digest_after_migration = hashlib.sha256(
                    self.config.database_path.read_bytes()
                ).hexdigest()
            return _completed(call)

        assert kind == "healthcheck"
        assert call.cwd == self.config.empty_work_dir
        assert call.uid == self.config.runtime_uid
        assert call.gid == self.config.runtime_gid
        assert call.env == {"DATABASE_PATH": str(database_path)}
        executable = Path(call.argv[0])
        candidate = self.config.releases_dir / TARGET_SHA
        previous = self.config.releases_dir / LAST_SHA
        if executable == candidate / ".venv/bin/python":
            assert _database_has_migration_marker(database_path)
            if database_path != self.config.database_path:
                if self.fail_stage == "dry-health":
                    return _completed(call, returncode=1, stderr="dry health failed")
                return _completed(call)
            if not self.service_running:
                self._record_journal_phase("health:pre-switch")
                assert self.config.current_link.resolve() == (
                    self.config.releases_dir / LAST_SHA
                )
                return _completed(call)
            self.post_start_candidate_healthchecks += 1
            self._record_journal_phase("health:post-start")
            assert self.config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
            if self.fail_stage == "post-health":
                with sqlite3.connect(database_path) as database:
                    database.execute(
                        "INSERT INTO deployment_probe(value) "
                        "VALUES ('accepted-after-start')"
                    )
                return _completed(call, returncode=1, stderr="candidate unhealthy")
            return _completed(call)
        if executable == previous / ".venv/bin/python":
            if database_path == self.config.database_path:
                assert self.service_running
                self.previous_live_healthchecks += 1
                healthy = (
                    self.previous_live_health_states.pop(0)
                    if self.previous_live_health_states
                    else True
                )
                self._record_journal_phase("health:previous-live")
                return _completed(
                    call,
                    returncode=0 if healthy else 1,
                    stderr="previous release unhealthy" if not healthy else "",
                )
            assert _database_has_migration_marker(database_path)
            return _completed(
                call,
                returncode=0 if self.previous_compatible else 1,
                stderr="previous release incompatible"
                if not self.previous_compatible
                else "",
            )
        raise AssertionError(f"unexpected healthcheck executable: {executable}")

    def _record_journal_phase(self, boundary: str) -> None:
        if self.config.deployment_journal_path.exists():
            journal = _read_json(self.config.deployment_journal_path)
            self.journal_phases_at_boundaries.append((boundary, journal["phase"]))


def _scenario(config, *, heads: list[str] | None = None, **overrides) -> RunnerScenario:
    values: dict[str, Any] = {
        "git_path": config.git_path,
        "uv_path": config.uv_path,
        "remote_heads": heads or [TARGET_SHA, TARGET_SHA],
        "tree_output": _safe_tree(),
        "archive_output": _safe_archive(),
        "raw_commits": {
            TARGET_SHA: _raw_commit("ssh"),
            PR_HEAD_SHA: _raw_commit("ssh"),
        },
    }
    values.update(overrides)
    return RunnerScenario(**values)


def _trusted_scenario(
    config,
    *,
    heads: list[str] | None = None,
    **overrides,
) -> RunnerScenario:
    forbidden_overrides = {"raw_commits", "verify_returncode"}.intersection(overrides)
    assert not forbidden_overrides, (
        "trusted scenarios cannot override authentication evidence: "
        f"{sorted(forbidden_overrides)}"
    )
    extra_hook = overrides.pop("call_hook", None)
    return _scenario(
        config,
        heads=heads,
        call_hook=_trusted_rebase_verification_hook(extra_hook),
        **overrides,
    )


def _controller(
    controller_module: ModuleType,
    config,
    runner: FakeRunner,
    http: FakeHttp,
    *,
    sleeper=None,
):
    kwargs = {
        "config": config,
        "runner": runner,
        "http": http,
    }
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    return controller_module.ReleaseController(
        **kwargs,
    )


def _status(result) -> str:
    assert not isinstance(result, (str, int))
    assert hasattr(result, "status")
    assert hasattr(result, "reason")
    assert isinstance(result.reason, str) and result.reason
    raw_status = result.status
    if hasattr(raw_status, "value"):
        raw_status = raw_status.value
    return str(raw_status).casefold()


def _assert_status(result, expected: str) -> None:
    assert _status(result) == expected


def _assert_ordered_subsequence(actual: list[str], required: tuple[str, ...]) -> None:
    positions = [actual.index(phase) for phase in required]
    assert positions == sorted(positions)


def _assert_workflow_request(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.github.com"
    assert parsed.path == (
        "/repos/noxiouz/honeybuy_tg/actions/workflows/tests.yml/runs"
    )
    query = urllib.parse.parse_qs(parsed.query)
    assert query["branch"] == [BRANCH]
    assert query["event"] == ["push"]
    assert query["head_sha"] == [TARGET_SHA]
    assert int(query["per_page"][0]) >= 2


def _preparation_calls(runner: FakeRunner) -> list[RunCall]:
    return [
        call
        for call in runner.calls
        if _command_kind(call)
        in {"ls-tree", "archive", "uv-sync", "import-smoke", "systemctl"}
    ]


def _git_config_overrides(call: RunCall) -> set[str]:
    overrides: set[str] = set()
    for index, argument in enumerate(call.argv[:-1]):
        if argument == "-c":
            overrides.add(call.argv[index + 1])
    return overrides


def _assert_git_call_hardened(call: RunCall, config) -> None:
    assert call.argv[0] == str(config.git_path)
    repository_indexes = [
        index for index, argument in enumerate(call.argv[:-1]) if argument == "-C"
    ]
    assert len(repository_indexes) == 1
    assert call.argv[repository_indexes[0] + 1] == str(config.source_repo)
    assert call.env is not None
    required_environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_SSH_COMMAND": "/bin/false",
    }
    assert required_environment.items() <= call.env.items()
    required_overrides = {
        "core.hooksPath=/dev/null",
        "credential.helper=",
        "protocol.allow=never",
        "protocol.https.allow=always",
    }
    assert required_overrides <= _git_config_overrides(call)


def _assert_all_git_calls_hardened(runner: FakeRunner, config) -> None:
    git_calls = [call for call in runner.calls if call.argv[0] == str(config.git_path)]
    assert git_calls
    for call in git_calls:
        _assert_git_call_hardened(call, config)


def _sqlite_argument_path(value: object) -> Path | None:
    try:
        raw = os.fspath(value)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    if raw.startswith("file:"):
        parsed = urllib.parse.urlsplit(raw)
        return Path(urllib.parse.unquote(parsed.path))
    return Path(raw)


@pytest.mark.parametrize(
    "remote_head",
    [
        pytest.param("A" * 40, id="uppercase"),
        pytest.param("a" * 39, id="short"),
        pytest.param(f"{'a' * 40}\n../escape", id="multiline"),
        pytest.param(f"../{'a' * 40}", id="path-like"),
    ],
)
def test_rejects_noncanonical_remote_sha_before_path_or_command_use(
    controller_module,
    tmp_path,
    remote_head,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config, heads=[remote_head]), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert [_command_kind(call) for call in runner.calls] == ["fetch", "rev-parse"]
    assert http.calls == []
    assert list(config.releases_dir.iterdir()) == []
    assert all(remote_head not in argument for call in runner.calls for argument in call.argv)


def test_held_flock_returns_busy_before_network_or_commands(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    with config.lock_path.open("a+") as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "busy")
    assert runner.calls == []
    assert http.calls == []
    assert events == []


def test_fetches_fixed_https_main_and_accepts_only_matching_green_workflow(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert runner.calls[0].argv[-6:] == (
        "fetch",
        "--quiet",
        "--force",
        "--no-tags",
        REMOTE_URL,
        "refs/heads/main",
    )
    assert runner.calls[1].argv[-3:] == (
        "rev-parse",
        "--verify",
        "FETCH_HEAD^{commit}",
    )
    _assert_all_git_calls_hardened(runner, config)
    assert len(http.calls) == 2
    requested_url, timeout = http.calls[0]
    _assert_workflow_request(requested_url)
    assert timeout == config.api_timeout_seconds
    assert http.calls[1][0].endswith(f"/commits/{TARGET_SHA}/pulls")
    assert events[:3] == ["run:fetch", "run:rev-parse", "http:workflow"]


def test_workflow_query_is_scoped_to_exact_candidate_sha(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    runner = FakeRunner(_trusted_scenario(config), [])
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), [])

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert len(http.calls) == 2
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(http.calls[0][0]).query)
    assert query["head_sha"] == [TARGET_SHA]


@pytest.mark.parametrize(
    ("ordering_case", "stale_conclusion", "newest_conclusion", "expected_status"),
    [
        pytest.param(
            ordering_case,
            stale_conclusion,
            newest_conclusion,
            expected_status,
            id=f"{ordering_case}-{case_id}",
        )
        for ordering_case in ("run-number", "run-attempt", "id-tiebreak")
        for stale_conclusion, newest_conclusion, expected_status, case_id in (
            ("success", "failure", "rejected", "newest-failed"),
            ("failure", "success", "prepared", "newest-green"),
        )
    ],
)
def test_workflow_rerun_selection_uses_validated_ordering_not_api_order(
    controller_module,
    tmp_path,
    ordering_case,
    stale_conclusion,
    newest_conclusion,
    expected_status,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    runner = FakeRunner(_trusted_scenario(config), [])
    stale_order = {
        "run-number": (999, 26, 99),
        "run-attempt": (400, 27, 1),
        "id-tiebreak": (400, 27, 1),
    }[ordering_case]
    newest_order = {
        "run-number": (400, 27, 1),
        # GitHub reruns retain the workflow-run id and advance run_attempt.
        "run-attempt": (400, 27, 2),
        "id-tiebreak": (401, 27, 1),
    }[ordering_case]
    stale = _workflow_run(
        TARGET_SHA,
        run_id=stale_order[0],
        run_number=stale_order[1],
        run_attempt=stale_order[2],
        conclusion=stale_conclusion,
    )
    newest = _workflow_run(
        TARGET_SHA,
        run_id=newest_order[0],
        run_number=newest_order[1],
        run_attempt=newest_order[2],
        conclusion=newest_conclusion,
    )
    # Deliberately put stale evidence first: response array order is not trust
    # evidence and may change across GitHub pagination/reruns.
    http = _trusted_http(_workflow_payload(stale, newest), [])

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, expected_status)
    if expected_status == "rejected":
        assert _preparation_calls(runner) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("id", True, id="boolean-id"),
        pytest.param("id", 0, id="zero-id"),
        pytest.param("run_number", "27", id="string-run-number"),
        pytest.param("run_number", 0, id="zero-run-number"),
        pytest.param("run_attempt", False, id="boolean-run-attempt"),
        pytest.param("run_attempt", 0, id="zero-run-attempt"),
    ],
)
def test_workflow_rerun_ordering_fields_must_be_positive_integers(
    controller_module,
    tmp_path,
    field,
    value,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    runner = FakeRunner(_scenario(config), [])
    run = _workflow_run(TARGET_SHA)
    run[field] = value
    http = FakeHttp(_workflow_payload(run), [])

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert _preparation_calls(runner) == []


@pytest.mark.parametrize("field", ["id", "run_number", "run_attempt"])
def test_malformed_newer_workflow_evidence_is_not_silently_skipped(
    controller_module,
    tmp_path,
    field,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    runner = FakeRunner(_scenario(config), [])
    older_green = _workflow_run(
        TARGET_SHA,
        run_id=400,
        run_number=26,
        run_attempt=1,
    )
    malformed_newer = _workflow_run(
        TARGET_SHA,
        run_id=401,
        run_number=27,
        run_attempt=1,
        conclusion="failure",
    )
    malformed_newer[field] = "invalid"
    http = FakeHttp(_workflow_payload(older_green, malformed_newer), [])

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert _preparation_calls(runner) == []


@pytest.mark.parametrize(
    ("status", "conclusion", "expected_status"),
    [
        pytest.param("in_progress", None, "transient", id="current-pending"),
        pytest.param("completed", "failure", "rejected", id="current-failed"),
    ],
)
def test_does_not_select_older_green_run_when_current_head_is_not_green(
    controller_module,
    tmp_path,
    status,
    conclusion,
    expected_status,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    current = _workflow_run(TARGET_SHA, status=status, conclusion=conclusion)
    older_green = _workflow_run(LAST_SHA)
    http = FakeHttp(_workflow_payload(current, older_green), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, expected_status)
    assert _preparation_calls(runner) == []


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        pytest.param("head_sha", OTHER_SHA, id="sha"),
        pytest.param("head_branch", "develop", id="branch"),
        pytest.param("event", "workflow_dispatch", id="event"),
        pytest.param("path", ".github/workflows/other.yml", id="workflow-path"),
        pytest.param("repository", {"full_name": "attacker/fork"}, id="repository"),
        pytest.param(
            "head_repository",
            {"full_name": "attacker/fork"},
            id="head-repository",
        ),
    ],
)
def test_rejects_workflow_run_with_wrong_identity(
    controller_module,
    tmp_path,
    field,
    wrong_value,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    run = _workflow_run(TARGET_SHA)
    run[field] = wrong_value
    http = FakeHttp(_workflow_payload(run), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert _preparation_calls(runner) == []


def test_absent_deployed_state_fails_closed_without_network(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


def test_last_deployed_sha_must_be_ancestor(controller_module, tmp_path):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, ancestor_returncode=1),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    ancestor_call = next(
        call for call in runner.calls if _command_kind(call) == "merge-base"
    )
    assert ancestor_call.argv[-2:] == (LAST_SHA, TARGET_SHA)
    assert _preparation_calls(runner) == []


def test_same_healthy_sha_is_noop_without_preparation_or_service_mutation(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config, TARGET_SHA)
    release = _create_ready_release(config, TARGET_SHA, make_current=True)
    config.database_path.write_bytes(b"database sentinel")
    database_before = config.database_path.read_bytes()
    current_before = config.current_link.resolve()
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "noop")
    assert _preparation_calls(runner) == []
    healthcheck = next(
        call for call in runner.calls if _command_kind(call) == "healthcheck"
    )
    assert healthcheck.argv == (
        str(release / ".venv/bin/python"),
        "-m",
        "honeybuy_tg",
        "healthcheck",
    )
    assert healthcheck.cwd == config.empty_work_dir
    assert healthcheck.uid == config.runtime_uid
    assert healthcheck.gid == config.runtime_gid
    assert healthcheck.env == {"DATABASE_PATH": str(config.database_path)}
    service_checks = [
        call for call in runner.calls if _command_kind(call) == "service-active"
    ]
    assert len(service_checks) == 1
    assert service_checks[0].argv == (
        str(config.systemctl_path),
        "is-active",
        "--quiet",
        config.service_name,
    )
    assert list(config.empty_work_dir.iterdir()) == []
    assert release not in config.empty_work_dir.parents
    assert config.current_link.resolve() == current_before
    assert config.database_path.read_bytes() == database_before


@pytest.mark.parametrize("service_state", ["inactive", "failed", "restarting"])
def test_noop_rejects_non_active_systemd_service(
    controller_module,
    tmp_path,
    service_state,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config, TARGET_SHA)
    _create_ready_release(config, TARGET_SHA, make_current=True)
    config.database_path.write_bytes(b"database sentinel")
    before_database = config.database_path.read_bytes()
    events: list[str] = []

    def report_service_state(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if _command_kind(call) != "service-active":
            return None
        assert call.argv == (
            str(config.systemctl_path),
            "is-active",
            "--quiet",
            config.service_name,
        )
        return _completed(call, returncode=3, stdout=f"{service_state}\n")

    runner = FakeRunner(
        _scenario(config, call_hook=report_service_state),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert any(_command_kind(call) == "service-active" for call in runner.calls)
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert config.database_path.read_bytes() == before_database


def test_commit_verification_uses_only_external_allowed_signers_file(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    verify_calls = [
        call for call in runner.calls if _command_kind(call) == "verify-commit"
    ]
    assert [call.argv[-1] for call in verify_calls] == [TARGET_SHA, PR_HEAD_SHA]
    for verify_call in verify_calls:
        assert verify_call.argv[0] == str(config.git_path)
        assert f"gpg.ssh.allowedSignersFile={config.allowed_signers_path}" in (
            _git_config_overrides(verify_call)
        )
    assert config.releases_dir not in config.allowed_signers_path.parents
    assert config.source_repo not in config.allowed_signers_path.parents
    assert len(http.calls) == 2
    assert "/commits/" not in http.calls[0][0]
    assert http.calls[1][0].endswith(f"/commits/{TARGET_SHA}/pulls")


def test_direct_commit_verification_first_requires_one_raw_ssh_signature(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    raw_calls = [
        call
        for call in runner.calls
        if _command_kind(call) == "cat-file" and call.argv[-1] == TARGET_SHA
    ]
    assert len(raw_calls) == 1
    raw_call = raw_calls[0]
    assert raw_call.argv[-3:] == ("cat-file", "commit", TARGET_SHA)
    _assert_git_call_hardened(raw_call, config)
    verify_index = next(
        index
        for index, call in enumerate(runner.calls)
        if _command_kind(call) == "verify-commit" and call.argv[-1] == TARGET_SHA
    )
    assert runner.calls.index(raw_call) < verify_index
    pr_raw_call = next(
        call
        for call in runner.calls
        if _command_kind(call) == "cat-file" and call.argv[-1] == PR_HEAD_SHA
    )
    pr_verify_index = next(
        index
        for index, call in enumerate(runner.calls)
        if _command_kind(call) == "verify-commit" and call.argv[-1] == PR_HEAD_SHA
    )
    assert verify_index < runner.calls.index(pr_raw_call) < pr_verify_index
    first_verify_event = events.index("run:verify-commit")
    associated_event = events.index("http:associated-pulls")
    last_verify_event = len(events) - 1 - events[::-1].index("run:verify-commit")
    assert first_verify_event < associated_event < last_verify_event
    assert len(http.calls) == 2


def test_directly_signed_main_commit_without_matching_merged_pr_is_rejected(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [],
        events,
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    verify_calls = [
        call
        for call in runner.calls
        if _command_kind(call) == "verify-commit" and call.argv[-1] == TARGET_SHA
    ]
    assert len(verify_calls) == 1
    assert len(http.calls) == 2
    _assert_workflow_request(http.calls[0][0])
    assert http.calls[1] == (
        f"https://api.github.com/repos/{REPOSITORY}/commits/{TARGET_SHA}/pulls",
        config.api_timeout_seconds,
    )
    assert _preparation_calls(runner) == []


@pytest.mark.parametrize(
    ("signature_kinds", "case_id"),
    [
        pytest.param((), "unsigned", id="unsigned"),
        pytest.param(("gpg",), "gpg", id="gpg"),
        pytest.param(("x509",), "x509", id="x509"),
        pytest.param(("ssh", "ssh"), "multiple-ssh", id="multiple-ssh"),
    ],
)
def test_non_single_ssh_candidate_signature_uses_strict_rebase_fallback(
    controller_module,
    tmp_path,
    signature_kinds,
    case_id,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(
        _scenario(
            config,
            raw_commits={
                TARGET_SHA: _raw_commit(*signature_kinds),
                PR_HEAD_SHA: _raw_commit("ssh"),
            },
            call_hook=_rebase_verification_hook(candidate_verify_returncode=0),
        ),
        events,
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [_associated_pull()],
        events,
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert case_id
    assert len(http.calls) == 2
    assert http.calls[1][0].endswith(f"/commits/{TARGET_SHA}/pulls")
    inspected_commits = [
        call.argv[-1]
        for call in runner.calls
        if _command_kind(call) == "cat-file"
    ]
    assert inspected_commits == [TARGET_SHA, PR_HEAD_SHA]


def test_failed_pull_request_head_signature_is_rejected_before_preparation(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(
        _scenario(
            config,
            call_hook=_rebase_verification_hook(
                signed_head=False,
                candidate_verify_returncode=1,
            ),
        ),
        events,
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [_associated_pull()],
        events,
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    verified_commits = [
        call.argv[-1]
        for call in runner.calls
        if _command_kind(call) == "verify-commit"
    ]
    assert verified_commits == [TARGET_SHA, PR_HEAD_SHA]
    assert len(http.calls) == 2
    assert _preparation_calls(runner) == []


def test_unsigned_rebase_candidate_accepts_only_signed_same_tree_pr_head(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(
        _scenario(
            config,
            raw_commits={
                TARGET_SHA: _raw_commit(),
                PR_HEAD_SHA: _raw_commit("ssh"),
            },
            call_hook=_rebase_verification_hook(),
        ),
        events,
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [_associated_pull()],
        events,
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert [
        call.argv[-1]
        for call in runner.calls
        if _command_kind(call) == "verify-commit"
    ] == [PR_HEAD_SHA]
    assert len(http.calls) == 2
    _assert_workflow_request(http.calls[0][0])
    assert http.calls[1] == (
        f"https://api.github.com/repos/{REPOSITORY}/commits/{TARGET_SHA}/pulls",
        config.api_timeout_seconds,
    )
    pull_fetch = next(
        call
        for call in runner.calls
        if _command_kind(call) == "fetch" and call.argv[-1] == "refs/pull/17/head"
    )
    assert pull_fetch.argv[-6:] == (
        "fetch",
        "--quiet",
        "--force",
        "--no-tags",
        REMOTE_URL,
        "refs/pull/17/head",
    )
    _assert_git_call_hardened(pull_fetch, config)
    head_verification = next(
        call
        for call in runner.calls
        if _command_kind(call) == "verify-commit" and call.argv[-1] == PR_HEAD_SHA
    )
    assert head_verification.argv[-2:] == (
        "verify-commit",
        PR_HEAD_SHA,
    )
    _assert_git_call_hardened(head_verification, config)
    assert f"gpg.ssh.allowedSignersFile={config.allowed_signers_path}" in (
        _git_config_overrides(head_verification)
    )
    assert "gpg.ssh.program=/usr/bin/ssh-keygen" in _git_config_overrides(
        head_verification
    )
    _assert_all_git_calls_hardened(runner, config)
    tree_revisions = [
        call.argv[-1]
        for call in runner.calls
        if _command_kind(call) == "rev-parse" and call.argv[-1].endswith("^{tree}")
    ]
    assert tree_revisions == [f"{TARGET_SHA}^{{tree}}", f"{PR_HEAD_SHA}^{{tree}}"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("base_repository", "attacker/fork", id="base-repository"),
        pytest.param("base_branch", "develop", id="base-branch"),
        pytest.param("merge_sha", OTHER_SHA, id="merge-sha"),
        pytest.param("head_repository", "attacker/fork", id="head-repository"),
        pytest.param("merged_at", None, id="unmerged"),
    ],
)
def test_rebase_fallback_ignores_unrelated_association_before_ambiguity_check(
    controller_module,
    tmp_path,
    field,
    value,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    unrelated = _associated_pull(number=18, **{field: value})
    matching = _associated_pull(number=17)
    events: list[str] = []
    runner = FakeRunner(
        _scenario(config, call_hook=_rebase_verification_hook()),
        events,
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [unrelated, matching],
        events,
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    pull_fetches = [
        call.argv[-1]
        for call in runner.calls
        if _command_kind(call) == "fetch"
        and call.argv[-1].startswith("refs/pull/")
    ]
    assert pull_fetches == ["refs/pull/17/head"]


def test_rebase_fallback_rejects_two_strictly_matching_associations(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    runner = FakeRunner(
        _scenario(config, call_hook=_rebase_verification_hook()),
        [],
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [_associated_pull(number=17), _associated_pull(number=18)],
        [],
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert not any(
        _command_kind(call) == "fetch"
        and call.argv[-1].startswith("refs/pull/")
        for call in runner.calls
    )


@pytest.mark.parametrize(
    "invalid_evidence",
    [
        pytest.param("no-pr", id="no-associated-pr"),
        pytest.param("multiple-prs", id="ambiguous-prs"),
        pytest.param("unmerged", id="unmerged"),
        pytest.param("base-repository", id="wrong-base-repository"),
        pytest.param("base-branch", id="wrong-base-branch"),
        pytest.param("merge-sha", id="wrong-merge-sha"),
        pytest.param("head-repository", id="fork-head"),
        pytest.param("number-string", id="nonnumeric-pr-number"),
        pytest.param("number-zero", id="invalid-pr-number"),
        pytest.param("head-uppercase", id="uppercase-head-sha"),
        pytest.param("head-short", id="short-head-sha"),
        pytest.param("fetched-head", id="fetched-head-mismatch"),
        pytest.param("unsigned-head", id="unsigned-head"),
        pytest.param("tree", id="tree-mismatch"),
    ],
)
def test_rebase_signature_fallback_rejects_ambiguous_or_untrusted_evidence(
    controller_module,
    tmp_path,
    invalid_evidence,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    pull = _associated_pull()
    pull_payload: object = [pull]
    fetched_head = PR_HEAD_SHA
    signed_head = True
    matching_tree = True
    if invalid_evidence == "no-pr":
        pull_payload = []
    elif invalid_evidence == "multiple-prs":
        pull_payload = [pull, _associated_pull(number=18)]
    elif invalid_evidence == "unmerged":
        pull["merged_at"] = None
    elif invalid_evidence == "base-repository":
        pull["base"]["repo"]["full_name"] = "attacker/fork"
    elif invalid_evidence == "base-branch":
        pull["base"]["ref"] = "develop"
    elif invalid_evidence == "merge-sha":
        pull["merge_commit_sha"] = OTHER_SHA
    elif invalid_evidence == "head-repository":
        pull["head"]["repo"]["full_name"] = "attacker/fork"
    elif invalid_evidence == "number-string":
        pull["number"] = "17"
    elif invalid_evidence == "number-zero":
        pull["number"] = 0
    elif invalid_evidence == "head-uppercase":
        pull["head"]["sha"] = PR_HEAD_SHA.upper()
    elif invalid_evidence == "head-short":
        pull["head"]["sha"] = PR_HEAD_SHA[:-1]
    elif invalid_evidence == "fetched-head":
        fetched_head = OTHER_SHA
    elif invalid_evidence == "unsigned-head":
        signed_head = False
    elif invalid_evidence == "tree":
        matching_tree = False
    events: list[str] = []
    runner = FakeRunner(
        _scenario(
            config,
            raw_commits={
                TARGET_SHA: _raw_commit(),
                PR_HEAD_SHA: _raw_commit("ssh"),
            },
            call_hook=_rebase_verification_hook(
                fetched_head=fetched_head,
                signed_head=signed_head,
                matching_tree=matching_tree,
            ),
        ),
        events,
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        pull_payload,
        events,
    )

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert len(http.calls) == 2
    assert http.calls[1][0] == (
        f"https://api.github.com/repos/{REPOSITORY}/commits/{TARGET_SHA}/pulls"
    )
    assert not any(
        _command_kind(call) in {"ls-tree", "archive", "uv-sync", "import-smoke"}
        for call in runner.calls
    )


@pytest.mark.parametrize(
    "tree_output",
    [
        pytest.param(
            f"120000 blob {OBJECT_SHA}\ttracked-link\0".encode(),
            id="symlink",
        ),
        pytest.param(
            f"160000 commit {OBJECT_SHA}\tvendor/submodule\0".encode(),
            id="submodule",
        ),
        pytest.param(
            f"100644 blob {OBJECT_SHA}\t../escape\0".encode(),
            id="traversal",
        ),
    ],
)
def test_rejects_unsafe_git_tree_before_archive_or_build(
    controller_module,
    tmp_path,
    tree_output,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, tree_output=tree_output),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    kinds = [_command_kind(call) for call in runner.calls]
    assert "ls-tree" in kinds
    assert "archive" not in kinds
    assert "uv-sync" not in kinds
    assert not (config.releases_dir / TARGET_SHA).exists()


@pytest.mark.parametrize(
    ("name", "payload", "member_type", "escape_kind"),
    [
        pytest.param("../outside", b"owned", None, "parent", id="parent"),
        pytest.param("__ABSOLUTE__", b"owned", None, "absolute", id="absolute"),
        pytest.param("link", b"../outside", tarfile.SYMTYPE, None, id="symlink"),
        pytest.param("hardlink", b"uv.lock", tarfile.LNKTYPE, None, id="hardlink"),
        pytest.param("pipe", b"", tarfile.FIFOTYPE, None, id="fifo"),
    ],
)
def test_rejects_unsafe_archive_members_without_escape_or_build(
    controller_module,
    tmp_path,
    name,
    payload,
    member_type,
    escape_kind,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    if name == "__ABSOLUTE__":
        name = str(tmp_path / "absolute-escape")
    archive_output = _archive(
        [
            ("uv.lock", b"version = 1\n", None),
            (name, payload, member_type),
        ]
    )
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, archive_output=archive_output),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    kinds = [_command_kind(call) for call in runner.calls]
    assert "archive" in kinds
    assert "uv-sync" not in kinds
    if escape_kind == "parent":
        assert not (config.releases_dir / "outside").exists()
    elif escape_kind == "absolute":
        assert not (tmp_path / "absolute-escape").exists()
    candidate = config.releases_dir / TARGET_SHA
    member_path = candidate / name if not Path(name).is_absolute() else Path(name)
    assert not member_path.exists()
    assert not member_path.is_symlink()
    assert not (candidate / ".ready.json").exists()


def test_prepares_exact_release_with_locked_unprivileged_build_and_import_smoke(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.database_path.write_bytes(b"database sentinel")
    before_database = config.database_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    release = config.releases_dir / TARGET_SHA
    assert (release / "pyproject.toml").is_file()
    assert (release / "uv.lock").read_bytes() == b"version = 1\n"
    assert (release / "src/honeybuy_tg/__init__.py").is_file()
    manifest = json.loads((release / ".ready.json").read_text())
    assert manifest == {
        "sha": TARGET_SHA,
        "uv_lock_sha256": hashlib.sha256(b"version = 1\n").hexdigest(),
    }
    for path in (release, *release.rglob("*")):
        metadata = path.lstat()
        assert stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        assert metadata.st_uid == config.release_uid
        assert metadata.st_gid == config.release_gid
        assert metadata.st_mode & 0o022 == 0

    sync_call = next(call for call in runner.calls if _command_kind(call) == "uv-sync")
    assert sync_call.argv == (
        str(config.uv_path),
        "sync",
        "--frozen",
        "--no-dev",
        "--no-editable",
    )
    assert sync_call.cwd is not None
    build_directory = sync_call.cwd
    assert (release / "pyproject.toml").is_file()
    assert (release / "uv.lock").read_bytes() == b"version = 1\n"
    assert sync_call.uid == config.build_uid
    assert sync_call.gid == config.build_gid
    assert sync_call.env is not None
    assert sync_call.env["UV_CACHE_DIR"] == str(config.uv_cache_dir)
    assert not {
        "TELEGRAM_BOT_TOKEN",
        "OPENAI_API_KEY",
        "OWNER_USER_ID",
        "OWNER_USERNAME",
    }.intersection(sync_call.env)

    smoke_call = next(
        call for call in runner.calls if _command_kind(call) == "import-smoke"
    )
    assert smoke_call.argv == (
        str(build_directory / ".venv/bin/python"),
        "-c",
        "import honeybuy_tg",
    )
    assert smoke_call.cwd == build_directory
    assert smoke_call.uid == config.build_uid
    assert smoke_call.gid == config.build_gid
    assert all(isinstance(call.argv, tuple) for call in runner.calls)
    assert all(
        isinstance(argument, str)
        for call in runner.calls
        for argument in call.argv
    )
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert config.database_path.read_bytes() == before_database
    assert not config.current_link.exists()


@pytest.mark.parametrize(
    ("sync_returncode", "smoke_returncode"),
    [
        pytest.param(1, 0, id="dependency-sync"),
        pytest.param(0, 1, id="import-smoke"),
    ],
)
def test_prepare_failure_leaves_current_database_and_service_untouched(
    controller_module,
    tmp_path,
    sync_returncode,
    smoke_returncode,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    config.database_path.write_bytes(b"database sentinel")
    database_before = config.database_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(
            config,
            sync_returncode=sync_returncode,
            smoke_returncode=smoke_returncode,
        ),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.database_path.read_bytes() == database_before
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert not (config.releases_dir / TARGET_SHA / ".ready.json").exists()


def test_reuses_only_safe_existing_release_with_matching_manifest_and_lock_hash(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    release = _create_ready_release(config, TARGET_SHA)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert (release / ".ready.json").is_file()
    kinds = [_command_kind(call) for call in runner.calls]
    assert "ls-tree" not in kinds
    assert "archive" not in kinds
    assert "uv-sync" not in kinds
    assert "import-smoke" not in kinds
    main_fetches = [
        call
        for call in runner.calls
        if _command_kind(call) == "fetch" and call.argv[-1] == "refs/heads/main"
    ]
    assert len(main_fetches) == 2


@pytest.mark.parametrize(
    "unsafe_variant",
    [
        pytest.param("root-symlink", id="root-symlink"),
        pytest.param("world-writable", id="world-writable"),
        pytest.param("nested-symlink", id="nested-symlink"),
        pytest.param("manifest-sha", id="manifest-sha"),
        pytest.param("lock-hash", id="lock-hash"),
        pytest.param("ownership", id="ownership"),
    ],
)
def test_rejects_unsafe_or_unattested_existing_release(
    controller_module,
    tmp_path,
    unsafe_variant,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    if unsafe_variant == "root-symlink":
        external = tmp_path / "external-release"
        external.mkdir()
        (config.releases_dir / TARGET_SHA).symlink_to(external)
    else:
        release = _create_ready_release(config, TARGET_SHA)
        if unsafe_variant == "world-writable":
            (release / "uv.lock").chmod(0o666)
        elif unsafe_variant == "nested-symlink":
            (release / "link").symlink_to(tmp_path / "outside")
        elif unsafe_variant == "manifest-sha":
            manifest = json.loads((release / ".ready.json").read_text())
            manifest["sha"] = OTHER_SHA
            (release / ".ready.json").write_text(json.dumps(manifest))
        elif unsafe_variant == "lock-hash":
            (release / "uv.lock").write_bytes(b"modified lock\n")
        elif unsafe_variant == "ownership":
            config = replace(config, release_uid=os.getuid() + 1)

    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    kinds = [_command_kind(call) for call in runner.calls]
    assert "uv-sync" not in kinds
    assert "import-smoke" not in kinds
    assert "systemctl" not in kinds


def test_remote_head_change_after_prepare_aborts_before_activation(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    config.database_path.write_bytes(b"database sentinel")
    database_before = config.database_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, heads=[TARGET_SHA, OTHER_SHA]),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "transient")
    kinds = [_command_kind(call) for call in runner.calls]
    main_fetches = [
        call
        for call in runner.calls
        if _command_kind(call) == "fetch" and call.argv[-1] == "refs/heads/main"
    ]
    assert len(main_fetches) == 2
    smoke_index = kinds.index("import-smoke")
    assert kinds[smoke_index + 1 : smoke_index + 3] == ["fetch", "rev-parse"]
    assert "systemctl" not in kinds
    assert config.current_link.resolve() == previous_release
    assert config.database_path.read_bytes() == database_before


@pytest.mark.parametrize(
    ("payload", "error", "expected_status"),
    [
        pytest.param(None, TimeoutError("timed out"), "transient", id="timeout"),
        pytest.param(
            None,
            urllib.error.HTTPError(WORKFLOW_URL, 429, "rate limited", None, None),
            "transient",
            id="rate-limit",
        ),
        pytest.param(
            None,
            urllib.error.HTTPError(WORKFLOW_URL, 503, "unavailable", None, None),
            "transient",
            id="server-error",
        ),
        pytest.param(
            None,
            json.JSONDecodeError("invalid JSON", "{", 0),
            "rejected",
            id="invalid-json",
        ),
        pytest.param({"workflow_runs": "not-a-list"}, None, "rejected", id="malformed"),
    ],
)
def test_github_api_failure_fails_closed_without_service_effects(
    controller_module,
    tmp_path,
    payload,
    error,
    expected_status,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.database_path.write_bytes(b"database sentinel")
    database_before = config.database_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(payload, events, error=error)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, expected_status)
    assert len(http.calls) == 1
    requested_url, timeout = http.calls[0]
    _assert_workflow_request(requested_url)
    assert timeout == config.api_timeout_seconds
    assert _preparation_calls(runner) == []
    assert config.database_path.read_bytes() == database_before
    assert not config.current_link.exists()


def test_deploy_journals_wal_safe_activation_and_commits_state_only_when_stable(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(config)
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)
    sleeper = FakeSleeper(config.lock_path)
    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    def forbid_plain_database_copy(*_args, **_kwargs):
        raise AssertionError("live SQLite must be backed up with sqlite3.Connection.backup")

    monkeypatch.setattr(controller_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(shutil, "copy2", forbid_plain_database_copy)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "production-telegram-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "production-openai-secret")

    try:
        result = _controller(
            controller_module,
            config,
            runner,
            http,
            sleeper=sleeper,
        ).deploy()
    finally:
        writer.close()

    _assert_status(result, "deployed")
    candidate_release = config.releases_dir / TARGET_SHA
    assert config.current_link.resolve() == candidate_release
    assert config.previous_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert _database_values(config.database_path) == ["committed-in-wal"]
    assert _database_has_migration_marker(config.database_path)

    receipts = list(config.deployment_receipts_dir.glob("*.json"))
    assert len(receipts) == 1
    receipt = _read_json(receipts[0])
    assert receipt["candidate_sha"] == TARGET_SHA
    assert receipt["previous_sha"] == LAST_SHA
    _assert_ordered_subsequence(receipt["phases"], ACTIVATION_PHASES)
    backup_record = receipt["backup"]
    backup_path = config.database_backup_dir / backup_record["name"]
    assert hashlib.sha256(backup_path.read_bytes()).hexdigest() == backup_record["sha256"]
    backup_metadata = backup_path.stat()
    assert backup_record["schema_version"] == 2
    assert backup_record["uid"] == backup_metadata.st_uid
    assert backup_record["gid"] == backup_metadata.st_gid
    assert backup_record["mode"] == stat.S_IMODE(backup_metadata.st_mode) == 0o600
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("PRAGMA foreign_key_check").fetchall() == []
        assert backup.execute(
            "SELECT value FROM deployment_probe ORDER BY id"
        ).fetchall() == [("committed-in-wal",)]

    assert not config.deployment_journal_path.exists()
    assert len(fsync_calls) >= len(ACTIVATION_PHASES)
    assert probe.journal_phases_at_boundaries[:4] == [
        ("systemctl:stop", "prepared"),
        ("migrate:live", "backed_up"),
        ("health:pre-switch", "migrated"),
        ("systemctl:start", "start_requested"),
    ]
    post_health_phases = [
        phase
        for boundary, phase in probe.journal_phases_at_boundaries
        if boundary == "health:post-start"
    ]
    assert len(post_health_phases) == config.stable_health_checks
    assert all(phase in {"start_requested", "health_checking"} for phase in post_health_phases)
    assert probe.lock_held_at_service_calls == [True, True]
    assert probe.post_start_candidate_healthchecks == config.stable_health_checks
    assert probe.service_active_checks == config.stable_health_checks
    assert sleeper.calls == [
        config.stable_health_interval_seconds,
        config.stable_health_interval_seconds,
    ]
    assert sleeper.lock_held_during_calls == [True, True]
    kinds = [_command_kind(call) for call in runner.calls]
    first_stop = next(
        index
        for index, call in enumerate(runner.calls)
        if _command_kind(call) == "systemctl" and call.argv[-2] == "stop"
    )
    assert kinds[first_stop - 2 : first_stop] == ["fetch", "rev-parse"]
    assert "migrate" in kinds[:first_stop]
    assert "healthcheck" in kinds[:first_stop]
    assert not any(
        secret in json.dumps(receipt)
        for secret in ("production-telegram-secret", "production-openai-secret")
    )


def test_controller_config_declares_crash_safe_activation_paths_and_health_window(
    controller_module,
):
    field_names = set(controller_module.ControllerConfig.__dataclass_fields__)

    assert {
        "previous_link",
        "deployment_journal_path",
        "deployment_receipts_dir",
        "quarantine_dir",
        "scratch_dir",
        "stable_health_checks",
        "stable_health_interval_seconds",
    } <= field_names


def test_post_start_health_failure_preserves_migrated_database_on_code_rollback(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(config, fail_stage="post-health", previous_compatible=True)
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_values(config.database_path) == [
        "committed-in-wal",
        "accepted-after-start",
    ]
    assert _database_has_migration_marker(config.database_path)
    systemctl_actions = [
        call.argv[-2]
        for call in runner.calls
        if _command_kind(call) == "systemctl"
    ]
    assert systemctl_actions == ["stop", "start", "stop", "start"]
    previous_health = [
        call
        for call in runner.calls
        if _command_kind(call) == "healthcheck"
        and Path(call.argv[0]).is_relative_to(previous_release)
    ]
    compatibility_health = [
        call
        for call in previous_health
        if Path(call.env["DATABASE_PATH"]) != config.database_path
    ]
    live_health = [
        call
        for call in previous_health
        if Path(call.env["DATABASE_PATH"]) == config.database_path
    ]
    assert len(compatibility_health) == 1
    assert len(live_health) == config.stable_health_checks
    assert list(config.quarantine_dir.glob(f"{TARGET_SHA}*.json"))


def test_deploy_returns_prepare_failures_without_service_or_database_effects(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.database_path.write_bytes(b"database sentinel")
    before_database = config.database_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config, sync_returncode=1), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert config.database_path.read_bytes() == before_database
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"


@pytest.mark.parametrize(
    "failure_stage",
    [
        pytest.param("dry-migrate", id="migration"),
        pytest.param("dry-health", id="health"),
    ],
)
def test_candidate_dry_run_failure_never_stops_or_touches_live_state(
    controller_module,
    tmp_path,
    failure_stage,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(config, fail_stage=failure_stage)
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_values(config.database_path) == ["committed-in-wal"]
    assert not _database_has_migration_marker(config.database_path)
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert not config.deployment_journal_path.exists()


def test_deploy_refetches_main_after_dry_run_immediately_before_stop(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(config)
    runner = FakeRunner(
        _trusted_scenario(
            config,
            heads=[TARGET_SHA, TARGET_SHA, OTHER_SHA],
            call_hook=probe,
        ),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "transient")
    assert config.current_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert not _database_has_migration_marker(config.database_path)
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    main_fetches = [
        call
        for call in runner.calls
        if _command_kind(call) == "fetch" and call.argv[-1] == "refs/heads/main"
    ]
    assert len(main_fetches) == 3


@pytest.mark.parametrize(
    "mismatch",
    [
        pytest.param("current-link", id="current-link"),
        pytest.param("previous-manifest", id="previous-manifest"),
        pytest.param("state-changed", id="state-changed-during-prepare"),
    ],
)
def test_deploy_fails_closed_if_current_state_and_previous_release_disagree(
    controller_module,
    tmp_path,
    mismatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    if mismatch == "current-link":
        other_release = _create_ready_release(config, OTHER_SHA)
        config.current_link.unlink()
        config.current_link.symlink_to(other_release)
    elif mismatch == "previous-manifest":
        manifest_path = previous_release / ".ready.json"
        manifest = _read_json(manifest_path)
        manifest["sha"] = OTHER_SHA
        manifest_path.write_text(json.dumps(manifest) + "\n")
    writer = _create_wal_database(config.database_path)

    def change_state_after_final_fetch(
        call: RunCall,
        occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        response = probe(call, occurrence)
        if (
            mismatch == "state-changed"
            and _command_kind(call) == "rev-parse"
            and occurrence == 3
        ):
            _write_deployed_state(config, OTHER_SHA)
        return response

    events: list[str] = []
    probe = ActivationProbe(config)
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=change_state_after_final_fetch),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "rejected")
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert not _database_has_migration_marker(config.database_path)


@pytest.mark.parametrize(
    "failure_stage",
    [
        pytest.param("stop", id="stop"),
        pytest.param("backup", id="backup-validation"),
        pytest.param("live-migrate", id="migration"),
        pytest.param("switch", id="symlink-switch"),
    ],
)
def test_pre_start_failures_restore_old_code_and_verified_database_without_advancing(
    controller_module,
    tmp_path,
    monkeypatch,
    failure_stage,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    if failure_stage == "backup":
        writer.execute("PRAGMA foreign_keys = OFF")
        writer.execute("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)")
        writer.execute(
            "CREATE TABLE fk_child (parent_id INTEGER REFERENCES fk_parent(id))"
        )
        writer.execute("INSERT INTO fk_child VALUES (999)")
        writer.commit()
    events: list[str] = []
    probe = ActivationProbe(
        config,
        fail_stage=failure_stage if failure_stage != "switch" else None,
    )
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    if failure_stage == "switch":
        real_replace = controller_module.os.replace
        failed_once = False

        def fail_candidate_switch(source, destination):
            nonlocal failed_once
            if Path(destination) == config.current_link and not failed_once:
                failed_once = True
                raise OSError("injected current-link switch failure")
            return real_replace(source, destination)

        monkeypatch.setattr(controller_module.os, "replace", fail_candidate_switch)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_values(config.database_path) == ["committed-in-wal"]
    assert not _database_has_migration_marker(config.database_path)
    if config.deployment_journal_path.exists():
        journal = _read_json(config.deployment_journal_path)
        assert "start_requested" not in journal["phases"]


@pytest.mark.parametrize(
    "failure_stage",
    [
        pytest.param("start", id="start-command"),
        pytest.param("post-health", id="stable-health"),
    ],
)
def test_post_boundary_failure_rolls_back_only_code_when_previous_is_compatible(
    controller_module,
    tmp_path,
    failure_stage,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(
        config,
        fail_stage=failure_stage,
        previous_compatible=True,
    )
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_has_migration_marker(config.database_path)
    expected_values = ["committed-in-wal"]
    if failure_stage == "post-health":
        expected_values.append("accepted-after-start")
    assert _database_values(config.database_path) == expected_values
    previous_health = [
        call
        for call in runner.calls
        if _command_kind(call) == "healthcheck"
        and Path(call.argv[0]).is_relative_to(previous_release)
    ]
    compatibility_health = [
        call
        for call in previous_health
        if Path(call.env["DATABASE_PATH"]) != config.database_path
    ]
    live_health = [
        call
        for call in previous_health
        if Path(call.env["DATABASE_PATH"]) == config.database_path
    ]
    assert len(compatibility_health) == 1
    assert len(live_health) == config.stable_health_checks
    compatibility_database = Path(compatibility_health[0].env["DATABASE_PATH"])
    assert compatibility_database != config.database_path
    systemctl_actions = [
        call.argv[-2]
        for call in runner.calls
        if _command_kind(call) == "systemctl"
    ]
    assert systemctl_actions == ["stop", "start", "stop", "start"]
    assert list(config.quarantine_dir.glob(f"{TARGET_SHA}*.json"))


@pytest.mark.parametrize(
    "failure_stage",
    [
        pytest.param("start", id="start-command"),
        pytest.param("post-health", id="stable-health"),
    ],
)
def test_post_boundary_incompatible_previous_leaves_service_stopped_for_intervention(
    controller_module,
    tmp_path,
    failure_stage,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(
        config,
        fail_stage=failure_stage,
        previous_compatible=False,
    )
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "intervention_required")
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_has_migration_marker(config.database_path)
    if failure_stage == "post-health":
        assert _database_values(config.database_path)[-1] == "accepted-after-start"
    systemctl_actions = [
        call.argv[-2]
        for call in runner.calls
        if _command_kind(call) == "systemctl"
    ]
    assert systemctl_actions[-1] == "stop"
    assert probe.service_running is False
    assert config.deployment_journal_path.exists()
    journal = _read_json(config.deployment_journal_path)
    assert journal["phase"] in {"start_requested", "intervention_required"}
    backup_path = config.database_backup_dir / journal["backup"]["name"]
    assert backup_path.is_file()
    assert list(config.quarantine_dir.glob(f"{TARGET_SHA}*.json"))


def test_quarantined_candidate_is_not_retried_by_next_timer_deploy(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    first_events: list[str] = []
    first_probe = ActivationProbe(
        config,
        fail_stage="post-health",
        previous_compatible=True,
    )
    first_runner = FakeRunner(
        _trusted_scenario(config, call_hook=first_probe),
        first_events,
    )
    first_http = _trusted_http(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        first_events,
    )
    try:
        first = _controller(
            controller_module,
            config,
            first_runner,
            first_http,
        ).deploy()
    finally:
        writer.close()

    _assert_status(first, "rejected")
    assert config.current_link.resolve() == previous_release
    assert list(config.quarantine_dir.glob(f"{TARGET_SHA}*.json"))
    database_before_retry = config.database_path.read_bytes()
    second_events: list[str] = []
    second_runner = FakeRunner(_scenario(config), second_events)
    second_http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), second_events)

    second = _controller(
        controller_module,
        config,
        second_runner,
        second_http,
    ).deploy()

    _assert_status(second, "rejected")
    assert not any(
        _command_kind(call) in {"systemctl", "migrate", "healthcheck"}
        for call in second_runner.calls
    )
    assert config.database_path.read_bytes() == database_before_retry
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"


@pytest.mark.parametrize(
    "phase",
    [
        pytest.param("stopped", id="stopped"),
        pytest.param("backed_up", id="backed-up"),
        pytest.param("migrated", id="migrated"),
        pytest.param("switched", id="switched"),
    ],
)
def test_next_deploy_recovers_pre_start_journal_by_restoring_old_release(
    controller_module,
    tmp_path,
    monkeypatch,
    phase,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate_release = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    backup_record = None
    backup_path = None
    if phase != "stopped":
        backup_path, backup_record = _write_recovery_backup(config)
    if phase in {"migrated", "switched"}:
        _apply_candidate_migration(config.database_path)
    if phase == "switched":
        config.current_link.unlink()
        config.current_link.symlink_to(candidate_release)
    _write_journal(config, phase=phase, backup=backup_record)

    def forbid_plain_restore(*_args, **_kwargs):
        raise AssertionError("recovery must restore SQLite through its backup API")

    monkeypatch.setattr(shutil, "copy2", forbid_plain_restore)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_values(config.database_path) == ["committed-in-wal"]
    assert not _database_has_migration_marker(config.database_path)
    assert not config.deployment_journal_path.exists()
    assert http.calls == []
    systemctl_actions = [
        call.argv[-2]
        for call in runner.calls
        if _command_kind(call) == "systemctl"
    ]
    assert systemctl_actions[-1] == "start"
    if backup_path is not None:
        assert backup_path.is_file()


@pytest.mark.parametrize(
    ("previous_compatible", "expected_status"),
    [
        pytest.param(True, "rejected", id="code-only-rollback"),
        pytest.param(False, "intervention_required", id="manual-intervention"),
    ],
)
def test_next_deploy_recovers_start_requested_without_restoring_database(
    controller_module,
    tmp_path,
    previous_compatible,
    expected_status,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate_release = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup_path, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    config.current_link.unlink()
    config.current_link.symlink_to(candidate_release)
    _write_journal(config, phase="start_requested", backup=backup_record)
    live_database_before = config.database_path.read_bytes()
    events: list[str] = []
    probe = ActivationProbe(config, previous_compatible=previous_compatible)
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, expected_status)
    assert config.database_path.read_bytes() == live_database_before
    assert _database_has_migration_marker(config.database_path)
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert http.calls == []
    systemctl_actions = [
        call.argv[-2]
        for call in runner.calls
        if _command_kind(call) == "systemctl"
    ]
    assert systemctl_actions[0] == "stop"
    if previous_compatible:
        assert config.current_link.resolve() == previous_release
        assert systemctl_actions[-1] == "start"
    else:
        assert systemctl_actions == ["stop"]
        assert config.current_link.resolve() == candidate_release
        assert config.deployment_journal_path.exists()


@pytest.mark.parametrize(
    "rollback_path",
    [
        pytest.param("pre-start", id="pre-start-rollback"),
        pytest.param("post-start", id="post-start-code-rollback"),
        pytest.param("recover-pre-start", id="pre-start-journal-recovery"),
        pytest.param("recover-boundary", id="no-restore-boundary-recovery"),
    ],
)
def test_every_previous_release_restart_requires_stable_service_and_app_health(
    controller_module,
    tmp_path,
    rollback_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate_release = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    backup_path, backup_record = _write_recovery_backup(config)
    events: list[str] = []
    probe = ActivationProbe(config)
    sleeper = FakeSleeper(config.lock_path)
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events),
        sleeper=sleeper,
    )

    if rollback_path == "pre-start":
        _write_journal(config, phase="stopped", backup=None)
        journal = _read_json(config.deployment_journal_path)
        probe.service_running = False
        result = controller._rollback_before_start(
            journal,
            previous_release,
            restore_database=False,
            reason="injected pre-start failure",
        )
    elif rollback_path == "post-start":
        _apply_candidate_migration(config.database_path)
        config.current_link.unlink()
        config.current_link.symlink_to(candidate_release)
        _write_journal(config, phase="start_requested", backup=backup_record)
        result = controller._rollback_after_start_requested(
            _read_json(config.deployment_journal_path),
            previous_release,
            reason="injected post-start failure",
        )
    elif rollback_path == "recover-pre-start":
        _apply_candidate_migration(config.database_path)
        _write_journal(config, phase="migrated", backup=backup_record)
        result = controller.deploy()
    else:
        _apply_candidate_migration(config.database_path)
        _write_journal(config, phase="migrated", backup=backup_record)
        journal = _read_json(config.deployment_journal_path)
        assert controller._mark_rollback_start_requested(journal)
        result = controller.deploy()

    _assert_status(result, "rejected")
    assert backup_path.is_file()
    assert config.current_link.resolve() == previous_release
    assert probe.service_active_checks == config.stable_health_checks
    assert probe.previous_live_healthchecks == config.stable_health_checks
    assert sleeper.calls == [
        config.stable_health_interval_seconds,
        config.stable_health_interval_seconds,
    ]
    assert not config.deployment_journal_path.exists()


@pytest.mark.parametrize(
    "failure_mode",
    [
        pytest.param("inactive", id="inactive-after-start"),
        pytest.param("crash-loop", id="becomes-inactive-during-window"),
        pytest.param("app-unhealthy", id="previous-app-health-fails"),
    ],
)
def test_failed_previous_restart_retains_durable_no_restore_intervention_journal(
    controller_module,
    tmp_path,
    failure_mode,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup_path, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_journal(config, phase="migrated", backup=backup_record)
    journal = _read_json(config.deployment_journal_path)
    events: list[str] = []
    probe = ActivationProbe(
        config,
        service_active_states=(
            ["inactive"]
            if failure_mode == "inactive"
            else ["active", "inactive"]
            if failure_mode == "crash-loop"
            else ["active"]
        ),
        previous_live_health_states=(
            [False] if failure_mode == "app-unhealthy" else None
        ),
    )
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events),
        sleeper=FakeSleeper(config.lock_path),
    )
    assert controller._mark_rollback_start_requested(journal)

    result = controller.deploy()

    _assert_status(result, "intervention_required")
    assert config.current_link.resolve() == previous_release
    retained = _read_json(config.deployment_journal_path)
    assert retained["phase"] == "intervention_required"
    assert retained["no_database_restore"] is True
    assert retained["rollback_from_phase"] == "migrated"
    assert _database_has_migration_marker(config.database_path)
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"


@pytest.mark.parametrize(
    "corruption",
    [
        pytest.param("invalid-json", id="invalid-json"),
        pytest.param("state-mismatch", id="state-mismatch"),
        pytest.param("link-mismatch", id="link-mismatch"),
        pytest.param("backup-digest", id="backup-digest"),
    ],
)
def test_corrupt_or_inconsistent_recovery_journal_fails_closed(
    controller_module,
    tmp_path,
    corruption,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    candidate_release = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    backup_path, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    config.current_link.unlink()
    config.current_link.symlink_to(candidate_release)
    _write_journal(config, phase="start_requested", backup=backup_record)
    if corruption == "invalid-json":
        config.deployment_journal_path.write_text("{not json")
    elif corruption == "state-mismatch":
        _write_deployed_state(config, OTHER_SHA)
    elif corruption == "link-mismatch":
        unexpected = _create_ready_release(config, OTHER_SHA)
        config.current_link.unlink()
        config.current_link.symlink_to(unexpected)
    elif corruption == "backup-digest":
        backup_path.write_bytes(backup_path.read_bytes() + b"tampered")
    database_before = config.database_path.read_bytes()
    current_before = config.current_link.readlink()
    state_before = config.deployed_state_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "intervention_required")
    assert runner.calls == []
    assert http.calls == []
    assert config.database_path.read_bytes() == database_before
    assert config.current_link.readlink() == current_before
    assert config.deployed_state_path.read_bytes() == state_before
    assert config.deployment_journal_path.exists()


@pytest.mark.parametrize(
    "missing_identity",
    [
        pytest.param("build", id="build"),
        pytest.param("runtime", id="runtime"),
    ],
)
def test_missing_required_identity_rejects_before_network_or_root_execution(
    controller_module,
    tmp_path,
    missing_identity,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    if missing_identity == "build":
        config = replace(config, build_uid=None, build_gid=None)
    else:
        config = replace(config, runtime_uid=None, runtime_gid=None)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


def test_default_config_fails_closed_when_deployment_users_are_missing(
    controller_module,
    monkeypatch,
    capsys,
):
    looked_up: list[str] = []

    def missing_user(name: str):
        looked_up.append(name)
        raise KeyError(name)

    monkeypatch.setattr(controller_module.pwd, "getpwnam", missing_user)

    exit_code = controller_module.main(["reconcile"])

    assert exit_code != 0
    assert looked_up
    output = capsys.readouterr().out.casefold()
    assert "required deployment user" in output
    assert "rejected" in output


def test_expected_venv_python_symlinks_are_preserved_and_chowned_without_following(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    symlink_paths: list[Path] = []

    def create_python_symlinks(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if _command_kind(call) != "uv-sync":
            return None
        assert call.cwd is not None
        bin_dir = call.cwd / ".venv/bin"
        bin_dir.mkdir(parents=True)
        interpreter = bin_dir / "python3.13"
        interpreter.write_bytes(b"test python shim")
        interpreter.chmod(0o755)
        for name in ("python", "python3"):
            link = bin_dir / name
            link.symlink_to("python3.13")
            symlink_paths.append(link)
        return _completed(call)

    real_chown = controller_module.os.chown
    real_lchown = controller_module.os.lchown
    lchowned: list[Path] = []

    def guarded_chown(path, uid, gid):
        assert not Path(path).is_symlink()
        return real_chown(path, uid, gid)

    def recording_lchown(path, uid, gid):
        lchowned.append(Path(path))
        return real_lchown(path, uid, gid)

    monkeypatch.setattr(controller_module.os, "chown", guarded_chown)
    monkeypatch.setattr(controller_module.os, "lchown", recording_lchown)
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=create_python_symlinks),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert symlink_paths
    staging = config.releases_dir / f".{TARGET_SHA}.tmp"
    release = config.releases_dir / TARGET_SHA
    promoted_symlinks = [release / path.relative_to(staging) for path in symlink_paths]
    assert all(path.is_symlink() for path in promoted_symlinks)
    assert set(symlink_paths).issubset(set(lchowned))


@pytest.mark.parametrize(
    ("relative_path", "target"),
    [
        pytest.param(".venv/bin/pip", "python3.13", id="unexpected-venv-link"),
        pytest.param("application-link", "uv.lock", id="application-link"),
        pytest.param(".venv/bin/python", "../../uv.lock", id="escaping-python"),
    ],
)
def test_arbitrary_release_symlinks_remain_rejected(
    controller_module,
    tmp_path,
    relative_path,
    target,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)

    def create_unsafe_symlink(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if _command_kind(call) != "uv-sync":
            return None
        assert call.cwd is not None
        python = call.cwd / ".venv/bin/python3.13"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"test python shim")
        python.chmod(0o755)
        link = call.cwd / relative_path
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        if relative_path != ".venv/bin/python":
            (call.cwd / ".venv/bin/python").symlink_to("python3.13")
        return _completed(call)

    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=create_unsafe_symlink),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")


@pytest.mark.parametrize("service_state", ["inactive", "failed", "restarting"])
def test_candidate_is_not_committed_without_active_stable_systemd_service(
    controller_module,
    tmp_path,
    service_state,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    probe = ActivationProbe(
        config,
        previous_compatible=True,
        service_active_states=[service_state],
    )
    runner = FakeRunner(_trusted_scenario(config, call_hook=probe), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    try:
        result = _controller(controller_module, config, runner, http).deploy()
    finally:
        writer.close()

    _assert_status(result, "rejected")
    assert probe.service_active_checks >= 1
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert list(config.deployment_receipts_dir.glob("*.json")) == []


@pytest.mark.parametrize("mutation", ["tracked-content", "untracked-source"])
def test_build_user_cannot_change_authenticated_source_tree(
    controller_module,
    tmp_path,
    mutation,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)

    def mutate_after_build(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if _command_kind(call) != "uv-sync":
            return None
        assert call.cwd is not None
        python = call.cwd / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"test python shim")
        python.chmod(0o755)
        if mutation == "tracked-content":
            (call.cwd / "src/honeybuy_tg/__init__.py").write_text("MUTATED = True\n")
        else:
            (call.cwd / "src/honeybuy_tg/injected.py").write_text("INJECTED = True\n")
        return _completed(call)

    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=mutate_after_build),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert not (config.releases_dir / TARGET_SHA / ".ready.json").exists()


def test_surviving_build_child_cannot_redirect_root_finalization_outside_release(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    outside = tmp_path / "outside-owned-by-attacker"
    outside.write_bytes(b"outside sentinel")
    outside.chmod(0o600)
    outside_before = (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode))
    build_finished = False
    raced = False

    def finish_build(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        nonlocal build_finished
        if _command_kind(call) != "uv-sync":
            return None
        assert call.cwd is not None
        python = call.cwd / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"test python shim")
        python.chmod(0o755)
        build_finished = True
        return _completed(call)

    tracked = config.releases_dir / TARGET_SHA / "src/honeybuy_tg/__init__.py"

    def inject_race() -> None:
        nonlocal raced
        if not build_finished or raced or not tracked.is_file():
            return
        tracked.unlink()
        tracked.symlink_to(outside)
        raced = True

    def racing_chown(path, _uid, _gid, *args, **kwargs):
        if Path(path) == tracked:
            inject_race()

    monkeypatch.setattr(controller_module.os, "chown", racing_chown)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config, call_hook=finish_build), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected" if raced else "prepared")
    assert outside.read_bytes() == outside_before[0]
    assert stat.S_IMODE(outside.stat().st_mode) == outside_before[1]


def test_database_clone_does_not_follow_source_swapped_to_symlink(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    source = config.database_path
    target = config.scratch_dir / "clone.sqlite3"
    outside = tmp_path / "outside.sqlite3"
    for path, value in ((source, "trusted"), (outside, "attacker")):
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            database.execute("INSERT INTO marker VALUES (?)", (value,))
    outside_before = outside.read_bytes()
    saved_source = source.with_name("trusted-source.sqlite3")
    real_connect = sqlite3.connect
    raced = False

    def racing_connect(database, *args, **kwargs):
        nonlocal raced
        if not raced and _sqlite_argument_path(database) == source:
            os.replace(source, saved_source)
            source.symlink_to(outside)
            raced = True
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(controller_module.sqlite3, "connect", racing_connect)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)
    controller = _controller(controller_module, config, runner, http)

    try:
        controller._clone_database(source, target)
    except (OSError, sqlite3.Error, ValueError):
        pass

    assert outside.read_bytes() == outside_before
    if target.exists() and target.stat().st_size:
        with real_connect(target) as database:
            assert database.execute("SELECT value FROM marker").fetchone() == (
                "trusted",
            )


def test_sqlite_clone_helper_closes_wal_connections_before_sidecar_cleanup(
    controller_module,
    tmp_path,
):
    source_directory = tmp_path / "source"
    target_directory = tmp_path / "target"
    source_directory.mkdir()
    target_directory.mkdir()
    source = source_directory / "live.sqlite3"
    target = target_directory / "clone.sqlite3"
    writer = _create_wal_database(source)
    try:
        completed = subprocess.run(
            (
                sys.executable,
                "-I",
                "-c",
                controller_module.SQLITE_CLONE_HELPER,
                str(source),
                str(target),
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
    finally:
        writer.close()

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert sorted(path.name for path in target_directory.iterdir()) == [target.name]
    assert target.read_bytes()[18:20] == b"\x02\x02"
    immutable_uri = f"file:{urllib.parse.quote(str(target), safe='/')}?immutable=1"
    with closing(sqlite3.connect(immutable_uri, uri=True)) as clone:
        assert clone.execute(
            "SELECT value FROM deployment_probe ORDER BY id"
        ).fetchall() == [("committed-in-wal",)]
    assert sorted(path.name for path in target_directory.iterdir()) == [target.name]


def test_sqlite_clone_helper_explicitly_closes_connections_before_cleanup(
    controller_module,
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"
    source.touch()
    events: list[str] = []
    connections = []

    class FakeCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class FakeConnection:
        def __init__(self, database, **_kwargs):
            self.database = str(database)
            self.closed = False
            connections.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            # Deliberately mirror sqlite3.Connection: leaving a transaction
            # context does not close the underlying database handle.
            return False

        def close(self):
            self.closed = True
            events.append(f"close:{len(connections)}")

        def backup(self, _target):
            events.append("backup")

        def execute(self, statement):
            normalized = "".join(statement.casefold().split())
            if normalized == "pragmajournal_mode":
                return FakeCursor(("wal",))
            if normalized == "pragmawal_checkpoint(truncate)":
                return FakeCursor((0, 0, 0))
            if normalized == "pragmajournal_mode=delete":
                assert len(connections) == 3
                assert all(connection.closed for connection in connections[:2])
                return FakeCursor(("delete",))
            if normalized == "pragmajournal_mode=wal":
                return FakeCursor(("wal",))
            raise AssertionError(f"unexpected SQL: {statement}")

    fake_sqlite = ModuleType("sqlite3")
    fake_sqlite.connect = FakeConnection
    real_unlink = Path.unlink

    def unlink_after_close(path, *args, **kwargs):
        assert all(connection.closed for connection in connections)
        events.append(f"unlink:{Path(path).name}")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "sqlite3", fake_sqlite)
    monkeypatch.setattr(Path, "unlink", unlink_after_close)
    monkeypatch.setattr(sys, "argv", ["clone-helper", str(source), str(target)])

    exec(controller_module.SQLITE_CLONE_HELPER, {"__name__": "__main__"})

    assert len(connections) == 3
    assert all(connection.closed for connection in connections)
    assert events[-2:] == ["unlink:target.sqlite3-wal", "unlink:target.sqlite3-shm"]


def test_in_process_clone_closes_every_connection_before_mode_and_sidecar_changes(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    source = config.database_path
    writer = _create_wal_database(source)
    writer.close()
    target = config.scratch_dir / "tracked-clone.sqlite3"
    real_connect = sqlite3.connect
    real_remove_sidecars = controller_module._remove_database_sidecars
    connections = []

    class TrackedConnection:
        def __init__(self, *args, **kwargs):
            self.connection = real_connect(*args, **kwargs)
            self.closed = False
            connections.append(self)

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self.connection.__exit__(*exc_info)

        def close(self):
            self.closed = True
            return self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, *args, **kwargs):
            if statement.casefold().startswith("pragma journal_mode=delete"):
                assert all(connection.closed for connection in connections[:-1])
            return self.connection.execute(statement, *args, **kwargs)

        def backup(self, target_connection, *args, **kwargs):
            target = getattr(target_connection, "connection", target_connection)
            return self.connection.backup(target, *args, **kwargs)

    def tracked_connect(*args, **kwargs):
        return TrackedConnection(*args, **kwargs)

    def remove_sidecars_only_after_close(path):
        assert connections
        assert all(connection.closed for connection in connections)
        return real_remove_sidecars(path)

    monkeypatch.setattr(controller_module.sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(
        controller_module,
        "_remove_database_sidecars",
        remove_sidecars_only_after_close,
    )
    events: list[str] = []
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), events),
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events),
    )

    controller._clone_database_in_process(source, target)

    assert connections
    assert all(connection.closed for connection in connections)
    with closing(real_connect(target)) as clone:
        assert clone.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert clone.execute(
            "SELECT value FROM deployment_probe ORDER BY id"
        ).fetchall() == [("committed-in-wal",)]


def test_database_restore_does_not_follow_live_path_swapped_to_symlink(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    outside = tmp_path / "outside-live-target.sqlite3"
    with sqlite3.connect(outside) as database:
        database.execute(
            "CREATE TABLE deployment_probe "
            "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        database.execute("INSERT INTO deployment_probe(value) VALUES ('outside')")
        database.execute("PRAGMA user_version = 2")
    outside_before = (outside.read_bytes(), stat.S_IMODE(outside.stat().st_mode))

    saved_live = config.database_path.with_name("migrated-live.sqlite3")
    real_connect = sqlite3.connect
    real_replace = controller_module.os.replace
    real_replace(config.database_path, saved_live)
    config.database_path.symlink_to(outside)
    unsafe_chowns: list[Path] = []

    def forbid_live_destination_connect(database, *args, **kwargs):
        if _sqlite_argument_path(database) == config.database_path:
            raise AssertionError("restore must not open the live destination with SQLite")
        return real_connect(database, *args, **kwargs)

    def recording_chown(path, _uid, _gid, *args, **kwargs):
        if Path(path).is_symlink():
            unsafe_chowns.append(Path(path))

    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)
    controller = _controller(controller_module, config, runner, http)

    with monkeypatch.context() as restore_patch:
        restore_patch.setattr(
            controller_module.sqlite3,
            "connect",
            forbid_live_destination_connect,
        )
        restore_patch.setattr(controller_module.os, "geteuid", lambda: 0)
        restore_patch.setattr(controller_module.os, "chown", recording_chown)
        controller._restore_database(
            {"candidate_sha": TARGET_SHA, "backup": backup_record}
        )

    assert outside.read_bytes() == outside_before[0]
    assert stat.S_IMODE(outside.stat().st_mode) == outside_before[1]
    assert unsafe_chowns == []
    assert not config.database_path.is_symlink()
    assert _database_values(config.database_path) == ["committed-in-wal"]


def test_database_restore_uses_atomic_replace_and_cleans_sqlite_sidecars(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    for suffix in ("-wal", "-shm"):
        config.database_path.with_name(f"{config.database_path.name}{suffix}").touch()
    real_replace = controller_module.os.replace
    replace_destinations: list[Path] = []

    def recording_replace(source, destination, *args, **kwargs):
        replace_destinations.append(Path(destination))
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(controller_module.os, "replace", recording_replace)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)
    controller = _controller(controller_module, config, runner, http)

    controller._restore_database(
        {"candidate_sha": TARGET_SHA, "backup": backup_record}
    )

    assert not config.database_path.is_symlink()
    assert config.database_path in replace_destinations
    assert not config.database_path.with_name(
        f"{config.database_path.name}-wal"
    ).exists()
    assert not config.database_path.with_name(
        f"{config.database_path.name}-shm"
    ).exists()
    with sqlite3.connect(config.database_path) as restored:
        assert restored.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert restored.execute(
            "SELECT value FROM deployment_probe ORDER BY id"
        ).fetchall() == [("committed-in-wal",)]


def test_absent_database_restore_fsyncs_parent_before_no_restore_boundary(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    paths = [
        config.database_path,
        config.database_path.with_name(f"{config.database_path.name}-wal"),
        config.database_path.with_name(f"{config.database_path.name}-shm"),
    ]
    for path in paths:
        path.write_bytes(b"stale database artifact")
    journal = {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "previous_sha": LAST_SHA,
        "phase": "migrated",
        "phases": list(ACTIVATION_PHASES[:4]),
        "backup": None,
        "database_existed": False,
    }
    events: list[str] = []
    real_unlink = controller_module._unlink_regular_or_missing
    real_fsync_directory = controller_module._fsync_directory
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), events),
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events),
    )
    real_write_journal = controller._write_journal

    def recording_unlink(path):
        if path in paths:
            events.append(f"unlink:{path.name}")
        return real_unlink(path)

    def recording_fsync_directory(path):
        if path == config.database_path.parent:
            events.append("fsync:database-parent")
        return real_fsync_directory(path)

    def recording_write_journal(payload):
        if payload.get("phase") == "rollback_start_requested":
            events.append("journal:rollback-start-requested")
        return real_write_journal(payload)

    monkeypatch.setattr(
        controller_module,
        "_unlink_regular_or_missing",
        recording_unlink,
    )
    monkeypatch.setattr(
        controller_module,
        "_fsync_directory",
        recording_fsync_directory,
    )
    monkeypatch.setattr(controller, "_write_journal", recording_write_journal)

    controller._restore_database(journal)
    assert controller._mark_rollback_start_requested(journal)

    removal_events = [f"unlink:{path.name}" for path in paths]
    assert events[:3] == removal_events
    fsync_index = events.index("fsync:database-parent")
    journal_index = events.index("journal:rollback-start-requested")
    assert fsync_index == 3
    assert fsync_index < journal_index
    assert not any(path.exists() for path in paths)


def test_recovery_journals_no_restore_boundary_before_restarting_old_service(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_journal(config, phase="migrated", backup=backup_record)
    phases_at_start: list[str] = []

    def crash_after_old_start(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if _command_kind(call) != "systemctl" or call.argv[1] != "start":
            return None
        phases_at_start.append(_read_json(config.deployment_journal_path)["phase"])
        with sqlite3.connect(config.database_path) as database:
            database.execute(
                "INSERT INTO deployment_probe(value) VALUES ('write-after-restart')"
            )
        raise SimulatedControllerCrash

    first_events: list[str] = []
    first_runner = FakeRunner(
        _scenario(config, call_hook=crash_after_old_start),
        first_events,
    )
    first_http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), first_events)

    with pytest.raises(SimulatedControllerCrash):
        _controller(
            controller_module,
            config,
            first_runner,
            first_http,
        ).deploy()

    assert "write-after-restart" in _database_values(config.database_path)
    second_events: list[str] = []
    second_runner = FakeRunner(_scenario(config), second_events)
    second_http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), second_events)

    result = _controller(
        controller_module,
        config,
        second_runner,
        second_http,
    ).deploy()

    _assert_status(result, "rejected")
    assert len(phases_at_start) == 1
    assert phases_at_start[0] not in {
        "prepared",
        "stopped",
        "backed_up",
        "migrated",
        "switched",
    }
    assert "write-after-restart" in _database_values(config.database_path)
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert not config.deployment_journal_path.exists()


def test_recovery_accepts_crash_after_current_switch_before_phase_advance(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)
    controller = _controller(controller_module, config, runner, http)
    real_switch_link = controller._switch_link

    def crash_after_current_switch(link, release):
        real_switch_link(link, release)
        if link == config.current_link:
            raise SimulatedControllerCrash

    monkeypatch.setattr(controller, "_switch_link", crash_after_current_switch)
    try:
        with pytest.raises(SimulatedControllerCrash):
            controller.deploy()
    finally:
        writer.close()

    assert config.current_link.resolve() == config.releases_dir / TARGET_SHA
    recovery_events: list[str] = []
    recovery_runner = FakeRunner(_scenario(config), recovery_events)
    recovery_http = FakeHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        recovery_events,
    )

    result = _controller(
        controller_module,
        config,
        recovery_runner,
        recovery_http,
    ).deploy()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    assert _database_values(config.database_path) == ["committed-in-wal"]
    assert not _database_has_migration_marker(config.database_path)
    assert not config.deployment_journal_path.exists()
    assert recovery_http.calls == []


def _extended_control_plane_path(config, target_name: str) -> Path:
    paths = {
        "releases-directory": config.releases_dir,
        "release-link-directory": config.current_link.parent,
        "database-backup-directory": config.database_backup_dir,
        "database-scratch-directory": config.scratch_dir,
        "empty-work-directory": config.empty_work_dir,
        "receipts-directory": config.deployment_receipts_dir,
        "quarantine-directory": config.quarantine_dir,
        "deployment-journal": config.deployment_journal_path,
        "receipt-file": config.deployment_receipts_dir / f"{LAST_SHA}.json",
        "quarantine-file": config.quarantine_dir / f"{TARGET_SHA}.json",
    }
    return paths[target_name]


def _populate_extended_control_plane(config, target_name: str) -> Path:
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    _create_ready_release(config, TARGET_SHA)
    target = _extended_control_plane_path(config, target_name)
    if target_name == "deployment-journal":
        _write_journal(config, phase="prepared", backup=None)
    elif target_name in {"receipt-file", "quarantine-file"}:
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0o600)
    return target


@pytest.mark.parametrize(
    "target_name",
    [
        "releases-directory",
        "release-link-directory",
        "database-backup-directory",
        "database-scratch-directory",
        "empty-work-directory",
        "receipts-directory",
        "quarantine-directory",
        "deployment-journal",
        "receipt-file",
        "quarantine-file",
    ],
)
def test_all_controller_namespaces_reject_writable_metadata_before_effects(
    controller_module,
    tmp_path,
    target_name,
):
    config = _activation_config(controller_module, tmp_path)
    target = _populate_extended_control_plane(config, target_name)
    target.chmod(0o777 if target.is_dir() else 0o666)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "unsafe" in result.reason
    assert runner.calls == []
    assert http.calls == []


@pytest.mark.parametrize(
    "target_name",
    [
        "releases-directory",
        "release-link-directory",
        "database-backup-directory",
        "database-scratch-directory",
        "empty-work-directory",
        "receipts-directory",
        "quarantine-directory",
        "deployment-journal",
        "receipt-file",
        "quarantine-file",
    ],
)
def test_all_controller_namespaces_reject_wrong_owner_before_effects(
    controller_module,
    tmp_path,
    monkeypatch,
    target_name,
):
    config = _activation_config(controller_module, tmp_path)
    target = _populate_extended_control_plane(config, target_name)
    target_inode = target.lstat().st_ino
    real_lstat = controller_module.os.lstat

    def wrong_owner_lstat(path, *args, **kwargs):
        metadata = real_lstat(path, *args, **kwargs)
        if metadata.st_ino == target_inode:
            return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
        return metadata

    monkeypatch.setattr(controller_module.os, "lstat", wrong_owner_lstat)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "unsafe" in result.reason
    assert runner.calls == []
    assert http.calls == []


def test_controller_namespace_symlink_is_rejected_before_effects(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    real_directory = tmp_path / "attacker-controlled-scratch"
    real_directory.mkdir()
    config.scratch_dir.rmdir()
    config.scratch_dir.symlink_to(real_directory, target_is_directory=True)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "unsafe" in result.reason
    assert runner.calls == []
    assert http.calls == []


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "source-repository",
        "state-directory",
        "deployed-state",
        "trust-directory",
        "allowed-signers",
    ],
)
def test_root_trust_paths_reject_group_or_world_writable_metadata(
    controller_module,
    tmp_path,
    unsafe_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    paths = {
        "source-repository": config.source_repo,
        "state-directory": config.deployed_state_path.parent,
        "deployed-state": config.deployed_state_path,
        "trust-directory": config.allowed_signers_path.parent,
        "allowed-signers": config.allowed_signers_path,
    }
    paths[unsafe_path].chmod(0o777 if paths[unsafe_path].is_dir() else 0o666)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "source-repository",
        "state-directory",
        "deployed-state",
        "trust-directory",
        "allowed-signers",
    ],
)
def test_root_trust_paths_reject_wrong_owner_metadata(
    controller_module,
    tmp_path,
    monkeypatch,
    unsafe_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    paths = {
        "source-repository": config.source_repo,
        "state-directory": config.deployed_state_path.parent,
        "deployed-state": config.deployed_state_path,
        "trust-directory": config.allowed_signers_path.parent,
        "allowed-signers": config.allowed_signers_path,
    }
    target = paths[unsafe_path]
    target_inode = target.lstat().st_ino
    real_lstat = controller_module.os.lstat
    real_fstat = controller_module.os.fstat

    def wrong_owner(metadata):
        if metadata.st_ino == target_inode:
            return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
        return metadata

    def wrong_owner_lstat(path, *args, **kwargs):
        return wrong_owner(real_lstat(path, *args, **kwargs))

    def wrong_owner_fstat(descriptor):
        return wrong_owner(real_fstat(descriptor))

    monkeypatch.setattr(controller_module.os, "lstat", wrong_owner_lstat)
    monkeypatch.setattr(controller_module.os, "fstat", wrong_owner_fstat)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


def test_every_git_command_ignores_untrusted_config_hooks_and_helpers(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    git_calls = [call for call in runner.calls if call.argv[0] == str(config.git_path)]
    _assert_all_git_calls_hardened(runner, config)
    verify_call = next(
        call for call in git_calls if _command_kind(call) == "verify-commit"
    )
    assert "gpg.ssh.program=/usr/bin/ssh-keygen" in _git_config_overrides(
        verify_call
    )


def test_rejects_lock_inode_with_untrusted_owner_or_mode(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.lock_path.parent.mkdir(parents=True)
    config.lock_path.write_text("attacker-controlled\n")
    config.lock_path.chmod(0o666)
    real_fstat = controller_module.os.fstat
    lock_inode = config.lock_path.stat().st_ino

    def wrong_owner_fstat(descriptor):
        metadata = real_fstat(descriptor)
        if metadata.st_ino == lock_inode:
            return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
        return metadata

    monkeypatch.setattr(controller_module.os, "fstat", wrong_owner_fstat)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


def test_rejects_hardlinked_lock_inode(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.lock_path.parent.mkdir(parents=True)
    config.lock_path.touch(mode=0o600)
    os.link(config.lock_path, config.lock_path.with_name("attacker-hardlink"))
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


def test_rejects_lock_path_replaced_after_open(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.lock_path.parent.mkdir(parents=True)
    config.lock_path.touch(mode=0o600)
    moved_lock = config.lock_path.with_name("original-lock")
    real_open = controller_module.os.open
    raced = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal raced
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if not raced and Path(path) == config.lock_path:
            os.replace(config.lock_path, moved_lock)
            config.lock_path.write_text("replacement\n")
            config.lock_path.chmod(0o600)
            raced = True
        return descriptor

    monkeypatch.setattr(controller_module.os, "open", racing_open)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    assert raced
    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []


def test_rejects_writable_lock_directory_before_opening_lock(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.lock_path.parent.mkdir(parents=True)
    config.lock_path.parent.chmod(0o777)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert not config.lock_path.exists()
    assert runner.calls == []
    assert http.calls == []


def test_production_identity_policy_is_explicit_and_defaults_to_root_releases(
    controller_module,
):
    defaults = controller_module.ControllerConfig()

    assert defaults.test_mode is False
    assert defaults.release_uid == 0
    assert defaults.release_gid == 0


@pytest.mark.parametrize(
    ("invalid_identity", "effective_uid", "expected_reason"),
    [
        pytest.param(
            "nonroot-controller",
            1000,
            "production release controller must run as root",
            id="nonroot-controller",
        ),
        pytest.param(
            "root-build",
            0,
            "build and runtime identities must be non-root",
            id="root-build",
        ),
        pytest.param(
            "root-runtime",
            0,
            "build and runtime identities must be non-root",
            id="root-runtime",
        ),
        pytest.param(
            "shared-runtime",
            0,
            "build and runtime identities must be distinct",
            id="shared-uid",
        ),
        pytest.param(
            "shared-group",
            0,
            "build and runtime identities must be distinct",
            id="shared-gid",
        ),
        pytest.param(
            "nonroot-release",
            0,
            "production releases must be owned by root",
            id="nonroot-release",
        ),
        pytest.param(
            "nonroot-release-group",
            0,
            "production releases must be owned by root",
            id="nonroot-release-group",
        ),
    ],
)
def test_production_rejects_root_or_shared_worker_identities_before_commands(
    controller_module,
    tmp_path,
    monkeypatch,
    invalid_identity,
    effective_uid,
    expected_reason,
):
    config = _config(controller_module, tmp_path)
    values = {
        "build_uid": 10001,
        "build_gid": 10001,
        "runtime_uid": 10002,
        "runtime_gid": 10002,
        "release_uid": 0,
        "release_gid": 0,
    }
    if invalid_identity == "root-build":
        values["build_uid"] = 0
    elif invalid_identity == "root-runtime":
        values["runtime_uid"] = 0
    elif invalid_identity == "shared-runtime":
        values["runtime_uid"] = values["build_uid"]
    elif invalid_identity == "shared-group":
        values["runtime_gid"] = values["build_gid"]
    elif invalid_identity == "nonroot-release":
        values["release_uid"] = 10003
    elif invalid_identity == "nonroot-release-group":
        values["release_gid"] = 10003
    config = replace(config, **values)
    object.__setattr__(config, "test_mode", False)
    monkeypatch.setattr(controller_module.os, "geteuid", lambda: effective_uid)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert result.reason == expected_reason
    assert runner.calls == []
    assert http.calls == []


def test_production_v2_ready_manifest_round_trips_and_detects_tampering(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    object.__setattr__(config, "test_mode", False)
    release = _create_ready_release(config, TARGET_SHA)

    controller_module._finalize_release_tree(
        release,
        TARGET_SHA,
        config.release_uid,
        config.release_gid,
        source_tree_sha256="a" * 64,
        archive_sha256="b" * 64,
        legacy_manifest=False,
    )

    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), []),
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), []),
    )
    manifest = _read_json(release / ".ready.json")
    assert manifest["version"] == 2
    assert set(manifest) == {
        "version",
        "sha",
        "source_tree_sha256",
        "archive_sha256",
        "uv_lock_sha256",
        "venv_sha256",
        "artifact_sha256",
        "provenance_sha256",
    }
    assert controller._ready_release_is_safe(release, TARGET_SHA)

    (release / "uv.lock").write_bytes(b"tampered after readiness\n")

    assert not controller._ready_release_is_safe(release, TARGET_SHA)


def test_production_service_observation_parses_exact_systemd_show_fields(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    object.__setattr__(config, "test_mode", False)

    def systemd_responses(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if len(call.argv) > 1 and call.argv[1] == "show":
            return _completed(
                call,
                stdout=(
                    "ActiveState=active\n"
                    "SubState=running\n"
                    "MainPID=4242\n"
                    "NRestarts=3\n"
                    "ExecMainStatus=0\n"
                ),
            )
        return None

    runner = FakeRunner(
        _scenario(config, call_hook=systemd_responses),
        [],
    )
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), []),
    )

    observation = controller._service_observation()

    assert observation == controller_module.ServiceObservation(
        main_pid=4242,
        restart_count=3,
        exit_status=0,
    )
    show_call = next(call for call in runner.calls if call.argv[1] == "show")
    assert show_call.argv == (
        str(config.systemctl_path),
        "show",
        "--no-pager",
        "--property=ActiveState",
        "--property=SubState",
        "--property=MainPID",
        "--property=NRestarts",
        "--property=ExecMainStatus",
        config.service_name,
    )
    assert show_call.cwd is None
    assert show_call.uid is None
    assert show_call.gid is None
    assert show_call.env is None


@pytest.mark.parametrize(
    "show_output",
    [
        pytest.param(
            "ActiveState=inactive\nSubState=dead\nMainPID=0\n"
            "NRestarts=0\nExecMainStatus=0\n",
            id="inactive",
        ),
        pytest.param(
            "ActiveState=active\nSubState=running\nMainPID=not-a-pid\n"
            "NRestarts=0\nExecMainStatus=0\n",
            id="malformed-number",
        ),
        pytest.param(
            "ActiveState=active\nActiveState=active\nSubState=running\n"
            "MainPID=42\nNRestarts=0\nExecMainStatus=0\n",
            id="duplicate-field",
        ),
        pytest.param(
            "ActiveState=active\nSubState=running\nMainPID=42\nNRestarts=0\n",
            id="missing-field",
        ),
    ],
)
def test_production_service_observation_rejects_untrusted_show_output(
    controller_module,
    tmp_path,
    show_output,
):
    config = _config(controller_module, tmp_path)
    object.__setattr__(config, "test_mode", False)

    def systemd_responses(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        if len(call.argv) > 1 and call.argv[1] == "show":
            return _completed(call, stdout=show_output)
        return None

    runner = FakeRunner(_scenario(config, call_hook=systemd_responses), [])
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), []),
    )

    assert controller._service_observation() is None


def test_production_live_database_clone_uses_runtime_identity_path(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    config = replace(
        config,
        runtime_uid=10002,
        runtime_gid=10002,
        test_mode=False,
    )
    target = config.scratch_dir / "runtime-clone.sqlite3"
    called: list[tuple[Path, Path]] = []
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), []),
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), []),
    )

    def runtime_clone(source_path, target_path):
        called.append((source_path, target_path))

    monkeypatch.setattr(controller_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(controller, "_clone_database_as_runtime", runtime_clone)

    controller._clone_database(config.database_path, target)

    assert called == [(config.database_path, target)]


class _FakeHostProcess:
    _next_pid = 8000

    def __init__(
        self,
        argv,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
        communicate_effects: list[object] | None = None,
    ):
        self.args = tuple(str(argument) for argument in argv)
        self.stdout_data = stdout
        self.stderr_data = stderr
        self.returncode = returncode
        self.communicate_effects = list(communicate_effects or [])
        self.pid = self._next_pid
        type(self)._next_pid += 1

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def communicate(self, input=None, timeout=None):
        del input, timeout
        if self.communicate_effects:
            effect = self.communicate_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            assert isinstance(effect, tuple) and len(effect) == 2
            return effect
        return self.stdout_data, self.stderr_data

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -signal.SIGKILL


def _systemd_option(argv: tuple[str, ...], name: str) -> str | None:
    prefix = f"{name}="
    for index, argument in enumerate(argv):
        if argument.startswith(prefix):
            return argument.removeprefix(prefix)
        if argument == name and index + 1 < len(argv):
            return argv[index + 1]
    return None


@dataclass(frozen=True)
class _SyntheticTransientState:
    load_state: str = "loaded"
    active_state: str = "inactive"
    sub_state: str = "dead"
    result: str = "success"
    exec_main_code: str = "exited"
    exit_status: int = 0
    control_group: str = "/system.slice/{unit}"


def _synthetic_transient_status(
    state: _SyntheticTransientState,
    unit: str,
) -> bytes:
    return (
        f"ControlGroup={state.control_group.format(unit=unit)}\n"
        f"LoadState={state.load_state}\n"
        f"ActiveState={state.active_state}\n"
        f"SubState={state.sub_state}\n"
        f"Result={state.result}\n"
        f"ExecMainCode={state.exec_main_code}\n"
        f"ExecMainStatus={state.exit_status}\n"
    ).encode("ascii")


def _fake_transient_host(
    controller_module,
    tmp_path,
    monkeypatch,
    *,
    populated: int | None,
    states: tuple[_SyntheticTransientState, ...] = (),
    workload_returncode: int = 0,
    workload_stdout: bytes = b"",
    workload_stderr: bytes = b"",
    workload_communicate_effects: tuple[object, ...] = (),
):
    systemd_run_path = Path("/test-tools/systemd-run")
    systemctl_path = Path("/test-tools/systemctl")
    cgroup_root = tmp_path / "sys/fs/cgroup"
    calls: list[tuple[str, ...]] = []
    killpg_calls: list[int] = []
    status_index = 0
    default_state = _SyntheticTransientState()

    def fake_popen(argv, **_kwargs):
        nonlocal status_index
        normalized = tuple(str(argument) for argument in argv)
        calls.append(normalized)
        executable = Path(normalized[0])
        if executable == systemd_run_path:
            unit = _systemd_option(normalized, "--unit")
            assert unit is not None
            if populated is not None:
                unit_cgroup = cgroup_root / "system.slice" / unit
                unit_cgroup.mkdir(parents=True)
                (unit_cgroup / "cgroup.events").write_text(
                    f"populated {populated}\nfrozen 0\n",
                    encoding="ascii",
                )
            return _FakeHostProcess(
                normalized,
                stdout=workload_stdout,
                stderr=workload_stderr,
                returncode=workload_returncode,
                communicate_effects=list(workload_communicate_effects),
            )
        if executable == systemctl_path and "show" in normalized:
            unit = normalized[-1]
            state = (
                states[min(status_index, len(states) - 1)]
                if states
                else default_state
            )
            status_index += 1
            return _FakeHostProcess(
                normalized,
                stdout=_synthetic_transient_status(state, unit),
            )
        return _FakeHostProcess(normalized)

    monkeypatch.setattr(controller_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        controller_module,
        "_kill_process_group",
        lambda pid: killpg_calls.append(pid),
        raising=False,
    )
    runner = controller_module.SubprocessRunner(timeout_seconds=0.5)
    # These fixed test paths make the host-control boundary and cgroup
    # observation deterministic without asking the test host to run systemd.
    runner._systemd_run_path = systemd_run_path
    runner._systemctl_path = systemctl_path
    runner._cgroup_root = cgroup_root
    runner._controller_unit = "honeybuy-release-controller.service"
    return runner, calls, killpg_calls, cgroup_root


def test_workload_runner_uses_unique_transient_systemd_cgroup_and_observes_empty(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, calls, killpg_calls, cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=0,
    )

    completed = runner.run(
        ("/candidate/.venv/bin/python", "-m", "honeybuy_tg", "migrate"),
        cwd=Path("/var/empty/honeybuy-healthcheck"),
        uid=10001,
        gid=10002,
        env={"DATABASE_PATH": "/var/lib/honeybuy-tg/honeybuy.sqlite3"},
    )

    assert completed.returncode == 0
    transient_calls = [
        argv for argv in calls if Path(argv[0]) == Path("/test-tools/systemd-run")
    ]
    assert len(transient_calls) == 1
    launch = transient_calls[0]
    unit = _systemd_option(launch, "--unit")
    assert unit is not None
    assert unit.startswith("honeybuy-release-workload-")
    assert unit.endswith(".service")
    assert "--wait" in launch
    assert "--collect" not in launch
    assert "--service-type=exec" in launch
    joined = "\n".join(launch)
    assert "KillMode=control-group" in joined
    assert "SendSIGKILL=yes" in joined
    assert "RuntimeMaxSec=" in joined
    assert "TimeoutStopSec=" in joined
    assert "BindsTo=honeybuy-release-controller.service" in joined
    assert "After=honeybuy-release-controller.service" in joined
    separator = launch.index("--")
    assert launch[separator + 1 :] == (
        "/candidate/.venv/bin/python",
        "-m",
        "honeybuy_tg",
        "migrate",
    )
    assert (cgroup_root / "system.slice" / unit / "cgroup.events").is_file()
    show_indices = [
        index
        for index, argv in enumerate(calls)
        if Path(argv[0]) == Path("/test-tools/systemctl") and "show" in argv
    ]
    assert show_indices and calls.index(launch) < show_indices[0]
    assert killpg_calls == []


def test_retained_terminal_unit_after_cgroup_collection_is_accepted(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, calls, killpg_calls, cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=None,
        states=(
            _SyntheticTransientState(
                load_state="loaded",
                active_state="inactive",
                sub_state="dead",
                result="success",
                exec_main_code="exited",
                exit_status=0,
                control_group="",
            ),
        ),
    )
    payload = ("/candidate/.venv/bin/python", "-c", "pass")

    completed = runner.run(payload, uid=10001, gid=10002, env={})

    assert completed.returncode == 0
    launch = next(
        call for call in calls if Path(call[0]) == Path("/test-tools/systemd-run")
    )
    unit = _systemd_option(launch, "--unit")
    assert unit is not None
    assert not (cgroup_root / "system.slice" / unit).exists()
    assert not any(
        Path(call[0]) == Path("/test-tools/systemctl")
        and call[1] in {"stop", "kill", "reset-failed"}
        for call in calls
    )
    assert killpg_calls == []


def test_failed_retained_terminal_unit_is_reset_without_requiring_unload(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, calls, killpg_calls, cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=None,
        states=(
            _SyntheticTransientState(
                load_state="loaded",
                active_state="failed",
                sub_state="failed",
                result="exit-code",
                exec_main_code="exited",
                exit_status=23,
                control_group="",
            ),
            _SyntheticTransientState(control_group=""),
        ),
        workload_returncode=1,
    )
    payload = ("/candidate/.venv/bin/python", "-c", "raise SystemExit(23)")

    completed = runner.run(payload, uid=10001, gid=10002, env={})

    assert completed.returncode != 0
    launch = next(
        call for call in calls if Path(call[0]) == Path("/test-tools/systemd-run")
    )
    unit = _systemd_option(launch, "--unit")
    assert unit is not None
    assert not (cgroup_root / "system.slice" / unit).exists()
    assert sum(
        Path(call[0]) == Path("/test-tools/systemctl")
        and call[1] == "reset-failed"
        for call in calls
    ) == 1
    assert not any(
        Path(call[0]) == Path("/test-tools/systemctl")
        and call[1] in {"stop", "kill"}
        for call in calls
    )
    assert killpg_calls == []


def test_manager_enforced_runtime_limit_is_reported_as_timeout_expired(
    controller_module,
    tmp_path,
    monkeypatch,
):
    payload = (
        "/candidate/.venv/bin/python",
        "-c",
        "import time; time.sleep(30)",
    )
    runner, calls, killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=None,
        states=(
            _SyntheticTransientState(
                active_state="failed",
                sub_state="failed",
                result="timeout",
                exec_main_code="killed",
                exit_status=15,
                control_group="",
            ),
            _SyntheticTransientState(control_group=""),
        ),
        workload_returncode=1,
        workload_stdout=b"captured before manager timeout",
        workload_stderr=b"runtime limit reached",
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        runner.run(payload, uid=10001, gid=10002, env={})

    assert raised.value.cmd == payload
    assert raised.value.timeout == 0.5
    assert raised.value.output == b"captured before manager timeout"
    assert raised.value.stderr == b"runtime limit reached"
    systemctl_actions = [
        call[1]
        for call in calls
        if Path(call[0]) == Path("/test-tools/systemctl")
    ]
    assert systemctl_actions.count("reset-failed") == 1
    assert not {"stop", "kill"}.intersection(systemctl_actions)
    assert killpg_calls == []


def test_manager_timeout_with_successful_client_status_fails_closed(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, calls, killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=None,
        states=(
            _SyntheticTransientState(
                active_state="failed",
                sub_state="failed",
                result="timeout",
                exec_main_code="killed",
                exit_status=15,
                control_group="",
            ),
            _SyntheticTransientState(control_group=""),
        ),
        workload_returncode=0,
    )

    with pytest.raises(controller_module.ContainmentFailure):
        runner.run(
            ("/candidate/.venv/bin/python", "-c", "pass"),
            uid=10001,
            gid=10002,
            env={},
        )

    systemctl_actions = [
        call[1]
        for call in calls
        if Path(call[0]) == Path("/test-tools/systemctl")
    ]
    assert systemctl_actions.count("reset-failed") == 1
    assert not {"stop", "kill"}.intersection(systemctl_actions)
    assert killpg_calls == []


def test_timeout_preserves_original_error_for_retained_terminal_unit_without_cgroup(
    controller_module,
    tmp_path,
    monkeypatch,
):
    payload = ("/candidate/.venv/bin/python", "-c", "pass")
    original_timeout = subprocess.TimeoutExpired(
        ("/test-tools/systemd-run",),
        0.5,
        output=b"partial output",
        stderr=b"deadline exceeded",
    )
    runner, calls, killpg_calls, cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=None,
        states=(_SyntheticTransientState(control_group=""),),
        workload_communicate_effects=(original_timeout, (b"", b"")),
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        runner.run(payload, uid=10001, gid=10002, env={})

    assert raised.value.cmd == payload
    assert raised.value.timeout == 0.5
    assert raised.value.output == b"partial output"
    assert raised.value.stderr == b"deadline exceeded"
    launch = next(
        call for call in calls if Path(call[0]) == Path("/test-tools/systemd-run")
    )
    unit = _systemd_option(launch, "--unit")
    assert unit is not None
    assert not (cgroup_root / "system.slice" / unit).exists()
    assert any(
        Path(call[0]) == Path("/test-tools/systemctl") and call[1] == "stop"
        for call in calls
    )
    assert not any(
        Path(call[0]) == Path("/test-tools/systemctl") and call[1] == "kill"
        for call in calls
    )
    assert killpg_calls == []


@pytest.mark.parametrize(
    ("state", "populated"),
    [
        pytest.param(
            _SyntheticTransientState(
                active_state="active",
                sub_state="running",
                control_group="",
            ),
            None,
            id="active-running-without-cgroup",
        ),
        pytest.param(
            _SyntheticTransientState(
                active_state="inactive",
                sub_state="failed",
            ),
            0,
            id="mixed-inactive-failed",
        ),
        pytest.param(
            _SyntheticTransientState(
                active_state="failed",
                sub_state="dead",
            ),
            0,
            id="mixed-failed-dead",
        ),
        pytest.param(
            _SyntheticTransientState(control_group=""),
            0,
            id="empty-control-group-with-cgroup",
        ),
        pytest.param(
            _SyntheticTransientState(control_group="/system.slice/{unit}"),
            None,
            id="expected-control-group-without-cgroup",
        ),
        pytest.param(
            _SyntheticTransientState(
                control_group="/system.slice/foreign.service",
            ),
            None,
            id="foreign-control-group-without-cgroup",
        ),
    ],
)
def test_contradictory_transient_unit_and_cgroup_evidence_fails_closed(
    controller_module,
    tmp_path,
    monkeypatch,
    state,
    populated,
):
    runner, _calls, killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=populated,
        states=(state,),
    )

    with pytest.raises(controller_module.ContainmentFailure):
        runner.run(
            ("/candidate/.venv/bin/python", "-c", "pass"),
            uid=10001,
            gid=10002,
            env={},
        )

    assert killpg_calls == []


def test_nonempty_transient_cgroup_is_a_fatal_containment_failure(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, _calls, killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=1,
    )
    containment_failure = getattr(controller_module, "ContainmentFailure", None)
    assert isinstance(containment_failure, type)

    with pytest.raises(containment_failure):
        runner.run(
            ("/candidate/.venv/bin/python", "-m", "honeybuy_tg", "migrate"),
            cwd=Path("/var/empty/honeybuy-healthcheck"),
            uid=10001,
            gid=10002,
            env={"DATABASE_PATH": "/var/lib/honeybuy-tg/honeybuy.sqlite3"},
        )

    assert killpg_calls == []


def test_containment_failure_bypasses_normal_command_failure_handling(
    controller_module,
    tmp_path,
):
    containment_failure = getattr(controller_module, "ContainmentFailure", None)
    assert isinstance(containment_failure, type)
    config = _activation_config(controller_module, tmp_path)

    class EscapedWorkloadRunner:
        def run(self, *_args, **_kwargs):
            raise containment_failure("workload cgroup did not empty")

    controller = controller_module.ReleaseController(
        config=config,
        runner=EscapedWorkloadRunner(),
        http=FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), []),
    )

    with pytest.raises(containment_failure):
        controller._run_command(
            ("/candidate/.venv/bin/python", "-m", "honeybuy_tg", "migrate"),
            uid=config.runtime_uid,
            gid=config.runtime_gid,
            env={"DATABASE_PATH": str(config.database_path)},
        )


def test_post_launch_workload_communicate_error_attempts_cleanup_and_is_fatal(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, calls, _killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=0,
    )
    original_popen = controller_module.subprocess.Popen

    def crashing_workload_popen(argv, **kwargs):
        process = original_popen(argv, **kwargs)
        if Path(argv[0]) == Path("/test-tools/systemd-run"):
            process.communicate_effects.append(OSError("systemd-run client crashed"))
        return process

    monkeypatch.setattr(
        controller_module.subprocess,
        "Popen",
        crashing_workload_popen,
    )
    config = _activation_config(controller_module, tmp_path)
    controller = controller_module.ReleaseController(
        config=config,
        runner=runner,
        http=FakeHttp(None, []),
    )

    with pytest.raises(controller_module.ContainmentFailure):
        controller._run_command(
            ("/candidate/.venv/bin/python", "-m", "honeybuy_tg", "migrate"),
            cwd=Path("/var/empty/honeybuy-healthcheck"),
            uid=10001,
            gid=10002,
            env={"DATABASE_PATH": "/var/lib/honeybuy-tg/honeybuy.sqlite3"},
        )

    assert any(
        Path(call[0]) == Path("/test-tools/systemctl") and "stop" in call
        for call in calls
    )


@pytest.mark.parametrize(
    "status_effect",
    [
        pytest.param(OSError("systemctl show client crashed"), id="communicate-oserror"),
        pytest.param(None, id="missing-returncode"),
    ],
)
def test_transient_status_uncertainty_attempts_cleanup_and_is_fatal(
    controller_module,
    tmp_path,
    monkeypatch,
    status_effect,
):
    runner, calls, _killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=0,
    )
    original_popen = controller_module.subprocess.Popen
    injected = False

    def crashing_status_popen(argv, **kwargs):
        nonlocal injected
        process = original_popen(argv, **kwargs)
        normalized = tuple(str(argument) for argument in argv)
        if (
            not injected
            and Path(normalized[0]) == Path("/test-tools/systemctl")
            and "show" in normalized
        ):
            injected = True
            if isinstance(status_effect, BaseException):
                process.communicate_effects.append(status_effect)
            else:
                process.returncode = None
        return process

    monkeypatch.setattr(
        controller_module.subprocess,
        "Popen",
        crashing_status_popen,
    )
    config = _activation_config(controller_module, tmp_path)
    controller = controller_module.ReleaseController(
        config=config,
        runner=runner,
        http=FakeHttp(None, []),
    )

    with pytest.raises(controller_module.ContainmentFailure):
        controller._run_command(
            ("/candidate/.venv/bin/python", "-c", "pass"),
            uid=10001,
            gid=10002,
            env={},
        )

    assert injected
    assert any(
        Path(call[0]) == Path("/test-tools/systemctl") and "stop" in call
        for call in calls
    )


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(OSError("systemctl client crashed"), id="communicate-oserror"),
        pytest.param(None, id="missing-returncode"),
    ],
)
def test_managed_service_control_uncertainty_is_fatal(
    controller_module,
    tmp_path,
    monkeypatch,
    failure,
):
    systemctl_path = Path("/test-tools/systemctl")

    def fake_popen(argv, **_kwargs):
        process = _FakeHostProcess(argv)
        if isinstance(failure, BaseException):
            process.communicate_effects.append(failure)
        else:
            process.returncode = None
        return process

    monkeypatch.setattr(controller_module.subprocess, "Popen", fake_popen)
    runner = controller_module.SubprocessRunner(
        timeout_seconds=0.5,
        systemctl_path=systemctl_path,
        cgroup_root=tmp_path / "synthetic-cgroup",
        managed_service="honeybuy-tg.service",
    )
    config = _activation_config(controller_module, tmp_path)
    controller = controller_module.ReleaseController(
        config=config,
        runner=runner,
        http=FakeHttp(None, []),
    )

    with pytest.raises(controller_module.ContainmentFailure):
        controller._run_control_command(
            (str(systemctl_path), "start", "honeybuy-tg.service")
        )


def test_timeout_cleanup_communicate_error_is_fatal_after_stop_attempt(
    controller_module,
    tmp_path,
    monkeypatch,
):
    runner, calls, _killpg_calls, _cgroup_root = _fake_transient_host(
        controller_module,
        tmp_path,
        monkeypatch,
        populated=0,
    )
    original_popen = controller_module.subprocess.Popen

    def crashing_cleanup_popen(argv, **kwargs):
        process = original_popen(argv, **kwargs)
        if Path(argv[0]) == Path("/test-tools/systemd-run"):
            process.communicate_effects.extend(
                [
                    subprocess.TimeoutExpired(tuple(argv), 0.5),
                    OSError("systemd-run client crashed during reap"),
                ]
            )
        return process

    monkeypatch.setattr(
        controller_module.subprocess,
        "Popen",
        crashing_cleanup_popen,
    )
    config = _activation_config(controller_module, tmp_path)
    controller = controller_module.ReleaseController(
        config=config,
        runner=runner,
        http=FakeHttp(None, []),
    )

    with pytest.raises(controller_module.ContainmentFailure):
        controller._run_command(
            ("/candidate/.venv/bin/python", "-c", "pass"),
            uid=10001,
            gid=10002,
            env={},
        )

    assert any(
        Path(call[0]) == Path("/test-tools/systemctl") and "stop" in call
        for call in calls
    )


@pytest.mark.parametrize("close_standard_streams", [True, False])
def test_linux_transient_runner_contains_setsid_descendants(
    controller_module,
    close_standard_streams,
):
    if sys.platform != "linux":
        pytest.skip("requires Linux systemd with a unified cgroup hierarchy")
    if os.geteuid() != 0:
        pytest.skip("requires root to create a transient system service")
    if not Path("/run/systemd/system").is_dir():
        pytest.skip("requires systemd as PID 1")
    if shutil.which("systemd-run") is None or shutil.which("systemctl") is None:
        pytest.skip("requires systemd-run and systemctl")

    system_python_path = Path(sys.executable).resolve(strict=True)
    protect_home_hidden_roots = (Path("/home"), Path("/root"), Path("/run/user"))
    hidden_root = next(
        (
            root
            for root in protect_home_hidden_roots
            if system_python_path.is_relative_to(root)
        ),
        None,
    )
    assert hidden_root is None, (
        "resolved test interpreter is hidden by ProtectHome=yes: "
        f"sys.executable={sys.executable!r}, resolved={str(system_python_path)!r}, "
        f"hidden_root={str(hidden_root)!r}"
    )
    nobody = pwd.getpwnam("nobody")
    work_root = Path(tempfile.mkdtemp(prefix="honeybuy-containment-", dir="/run"))
    work_root.chmod(0o755)
    runtime_dir = work_root / "runtime"
    scratch_dir = work_root / "scratch"
    destination_dir = work_root / "destination"
    empty_work_dir = work_root / "empty"
    runtime_dir.mkdir(mode=0o700)
    os.chown(runtime_dir, nobody.pw_uid, nobody.pw_gid)
    scratch_dir.mkdir(mode=0o711)
    destination_dir.mkdir(mode=0o700)
    empty_work_dir.mkdir(mode=0o755)
    trigger = runtime_dir / "release"
    mutation = runtime_dir / "escaped-mutation"
    child_pid = runtime_dir / "child.pid"
    source_database = runtime_dir / "source.sqlite3"
    cloned_database = destination_dir / "clone.sqlite3"
    result_path = work_root / "runner-result.json"
    unit = (
        f"honeybuy-controller-test-{os.getpid()}-{int(close_standard_streams)}-"
        f"{time.time_ns()}.service"
    )
    stream_expression = "subprocess.DEVNULL" if close_standard_streams else "None"
    child_code = textwrap.dedent(
        f"""
        import os
        import pathlib
        import time

        child_pid = pathlib.Path({str(child_pid)!r})
        trigger = pathlib.Path({str(trigger)!r})
        child_pid.write_text(str(os.getpid()), encoding="ascii")
        while not trigger.exists():
            time.sleep(0.01)
        pathlib.Path({str(mutation)!r}).write_text("escaped", encoding="ascii")
        """
    )
    parent_code = textwrap.dedent(
        f"""
        import pathlib
        import subprocess
        import time

        child_pid = pathlib.Path({str(child_pid)!r})
        system_python_path = {str(system_python_path)!r}
        subprocess.Popen(
            [system_python_path, "-I", "-c", {child_code!r}],
            start_new_session=True,
            stdout={stream_expression},
            stderr={stream_expression},
        )
        deadline = time.monotonic() + 1.0
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not child_pid.exists():
            raise RuntimeError("descendant did not report its pid")
        """
    )
    harness = textwrap.dedent(
        f"""
        import importlib.util
        import json
        import os
        import pathlib
        import sqlite3
        import subprocess
        import sys
        import time

        unit = {unit!r}
        spec = importlib.util.spec_from_file_location(
            "honeybuy_release_controller_integration", {str(CONTROLLER_PATH)!r}
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        system_python_path = pathlib.Path({str(system_python_path)!r})

        shown = subprocess.run(
            [
                "/usr/bin/systemctl",
                "show",
                "--no-pager",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=MainPID",
                unit,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
            text=True,
        )
        fields = dict(
            line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line
        )
        assert shown.returncode == 0
        assert fields == {{
            "LoadState": "loaded",
            "ActiveState": "active",
            "MainPID": str(os.getpid()),
        }}

        runner = module.SubprocessRunner(
            timeout_seconds=2.0,
            management_timeout_seconds=2.0,
            controller_unit=unit,
        )
        descendant = runner.run(
            (str(system_python_path), "-I", "-c", {parent_code!r}),
            uid={nobody.pw_uid},
            gid={nobody.pw_gid},
            env={{}},
            writable_paths=(pathlib.Path({str(runtime_dir)!r}),),
        )
        assert descendant.returncode == 0, (
            "descendant workload failed: "
            f"returncode={{descendant.returncode}}, "
            f"stdout={{descendant.stdout!r}}, stderr={{descendant.stderr!r}}"
        )
        escaped_pid = int(pathlib.Path({str(child_pid)!r}).read_text(encoding="ascii"))
        pathlib.Path({str(trigger)!r}).touch()
        descendant_gone = False
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                os.kill(escaped_pid, 0)
            except ProcessLookupError:
                descendant_gone = True
                break
            time.sleep(0.01)
        assert descendant_gone
        assert not pathlib.Path({str(mutation)!r}).exists()

        nonzero = runner.run(
            (str(system_python_path), "-I", "-c", "raise SystemExit(23)"),
            uid={nobody.pw_uid},
            gid={nobody.pw_gid},
            env={{}},
        )
        assert nonzero.returncode != 0

        timed_out = False
        try:
            runner.run(
                (
                    str(system_python_path),
                    "-I",
                    "-c",
                    "import time; time.sleep(30)",
                ),
                uid={nobody.pw_uid},
                gid={nobody.pw_gid},
                env={{}},
            )
        except subprocess.TimeoutExpired:
            timed_out = True
        assert timed_out

        source_database = pathlib.Path({str(source_database)!r})
        with sqlite3.connect(source_database) as database:
            database.execute("CREATE TABLE probe (value TEXT NOT NULL)")
            database.execute("INSERT INTO probe VALUES ('runtime clone')")
            database.commit()
        source_database.chmod(0o600)
        os.chown(source_database, {nobody.pw_uid}, {nobody.pw_gid})
        config = module.ControllerConfig(
            database_path=source_database,
            scratch_dir=pathlib.Path({str(scratch_dir)!r}),
            empty_work_dir=pathlib.Path({str(empty_work_dir)!r}),
            system_python_path=system_python_path,
            runtime_uid={nobody.pw_uid},
            runtime_gid={nobody.pw_gid},
            test_mode=False,
        )
        controller = module.ReleaseController(config=config, runner=runner, http=object())
        cloned_database = pathlib.Path({str(cloned_database)!r})
        controller._clone_database(source_database, cloned_database)
        with sqlite3.connect(cloned_database) as database:
            assert database.execute("SELECT value FROM probe").fetchone() == (
                "runtime clone",
            )
        assert cloned_database.stat().st_uid == 0
        assert cloned_database.stat().st_gid == 0
        assert cloned_database.stat().st_mode & 0o777 == 0o600

        pathlib.Path({str(result_path)!r}).write_text(
            json.dumps(
                {{
                    "main_pid": os.getpid(),
                    "descendant": "contained",
                    "nonzero": nonzero.returncode,
                    "timeout": timed_out,
                    "database_clone": "ok",
                }},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        """
    )
    escaped_pid = None
    try:
        completed = subprocess.run(
            (
                "systemd-run",
                "--quiet",
                "--wait",
                "--pipe",
                f"--unit={unit}",
                "--service-type=exec",
                "--property=Type=exec",
                "--property=KillMode=control-group",
                "--property=SendSIGKILL=yes",
                "--property=TimeoutStartSec=15s",
                "--property=TimeoutStopSec=5s",
                "--",
                str(system_python_path),
                "-I",
                "-c",
                harness,
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if child_pid.exists():
            escaped_pid = int(child_pid.read_text(encoding="ascii"))
        assert completed.returncode == 0, (
            "containment harness failed: "
            f"returncode={completed.returncode}, "
            f"stdout={completed.stdout.decode('utf-8', 'replace')!r}, "
            f"stderr={completed.stderr.decode('utf-8', 'replace')!r}"
        )
        result = _read_json(result_path)
        assert result["main_pid"] > 1
        assert result["descendant"] == "contained"
        assert result["nonzero"] != 0
        assert result["timeout"] is True
        assert result["database_clone"] == "ok"
        assert not mutation.exists()
        if escaped_pid is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(escaped_pid, 0)
            escaped_pid = None
    finally:
        subprocess.run(
            ("systemctl", "stop", unit),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        subprocess.run(
            ("systemctl", "reset-failed", unit),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if escaped_pid is not None:
            try:
                os.kill(escaped_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        shutil.rmtree(work_root)


def _create_pending_bootstrap(config) -> Path:
    _create_bootstrap_database(config.database_path)
    release = _create_ready_release(config, TARGET_SHA, make_current=True)
    _write_deployed_state(
        config,
        TARGET_SHA,
        install_control_plane=False,
    )
    _write_bootstrap_journal(config)
    return release


def test_controller_declares_dedicated_one_time_bootstrap_state(
    controller_module,
):
    fields = controller_module.ControllerConfig.__dataclass_fields__

    assert "bootstrap_journal_path" in fields
    assert fields["bootstrap_journal_path"].default == Path(
        "/var/lib/honeybuy-release-controller/bootstrap-journal.json"
    )
    assert controller_module.ResultStatus.BOOTSTRAPPED.value == "bootstrapped"


def test_host_systemd_allowlist_accepts_only_exact_bootstrap_state_query(
    controller_module,
    tmp_path,
):
    systemctl_path = Path("/test-tools/systemctl")
    runner = controller_module.SubprocessRunner(
        timeout_seconds=1,
        systemctl_path=systemctl_path,
        cgroup_root=tmp_path / "cgroup",
    )
    query = (
        str(systemctl_path),
        "show",
        "--no-pager",
        "--property=LoadState",
        "--property=ActiveState",
        "--property=SubState",
        "honeybuy-tg.service",
    )

    assert runner._valid_service_control_argv(query)
    assert not runner._valid_service_control_argv(
        (*query[:-1], "attacker.service")
    )
    assert not runner._valid_service_control_argv(
        (*query[:-1], "--property=Environment", query[-1])
    )


def test_pristine_copy_host_bootstrap_is_authenticated_durable_and_data_preserving(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    app_root = config.releases_dir.parent
    (app_root / "legacy-bot.py").write_text("# ignored legacy copy\n")
    (app_root / ".venv/bin").mkdir(parents=True)
    (app_root / ".venv/bin/python").write_bytes(b"legacy interpreter")
    release = config.releases_dir / TARGET_SHA
    staging = config.releases_dir / f".{TARGET_SHA}.tmp"
    events: list[str] = []
    probe = BootstrapProbe(config)
    runner = FakeRunner(
        _trusted_scenario(config, ancestor_returncode=1, call_hook=probe),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)
    controller = _controller(controller_module, config, runner, http)
    real_atomic_write_json = controller_module._atomic_write_json
    real_fsync_directory = controller_module._fsync_directory
    real_mkdir = Path.mkdir
    real_switch_link = controller._switch_link
    real_write_deployed_sha = controller._write_deployed_sha
    release_mutation_state: list[tuple[str | None, bool]] = []

    def recording_atomic_write_json(path, payload, *, mode):
        real_atomic_write_json(path, payload, mode=mode)
        if path == config.bootstrap_journal_path:
            events.append(f"bootstrap-journal:{payload.get('phase')}")

    def recording_fsync_directory(path):
        real_fsync_directory(path)
        if path == config.bootstrap_journal_path.parent:
            phase = (
                _read_json(config.bootstrap_journal_path).get("phase")
                if config.bootstrap_journal_path.is_file()
                else None
            )
            events.append(f"bootstrap-fsync:{phase}")

    def recording_mkdir(path, *args, **kwargs):
        if Path(path) == staging:
            phase = (
                _read_json(config.bootstrap_journal_path).get("phase")
                if config.bootstrap_journal_path.is_file()
                else None
            )
            release_mutation_state.append(
                (phase, "bootstrap-fsync:intent" in events)
            )
            events.append("bootstrap:release-mutation")
        return real_mkdir(path, *args, **kwargs)

    def recording_switch_link(link, target):
        real_switch_link(link, target)
        if link == config.current_link:
            events.append("bootstrap:current-linked")

    def recording_write_deployed_sha(sha):
        real_write_deployed_sha(sha)
        events.append("bootstrap:state-written")

    monkeypatch.setattr(
        controller_module,
        "_atomic_write_json",
        recording_atomic_write_json,
    )
    monkeypatch.setattr(
        controller_module,
        "_fsync_directory",
        recording_fsync_directory,
    )
    monkeypatch.setattr(Path, "mkdir", recording_mkdir)
    monkeypatch.setattr(controller, "_switch_link", recording_switch_link)
    monkeypatch.setattr(
        controller,
        "_write_deployed_sha",
        recording_write_deployed_sha,
    )

    result = controller.deploy()

    _assert_status(result, "bootstrapped")
    assert result.sha == TARGET_SHA
    assert release_mutation_state == [("intent", True)]
    journal = _read_json(config.bootstrap_journal_path)
    assert journal == {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "phase": "awaiting_service",
        "phases": list(BOOTSTRAP_PHASES),
    }
    assert config.current_link.resolve() == release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert controller._ready_release_is_safe(release, TARGET_SHA)
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert list(config.database_backup_dir.iterdir()) == []
    assert probe.service_state_checks >= 1
    assert probe.healthchecks == 1
    assert probe.health_journal_phases == ["release_prepared"]
    command_kinds = [_command_kind(call) for call in runner.calls]
    main_fetches = [
        call
        for call in runner.calls
        if _command_kind(call) == "fetch" and call.argv[-1] == "refs/heads/main"
    ]
    assert len(main_fetches) == 2
    fetched_commit_resolutions = [
        call
        for call in runner.calls
        if _command_kind(call) == "rev-parse"
        and call.argv[-1] == "FETCH_HEAD^{commit}"
    ]
    assert len(fetched_commit_resolutions) == 3
    assert "cat-file" in command_kinds
    assert "verify-commit" in command_kinds
    assert "merge-base" not in command_kinds
    assert "migrate" not in command_kinds
    assert not any(
        kind == "systemctl" and call.argv[1] in {"start", "stop"}
        for kind, call in zip(command_kinds, runner.calls, strict=True)
    )
    resolve_events = [
        index
        for index, event in enumerate(events)
        if event == "run:rev-parse"
    ]
    fetch_events = [
        index for index, event in enumerate(events) if event == "run:fetch"
    ]
    assert len(resolve_events) == 5
    assert len(fetch_events) == 3
    assert events.index("run:healthcheck") < fetch_events[-1] < resolve_events[-1]
    assert events.index("bootstrap:current-linked") > resolve_events[-1]
    assert events.index("bootstrap:state-written") > events.index(
        "bootstrap:current-linked"
    )
    assert len(http.calls) == 2
    _assert_workflow_request(http.calls[0][0])
    assert http.calls[1][0].endswith(f"/commits/{TARGET_SHA}/pulls")
    intent_index = events.index("bootstrap-journal:intent")
    assert events.index("http:workflow") < intent_index
    assert events.index("run:cat-file") < intent_index
    assert events.index("run:verify-commit") < intent_index


def test_pristine_bootstrap_accepts_only_strict_rebase_signature_evidence(
    controller_module,
    tmp_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    events: list[str] = []
    probe = BootstrapProbe(config)
    rebase_hook = _rebase_verification_hook(candidate_verify_returncode=0)

    def combined_hook(
        call: RunCall,
        occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        return probe(call, occurrence) or rebase_hook(call, occurrence)

    runner = FakeRunner(
        _scenario(
            config,
            ancestor_returncode=1,
            raw_commits={
                TARGET_SHA: _raw_commit(),
                PR_HEAD_SHA: _raw_commit("ssh"),
            },
            call_hook=combined_hook,
        ),
        events,
    )
    http = RebaseHttp(
        _workflow_payload(_workflow_run(TARGET_SHA)),
        [_associated_pull()],
        events,
    )

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "bootstrapped")
    assert [
        call.argv[-1]
        for call in runner.calls
        if _command_kind(call) == "cat-file"
    ] == [TARGET_SHA, PR_HEAD_SHA]
    assert len(http.calls) == 2
    assert http.calls[1][0].endswith(f"/commits/{TARGET_SHA}/pulls")
    assert not any(_command_kind(call) == "merge-base" for call in runner.calls)


@pytest.mark.parametrize(
    ("existing_evidence", "expected_status"),
    [
        pytest.param("malformed-deployed-state", "rejected", id="malformed-state"),
        pytest.param("release", "rejected", id="nonempty-releases"),
        pytest.param("current-link", "rejected", id="existing-current-link"),
        pytest.param("previous-link", "rejected", id="existing-previous-link"),
        pytest.param("receipt", "rejected", id="nonempty-receipts"),
        pytest.param("quarantine", "rejected", id="nonempty-quarantine"),
        pytest.param(
            "activation-journal",
            "intervention_required",
            id="activation-journal",
        ),
    ],
)
def test_established_or_partial_state_is_never_treated_as_pristine_bootstrap(
    controller_module,
    tmp_path,
    existing_evidence,
    expected_status,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    if existing_evidence == "malformed-deployed-state":
        config.deployed_state_path.write_text("not-a-sha\n")
    elif existing_evidence in {"release", "current-link", "previous-link"}:
        release = _create_ready_release(config, LAST_SHA)
        if existing_evidence == "current-link":
            config.current_link.symlink_to(release)
        elif existing_evidence == "previous-link":
            config.previous_link.symlink_to(release)
    elif existing_evidence == "receipt":
        (config.deployment_receipts_dir / f"{LAST_SHA}.json").write_text("{}\n")
    elif existing_evidence == "quarantine":
        (config.quarantine_dir / f"{LAST_SHA}.json").write_text("{}\n")
    else:
        config.deployment_journal_path.write_text("{}\n")
    events: list[str] = []
    runner = FakeRunner(
        _scenario(config, call_hook=BootstrapProbe(config)),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, expected_status)
    assert "bootstrap" in result.reason.casefold()
    assert not config.bootstrap_journal_path.exists()
    assert not (config.releases_dir / TARGET_SHA).exists()
    assert not any(
        _command_kind(call) in {"ls-tree", "archive", "uv-sync", "import-smoke"}
        for call in runner.calls
    )
    assert http.calls == []


@pytest.mark.parametrize(
    ("load_state", "active_state", "reason_fragment"),
    [
        pytest.param("loaded", "active", "inactive", id="legacy-service-active"),
        pytest.param("not-found", "inactive", "loaded", id="unit-not-loaded"),
        pytest.param("loaded", "failed", "inactive", id="unit-failed"),
    ],
)
def test_pristine_bootstrap_requires_managed_unit_definitively_loaded_and_inactive(
    controller_module,
    tmp_path,
    load_state,
    active_state,
    reason_fragment,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    probe = BootstrapProbe(
        config,
        load_state=load_state,
        active_state=active_state,
    )
    events: list[str] = []
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert reason_fragment in result.reason.casefold()
    assert probe.service_state_checks == 1
    assert not config.bootstrap_journal_path.exists()
    assert not (config.releases_dir / TARGET_SHA).exists()
    assert http.calls == []


@pytest.mark.parametrize(
    "database_state",
    [
        pytest.param("missing", id="missing"),
        pytest.param("old-schema", id="old-schema"),
        pytest.param("new-schema", id="new-schema"),
        pytest.param("corrupt", id="corrupt"),
        pytest.param("symlink", id="symlink"),
    ],
)
def test_pristine_bootstrap_requires_safe_schema_compatible_existing_database(
    controller_module,
    tmp_path,
    database_state,
):
    config = _bootstrap_config(controller_module, tmp_path)
    if database_state == "old-schema":
        _create_bootstrap_database(config.database_path, schema_version=1)
    elif database_state == "new-schema":
        _create_bootstrap_database(config.database_path, schema_version=3)
    elif database_state == "corrupt":
        config.database_path.write_bytes(b"not sqlite")
        config.database_path.chmod(0o600)
    elif database_state == "symlink":
        outside = tmp_path / "outside.sqlite3"
        _create_bootstrap_database(outside)
        config.database_path.symlink_to(outside)
    probe = BootstrapProbe(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "database" in result.reason.casefold()
    assert not config.bootstrap_journal_path.exists()
    assert not (config.releases_dir / TARGET_SHA).exists()
    assert not any(
        _command_kind(call) in {"ls-tree", "archive", "uv-sync", "import-smoke"}
        for call in runner.calls
    )
    assert http.calls == []


def test_ancestry_is_still_mandatory_outside_pristine_bootstrap(
    controller_module,
    tmp_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    events: list[str] = []
    runner = FakeRunner(_trusted_scenario(config, ancestor_returncode=1), events)
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "ancestor" in result.reason.casefold()
    assert sum(
        _command_kind(call) == "merge-base" for call in runner.calls
    ) == 1
    assert not config.bootstrap_journal_path.exists()


@pytest.mark.parametrize("crash_phase", BOOTSTRAP_PHASES[:4])
def test_bootstrap_crash_phases_resume_without_mistaking_partial_state_as_pristine(
    controller_module,
    tmp_path,
    monkeypatch,
    crash_phase,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    first_events: list[str] = []
    first_runner = FakeRunner(
        _trusted_scenario(config, call_hook=BootstrapProbe(config)),
        first_events,
    )
    first_controller = _controller(
        controller_module,
        config,
        first_runner,
        _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), first_events),
    )
    real_atomic_write_json = controller_module._atomic_write_json

    def crash_after_durable_phase(path, payload, *, mode):
        real_atomic_write_json(path, payload, mode=mode)
        if (
            path == config.bootstrap_journal_path
            and payload.get("phase") == crash_phase
        ):
            raise SimulatedControllerCrash

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(
            controller_module,
            "_atomic_write_json",
            crash_after_durable_phase,
        )
        with pytest.raises(SimulatedControllerCrash):
            first_controller.deploy()

    crashed_journal = _read_json(config.bootstrap_journal_path)
    assert crashed_journal["candidate_sha"] == TARGET_SHA
    assert crashed_journal["phase"] == crash_phase
    assert crashed_journal["phases"] == list(
        BOOTSTRAP_PHASES[: BOOTSTRAP_PHASES.index(crash_phase) + 1]
    )
    release = config.releases_dir / TARGET_SHA
    expected_release = crash_phase != "intent"
    expected_link = crash_phase in {"current_linked", "state_written"}
    expected_state = crash_phase == "state_written"
    assert release.exists() is expected_release
    assert config.current_link.exists() is expected_link
    assert config.deployed_state_path.exists() is expected_state

    recovery_events: list[str] = []
    recovery_runner = FakeRunner(
        _scenario(config, call_hook=BootstrapProbe(config)),
        recovery_events,
    )
    recovery = _controller(
        controller_module,
        config,
        recovery_runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), recovery_events),
    ).deploy()

    _assert_status(recovery, "bootstrapped")
    assert config.current_link.resolve() == release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert _read_json(config.bootstrap_journal_path)["phase"] == "awaiting_service"
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert not any(_command_kind(call) == "migrate" for call in recovery_runner.calls)
    assert not any(
        _command_kind(call) == "systemctl"
        and len(call.argv) > 1
        and call.argv[1] in {"start", "stop", "restart", "try-restart"}
        for call in recovery_runner.calls
    )


def test_remote_main_change_before_bootstrap_state_commit_never_claims_baseline(
    controller_module,
    tmp_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(
            config,
            heads=[TARGET_SHA, OTHER_SHA],
            ancestor_returncode=1,
            call_hook=BootstrapProbe(config),
        ),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "transient")
    assert result.sha == TARGET_SHA
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()
    assert not config.bootstrap_journal_path.exists()
    assert not (config.releases_dir / TARGET_SHA).exists()
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert not any(_command_kind(call) == "migrate" for call in runner.calls)
    assert not any(
        _command_kind(call) == "systemctl"
        and len(call.argv) > 1
        and call.argv[1] in {"start", "stop", "restart", "try-restart"}
        for call in runner.calls
    )


@pytest.mark.parametrize(
    ("crash_boundary", "journal_phase", "link_exists", "state_exists"),
    [
        pytest.param(
            "release-prepared",
            "intent",
            False,
            False,
            id="release-created-before-phase",
        ),
        pytest.param(
            "current-linked",
            "release_prepared",
            True,
            False,
            id="link-switched-before-phase",
        ),
        pytest.param(
            "state-written",
            "current_linked",
            True,
            True,
            id="state-written-before-phase",
        ),
    ],
)
def test_bootstrap_recovers_side_effect_before_phase_advance_windows(
    controller_module,
    tmp_path,
    monkeypatch,
    crash_boundary,
    journal_phase,
    link_exists,
    state_exists,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=BootstrapProbe(config)),
        events,
    )
    controller = _controller(
        controller_module,
        config,
        runner,
        _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events),
    )

    with monkeypatch.context() as crash_patch:
        if crash_boundary == "release-prepared":
            real_prepare_release = controller._prepare_release

            def crash_after_prepare(*args, **kwargs):
                result = real_prepare_release(*args, **kwargs)
                if result is None:
                    raise SimulatedControllerCrash
                return result

            crash_patch.setattr(controller, "_prepare_release", crash_after_prepare)
        elif crash_boundary == "current-linked":
            real_switch_link = controller._switch_link

            def crash_after_link(link, release):
                real_switch_link(link, release)
                if link == config.current_link:
                    raise SimulatedControllerCrash

            crash_patch.setattr(controller, "_switch_link", crash_after_link)
        else:
            real_write_deployed_sha = controller._write_deployed_sha

            def crash_after_state(sha):
                real_write_deployed_sha(sha)
                raise SimulatedControllerCrash

            crash_patch.setattr(
                controller,
                "_write_deployed_sha",
                crash_after_state,
            )

        with pytest.raises(SimulatedControllerCrash):
            controller.deploy()

    release = config.releases_dir / TARGET_SHA
    assert release.is_dir()
    assert _read_json(config.bootstrap_journal_path)["phase"] == journal_phase
    assert config.current_link.exists() is link_exists
    assert config.deployed_state_path.exists() is state_exists

    recovery_events: list[str] = []
    recovery_runner = FakeRunner(
        _scenario(config, call_hook=BootstrapProbe(config)),
        recovery_events,
    )
    result = _controller(
        controller_module,
        config,
        recovery_runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), recovery_events),
    ).deploy()

    _assert_status(result, "bootstrapped")
    assert config.current_link.resolve() == release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert _read_json(config.bootstrap_journal_path)["phase"] == "awaiting_service"
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert not any(_command_kind(call) == "migrate" for call in recovery_runner.calls)
    assert not any(
        _command_kind(call) == "systemctl"
        and len(call.argv) > 1
        and call.argv[1] in {"start", "stop", "restart", "try-restart"}
        for call in recovery_runner.calls
    )


def test_pending_bootstrap_finalizes_only_after_stable_service_and_app_health(
    controller_module,
    tmp_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    release = _create_pending_bootstrap(config)
    _write_control_plane_manifest(config, phase="installed")
    database_before = _bootstrap_database_snapshot(config.database_path)
    events: list[str] = []
    probe = BootstrapProbe(config, active_state="active")
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    sleeper = FakeSleeper(config.lock_path)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(
        controller_module,
        config,
        runner,
        http,
        sleeper=sleeper,
    ).deploy()

    _assert_status(result, "bootstrapped")
    assert result.sha == TARGET_SHA
    assert probe.service_active_checks == config.stable_health_checks
    assert probe.healthchecks == config.stable_health_checks
    assert sleeper.calls == [
        config.stable_health_interval_seconds,
        config.stable_health_interval_seconds,
    ]
    assert not config.bootstrap_journal_path.exists()
    receipt_path = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    receipt = _read_json(receipt_path)
    assert receipt["candidate_sha"] == TARGET_SHA
    assert receipt["phase"] == "healthy"
    assert receipt["bootstrap"] is True
    assert config.current_link.resolve() == release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert http.calls == []
    assert not any(
        _command_kind(call) in {"fetch", "merge-base", "migrate", "uv-sync"}
        or (
            _command_kind(call) == "systemctl"
            and len(call.argv) > 1
            and call.argv[1] in {"start", "stop", "restart", "try-restart"}
        )
        for call in runner.calls
    )


def test_pending_bootstrap_receipt_is_idempotent_across_crash_before_journal_clear(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_pending_bootstrap(config)
    _write_control_plane_manifest(config, phase="installed")
    database_before = _bootstrap_database_snapshot(config.database_path)
    receipt_path = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    events: list[str] = []
    controller = _controller(
        controller_module,
        config,
        FakeRunner(
            _scenario(
                config,
                call_hook=BootstrapProbe(config, active_state="active"),
            ),
            events,
        ),
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events),
        sleeper=FakeSleeper(config.lock_path),
    )
    real_atomic_write_json = controller_module._atomic_write_json
    crashed = False

    def crash_after_receipt(path, payload, *, mode):
        nonlocal crashed
        real_atomic_write_json(path, payload, mode=mode)
        if path == receipt_path and not crashed:
            crashed = True
            raise SimulatedControllerCrash

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(
            controller_module,
            "_atomic_write_json",
            crash_after_receipt,
        )
        with pytest.raises(SimulatedControllerCrash):
            controller.deploy()

    assert crashed
    assert receipt_path.is_file()
    assert config.bootstrap_journal_path.is_file()
    recovery_events: list[str] = []
    recovery_runner = FakeRunner(
        _scenario(
            config,
            call_hook=BootstrapProbe(config, active_state="active"),
        ),
        recovery_events,
    )

    result = _controller(
        controller_module,
        config,
        recovery_runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), recovery_events),
        sleeper=FakeSleeper(config.lock_path),
    ).deploy()

    _assert_status(result, "bootstrapped")
    assert not config.bootstrap_journal_path.exists()
    receipt = _read_json(receipt_path)
    assert receipt["candidate_sha"] == TARGET_SHA
    assert receipt["bootstrap"] is True
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert not any(
        _command_kind(call) in {"fetch", "ls-tree", "archive", "uv-sync", "migrate"}
        for call in recovery_runner.calls
    )


@pytest.mark.parametrize(
    ("active_state", "healthy", "expected_status", "expected_healthchecks"),
    [
        pytest.param("inactive", True, "bootstrapped", 0, id="still-inactive"),
        pytest.param("active", False, "rejected", 1, id="app-unhealthy"),
    ],
)
def test_pending_bootstrap_stays_pending_without_healthy_active_service(
    controller_module,
    tmp_path,
    active_state,
    healthy,
    expected_status,
    expected_healthchecks,
):
    config = _bootstrap_config(controller_module, tmp_path)
    release = _create_pending_bootstrap(config)
    _write_control_plane_manifest(config, phase="installed")
    journal_before = config.bootstrap_journal_path.read_bytes()
    database_before = _bootstrap_database_snapshot(config.database_path)
    events: list[str] = []
    probe = BootstrapProbe(
        config,
        active_state=active_state,
        healthy=healthy,
    )
    runner = FakeRunner(
        _scenario(config, heads=[OTHER_SHA], call_hook=probe),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(OTHER_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, expected_status)
    assert probe.healthchecks == expected_healthchecks
    assert config.bootstrap_journal_path.read_bytes() == journal_before
    assert list(config.deployment_receipts_dir.iterdir()) == []
    assert config.current_link.resolve() == release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert http.calls == []
    assert not any(
        _command_kind(call) in {"fetch", "merge-base", "migrate", "uv-sync"}
        or (
            _command_kind(call) == "systemctl"
            and len(call.argv) > 1
            and call.argv[1] in {"start", "stop", "restart", "try-restart"}
        )
        for call in runner.calls
    )


@pytest.mark.parametrize(
    ("journal_state", "expected_status"),
    [
        pytest.param("corrupt", "intervention_required", id="corrupt-json"),
        pytest.param(
            "inconsistent",
            "intervention_required",
            id="inconsistent-history",
        ),
        pytest.param("symlink", "rejected", id="symlink"),
        pytest.param("writable", "rejected", id="writable"),
    ],
)
def test_unsafe_or_corrupt_bootstrap_journal_fails_closed_before_effects(
    controller_module,
    tmp_path,
    journal_state,
    expected_status,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    if journal_state == "corrupt":
        config.bootstrap_journal_path.write_text("{broken json\n")
        config.bootstrap_journal_path.chmod(0o600)
    elif journal_state == "inconsistent":
        config.bootstrap_journal_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "candidate_sha": TARGET_SHA,
                    "phase": "current_linked",
                    "phases": ["intent", "current_linked"],
                }
            )
            + "\n"
        )
        config.bootstrap_journal_path.chmod(0o600)
    elif journal_state == "symlink":
        outside = tmp_path / "attacker-bootstrap-journal"
        outside.write_text("{}\n")
        config.bootstrap_journal_path.symlink_to(outside)
    else:
        _write_bootstrap_journal(config, phase="intent")
        config.bootstrap_journal_path.chmod(0o666)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, expected_status)
    assert "bootstrap" in result.reason.casefold()
    assert runner.calls == []
    assert http.calls == []
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert config.bootstrap_journal_path.exists()


@pytest.mark.parametrize("crash_point", ["extract", "uv-sync"])
def test_established_release_build_crash_recovers_via_trusted_staging(
    controller_module,
    tmp_path,
    monkeypatch,
    crash_point,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    release = config.releases_dir / TARGET_SHA
    staging = config.releases_dir / f".{TARGET_SHA}.tmp"
    build_path: Path | None = None
    sentinel_name = "crash-left-sentinel"
    real_extract = controller_module._extract_validated_tar

    def crash_during_extract(members, destination):
        nonlocal build_path
        build_path = destination
        real_extract(members, destination)
        (destination / sentinel_name).write_text("partial", encoding="ascii")
        raise SimulatedControllerCrash

    def crash_during_build(
        call: RunCall,
        _occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        nonlocal build_path
        if _command_kind(call) == "uv-sync":
            build_path = call.cwd
            assert build_path is not None
            (build_path / sentinel_name).write_text("partial", encoding="ascii")
            raise SimulatedControllerCrash
        return None

    first_events: list[str] = []
    hook = crash_during_build if crash_point == "uv-sync" else None
    first = _controller(
        controller_module,
        config,
        FakeRunner(_trusted_scenario(config, call_hook=hook), first_events),
        _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), first_events),
    )

    with monkeypatch.context() as crash_patch:
        if crash_point == "extract":
            crash_patch.setattr(
                controller_module,
                "_extract_validated_tar",
                crash_during_extract,
            )
        with pytest.raises(SimulatedControllerCrash):
            first.reconcile()

    assert build_path == staging
    assert staging.is_dir()
    assert (staging / sentinel_name).is_file()
    assert not release.exists()
    assert config.current_link.resolve() == previous
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{LAST_SHA}\n"
    promotion_events: list[str] = []
    real_replace = controller_module.os.replace

    def record_ready_promotion(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if destination_path == release:
            assert source_path == staging
            assert not release.exists()
            assert _read_json(staging / ".ready.json")["sha"] == TARGET_SHA
            assert _lock_is_held(config.lock_path)
            promotion_events.append("ready-staging-promoted")
        return real_replace(source, destination)

    recovery_events: list[str] = []
    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(
            controller_module.os,
            "replace",
            record_ready_promotion,
        )
        recovered = _controller(
            controller_module,
            config,
            FakeRunner(_trusted_scenario(config), recovery_events),
            _trusted_http(
                _workflow_payload(_workflow_run(TARGET_SHA)),
                recovery_events,
            ),
        ).reconcile()

    _assert_status(recovered, "prepared")
    assert promotion_events == ["ready-staging-promoted"]
    assert release.is_dir()
    assert (release / ".ready.json").is_file()
    assert not staging.exists()
    assert not (release / sentinel_name).exists()
    assert config.current_link.resolve() == previous


@pytest.mark.parametrize(
    "unsafe_state",
    [
        pytest.param("symlink", id="symlink"),
        pytest.param("regular-file", id="not-directory"),
        pytest.param("writable-mode", id="writable-directory"),
        pytest.param("wrong-owner", id="wrong-owner"),
        pytest.param("unexpected-name", id="unexpected-staging-name"),
    ],
)
def test_untrusted_or_unexpected_release_staging_fails_closed(
    controller_module,
    tmp_path,
    monkeypatch,
    unsafe_state,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    staging = config.releases_dir / f".{TARGET_SHA}.tmp"
    evidence = staging
    if unsafe_state == "symlink":
        outside = tmp_path / "attacker-release"
        outside.mkdir()
        staging.symlink_to(outside, target_is_directory=True)
    elif unsafe_state == "regular-file":
        staging.write_text("not a staging directory", encoding="ascii")
    elif unsafe_state in {"writable-mode", "wrong-owner"}:
        staging.mkdir(mode=0o755)
        if unsafe_state == "writable-mode":
            staging.chmod(0o777)
        else:
            real_lstat = controller_module.os.lstat

            def wrong_staging_owner(path, *args, **kwargs):
                metadata = real_lstat(path, *args, **kwargs)
                if not args and not kwargs and Path(path) == staging:
                    return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
                return metadata

            monkeypatch.setattr(controller_module.os, "lstat", wrong_staging_owner)
    else:
        evidence = config.releases_dir / f".{TARGET_SHA}.tmp.extra"
        evidence.mkdir()
    runner = FakeRunner(_scenario(config), [])
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), [])

    result = _controller(controller_module, config, runner, http).reconcile()

    assert _status(result) in {"rejected", "intervention_required"}
    assert runner.calls == []
    assert http.calls == []
    assert evidence.exists() or evidence.is_symlink()
    assert not (config.releases_dir / TARGET_SHA).exists()
    assert config.current_link.resolve() == previous


def test_bootstrap_recovers_crash_inside_partial_release_preparation(
    controller_module,
    tmp_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    first_events: list[str] = []
    first_probe = BootstrapProbe(config)
    staging = config.releases_dir / f".{TARGET_SHA}.tmp"
    build_path: Path | None = None

    def crash_during_build(
        call: RunCall,
        occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        nonlocal build_path
        probed = first_probe(call, occurrence)
        if probed is not None:
            return probed
        if _command_kind(call) == "uv-sync":
            build_path = call.cwd
            raise SimulatedControllerCrash
        return None

    first = _controller(
        controller_module,
        config,
        FakeRunner(
            _trusted_scenario(config, call_hook=crash_during_build),
            first_events,
        ),
        _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), first_events),
    )

    with pytest.raises(SimulatedControllerCrash):
        first.deploy()

    release = config.releases_dir / TARGET_SHA
    assert build_path == staging
    assert staging.is_dir()
    assert not (staging / ".ready.json").exists()
    assert not release.exists()
    assert _read_json(config.bootstrap_journal_path)["phase"] == "intent"
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()

    recovery_events: list[str] = []
    recovery_runner = FakeRunner(
        _scenario(config, call_hook=BootstrapProbe(config)),
        recovery_events,
    )
    recovered = _controller(
        controller_module,
        config,
        recovery_runner,
        FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), recovery_events),
    ).deploy()

    _assert_status(recovered, "bootstrapped")
    assert _read_json(config.bootstrap_journal_path)["phase"] == "awaiting_service"
    assert config.current_link.resolve() == release
    assert (release / ".ready.json").is_file()
    assert not staging.exists()
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{TARGET_SHA}\n"
    assert _bootstrap_database_snapshot(config.database_path) == database_before


def test_bootstrap_recovers_abort_crash_after_release_discard_before_journal_clear(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    database_before = _bootstrap_database_snapshot(config.database_path)
    events: list[str] = []
    controller = _controller(
        controller_module,
        config,
        FakeRunner(
            _trusted_scenario(
                config,
                heads=[TARGET_SHA, OTHER_SHA],
                call_hook=BootstrapProbe(config),
            ),
            events,
        ),
        _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events),
    )
    release = config.releases_dir / TARGET_SHA
    real_discard = controller._discard_partial_release

    def crash_after_durable_discard(path, sha):
        real_discard(path, sha)
        assert not path.exists()
        raise SimulatedControllerCrash

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(
            controller,
            "_discard_partial_release",
            crash_after_durable_discard,
        )
        with pytest.raises(SimulatedControllerCrash):
            controller.deploy()

    assert not release.exists()
    assert _read_json(config.bootstrap_journal_path)["phase"] == "release_prepared"
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()

    recovery_events: list[str] = []
    recovered = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), recovery_events),
        FakeHttp(None, recovery_events),
    ).deploy()

    _assert_status(recovered, "transient")
    assert not config.bootstrap_journal_path.exists()
    assert not release.exists()
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()
    assert _bootstrap_database_snapshot(config.database_path) == database_before
    assert recovery_events == []


@pytest.mark.parametrize(
    ("rollback_kind", "journal_phase", "database_is_restored"),
    [
        pytest.param("before-start", "switched", True, id="before-start"),
        pytest.param("post-start", "start_requested", False, id="post-start"),
    ],
)
def test_rollback_intent_is_durable_before_switch_back_and_recovers_crash_window(
    controller_module,
    tmp_path,
    monkeypatch,
    rollback_kind,
    journal_phase,
    database_is_restored,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    config.current_link.unlink()
    config.current_link.symlink_to(candidate)
    _write_journal(config, phase=journal_phase, backup=backup_record)
    events: list[str] = []
    first_probe = ActivationProbe(config)
    first = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config, call_hook=first_probe), events),
        FakeHttp(None, events),
    )
    real_switch = first._switch_link
    real_write_journal = first._write_journal

    def record_journal(payload):
        written = real_write_journal(payload)
        if written:
            events.append(f"journal:{payload.get('phase')}")
        return written

    def crash_after_switch_back(link, release):
        real_switch(link, release)
        if link == config.current_link and release == previous:
            events.append("link:previous")
            raise SimulatedControllerCrash

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(first, "_write_journal", record_journal)
        crash_patch.setattr(first, "_switch_link", crash_after_switch_back)
        with pytest.raises(SimulatedControllerCrash):
            if rollback_kind == "before-start":
                first._rollback_before_start(
                    _read_json(config.deployment_journal_path),
                    previous,
                    restore_database=True,
                    reason="injected pre-start rollback",
                )
            else:
                first._rollback_after_start_requested(
                    _read_json(config.deployment_journal_path),
                    previous,
                    reason="injected post-start rollback",
                )

    assert config.current_link.resolve() == previous
    assert _read_json(config.deployment_journal_path)["phase"] == "rollback_pending"
    assert events.index("journal:rollback_pending") < events.index("link:previous")
    assert _database_has_migration_marker(config.database_path) is (
        not database_is_restored
    )
    database_before_recovery = config.database_path.read_bytes()
    recovery_events: list[str] = []
    recovery_probe = ActivationProbe(config)
    recovered = _controller(
        controller_module,
        config,
        FakeRunner(
            _scenario(config, call_hook=recovery_probe),
            recovery_events,
        ),
        FakeHttp(None, recovery_events),
    ).deploy()

    _assert_status(recovered, "rejected")
    assert config.current_link.resolve() == previous
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{LAST_SHA}\n"
    assert not config.deployment_journal_path.exists()
    if rollback_kind == "post-start":
        assert config.database_path.read_bytes() == database_before_recovery
        assert _database_has_migration_marker(config.database_path)
    else:
        assert not _database_has_migration_marker(config.database_path)


def test_healthy_phase_rollback_restores_deployed_state_before_restart_and_clear(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    previous = _create_ready_release(config, LAST_SHA)
    candidate = _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_deployed_state(config, TARGET_SHA)
    _write_journal(config, phase="healthy", backup=backup_record)
    events: list[str] = []
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), events),
        FakeHttp(None, events),
    )
    real_write_deployed_sha = controller._write_deployed_sha

    def systemctl(action):
        if action == "start":
            events.append(
                "start-with-state:"
                + config.deployed_state_path.read_text(encoding="ascii").strip()
            )
        return subprocess.CompletedProcess(
            args=(str(config.systemctl_path), action, config.service_name),
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

    def healthcheck(release, _database):
        return subprocess.CompletedProcess(
            args=(str(release / ".venv/bin/python"),),
            returncode=1 if release == candidate else 0,
            stdout=b"",
            stderr=b"candidate unhealthy" if release == candidate else b"",
        )

    def write_deployed_sha(sha):
        real_write_deployed_sha(sha)
        events.append(f"state-written:{sha}")

    monkeypatch.setattr(
        controller,
        "_service_observation",
        lambda: controller_module.ServiceObservation(1, 0, 0),
    )
    monkeypatch.setattr(controller, "_healthcheck", healthcheck)
    monkeypatch.setattr(controller, "_systemctl", systemctl)
    monkeypatch.setattr(controller, "_previous_release_is_compatible", lambda *_: True)
    monkeypatch.setattr(controller, "_write_deployed_sha", write_deployed_sha)
    monkeypatch.setattr(
        controller,
        "_release_is_stably_healthy",
        lambda release: release == previous,
    )

    recovered = controller.deploy()

    _assert_status(recovered, "rejected")
    assert config.current_link.resolve() == previous
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{LAST_SHA}\n"
    assert events == [
        f"state-written:{LAST_SHA}",
        f"start-with-state:{LAST_SHA}",
    ]
    assert not config.deployment_journal_path.exists()
    assert _database_has_migration_marker(config.database_path)


def test_production_bootstrap_database_validation_uses_runtime_identity_and_ancestors(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = replace(
        _bootstrap_config(controller_module, tmp_path),
        test_mode=False,
        runtime_uid=12001,
        runtime_gid=12002,
    )
    captured: list[tuple[Path, int | None, int | None, bool]] = []

    def record_validation(
        path,
        *,
        expected_uid,
        expected_gid,
        require_root_ancestors,
    ):
        captured.append(
            (path, expected_uid, expected_gid, require_root_ancestors)
        )

    monkeypatch.setattr(
        controller_module,
        "_validate_bootstrap_database",
        record_validation,
    )
    controller = controller_module.ReleaseController(
        config=config,
        runner=FakeRunner(_scenario(config), []),
        http=FakeHttp(None, []),
    )

    assert controller._bootstrap_database_error() is None
    assert captured == [
        (config.database_path, 12001, 12002, True),
    ]


@pytest.mark.parametrize(
    "unsafe_state",
    [
        pytest.param("mode", id="not-exactly-0600"),
        pytest.param("uid", id="wrong-uid"),
        pytest.param("gid", id="wrong-gid"),
        pytest.param("symlink", id="symlink"),
        pytest.param("hardlink", id="hardlink"),
        pytest.param("wal", id="wal-sidecar"),
        pytest.param("shm", id="shm-sidecar"),
        pytest.param("journal", id="delete-journal-sidecar"),
    ],
)
def test_bootstrap_database_validator_rejects_unsafe_production_metadata(
    controller_module,
    tmp_path,
    unsafe_state,
):
    database = tmp_path / "honeybuy.sqlite3"
    _create_bootstrap_database(database)
    validation_path = database
    expected_uid = os.getuid()
    expected_gid = os.getgid()
    if unsafe_state == "mode":
        database.chmod(0o640)
    elif unsafe_state == "uid":
        expected_uid += 1
    elif unsafe_state == "gid":
        expected_gid += 1
    elif unsafe_state == "symlink":
        validation_path = tmp_path / "database-link.sqlite3"
        validation_path.symlink_to(database)
    elif unsafe_state == "hardlink":
        os.link(database, tmp_path / "database-hardlink.sqlite3")
    else:
        database.with_name(f"{database.name}-{unsafe_state}").write_bytes(
            b"unresolved SQLite sidecar"
        )

    with pytest.raises((OSError, ValueError)):
        controller_module._validate_bootstrap_database(
            validation_path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            require_root_ancestors=False,
        )


def test_bootstrap_database_validator_requires_safe_parent_and_root_ancestors(
    controller_module,
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "honeybuy.sqlite3"
    _create_bootstrap_database(database)
    expected_uid = os.getuid()
    expected_gid = os.getgid()
    parent_calls: list[tuple[Path, int, int, str]] = []
    ancestor_calls: list[Path] = []

    def parent_is_safe(path, *, expected_uid, expected_gid, kind, **_kwargs):
        parent_calls.append((path, expected_uid, expected_gid, kind))
        return True

    def ancestors_are_unsafe(path):
        ancestor_calls.append(path)
        return False

    monkeypatch.setattr(controller_module, "_trusted_path_metadata", parent_is_safe)
    monkeypatch.setattr(
        controller_module,
        "_root_ancestor_metadata_is_safe",
        ancestors_are_unsafe,
    )

    with pytest.raises(ValueError, match="ancestor"):
        controller_module._validate_bootstrap_database(
            database,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            require_root_ancestors=True,
        )

    assert parent_calls == [
        (database.parent, expected_uid, expected_gid, "directory")
    ]
    assert ancestor_calls == [database.parent]


def test_bootstrap_database_validator_detects_path_change_during_validation(
    controller_module,
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "honeybuy.sqlite3"
    _create_bootstrap_database(database)
    real_match = controller_module._path_matches_metadata
    match_calls = 0

    def path_changes_after_open(path, metadata):
        nonlocal match_calls
        if path == database:
            match_calls += 1
            if match_calls >= 2:
                return False
        return real_match(path, metadata)

    monkeypatch.setattr(
        controller_module,
        "_path_matches_metadata",
        path_changes_after_open,
    )

    with pytest.raises(ValueError, match="changed during validation"):
        controller_module._validate_bootstrap_database(
            database,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            require_root_ancestors=False,
        )

    assert match_calls >= 2


def test_bootstrap_database_validator_detects_sidecar_appearing_during_validation(
    controller_module,
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "honeybuy.sqlite3"
    _create_bootstrap_database(database)
    journal = database.with_name(f"{database.name}-journal")
    real_exists = controller_module._path_exists_lstat
    journal_checks = 0

    def sidecar_appears(path):
        nonlocal journal_checks
        if path == journal:
            journal_checks += 1
            return journal_checks >= 2
        return real_exists(path)

    monkeypatch.setattr(
        controller_module,
        "_path_exists_lstat",
        sidecar_appears,
    )

    with pytest.raises(ValueError, match="sidecar appeared"):
        controller_module._validate_bootstrap_database(
            database,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
            require_root_ancestors=False,
        )

    assert journal_checks == 2


@pytest.mark.parametrize(
    ("helper_name", "database_remains"),
    [
        pytest.param("_remove_database_sidecars", True, id="sidecars-only"),
        pytest.param("_remove_database_files", False, id="database-and-sidecars"),
    ],
)
def test_database_cleanup_helpers_remove_delete_journal_sidecar(
    controller_module,
    tmp_path,
    helper_name,
    database_remains,
):
    database = tmp_path / "honeybuy.sqlite3"
    database.write_bytes(b"database")
    sidecars = [
        database.with_name(f"{database.name}{suffix}")
        for suffix in ("-wal", "-shm", "-journal")
    ]
    for sidecar in sidecars:
        sidecar.write_bytes(b"sidecar")

    getattr(controller_module, helper_name)(database)

    assert database.exists() is database_remains
    assert not any(sidecar.exists() for sidecar in sidecars)


@pytest.mark.parametrize(
    "helper_name",
    ["_remove_database_sidecars", "_remove_database_files"],
)
def test_database_cleanup_helpers_reject_unsafe_delete_journal_type(
    controller_module,
    tmp_path,
    helper_name,
):
    database = tmp_path / "honeybuy.sqlite3"
    database.write_bytes(b"database")
    journal = database.with_name(f"{database.name}-journal")
    journal.mkdir()

    with pytest.raises(OSError, match="unsafe path"):
        getattr(controller_module, helper_name)(database)

    assert journal.is_dir()


def test_restore_removes_hot_delete_journal_before_restored_database_is_reopened(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    writer = _create_wal_database(config.database_path)
    writer.close()
    with closing(sqlite3.connect(config.database_path)) as database:
        assert database.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    _backup, backup_record = _write_recovery_backup(config)
    child = (
        "import os,sqlite3,sys;"
        "db=sqlite3.connect(sys.argv[1]);"
        "db.execute('PRAGMA journal_mode=DELETE');"
        "db.execute('PRAGMA synchronous=FULL');"
        "db.execute('BEGIN IMMEDIATE');"
        "db.execute(\"INSERT INTO deployment_probe(value) "
        "VALUES ('uncommitted-candidate-write')\");"
        "os._exit(23)"
    )
    crashed = subprocess.run(
        (sys.executable, "-I", "-c", child, str(config.database_path)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )
    journal = config.database_path.with_name(f"{config.database_path.name}-journal")
    assert crashed.returncode == 23, crashed.stderr.decode("utf-8", "replace")
    assert journal.is_file()
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), []),
        FakeHttp(None, []),
    )

    controller._restore_database(
        {"candidate_sha": TARGET_SHA, "backup": backup_record}
    )

    assert not journal.exists()
    assert _database_values(config.database_path) == ["committed-in-wal"]


@pytest.mark.parametrize(
    ("action", "minimum_timeout"),
    [
        pytest.param("start", 120.0, id="start"),
        pytest.param("stop", 30.0, id="stop"),
    ],
)
def test_managed_service_control_deadline_covers_systemd_unit_bound(
    controller_module,
    tmp_path,
    monkeypatch,
    action,
    minimum_timeout,
):
    systemctl_path = Path("/test-tools/systemctl")
    calls: list[tuple[tuple[str, ...], float | None]] = []

    class TimingProcess(_FakeHostProcess):
        def communicate(self, input=None, timeout=None):
            del input
            calls.append((self.args, timeout))
            return self.stdout_data, self.stderr_data

    monkeypatch.setattr(
        controller_module.subprocess,
        "Popen",
        lambda argv, **_kwargs: TimingProcess(argv),
    )
    runner = controller_module.SubprocessRunner(
        timeout_seconds=120.0,
        management_timeout_seconds=10.0,
        systemctl_path=systemctl_path,
        cgroup_root=tmp_path / "synthetic-cgroup",
        managed_service="honeybuy-tg.service",
    )

    completed = runner.run_control(
        (str(systemctl_path), action, "honeybuy-tg.service")
    )

    assert completed.returncode == 0
    command, timeout = calls[0]
    assert command == (str(systemctl_path), action, "honeybuy-tg.service")
    assert timeout is not None and timeout >= minimum_timeout


def test_rollback_recovery_recreates_exact_quarantine_before_clearing_journal(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    config.current_link.unlink()
    config.current_link.symlink_to(candidate)
    _write_journal(config, phase="start_requested", backup=backup_record)
    reason = "candidate failed injected post-start health check"
    first_events: list[str] = []
    first_probe = ActivationProbe(config)

    def crash_after_previous_start(
        call: RunCall,
        occurrence: int,
    ) -> subprocess.CompletedProcess | None:
        completed = first_probe(call, occurrence)
        if (
            _command_kind(call) == "systemctl"
            and call.argv[-2] == "start"
        ):
            assert completed is not None and completed.returncode == 0
            assert (
                _read_json(config.deployment_journal_path)["phase"]
                == "rollback_start_requested"
            )
            raise SimulatedControllerCrash
        return completed

    first = _controller(
        controller_module,
        config,
        FakeRunner(
            _scenario(config, call_hook=crash_after_previous_start),
            first_events,
        ),
        FakeHttp(None, first_events),
    )
    quarantine = config.quarantine_dir / f"{TARGET_SHA}.json"

    with monkeypatch.context() as crash_patch:
        crash_patch.setattr(first, "_write_quarantine", lambda *_: False)
        with pytest.raises(SimulatedControllerCrash):
            first._rollback_after_start_requested(
                _read_json(config.deployment_journal_path),
                previous,
                reason=reason,
            )

    assert not quarantine.exists()
    assert config.current_link.resolve() == previous
    assert _read_json(config.deployment_journal_path)["phase"] == (
        "rollback_start_requested"
    )
    recovery_events: list[str] = []
    recovery = _controller(
        controller_module,
        config,
        FakeRunner(
            _scenario(config, call_hook=ActivationProbe(config)),
            recovery_events,
        ),
        FakeHttp(None, recovery_events),
    )
    real_clear = recovery._clear_journal
    expected_quarantine = {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "reason": reason,
    }

    def clear_only_after_exact_quarantine(*, required=False):
        assert _read_json(quarantine) == expected_quarantine
        assert stat.S_IMODE(quarantine.lstat().st_mode) == 0o600
        return real_clear(required=required)

    monkeypatch.setattr(
        recovery,
        "_clear_journal",
        clear_only_after_exact_quarantine,
    )

    recovered = recovery.deploy()

    _assert_status(recovered, "rejected")
    assert _read_json(quarantine) == expected_quarantine
    assert not config.deployment_journal_path.exists()
    assert recovery._candidate_is_quarantined(TARGET_SHA)
    assert recovery._activation_state_error(
        candidate_sha=TARGET_SHA,
        previous_sha=LAST_SHA,
    ) == "candidate release is quarantined"


def test_healthy_rollback_state_write_failure_retains_recovery_journal(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    previous = _create_ready_release(config, LAST_SHA)
    candidate = _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_deployed_state(config, TARGET_SHA)
    _write_journal(config, phase="healthy", backup=backup_record)
    actions: list[str] = []
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config), []),
        FakeHttp(None, []),
    )

    def healthcheck(release, _database):
        return subprocess.CompletedProcess(
            args=(str(release / ".venv/bin/python"),),
            returncode=1 if release == candidate else 0,
            stdout=b"",
            stderr=b"",
        )

    def systemctl(action):
        actions.append(action)
        return subprocess.CompletedProcess(
            args=(str(config.systemctl_path), action, config.service_name),
            returncode=0,
            stdout=b"",
            stderr=b"",
        )

    def fail_previous_state_write(sha):
        assert sha == LAST_SHA
        raise OSError("deployed-state write failed")

    monkeypatch.setattr(
        controller,
        "_service_observation",
        lambda: controller_module.ServiceObservation(1, 0, 0),
    )
    monkeypatch.setattr(controller, "_healthcheck", healthcheck)
    monkeypatch.setattr(controller, "_systemctl", systemctl)
    monkeypatch.setattr(controller, "_previous_release_is_compatible", lambda *_: True)
    monkeypatch.setattr(controller, "_write_deployed_sha", fail_previous_state_write)

    recovered = controller.deploy()

    _assert_status(recovered, "intervention_required")
    assert actions == ["stop"]
    assert config.current_link.resolve() == previous
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{TARGET_SHA}\n"
    assert config.deployment_journal_path.exists()


def test_pre_start_rollback_does_not_rewrite_correct_deployed_state(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    config.current_link.unlink()
    config.current_link.symlink_to(candidate)
    _write_journal(config, phase="switched", backup=backup_record)
    probe = ActivationProbe(config)
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config, call_hook=probe), []),
        FakeHttp(None, []),
    )
    monkeypatch.setattr(
        controller,
        "_write_deployed_sha",
        lambda _sha: pytest.fail("correct pre-start state must not be rewritten"),
    )

    recovered = controller._rollback_before_start(
        _read_json(config.deployment_journal_path),
        previous,
        restore_database=True,
        reason="injected pre-start rollback",
    )

    _assert_status(recovered, "rejected")
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{LAST_SHA}\n"
    assert config.current_link.resolve() == previous
    assert not config.deployment_journal_path.exists()


def test_pre_start_state_drift_never_clears_journal_with_link_state_mismatch(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config, TARGET_SHA)
    previous = _create_ready_release(config, LAST_SHA)
    _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_journal(config, phase="switched", backup=backup_record)
    probe = ActivationProbe(config)
    controller = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config, call_hook=probe), []),
        FakeHttp(None, []),
    )

    recovered = controller._rollback_before_start(
        _read_json(config.deployment_journal_path),
        previous,
        restore_database=True,
        reason="injected pre-start rollback after state drift",
    )

    if _status(recovered) == "rejected":
        assert config.deployed_state_path.read_text(encoding="ascii") == f"{LAST_SHA}\n"
        assert not config.deployment_journal_path.exists()
    else:
        _assert_status(recovered, "intervention_required")
        assert config.deployment_journal_path.exists()
    assert not (
        config.current_link.resolve() == previous
        and config.deployed_state_path.read_text(encoding="ascii") == f"{TARGET_SHA}\n"
        and not config.deployment_journal_path.exists()
    )


def test_controller_config_declares_installed_control_plane_paths(controller_module):
    fields = controller_module.ControllerConfig.__dataclass_fields__

    assert fields["control_plane_manifest_path"].default == Path(
        "/var/lib/honeybuy-release-controller/control-plane-manifest.json"
    )
    assert fields["installed_controller_path"].default == Path(
        "/usr/local/lib/honeybuy/release_controller.py"
    )
    assert fields["app_service_unit_path"].default == Path(
        "/etc/systemd/system/honeybuy-tg.service"
    )
    assert fields["controller_service_unit_path"].default == Path(
        "/etc/systemd/system/honeybuy-release-controller.service"
    )
    assert fields["controller_timer_unit_path"].default == Path(
        "/etc/systemd/system/honeybuy-release-controller.timer"
    )


@pytest.mark.parametrize(
    ("manifest_state", "expected_status"),
    [
        pytest.param("exact-pending", "bootstrapped", id="exact-pending"),
        pytest.param("pending-without-installed-app", "bootstrapped", id="app-pending"),
        pytest.param("pending-with-legacy-app", "bootstrapped", id="legacy-app"),
        pytest.param("missing", "rejected", id="missing"),
        pytest.param("installed", "rejected", id="already-installed"),
        pytest.param("unknown", "rejected", id="unknown-phase"),
    ],
)
def test_pristine_bootstrap_accepts_only_exact_bootstrap_pending_manifest(
    controller_module,
    tmp_path,
    manifest_state,
    expected_status,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    if manifest_state == "pending-without-installed-app":
        config.app_service_unit_path.unlink()
    elif manifest_state == "pending-with-legacy-app":
        config.app_service_unit_path.write_bytes(b"legacy manual unit\n")
        metadata = os.lstat(config.app_service_unit_path)
        manifest = _read_json(config.control_plane_manifest_path)
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_nlink == 1
        assert metadata.st_uid == config.release_uid
        assert metadata.st_gid == config.release_gid
        assert not metadata.st_mode & 0o022
        assert hashlib.sha256(config.app_service_unit_path.read_bytes()).hexdigest() != (
            manifest["files"]["deploy/systemd/honeybuy-tg.service"]
        )
    elif manifest_state == "missing":
        config.control_plane_manifest_path.unlink()
    elif manifest_state in {"installed", "unknown"}:
        _write_control_plane_manifest(config, phase=manifest_state)
    events: list[str] = []
    probe = BootstrapProbe(config)
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=probe),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, expected_status)
    if expected_status == "bootstrapped":
        assert _read_json(config.bootstrap_journal_path)["phase"] == (
            "awaiting_service"
        )
    else:
        assert runner.calls == []
        assert http.calls == []
        assert not config.current_link.exists()
        assert not config.deployed_state_path.exists()


@pytest.mark.parametrize(
    "unsafe_state",
    [
        pytest.param("symlink", id="symlink"),
        pytest.param("group-writable", id="group-writable"),
        pytest.param("world-writable", id="world-writable"),
        pytest.param("hardlink", id="hardlink"),
        pytest.param("wrong-owner", id="wrong-owner"),
    ],
)
def test_pending_bootstrap_legacy_app_unit_requires_safe_metadata_before_effects(
    controller_module,
    tmp_path,
    monkeypatch,
    unsafe_state,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    legacy_unit = config.app_service_unit_path
    legacy_unit.write_bytes(b"legacy manual unit\n")
    legacy_unit.chmod(0o644)
    if unsafe_state == "symlink":
        legacy_unit.unlink()
        untrusted = tmp_path / "untrusted-legacy-app.service"
        untrusted.write_bytes(b"untrusted legacy unit\n")
        legacy_unit.symlink_to(untrusted)
    elif unsafe_state == "group-writable":
        legacy_unit.chmod(0o664)
    elif unsafe_state == "world-writable":
        legacy_unit.chmod(0o666)
    elif unsafe_state == "hardlink":
        os.link(legacy_unit, tmp_path / "legacy-app-hardlink")
    else:
        real_lstat = controller_module.os.lstat

        def wrong_legacy_owner(path, *args, **kwargs):
            metadata = real_lstat(path, *args, **kwargs)
            if not args and not kwargs and Path(path) == legacy_unit:
                return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
            return metadata

        monkeypatch.setattr(controller_module.os, "lstat", wrong_legacy_owner)
    events: list[str] = []
    runner = FakeRunner(
        _scenario(config, call_hook=BootstrapProbe(config)),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert result.reason == (
        "installed control-plane artifact is unsafe: "
        "deploy/systemd/honeybuy-tg.service"
    )
    assert runner.calls == []
    assert http.calls == []
    assert not config.bootstrap_journal_path.exists()
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()


@pytest.mark.parametrize(
    "candidate_path",
    sorted(
        set(CONTROL_PLANE_ASSETS)
        - {"deploy/systemd/honeybuy-tg.service"}
    ),
)
def test_pending_bootstrap_still_requires_other_installed_control_plane_hashes(
    controller_module,
    tmp_path,
    candidate_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    installed = getattr(config, CONTROL_PLANE_CONFIG_PATHS[candidate_path])
    installed.write_bytes(installed.read_bytes() + b"tampered\n")
    events: list[str] = []
    runner = FakeRunner(
        _scenario(config, call_hook=BootstrapProbe(config)),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert result.reason == f"installed control-plane artifact differs: {candidate_path}"
    assert runner.calls == []
    assert http.calls == []
    assert not config.bootstrap_journal_path.exists()
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()


@pytest.mark.parametrize("entrypoint", ["noop", "activation", "recovery"])
def test_established_entrypoints_require_installed_control_plane_manifest(
    controller_module,
    tmp_path,
    entrypoint,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    if entrypoint in {"activation", "recovery"}:
        _create_ready_release(config, TARGET_SHA)
    if entrypoint == "recovery":
        _write_journal(config, phase="prepared", backup=None)
    _write_control_plane_manifest(config, phase="bootstrap_pending")
    events: list[str] = []
    heads = [LAST_SHA] if entrypoint == "noop" else [TARGET_SHA]
    runner = FakeRunner(_scenario(config, heads=heads), events)
    http = FakeHttp(_workflow_payload(_workflow_run(heads[0])), events)
    controller = _controller(controller_module, config, runner, http)

    result = controller.reconcile() if entrypoint == "noop" else controller.deploy()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []
    assert config.current_link.resolve() == previous
    assert not any(config.quarantine_dir.iterdir())


@pytest.mark.parametrize(
    "unsafe_state",
    [
        pytest.param("missing", id="missing"),
        pytest.param("invalid-json", id="invalid-json"),
        pytest.param("missing-file", id="missing-file"),
        pytest.param("extra-file", id="extra-file"),
        pytest.param("uppercase-digest", id="uppercase-digest"),
        pytest.param("mode", id="writable-mode"),
        pytest.param("symlink", id="symlink"),
        pytest.param("hardlink", id="hardlink"),
        pytest.param("owner", id="wrong-owner"),
    ],
)
def test_control_plane_manifest_failure_precedes_namespace_and_external_effects(
    controller_module,
    tmp_path,
    monkeypatch,
    unsafe_state,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    manifest = config.control_plane_manifest_path
    if unsafe_state == "missing":
        manifest.unlink()
    elif unsafe_state == "invalid-json":
        manifest.write_text("{\n", encoding="utf-8")
    elif unsafe_state in {"missing-file", "extra-file", "uppercase-digest"}:
        payload = _read_json(manifest)
        files = payload["files"]
        if unsafe_state == "missing-file":
            files.pop(next(iter(files)))
        elif unsafe_state == "extra-file":
            files["deploy/ubuntu/untrusted-extra"] = "0" * 64
        else:
            key = next(iter(files))
            files[key] = files[key].upper()
        manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    elif unsafe_state == "mode":
        manifest.chmod(0o666)
    elif unsafe_state == "symlink":
        outside = tmp_path / "attacker-control-plane.json"
        outside.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
        manifest.unlink()
        manifest.symlink_to(outside)
    elif unsafe_state == "hardlink":
        os.link(manifest, tmp_path / "control-plane-hardlink.json")
    else:
        real_lstat = controller_module.os.lstat

        def wrong_manifest_owner(path, *args, **kwargs):
            metadata = real_lstat(path, *args, **kwargs)
            if not args and not kwargs and Path(path) == manifest:
                return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
            return metadata

        monkeypatch.setattr(controller_module.os, "lstat", wrong_manifest_owner)
    shutil.rmtree(config.deployment_receipts_dir)
    shutil.rmtree(config.quarantine_dir)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []
    assert config.current_link.resolve() == previous
    assert not config.deployment_receipts_dir.exists()
    assert not config.quarantine_dir.exists()
    assert not config.deployment_journal_path.exists()


@pytest.mark.parametrize("candidate_path", sorted(CONTROL_PLANE_ASSETS))
def test_installed_control_plane_artifact_hashes_must_exactly_match_manifest(
    controller_module,
    tmp_path,
    candidate_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    installed = getattr(config, CONTROL_PLANE_CONFIG_PATHS[candidate_path])
    installed.write_bytes(installed.read_bytes() + b"tampered\n")
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert runner.calls == []
    assert http.calls == []
    assert config.current_link.resolve() == previous
    assert not config.deployment_journal_path.exists()
    assert not any(config.quarantine_dir.iterdir())


@pytest.mark.parametrize("candidate_path", sorted(CONTROL_PLANE_ASSETS))
def test_candidate_control_plane_assets_match_manifest_before_activation(
    controller_module,
    tmp_path,
    candidate_path,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate = _create_ready_release(config, TARGET_SHA)
    (candidate / candidate_path).write_bytes(b"candidate substitution\n")
    writer = _create_wal_database(config.database_path)
    writer.close()
    events: list[str] = []
    probe = ActivationProbe(config)
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=probe),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert not any(
        _command_kind(call) == "migrate"
        and call.env == {"DATABASE_PATH": str(config.database_path)}
        for call in runner.calls
    )
    assert config.current_link.resolve() == previous
    assert not config.deployment_journal_path.exists()
    assert not any(config.quarantine_dir.iterdir())
    assert not _database_has_migration_marker(config.database_path)


@pytest.mark.parametrize("mutated_copy", ["candidate", "installed"])
def test_control_plane_is_rechecked_after_dry_run_before_service_stop(
    controller_module,
    tmp_path,
    mutated_copy,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    candidate = _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    probe = ActivationProbe(config)
    mutation = (
        candidate / "deploy/ubuntu/release_controller.py"
        if mutated_copy == "candidate"
        else config.installed_controller_path
    )
    mutation_seen = False

    def mutate_on_final_fetch(call: RunCall, occurrence: int):
        nonlocal mutation_seen
        probed = probe(call, occurrence)
        if probed is not None:
            return probed
        if _command_kind(call) == "rev-parse" and occurrence == 3:
            mutation.write_bytes(mutation.read_bytes() + b"changed after dry run\n")
            mutation_seen = True
        return None

    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=mutate_on_final_fetch),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    assert mutation_seen
    _assert_status(result, "rejected")
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert not any(
        _command_kind(call) == "migrate"
        and call.env == {"DATABASE_PATH": str(config.database_path)}
        for call in runner.calls
    )
    assert config.current_link.resolve() == previous
    assert not config.deployment_journal_path.exists()
    assert not any(config.quarantine_dir.iterdir())
    assert not _database_has_migration_marker(config.database_path)


@pytest.mark.parametrize("candidate_path", sorted(CONTROL_PLANE_ASSETS))
def test_bootstrap_rechecks_candidate_control_plane_before_current_link(
    controller_module,
    tmp_path,
    candidate_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_bootstrap_database(config.database_path)
    probe = BootstrapProbe(config)
    mutated = False

    def mutate_on_final_fetch(call: RunCall, occurrence: int):
        nonlocal mutated
        probed = probe(call, occurrence)
        if probed is not None:
            return probed
        if _command_kind(call) == "rev-parse" and occurrence == 2:
            release_asset = config.releases_dir / TARGET_SHA / candidate_path
            release_asset.write_bytes(release_asset.read_bytes() + b"late mutation\n")
            mutated = True
        return None

    events: list[str] = []
    runner = FakeRunner(
        _trusted_scenario(config, call_hook=mutate_on_final_fetch),
        events,
    )
    http = _trusted_http(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    assert mutated
    _assert_status(result, "rejected")
    assert not config.current_link.exists()
    assert not config.deployed_state_path.exists()
    assert not any(
        _command_kind(call) == "systemctl"
        and len(call.argv) > 1
        and call.argv[1] in {"start", "stop", "restart", "try-restart"}
        for call in runner.calls
    )
    assert not any(_command_kind(call) == "migrate" for call in runner.calls)
    assert not any(config.quarantine_dir.iterdir())
    assert config.bootstrap_journal_path.exists()


def test_pending_bootstrap_cannot_finalize_until_control_plane_is_installed(
    controller_module,
    tmp_path,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_pending_bootstrap(config)
    first_probe = BootstrapProbe(config, active_state="active")
    first_runner = FakeRunner(
        _scenario(config, call_hook=first_probe),
        [],
    )
    receipt = config.deployment_receipts_dir / f"{TARGET_SHA}.json"

    pending = _controller(
        controller_module,
        config,
        first_runner,
        FakeHttp(None, []),
    ).deploy()

    assert _status(pending) in {"bootstrapped", "rejected"}
    assert config.bootstrap_journal_path.exists()
    assert not receipt.exists()
    _write_control_plane_manifest(config, phase="installed")
    installed_probe = BootstrapProbe(config, active_state="active")
    installed = _controller(
        controller_module,
        config,
        FakeRunner(_scenario(config, call_hook=installed_probe), []),
        FakeHttp(None, []),
    ).deploy()

    _assert_status(installed, "bootstrapped")
    assert installed_probe.healthchecks == config.stable_health_checks
    assert receipt.is_file()
    assert not config.bootstrap_journal_path.exists()


def test_exact_bootstrap_receipt_temp_is_promoted_under_lock_and_reused(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _bootstrap_config(controller_module, tmp_path)
    _create_pending_bootstrap(config)
    _write_control_plane_manifest(config, phase="installed")
    journal = _read_json(config.bootstrap_journal_path)
    receipt = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    temporary = receipt.with_name(f".{receipt.name}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "candidate_sha": TARGET_SHA,
                "phase": "healthy",
                "phases": journal["phases"],
                "bootstrap": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary_inode = temporary.stat().st_ino
    events: list[str] = []
    real_replace = controller_module.os.replace
    real_atomic_write = controller_module._atomic_write_json

    def record_promotion(source, destination):
        if Path(source) == temporary and Path(destination) == receipt:
            assert _lock_is_held(config.lock_path)
            events.append("receipt-temp-promoted")
        return real_replace(source, destination)

    def forbid_receipt_rewrite(path, payload, *, mode):
        if Path(path) == receipt:
            pytest.fail("an exact recovered receipt must be reused, not rewritten")
        return real_atomic_write(path, payload, mode=mode)

    probe = BootstrapProbe(config, active_state="active")
    runner = FakeRunner(_scenario(config, call_hook=probe), events)
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(None, events),
    )

    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(controller_module.os, "replace", record_promotion)
        recovery_patch.setattr(
            controller_module,
            "_atomic_write_json",
            forbid_receipt_rewrite,
        )
        recovered = controller.deploy()

    _assert_status(recovered, "bootstrapped")
    assert events[0] == "receipt-temp-promoted"
    assert receipt.stat().st_ino == temporary_inode
    assert not temporary.exists()
    assert not config.bootstrap_journal_path.exists()


def test_exact_quarantine_temp_is_promoted_and_blocks_candidate_after_recovery(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA)
    candidate = _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_journal(config, phase="start_requested", backup=backup_record)
    reason = "recovered interrupted post-start activation"
    quarantine = config.quarantine_dir / f"{TARGET_SHA}.json"
    temporary = quarantine.with_name(f".{quarantine.name}.tmp")
    expected = {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "reason": reason,
    }
    temporary.write_text(
        json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary_inode = temporary.stat().st_ino
    events: list[str] = []
    real_replace = controller_module.os.replace
    real_atomic_write = controller_module._atomic_write_json

    def record_promotion(source, destination):
        if Path(source) == temporary and Path(destination) == quarantine:
            assert _lock_is_held(config.lock_path)
            events.append("quarantine-temp-promoted")
        return real_replace(source, destination)

    def forbid_quarantine_rewrite(path, payload, *, mode):
        if Path(path) == quarantine:
            pytest.fail("an exact recovered quarantine must be reused, not rewritten")
        return real_atomic_write(path, payload, mode=mode)

    runner = FakeRunner(_scenario(config), events)
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(None, events),
    )

    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(controller_module.os, "replace", record_promotion)
        recovery_patch.setattr(
            controller_module,
            "_atomic_write_json",
            forbid_quarantine_rewrite,
        )
        recovered = controller.deploy()

    _assert_status(recovered, "rejected")
    assert events[0] == "quarantine-temp-promoted"
    assert quarantine.stat().st_ino == temporary_inode
    assert _read_json(quarantine) == expected
    assert not temporary.exists()
    assert config.current_link.resolve() == previous
    assert candidate != previous
    assert not config.deployment_journal_path.exists()
    assert controller._candidate_is_quarantined(TARGET_SHA)


def test_exact_activation_receipt_temp_is_promoted_before_healthy_recovery(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _create_ready_release(config, LAST_SHA)
    _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_deployed_state(config, TARGET_SHA)
    _write_journal(config, phase="healthy", backup=backup_record)
    journal = _read_json(config.deployment_journal_path)
    expected = _activation_receipt_payload(journal)
    receipt = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    temporary = receipt.with_name(f".{receipt.name}.tmp")
    temporary.write_text(
        json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary_inode = temporary.stat().st_ino
    events: list[str] = []
    real_replace = controller_module.os.replace
    real_atomic_write = controller_module._atomic_write_json

    def record_promotion(source, destination):
        if Path(source) == temporary and Path(destination) == receipt:
            assert _lock_is_held(config.lock_path)
            events.append("activation-receipt-temp-promoted")
        return real_replace(source, destination)

    def forbid_receipt_rewrite(path, payload, *, mode):
        if Path(path) == receipt:
            pytest.fail("an exact recovered activation receipt must be reused")
        return real_atomic_write(path, payload, mode=mode)

    runner = FakeRunner(_scenario(config), events)
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(None, events),
    )

    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(controller_module.os, "replace", record_promotion)
        recovery_patch.setattr(
            controller_module,
            "_atomic_write_json",
            forbid_receipt_rewrite,
        )
        recovered = controller.deploy()

    _assert_status(recovered, "deployed")
    assert events[0] == "activation-receipt-temp-promoted"
    assert receipt.stat().st_ino == temporary_inode
    assert not temporary.exists()
    assert not config.deployment_journal_path.exists()
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{TARGET_SHA}\n"
    assert _read_json(receipt) == expected


def test_existing_exact_activation_receipt_discards_stale_exact_temp_under_lock(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _create_ready_release(config, LAST_SHA)
    _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_deployed_state(config, TARGET_SHA)
    _write_journal(config, phase="healthy", backup=backup_record)
    expected = _activation_receipt_payload(
        _read_json(config.deployment_journal_path)
    )
    receipt = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
    receipt.write_text(encoded, encoding="utf-8")
    receipt.chmod(0o600)
    receipt_inode = receipt.stat().st_ino
    temporary = receipt.with_name(f".{receipt.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.chmod(0o600)
    events: list[str] = []
    real_unlink = controller_module._unlink_regular_or_missing
    real_atomic_write = controller_module._atomic_write_json

    def record_temp_discard(path):
        if Path(path) == temporary:
            assert _lock_is_held(config.lock_path)
            events.append("activation-receipt-temp-discarded")
        return real_unlink(path)

    def forbid_receipt_rewrite(path, payload, *, mode):
        if Path(path) == receipt:
            pytest.fail("an existing exact activation receipt must be reused")
        return real_atomic_write(path, payload, mode=mode)

    runner = FakeRunner(_scenario(config), events)
    controller = _controller(
        controller_module,
        config,
        runner,
        FakeHttp(None, events),
    )

    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(
            controller_module,
            "_unlink_regular_or_missing",
            record_temp_discard,
        )
        recovery_patch.setattr(
            controller_module,
            "_atomic_write_json",
            forbid_receipt_rewrite,
        )
        recovered = controller.deploy()

    _assert_status(recovered, "deployed")
    assert events[0] == "activation-receipt-temp-discarded"
    assert not temporary.exists()
    assert receipt.stat().st_ino == receipt_inode
    assert _read_json(receipt) == expected
    assert not config.deployment_journal_path.exists()


@pytest.mark.parametrize(
    "unsafe_state",
    [
        pytest.param("mismatched-payload", id="mismatched-payload"),
        pytest.param("symlink", id="symlink"),
        pytest.param("writable-mode", id="writable-mode"),
    ],
)
def test_invalid_activation_receipt_temp_fails_closed_and_remains_for_inspection(
    controller_module,
    tmp_path,
    unsafe_state,
):
    config = _activation_config(controller_module, tmp_path)
    _create_ready_release(config, LAST_SHA)
    candidate = _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_deployed_state(config, TARGET_SHA)
    _write_journal(config, phase="healthy", backup=backup_record)
    expected = _activation_receipt_payload(
        _read_json(config.deployment_journal_path)
    )
    receipt = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    temporary = receipt.with_name(f".{receipt.name}.tmp")
    encoded = json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
    if unsafe_state == "mismatched-payload":
        mismatched = {**expected, "previous_sha": OTHER_SHA}
        temporary.write_text(
            json.dumps(mismatched, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
    elif unsafe_state == "symlink":
        untrusted = tmp_path / "untrusted-activation-receipt"
        untrusted.write_text(encoded, encoding="utf-8")
        temporary.symlink_to(untrusted)
    else:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.chmod(0o666)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(None, events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "atomic receipt temporary" in result.reason
    assert runner.calls == []
    assert http.calls == []
    assert temporary.exists() or temporary.is_symlink()
    assert not receipt.exists()
    assert config.deployment_journal_path.exists()
    assert config.current_link.resolve() == candidate


def test_conflicting_activation_receipt_evidence_fails_closed_without_discard(
    controller_module,
    tmp_path,
):
    config = _activation_config(controller_module, tmp_path)
    _create_ready_release(config, LAST_SHA)
    candidate = _create_ready_release(config, TARGET_SHA, make_current=True)
    writer = _create_wal_database(config.database_path)
    writer.close()
    _backup, backup_record = _write_recovery_backup(config)
    _apply_candidate_migration(config.database_path)
    _write_deployed_state(config, TARGET_SHA)
    _write_journal(config, phase="healthy", backup=backup_record)
    expected = _activation_receipt_payload(
        _read_json(config.deployment_journal_path)
    )
    receipt = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
    conflicting = {**expected, "previous_sha": OTHER_SHA}
    receipt.write_text(
        json.dumps(conflicting, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    temporary = receipt.with_name(f".{receipt.name}.tmp")
    temporary.write_text(
        json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(None, events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert "atomic receipt" in result.reason
    assert runner.calls == []
    assert http.calls == []
    assert _read_json(receipt) == conflicting
    assert _read_json(temporary) == expected
    assert config.deployment_journal_path.exists()
    assert config.current_link.resolve() == candidate


def test_exact_activation_journal_temp_is_promoted_before_recovery_dispatch(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous = _create_ready_release(config, LAST_SHA, make_current=True)
    _create_ready_release(config, TARGET_SHA)
    writer = _create_wal_database(config.database_path)
    writer.close()
    journal = {
        "version": 1,
        "candidate_sha": TARGET_SHA,
        "previous_sha": LAST_SHA,
        "phase": "prepared",
        "phases": ["prepared"],
        "backup": None,
        "database_existed": True,
    }
    temporary = config.deployment_journal_path.with_name(
        f".{config.deployment_journal_path.name}.tmp"
    )
    temporary.write_text(json.dumps(journal) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary_inode = temporary.stat().st_ino
    events: list[str] = []
    real_replace = controller_module.os.replace

    def record_promotion(source, destination):
        if (
            Path(source) == temporary
            and Path(destination) == config.deployment_journal_path
        ):
            assert _lock_is_held(config.lock_path)
            events.append("journal-temp-promoted")
        return real_replace(source, destination)

    runner = FakeRunner(
        _scenario(config, call_hook=ActivationProbe(config)),
        events,
    )
    http = FakeHttp(None, events)

    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(controller_module.os, "replace", record_promotion)
        recovered = _controller(
            controller_module,
            config,
            runner,
            http,
        ).deploy()

    _assert_status(recovered, "rejected")
    assert events[0] == "journal-temp-promoted"
    assert not temporary.exists()
    assert not config.deployment_journal_path.exists()
    assert config.current_link.resolve() == previous
    assert http.calls == []
    # Promotion preserves the inode until normal recovery clears the journal.
    assert temporary_inode > 0


def test_existing_atomic_destination_discards_stale_exact_temp_under_lock(
    controller_module,
    tmp_path,
    monkeypatch,
):
    config = _activation_config(controller_module, tmp_path)
    _write_deployed_state(config)
    _create_ready_release(config, LAST_SHA, make_current=True)
    final_inode = config.deployed_state_path.stat().st_ino
    temporary = config.deployed_state_path.with_name(
        f".{config.deployed_state_path.name}.tmp"
    )
    temporary.write_text(f"{TARGET_SHA}\n", encoding="ascii")
    temporary.chmod(0o644)
    events: list[str] = []
    real_unlink = controller_module._unlink_regular_or_missing

    def record_temp_discard(path):
        if Path(path) == temporary:
            assert _lock_is_held(config.lock_path)
            events.append("state-temp-discarded")
        return real_unlink(path)

    runner = FakeRunner(_scenario(config, heads=[LAST_SHA]), events)

    with monkeypatch.context() as recovery_patch:
        recovery_patch.setattr(
            controller_module,
            "_unlink_regular_or_missing",
            record_temp_discard,
        )
        result = _controller(
            controller_module,
            config,
            runner,
            FakeHttp(None, events),
        ).reconcile()

    _assert_status(result, "noop")
    assert events[0] == "state-temp-discarded"
    assert not temporary.exists()
    assert config.deployed_state_path.stat().st_ino == final_inode
    assert config.deployed_state_path.read_text(encoding="ascii") == f"{LAST_SHA}\n"


@pytest.mark.parametrize("namespace", ["receipt", "quarantine"])
@pytest.mark.parametrize(
    "unsafe_state",
    [
        pytest.param("symlink", id="symlink"),
        pytest.param("directory", id="directory"),
        pytest.param("hardlink", id="hardlink"),
        pytest.param("mode", id="writable-mode"),
        pytest.param("owner", id="wrong-owner"),
        pytest.param("unexpected-name", id="unexpected-name"),
    ],
)
def test_atomic_json_temp_with_unsafe_identity_fails_closed_before_effects(
    controller_module,
    tmp_path,
    monkeypatch,
    namespace,
    unsafe_state,
):
    if namespace == "receipt":
        config = _bootstrap_config(controller_module, tmp_path)
        release = _create_pending_bootstrap(config)
        _write_control_plane_manifest(config, phase="installed")
        journal = _read_json(config.bootstrap_journal_path)
        final = config.deployment_receipts_dir / f"{TARGET_SHA}.json"
        payload = {
            "version": 1,
            "candidate_sha": TARGET_SHA,
            "phase": "healthy",
            "phases": journal["phases"],
            "bootstrap": True,
        }
    else:
        config = _activation_config(controller_module, tmp_path)
        _write_deployed_state(config)
        _create_ready_release(config, LAST_SHA)
        release = _create_ready_release(config, TARGET_SHA, make_current=True)
        writer = _create_wal_database(config.database_path)
        writer.close()
        _backup, backup_record = _write_recovery_backup(config)
        _apply_candidate_migration(config.database_path)
        _write_journal(config, phase="start_requested", backup=backup_record)
        final = config.quarantine_dir / f"{TARGET_SHA}.json"
        payload = {
            "version": 1,
            "candidate_sha": TARGET_SHA,
            "reason": "recovered interrupted post-start activation",
        }
    temporary = final.with_name(f".{final.name}.tmp")
    if unsafe_state == "unexpected-name":
        temporary = temporary.with_name(f"{temporary.name}.extra")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    if unsafe_state == "symlink":
        outside = tmp_path / f"attacker-{namespace}-temp"
        outside.write_text(encoded, encoding="utf-8")
        temporary.symlink_to(outside)
    elif unsafe_state == "directory":
        temporary.mkdir()
    else:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.chmod(0o600)
        if unsafe_state == "hardlink":
            os.link(temporary, tmp_path / f"{namespace}-temp-hardlink")
        elif unsafe_state == "mode":
            temporary.chmod(0o666)
        elif unsafe_state == "owner":
            real_lstat = controller_module.os.lstat

            def wrong_temp_owner(path, *args, **kwargs):
                metadata = real_lstat(path, *args, **kwargs)
                if not args and not kwargs and Path(path) == temporary:
                    return _StatProxy(metadata, st_uid=metadata.st_uid + 1)
                return metadata

            monkeypatch.setattr(controller_module.os, "lstat", wrong_temp_owner)
    runner = FakeRunner(_scenario(config), [])
    http = FakeHttp(None, [])

    result = _controller(controller_module, config, runner, http).deploy()

    assert _status(result) in {"rejected", "intervention_required"}
    assert runner.calls == []
    assert http.calls == []
    assert temporary.exists() or temporary.is_symlink()
    assert not final.exists()
    assert config.current_link.resolve() == release
