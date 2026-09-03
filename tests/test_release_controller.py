from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


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
        if self.error is not None:
            raise self.error
        return self.payload


@dataclass
class RunnerScenario:
    git_path: Path
    uv_path: Path
    remote_heads: list[str]
    tree_output: bytes
    archive_output: bytes
    verify_returncode: int = 0
    ancestor_returncode: int = 0
    sync_returncode: int = 0
    smoke_returncode: int = 0
    healthcheck_returncode: int = 0
    migrate_returncode: int = 0
    systemctl_returncode: int = 0
    _head_index: int = 0

    def respond(self, call: RunCall) -> subprocess.CompletedProcess:
        kind = _command_kind(call)
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
    if "systemctl" in Path(argv[0]).name:
        return "systemctl"
    return "unknown"


def _safe_tree() -> bytes:
    entries = (
        ("100644", "blob", "pyproject.toml"),
        ("100644", "blob", "uv.lock"),
        ("100644", "blob", "src/honeybuy_tg/__init__.py"),
    )
    return b"".join(
        f"{mode} {kind} {OBJECT_SHA}\t{name}\0".encode()
        for mode, kind, name in entries
    )


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
    return _archive(
        [
            ("pyproject.toml", b"[project]\nname='honeybuy-tg'\n", None),
            ("uv.lock", b"version = 1\n", None),
            ("src/honeybuy_tg/__init__.py", b"__all__ = []\n", None),
        ]
    )


