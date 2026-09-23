#!/usr/bin/env python3

"""Deterministic Blueprints planning and sealed candidate synthesis."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from typing import Any, Callable, Iterable, NoReturn

from jsonschema import Draft202012Validator

from workbench_blueprints import standards
from workbench_blueprints.layout import SCHEMA_ROOT, WORKBENCH_ROOT


REPO_ROOT = WORKBENCH_ROOT
REQUEST_SCHEMA = SCHEMA_ROOT / "blueprints-request-v1.schema.json"
PLAN_SCHEMA = SCHEMA_ROOT / "blueprints-plan-v1.schema.json"
RUN_SCHEMA = SCHEMA_ROOT / "blueprints-run-v1.schema.json"
TARGET_SCHEMA = SCHEMA_ROOT / "blueprints-target-manifest-v1.schema.json"
EVIDENCE_SCHEMA = (
    SCHEMA_ROOT / "blueprints-planning-evidence-v1.schema.json"
)
SEALED_SCHEMA = SCHEMA_ROOT / "blueprints-sealed-manifest-v1.schema.json"
ENGINE_CONTRACT_ID = "BLUEPRINTS-EXECUTABLE-ENGINE-V1"
PROTECTED_PREFIXES = (".git/", ".workbench/blueprints/")
TEMPLATE_PARAMETER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")

FormatterRunner = Callable[
    [dict[str, Any], dict[str, bytes]], dict[str, bytes]
]
HookRunner = Callable[
    [dict[str, Any], dict[str, Any], str], bytes
]


class PlannerDiagnostic(Exception):
    """A stable fail-closed planner diagnostic."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.location = location
        self.message = message

    def __str__(self) -> str:
        return f"{self.code} {self.location}: {self.message}"


class BlueprintsGitBindingError(RuntimeError):
    """The user-selected Git executable cannot be used by Blueprints."""


def configured_git_executable(executable: str | None = None) -> str:
    """Resolve Workbench setup's Git selection for standalone Blueprint CLIs."""

    selected = executable or os.environ.get("WORKBENCH_GIT_EXECUTABLE")
    if selected:
        path = Path(selected).expanduser()
        if executable is None and not path.is_absolute():
            raise BlueprintsGitBindingError(
                "WORKBENCH_GIT_EXECUTABLE must be an absolute path"
            )
        if path.is_absolute():
            if not path.is_file():
                raise BlueprintsGitBindingError(
                    f"configured Git executable is unavailable: {path}"
                )
            return str(path)
        discovered = shutil.which(selected, path=os.environ.get("PATH", ""))
        if discovered is None:
            raise BlueprintsGitBindingError(
                f"explicit Git executable is unavailable: {selected}"
            )
        return discovered
    discovered = shutil.which("git", path=os.environ.get("PATH", ""))
    if discovered is None:
        raise BlueprintsGitBindingError("Git executable is unavailable")
    return discovered


def _fail(code: str, location: str, message: str) -> NoReturn:
    raise PlannerDiagnostic(code, location, message)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(standards.canonical_json(value).encode("utf-8"))


