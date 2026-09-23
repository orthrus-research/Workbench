#!/usr/bin/env python3

"""Fail-closed, disposable Blueprints candidate simulation."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from typing import Any, Callable, NoReturn

from jsonschema import Draft202012Validator

from workbench_blueprints import planner, standards
from workbench_blueprints.layout import SCHEMA_ROOT, WORKBENCH_ROOT


REPO_ROOT = WORKBENCH_ROOT
ENVIRONMENT_SCHEMA = (
    SCHEMA_ROOT / "blueprints-environment-lock-v1.schema.json"
)
SIMULATION_EVIDENCE_SCHEMA = (
    SCHEMA_ROOT / "blueprints-simulation-evidence-v1.schema.json"
)
ENGINE_CONTRACT_ID = "BLUEPRINTS-EXECUTABLE-ENGINE-V1"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
UNIVERSAL_STAGES = (
    "schema-standard-validity",
    "composition-parameter-validity",
    "authorized-path-allocation-collision",
    "deterministic-regeneration",
    "target-state-consistency",
    "pinned-formatter-stability",
    "isolated-compilation",
    "target-existing-tests",
    "generated-invariant-tests",
    "generated-collision-tests",
    "generated-determinism-tests",
    "generated-placement-tests",
)
COMMAND_STAGES = ("isolated-compilation", "target-existing-tests")

DependencyProvider = Callable[[dict[str, Any]], bytes]


class SimulationDiagnostic(Exception):
    """A stable fail-closed simulator diagnostic."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.location = location
        self.message = message

    def __str__(self) -> str:
        return f"{self.code} {self.location}: {self.message}"


def _fail(code: str, location: str, message: str) -> NoReturn:
    raise SimulationDiagnostic(code, location, message)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(standards.canonical_json(value).encode("utf-8"))


def _read_regular(path: Path, code: str) -> bytes:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
    except OSError as exc:
        _fail(code, str(path), str(exc))
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            _fail(code, str(path), "path is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("BPX100_SCHEMA_READ", str(path), str(exc))
    if not isinstance(value, dict):
        _fail("BPX100_SCHEMA_READ", str(path), "schema root is not an object")
    return value


def _pointer(parts: Any) -> str:
    encoded = [
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    ]
    return "/" + "/".join(encoded) if encoded else "/"


def _validate(value: dict[str, Any], path: Path, source: str) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema(path)).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        _fail(
            "BPX101_SCHEMA",
            f"{source}#{_pointer(error.absolute_path)}",
            error.message,
        )