def _workflow_run(
    sha: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    branch: str = BRANCH,
    event: str = "push",
    path: str = WORKFLOW_PATH,
    repository: str = REPOSITORY,
) -> dict[str, object]:
    return {
        "id": 100,
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

    return controller_module.ControllerConfig(
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


def _write_deployed_state(config, sha: str = LAST_SHA) -> None:
    config.deployed_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.deployed_state_path.write_text(f"{sha}\n")
    config.deployed_state_path.chmod(0o644)


def _create_ready_release(config, sha: str, *, make_current: bool = False) -> Path:
    release = config.releases_dir / sha
    release.mkdir(parents=True)
    uv_lock = release / "uv.lock"
    uv_lock.write_bytes(b"version = 1\n")
    python_path = release / ".venv/bin/python"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"test python shim")
    python_path.chmod(0o755)
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


def _scenario(config, *, heads: list[str] | None = None, **overrides) -> RunnerScenario:
    values: dict[str, Any] = {
        "git_path": config.git_path,
        "uv_path": config.uv_path,
        "remote_heads": heads or [TARGET_SHA, TARGET_SHA],
        "tree_output": _safe_tree(),
        "archive_output": _safe_archive(),
    }
    values.update(overrides)
    return RunnerScenario(**values)


def _controller(
    controller_module: ModuleType,
    config,
    runner: FakeRunner,
    http: FakeHttp,
):
    return controller_module.ReleaseController(
        config=config,
        runner=runner,
        http=http,
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
    assert int(query["per_page"][0]) >= 2


def _preparation_calls(runner: FakeRunner) -> list[RunCall]:
    return [
        call
        for call in runner.calls
        if _command_kind(call)
        in {"ls-tree", "archive", "uv-sync", "import-smoke", "systemctl"}
    ]


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
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert runner.calls[0].argv == (
        str(config.git_path),
        "-C",
        str(config.source_repo),
        "fetch",
        "--quiet",
        "--force",
        "--no-tags",
        REMOTE_URL,
        "refs/heads/main",
    )
    assert runner.calls[1].argv == (
        str(config.git_path),
        "-C",
        str(config.source_repo),
        "rev-parse",
        "--verify",
        "FETCH_HEAD^{commit}",
    )
    assert len(http.calls) == 1
    requested_url, timeout = http.calls[0]
    _assert_workflow_request(requested_url)
    assert timeout == config.api_timeout_seconds
    assert events[:3] == ["run:fetch", "run:rev-parse", "http:workflow"]


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
        _scenario(config, ancestor_returncode=1),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    ancestor_call = next(
        call for call in runner.calls if _command_kind(call) == "merge-base"
    )
    assert ancestor_call.argv[-2:] == (LAST_SHA, TARGET_SHA)
    assert _preparation_calls(runner) == []


def test_same_healthy_sha_is_noop_without_preparation_or_service_effects(
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
    assert list(config.empty_work_dir.iterdir()) == []
    assert release not in config.empty_work_dir.parents
    assert config.current_link.resolve() == current_before
    assert config.database_path.read_bytes() == database_before


def test_commit_verification_uses_only_external_allowed_signers_file(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    verify_call = next(
        call for call in runner.calls if _command_kind(call) == "verify-commit"
    )
    assert verify_call.argv == (
        str(config.git_path),
        "-C",
        str(config.source_repo),
        "-c",
        f"gpg.ssh.allowedSignersFile={config.allowed_signers_path}",
        "verify-commit",
        TARGET_SHA,
    )
    assert config.releases_dir not in config.allowed_signers_path.parents
    assert config.source_repo not in config.allowed_signers_path.parents


def test_failed_or_wrong_commit_signature_is_rejected_before_preparation(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    events: list[str] = []
    runner = FakeRunner(_scenario(config, verify_returncode=1), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "rejected")
    assert any(_command_kind(call) == "verify-commit" for call in runner.calls)
    assert _preparation_calls(runner) == []


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
    runner = FakeRunner(_scenario(config, tree_output=tree_output), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

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
    runner = FakeRunner(_scenario(config, archive_output=archive_output), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

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
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

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
    assert sync_call.cwd == release
    assert sync_call.uid == config.build_uid
    assert sync_call.gid == config.build_gid
    assert sync_call.env == {"UV_CACHE_DIR": str(config.uv_cache_dir)}

    smoke_call = next(
        call for call in runner.calls if _command_kind(call) == "import-smoke"
    )
    assert smoke_call.argv == (
        str(release / ".venv/bin/python"),
        "-c",
        "import honeybuy_tg",
    )
    assert smoke_call.cwd == release
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
        _scenario(
            config,
            sync_returncode=sync_returncode,
            smoke_returncode=smoke_returncode,
        ),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

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
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "prepared")
    assert (release / ".ready.json").is_file()
    kinds = [_command_kind(call) for call in runner.calls]
    assert "ls-tree" not in kinds
    assert "archive" not in kinds
    assert "uv-sync" not in kinds
    assert "import-smoke" not in kinds
    assert kinds.count("fetch") == 2


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
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

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
        _scenario(config, heads=[TARGET_SHA, OTHER_SHA]),
        events,
    )
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).reconcile()

    _assert_status(result, "transient")
    kinds = [_command_kind(call) for call in runner.calls]
    assert kinds.count("fetch") == 2
    assert kinds.count("rev-parse") == 2
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


def test_deploy_activates_prepared_release_with_backup_migrate_health_and_state(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.database_path.write_bytes(b"database sentinel")
    events: list[str] = []
    runner = FakeRunner(_scenario(config), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "deployed")
    release = config.releases_dir / TARGET_SHA
    assert config.current_link.is_symlink()
    assert config.current_link.resolve() == release
    assert config.deployed_state_path.read_text() == f"{TARGET_SHA}\n"
    backups = list(config.database_backup_dir.glob("honeybuy.sqlite3.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"database sentinel"
    migrate = next(call for call in runner.calls if _command_kind(call) == "migrate")
    assert migrate.argv == (
        str(release / ".venv/bin/python"),
        "-m",
        "honeybuy_tg",
        "migrate",
    )
    assert migrate.cwd == release
    assert migrate.uid == config.runtime_uid
    assert migrate.gid == config.runtime_gid
    assert migrate.env == {"DATABASE_PATH": str(config.database_path)}
    healthcheck = [
        call for call in runner.calls if _command_kind(call) == "healthcheck"
    ][-1]
    assert healthcheck.cwd == config.empty_work_dir
    assert healthcheck.uid == config.runtime_uid
    assert healthcheck.gid == config.runtime_gid
    assert healthcheck.env == {"DATABASE_PATH": str(config.database_path)}
    systemctl_calls = [
        call.argv for call in runner.calls if _command_kind(call) == "systemctl"
    ]
    assert systemctl_calls == [
        (str(config.systemctl_path), "stop", config.service_name),
        (str(config.systemctl_path), "start", config.service_name),
    ]


def test_deploy_rolls_back_link_and_database_when_startup_health_fails(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    previous_release = _create_ready_release(config, LAST_SHA, make_current=True)
    config.database_path.write_bytes(b"database before deploy")
    events: list[str] = []
    runner = FakeRunner(_scenario(config, healthcheck_returncode=1), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert config.current_link.resolve() == previous_release
    assert config.database_path.read_bytes() == b"database before deploy"
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
    systemctl_calls = [
        call.argv for call in runner.calls if _command_kind(call) == "systemctl"
    ]
    assert systemctl_calls == [
        (str(config.systemctl_path), "stop", config.service_name),
        (str(config.systemctl_path), "start", config.service_name),
        (str(config.systemctl_path), "stop", config.service_name),
        (str(config.systemctl_path), "start", config.service_name),
    ]


def test_deploy_returns_prepare_failures_without_service_or_database_effects(
    controller_module,
    tmp_path,
):
    config = _config(controller_module, tmp_path)
    _write_deployed_state(config)
    config.database_path.write_bytes(b"database sentinel")
    before_database = config.database_path.read_bytes()
    events: list[str] = []
    runner = FakeRunner(_scenario(config, verify_returncode=1), events)
    http = FakeHttp(_workflow_payload(_workflow_run(TARGET_SHA)), events)

    result = _controller(controller_module, config, runner, http).deploy()

    _assert_status(result, "rejected")
    assert not any(_command_kind(call) == "systemctl" for call in runner.calls)
    assert config.database_path.read_bytes() == before_database
    assert config.deployed_state_path.read_text() == f"{LAST_SHA}\n"
