#!/usr/bin/env python3

"""Plan, build, or verify a sterile clean-root public source export.

The reviewed Git commit is the source of every exported byte and mode. The
working tree is inspected only as a release-process guard; it is never copied.
A build also requires a trusted operator's local secret-scan receipt bound to
the exact commit and canonical public-tree digest. This tool validates that
binding, but it cannot prove that the operator actually ran the named scanner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_SOURCE = ROOT / "validation"
for source in (ROOT / "tools", VALIDATION_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from validate import DISALLOWED_SUFFIXES, MAX_SOURCE_BYTES  # noqa: E402
from repository_policy import (  # noqa: E402
    PublicRepositoryError,
    load_public_repository,
)
from validate_public_tree import private_reason, private_markdown_reason  # noqa: E402


MAX_SOURCE_FILES = 10_000
MAX_TOTAL_SOURCE_BYTES = 128 * 1024 * 1024
MAX_PATH_DEPTH = 32
MAX_PATH_UTF8_BYTES = 512
MAX_RECEIPT_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MIN_GITLEAKS_VERSION = (8, 30, 1)

OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?$")
WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
SENSITIVE_BASENAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets",
    "secrets.json",
}
SENSITIVE_DIRECTORY_NAMES = {"credential", "credentials", "secret", "secrets"}
SENSITIVE_SUFFIXES = {
    ".jks",
    ".kdbx",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".token",
}
PRIVATE_KEY_MARKERS = tuple(
    b"-----BEGIN " + kind + b"-----"
    for kind in (
        b"PRIVATE KEY",
        b"ENCRYPTED PRIVATE KEY",
        b"OPENSSH PRIVATE KEY",
        b"RSA PRIVATE KEY",
        b"DSA PRIVATE KEY",
        b"EC PRIVATE KEY",
    )
)

# Public binary content is fail-closed. Add a path only after its provenance,
# deterministic generation, and validation are reviewed. Text SVG assets do
# not need an entry because they must pass the UTF-8 text policy.
ALLOWED_BINARY_ASSETS: dict[str, bytes] = {
    "clients/vscode/media/workbench-icon.png":
        b"\x89PNG\r\n\x1a\n",
}


class PublicExportError(ValueError):
    pass


def _git_environment() -> dict[str, str]:
    """Return an environment that cannot redirect Git to another repository."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return environment


def _git_command(*arguments: str) -> list[str]:
    executable = shutil.which("git")
    if executable is None:
        raise PublicExportError("Git is unavailable")
    return [
        executable,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "submodule.recurse=false",
        *arguments,
    ]