def compile_environment_lock(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate one approved, content-bound environment."""

    try:
        normalized = planner._normalize_json(copy.deepcopy(value))
    except planner.PlannerDiagnostic as exc:
        _fail("BPX102_ENVIRONMENT_VALUE", exc.location, exc.message)
    if not isinstance(normalized, dict):
        _fail("BPX102_ENVIRONMENT_VALUE", "/", "lock must be an object")
    _validate(normalized, ENVIRONMENT_SCHEMA, "environment-lock")
    normalized["dependencies"] = sorted(
        normalized["dependencies"], key=lambda row: row["id"]
    )
    normalized["commands"] = sorted(
        normalized["commands"],
        key=lambda row: COMMAND_STAGES.index(row["stage_id"]),
    )
    dependency_ids = [row["id"] for row in normalized["dependencies"]]
    if len(dependency_ids) != len(set(dependency_ids)):
        _fail(
            "BPX103_ENVIRONMENT_DUPLICATE",
            "/dependencies",
            "dependency ids must be unique",
        )
    command_ids = [row["stage_id"] for row in normalized["commands"]]
    if command_ids != list(COMMAND_STAGES):
        _fail(
            "BPX104_ENVIRONMENT_COMMANDS",
            "/commands",
            "compile and existing-test commands must occur exactly once",
        )
    known = set(dependency_ids)
    for index, command in enumerate(normalized["commands"]):
        required = command["dependency_ids"]
        if any(item not in known for item in required):
            _fail(
                "BPX105_UNKNOWN_DEPENDENCY",
                f"/commands/{index}/dependency_ids",
                "command references an unlocked dependency",
            )
        if command["argv"][0] not in required:
            _fail(
                "BPX106_UNBOUND_EXECUTABLE",
                f"/commands/{index}/argv/0",
                "command executable must be one of its locked dependencies",
            )
    executable = Path(normalized["isolation"]["executable"])
    if not executable.is_absolute() or executable.is_symlink():
        _fail(
            "BPX107_ISOLATOR",
            "/isolation/executable",
            "isolator must be an absolute, non-symlink regular file",
        )
    content = _read_regular(executable, "BPX107_ISOLATOR")
    if _digest_bytes(content) != normalized["isolation"]["executable_sha256"]:
        _fail(
            "BPX108_ISOLATOR_DIGEST",
            str(executable),
            "isolator bytes do not match the environment lock",
        )
    return normalized


def environment_lock_sha256(value: dict[str, Any]) -> str:
    return _digest_json(compile_environment_lock(value))


def authority_state_sha256(
    registry_root: Path,
    asset_root: Path,
    ledger_path: Path,
) -> str:
    """Return the exact admitted registry/ledger authority fingerprint."""

    try:
        registry = standards.check_registry(
            registry_root, asset_root=asset_root
        )
        ledger = standards.validate_allocation_ledger(
            ledger_path,
            registry_root=registry_root,
            asset_root=asset_root,
            registry=registry,
        )
    except standards.StandardDiagnostic as exc:
        _fail("BPX125_AUTHORITY", exc.location, exc.message)
    return _digest_json(
        {
            "registry_id": registry["registry_id"],
            "ledger_id": ledger["ledger_id"],
            "standard_ids": [
                row["standard_id"] for row in registry["standards"]
            ],
        }
    )


class DependencyCache:
    """Private content-addressed cache populated only through an injected policy."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.is_symlink():
            _fail("BPX109_CACHE_ROOT", str(self.root), "cache root is a symlink")
        if self.root.exists() and not self.root.is_dir():
            _fail(
                "BPX109_CACHE_ROOT",
                str(self.root),
                "cache root is not a directory",
            )

    def _path(self, digest: str) -> Path:
        return self.root / "objects" / digest[:2] / digest

    def ensure(
        self,
        definition: dict[str, Any],
        provider: DependencyProvider | None = None,
    ) -> Path:
        path = self._path(definition["sha256"])
        if path.exists():
            if path.is_symlink():
                _fail(
                    "BPX110_DEPENDENCY_CACHE",
                    str(path),
                    "cached dependency is a symlink",
                )
            content = _read_regular(path, "BPX110_DEPENDENCY_CACHE")
        else:
            if provider is None:
                _fail(
                    "BPX111_DEPENDENCY_UNAVAILABLE",
                    definition["id"],
                    "approved dependency provider is unavailable",
                )
            try:
                content = provider(copy.deepcopy(definition))
            except SimulationDiagnostic:
                raise
            except Exception as exc:
                _fail(
                    "BPX111_DEPENDENCY_UNAVAILABLE",
                    definition["id"],
                    f"approved provider failed: {type(exc).__name__}",
                )
            if not isinstance(content, bytes):
                _fail(
                    "BPX112_DEPENDENCY_PROVIDER",
                    definition["id"],
                    "provider must return bytes",
                )
            if (
                len(content) != definition["size"]
                or _digest_bytes(content) != definition["sha256"]
            ):
                _fail(
                    "BPX113_DEPENDENCY_DIGEST",
                    definition["id"],
                    "acquired bytes do not match the approved lock",
                )
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            objects = self.root / "objects"
            objects.mkdir(mode=0o700, exist_ok=True)
            path.parent.mkdir(mode=0o700, exist_ok=True)
            if any(row.is_symlink() for row in (self.root, objects, path.parent)):
                _fail(
                    "BPX109_CACHE_ROOT",
                    str(path.parent),
                    "cache path contains a symlink",
                )
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=".dependency.",
                    delete=False,
                ) as handle:
                    temporary = handle.name
                    os.fchmod(handle.fileno(), 0o500)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                temporary = None
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        if (
            len(content) != definition["size"]
            or _digest_bytes(content) != definition["sha256"]
        ):
            _fail(
                "BPX110_DEPENDENCY_CACHE",
                str(path),
                "cached bytes do not match the approved lock",
            )
        os.chmod(path, 0o500)
        return path