def _read_regular_nofollow(path: Path, code: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        _fail(code, str(path), str(exc))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail(code, str(path), "path is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _identity(prefix: str, value: dict[str, Any], field: str) -> str:
    projected = copy.deepcopy(value)
    projected.pop(field, None)
    return prefix + _digest_json(projected)


def _schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("BPP100_SCHEMA_READ", str(path), str(exc))
    if not isinstance(value, dict):
        _fail("BPP100_SCHEMA_READ", str(path), "schema root is not an object")
    return value


def _pointer(parts: Iterable[Any]) -> str:
    encoded = [
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    ]
    return "/" + "/".join(encoded) if encoded else "/"


def _validate_schema(value: dict[str, Any], path: Path, source: str) -> None:
    errors = sorted(
        Draft202012Validator(_schema(path)).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        _fail(
            "BPP101_SCHEMA",
            f"{source}#{_pointer(error.absolute_path)}",
            error.message,
        )
def _validate_candidate(candidate: dict[str, Any]) -> None:
    schema = _schema(RUN_SCHEMA)["properties"]["candidate"]["oneOf"][1]
    errors = sorted(
        Draft202012Validator(schema).iter_errors(candidate),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        _fail(
            "BPP101_SCHEMA",
            f"candidate#{_pointer(error.absolute_path)}",
            error.message,
        )
    expected = _identity(
        "blueprints-candidate:sha256:", candidate, "candidate_id"
    )
    if candidate["candidate_id"] != expected:
        _fail(
            "BPP106_CANDIDATE_ID",
            "/candidate_id",
            f"expected {expected}",
        )


def _normalize_json(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not (float("-inf") < value < float("inf")):
            _fail("BPP102_NONFINITE", "/", "numbers must be finite")
        return int(value) if value.is_integer() else value
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("BPP103_JSON_KEY", "/", "object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                _fail(
                    "BPP104_NORMALIZED_COLLISION",
                    "/",
                    f"duplicate normalized key {normalized_key!r}",
                )
            normalized[normalized_key] = _normalize_json(item)
        return normalized
    _fail(
        "BPP105_NON_JSON_VALUE",
        "/",
        f"value has forbidden type {type(value).__name__}",
    )


def _git(
    repo: Path,
    *arguments: str,
    text: bool = False,
    executable: str | None = None,
) -> bytes | str:
    try:
        git = configured_git_executable(executable)
    except BlueprintsGitBindingError as exc:
        _fail("BPP110_GIT", str(repo), str(exc))
    try:
        result = subprocess.run(
            [git, "-C", str(repo), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (
            exc.stderr.decode("utf-8", errors="replace").strip()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        _fail("BPP110_GIT", str(repo), detail)
    if text:
        try:
            return result.stdout.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            _fail("BPP111_GIT_ENCODING", str(repo), str(exc))
    return result.stdout


def _nul_records(value: bytes) -> list[bytes]:
    if not value:
        return []
    records = value.split(b"\0")
    if records[-1] == b"":
        records.pop()
    return records


def _decode_git_path(
    value: bytes, location: str, *, allow_protected: bool = False
) -> str:
    try:
        decoded = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("BPP112_PATH_ENCODING", location, str(exc))
    path = unicodedata.normalize("NFC", decoded)
    if path != decoded:
        _fail(
            "BPP129_TARGET_PATH_COLLISION",
            location,
            "Git paths must already be Unicode NFC",
        )
    protected_candidate = path if path.endswith("/") else path + "/"
    if allow_protected and any(
        protected_candidate.startswith(prefix)
        for prefix in PROTECTED_PREFIXES
    ):
        return path
    _safe_path(
        path,
        directory=False,
        location=location,
        allow_protected=allow_protected,
    )
    return path


def _safe_path(
    value: str,
    *,
    directory: bool,
    location: str,
    allow_protected: bool = False,
) -> None:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or value.endswith("/") != directory
    ):
        _fail("BPP113_UNSAFE_PATH", location, f"invalid path {value!r}")
    stripped = value[:-1] if directory else value
    parts = stripped.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail("BPP113_UNSAFE_PATH", location, f"invalid path {value!r}")
    normalized = str(PurePosixPath(stripped)) + ("/" if directory else "")
    if normalized != value:
        _fail("BPP113_UNSAFE_PATH", location, f"non-normal path {value!r}")
    candidate = value if directory else value + "/"
    if (
        not allow_protected
        and any(candidate.startswith(prefix) for prefix in PROTECTED_PREFIXES)
    ):
        _fail("BPP114_PROTECTED_PATH", location, f"protected path {value!r}")


def _blob_sha256(
    repo: Path,
    object_id: str,
    object_type: str,
    *,
    git_executable: str | None = None,
) -> str:
    if object_type == "blob":
        return _digest_bytes(
            _git(
                repo,
                "cat-file",
                "blob",
                object_id,
                executable=git_executable,
            )
        )
    return _digest_bytes(object_id.encode("ascii"))


def _tree_records(
    repo: Path,
    revision: str,
    *,
    git_executable: str | None = None,
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for raw in _nul_records(
        _git(
            repo,
            "ls-tree",
            "-r",
            "-z",
            revision,
            executable=git_executable,
        )
    ):
        metadata, separator, raw_path = raw.partition(b"\t")
        if separator != b"\t":
            _fail("BPP115_GIT_RECORD", revision, "invalid ls-tree record")
        try:
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
        except (UnicodeDecodeError, ValueError) as exc:
            _fail("BPP115_GIT_RECORD", revision, str(exc))
        path = _decode_git_path(raw_path, revision)
        if path in records:
            _fail(
                "BPP129_TARGET_PATH_COLLISION",
                path,
                "Git paths collide after Unicode normalization",
            )
        records[path] = {
            "mode": mode.zfill(6),
            "type": object_type,
            "oid": object_id,
            "raw": raw_path.hex(),
        }
    return records


def _index_records(
    repo: Path,
    *,
    git_executable: str | None = None,
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    for raw in _nul_records(
        _git(
            repo,
            "ls-files",
            "-s",
            "-z",
            executable=git_executable,
        )
    ):
        metadata, separator, raw_path = raw.partition(b"\t")
        if separator != b"\t":
            _fail("BPP115_GIT_RECORD", str(repo), "invalid index record")
        try:
            mode, object_id, stage = metadata.decode("ascii").split(" ")
        except (UnicodeDecodeError, ValueError) as exc:
            _fail("BPP115_GIT_RECORD", str(repo), str(exc))
        if stage != "0":
            _fail(
                "BPP116_UNMERGED_INDEX",
                str(repo),
                "target index contains unmerged entries",
            )
        path = _decode_git_path(raw_path, str(repo))
        if path in records:
            _fail(
                "BPP129_TARGET_PATH_COLLISION",
                path,
                "index paths collide after Unicode normalization",
            )
        records[path] = {
            "mode": mode.zfill(6),
            "type": "commit" if mode == "160000" else "blob",
            "oid": object_id,
            "raw": raw_path.hex(),
        }
    return records


def _worktree_record(repo: Path, path: str) -> tuple[str, str, str | None]:
    candidate = repo / path
    try:
        status = candidate.lstat()
    except FileNotFoundError:
        return "deleted", "000000", None
    if stat.S_ISLNK(status.st_mode):
        content = os.readlink(os.fsencode(candidate))
        return "symlink", "120000", _digest_bytes(content)
    if stat.S_ISREG(status.st_mode):
        try:
            descriptor = os.open(
                candidate,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            _fail("BPP117_TARGET_KIND", path, str(exc))
        try:
            opened_status = os.fstat(descriptor)
            if not stat.S_ISREG(opened_status.st_mode):
                _fail(
                    "BPP117_TARGET_KIND",
                    path,
                    "target changed kind during capture",
                )
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read()
        finally:
            os.close(descriptor)
        mode = (
            "100755" if opened_status.st_mode & stat.S_IXUSR else "100644"
        )
        return "file", mode, _digest_bytes(content)
    _fail("BPP117_TARGET_KIND", path, "target entry is not a file or symlink")


def _target_projection(
    repository_id: str,
    revision: str,
    dirty: bool,
    manifest_sha256: str,
) -> dict[str, Any]:
    target = {
        "repository_id": repository_id,
        "revision": revision,
        "worktree": {
            "dirty": dirty,
            "manifest_sha256": manifest_sha256,
        },
    }
    target["target_state_id"] = (
        "blueprints-target-state:sha256:" + _digest_json(target)
    )
    return target


def capture_target_state(
    repository: Path,
    repository_id: str,
    *,
    git_executable: str | None = None,
) -> dict[str, Any]:
    """Capture HEAD, index, worktree, and permitted untracked bytes exactly."""

    root = repository.resolve()
    top = Path(
        str(
            _git(
                root,
                "rev-parse",
                "--show-toplevel",
                text=True,
                executable=git_executable,
            )
        )
    ).resolve()
    if top != root:
        _fail(
            "BPP118_REPOSITORY_ROOT",
            str(repository),
            f"expected Git root {root}, found {top}",
        )
    revision = str(
        _git(
            root,
            "rev-parse",
            "HEAD",
            text=True,
            executable=git_executable,
        )
    )
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        _fail("BPP119_REVISION", str(root), f"invalid HEAD {revision!r}")

    head = _tree_records(root, revision, git_executable=git_executable)
    index = _index_records(root, git_executable=git_executable)
    untracked_raw: dict[str, str] = {}
    for raw in _nul_records(
        _git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            executable=git_executable,
        )
    ):
        path = _decode_git_path(raw, str(root), allow_protected=True)
        if any(
            (path + "/").startswith(prefix) for prefix in PROTECTED_PREFIXES
        ):
            continue
        if path in untracked_raw:
            _fail(
                "BPP129_TARGET_PATH_COLLISION",
                path,
                "untracked paths collide after Unicode normalization",
            )
        untracked_raw[path] = raw.hex()
    untracked = set(untracked_raw)
    for path in set(head) & set(index):
        if head[path]["raw"] != index[path]["raw"]:
            _fail(
                "BPP129_TARGET_PATH_COLLISION",
                path,
                "HEAD and index paths differ before Unicode normalization",
            )
    for path in (set(head) | set(index)) & untracked:
        tracked_raw = (index.get(path) or head[path])["raw"]
        if tracked_raw != untracked_raw[path]:
            _fail(
                "BPP129_TARGET_PATH_COLLISION",
                path,
                "tracked and untracked paths differ before Unicode normalization",
            )

    entries: list[dict[str, Any]] = []
    for path in sorted(set(head) | set(index) | untracked):
        head_row = head.get(path)
        index_row = index.get(path)
        if (index_row or head_row or {}).get("mode") == "160000":
            _fail(
                "BPP117_TARGET_KIND",
                path,
                "Git submodules require recursive target capture",
            )
        kind, worktree_mode, worktree_sha256 = _worktree_record(root, path)
        head_sha256 = (
            _blob_sha256(
                root,
                head_row["oid"],
                head_row["type"],
                git_executable=git_executable,
            )
            if head_row is not None
            else None
        )
        index_sha256 = (
            _blob_sha256(
                root,
                index_row["oid"],
                index_row["type"],
                git_executable=git_executable,
            )
            if index_row is not None
            else None
        )
        layers: list[str] = []
        if head_row is not None or index_row is not None:
            layers.append("tracked")
        if (head_sha256, (head_row or {}).get("mode")) != (
            index_sha256,
            (index_row or {}).get("mode"),
        ):
            layers.append("staged")
        if index_row is not None and (
            index_sha256,
            index_row["mode"],
        ) != (worktree_sha256, worktree_mode):
            layers.append("unstaged")
        if path in untracked:
            layers.append("untracked")
        mode = (
            worktree_mode
            if kind != "deleted"
            else (index_row or head_row or {"mode": "000000"})["mode"]
        )
        entries.append(
            {
                "path": path,
                "kind": kind,
                "mode": mode,
                "head_mode": (
                    None if head_row is None else head_row["mode"]
                ),
                "index_mode": (
                    None if index_row is None else index_row["mode"]
                ),
                "head_sha256": head_sha256,
                "index_sha256": index_sha256,
                "worktree_sha256": worktree_sha256,
                "layers": layers or ["tracked"],
            }
        )
    manifest_sha256 = _digest_json(entries)
    dirty = any(
        any(layer in {"staged", "unstaged", "untracked"} for layer in row["layers"])
        for row in entries
    )
    target = _target_projection(
        repository_id, revision, dirty, manifest_sha256
    )
    manifest = {
        "schema_version": 1,
        "format": "susy-blueprints-target-manifest-v1",
        "contract_id": ENGINE_CONTRACT_ID,
        "target_state_id": target["target_state_id"],
        "repository_id": repository_id,
        "revision": revision,
        "dirty": dirty,
        "manifest_sha256": manifest_sha256,
        "entries": entries,
    }
    _validate_schema(manifest, TARGET_SCHEMA, "target-manifest")
    return manifest


def target_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    _validate_target_manifest(manifest)
    return {
        "target_state_id": manifest["target_state_id"],
        "repository_id": manifest["repository_id"],
        "revision": manifest["revision"],
        "worktree": {
            "dirty": manifest["dirty"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
    }


def _validate_target_manifest(manifest: dict[str, Any]) -> None:
    _validate_schema(manifest, TARGET_SCHEMA, "target-manifest")
    layer_order = ["tracked", "staged", "unstaged", "untracked"]
    for index, row in enumerate(manifest["entries"]):
        _safe_path(
            row["path"],
            directory=False,
            location=f"/entries/{index}/path",
        )
        if row["layers"] != [
            layer for layer in layer_order if layer in row["layers"]
        ]:
            _fail(
                "BPP122_TARGET_ORDER",
                f"/entries/{index}/layers",
                "target layers are not in canonical order",
            )
        if (row["kind"] == "deleted") != (
            row["worktree_sha256"] is None
        ):
            _fail(
                "BPP127_TARGET_ENTRY",
                f"/entries/{index}",
                "only deleted entries have null worktree content",
            )
        if (row["head_sha256"] is None) != (row["head_mode"] is None):
            _fail(
                "BPP127_TARGET_ENTRY",
                f"/entries/{index}",
                "HEAD content and mode must be present together",
            )
        if (row["index_sha256"] is None) != (row["index_mode"] is None):
            _fail(
                "BPP127_TARGET_ENTRY",
                f"/entries/{index}",
                "index content and mode must be present together",
            )
        if (
            "untracked" in row["layers"]
            and "tracked" not in row["layers"]
            and (
                row["head_sha256"] is not None
                or row["index_sha256"] is not None
            )
        ):
            _fail(
                "BPP127_TARGET_ENTRY",
                f"/entries/{index}",
                "untracked entries cannot have HEAD or index content",
            )
        if "tracked" in row["layers"] and (
            row["head_sha256"] is None
            and row["index_sha256"] is None
        ):
            _fail(
                "BPP127_TARGET_ENTRY",
                f"/entries/{index}",
                "tracked entries require HEAD or index content",
            )
    expected_manifest = _digest_json(manifest["entries"])
    if manifest["manifest_sha256"] != expected_manifest:
        _fail(
            "BPP120_TARGET_MANIFEST_ID",
            "/manifest_sha256",
            f"expected {expected_manifest}",
        )
    target = _target_projection(
        manifest["repository_id"],
        manifest["revision"],
        manifest["dirty"],
        manifest["manifest_sha256"],
    )
    if manifest["target_state_id"] != target["target_state_id"]:
        _fail(
            "BPP121_TARGET_STATE_ID",
            "/target_state_id",
            f"expected {target['target_state_id']}",
        )
    paths = [row["path"] for row in manifest["entries"]]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        _fail(
            "BPP122_TARGET_ORDER",
            "/entries",
            "target entries must be unique and path-sorted",
        )
    expected_dirty = any(
        any(
            layer in {"staged", "unstaged", "untracked"}
            for layer in row["layers"]
        )
        for row in manifest["entries"]
    )
    if manifest["dirty"] != expected_dirty:
        _fail(
            "BPP128_TARGET_DIRTY",
            "/dirty",
            f"expected {expected_dirty}",
        )


def compile_request(
    intake: dict[str, Any],
    target_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Compile any intake adapter's JSON projection to one canonical request."""

    _validate_target_manifest(target_manifest)
    value = _normalize_json(copy.deepcopy(intake))
    if not isinstance(value, dict):
        _fail("BPP123_INTAKE", "/", "intake must be an object")
    expected = {
        "sequence",
        "feature_family",
        "intent",
        "parameters",
        "requested_variants",
        "output_mode",
        "consent",
    }
    if set(value) != expected:
        _fail(
            "BPP123_INTAKE",
            "/",
            f"intake fields must be exactly {sorted(expected)}",
        )
    parameters = value["parameters"]
    if not isinstance(parameters, list):
        _fail("BPP123_INTAKE", "/parameters", "parameters must be an array")
    parameters.sort(
        key=lambda row: row.get("name", "") if isinstance(row, dict) else ""
    )
    names = [
        row.get("name")
        for row in parameters
        if isinstance(row, dict)
    ]
    if len(names) != len(parameters) or len(names) != len(set(names)):
        _fail(
            "BPP124_PARAMETER_DUPLICATE",
            "/parameters",
            "parameter names must be unique",
        )
    variants = value["requested_variants"]
    if not isinstance(variants, list):
        _fail(
            "BPP123_INTAKE",
            "/requested_variants",
            "requested_variants must be an array",
        )
    if len(variants) != len(set(variants)):
        _fail(
            "BPP125_VARIANT_DUPLICATE",
            "/requested_variants",
            "requested variants must be unique",
        )
    variants.sort()
    request = {
        "schema_version": 1,
        "format": "susy-blueprints-request-v1",
        "contract_id": ENGINE_CONTRACT_ID,
        **value,
        "target": target_from_manifest(target_manifest),
    }
    if (
        request["output_mode"] == "direct-apply"
        and not request["consent"]["allow_direct_apply"]
    ):
        _fail(
            "BPP126_DIRECT_APPLY_CONSENT",
            "/consent/allow_direct_apply",
            "direct-apply requires explicit consent",
        )
    request["request_id"] = _identity(
        "blueprints-request:sha256:", request, "request_id"
    )
    _validate_schema(request, REQUEST_SCHEMA, "request")
    return request


def _verify_evidence(evidence: dict[str, Any], target_state_id: str) -> None:
    _validate_schema(evidence, EVIDENCE_SCHEMA, "planning-evidence")
    if evidence["target_state_id"] != target_state_id:
        _fail(
            "BPP130_EVIDENCE_TARGET",
            "/target_state_id",
            "planning evidence targets a different state",
        )
    for group in ("queries",):
        rows = evidence["atlas"][group]
        keys = [(row["standard_key"], row["query_id"]) for row in rows]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            _fail(
                "BPP131_EVIDENCE_ORDER",
                f"/atlas/{group}",
                "evidence rows must be unique and key-sorted",
            )
        for index, row in enumerate(rows):
            if (
                row["availability"] == "unavailable"
                and row["result"] is not None
            ):
                _fail(
                    "BPP134_UNAVAILABLE_RESULT",
                    f"/atlas/{group}/{index}/result",
                    "unavailable Atlas evidence must have a null result",
                )
            projected = dict(row)
            claimed = projected.pop("evidence_sha256")
            expected = _digest_json(projected)
            if claimed != expected:
                _fail(
                    "BPP132_EVIDENCE_ID",
                    f"/atlas/{group}/{index}/evidence_sha256",
                    f"expected {expected}",
                )
    for group in ("allocation", "reconciliation"):
        rows = evidence[group]
        key_name = "domain_name" if group == "allocation" else "standard_key"
        keys = [row[key_name] for row in rows]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            _fail(
                "BPP131_EVIDENCE_ORDER",
                f"/{group}",
                "evidence rows must be unique and key-sorted",
            )
        for index, row in enumerate(rows):
            if group == "allocation" and row["occupied_values"] != sorted(
                row["occupied_values"]
            ):
                _fail(
                    "BPP131_EVIDENCE_ORDER",
                    f"/allocation/{index}/occupied_values",
                    "occupied values must be sorted",
                )
            projected = dict(row)
            claimed = projected.pop("evidence_sha256")
            expected = _digest_json(projected)
            if claimed != expected:
                _fail(
                    "BPP132_EVIDENCE_ID",
                    f"/{group}/{index}/evidence_sha256",
                    f"expected {expected}",
                )
    atlas_projection = {
        "target_state_id": evidence["target_state_id"],
        "relevant_drift": evidence["atlas"]["relevant_drift"],
        "queries": evidence["atlas"]["queries"],
    }
    expected_current = "atlas-current:sha256:" + _digest_json(atlas_projection)
    if evidence["atlas"]["current_id"] != expected_current:
        _fail(
            "BPP133_ATLAS_CURRENT_ID",
            "/atlas/current_id",
            f"expected {expected_current}",
        )


def build_planning_evidence(
    target_state_id: str,
    *,
    queries: list[dict[str, Any]],
    allocation: list[dict[str, Any]],
    reconciliation: list[dict[str, Any]],
    relevant_drift: str = "none",
) -> dict[str, Any]:
    """Build a canonical, content-bound planning evidence bundle."""

    normalized_queries = []
    for row in queries:
        item = _normalize_json(copy.deepcopy(row))
        item["evidence_sha256"] = _digest_json(item)
        normalized_queries.append(item)
    normalized_queries.sort(key=lambda row: (row["standard_key"], row["query_id"]))
    normalized_allocation = []
    for row in allocation:
        item = _normalize_json(copy.deepcopy(row))
        item["occupied_values"] = sorted(set(item["occupied_values"]))
        item["evidence_sha256"] = _digest_json(item)
        normalized_allocation.append(item)
    normalized_allocation.sort(key=lambda row: row["domain_name"])
    normalized_reconciliation = []
    for row in reconciliation:
        item = _normalize_json(copy.deepcopy(row))
        item["evidence_sha256"] = _digest_json(item)
        normalized_reconciliation.append(item)
    normalized_reconciliation.sort(key=lambda row: row["standard_key"])
    evidence = {
        "schema_version": 1,
        "format": "susy-blueprints-planning-evidence-v1",
        "contract_id": ENGINE_CONTRACT_ID,
        "target_state_id": target_state_id,
        "atlas": {
            "current_id": "",
            "relevant_drift": relevant_drift,
            "queries": normalized_queries,
        },
        "allocation": normalized_allocation,
        "reconciliation": normalized_reconciliation,
    }
    atlas_projection = {
        "target_state_id": target_state_id,
        "relevant_drift": relevant_drift,
        "queries": normalized_queries,
    }
    evidence["atlas"]["current_id"] = (
        "atlas-current:sha256:" + _digest_json(atlas_projection)
    )
    _verify_evidence(evidence, target_state_id)
    return evidence


def _json_pointer_get(value: Any, pointer: str) -> tuple[bool, Any]:
    if pointer == "":
        return True, value
    if not pointer.startswith("/"):
        return False, None
    current = value
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _evaluate_expression(expression: dict[str, Any], values: dict[str, Any]) -> Any:
    operator = expression["operator"]
    arguments = expression["arguments"]
    if operator == "literal":
        return arguments[0]
    if operator == "parameter":
        name = arguments[0]
        if name not in values:
            _fail("BPP140_PARAMETER_UNAVAILABLE", name, "parameter is unavailable")
        return values[name]
    resolved = [_evaluate_expression(argument, values) for argument in arguments]
    if operator == "concat":
        return unicodedata.normalize(
            "NFC", "".join(_scalar_text(item) for item in resolved)
        )
    if operator == "lowercase":
        return unicodedata.normalize("NFC", _scalar_text(resolved[0]).lower())
    if operator == "uppercase":
        return unicodedata.normalize("NFC", _scalar_text(resolved[0]).upper())
    if operator == "replace":
        return unicodedata.normalize(
            "NFC",
            _scalar_text(resolved[0]).replace(
                _scalar_text(resolved[1]), _scalar_text(resolved[2])
            ),
        )
    if operator == "slugify":
        slug = re.sub(r"[^a-z0-9]+", "_", _scalar_text(resolved[0]).lower())
        return unicodedata.normalize("NFC", slug.strip("_"))
    if operator == "posix-path-join":
        pieces = [_scalar_text(item).strip("/") for item in resolved]
        result = "/".join(piece for piece in pieces if piece)
        _safe_path(result, directory=False, location="/expression")
        return unicodedata.normalize("NFC", result)
    if operator == "json-pointer-get":
        found, result = _json_pointer_get(resolved[0], _scalar_text(resolved[1]))
        if not found:
            _fail(
                "BPP141_JSON_POINTER",
                _scalar_text(resolved[1]),
                "JSON pointer did not resolve",
            )
        return result
    _fail("BPP142_EXPRESSION_OPERATOR", "/", f"unknown operator {operator!r}")


def _scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return standards.canonical_json(value)


def _condition_matches(condition: dict[str, Any], values: dict[str, Any]) -> bool:
    name = condition["parameter"]
    exists = name in values and values[name] is not None
    if condition["operator"] == "exists":
        return exists
    if not exists:
        return False
    actual = values[name]
    expected = condition["value"]
    if condition["operator"] == "equals":
        return actual == expected
    if condition["operator"] == "not-equals":
        return actual != expected
    if condition["operator"] == "in":
        return actual in expected
    if condition["operator"] == "matches":
        return isinstance(actual, str) and re.fullmatch(expected, actual) is not None
    return False


def _constraints_match(value: Any, parameter: dict[str, Any]) -> bool:
    if parameter["class"] == "optional" and value is None:
        return True
    try:
        standards._check_constraints(value, parameter, parameter["name"])
    except standards.StandardDiagnostic:
        return False
    return True


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _record_binding(
    entry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "standard_id": entry["standard_id"],
        "standard_sha256": entry["standard_sha256"],
        "version": entry["version"],
    }


def _atlas_invariant_status(
    invariant: dict[str, Any], result: Any
) -> str:
    found, actual = _json_pointer_get(result, invariant["result_path"])
    operator = invariant["operator"]
    expected = invariant["expected"]
    if operator == "exists":
        passed = found == bool(expected)
    elif not found:
        passed = False
    elif operator == "equals":
        passed = actual == expected
    elif operator == "not-equals":
        passed = actual != expected
    elif operator == "contains":
        try:
            passed = expected in actual
        except TypeError:
            passed = False
    elif operator == "matches":
        try:
            passed = (
                isinstance(actual, str)
                and isinstance(expected, str)
                and re.fullmatch(expected, actual) is not None
            )
        except re.error:
            passed = False
    else:
        passed = False
    return "pass" if passed else "fail"


def _render_template(
    content: bytes, values: dict[str, Any], location: str
) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("BPP150_TEMPLATE_ENCODING", location, str(exc))
    matches = list(TEMPLATE_PARAMETER_RE.finditer(text))
    residue = TEMPLATE_PARAMETER_RE.sub("", text)
    if "{{" in residue or "}}" in residue:
        _fail(
            "BPP151_TEMPLATE_SYNTAX",
            location,
            "template contains an invalid placeholder",
        )
    unknown = sorted(
        {match.group(1) for match in matches} - set(values)
    )
    if unknown:
        _fail(
            "BPP152_TEMPLATE_PARAMETER",
            location,
            f"template references unavailable parameters {unknown}",
        )
    rendered = TEMPLATE_PARAMETER_RE.sub(
        lambda match: _scalar_text(values[match.group(1)]),
        text,
    )
    return rendered.encode("utf-8")


def _operation_manifest(operations: list[dict[str, Any]]) -> str:
    return _digest_json(operations)


def _precedes(
    winner: str,
    loser: str,
    surface: str,
    rules: list[dict[str, Any]],
) -> bool:
    graph: dict[str, set[str]] = {}
    for rule in rules:
        if rule["surface"] == surface:
            graph.setdefault(rule["winner"], set()).add(rule["loser"])
    pending = list(graph.get(winner, set()))
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == loser:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(graph.get(current, set()))
    return False


class SealedStore:
    """Private content-addressed storage that never returns a filesystem locator."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.is_symlink():
            _fail(
                "BPP168_SEALED_ROOT",
                str(self.root),
                "sealed root cannot be a symlink",
            )
        if self.root.exists() and not self.root.is_dir():
            _fail(
                "BPP168_SEALED_ROOT",
                str(self.root),
                "sealed root must be a directory",
            )

    def _path(self, digest: str) -> Path:
        return self.root / "objects" / digest[:2] / f"{digest}.json"

    def put(self, payload: dict[str, Any]) -> str:
        _validate_schema(payload, SEALED_SCHEMA, "sealed-manifest")
        serialized = standards.canonical_json(payload).encode("utf-8")
        digest = _digest_bytes(serialized)
        path = self._path(digest)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        objects = self.root / "objects"
        if objects.is_symlink():
            _fail(
                "BPP168_SEALED_ROOT",
                str(objects),
                "sealed object directory cannot be a symlink",
            )
        objects.mkdir(mode=0o700, exist_ok=True)
        if path.parent.is_symlink():
            _fail(
                "BPP168_SEALED_ROOT",
                str(path.parent),
                "sealed shard directory cannot be a symlink",
            )
        path.parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(objects, 0o700)
        os.chmod(path.parent, 0o700)
        if path.exists():
            if path.is_symlink() or _read_regular_nofollow(
                path, "BPP160_SEALED_COLLISION"
            ) != serialized:
                _fail(
                    "BPP160_SEALED_COLLISION",
                    "local-cas:sha256:" + digest,
                    "sealed object does not match its content identity",
                )
        else:
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=".sealed.",
                    delete=False,
                ) as handle:
                    temporary_name = handle.name
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, path)
                temporary_name = None
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)
        os.chmod(path, 0o600)
        return "local-cas:sha256:" + digest

    def read(self, locator: str) -> dict[str, Any]:
        match = re.fullmatch(r"local-cas:sha256:([0-9a-f]{64})", locator)
        if match is None:
            _fail("BPP161_SEALED_LOCATOR", "/", "invalid sealed locator")
        digest = match.group(1)
        path = self._path(digest)
        if (
            self.root.is_symlink()
            or (self.root / "objects").is_symlink()
            or path.parent.is_symlink()
        ):
            _fail(
                "BPP168_SEALED_ROOT",
                locator,
                "sealed storage contains a symlinked directory",
            )
        if not path.is_file() or path.is_symlink():
            _fail("BPP162_SEALED_MISSING", locator, "sealed object is missing")
        content = _read_regular_nofollow(path, "BPP162_SEALED_MISSING")
        if _digest_bytes(content) != digest:
            _fail("BPP163_SEALED_DIGEST", locator, "sealed object digest drift")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            _fail("BPP164_SEALED_JSON", locator, str(exc))
        _validate_schema(payload, SEALED_SCHEMA, locator)
        if standards.canonical_json(payload).encode("utf-8") != content:
            _fail("BPP165_SEALED_CANONICAL", locator, "object is not canonical")
        operations = []
        for row in payload["operations"]:
            operation = {
                "ordinal": row["ordinal"],
                "operation": row["operation"],
                "path": row["path"],
                "content_sha256": row["content_sha256"],
            }
            if row["operation"] == "delete":
                if row["content_base64"] is not None:
                    _fail("BPP166_SEALED_CONTENT", locator, "delete has content")
            else:
                decoded = base64.b64decode(row["content_base64"], validate=True)
                if _digest_bytes(decoded) != row["content_sha256"]:
                    _fail("BPP166_SEALED_CONTENT", locator, "content digest drift")
            operations.append(operation)
        if _operation_manifest(operations) != payload["content_manifest_sha256"]:
            _fail("BPP167_SEALED_MANIFEST", locator, "operation manifest drift")
        return payload


class Planner:
    """Compile one request and current evidence into a plan and sealed candidate."""

    def __init__(
        self,
        *,
        registry_root: Path,
        asset_root: Path = REPO_ROOT,
        ledger_path: Path,
        target_repository: Path,
        sealed_store: SealedStore,
        formatter_runner: FormatterRunner | None = None,
        hook_runner: HookRunner | None = None,
    ) -> None:
        self.registry_root = registry_root
        self.asset_root = asset_root
        self.ledger_path = ledger_path
        self.target_repository = target_repository.resolve()
        self.sealed_store = sealed_store
        self.formatter_runner = formatter_runner
        self.hook_runner = hook_runner

    def _load_standards(
        self,
    ) -> tuple[
        dict[str, Any],
        list[tuple[dict[str, Any], dict[str, Any]]],
        str,
    ]:
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
            _fail("BPP173_AUTHORITY_INVALID", exc.location, str(exc))
        records = []
        for entry in registry["standards"]:
            compiled, _ = standards.compile_file(
                self.registry_root / entry["source_path"],
                registry_root=self.registry_root,
                asset_root=self.asset_root,
            )
            digest = _digest_json(compiled)
            if (
                digest != entry["standard_sha256"]
                or entry["standard_id"]
                != "blueprints-standard:sha256:" + digest
            ):
                _fail(
                    "BPP170_STANDARD_ID",
                    entry["source_path"],
                    "registry and compiled standard identity disagree",
                )
            records.append((compiled, entry))
        authority_state = _digest_json(
            {
                "registry_id": registry["registry_id"],
                "ledger_id": ledger["ledger_id"],
                "standard_ids": [
                    entry["standard_id"] for _compiled, entry in records
                ],
            }
        )
        return ledger, records, authority_state

    def _require_target_unchanged(
        self, expected: dict[str, Any]
    ) -> None:
        current = capture_target_state(
            self.target_repository, expected["repository_id"]
        )
        if current["target_state_id"] != expected["target_state_id"]:
            _fail(
                "BPP171_STALE_TARGET",
                str(self.target_repository),
                "target changed during planning",
            )

    def execute(
        self,
        intake: dict[str, Any],
        target_manifest: dict[str, Any],
        evidence: dict[str, Any],
        *,
        choices: dict[str, Any] | None = None,
        edit_generation: int = 0,
    ) -> dict[str, Any]:
        choices = _normalize_json(
            copy.deepcopy({} if choices is None else choices)
        )
        if not isinstance(choices, dict):
            _fail("BPP172_CHOICES", "/", "planner choices must be an object")
        if set(choices) - {
            "primary_standard_id",
            "include_components",
            "primary_variant",
            "component_variants",
        }:
            _fail("BPP172_CHOICES", "/", "planner choices contain unknown fields")
        if (
            not isinstance(edit_generation, int)
            or isinstance(edit_generation, bool)
            or edit_generation < 0
        ):
            _fail(
                "BPP175_EDIT_GENERATION",
                "/edit_generation",
                "edit generation must be a non-negative integer",
            )
        for field in ("primary_standard_id", "primary_variant"):
            if field in choices and not isinstance(choices[field], str):
                _fail(
                    "BPP172_CHOICES",
                    f"/{field}",
                    f"{field} must be a string",
                )
        included = choices.get("include_components", [])
        if (
            not isinstance(included, list)
            or any(not isinstance(item, str) for item in included)
            or len(included) != len(set(included))
        ):
            _fail(
                "BPP172_CHOICES",
                "/include_components",
                "included components must be a unique string array",
            )
        component_choices = choices.get("component_variants", {})
        if (
            not isinstance(component_choices, dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in component_choices.items()
            )
        ):
            _fail(
                "BPP172_CHOICES",
                "/component_variants",
                "component variants must map strings to strings",
            )
        request = compile_request(intake, target_manifest)
        _verify_evidence(evidence, request["target"]["target_state_id"])
        self._require_target_unchanged(target_manifest)
        ledger, records, authority_state = self._load_standards()

        candidates = [
            (compiled, entry)
            for compiled, entry in records
            if compiled["kind"] == "primary"
            and compiled["lifecycle"] == "active"
            and compiled["feature_family"] == request["feature_family"]
            and any(
                target["repository_id"] == request["target"]["repository_id"]
                for target in compiled["targets"]
            )
        ]
        if not candidates:
            return {
                "request": request,
                "plan": None,
                "candidate": None,
                "diagnostics": ["BPP200_NO_ADMITTED_STANDARD"],
                "compliant_revisions": [],
                "planning_evidence_sha256": _digest_json(evidence),
            }
        candidates.sort(
            key=lambda row: (
                -row[0]["priority"],
                -row[0]["specificity"],
                row[1]["standard_id"],
            )
        )
        selected = candidates[0]
        top_rank = (selected[0]["priority"], selected[0]["specificity"])
        tied = [
            row
            for row in candidates
            if (row[0]["priority"], row[0]["specificity"]) == top_rank
        ]
        blocked: set[str] = set()
        rationale = {"standard-priority-specificity"}
        explicit_primary = choices.get("primary_standard_id")
        if len(tied) > 1:
            by_id = {
                entry["standard_id"]: (compiled, entry)
                for compiled, entry in tied
            }
            if explicit_primary in by_id:
                selected = by_id[explicit_primary]
                rationale.add("developer-standard-choice")
            else:
                blocked.add("BPP201_STANDARD_SELECTION_TIE")
                rationale.add("developer-choice-required")

        primary, primary_entry = selected
        selected_records = [(primary, primary_entry, "primary")]
        requested_components = set(choices.get("include_components", []))
        known_component_keys = {
            row["standard_key"] for row in primary["composition"]["allowed"]
        }
        if requested_components - known_component_keys:
            blocked.add("BPP202_UNKNOWN_COMPONENT")
        by_identity = {
            (compiled["standard_key"], compiled["version"]): (compiled, entry)
            for compiled, entry in records
        }
        component_rules: dict[str, dict[str, Any]] = {}
        for rule in primary["composition"]["allowed"]:
            component_rules[rule["standard_key"]] = rule
            if (
                not rule["required"]
                and rule["standard_key"] not in requested_components
            ):
                continue
            possibilities = [
                by_identity[(rule["standard_key"], version)]
                for version in rule["versions"]
                if (rule["standard_key"], version) in by_identity
                and by_identity[(rule["standard_key"], version)][0]["lifecycle"]
                == "active"
            ]
            if not possibilities:
                blocked.add("BPP203_COMPONENT_UNAVAILABLE")
                continue
            possibilities.sort(
                key=lambda row: (
                    -_version_tuple(row[0]["version"])[0],
                    -_version_tuple(row[0]["version"])[1],
                    -_version_tuple(row[0]["version"])[2],
                    row[1]["standard_id"],
                )
            )
            component, entry = possibilities[0]
            selected_records.append((component, entry, component["standard_key"]))
        selected_records[1:] = sorted(
            selected_records[1:], key=lambda row: row[1]["standard_id"]
        )

        parameter_sources: dict[str, list[tuple[dict[str, Any], str]]] = {}
        for compiled, _entry, participant in selected_records:
            for parameter in compiled["parameters"]:
                parameter_sources.setdefault(parameter["name"], []).append(
                    (parameter, participant)
                )
        parameter_definitions: dict[str, dict[str, Any]] = {}
        for name, definitions in parameter_sources.items():
            canonical = {
                standards.canonical_json(parameter): (parameter, participant)
                for parameter, participant in definitions
            }
            if len(canonical) == 1:
                parameter_definitions[name] = definitions[0][0]
                continue
            rules = [
                row
                for row in primary["composition"]["precedence"]
                if row["surface"] == f"parameter-{name}"
            ]
            participants = {participant for _parameter, participant in definitions}
            losers = {
                row["loser"]
                for row in rules
                if row["winner"] in participants and row["loser"] in participants
            }
            winners = participants - losers
            if len(winners) != 1:
                blocked.add("BPP204_PARAMETER_DEFINITION_CONFLICT")
            else:
                winner = next(iter(winners))
                parameter_definitions[name] = next(
                    parameter
                    for parameter, participant in definitions
                    if participant == winner
                )

        supplied = {row["name"]: row["value"] for row in request["parameters"]}
        revisions: set[str] = set()
        unknown_parameters = set(supplied) - set(parameter_definitions)
        if unknown_parameters:
            revisions.update(f"remove:{name}" for name in unknown_parameters)
        protected_supplied = {
            name
            for name, parameter in parameter_definitions.items()
            if parameter["class"] in {"derived", "allocated", "defaulted"}
            and name in supplied
        }
        revisions.update(f"remove:{name}" for name in protected_supplied)
        if revisions and not request["consent"]["accept_compliant_revision"]:
            blocked.add("BPP205_COMPLIANT_REVISION_REQUIRED")
        for name in unknown_parameters | protected_supplied:
            supplied.pop(name, None)

        values: dict[str, Any] = {}
        origins: dict[str, str] = {}
        accepted: dict[str, bool] = {}
        for name in sorted(parameter_definitions):
            parameter = parameter_definitions[name]
            parameter_class = parameter["class"]
            if parameter_class == "required":
                if name not in supplied:
                    blocked.add(f"BPP206_REQUIRED_PARAMETER:{name}")
                    continue
                values[name] = supplied[name]
                origins[name] = "request"
                accepted[name] = False
            elif parameter_class == "optional":
                values[name] = supplied.get(name)
                origins[name] = "request" if name in supplied else "optional-absent"
                accepted[name] = False
            elif parameter_class == "defaulted":
                values[name] = parameter["default"]
                origins[name] = "standard-default"
                accepted[name] = name in protected_supplied

        allocation_by_domain = {
            row["domain_name"]: row for row in evidence["allocation"]
        }
        ledger_occupied = {
            (row["domain_name"], row["value"])
            for row in ledger["reservations"]
        }
        domains = {
            domain["name"]: domain
            for compiled, _entry, _participant in selected_records
            for domain in compiled["allocation"]["domains"]
        }
        for name in sorted(parameter_definitions):
            parameter = parameter_definitions[name]
            if parameter["class"] != "allocated":
                continue
            domain = domains[parameter["allocation_domain"]]
            row = allocation_by_domain.get(domain["name"])
            if row is None or row["authority_id"] != domain["authority_id"]:
                blocked.add(f"BPP207_ALLOCATION_EVIDENCE:{domain['name']}")
                continue
            if domain["mode"] == "blueprints-ledger":
                occupied = set(row["occupied_values"]) | {
                    value
                    for domain_name, value in ledger_occupied
                    if domain_name == domain["name"]
                }
                value = next(
                    (
                        candidate
                        for candidate in range(
                            domain["pool"]["minimum"],
                            domain["pool"]["maximum"] + 1,
                        )
                        if candidate not in occupied
                        and _constraints_match(candidate, parameter)
                    ),
                    None,
                )
                if value is None:
                    blocked.add(f"BPP208_ALLOCATION_EXHAUSTED:{domain['name']}")
                    continue
            else:
                value = row["proposed_value"]
                if value is None or value in row["occupied_values"]:
                    blocked.add(f"BPP209_ALLOCATION_UNAVAILABLE:{domain['name']}")
                    continue
            values[name] = value
            origins[name] = (
                f"allocation:{domain['name']}:{domain['authority_id']}:"
                f"{row['evidence_sha256']}:{ledger['ledger_id']}"
            )
            accepted[name] = name in protected_supplied

        unresolved_derived = {
            name
            for name, parameter in parameter_definitions.items()
            if parameter["class"] == "derived"
        }
        while unresolved_derived:
            progressed = False
            for name in sorted(unresolved_derived):
                refs = standards._expression_parameter_refs(
                    parameter_definitions[name]["derivation"]
                )
                if refs <= set(values):
                    values[name] = _evaluate_expression(
                        parameter_definitions[name]["derivation"], values
                    )
                    origins[name] = "standard-derivation"
                    accepted[name] = name in protected_supplied
                    unresolved_derived.remove(name)
                    progressed = True
                    break
            if not progressed:
                blocked.add("BPP210_DERIVATION_UNAVAILABLE")
                break

        effective_parameters = []
        for name in sorted(values):
            parameter = parameter_definitions[name]
            if not _constraints_match(values[name], parameter):
                blocked.add(f"BPP211_PARAMETER_CONSTRAINT:{name}")
            effective_parameters.append(
                {
                    "name": name,
                    "class": parameter["class"],
                    "value": values[name],
                    "origin": origins[name],
                    "accepted": accepted[name],
                }
            )

        selected_variants: dict[str, set[str]] = {}
        primary_applicable = [
            variant
            for variant in primary["variants"]
            if all(
                _condition_matches(condition, values)
                for condition in variant["when"]
            )
        ]
        by_variant = {row["id"]: row for row in primary["variants"]}
        requested_variants = set(request["requested_variants"])
        explicit_variant = choices.get("primary_variant")
        if explicit_variant is not None:
            requested_variants.add(explicit_variant)
            rationale.add("developer-variant-choice")
        unknown_variants = requested_variants - set(by_variant)
        if unknown_variants:
            blocked.add("BPP212_UNKNOWN_VARIANT")
        inapplicable = requested_variants - {
            row["id"] for row in primary_applicable
        }
        if inapplicable:
            blocked.add("BPP213_INAPPLICABLE_VARIANT")
        if requested_variants and not unknown_variants and not inapplicable:
            selected_variants["primary"] = set(requested_variants)
        elif primary_applicable:
            primary_applicable.sort(
                key=lambda row: (
                    -row["priority"],
                    -row["specificity"],
                    row["id"],
                )
            )
            top_variant_rank = (
                primary_applicable[0]["priority"],
                primary_applicable[0]["specificity"],
            )
            top_variants = [
                row
                for row in primary_applicable
                if (row["priority"], row["specificity"]) == top_variant_rank
            ]
            if len(top_variants) > 1:
                blocked.add("BPP214_VARIANT_SELECTION_TIE")
            selected_variants["primary"] = {top_variants[0]["id"]}
        else:
            selected_variants["primary"] = set()

        component_variant_choices = choices.get("component_variants", {})
        for component, _entry, participant in selected_records[1:]:
            rule = component_rules[component["standard_key"]]
            allowed_ids = set(rule["component_variants"])
            applicable = [
                variant
                for variant in component["variants"]
                if variant["id"] in allowed_ids
                and all(
                    _condition_matches(condition, values)
                    for condition in variant["when"]
                )
            ]
            explicit = component_variant_choices.get(component["standard_key"])
            if explicit is not None:
                applicable_by_id = {row["id"]: row for row in applicable}
                if explicit not in applicable_by_id:
                    blocked.add("BPP215_COMPONENT_VARIANT")
                    selected_variants[participant] = set()
                else:
                    selected_variants[participant] = {explicit}
            elif applicable:
                applicable.sort(
                    key=lambda row: (
                        -row["priority"],
                        -row["specificity"],
                        row["id"],
                    )
                )
                selected_variants[participant] = {applicable[0]["id"]}
            else:
                blocked.add("BPP215_COMPONENT_VARIANT")
                selected_variants[participant] = set()

        primary_selected_variants = selected_variants["primary"]
        for component, _entry, participant in selected_records[1:]:
            rule = component_rules[component["standard_key"]]
            if rule["primary_variants"] and not (
                primary_selected_variants & set(rule["primary_variants"])
            ):
                blocked.add("BPP216_COMPONENT_COMPATIBILITY")
            compatible = set.intersection(
                *(
                    set(by_variant[variant_id]["compatible_components"])
                    for variant_id in primary_selected_variants
                )
            ) if primary_selected_variants else set()
            if (
                primary_selected_variants
                and component["standard_key"] not in compatible
            ):
                blocked.add("BPP216_COMPONENT_COMPATIBILITY")

        baselines = {
            compiled["atlas"]["baseline_id"]
            for compiled, _entry, _participant in selected_records
        }
        if len(baselines) != 1:
            blocked.add("BPP217_ATLAS_BASELINE_CONFLICT")
        baseline_id = sorted(baselines)[0]
        query_evidence = {
            (row["standard_key"], row["query_id"]): row
            for row in evidence["atlas"]["queries"]
        }
        invariant_results = []
        for compiled, _entry, _participant in selected_records:
            for query in compiled["atlas"]["queries"]:
                row = query_evidence.get((compiled["standard_key"], query["query_id"]))
                for invariant in query["invariants"]:
                    invariant_id = (
                        f"{compiled['standard_key']}:{query['id']}:{invariant['id']}"
                    )
                    if (
                        row is None
                        or row["availability"] == "unavailable"
                        or row["result_id"] != query["result_id"]
                    ):
                        status = "unavailable"
                        evidence_sha256 = (
                            row["evidence_sha256"] if row is not None else "0" * 64
                        )
                    else:
                        status = _atlas_invariant_status(invariant, row["result"])
                        evidence_sha256 = row["evidence_sha256"]
                    if status != "pass":
                        blocked.add(f"BPP218_ATLAS_INVARIANT:{invariant_id}")
                    invariant_results.append(
                        {
                            "invariant_id": invariant_id,
                            "status": status,
                            "evidence_sha256": evidence_sha256,
                        }
                    )
        invariant_results.sort(key=lambda row: row["invariant_id"])
        if evidence["atlas"]["relevant_drift"] == "blocking":
            blocked.add("BPP219_ATLAS_RELEVANT_DRIFT")

        reconciliation_by_standard = {
            row["standard_key"]: row for row in evidence["reconciliation"]
        }
        reconciliation_outcome: dict[str, str] = {}
        for compiled, _entry, _participant in selected_records:
            if not compiled["reconciliation"]:
                continue
            row = reconciliation_by_standard.get(compiled["standard_key"])
            if row is None:
                blocked.add(
                    f"BPP220_RECONCILIATION_EVIDENCE:{compiled['standard_key']}"
                )
                continue
            matches = [
                rule
                for rule in compiled["reconciliation"]
                if rule["when"]["kind"] == row["identity_kind"]
            ]
            if len(matches) != 1:
                blocked.add(
                    f"BPP221_RECONCILIATION_RULE:{compiled['standard_key']}"
                )
                continue
            observed = row["observed"]
            fields = matches[0]["when"]["fields"]
            if row["identity_kind"] == "identity-absent":
                classification_valid = observed is None
            elif row["identity_kind"] == "identity-equivalent":
                classification_valid = (
                    isinstance(observed, dict)
                    and all(
                        field in values
                        and observed.get(field) == values[field]
                        for field in fields
                    )
                )
            else:
                classification_valid = observed is not None
            if not classification_valid:
                blocked.add(
                    f"BPP237_RECONCILIATION_CLASSIFICATION:"
                    f"{compiled['standard_key']}"
                )
                continue
            outcome = matches[0]["outcome"]
            reconciliation_outcome[compiled["standard_key"]] = outcome
            if outcome in {"equivalent", "request-new-identity", "reject"}:
                blocked.add(
                    f"BPP222_RECONCILIATION_{outcome.upper().replace('-', '_')}"
                )

        authorized_paths = sorted(
            {
                path
                for compiled, _entry, _participant in selected_records
                for target in compiled["targets"]
                if target["repository_id"] == request["target"]["repository_id"]
                for path in target["allowed_paths"]
            }
        )
        universal_stages = [
            ("schema-standard-validity", "static"),
            ("composition-parameter-validity", "static"),
            ("authorized-path-allocation-collision", "static"),
            ("deterministic-regeneration", "static"),
            ("target-state-consistency", "static"),
            ("pinned-formatter-stability", "format"),
            ("isolated-compilation", "compile"),
            ("target-existing-tests", "test"),
            ("generated-invariant-tests", "test"),
            ("generated-collision-tests", "test"),
            ("generated-determinism-tests", "test"),
            ("generated-placement-tests", "test"),
        ]
        additional_stages = sorted(
            {
                (
                    f"{compiled['standard_key']}--{gate['id']}",
                    gate["stage"],
                )
                for compiled, _entry, _participant in selected_records
                for gate in compiled["validation"]["gates"]
                if gate["required"]
            }
        )
        validation_stages = [
            {
                "ordinal": ordinal,
                "stage_id": stage_id,
                "kind": kind,
                "required": True,
            }
            for ordinal, (stage_id, kind) in enumerate(
                universal_stages + additional_stages
            )
        ]

        rendered_operations: list[dict[str, Any]] = []
        sealed_operations: list[dict[str, Any]] = []
        if not blocked:
            try:
                first = self._render(
                    selected_records,
                    selected_variants,
                    values,
                    request,
                    primary,
                    reconciliation_outcome,
                )
                second = self._render(
                    selected_records,
                    selected_variants,
                    values,
                    request,
                    primary,
                    reconciliation_outcome,
                )
                if first != second:
                    blocked.add("BPP223_NONDETERMINISTIC_RENDER")
                else:
                    rendered_operations, sealed_operations = first
            except PlannerDiagnostic as exc:
                if exc.code in {
                    "BPP171_STALE_TARGET",
                    "BPP160_SEALED_COLLISION",
                }:
                    raise
                blocked.add(exc.code)

        variant_ids = sorted(
            variant_id
            if participant == "primary"
            else f"{participant}--{variant_id}"
            for participant, variants in selected_variants.items()
            for variant_id in variants
        )
        developer_choice_required = any(
            reason
            in {
                "BPP201_STANDARD_SELECTION_TIE",
                "BPP214_VARIANT_SELECTION_TIE",
            }
            for reason in blocked
        )
        plan = {
            "schema_version": 1,
            "format": "susy-blueprints-plan-v1",
            "contract_id": ENGINE_CONTRACT_ID,
            "request_id": request["request_id"],
            "target_state_id": request["target"]["target_state_id"],
            "standards": {
                "primary": _record_binding(primary_entry),
                "components": [
                    _record_binding(entry)
                    for _compiled, entry, _participant in selected_records[1:]
                ],
            },
            "selection": {
                "variant_ids": variant_ids,
                "rationale_codes": sorted(rationale),
                "developer_choice_required": developer_choice_required,
            },
            "effective_parameters": effective_parameters,
            "atlas": {
                "baseline_id": baseline_id,
                "current_id": evidence["atlas"]["current_id"],
                "relevant_drift": evidence["atlas"]["relevant_drift"],
                "invariant_results": invariant_results,
            },
            "authorized_paths": authorized_paths,
            "operations": rendered_operations if not blocked else [],
            "validation_stages": validation_stages,
            "status": "blocked" if blocked else "ready",
            "blocked_reasons": sorted(blocked),
        }
        plan["plan_id"] = _identity("blueprints-plan:sha256:", plan, "plan_id")
        _validate_schema(plan, PLAN_SCHEMA, "plan")
        candidate = None
        if plan["status"] == "ready":
            self._require_target_unchanged(target_manifest)
            _ledger_now, _records_now, authority_now = self._load_standards()
            if authority_now != authority_state:
                _fail(
                    "BPP174_AUTHORITY_DRIFT",
                    str(self.registry_root),
                    "registry or allocation ledger changed during planning",
                )
            content_manifest_sha256 = _operation_manifest(plan["operations"])
            payload = {
                "schema_version": 1,
                "format": "susy-blueprints-sealed-manifest-v1",
                "contract_id": ENGINE_CONTRACT_ID,
                "plan_id": plan["plan_id"],
                "target_state_id": plan["target_state_id"],
                "standard_ids": [
                    plan["standards"]["primary"]["standard_id"],
                    *[
                        row["standard_id"]
                        for row in plan["standards"]["components"]
                    ],
                ],
                "content_manifest_sha256": content_manifest_sha256,
                "operations": sealed_operations,
            }
            locator = self.sealed_store.put(payload)
            candidate = {
                "plan_id": plan["plan_id"],
                "target_state_id": plan["target_state_id"],
                "standard_ids": payload["standard_ids"],
                "content_manifest_sha256": content_manifest_sha256,
                "sealed_locator": locator,
                "edit_generation": edit_generation,
            }
            candidate["candidate_id"] = _identity(
                "blueprints-candidate:sha256:", candidate, "candidate_id"
            )
            _validate_candidate(candidate)
        return {
            "request": request,
            "plan": plan,
            "candidate": candidate,
            "diagnostics": sorted(blocked),
            "compliant_revisions": [
                {
                    "action": "remove-developer-value",
                    "parameter": revision.split(":", 1)[1],
                    "accepted": request["consent"][
                        "accept_compliant_revision"
                    ],
                }
                for revision in sorted(revisions)
            ],
            "planning_evidence_sha256": _digest_json(evidence),
        }

    def _render(
        self,
        selected_records: list[
            tuple[dict[str, Any], dict[str, Any], str]
        ],
        selected_variants: dict[str, set[str]],
        values: dict[str, Any],
        request: dict[str, Any],
        primary: dict[str, Any],
        reconciliation_outcome: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        outputs: list[dict[str, Any]] = []
        for compiled, _entry, participant in selected_records:
            templates = {
                compiled["mandatory_core"]["id"]: compiled["mandatory_core"],
                **{
                    variant["template"]["id"]: variant["template"]
                    for variant in compiled["variants"]
                },
            }
            hooks = {hook["id"]: hook for hook in compiled["hooks"]}
            for output in compiled["rendering"]["outputs"]:
                if (
                    output["repository_id"]
                    != request["target"]["repository_id"]
                ):
                    continue
                if output["variants"] and not (
                    set(output["variants"]) & selected_variants[participant]
                ):
                    continue
                outcome = reconciliation_outcome.get(compiled["standard_key"])
                if outcome == "create" and output["operation"] != "create":
                    _fail(
                        "BPP235_RECONCILIATION_OPERATION",
                        output["id"],
                        "create reconciliation selected a non-create output",
                    )
                if outcome == "approved-update" and output["operation"] == "create":
                    _fail(
                        "BPP235_RECONCILIATION_OPERATION",
                        output["id"],
                        "approved update selected a create output",
                    )
                path_value = _evaluate_expression(output["path"], values)
                if not isinstance(path_value, str):
                    _fail(
                        "BPP224_RENDER_PATH_TYPE",
                        output["id"],
                        "render path expression did not return a string",
                    )
                _safe_path(path_value, directory=False, location=output["id"])
                target = next(
                    row
                    for row in compiled["targets"]
                    if row["repository_id"] == output["repository_id"]
                    and row["integration_surface"]
                    == output["integration_surface"]
                )
                if not any(
                    path_value.startswith(prefix)
                    for prefix in target["allowed_paths"]
                ):
                    _fail(
                        "BPP225_RENDER_PATH_AUTHORITY",
                        path_value,
                        "render path is outside standard authorization",
                    )
                content = None
                if output["operation"] != "delete":
                    source = output["source"]
                    if source["kind"] == "template":
                        template = templates[source["id"]]
                        template_bytes = _read_regular_nofollow(
                            self.asset_root / template["path"],
                            "BPP234_TEMPLATE_DIGEST",
                        )
                        if _digest_bytes(template_bytes) != template["sha256"]:
                            _fail(
                                "BPP234_TEMPLATE_DIGEST",
                                template["path"],
                                "template changed after standard admission check",
                            )
                        content = _render_template(
                            template_bytes,
                            values,
                            template["path"],
                        )
                    else:
                        if self.hook_runner is None:
                            _fail(
                                "BPP226_RENDER_HOOK_UNAVAILABLE",
                                source["id"],
                                "trusted render hook runner is unavailable",
                            )
                        content = self.hook_runner(
                            hooks[source["id"]], values, path_value
                        )
                        if not isinstance(content, bytes):
                            _fail(
                                "BPP227_RENDER_HOOK_RESULT",
                                source["id"],
                                "trusted render hook did not return bytes",
                            )
                outputs.append(
                    {
                        "participant": participant,
                        "surface": output["integration_surface"],
                        "operation": output["operation"],
                        "path": path_value,
                        "content": content,
                    }
                )

        by_path: dict[str, list[dict[str, Any]]] = {}
        for output in outputs:
            by_path.setdefault(output["path"], []).append(output)
        resolved: list[dict[str, Any]] = []
        precedence = primary["composition"]["precedence"]
        for path, contenders in sorted(by_path.items()):
            if len(contenders) == 1:
                resolved.append(contenders[0])
                continue
            winners = []
            for contender in contenders:
                if all(
                    _precedes(
                        contender["participant"],
                        other["participant"],
                        contender["surface"],
                        precedence,
                    )
                    for other in contenders
                    if other is not contender
                ):
                    winners.append(contender)
            if len(winners) != 1:
                _fail(
                    "BPP228_RENDER_COLLISION",
                    path,
                    "output collision has no unique admitted precedence winner",
                )
            resolved.append(winners[0])

        if not resolved:
            _fail(
                "BPP236_NO_TARGET_OUTPUT",
                request["target"]["repository_id"],
                "selected standards produced no operation for the target repository",
            )

        repository = self.target_repository
        for output in resolved:
            candidate = repository / output["path"]
            cursor = repository
            for part in PurePosixPath(output["path"]).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    _fail(
                        "BPP229_TARGET_SYMLINK",
                        output["path"],
                        "operation path contains a symlink component",
                    )
            exists = candidate.exists() or candidate.is_symlink()
            if output["operation"] == "create" and exists:
                _fail(
                    "BPP230_CREATE_EXISTS",
                    output["path"],
                    "create target already exists",
                )
            if output["operation"] in {"update", "delete"} and not exists:
                _fail(
                    "BPP231_UPDATE_MISSING",
                    output["path"],
                    "update/delete target does not exist",
                )

        formatted = {
            row["path"]: row["content"]
            for row in resolved
            if row["content"] is not None
        }
        for compiled, _entry, _participant in selected_records:
            for formatter in compiled["validation"]["formatters"]:
                if (
                    formatter["repository_id"]
                    != request["target"]["repository_id"]
                ):
                    continue
                affected = {
                    path: content
                    for path, content in formatted.items()
                    if any(path.startswith(prefix) for prefix in formatter["paths"])
                }
                if not affected:
                    continue
                if self.formatter_runner is None:
                    _fail(
                        "BPP232_FORMATTER_UNAVAILABLE",
                        formatter["id"],
                        "pinned formatter runner is unavailable",
                    )
                result = self.formatter_runner(formatter, dict(affected))
                if set(result) != set(affected) or any(
                    not isinstance(content, bytes) for content in result.values()
                ):
                    _fail(
                        "BPP233_FORMATTER_SCOPE",
                        formatter["id"],
                        "formatter changed paths or returned non-byte content",
                    )
                formatted.update(result)

        operations = []
        sealed_operations = []
        for ordinal, output in enumerate(sorted(resolved, key=lambda row: row["path"])):
            content = (
                None
                if output["operation"] == "delete"
                else formatted[output["path"]]
            )
            digest = None if content is None else _digest_bytes(content)
            operation = {
                "ordinal": ordinal,
                "operation": output["operation"],
                "path": output["path"],
                "content_sha256": digest,
            }
            operations.append(operation)
            sealed_operations.append(
                {
                    **operation,
                    "content_base64": (
                        None
                        if content is None
                        else base64.b64encode(content).decode("ascii")
                    ),
                }
            )
        return operations, sealed_operations


def invalidate_candidate(
    candidate: dict[str, Any],
    *,
    invalidated_ids: list[str],
) -> dict[str, Any]:
    """Produce the P01 invalidation projection for an edited candidate generation."""

    _validate_candidate(candidate)
    downstream_id = re.compile(
        r"^blueprints-(?:candidate|simulation|release|patch|application|"
        r"verification|proof):sha256:[0-9a-f]{64}$"
    )
    if any(
        not isinstance(identity, str)
        or downstream_id.fullmatch(identity) is None
        for identity in invalidated_ids
    ):
        _fail(
            "BPP176_INVALIDATION_ID",
            "/invalidated_ids",
            "invalidation contains an unsupported identity",
        )
    identities = sorted(
        set([candidate["candidate_id"], *invalidated_ids])
    )
    return {
        "invalidated_ids": identities,
        "reason": "request-plan-or-candidate-edited",
        "prior_edit_generation": candidate["edit_generation"],
        "next_edit_generation": candidate["edit_generation"] + 1,
    }