def _git(root: Path, *arguments: str, timeout: int = 120) -> bytes:
    completed = subprocess.run(
        _git_command(*arguments),
        cwd=root,
        check=False,
        env=_git_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PublicExportError(f"Git inspection failed: {detail}")
    return completed.stdout


def _verified_git_root(root: Path) -> tuple[Path, str]:
    root = root.resolve()
    if not root.is_dir():
        raise PublicExportError(f"repository root is not a directory: {root}")
    observed = Path(
        _git(root, "rev-parse", "--show-toplevel")
        .decode("utf-8", errors="strict")
        .strip()
    ).resolve()
    if observed != root:
        raise PublicExportError(
            f"Git worktree root mismatch: expected {root}, observed {observed}"
        )
    object_format = (
        _git(root, "rev-parse", "--show-object-format")
        .decode("ascii", errors="strict")
        .strip()
    )
    if object_format not in {"sha1", "sha256"}:
        raise PublicExportError(f"unsupported Git object format: {object_format}")
    return root, object_format


def _exact_commit(root: Path, revision: str, object_format: str) -> str:
    expected_length = 40 if object_format == "sha1" else 64
    normalized = revision.lower()
    if not re.fullmatch(rf"[0-9a-f]{{{expected_length}}}", normalized):
        raise PublicExportError(
            f"reviewed revision must be a full {expected_length}-hex Git commit id"
        )
    observed = (
        _git(root, "rev-parse", "--verify", f"{normalized}^{{commit}}")
        .decode("ascii", errors="strict")
        .strip()
        .lower()
    )
    if observed != normalized:
        raise PublicExportError("reviewed revision did not resolve to itself")
    return observed


def _head_commit(root: Path) -> str:
    return (
        _git(root, "rev-parse", "--verify", "HEAD^{commit}")
        .decode("ascii", errors="strict")
        .strip()
        .lower()
    )


def _index_state_is_plain(root: Path) -> bool:
    raw = _git(root, "ls-files", "-v", "-z")
    return all(not item or item.startswith(b"H ") for item in raw.split(b"\0"))


def _validate_public_path(relative: str) -> tuple[str, ...]:
    candidate = PurePosixPath(relative)
    if reason := private_reason(relative):
        raise PublicExportError(f"public source contains private harness material: {relative}: {reason}")
    if (
        candidate.is_absolute()
        or not candidate.parts
        or ".." in candidate.parts
        or candidate.as_posix() != relative
    ):
        raise PublicExportError(f"unsafe repository path: {relative!r}")
    if unicodedata.normalize("NFC", relative) != relative:
        raise PublicExportError(f"public source path is not NFC: {relative!r}")
    if len(relative.encode("utf-8")) > MAX_PATH_UTF8_BYTES:
        raise PublicExportError(f"public source path is too long: {relative}")
    if len(candidate.parts) > MAX_PATH_DEPTH:
        raise PublicExportError(f"public source path is too deep: {relative}")

    for part in candidate.parts:
        folded = part.casefold()
        if (
            "\\" in part
            or ":" in part
            or any(ord(character) < 32 or ord(character) == 127 for character in part)
            or part.endswith((" ", "."))
            or folded == ".git"
        ):
            raise PublicExportError(f"unsafe repository path component: {relative!r}")
        windows_stem = part.split(".", 1)[0].casefold()
        if windows_stem in WINDOWS_RESERVED_NAMES:
            raise PublicExportError(f"Windows-reserved public path: {relative}")
    return candidate.parts


def _validate_sensitive_path(relative: str, parts: tuple[str, ...]) -> None:
    folded_parts = tuple(part.casefold() for part in parts)
    basename = folded_parts[-1]
    if any(part in SENSITIVE_DIRECTORY_NAMES for part in folded_parts[:-1]):
        raise PublicExportError(f"public source uses a sensitive directory: {relative}")
    if (
        basename in SENSITIVE_BASENAMES
        or (basename.startswith(".env.") and basename != ".env.example")
        or basename.startswith("credentials.")
        or basename.startswith("secrets.")
        or PurePosixPath(basename).suffix in SENSITIVE_SUFFIXES
    ):
        raise PublicExportError(f"public source has a sensitive path: {relative}")


def _validate_content(relative: str, content: bytes) -> str:
    if any(marker in content for marker in PRIVATE_KEY_MARKERS):
        raise PublicExportError(f"public source contains a private-key marker: {relative}")
    binary_signature = ALLOWED_BINARY_ASSETS.get(relative)
    if binary_signature is not None:
        if not content.startswith(binary_signature):
            raise PublicExportError(
                f"allowlisted binary has an unexpected signature: {relative}"
            )
        return "reviewed-binary"
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PublicExportError(
            f"public source is non-allowlisted binary content: {relative}"
        ) from exc
    if reason := private_markdown_reason(relative, text):
        raise PublicExportError(f"public Markdown contains private coordination: {relative}: {reason}")
    if any(
        ord(character) < 32 and character not in "\t\n\r\f"
        for character in text
    ):
        raise PublicExportError(
            f"public source contains binary control characters: {relative}"
        )
    return "utf-8-text"


def _parse_tree_rows(root: Path, commit: str) -> list[dict[str, Any]]:
    raw = _git(root, "ls-tree", "-rzl", "--full-tree", commit)
    rows: list[dict[str, Any]] = []
    path_keys: dict[str, str] = {}
    total_bytes = 0
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            metadata, path_raw = item.split(b"\t", 1)
            mode_raw, kind_raw, oid_raw, size_raw = metadata.split()
            relative = path_raw.decode("utf-8", errors="strict")
            mode = mode_raw.decode("ascii", errors="strict")
            kind = kind_raw.decode("ascii", errors="strict")
            oid = oid_raw.decode("ascii", errors="strict").lower()
        except (UnicodeError, ValueError) as exc:
            raise PublicExportError("Git tree inventory is malformed") from exc
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise PublicExportError(
                f"public Git tree contains a symlink, gitlink, or non-blob: {relative}"
            )
        try:
            size = int(size_raw)
        except ValueError as exc:
            raise PublicExportError(f"Git blob size is malformed: {relative}") from exc
        if not OBJECT_ID_RE.fullmatch(oid):
            raise PublicExportError(f"Git tree has an invalid object id: {relative}")
        parts = _validate_public_path(relative)
        _validate_sensitive_path(relative, parts)
        collision_key = unicodedata.normalize("NFC", relative).casefold()
        previous = path_keys.get(collision_key)
        if previous is not None:
            raise PublicExportError(
                f"public paths collide on case-insensitive filesystems: "
                f"{previous!r} and {relative!r}"
            )
        path_keys[collision_key] = relative
        if PurePosixPath(relative).suffix.lower() in DISALLOWED_SUFFIXES:
            raise PublicExportError(f"public source has disallowed suffix: {relative}")
        if size > MAX_SOURCE_BYTES:
            raise PublicExportError(f"public source exceeds size bound: {relative}")
        if size < 0:
            raise PublicExportError(f"public source has an invalid size: {relative}")
        total_bytes += size
        if total_bytes > MAX_TOTAL_SOURCE_BYTES:
            raise PublicExportError("public source exceeds aggregate size bound")
        rows.append(
            {
                "git_blob_oid": oid,
                "mode": mode,
                "path": relative,
                "size": size,
            }
        )
        if len(rows) > MAX_SOURCE_FILES:
            raise PublicExportError("public source exceeds file-count bound")
    if not rows:
        raise PublicExportError("public Git tree is empty")
    rows.sort(key=lambda row: row["path"].encode("utf-8"))
    return rows


def _read_git_blobs(root: Path, object_ids: Sequence[str]) -> dict[str, bytes]:
    unique_ids = list(dict.fromkeys(object_ids))
    request = b"".join(object_id.encode("ascii") + b"\n" for object_id in unique_ids)
    process = subprocess.Popen(
        _git_command("cat-file", "--batch"),
        cwd=root,
        env=_git_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(input=request, timeout=120)
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise PublicExportError(f"Git blob inspection failed: {detail}")

    result: dict[str, bytes] = {}
    offset = 0
    for requested in unique_ids:
        end = stdout.find(b"\n", offset)
        if end < 0:
            raise PublicExportError("Git blob response is truncated")
        header = stdout[offset:end].split()
        offset = end + 1
        if len(header) != 3:
            raise PublicExportError("Git blob response header is malformed")
        actual_oid, kind, size_raw = header
        try:
            size = int(size_raw)
        except ValueError as exc:
            raise PublicExportError("Git blob response size is malformed") from exc
        if actual_oid.decode("ascii").lower() != requested or kind != b"blob":
            raise PublicExportError("Git blob response identity is inconsistent")
        end = offset + size
        if end >= len(stdout) or stdout[end : end + 1] != b"\n":
            raise PublicExportError("Git blob response body is truncated")
        result[requested] = stdout[offset:end]
        offset = end + 1
    if offset != len(stdout):
        raise PublicExportError("Git blob response has trailing data")
    return result


def _canonical_compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_pretty_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _tree_digest(files: Sequence[Mapping[str, Any]]) -> str:
    tree_input = {
        "files": [dict(row) for row in files],
        "format": "workbench-public-tree-v1",
        "schema_version": 1,
    }
    return hashlib.sha256(_canonical_compact_json(tree_input)).hexdigest()


def _commit_inventory(root: Path, commit: str) -> tuple[list[dict[str, Any]], str, int]:
    rows = _parse_tree_rows(root, commit)
    blobs = _read_git_blobs(root, [row["git_blob_oid"] for row in rows])
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for row in rows:
        content = blobs[row["git_blob_oid"]]
        if len(content) != row["size"]:
            raise PublicExportError(f"Git blob size drifted: {row['path']}")
        files.append(
            {
                **row,
                "content_kind": _validate_content(row["path"], content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        total_bytes += len(content)
    return files, _tree_digest(files), total_bytes


def _strict_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or raw.startswith(b"\xef\xbb\xbf"):
        raise PublicExportError(f"{label} is empty or has a UTF-8 BOM")

    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise PublicExportError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                PublicExportError(f"{label} contains non-finite value {token!r}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PublicExportError(f"{label} is not strict UTF-8 JSON") from exc
    if type(value) is not dict:
        raise PublicExportError(f"{label} root must be an object")
    return value


def _safe_receipt_path(root: Path, value: Path) -> Path:
    if value.is_symlink():
        raise PublicExportError("secret-scan receipt must not be a symlink")
    receipt = value.resolve()
    if not receipt.is_file():
        raise PublicExportError(f"secret-scan receipt is not a regular file: {receipt}")
    if root in receipt.parents and not receipt.is_relative_to(root / ".workbench"):
        raise PublicExportError(
            "an in-repository secret-scan receipt must be under ignored .workbench storage"
        )
    if receipt.stat().st_size > MAX_RECEIPT_BYTES:
        raise PublicExportError("secret-scan receipt exceeds size bound")
    return receipt


def _receipt_template(commit: str, git_tree_oid: str, tree_sha256: str) -> dict[str, Any]:
    return {
        "allowlist_reviewed": True,
        "finding_count": 0,
        "format": "workbench-public-secret-scan-receipt-v1",
        "git_tree_oid": git_tree_oid,
        "result": "pass",
        "scan_scope": "exact-public-git-tree",
        "scanner": {"name": "gitleaks", "version": "8.30.1"},
        "schema_version": 1,
        "source_revision": commit,
        "tree_sha256": tree_sha256,
    }


def _validate_receipt(
    root: Path,
    value: Path,
    *,
    commit: str,
    git_tree_oid: str,
    tree_sha256: str,
) -> dict[str, Any]:
    receipt_path = _safe_receipt_path(root, value)
    raw = receipt_path.read_bytes()
    receipt = _strict_json_bytes(raw, label="secret-scan receipt")
    expected_keys = {
        "allowlist_reviewed",
        "finding_count",
        "format",
        "git_tree_oid",
        "result",
        "scan_scope",
        "scanner",
        "schema_version",
        "source_revision",
        "tree_sha256",
    }
    if set(receipt) != expected_keys:
        raise PublicExportError("secret-scan receipt fields are not exact")
    scanner = receipt.get("scanner")
    if type(scanner) is not dict or set(scanner) != {"name", "version"}:
        raise PublicExportError("secret-scan receipt scanner fields are not exact")
    scanner_version = scanner.get("version")
    match = (
        SEMVER_RE.fullmatch(scanner_version)
        if type(scanner_version) is str
        else None
    )
    if (
        receipt["format"] != "workbench-public-secret-scan-receipt-v1"
        or receipt["schema_version"] != 1
        or receipt["source_revision"] != commit
        or receipt["git_tree_oid"] != git_tree_oid
        or receipt["tree_sha256"] != tree_sha256
        or receipt["scan_scope"] != "exact-public-git-tree"
        or receipt["result"] != "pass"
        or receipt["finding_count"] != 0
        or receipt["allowlist_reviewed"] is not True
        or scanner.get("name") != "gitleaks"
        or match is None
        or tuple(int(number) for number in match.groups()) < MIN_GITLEAKS_VERSION
    ):
        raise PublicExportError(
            "secret-scan receipt is not a passing exact-tree Gitleaks attestation"
        )
    return {
        "allowlist_reviewed": True,
        "finding_count": 0,
        "git_tree_oid": git_tree_oid,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "required": True,
        "result": "pass",
        "scan_scope": "exact-public-git-tree",
        "scanner": dict(scanner),
        "source_revision": commit,
        "tree_sha256": tree_sha256,
        "verified": True,
    }


def public_export_plan(
    revision: str,
    secret_scan_receipt: Path | None = None,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    root, object_format = _verified_git_root(root)
    repository = load_public_repository(root)
    history = repository["history_policy"]
    if history["kind"] != "clean-root-export":
        raise PublicExportError("public history strategy is not a clean-root export")
    commit = _exact_commit(root, revision, object_format)
    head = _head_commit(root)
    git_tree_oid = (
        _git(root, "rev-parse", "--verify", f"{commit}^{{tree}}")
        .decode("ascii", errors="strict")
        .strip()
        .lower()
    )
    files, tree_sha256, total_bytes = _commit_inventory(root, commit)

    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    worktree_clean = not status
    index_state_clean = _index_state_is_plain(root)
    reviewed_revision_is_head = commit == head
    blockers: list[str] = []
    if not reviewed_revision_is_head:
        blockers.append("reviewed-revision-not-head")
    if not worktree_clean:
        blockers.append("working-tree-not-clean")
    if not index_state_clean:
        blockers.append("index-has-special-state")

    if secret_scan_receipt is None:
        secret_scan = {
            "required": True,
            "verified": False,
            "template": _receipt_template(commit, git_tree_oid, tree_sha256),
        }
        blockers.append("secret-scan-receipt-required")
    else:
        secret_scan = _validate_receipt(
            root,
            secret_scan_receipt,
            commit=commit,
            git_tree_oid=git_tree_oid,
            tree_sha256=tree_sha256,
        )

    return {
        "blockers": blockers,
        "destination": repository["destination"]["full_name"],
        "eligible": not blockers,
        "existing_branches_included": False,
        "existing_tags_included": False,
        "file_count": len(files),
        "files": files,
        "format": "workbench-public-export-plan-v1",
        "git_tree_oid": git_tree_oid,
        "history_strategy": history["kind"],
        "hosting_claims": repository["claims"],
        "index_state_clean": index_state_clean,
        "reviewed_revision_is_head": reviewed_revision_is_head,
        "schema_version": 1,
        "secret_scan": secret_scan,
        "source_clean": worktree_clean and index_state_clean,
        "source_head_revision": head,
        "source_revision": commit,
        "total_bytes": total_bytes,
        "tree_sha256": tree_sha256,
    }


def _safe_output(root: Path, value: Path) -> Path:
    output = value.resolve()
    if output == root or (
        root in output.parents
        and not output.is_relative_to(root / ".workbench/public-export")
    ):
        raise PublicExportError(
            "an in-repository export must be under .workbench/public-export"
        )
    if os.path.lexists(output):
        raise PublicExportError(f"export output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _write_tree_from_git(root: Path, tree: Path, files: Sequence[Mapping[str, Any]]) -> None:
    blobs = _read_git_blobs(root, [str(row["git_blob_oid"]) for row in files])
    tree.mkdir(mode=0o700)
    for row in files:
        content = blobs[str(row["git_blob_oid"])]
        if (
            len(content) != row["size"]
            or hashlib.sha256(content).hexdigest() != row["sha256"]
        ):
            raise PublicExportError(f"reviewed Git blob drifted: {row['path']}")
        destination = tree.joinpath(*PurePosixPath(str(row["path"])).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(content)
        os.chmod(destination, 0o755 if row["mode"] == "100755" else 0o644)
    for directory, directories, _ in os.walk(tree, topdown=False):
        os.chmod(directory, 0o755)
        for name in directories:
            os.chmod(Path(directory) / name, 0o755)


def _validated_manifest_files(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or not value or len(value) > MAX_SOURCE_FILES:
        raise PublicExportError("export manifest file inventory is invalid")
    files: list[dict[str, Any]] = []
    collision_keys: dict[str, str] = {}
    expected_keys = {
        "content_kind",
        "git_blob_oid",
        "mode",
        "path",
        "sha256",
        "size",
    }
    for raw_row in value:
        if type(raw_row) is not dict or set(raw_row) != expected_keys:
            raise PublicExportError("export manifest file row fields are not exact")
        row = dict(raw_row)
        if (
            type(row["path"]) is not str
            or row["mode"] not in {"100644", "100755"}
            or row["content_kind"] not in {"utf-8-text", "reviewed-binary"}
            or type(row["size"]) is not int
            or row["size"] < 0
            or row["size"] > MAX_SOURCE_BYTES
            or type(row["sha256"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None
            or type(row["git_blob_oid"]) is not str
            or OBJECT_ID_RE.fullmatch(row["git_blob_oid"]) is None
        ):
            raise PublicExportError("export manifest file row value is invalid")
        parts = _validate_public_path(row["path"])
        _validate_sensitive_path(row["path"], parts)
        if PurePosixPath(row["path"]).suffix.lower() in DISALLOWED_SUFFIXES:
            raise PublicExportError(
                f"export manifest contains a disallowed suffix: {row['path']}"
            )
        collision_key = unicodedata.normalize("NFC", row["path"]).casefold()
        previous = collision_keys.get(collision_key)
        if previous is not None:
            raise PublicExportError(
                f"export manifest paths collide: {previous!r} and {row['path']!r}"
            )
        collision_keys[collision_key] = row["path"]
        files.append(row)
    expected_order = sorted(files, key=lambda row: row["path"].encode("utf-8"))
    if files != expected_order:
        raise PublicExportError("export manifest file inventory is not canonical")
    return files


def _blob_oid(content: bytes, hexadecimal_length: int) -> str:
    algorithm = hashlib.sha1 if hexadecimal_length == 40 else hashlib.sha256
    digest = algorithm()
    digest.update(f"blob {len(content)}\0".encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def _verify_materialized_tree(
    tree: Path,
    files: Sequence[Mapping[str, Any]],
) -> int:
    if tree.is_symlink() or not tree.is_dir():
        raise PublicExportError("materialized tree is missing or indirect")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for directory, directories, filenames in os.walk(tree, followlinks=False):
        directory_path = Path(directory)
        for name in directories:
            child = directory_path / name
            if child.is_symlink():
                raise PublicExportError(f"materialized tree contains a symlink: {child}")
            actual_directories.add(child.relative_to(tree).as_posix())
        for name in filenames:
            child = directory_path / name
            relative = child.relative_to(tree).as_posix()
            file_stat = child.lstat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise PublicExportError(
                    f"materialized tree contains a non-regular file: {relative}"
                )
            actual_files.add(relative)

    expected_files = {str(row["path"]) for row in files}
    expected_directories = {
        parent.as_posix()
        for row in files
        for parent in PurePosixPath(str(row["path"])).parents
        if parent.as_posix() != "."
    }
    if actual_files != expected_files or actual_directories != expected_directories:
        raise PublicExportError("materialized tree inventory does not match its manifest")

    total_bytes = 0
    for row in files:
        relative = str(row["path"])
        path = tree.joinpath(*PurePosixPath(relative).parts)
        content = path.read_bytes()
        expected_permissions = 0o755 if row["mode"] == "100755" else 0o644
        if stat.S_IMODE(path.stat().st_mode) != expected_permissions:
            raise PublicExportError(f"materialized mode drifted: {relative}")
        content_kind = _validate_content(relative, content)
        if (
            len(content) != row["size"]
            or hashlib.sha256(content).hexdigest() != row["sha256"]
            or _blob_oid(content, len(str(row["git_blob_oid"])))
            != row["git_blob_oid"]
            or content_kind != row["content_kind"]
        ):
            raise PublicExportError(f"materialized content drifted: {relative}")
        total_bytes += len(content)
    return total_bytes


def verify_export(output_value: Path) -> dict[str, Any]:
    if output_value.is_symlink():
        raise PublicExportError("export output must not be a symlink")
    output = output_value.resolve()
    if not output.is_dir():
        raise PublicExportError(f"export output is not a directory: {output}")
    root_entries = {path.name for path in output.iterdir()}
    if root_entries != {"export-manifest.json", "tree"}:
        raise PublicExportError("export output root inventory is not exact")
    manifest_path = output / "export-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PublicExportError("export manifest is missing or indirect")
    raw_manifest = manifest_path.read_bytes()
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise PublicExportError("export manifest exceeds size bound")
    manifest = _strict_json_bytes(raw_manifest, label="export manifest")
    if raw_manifest != _canonical_pretty_json(manifest):
        raise PublicExportError("export manifest is not canonical LF JSON")
    expected_manifest_keys = {
        "blockers",
        "destination",
        "eligible",
        "existing_branches_included",
        "existing_tags_included",
        "file_count",
        "files",
        "format",
        "git_tree_oid",
        "history_strategy",
        "hosting_claims",
        "index_state_clean",
        "reviewed_revision_is_head",
        "schema_version",
        "secret_scan",
        "source_clean",
        "source_head_revision",
        "source_revision",
        "total_bytes",
        "tree_directory",
        "tree_sha256",
    }
    if set(manifest) != expected_manifest_keys:
        raise PublicExportError("export manifest fields are not exact")
    if (
        manifest["format"] != "workbench-public-export-manifest-v1"
        or manifest["schema_version"] != 1
        or manifest["history_strategy"] != "clean-root-export"
        or manifest["tree_directory"] != "tree"
        or manifest["eligible"] is not True
        or manifest["blockers"] != []
        or manifest["source_clean"] is not True
        or manifest["index_state_clean"] is not True
        or manifest["reviewed_revision_is_head"] is not True
        or manifest["source_revision"] != manifest["source_head_revision"]
        or not isinstance(manifest["hosting_claims"], dict)
        or manifest["existing_branches_included"] is not False
        or manifest["existing_tags_included"] is not False
        or OBJECT_ID_RE.fullmatch(str(manifest["source_revision"])) is None
        or OBJECT_ID_RE.fullmatch(str(manifest["git_tree_oid"])) is None
    ):
        raise PublicExportError("export manifest release invariants are invalid")
    secret_scan = manifest["secret_scan"]
    secret_scan_keys = {
        "allowlist_reviewed",
        "finding_count",
        "git_tree_oid",
        "receipt_sha256",
        "required",
        "result",
        "scan_scope",
        "scanner",
        "source_revision",
        "tree_sha256",
        "verified",
    }
    scanner = secret_scan.get("scanner") if type(secret_scan) is dict else None
    scanner_version = scanner.get("version") if type(scanner) is dict else None
    version_match = (
        SEMVER_RE.fullmatch(scanner_version)
        if type(scanner_version) is str
        else None
    )
    if (
        type(secret_scan) is not dict
        or set(secret_scan) != secret_scan_keys
        or secret_scan.get("required") is not True
        or secret_scan.get("verified") is not True
        or secret_scan.get("result") != "pass"
        or secret_scan.get("finding_count") != 0
        or secret_scan.get("allowlist_reviewed") is not True
        or secret_scan.get("source_revision") != manifest["source_revision"]
        or secret_scan.get("git_tree_oid") != manifest["git_tree_oid"]
        or secret_scan.get("tree_sha256") != manifest["tree_sha256"]
        or secret_scan.get("scan_scope") != "exact-public-git-tree"
        or type(scanner) is not dict
        or set(scanner) != {"name", "version"}
        or scanner.get("name") != "gitleaks"
        or version_match is None
        or tuple(int(number) for number in version_match.groups())
        < MIN_GITLEAKS_VERSION
        or re.fullmatch(r"[0-9a-f]{64}", str(secret_scan.get("receipt_sha256")))
        is None
    ):
        raise PublicExportError("export manifest secret-scan binding is invalid")

    files = _validated_manifest_files(manifest["files"])
    total_bytes = _verify_materialized_tree(output / "tree", files)
    if (
        manifest["file_count"] != len(files)
        or manifest["total_bytes"] != total_bytes
        or total_bytes > MAX_TOTAL_SOURCE_BYTES
        or manifest["tree_sha256"] != _tree_digest(files)
    ):
        raise PublicExportError("export manifest aggregate identity is invalid")
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"files", "hosting_claims"}
    }


def stage_secret_scan_input(
    output_value: Path,
    revision: str,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Materialize exact reviewed Git bytes for a local secret scan only."""

    root = root.resolve()
    plan = public_export_plan(revision, root=root)
    blockers = [
        blocker
        for blocker in plan["blockers"]
        if blocker != "secret-scan-receipt-required"
    ]
    if blockers:
        raise PublicExportError(
            "secret-scan input is ineligible: " + ", ".join(blockers)
        )
    output = _safe_output(root, output_value)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    os.chmod(temporary, 0o700)
    published = False
    try:
        tree = temporary / "tree"
        _write_tree_from_git(root, tree, plan["files"])
        total_bytes = _verify_materialized_tree(tree, plan["files"])
        if (
            total_bytes != plan["total_bytes"]
            or _tree_digest(plan["files"]) != plan["tree_sha256"]
        ):
            raise PublicExportError("secret-scan input aggregate identity drifted")
        manifest = {
            "eligible_for_public_import": False,
            "file_count": plan["file_count"],
            "files": plan["files"],
            "format": "workbench-public-secret-scan-input-v1",
            "git_tree_oid": plan["git_tree_oid"],
            "purpose": "local-secret-scan-input-only",
            "schema_version": 1,
            "source_revision": plan["source_revision"],
            "total_bytes": plan["total_bytes"],
            "tree_directory": "tree",
            "tree_sha256": plan["tree_sha256"],
        }
        manifest_path = temporary / "scan-input-manifest.json"
        manifest_path.write_bytes(_canonical_pretty_json(manifest))
        os.chmod(manifest_path, 0o644)
        if {path.name for path in temporary.iterdir()} != {
            "scan-input-manifest.json",
            "tree",
        }:
            raise PublicExportError("secret-scan input root inventory drifted")
        if os.path.lexists(output):
            raise PublicExportError(
                f"secret-scan input appeared during build: {output}"
            )
        os.rename(temporary, output)
        published = True
        return {key: value for key, value in manifest.items() if key != "files"}
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)


def build_export(
    output_value: Path,
    revision: str,
    secret_scan_receipt: Path,
    *,
    root: Path = ROOT,
) -> dict[str, Any]:
    root = root.resolve()
    plan = public_export_plan(revision, secret_scan_receipt, root=root)
    if not plan["eligible"]:
        raise PublicExportError(
            "public export is ineligible: " + ", ".join(plan["blockers"])
        )
    output = _safe_output(root, output_value)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    os.chmod(temporary, 0o700)
    published = False
    try:
        _write_tree_from_git(root, temporary / "tree", plan["files"])
        manifest = dict(plan)
        manifest["format"] = "workbench-public-export-manifest-v1"
        manifest["tree_directory"] = "tree"
        manifest_path = temporary / "export-manifest.json"
        manifest_path.write_bytes(_canonical_pretty_json(manifest))
        os.chmod(manifest_path, 0o644)
        verify_export(temporary)
        if os.path.lexists(output):
            raise PublicExportError(f"export output appeared during build: {output}")
        os.rename(temporary, output)
        published = True
        return verify_export(output)
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)


def _write_json(value: object) -> None:
    sys.stdout.buffer.write(_canonical_pretty_json(value))


def _plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "files"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--revision", required=True)
    plan_parser.add_argument("--secret-scan-receipt", type=Path)
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.add_argument("--require-ready", action="store_true")
    scan_parser = subparsers.add_parser("stage-scan")
    scan_parser.add_argument("--revision", required=True)
    scan_parser.add_argument("--output", type=Path, required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--revision", required=True)
    build_parser.add_argument("--secret-scan-receipt", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "plan":
            plan = public_export_plan(args.revision, args.secret_scan_receipt)
            if args.json:
                _write_json(_plan_summary(plan))
            else:
                print(
                    f"public export plan: {plan['file_count']} files, "
                    f"{plan['total_bytes']} bytes, clean={str(plan['source_clean']).lower()}, "
                    f"eligible={str(plan['eligible']).lower()}, "
                    f"tree sha256:{plan['tree_sha256']}"
                )
            if args.require_ready and not plan["eligible"]:
                return 1
        elif args.command == "stage-scan":
            _write_json(stage_secret_scan_input(args.output, args.revision))
        elif args.command == "build":
            _write_json(
                build_export(
                    args.output,
                    args.revision,
                    args.secret_scan_receipt,
                )
            )
        else:
            _write_json(verify_export(args.output))
        return 0
    except (OSError, PublicExportError, PublicRepositoryError, ValueError) as exc:
        print(f"public export failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
