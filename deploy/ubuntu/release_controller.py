#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol


SHA_RE = re.compile(r"[0-9a-f]{40}")
RESERVED_RELEASE_PATHS = {".ready.json", ".venv"}


class ResultStatus(StrEnum):
    BUSY = "busy"
    DEPLOYED = "deployed"
    NOOP = "noop"
    PREPARED = "prepared"
    REJECTED = "rejected"
    TRANSIENT = "transient"


@dataclass(frozen=True)
class ControllerResult:
    status: ResultStatus
    reason: str
    sha: str | None = None


@dataclass(frozen=True)
class ControllerConfig:
    repository: str = "noxiouz/honeybuy_tg"
    branch: str = "main"
    workflow_path: str = ".github/workflows/tests.yml"
    remote_url: str = "https://github.com/noxiouz/honeybuy_tg.git"
    source_repo: Path = Path("/var/lib/honeybuy-release-controller/repository")
    releases_dir: Path = Path("/opt/honeybuy-tg/releases")
    current_link: Path = Path("/opt/honeybuy-tg/current")
    deployed_state_path: Path = Path("/var/lib/honeybuy-release-controller/deployed-sha")
    database_backup_dir: Path = Path("/var/backups/honeybuy-tg")
    lock_path: Path = Path("/run/lock/honeybuy-release-controller.lock")
    allowed_signers_path: Path = Path("/etc/honeybuy-tg/allowed_signers")
    database_path: Path = Path("/var/lib/honeybuy-tg/honeybuy.sqlite3")
    uv_cache_dir: Path = Path("/var/cache/honeybuy-tg/uv")
    git_path: Path = Path("/usr/bin/git")
    uv_path: Path = Path("/usr/local/bin/uv")
    systemctl_path: Path = Path("/usr/bin/systemctl")
    service_name: str = "honeybuy-tg.service"
    build_uid: int | None = None
    build_gid: int | None = None
    release_uid: int = 0
    release_gid: int = 0
    runtime_uid: int | None = None
    runtime_gid: int | None = None
    empty_work_dir: Path = Path("/var/empty/honeybuy-healthcheck")
    api_timeout_seconds: float = 5.0
    command_timeout_seconds: float = 120.0
    gnupg_home: Path | None = None


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
    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path | None = None,
        uid: int | None = None,
        gid: int | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        clean_env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
        if env is not None:
            clean_env.update(env)
        return subprocess.run(
            tuple(str(argument) for argument in argv),
            cwd=None if cwd is None else str(cwd),
            env=clean_env,
            capture_output=True,
            text=False,
            check=False,
            timeout=self._timeout_seconds,
            preexec_fn=_drop_privileges(uid, gid),
        )


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
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessRunner(
            timeout_seconds=config.command_timeout_seconds
        )
        self.http = http or UrllibHttpClient()

    def reconcile(self) -> ControllerResult:
        self.config.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    return _result(ResultStatus.BUSY, "another reconcile is running")
                raise

            deployed_sha = self._read_deployed_sha()
            if deployed_sha is None:
                return _result(ResultStatus.REJECTED, "missing or invalid deployed sha")

            self.config.source_repo.mkdir(parents=True, exist_ok=True)
            self.config.releases_dir.mkdir(parents=True, exist_ok=True)

            remote_sha = self._fetch_and_resolve_main()
            if isinstance(remote_sha, ControllerResult):
                return remote_sha
            if not _is_canonical_sha(remote_sha):
                return _result(ResultStatus.REJECTED, "remote main is not canonical sha")

            if remote_sha == deployed_sha:
                return self._check_noop_release(remote_sha)

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

    def deploy(self) -> ControllerResult:
        result = self.reconcile()
        if result.status != ResultStatus.PREPARED or result.sha is None:
            return result
        return self._activate_release(result.sha)

    def _read_deployed_sha(self) -> str | None:
        try:
            value = self.config.deployed_state_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not _is_canonical_sha(value):
            return None
        return value

    def _fetch_and_resolve_main(self) -> str | ControllerResult:
        fetch = self.runner.run(
            (
                str(self.config.git_path),
                "-C",
                str(self.config.source_repo),
                "fetch",
                "--quiet",
                "--force",
                "--no-tags",
                self.config.remote_url,
                f"refs/heads/{self.config.branch}",
            )
        )
        if fetch.returncode != 0:
            return _result(ResultStatus.TRANSIENT, "failed to fetch remote main")

        resolved = self.runner.run(
            (
                str(self.config.git_path),
                "-C",
                str(self.config.source_repo),
                "rev-parse",
                "--verify",
                "FETCH_HEAD^{commit}",
            )
        )
        if resolved.returncode != 0:
            return _result(ResultStatus.TRANSIENT, "failed to resolve remote main")
        return _stdout_text(resolved).strip()

    def _require_green_workflow(self, sha: str) -> ControllerResult | None:
        try:
            payload = self.http.get_json(
                self._workflow_runs_url(),
                timeout=self.config.api_timeout_seconds,
            )
        except TimeoutError:
            return _result(ResultStatus.TRANSIENT, "github workflow api timed out", sha)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code <= 599:
                return _result(
                    ResultStatus.TRANSIENT,
                    f"github workflow api returned transient {exc.code}",
                    sha,
                )
            return _result(
                ResultStatus.REJECTED,
                f"github workflow api returned {exc.code}",
                sha,
            )
        except urllib.error.URLError:
            return _result(ResultStatus.TRANSIENT, "github workflow api unavailable", sha)
        except json.JSONDecodeError:
            return _result(ResultStatus.REJECTED, "github workflow api returned bad json")

        if not isinstance(payload, dict):
            return _result(ResultStatus.REJECTED, "github workflow api returned non-object")
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            return _result(ResultStatus.REJECTED, "github workflow runs are malformed")

        current_run: dict[str, Any] | None = None
        for candidate in runs:
            if isinstance(candidate, dict) and candidate.get("head_sha") == sha:
                current_run = candidate
                break
        if current_run is None:
            return _result(ResultStatus.REJECTED, "workflow result is not for current main", sha)

        identity_error = self._workflow_identity_error(current_run, sha)
        if identity_error is not None:
            return _result(ResultStatus.REJECTED, identity_error, sha)

        status = current_run.get("status")
        conclusion = current_run.get("conclusion")
        if status != "completed":
            return _result(ResultStatus.TRANSIENT, "current main workflow is not complete", sha)
        if conclusion != "success":
            return _result(ResultStatus.REJECTED, "current main workflow is not green", sha)
        return None

    def _workflow_runs_url(self) -> str:
        query = urllib.parse.urlencode(
            {
                "branch": self.config.branch,
                "event": "push",
                "per_page": "2",
            }
        )
        return (
            f"https://api.github.com/repos/{self.config.repository}/actions/workflows/"
            f"{urllib.parse.quote(Path(self.config.workflow_path).name, safe='')}/runs?{query}"
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
        if not self.config.allowed_signers_path.is_file():
            return _result(ResultStatus.REJECTED, "allowed signers file is missing", sha)

        verify_env = None
        if self.config.gnupg_home is not None:
            verify_env = {"GNUPGHOME": str(self.config.gnupg_home)}
        verified = self.runner.run(
            (
                str(self.config.git_path),
                "-C",
                str(self.config.source_repo),
                "-c",
                f"gpg.ssh.allowedSignersFile={self.config.allowed_signers_path}",
                "verify-commit",
                sha,
            ),
            env=verify_env,
        )
        if verified.returncode != 0:
            return _result(ResultStatus.REJECTED, "commit signature verification failed", sha)

        ancestor = self.runner.run(
            (
                str(self.config.git_path),
                "-C",
                str(self.config.source_repo),
                "merge-base",
                "--is-ancestor",
                deployed_sha,
                sha,
            )
        )
        if ancestor.returncode != 0:
            return _result(ResultStatus.REJECTED, "deployed sha is not an ancestor", sha)
        return None

    def _check_noop_release(self, sha: str) -> ControllerResult:
        release = self.config.releases_dir / sha
        if not self._ready_release_is_safe(release, sha):
            return _result(
                ResultStatus.REJECTED,
                "deployed release is missing a valid ready manifest",
                sha,
            )
        python = release / ".venv/bin/python"
        health = self.runner.run(
            (str(python), "-m", "honeybuy_tg", "healthcheck"),
            cwd=self.config.empty_work_dir,
            uid=self.config.runtime_uid,
            gid=self.config.runtime_gid,
            env={"DATABASE_PATH": str(self.config.database_path)},
        )
        if health.returncode != 0:
            return _result(ResultStatus.REJECTED, "deployed release healthcheck failed", sha)
        return _result(ResultStatus.NOOP, "remote main is already deployed and healthy", sha)

    def _prepare_release(self, sha: str, release: Path) -> ControllerResult | None:
        tree = self.runner.run(
            (
                str(self.config.git_path),
                "-C",
                str(self.config.source_repo),
                "ls-tree",
                "-rz",
                sha,
            )
        )
        if tree.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to inspect candidate tree", sha)
        if not _tree_is_safe(_stdout_bytes(tree)):
            return _result(ResultStatus.REJECTED, "candidate tree contains unsafe entries", sha)

        archive = self.runner.run(
            (
                str(self.config.git_path),
                "-C",
                str(self.config.source_repo),
                "archive",
                "--format=tar",
                sha,
            )
        )
        if archive.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to archive candidate tree", sha)

        try:
            members = _validated_tar_members(_stdout_bytes(archive))
        except ValueError as exc:
            return _result(ResultStatus.REJECTED, str(exc), sha)

        release.mkdir(mode=0o755)
        _extract_validated_tar(members, release)

        sync = self.runner.run(
            (
                str(self.config.uv_path),
                "sync",
                "--frozen",
                "--no-dev",
                "--no-editable",
            ),
            cwd=release,
            uid=self.config.build_uid,
            gid=self.config.build_gid,
            env={"UV_CACHE_DIR": str(self.config.uv_cache_dir)},
        )
        if sync.returncode != 0:
            return _result(ResultStatus.REJECTED, "dependency sync failed", sha)

        smoke = self.runner.run(
            (
                str(release / ".venv/bin/python"),
                "-c",
                "import honeybuy_tg",
            ),
            cwd=release,
            uid=self.config.build_uid,
            gid=self.config.build_gid,
        )
        if smoke.returncode != 0:
            return _result(ResultStatus.REJECTED, "import smoke failed", sha)

        try:
            _finalize_release_tree(
                release,
                sha,
                self.config.release_uid,
                self.config.release_gid,
            )
        except OSError as exc:
            return _result(ResultStatus.REJECTED, f"failed to finalize release: {exc}", sha)

        if not self._ready_release_is_safe(release, sha):
            return _result(ResultStatus.REJECTED, "prepared release failed validation", sha)
        return None

    def _activate_release(self, sha: str) -> ControllerResult:
        release = self.config.releases_dir / sha
        if not self._ready_release_is_safe(release, sha):
            return _result(ResultStatus.REJECTED, "prepared release failed validation", sha)
        if self.config.current_link.exists() and not self.config.current_link.is_symlink():
            return _result(ResultStatus.REJECTED, "current release path is not a symlink", sha)

        previous_target = self._current_release_target()
        backup_path = self._backup_database(sha)
        service_was_stopped = False

        stopped = self._systemctl("stop")
        if stopped.returncode != 0:
            return _result(ResultStatus.REJECTED, "failed to stop service", sha)
        service_was_stopped = True

        migrated = self.runner.run(
            (
                str(release / ".venv/bin/python"),
                "-m",
                "honeybuy_tg",
                "migrate",
            ),
            cwd=release,
            uid=self.config.runtime_uid,
            gid=self.config.runtime_gid,
            env={"DATABASE_PATH": str(self.config.database_path)},
        )
        if migrated.returncode != 0:
            self._restore_after_failed_activation(previous_target, backup_path, service_was_stopped)
            return _result(ResultStatus.REJECTED, "migration failed", sha)

        try:
            self._switch_current_link(release)
        except OSError as exc:
            self._restore_after_failed_activation(previous_target, backup_path, service_was_stopped)
            return _result(ResultStatus.REJECTED, f"failed to switch current release: {exc}", sha)

        started = self._systemctl("start")
        if started.returncode != 0:
            self._restore_after_failed_activation(previous_target, backup_path, True)
            return _result(ResultStatus.REJECTED, "failed to start service", sha)

        healthy = self.runner.run(
            (str(release / ".venv/bin/python"), "-m", "honeybuy_tg", "healthcheck"),
            cwd=self.config.empty_work_dir,
            uid=self.config.runtime_uid,
            gid=self.config.runtime_gid,
            env={"DATABASE_PATH": str(self.config.database_path)},
        )
        if healthy.returncode != 0:
            self._restore_after_failed_activation(previous_target, backup_path, True)
            return _result(ResultStatus.REJECTED, "startup healthcheck failed", sha)

        self._write_deployed_sha(sha)
        return _result(ResultStatus.DEPLOYED, "release deployed", sha)

    def _ready_release_is_safe(self, release: Path, sha: str) -> bool:
        if not release.is_dir() or release.is_symlink():
            return False
        if not _tree_metadata_is_safe(
            release,
            expected_uid=self.config.release_uid,
            expected_gid=self.config.release_gid,
        ):
            return False
        manifest_path = release / ".ready.json"
        uv_lock_path = release / "uv.lock"
        if not manifest_path.is_file() or not uv_lock_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if manifest.get("sha") != sha:
            return False
        try:
            actual_digest = hashlib.sha256(uv_lock_path.read_bytes()).hexdigest()
        except OSError:
            return False
        return manifest.get("uv_lock_sha256") == actual_digest

    def _backup_database(self, sha: str) -> Path | None:
        if not self.config.database_path.exists():
            return None
        self.config.database_backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self.config.database_backup_dir / (
            f"{self.config.database_path.name}.{sha}.bak"
        )
        shutil.copy2(self.config.database_path, backup_path)
        backup_path.chmod(0o600)
        return backup_path

    def _current_release_target(self) -> Path | None:
        if not self.config.current_link.is_symlink():
            return None
        try:
            return self.config.current_link.resolve(strict=True)
        except OSError:
            return None

    def _switch_current_link(self, release: Path) -> None:
        self.config.current_link.parent.mkdir(parents=True, exist_ok=True)
        temporary_link = self.config.current_link.parent / f".current.{release.name}.tmp"
        try:
            temporary_link.unlink()
        except FileNotFoundError:
            pass
        temporary_link.symlink_to(release)
        os.replace(temporary_link, self.config.current_link)

    def _restore_after_failed_activation(
        self,
        previous_target: Path | None,
        backup_path: Path | None,
        service_was_stopped: bool,
    ) -> None:
        if service_was_stopped:
            self._systemctl("stop")
        if previous_target is not None:
            self._switch_current_link(previous_target)
        elif self.config.current_link.is_symlink():
            self.config.current_link.unlink()
        if backup_path is not None:
            shutil.copy2(backup_path, self.config.database_path)
        elif self.config.database_path.exists():
            self.config.database_path.unlink()
        if service_was_stopped:
            self._systemctl("start")

    def _systemctl(self, action: str) -> subprocess.CompletedProcess[Any]:
        return self.runner.run(
            (
                str(self.config.systemctl_path),
                action,
                self.config.service_name,
            )
        )

    def _write_deployed_sha(self, sha: str) -> None:
        self.config.deployed_state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.deployed_state_path.with_suffix(".tmp")
        temporary.write_text(f"{sha}\n", encoding="utf-8")
        temporary.chmod(0o644)
        os.replace(temporary, self.config.deployed_state_path)


def _drop_privileges(uid: int | None, gid: int | None):
    if uid is None and gid is None:
        return None

    def demote() -> None:
        if gid is not None:
            os.setgid(gid)
        if uid is not None:
            os.setuid(uid)

    return demote


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


def _tree_is_safe(raw: bytes) -> bool:
    if not raw:
        return False
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            meta, raw_name = entry.split(b"\t", 1)
            mode, kind, _object = meta.decode("ascii").split(" ", 2)
            name = raw_name.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        if mode == "120000" or mode == "160000" or kind == "commit":
            return False
        if not _safe_relative_name(name):
            return False
    return True


def _safe_relative_name(name: str) -> bool:
    if not name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute():
        return False
    if any(part in ("", ".", "..") for part in path.parts):
        return False
    if path.parts[0] in RESERVED_RELEASE_PATHS:
        return False
    return True


def _validated_tar_members(raw: bytes) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive.getmembers():
            if not _safe_tar_member(member):
                raise ValueError(f"unsafe archive member: {member.name}")
            payload = None
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"missing archive payload: {member.name}")
                payload = source.read()
            members.append((member, payload))
    return members