class SimulationEvidenceStore:
    """Private content-addressed simulation evidence."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.is_symlink():
            _fail(
                "BPX115_EVIDENCE_ROOT",
                str(self.root),
                "evidence root is a symlink",
            )
        if self.root.exists() and not self.root.is_dir():
            _fail(
                "BPX115_EVIDENCE_ROOT",
                str(self.root),
                "evidence root is not a directory",
            )

    def put(self, evidence: dict[str, Any]) -> str:
        _validate(evidence, SIMULATION_EVIDENCE_SCHEMA, "simulation-evidence")
        content = standards.canonical_json(evidence).encode("utf-8")
        digest = _digest_bytes(content)
        path = self.root / "objects" / digest[:2] / f"{digest}.json"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or path.parent.is_symlink():
            _fail(
                "BPX115_EVIDENCE_ROOT",
                str(path.parent),
                "evidence path contains a symlink",
            )
        if path.exists():
            if _read_regular(path, "BPX116_EVIDENCE_COLLISION") != content:
                _fail(
                    "BPX116_EVIDENCE_COLLISION",
                    str(path),
                    "content-addressed evidence collision",
                )
        else:
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=".evidence.",
                    delete=False,
                ) as handle:
                    temporary = handle.name
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                temporary = None
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(path, 0o600)
        return "local-simulation-evidence:sha256:" + digest

    def read(self, locator: str) -> dict[str, Any]:
        match = re.fullmatch(
            r"local-simulation-evidence:sha256:([0-9a-f]{64})", locator
        )
        if match is None:
            _fail("BPX149_EVIDENCE_LOCATOR", "/", "invalid evidence locator")
        digest = match.group(1)
        path = self.root / "objects" / digest[:2] / f"{digest}.json"
        if (
            self.root.is_symlink()
            or path.parent.is_symlink()
            or path.is_symlink()
        ):
            _fail(
                "BPX115_EVIDENCE_ROOT",
                str(path),
                "evidence path contains a symlink",
            )
        content = _read_regular(path, "BPX150_EVIDENCE_MISSING")
        if _digest_bytes(content) != digest:
            _fail(
                "BPX151_EVIDENCE_DIGEST",
                locator,
                "evidence bytes drifted",
            )
        try:
            evidence = json.loads(content)
        except json.JSONDecodeError as exc:
            _fail("BPX152_EVIDENCE_JSON", locator, str(exc))
        if (
            not isinstance(evidence, dict)
            or standards.canonical_json(evidence).encode("utf-8") != content
        ):
            _fail(
                "BPX153_EVIDENCE_CANONICAL",
                locator,
                "evidence is not canonical",
            )
        _validate(evidence, SIMULATION_EVIDENCE_SCHEMA, locator)
        for row in evidence["gates"]:
            projected = dict(row)
            observed = projected.pop("evidence_sha256")
            if observed != _digest_json(projected):
                _fail(
                    "BPX154_GATE_EVIDENCE",
                    f"{locator}:{row['ordinal']}",
                    "gate evidence identity drifted",
                )
        return evidence


def _git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    try:
        git = planner.configured_git_executable()
    except planner.BlueprintsGitBindingError as exc:
        _fail("BPX117_GIT", str(repository), str(exc))
    result = subprocess.run(
        [git, "-C", str(repository), *arguments],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        _fail(
            "BPX117_GIT",
            str(repository),
            result.stderr.decode("utf-8", "replace").strip(),
        )
    return result.stdout


def _safe_parent(root: Path, relative: str) -> Path:
    planner._safe_path(relative, directory=False, location=relative)
    cursor = root
    parts = PurePosixPath(relative).parts
    for part in parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail(
                "BPX118_PATH_SYMLINK",
                relative,
                "path contains a symlinked parent",
            )
        if cursor.exists() and not cursor.is_dir():
            _fail(
                "BPX119_PATH_KIND",
                relative,
                "path parent is not a directory",
            )
        cursor.mkdir(mode=0o700, exist_ok=True)
    return root.joinpath(*parts)


def _source_worktree_bytes(root: Path, row: dict[str, Any]) -> bytes | None:
    path = root / row["path"]
    if row["kind"] == "deleted":
        if path.exists() or path.is_symlink():
            _fail(
                "BPX120_TARGET_RACE",
                row["path"],
                "expected source path to remain deleted",
            )
        return None
    if row["kind"] == "symlink":
        try:
            content = os.readlink(os.fsencode(path))
        except OSError as exc:
            _fail("BPX120_TARGET_RACE", row["path"], str(exc))
    else:
        content = _read_regular(path, "BPX120_TARGET_RACE")
    if _digest_bytes(content) != row["worktree_sha256"]:
        _fail(
            "BPX120_TARGET_RACE",
            row["path"],
            "source bytes changed after target capture",
        )
    return content


def _replace_worktree_path(
    root: Path, row: dict[str, Any], content: bytes | None
) -> None:
    path = _safe_parent(root, row["path"])
    if path.exists() or path.is_symlink():
        if path.is_dir() and not path.is_symlink():
            _fail("BPX119_PATH_KIND", row["path"], "target path is a directory")
        path.unlink()
    if content is None:
        return
    if row["kind"] == "symlink":
        os.symlink(os.fsdecode(content), path)
        return
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        0o700 if row["mode"] == "100755" else 0o600,
    )
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o755 if row["mode"] == "100755" else 0o644)


def _reconstruct_target(
    source: Path,
    destination: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        git = planner.configured_git_executable()
    except planner.BlueprintsGitBindingError as exc:
        _fail("BPX117_GIT", str(source), str(exc))
    result = subprocess.run(
        [
            git,
            "clone",
            "--quiet",
            "--no-local",
            "--no-hardlinks",
            str(source),
            str(destination),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        _fail(
            "BPX117_GIT",
            str(source),
            result.stderr.decode("utf-8", "replace").strip(),
        )
    _git(destination, "checkout", "--quiet", "--detach", manifest["revision"])
    for row in manifest["entries"]:
        if (row["head_sha256"], row["head_mode"]) != (
            row["index_sha256"],
            row["index_mode"],
        ):
            if row["index_sha256"] is None:
                _git(destination, "update-index", "--force-remove", "--", row["path"])
            else:
                index_record = _git(
                    source, "ls-files", "-s", "-z", "--", row["path"]
                )
                records = [
                    record for record in index_record.split(b"\0") if record
                ]
                if len(records) != 1:
                    _fail(
                        "BPX120_TARGET_RACE",
                        row["path"],
                        "source index entry changed after target capture",
                    )
                metadata, separator, raw_path = records[0].partition(b"\t")
                try:
                    mode, object_id, stage = metadata.decode("ascii").split(" ")
                    decoded_path = raw_path.decode("utf-8")
                except (UnicodeDecodeError, ValueError) as exc:
                    _fail("BPX120_TARGET_RACE", row["path"], str(exc))
                if (
                    separator != b"\t"
                    or stage != "0"
                    or decoded_path != row["path"]
                    or mode.zfill(6) != row["index_mode"]
                ):
                    _fail(
                        "BPX120_TARGET_RACE",
                        row["path"],
                        "source index identity changed after target capture",
                    )
                source_content = _git(
                    source, "cat-file", "blob", object_id
                )
                if _digest_bytes(source_content) != row["index_sha256"]:
                    _fail(
                        "BPX120_TARGET_RACE",
                        row["path"],
                        "source index changed after target capture",
                    )
                destination_object_id = _git(
                    destination, "hash-object", "-w", "--stdin",
                    input_bytes=source_content,
                ).decode("ascii").strip()
                _git(
                    destination,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"{row['index_mode']},{destination_object_id},{row['path']}",
                )
        content = _source_worktree_bytes(source, row)
        _replace_worktree_path(destination, row, content)
    current_source = planner.capture_target_state(
        source, manifest["repository_id"]
    )
    if current_source != manifest:
        _fail(
            "BPX120_TARGET_RACE",
            str(source),
            "source target changed during isolated reconstruction",
        )
    captured = planner.capture_target_state(
        destination, manifest["repository_id"]
    )
    if captured != manifest:
        _fail(
            "BPX121_RECONSTRUCTION",
            str(destination),
            "isolated target does not reproduce the exact manifest",
        )
    return captured


def _sealed_operations(
    candidate: dict[str, Any],
    plan: dict[str, Any],
    sealed_store: planner.SealedStore,
) -> list[dict[str, Any]]:
    payload = sealed_store.read(candidate["sealed_locator"])
    expected = {
        "plan_id": candidate["plan_id"],
        "target_state_id": candidate["target_state_id"],
        "standard_ids": candidate["standard_ids"],
        "content_manifest_sha256": candidate["content_manifest_sha256"],
    }
    if any(payload[key] != value for key, value in expected.items()):
        _fail(
            "BPX122_SEALED_BINDING",
            candidate["sealed_locator"],
            "sealed candidate does not match its public binding",
        )
    public_operations = [
        {
            "ordinal": row["ordinal"],
            "operation": row["operation"],
            "path": row["path"],
            "content_sha256": row["content_sha256"],
        }
        for row in payload["operations"]
    ]
    if public_operations != plan["operations"]:
        _fail(
            "BPX122_SEALED_BINDING",
            candidate["sealed_locator"],
            "sealed operations do not match the plan",
        )
    return payload["operations"]


def _apply_candidate(
    root: Path,
    operations: list[dict[str, Any]],
) -> None:
    for row in operations:
        path = _safe_parent(root, row["path"])
        exists = path.exists() or path.is_symlink()
        if row["operation"] == "create" and exists:
            _fail("BPX123_CREATE_COLLISION", row["path"], "path already exists")
        if row["operation"] in {"update", "delete"} and not exists:
            _fail("BPX124_UPDATE_MISSING", row["path"], "path does not exist")
        if exists and path.is_dir() and not path.is_symlink():
            _fail("BPX119_PATH_KIND", row["path"], "operation path is a directory")
        if row["operation"] == "delete":
            path.unlink()
            continue
        content = base64.b64decode(row["content_base64"], validate=True)
        previous_mode = path.stat(follow_symlinks=False).st_mode if exists else 0
        if exists:
            path.unlink()
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            os.write(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        executable = bool(previous_mode & stat.S_IXUSR)
        os.chmod(path, 0o755 if executable else 0o644)


def _changed_paths(
    before: dict[str, Any], after: dict[str, Any]
) -> set[str]:
    first = {row["path"]: row for row in before["entries"]}
    second = {row["path"]: row for row in after["entries"]}
    return {
        path
        for path in set(first) | set(second)
        if first.get(path) != second.get(path)
    }


def _command_result(
    lock: dict[str, Any],
    worktree: Path,
    argv: list[str],
    dependencies: dict[str, Path],
    *,
    hook: Path | None = None,
) -> dict[str, Any]:
    isolator = lock["isolation"]["executable"]
    arguments = [
        isolator,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for system_path in ("/bin", "/lib", "/lib64"):
        path = Path(system_path)
        if not path.exists():
            continue
        if path.is_symlink():
            arguments.extend(
                ["--symlink", os.readlink(system_path), system_path]
            )
        else:
            arguments.extend(["--ro-bind", system_path, system_path])
    arguments.extend(
        [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(worktree),
            "/work",
            "--dir",
            "/deps",
        ]
    )
    for dependency_id, path in sorted(dependencies.items()):
        arguments.extend(["--ro-bind", str(path), f"/deps/{dependency_id}"])
    if hook is not None:
        arguments.extend(["--ro-bind", str(hook), "/blueprints-hook"])
    arguments.extend(
        [
            "--chdir",
            "/work",
            "--clearenv",
            "--setenv",
            "PATH",
            "/deps:/usr/bin:/bin",
        ]
    )
    resolved = list(argv)
    if hook is None:
        resolved[0] = f"/deps/{resolved[0]}"
    else:
        resolved = ["/bin/sh", "/blueprints-hook"]
    arguments.extend(["--", *resolved])
    limit = lock["limits"]["max_output_bytes"]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            arguments,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        timed_out = False
        try:
            exit_code = process.wait(
                timeout=lock["limits"]["command_timeout_seconds"]
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            exit_code = process.wait()
        stdout_size = os.fstat(stdout.fileno()).st_size
        stderr_size = os.fstat(stderr.fileno()).st_size
        stdout.seek(0)
        stderr.seek(0)
        stdout_bytes = stdout.read(limit + 1)
        stderr_bytes = stderr.read(limit + 1)
    output_limited = stdout_size > limit or stderr_size > limit
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "output_limited": output_limited,
        "stdout_sha256": _digest_bytes(stdout_bytes[:limit]),
        "stderr_sha256": _digest_bytes(stderr_bytes[:limit]),
        "command_sha256": _digest_json(argv),
    }


class Simulator:
    """Run every required plan gate without exposing or applying candidate bytes."""

    def __init__(
        self,
        *,
        registry_root: Path,
        asset_root: Path,
        ledger_path: Path,
        target_repository: Path,
        sealed_store: planner.SealedStore,
        dependency_cache: DependencyCache,
        evidence_store: SimulationEvidenceStore,
        workspace_root: Path,
        dependency_provider: DependencyProvider | None = None,
        formatter_runner: planner.FormatterRunner | None = None,
        hook_runner: planner.HookRunner | None = None,
    ) -> None:
        self.registry_root = registry_root
        self.asset_root = asset_root
        self.ledger_path = ledger_path
        self.target_repository = target_repository.resolve()
        self.sealed_store = sealed_store
        self.dependency_cache = dependency_cache
        self.evidence_store = evidence_store
        self.workspace_root = workspace_root.absolute()
        if self.workspace_root.is_symlink():
            _fail(
                "BPX155_WORKSPACE_ROOT",
                str(self.workspace_root),
                "workspace root is a symlink",
            )
        if self.workspace_root.exists() and not self.workspace_root.is_dir():
            _fail(
                "BPX155_WORKSPACE_ROOT",
                str(self.workspace_root),
                "workspace root is not a directory",
            )
        self.dependency_provider = dependency_provider
        self.formatter_runner = formatter_runner
        self.hook_runner = hook_runner

    def _selected_standards(
        self, plan: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        try:
            registry = standards.check_registry(
                self.registry_root, asset_root=self.asset_root
            )
            ledger = standards.validate_allocation_ledger(
                self.ledger_path,
                registry_root=self.registry_root,
                asset_root=self.asset_root,
                registry=registry,
            )
        except standards.StandardDiagnostic as exc:
            _fail("BPX125_AUTHORITY", exc.location, exc.message)
        selected_ids = [
            plan["standards"]["primary"]["standard_id"],
            *[
                row["standard_id"]
                for row in plan["standards"]["components"]
            ],
        ]
        by_id: dict[str, dict[str, Any]] = {}
        for entry in registry["standards"]:
            if entry["standard_id"] not in selected_ids:
                continue
            compiled, _ = standards.compile_file(
                self.registry_root / entry["source_path"],
                registry_root=self.registry_root,
                asset_root=self.asset_root,
            )
            if (
                "blueprints-standard:sha256:" + _digest_json(compiled)
                != entry["standard_id"]
            ):
                _fail(
                    "BPX125_AUTHORITY",
                    entry["source_path"],
                    "compiled standard identity drift",
                )
            by_id[entry["standard_id"]] = compiled
        if set(by_id) != set(selected_ids):
            _fail(
                "BPX126_STANDARD_MISSING",
                "/plan/standards",
                "selected standard is not admitted at its bound identity",
            )
        authority_sha256 = _digest_json(
            {
                "registry_id": registry["registry_id"],
                "ledger_id": ledger["ledger_id"],
                "standard_ids": [
                    row["standard_id"] for row in registry["standards"]
                ],
            }
        )
        return [by_id[identity] for identity in selected_ids], authority_sha256

    def _authority_sha256(self) -> str:
        return authority_state_sha256(
            self.registry_root, self.asset_root, self.ledger_path
        )

    @staticmethod
    def _private_gate(
        ordinal: int,
        stage_id: str,
        status: str,
        reason_code: str,
        command: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command = {} if command is None else command
        row = {
            "ordinal": ordinal,
            "stage_id": stage_id,
            "status": status,
            "reason_code": reason_code,
            "command_sha256": command.get("command_sha256"),
            "exit_code": command.get("exit_code"),
            "timed_out": command.get("timed_out", False),
            "stdout_sha256": command.get("stdout_sha256", EMPTY_SHA256),
            "stderr_sha256": command.get("stderr_sha256", EMPTY_SHA256),
        }
        row["evidence_sha256"] = _digest_json(row)
        return row

    def _locked_dependencies(
        self,
        lock: dict[str, Any],
        ids: list[str],
    ) -> dict[str, Path]:
        definitions = {row["id"]: row for row in lock["dependencies"]}
        paths = {}
        for dependency_id in ids:
            definition = definitions.get(dependency_id)
            if definition is None:
                _fail(
                    "BPX105_UNKNOWN_DEPENDENCY",
                    dependency_id,
                    "runner executable is not environment-locked",
                )
            paths[dependency_id] = self.dependency_cache.ensure(
                definition, self.dependency_provider
            )
        return paths

    def _run_locked(
        self,
        lock: dict[str, Any],
        worktree: Path,
        argv: list[str],
        dependency_ids: list[str],
        *,
        hook: Path | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        if compile_environment_lock(lock) != lock:
            _fail(
                "BPX156_ENVIRONMENT_RACE",
                "/environment-lock",
                "approved environment changed before command execution",
            )
        dependencies = self._locked_dependencies(lock, dependency_ids)
        result = _command_result(
            lock, worktree, argv, dependencies, hook=hook
        )
        if result["timed_out"]:
            return "failed", "BPX127_COMMAND_TIMEOUT", result
        if result["output_limited"]:
            return "failed", "BPX114_OUTPUT_LIMIT", result
        if result["exit_code"] != 0:
            return "failed", "BPX128_COMMAND_FAILED", result
        return "passed", "BPX000_PASSED", result

    def execute(
        self,
        planning_result: dict[str, Any],
        *,
        intake: dict[str, Any],
        target_manifest: dict[str, Any],
        planning_evidence: dict[str, Any],
        environment_lock: dict[str, Any],
        choices: dict[str, Any] | None = None,
        edit_generation: int = 0,
    ) -> dict[str, Any]:
        """Simulate one exact ready candidate and return no candidate bytes."""

        request = planning_result.get("request")
        plan = planning_result.get("plan")
        candidate = planning_result.get("candidate")
        if not all(isinstance(value, dict) for value in (request, plan, candidate)):
            _fail(
                "BPX129_NOT_READY",
                "/",
                "simulation requires a ready request, plan, and candidate",
            )
        planner._validate_schema(request, planner.REQUEST_SCHEMA, "request")
        planner._validate_schema(plan, planner.PLAN_SCHEMA, "plan")
        planner._validate_candidate(candidate)
        planner._validate_target_manifest(target_manifest)
        planner._validate_schema(
            planning_evidence, planner.EVIDENCE_SCHEMA, "planning-evidence"
        )
        if plan["status"] != "ready":
            _fail("BPX129_NOT_READY", "/plan/status", "plan is not ready")
        if (
            request["request_id"] != plan["request_id"]
            or plan["plan_id"] != candidate["plan_id"]
            or plan["target_state_id"] != candidate["target_state_id"]
            or candidate["target_state_id"] != target_manifest["target_state_id"]
            or planning_result.get("planning_evidence_sha256")
            != _digest_json(planning_evidence)
        ):
            _fail(
                "BPX130_BINDING",
                "/",
                "planning artifacts do not share one exact causal binding",
            )
        lock = compile_environment_lock(environment_lock)
        lock_sha256 = _digest_json(lock)
        operations = _sealed_operations(candidate, plan, self.sealed_store)
        selected, authority_sha256 = self._selected_standards(plan)
        expected_stages = list(UNIVERSAL_STAGES) + sorted(
            f"{compiled['standard_key']}--{gate['id']}"
            for compiled in selected
            for gate in compiled["validation"]["gates"]
            if gate["required"]
        )
        actual_stages = [row["stage_id"] for row in plan["validation_stages"]]
        if (
            actual_stages != expected_stages
            or [row["ordinal"] for row in plan["validation_stages"]]
            != list(range(len(actual_stages)))
            or any(not row["required"] for row in plan["validation_stages"])
        ):
            _fail(
                "BPX131_GATE_PLAN",
                "/plan/validation_stages",
                "required gate list is not exact and contiguous",
            )

        private_gates: list[dict[str, Any]] = []
        failed = False
        primary_workspace: Path | None = None
        baseline: dict[str, Any] | None = None
        result_manifest: dict[str, Any] | None = None
        dependency_commands = {
            row["stage_id"]: row for row in lock["commands"]
        }
        standard_gates = {
            f"{compiled['standard_key']}--{gate['id']}": (compiled, gate)
            for compiled in selected
            for gate in compiled["validation"]["gates"]
            if gate["required"]
        }

        self.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        source_before = planner.capture_target_state(
            self.target_repository, target_manifest["repository_id"]
        )
        if source_before != target_manifest:
            _fail(
                "BPX120_TARGET_RACE",
                str(self.target_repository),
                "target changed before simulation",
            )
        temporary = tempfile.TemporaryDirectory(
            prefix="blueprints-simulation.", dir=self.workspace_root
        )
        try:
            root = Path(temporary.name)
            for stage in plan["validation_stages"]:
                ordinal = stage["ordinal"]
                stage_id = stage["stage_id"]
                if failed:
                    private_gates.append(
                        self._private_gate(
                            ordinal,
                            stage_id,
                            "unavailable",
                            "BPX132_PREDECESSOR_FAILED",
                        )
                    )
                    continue
                status = "passed"
                reason = "BPX000_PASSED"
                command_result: dict[str, Any] | None = None
                try:
                    if stage_id == "schema-standard-validity":
                        if candidate["standard_ids"] != [
                            plan["standards"]["primary"]["standard_id"],
                            *[
                                row["standard_id"]
                                for row in plan["standards"]["components"]
                            ],
                        ]:
                            _fail(
                                "BPX133_STANDARD_BINDING",
                                "/candidate/standard_ids",
                                "candidate standard binding differs from plan",
                            )
                    elif stage_id == "composition-parameter-validity":
                        if (
                            plan["selection"]["developer_choice_required"]
                            or plan["atlas"]["relevant_drift"] != "none"
                            or any(
                                row["status"] != "pass"
                                for row in plan["atlas"]["invariant_results"]
                            )
                        ):
                            _fail(
                                "BPX134_COMPOSITION",
                                "/plan",
                                "composition or effective parameter is not accepted",
                            )
                    elif stage_id == "authorized-path-allocation-collision":
                        authorized = plan["authorized_paths"]
                        if any(
                            not any(
                                row["path"].startswith(prefix)
                                for prefix in authorized
                            )
                            for row in plan["operations"]
                        ):
                            _fail(
                                "BPX135_PATH_AUTHORITY",
                                "/plan/operations",
                                "operation lies outside authorized paths",
                            )
                        occupied = {
                            value
                            for row in planning_evidence["allocation"]
                            for value in row["occupied_values"]
                        }
                        if any(
                            row["class"] == "allocated"
                            and row["value"] in occupied
                            for row in plan["effective_parameters"]
                        ):
                            _fail(
                                "BPX136_ALLOCATION_COLLISION",
                                "/plan/effective_parameters",
                                "provisional allocation is already occupied",
                            )
                    elif stage_id == "deterministic-regeneration":
                        regenerated = planner.Planner(
                            registry_root=self.registry_root,
                            asset_root=self.asset_root,
                            ledger_path=self.ledger_path,
                            target_repository=self.target_repository,
                            sealed_store=self.sealed_store,
                            formatter_runner=self.formatter_runner,
                            hook_runner=self.hook_runner,
                        ).execute(
                            intake,
                            target_manifest,
                            planning_evidence,
                            choices=choices,
                            edit_generation=edit_generation,
                        )
                        if regenerated != planning_result:
                            _fail(
                                "BPX137_REGENERATION",
                                "/",
                                "planning and sealed synthesis are not byte-identical",
                            )
                    elif stage_id == "target-state-consistency":
                        primary_workspace = root / "primary"
                        baseline = _reconstruct_target(
                            self.target_repository,
                            primary_workspace,
                            target_manifest,
                        )
                        _apply_candidate(primary_workspace, operations)
                        result_manifest = planner.capture_target_state(
                            primary_workspace, target_manifest["repository_id"]
                        )
                    elif stage_id == "pinned-formatter-stability":
                        if result_manifest is None:
                            _fail(
                                "BPX138_WORKSPACE_UNAVAILABLE",
                                stage_id,
                                "candidate workspace is unavailable",
                            )
                        for row in plan["operations"]:
                            if row["operation"] == "delete":
                                continue
                            after = next(
                                item
                                for item in result_manifest["entries"]
                                if item["path"] == row["path"]
                            )
                            if after["worktree_sha256"] != row["content_sha256"]:
                                _fail(
                                    "BPX139_FORMATTER_DRIFT",
                                    row["path"],
                                    "isolated candidate bytes differ from "
                                    "sealed output",
                                )
                    elif stage_id in COMMAND_STAGES:
                        if primary_workspace is None:
                            _fail(
                                "BPX138_WORKSPACE_UNAVAILABLE",
                                stage_id,
                                "candidate workspace is unavailable",
                            )
                        command = dependency_commands[stage_id]
                        status, reason, command_result = self._run_locked(
                            lock,
                            primary_workspace,
                            command["argv"],
                            command["dependency_ids"],
                        )
                    elif stage_id == "generated-invariant-tests":
                        if (
                            _digest_json(plan["operations"])
                            != candidate["content_manifest_sha256"]
                            or [row["ordinal"] for row in plan["operations"]]
                            != list(range(len(plan["operations"])))
                        ):
                            _fail(
                                "BPX140_GENERATED_INVARIANT",
                                "/plan/operations",
                                "operation manifest identity or ordinals drifted",
                            )
                    elif stage_id == "generated-collision-tests":
                        if baseline is None:
                            _fail(
                                "BPX138_WORKSPACE_UNAVAILABLE",
                                stage_id,
                                "baseline is unavailable",
                            )
                        existing = {row["path"] for row in baseline["entries"]
                                    if row["kind"] != "deleted"}
                        for row in operations:
                            if (
                                row["operation"] == "create"
                                and row["path"] in existing
                            ) or (
                                row["operation"] in {"update", "delete"}
                                and row["path"] not in existing
                            ):
                                _fail(
                                    "BPX141_GENERATED_COLLISION",
                                    row["path"],
                                    "operation does not match baseline existence",
                                )
                    elif stage_id == "generated-determinism-tests":
                        second = root / "determinism"
                        _reconstruct_target(
                            self.target_repository, second, target_manifest
                        )
                        _apply_candidate(second, operations)
                        second_manifest = planner.capture_target_state(
                            second, target_manifest["repository_id"]
                        )
                        if (
                            result_manifest is None
                            or second_manifest["manifest_sha256"]
                            != result_manifest["manifest_sha256"]
                        ):
                            _fail(
                                "BPX142_GENERATED_NONDETERMINISM",
                                stage_id,
                                "independent isolated results differ",
                            )
                    elif stage_id == "generated-placement-tests":
                        if baseline is None or result_manifest is None:
                            _fail(
                                "BPX138_WORKSPACE_UNAVAILABLE",
                                stage_id,
                                "candidate manifests are unavailable",
                            )
                        if _changed_paths(baseline, result_manifest) != {
                            row["path"] for row in operations
                        }:
                            _fail(
                                "BPX143_GENERATED_PLACEMENT",
                                "/plan/operations",
                                "candidate changed a path outside its operation set",
                            )
                    else:
                        compiled, gate = standard_gates[stage_id]
                        if primary_workspace is None:
                            _fail(
                                "BPX138_WORKSPACE_UNAVAILABLE",
                                stage_id,
                                "candidate workspace is unavailable",
                            )
                        if gate["runner"] == "test":
                            test = next(
                                row
                                for row in compiled["validation"]["tests"]
                                if row["id"] == gate["runner_id"]
                            )
                            fixtures = {
                                row["id"]: row
                                for row in compiled["validation"]["fixtures"]
                            }
                            for fixture_id in test["fixture_ids"]:
                                fixture = fixtures[fixture_id]
                                if not fixture["disposable"]:
                                    _fail(
                                        "BPX144_LIVE_FIXTURE",
                                        fixture_id,
                                        "simulation fixture is not disposable",
                                    )
                                for fixture_path in fixture["paths"]:
                                    candidate_path = primary_workspace / fixture_path
                                    if (
                                        not candidate_path.exists()
                                        and not candidate_path.is_symlink()
                                    ):
                                        _fail(
                                            "BPX145_FIXTURE_MISSING",
                                            fixture_path,
                                            "required disposable fixture is missing",
                                        )
                            status, reason, command_result = self._run_locked(
                                lock,
                                primary_workspace,
                                test["command"],
                                [test["command"][0]],
                            )
                        else:
                            hook = next(
                                row
                                for row in compiled["hooks"]
                                if row["id"] == gate["runner_id"]
                            )
                            hook_path = self.asset_root / hook["path"]
                            if (
                                _digest_bytes(
                                    _read_regular(
                                        hook_path, "BPX146_HOOK_DIGEST"
                                    )
                                )
                                != hook["sha256"]
                            ):
                                _fail(
                                    "BPX146_HOOK_DIGEST",
                                    hook["path"],
                                    "trusted hook bytes drifted",
                                )
                            status, reason, command_result = self._run_locked(
                                lock,
                                primary_workspace,
                                [hook["id"]],
                                [],
                                hook=hook_path,
                            )
                except (SimulationDiagnostic, planner.PlannerDiagnostic) as exc:
                    status = (
                        "unavailable"
                        if getattr(exc, "code", "").startswith(
                            ("BPX105_", "BPX111_", "BPX138_")
                        )
                        else "failed"
                    )
                    reason = (
                        exc.code
                        if isinstance(exc, SimulationDiagnostic)
                        else "BPX147_PLANNER_REJECTED"
                    )
                private_gates.append(
                    self._private_gate(
                        ordinal,
                        stage_id,
                        status,
                        reason,
                        command_result,
                    )
                )
                if status != "passed":
                    failed = True
        finally:
            temporary.cleanup()

        source_after = planner.capture_target_state(
            self.target_repository, target_manifest["repository_id"]
        )
        if source_after != target_manifest:
            _fail(
                "BPX148_SOURCE_MUTATED",
                str(self.target_repository),
                "simulation observed a target mutation",
            )
        if self._authority_sha256() != authority_sha256:
            _fail(
                "BPX157_AUTHORITY_RACE",
                str(self.registry_root),
                "standard registry or allocation ledger changed during simulation",
            )
        if compile_environment_lock(lock) != lock:
            _fail(
                "BPX156_ENVIRONMENT_RACE",
                "/environment-lock",
                "approved environment changed during simulation",
            )
        evidence = {
            "schema_version": 1,
            "format": "susy-blueprints-simulation-evidence-v1",
            "contract_id": ENGINE_CONTRACT_ID,
            "candidate_id": candidate["candidate_id"],
            "plan_id": plan["plan_id"],
            "target_state_id": target_manifest["target_state_id"],
            "environment_lock_sha256": lock_sha256,
            "authority_state_sha256": authority_sha256,
            "baseline_manifest_sha256": (
                EMPTY_SHA256 if baseline is None else baseline["manifest_sha256"]
            ),
            "result_manifest_sha256": (
                EMPTY_SHA256
                if result_manifest is None
                else result_manifest["manifest_sha256"]
            ),
            "gates": private_gates,
            "disposable_worktrees_removed": True,
        }
        evidence_locator = self.evidence_store.put(evidence)
        public_gates = [
            {
                "ordinal": row["ordinal"],
                "stage_id": row["stage_id"],
                "status": row["status"],
                "evidence_sha256": row["evidence_sha256"],
            }
            for row in private_gates
        ]
        simulation = {
            "candidate_id": candidate["candidate_id"],
            "status": (
                "passed"
                if all(row["status"] == "passed" for row in public_gates)
                else "failed"
            ),
            "environment_lock_sha256": lock_sha256,
            "gates": public_gates,
        }
        simulation["simulation_id"] = (
            "blueprints-simulation:sha256:" + _digest_json(simulation)
        )
        simulation_schema = _load_schema(planner.RUN_SCHEMA)["properties"][
            "simulation"
        ]["oneOf"][1]
        errors = list(Draft202012Validator(simulation_schema).iter_errors(simulation))
        if errors:
            _fail(
                "BPX101_SCHEMA",
                "simulation",
                sorted(errors, key=lambda error: error.message)[0].message,
            )
        return {
            "simulation": simulation,
            "evidence_locator": evidence_locator,
        }