def _extract_validated_tar(
    members: list[tuple[tarfile.TarInfo, bytes | None]],
    destination: Path,
) -> None:
    for member, payload in members:
        target = destination / member.name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(member.mode & 0o755)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            raise ValueError(f"missing archive payload: {member.name}")
        with target.open("xb") as output:
            output.write(payload)
        target.chmod(member.mode & 0o644)


def _safe_tar_member(member: tarfile.TarInfo) -> bool:
    if not _safe_relative_name(member.name):
        return False
    return member.isfile() or member.isdir()


def _finalize_release_tree(release: Path, sha: str, uid: int, gid: int) -> None:
    uv_lock = release / "uv.lock"
    digest = hashlib.sha256(uv_lock.read_bytes()).hexdigest()
    manifest = {"sha": sha, "uv_lock_sha256": digest}
    (release / ".ready.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    for path in sorted(release.rglob("*"), key=lambda candidate: len(candidate.parts)):
        if path.is_symlink():
            raise OSError(f"release contains symlink: {path}")
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            mode = 0o755
        elif stat.S_ISREG(metadata.st_mode):
            mode = metadata.st_mode & 0o755
            mode &= ~0o022
            if mode == 0:
                mode = 0o644
        else:
            raise OSError(f"release contains unsafe file type: {path}")
        os.chown(path, uid, gid)
        path.chmod(mode)
    os.chown(release, uid, gid)
    release.chmod(0o755)


def _tree_metadata_is_safe(root: Path, *, expected_uid: int, expected_gid: int) -> bool:
    for path in (root, *root.rglob("*")):
        try:
            metadata = path.lstat()
        except OSError:
            return False
        if path.is_symlink():
            return False
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            return False
        if metadata.st_uid != expected_uid or metadata.st_gid != expected_gid:
            return False
        if metadata.st_mode & 0o022:
            return False
    return True


def _build_default_config() -> ControllerConfig:
    return ControllerConfig(
        build_uid=_uid_for_user("honeybuy-build"),
        build_gid=_gid_for_user("honeybuy-build"),
        runtime_uid=_uid_for_user("honeybuy"),
        runtime_gid=_gid_for_user("honeybuy"),
    )


def _uid_for_user(name: str) -> int | None:
    try:
        import pwd

        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def _gid_for_user(name: str) -> int | None:
    try:
        import pwd

        return pwd.getpwnam(name).pw_gid
    except KeyError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Honeybuy release controller")
    parser.add_argument("command", choices=("reconcile",))
    args = parser.parse_args(argv)
    if args.command == "reconcile":
        result = ReleaseController(config=_build_default_config()).reconcile()
        print(f"{result.status.value}: {result.reason}")
        return 0 if result.status in (ResultStatus.NOOP, ResultStatus.PREPARED) else 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
