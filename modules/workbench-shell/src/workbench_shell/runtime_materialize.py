"""Materialize a local Packwiz workspace into a Cleanroom client fixture."""

from __future__ import annotations

from workbench_project_intelligence.working_tree import copy_tracked_workspace

from hashlib import sha256
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import url2pathname
from zipfile import BadZipFile, ZipFile

from workbench_project_intelligence.git_observation import (
    GitObservationError,
    require_configured_git_executable,
)

from workbench_core.artifact_store import (
    ArtifactStoreError,
    DOWNLOAD_CHUNK_BYTES,
    fetch_verified_artifact,
    sha256_file,
)
from workbench_core.configuration import (
    CONFIGURATION_PATH,
    ResolvedBindings,
    WorkbenchConfiguration,
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from .runtime_bootstrap import (
    RuntimeBootstrapError,
    bootstrap_project_runtime,
)
from workbench_core.runtime_java import JavaRuntimeError, ensure_java_runtime
from .runtime_plan import RuntimePlanError, plan_project_runtime
from workbench_api.state_paths import default_suite_state_root


RECEIPT_V2_PATH = Path("receipts/packwiz-materialization-v2.json")
BOOTSTRAP_RECEIPT_PATH = Path(
    "receipts/cleanroom-client-bootstrap-v1.json"
)
INSTALLER_MAIN_CLASS = "link.infra.packwiz.installer.Main"
MAX_INDEX_ENTRIES = 100_000
MAX_LOG_BYTES = 16 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
PACKWIZ_V2_POLICY = "pack-declared-defaults"
PACKWIZ_V2_POLICY_VERSION = 1


class PackwizMaterializationError(ValueError):
    """Raised when a Packwiz payload cannot be materialized safely."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _file_uri_path(uri: Any, label: str) -> Path:
    if not isinstance(uri, str):
        raise PackwizMaterializationError(
            f"{label} must be a local file URI"
        )
    parsed = urlparse(uri)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise PackwizMaterializationError(
            f"{label} must be a local file URI"
        )
    path_text = url2pathname(parsed.path)
    if (
        os.name == "nt"
        and len(path_text) >= 3
        and path_text[0] == "/"
        and path_text[2] == ":"
    ):
        path_text = path_text[1:]
    return Path(path_text)


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PackwizMaterializationError(
            f"{label} must be a portable relative path"
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or ":" in path.parts[0]
    ):
        raise PackwizMaterializationError(
            f"{label} must be a portable relative path"
        )
    return path


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise PackwizMaterializationError(
            f"{label} cannot be a symbolic link"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackwizMaterializationError(
            f"{label} is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise PackwizMaterializationError(
            f"{label} must be a JSON object"
        )
    return value


def _load_toml(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink():
        raise PackwizMaterializationError(
            f"{label} cannot be a symbolic link"
        )
    try:
        payload = path.read_bytes()
        value = tomllib.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PackwizMaterializationError(
            f"{label} is not valid TOML"
        ) from exc
    if not isinstance(value, dict):
        raise PackwizMaterializationError(
            f"{label} must be a TOML table"
        )
    return value, payload


def _git_bytes(
    workspace: Path,
    arguments: Sequence[str],
) -> bytes:
    try:
        git = require_configured_git_executable()
    except GitObservationError as exc:
        raise PackwizMaterializationError(
            f"cannot inspect Git workspace with {' '.join(arguments)}: {exc}"
        ) from exc
    try:
        completed = subprocess.run(
            [git, "-C", str(workspace), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackwizMaterializationError(
            f"cannot inspect Git workspace with {' '.join(arguments)}"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.decode(
            "utf-8",
            "replace",
        ).strip()
        raise PackwizMaterializationError(
            f"Git {' '.join(arguments)} failed: "
            f"{detail or 'unknown Git failure'}"
        )
    return completed.stdout


def _git_revision(workspace: Path) -> str:
    revision = _git_bytes(
        workspace,
        ("rev-parse", "HEAD"),
    ).decode("ascii", "strict").strip()
    if GIT_REVISION_RE.fullmatch(revision) is None:
        raise PackwizMaterializationError(
            "Git returned an invalid workspace revision"
        )
    return revision


def _tree_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tree_sha256": "sha256:" + sha256(
            _canonical_bytes(entries)
        ).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
    }




def _tree_identity(
    root: Path,
) -> tuple[dict[str, Any], dict[str, tuple[str, int]]]:
    if not root.is_dir() or root.is_symlink():
        raise PackwizMaterializationError(
            "materialized tree is not a regular directory"
        )
    entries: list[dict[str, Any]] = []
    hashes: dict[str, tuple[str, int]] = {}
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PackwizMaterializationError(
                f"materialized tree contains a symbolic link: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackwizMaterializationError(
                f"materialized tree contains a special file: {relative}"
            )
        digest, size = sha256_file(path)
        mode = stat.S_IMODE(path.stat().st_mode)
        entries.append({
            "mode": mode,
            "path": relative,
            "sha256": digest,
            "size": size,
        })
        hashes[relative] = (digest, size)
    return _tree_summary(entries), hashes


def _validate_source_binding(
    plan: Mapping[str, Any],
    staged_root: Path,
) -> None:
    project = plan.get("project")
    if not isinstance(project, dict):
        raise PackwizMaterializationError(
            "runtime plan lacks project context"
        )
    pack_path = staged_root / "pack.toml"
    pack_digest, _pack_size = sha256_file(pack_path)
    if pack_digest != project.get("manifest_sha256"):
        raise PackwizMaterializationError(
            "staged pack.toml changed after runtime planning"
        )
    index = project.get("index")
    if not isinstance(index, dict):
        raise PackwizMaterializationError(
            "runtime plan lacks Packwiz index context"
        )
    index_relative = _safe_relative(
        index.get("file"),
        "planned Packwiz index path",
    )
    index_digest, _index_size = sha256_file(
        staged_root.joinpath(*index_relative.parts)
    )
    if index_digest != index.get("actual_sha256"):
        raise PackwizMaterializationError(
            "staged Packwiz index changed after runtime planning"
        )


def _validate_refreshed_pack(
    plan: Mapping[str, Any],
    staged_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tree, file_hashes = _tree_identity(staged_root)
    pack, pack_bytes = _load_toml(
        staged_root / "pack.toml",
        "refreshed Packwiz manifest",
    )
    project = plan["project"]
    versions = pack.get("versions")
    if (
        pack.get("name") != project.get("name")
        or pack.get("version") != project.get("version")
        or not isinstance(versions, dict)
        or versions.get("minecraft") != project.get("minecraft_version")
    ):
        raise PackwizMaterializationError(
            "Packwiz refresh changed the planned pack identity"
        )
    index_binding = pack.get("index")
    if not isinstance(index_binding, dict):
        raise PackwizMaterializationError(
            "refreshed Packwiz manifest lacks an index"
        )
    index_relative = _safe_relative(
        index_binding.get("file"),
        "refreshed Packwiz index path",
    )
    if index_binding.get("hash-format") != "sha256":
        raise PackwizMaterializationError(
            "refreshed Packwiz index must use SHA-256"
        )
    declared_hash = index_binding.get("hash")
    if not isinstance(declared_hash, str) or (
        SHA256_RE.fullmatch(declared_hash) is None
    ):
        raise PackwizMaterializationError(
            "refreshed Packwiz manifest has an invalid index SHA-256"
        )
    index_path = staged_root.joinpath(*index_relative.parts)
    index_digest, index_size = sha256_file(index_path)
    if index_digest != declared_hash:
        raise PackwizMaterializationError(
            "Packwiz refresh produced an index hash mismatch"
        )
    index, _index_bytes = _load_toml(
        index_path,
        "refreshed Packwiz index",
    )
    if index.get("hash-format") != "sha256":
        raise PackwizMaterializationError(
            "refreshed Packwiz file entries must use SHA-256"
        )
    files = index.get("files")
    if not isinstance(files, list):
        raise PackwizMaterializationError(
            "refreshed Packwiz index lacks file entries"
        )
    if not files or len(files) > MAX_INDEX_ENTRIES:
        raise PackwizMaterializationError(
            "refreshed Packwiz index has an invalid entry count"
        )
    seen: set[str] = set()
    metafile_count = 0
    preserved_count = 0
    for position, record in enumerate(files):
        if not isinstance(record, dict):
            raise PackwizMaterializationError(
                f"Packwiz index entry {position} must be a table"
            )
        relative = _safe_relative(
            record.get("file"),
            f"Packwiz index entry {position} path",
        )
        key = relative.as_posix().casefold()
        if key in seen:
            raise PackwizMaterializationError(
                f"duplicate Packwiz index path: {relative.as_posix()}"
            )
        seen.add(key)
        expected_hash = record.get("hash")
        if not isinstance(expected_hash, str) or (
            SHA256_RE.fullmatch(expected_hash) is None
        ):
            raise PackwizMaterializationError(
                f"Packwiz index entry has an invalid SHA-256: "
                f"{relative.as_posix()}"
            )
        observed = file_hashes.get(relative.as_posix())
        if observed is None or observed[0] != expected_hash:
            raise PackwizMaterializationError(
                f"Packwiz index entry does not match staged bytes: "
                f"{relative.as_posix()}"
            )
        metafile_count += int(record.get("metafile") is True)
        preserved_count += int(record.get("preserve") is True)

    pack_identity = {
        "manifest_sha256": sha256(pack_bytes).hexdigest(),
        "manifest_size": len(pack_bytes),
        "index": {
            "file": index_relative.as_posix(),
            "hash_format": "sha256",
            "declared_hash": declared_hash,
            "actual_sha256": index_digest,
            "size": index_size,
            "entry_count": len(files),
            "metafile_count": metafile_count,
            "direct_file_count": len(files) - metafile_count,
            "preserved_entry_count": preserved_count,
        },
    }
    return pack_identity, tree


def _seed_payload(
    staged_root: Path,
    payload_root: Path,
    seed_roots: Sequence[Path | str],
    *,
    excluded_outputs: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    resolved_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for raw_root in seed_roots:
        lexical_root = Path(raw_root).expanduser()
        try:
            root_info = lexical_root.lstat()
        except OSError as exc:
            raise PackwizMaterializationError(
                f"Packwiz seed root cannot be inspected: {lexical_root}"
            ) from exc
        root = lexical_root.resolve()
        if (
            root in seen_roots
            or stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
        ):
            if root in seen_roots:
                continue
            raise PackwizMaterializationError(
                f"Packwiz seed root is not a regular directory: {root}"
            )
        seen_roots.add(root)
        resolved_roots.append(root)

    pack, _pack_bytes = _load_toml(
        staged_root / "pack.toml",
        "refreshed Packwiz manifest",
    )
    index_binding = pack.get("index")
    if not isinstance(index_binding, dict):
        raise PackwizMaterializationError(
            "refreshed Packwiz manifest lacks an index"
        )
    index_relative = _safe_relative(
        index_binding.get("file"),
        "refreshed Packwiz index path",
    )
    index, _index_bytes = _load_toml(
        staged_root.joinpath(*index_relative.parts),
        "refreshed Packwiz index",
    )
    files = index.get("files")
    if not isinstance(files, list):
        raise PackwizMaterializationError(
            "refreshed Packwiz index lacks file entries"
        )

    seeded: list[dict[str, Any]] = []
    output_paths: set[str] = set()
    for position, record in enumerate(files):
        if (
            not isinstance(record, dict)
            or record.get("metafile") is not True
        ):
            continue
        metadata_relative = _safe_relative(
            record.get("file"),
            f"Packwiz metafile entry {position} path",
        )
        metadata, _metadata_bytes = _load_toml(
            staged_root.joinpath(*metadata_relative.parts),
            f"Packwiz metafile {metadata_relative.as_posix()}",
        )
        side = metadata.get("side", "both")
        if side == "server":
            continue
        if side not in {"both", "client"}:
            raise PackwizMaterializationError(
                f"Packwiz metafile has an invalid side: "
                f"{metadata_relative.as_posix()}"
            )
        filename = metadata.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
        ):
            raise PackwizMaterializationError(
                f"Packwiz metafile has an unsafe filename: "
                f"{metadata_relative.as_posix()}"
            )
        output_relative = metadata_relative.parent / filename
        if output_relative.as_posix() in excluded_outputs:
            continue
        output_key = output_relative.as_posix().casefold()
        if output_key in output_paths:
            raise PackwizMaterializationError(
                f"Packwiz metafiles collide at output path: "
                f"{output_relative.as_posix()}"
            )
        output_paths.add(output_key)
        download = metadata.get("download")
        if not isinstance(download, dict):
            raise PackwizMaterializationError(
                f"Packwiz metafile lacks download identity: "
                f"{metadata_relative.as_posix()}"
            )
        hash_format = download.get("hash-format")
        expected_hash = download.get("hash")
        if (
            not isinstance(hash_format, str)
            or not isinstance(expected_hash, str)
            or not expected_hash
        ):
            raise PackwizMaterializationError(
                f"Packwiz metafile lacks a usable content hash: "
                f"{metadata_relative.as_posix()}"
            )
        try:
            hashlib.new(hash_format)
        except ValueError:
            continue

        selected: tuple[Path, str, int] | None = None
        for root in resolved_roots:
            candidate = root.joinpath(*output_relative.parts)
            if (
                not candidate.is_file()
                or candidate.is_symlink()
                or not candidate.resolve().is_relative_to(root)
            ):
                continue
            digest = hashlib.new(hash_format)
            size = 0
            try:
                with candidate.open("rb") as source:
                    while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
                        digest.update(chunk)
                        size += len(chunk)
            except OSError as exc:
                raise PackwizMaterializationError(
                    f"cannot read Packwiz seed candidate: {candidate}"
                ) from exc
            if digest.hexdigest().lower() == expected_hash.lower():
                selected = candidate, sha256_file(candidate)[0], size
                break
        if selected is None:
            continue
        source, sha256_digest, size = selected
        destination = payload_root.joinpath(*output_relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with source.open("rb") as input_stream:
                with destination.open("xb") as output_stream:
                    shutil.copyfileobj(
                        input_stream,
                        output_stream,
                        DOWNLOAD_CHUNK_BYTES,
                    )
            destination.chmod(0o644)
        except OSError as exc:
            raise PackwizMaterializationError(
                f"cannot copy Packwiz seed artifact: "
                f"{output_relative.as_posix()}"
            ) from exc
        seeded.append({
            "content_hash": expected_hash.lower(),
            "hash_format": hash_format,
            "output_path": output_relative.as_posix(),
            "sha256": sha256_digest,
            "size": size,
            "source_uri": source.as_uri(),
        })

    seeded.sort(key=lambda entry: entry["output_path"])
    return {
        "roots": [root.as_uri() for root in resolved_roots],
        "file_count": len(seeded),
        "total_bytes": sum(entry["size"] for entry in seeded),
        "entries_sha256": "sha256:" + sha256(
            _canonical_bytes(seeded)
        ).hexdigest(),
        "entries": seeded,
    }


def _packwiz_optional_decisions(
    staged_root: Path,
    *,
    side: str = "client",
) -> list[dict[str, Any]]:
    """Project Packwiz's declared target-side defaults without overrides."""

    if side not in {"client", "server"}:
        raise PackwizMaterializationError(
            "Packwiz optional target side must be client or server"
        )
    target_side = side

    pack, _pack_bytes = _load_toml(
        staged_root / "pack.toml",
        "refreshed Packwiz manifest",
    )
    index_binding = pack.get("index")
    if not isinstance(index_binding, dict):
        raise PackwizMaterializationError(
            "refreshed Packwiz manifest lacks an index"
        )
    index_relative = _safe_relative(
        index_binding.get("file"),
        "refreshed Packwiz index path",
    )
    index, _index_bytes = _load_toml(
        staged_root.joinpath(*index_relative.parts),
        "refreshed Packwiz index",
    )
    files = index.get("files")
    if not isinstance(files, list):
        raise PackwizMaterializationError(
            "refreshed Packwiz index lacks file entries"
        )

    decisions: list[dict[str, Any]] = []
    outputs: set[str] = set()
    for position, record in enumerate(files):
        if not isinstance(record, dict) or record.get("metafile") is not True:
            continue
        metadata_relative = _safe_relative(
            record.get("file"),
            f"Packwiz metafile entry {position} path",
        )
        metadata_path = staged_root.joinpath(*metadata_relative.parts)
        metadata, metadata_bytes = _load_toml(
            metadata_path,
            f"Packwiz metafile {metadata_relative.as_posix()}",
        )
        entry_side = metadata.get("side", "both")
        if entry_side not in {"both", "client", "server"}:
            raise PackwizMaterializationError(
                "Packwiz metafile has an invalid side: "
                + metadata_relative.as_posix()
            )
        if entry_side not in {"both", target_side}:
            continue
        option = metadata.get("option")
        if not isinstance(option, dict) or option.get("optional") is not True:
            continue
        declared_default = option.get("default", False)
        if type(declared_default) is not bool:
            raise PackwizMaterializationError(
                "Packwiz optional default must be boolean: "
                + metadata_relative.as_posix()
            )
        filename = metadata.get("filename")
        name = metadata.get("name")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(filename, str)
            or not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
        ):
            raise PackwizMaterializationError(
                "Packwiz optional metafile has incomplete identity: "
                + metadata_relative.as_posix()
            )
        output_relative = metadata_relative.parent / filename
        output_key = output_relative.as_posix().casefold()
        if output_key in outputs:
            raise PackwizMaterializationError(
                "Packwiz optional outputs collide: "
                + output_relative.as_posix()
            )
        outputs.add(output_key)
        decisions.append({
            "metadata_path": metadata_relative.as_posix(),
            "metafile_sha256": sha256(metadata_bytes).hexdigest(),
            "output_path": output_relative.as_posix(),
            "name": name,
            "side": entry_side,
            "declared_default": declared_default,
            "applied": declared_default,
        })
    decisions.sort(key=lambda row: row["metadata_path"])
    return decisions


def _packwiz_decisions_sha256(
    decisions: Sequence[Mapping[str, Any]],
) -> str:
    return "sha256:" + sha256(_canonical_bytes(list(decisions))).hexdigest()


def _packwiz_initial_state_bytes(
    decisions: Sequence[Mapping[str, Any]],
    *,
    side: str = "client",
) -> bytes:
    if side not in {"client", "server"}:
        raise PackwizMaterializationError(
            "Packwiz installer-state side must be client or server"
        )
    state = {
        "cachedFiles": {
            str(row["metadata_path"]): {
                "isOptional": True,
                "optionValue": bool(row["declared_default"]),
            }
            for row in decisions
        },
        "cachedSide": side,
    }
    return _canonical_bytes(state)


def _write_packwiz_initial_state(
    payload_root: Path,
    decisions: Sequence[Mapping[str, Any]],
    *,
    side: str = "client",
) -> dict[str, Any]:
    payload = _packwiz_initial_state_bytes(decisions, side=side)
    path = payload_root / "packwiz.json"
    try:
        path.write_bytes(payload)
        path.chmod(0o644)
    except OSError as exc:
        raise PackwizMaterializationError(
            "cannot initialize Packwiz optional state"
        ) from exc
    return {"sha256": sha256(payload).hexdigest(), "size": len(payload)}


def _verify_packwiz_final_state(
    payload_root: Path,
    decisions: Sequence[Mapping[str, Any]],
    refreshed_pack: Mapping[str, Any],
    *,
    side: str = "client",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state_path = payload_root / "packwiz.json"
    state = _load_json(state_path, "Packwiz Installer final state")
    if side not in {"client", "server"}:
        raise PackwizMaterializationError(
            "Packwiz final-state side must be client or server"
        )
    if state.get("cachedSide") != side:
        raise PackwizMaterializationError(
            "Packwiz Installer final state does not bind the selected side"
        )
    expected_pack_hash = {
        "type": "sha256",
        "value": refreshed_pack.get("manifest_sha256"),
    }
    refreshed_index = refreshed_pack.get("index")
    expected_index_hash = {
        "type": (
            refreshed_index.get("hash_format")
            if isinstance(refreshed_index, dict)
            else None
        ),
        "value": (
            refreshed_index.get("declared_hash")
            if isinstance(refreshed_index, dict)
            else None
        ),
    }
    if (
        state.get("packFileHash") != expected_pack_hash
        or state.get("indexFileHash") != expected_index_hash
    ):
        raise PackwizMaterializationError(
            "Packwiz Installer final state does not bind the refreshed pack"
        )
    cached_files = state.get("cachedFiles")
    if not isinstance(cached_files, dict):
        raise PackwizMaterializationError(
            "Packwiz Installer final state lacks cached files"
        )
    final_rows: list[dict[str, Any]] = []
    for decision in decisions:
        metadata_path = str(decision["metadata_path"])
        cached = cached_files.get(metadata_path)
        expected = bool(decision["declared_default"])
        if (
            not isinstance(cached, dict)
            or cached.get("isOptional") is not True
            or cached.get("optionValue") is not expected
        ):
            raise PackwizMaterializationError(
                "Packwiz Installer changed the declared option default: "
                + metadata_path
            )
        cached_location = cached.get("cachedLocation")
        if (
            (expected and cached_location != decision["output_path"])
            or (not expected and cached_location is not None)
        ):
            raise PackwizMaterializationError(
                "Packwiz Installer final state has an invalid optional "
                "location: " + metadata_path
            )
        output = payload_root.joinpath(
            *_safe_relative(
                decision["output_path"],
                "Packwiz optional output path",
            ).parts
        )
        present = output.exists() or output.is_symlink()
        output_sha256: str | None = None
        output_size: int | None = None
        if expected:
            if not output.is_file() or output.is_symlink():
                raise PackwizMaterializationError(
                    "enabled Packwiz optional output is missing: "
                    + str(decision["output_path"])
                )
            output_sha256, output_size = sha256_file(output)
        elif present:
            raise PackwizMaterializationError(
                "disabled Packwiz optional output is present: "
                + str(decision["output_path"])
            )
        final_rows.append({
            **dict(decision),
            "present": present,
            "output_sha256": output_sha256,
            "output_size": output_size,
        })
    digest, size = sha256_file(state_path)
    return {"sha256": digest, "size": size}, final_rows


def _launcher_tree_identity(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        relative_path = path.relative_to(root)
        if relative_path.parts and relative_path.parts[0] == ".minecraft":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            raise PackwizMaterializationError(
                "Cleanroom launcher base contains a symbolic link: " + relative
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackwizMaterializationError(
                "Cleanroom launcher base contains a special file: " + relative
            )
        digest, size = sha256_file(path)
        entries.append({
            "mode": stat.S_IMODE(path.stat().st_mode),
            "path": relative,
            "sha256": digest,
            "size": size,
        })
    return _tree_summary(entries)


def _copy_launcher_base(source: Path, destination: Path) -> dict[str, Any]:
    expected = _launcher_tree_identity(source)
    destination.mkdir(parents=True)
    for path in sorted(
        source.rglob("*"),
        key=lambda item: item.relative_to(source).as_posix(),
    ):
        relative = path.relative_to(source)
        if relative.parts and relative.parts[0] == ".minecraft":
            continue
        target = destination / relative
        if path.is_symlink():
            raise PackwizMaterializationError(
                "Cleanroom launcher base contains a symbolic link: "
                + relative.as_posix()
            )
        if path.is_dir():
            target.mkdir(exist_ok=True)
            continue
        if not path.is_file():
            raise PackwizMaterializationError(
                "Cleanroom launcher base contains a special file: "
                + relative.as_posix()
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("rb") as source_stream:
                with target.open("xb") as target_stream:
                    shutil.copyfileobj(
                        source_stream,
                        target_stream,
                        DOWNLOAD_CHUNK_BYTES,
                    )
            target.chmod(stat.S_IMODE(path.stat().st_mode))
        except OSError as exc:
            raise PackwizMaterializationError(
                "cannot copy the Cleanroom launcher base"
            ) from exc
    observed = _launcher_tree_identity(destination)
    if observed != expected:
        raise PackwizMaterializationError(
            "Cleanroom launcher base changed while copying"
        )
    return observed


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":  # pragma: no cover - host dependent
        try:
            source.rename(destination)
            return
        except OSError as exc:
            raise PackwizMaterializationError(
                "Packwiz V2 target appeared during publication"
            ) from exc
    if os.name != "posix":  # pragma: no cover - host dependent
        raise PackwizMaterializationError(
            "atomic Packwiz V2 publication is unavailable on this host"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:  # pragma: no cover - platform dependent
        raise PackwizMaterializationError(
            "atomic Packwiz V2 publication requires renameat2"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    ) != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise PackwizMaterializationError(
                "Packwiz V2 target appeared during publication"
            )
        if error_number in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
            mover = shutil.which("mv")
            if mover is None:  # pragma: no cover - minimal host image
                raise PackwizMaterializationError(
                    "atomic Packwiz V2 publication is unavailable on this "
                    "filesystem"
                )
            try:
                completed = subprocess.run(
                    [
                        mover,
                        "-T",
                        "--no-clobber",
                        "--",
                        str(source),
                        str(destination),
                    ],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=30.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise PackwizMaterializationError(
                    "cannot execute no-replace Packwiz V2 publication"
                ) from exc
            if source.exists() or source.is_symlink():
                raise PackwizMaterializationError(
                    "Packwiz V2 target appeared during publication"
                )
            if (
                completed.returncode
                or not destination.is_dir()
                or destination.is_symlink()
            ):
                raise PackwizMaterializationError(
                    "cannot atomically publish the Packwiz V2 target"
                )
            return
        raise PackwizMaterializationError(
            "cannot atomically publish the Packwiz V2 target: "
            + os.strerror(error_number)
        )


def _ensure_state_subdirectory(state_root: Path, *parts: str) -> Path:
    current = state_root
    for part in parts:
        current = current / part
        if current.exists() or current.is_symlink():
            try:
                info = current.lstat()
            except OSError as exc:
                raise PackwizMaterializationError(
                    "cannot inspect Packwiz V2 state directory"
                ) from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise PackwizMaterializationError(
                    "Packwiz V2 state path is not a regular directory: "
                    + str(current)
                )
        else:
            try:
                current.mkdir()
            except OSError as exc:
                raise PackwizMaterializationError(
                    "cannot create Packwiz V2 state directory"
                ) from exc
        if not current.resolve().is_relative_to(state_root):
            raise PackwizMaterializationError(
                "Packwiz V2 state directory escapes Workbench state"
            )
    return current


def _validate_plan(
    plan: Mapping[str, Any],
    *,
    workspace: Path,
    state_root: Path,
) -> tuple[Path, Path]:
    if plan.get("format") != "workbench-runtime-plan-v1":
        raise PackwizMaterializationError(
            "unsupported runtime plan format"
        )
    request = plan.get("request")
    blockers = plan.get("blockers")
    if (
        not isinstance(request, dict)
        or request.get("side") != "client"
        or request.get("launcher") not in {"prism", "multimc"}
    ):
        raise PackwizMaterializationError(
            "Packwiz materialization requires a Prism or MultiMC client plan"
        )
    if not isinstance(blockers, list):
        raise PackwizMaterializationError(
            "runtime plan lacks blockers"
        )
    blocker_ids = sorted(
        str(blocker.get("id"))
        for blocker in blockers
        if isinstance(blocker, dict)
    )
    if blocker_ids:
        raise PackwizMaterializationError(
            "Packwiz materialization is blocked: "
            + ", ".join(blocker_ids)
        )
    workspace_context = plan.get("workspace")
    if not isinstance(workspace_context, dict):
        raise PackwizMaterializationError(
            "runtime plan lacks workspace context"
        )
    planned_workspace = _file_uri_path(
        workspace_context.get("root_uri"),
        "planned workspace root",
    ).resolve()
    if planned_workspace != workspace:
        raise PackwizMaterializationError(
            "runtime plan belongs to a different workspace"
        )
    target = plan.get("target")
    if not isinstance(target, dict):
        raise PackwizMaterializationError(
            "runtime plan lacks target context"
        )
    fixture_root = _file_uri_path(
        target.get("fixture_root_uri"),
        "runtime fixture root",
    ).resolve()
    if not fixture_root.is_relative_to(state_root):
        raise PackwizMaterializationError(
            "runtime fixture root escapes Workbench state"
        )
    instance_root = fixture_root / "instance"
    bootstrap_receipt = _load_json(
        fixture_root / BOOTSTRAP_RECEIPT_PATH,
        "Cleanroom bootstrap receipt",
    )
    if (
        bootstrap_receipt.get("format")
        != "workbench-runtime-bootstrap-receipt-v1"
        or bootstrap_receipt.get("schema_version") != 1
        or bootstrap_receipt.get("state") != "materialized"
        or bootstrap_receipt.get("plan_id") != plan.get("plan_id")
    ):
        raise PackwizMaterializationError(
            "Cleanroom fixture does not match the runtime plan"
        )
    if not instance_root.is_dir() or instance_root.is_symlink():
        raise PackwizMaterializationError(
            "Cleanroom instance root is not a regular directory"
        )
    launcher_manifest = instance_root / "mmc-pack.json"
    if not launcher_manifest.is_file() or launcher_manifest.is_symlink():
        raise PackwizMaterializationError(
            "Cleanroom instance lacks a regular mmc-pack.json"
        )
    return fixture_root, instance_root


def _tool_identity(
    path: Path | str,
    label: str,
) -> tuple[Path, dict[str, Any]]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise PackwizMaterializationError(
            f"{label} is not a regular file: {resolved}"
        )
    digest, size = sha256_file(resolved)
    return resolved, {
        "sha256": digest,
        "size": size,
        "source_uri": resolved.as_uri(),
    }


def _validate_installer(
    installer_path: Path | str,
    installer_lock: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    path, observed = _tool_identity(
        installer_path,
        "Packwiz Installer",
    )
    expected = {
        "id": "packwiz_installer",
        "url": installer_lock.get("url"),
        "source_revision": installer_lock.get("source_revision"),
        "sha256": installer_lock.get("sha256"),
        "size": installer_lock.get("size"),
    }
    if (
        not isinstance(expected["url"], str)
        or not isinstance(expected["source_revision"], str)
        or GIT_REVISION_RE.fullmatch(expected["source_revision"]) is None
        or not isinstance(expected["sha256"], str)
        or SHA256_RE.fullmatch(expected["sha256"]) is None
        or type(expected["size"]) is not int
        or expected["size"] <= 0
    ):
        raise PackwizMaterializationError(
            "Packwiz Installer lock is incomplete"
        )
    if (
        observed["sha256"] != expected["sha256"]
        or observed["size"] != expected["size"]
    ):
        raise PackwizMaterializationError(
            "Packwiz Installer differs from its profile lock"
        )
    try:
        with ZipFile(path) as archive:
            if (
                "link/infra/packwiz/installer/Main.class"
                not in archive.namelist()
            ):
                raise PackwizMaterializationError(
                    "Packwiz Installer lacks its direct main class"
                )
    except BadZipFile as exc:
        raise PackwizMaterializationError(
            "Packwiz Installer is not a valid JAR"
        ) from exc
    return path, {
        **expected,
        "cache_uri": path.as_uri(),
        "entrypoint": INSTALLER_MAIN_CLASS,
    }


def _run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout_seconds: float,
    label: str,
) -> None:
    if timeout_seconds <= 0:
        raise PackwizMaterializationError(
            f"{label} timeout must be positive"
        )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() or log_path.is_symlink():
        if not log_path.is_file() or log_path.is_symlink():
            raise PackwizMaterializationError(
                f"{label} log target is not a regular file"
            )
        previous_digest, _previous_size = sha256_file(log_path)
        history = log_path.parent / "history"
        if history.exists() and (
            not history.is_dir() or history.is_symlink()
        ):
            raise PackwizMaterializationError(
                f"{label} log history is not a regular directory"
            )
        history.mkdir(exist_ok=True)
        archived = (
            history
            / f"{log_path.stem}-{previous_digest[:16]}{log_path.suffix}"
        )
        if not archived.exists():
            try:
                with log_path.open("rb") as source:
                    with archived.open("xb") as destination:
                        shutil.copyfileobj(
                            source,
                            destination,
                            DOWNLOAD_CHUNK_BYTES,
                        )
            except OSError as exc:
                raise PackwizMaterializationError(
                    f"cannot retain the previous {label} log"
                ) from exc
    try:
        with log_path.open("wb") as log:
            header = json.dumps(
                {"command": list(command), "cwd": str(cwd)},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            log.write(header + b"\n")
            log.flush()
            completed = subprocess.run(
                list(command),
                check=False,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        raise PackwizMaterializationError(
            f"{label} timed out; see {log_path.as_uri()}"
        ) from exc
    except OSError as exc:
        raise PackwizMaterializationError(
            f"cannot execute {label}; see {log_path.as_uri()}"
        ) from exc
    if log_path.stat().st_size > MAX_LOG_BYTES:
        raise PackwizMaterializationError(
            f"{label} log exceeds the retained size limit: "
            f"{log_path.as_uri()}"
        )
    if completed.returncode:
        raise PackwizMaterializationError(
            f"{label} exited with {completed.returncode}; "
            f"see {log_path.as_uri()}"
        )


def _materialization_identity(
    plan: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    pack: Mapping[str, Any],
    tools: Mapping[str, Any],
    launcher_manifest_sha256: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    installer = tools["installer"]
    packwiz = tools["packwiz"]
    java = tools["java"]
    return {
        "plan_id": plan["plan_id"],
        "source_tree_sha256": source["tree_sha256"],
        "pack_manifest_sha256": pack["manifest_sha256"],
        "pack_index_sha256": pack["index"]["actual_sha256"],
        "packwiz": {
            "sha256": packwiz["sha256"],
            "size": packwiz["size"],
        },
        "installer": {
            field: installer[field]
            for field in (
                "url",
                "source_revision",
                "sha256",
                "size",
                "entrypoint",
            )
        },
        "java": dict(java["identity"]),
        "launcher_manifest_sha256": launcher_manifest_sha256,
        "payload_tree_sha256": payload["tree_sha256"],
    }


def _write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(
                receipt,
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _v2_variant(
    plan: Mapping[str, Any],
    state_root: Path,
    decisions: Sequence[Mapping[str, Any]],
) -> tuple[str, Path, str]:
    decisions_sha256 = _packwiz_decisions_sha256(decisions)
    identity = {
        "plan_id": plan["plan_id"],
        "policy": PACKWIZ_V2_POLICY,
        "policy_version": PACKWIZ_V2_POLICY_VERSION,
        "decisions_sha256": decisions_sha256,
    }
    variant_id = "sha256:" + sha256(_canonical_bytes(identity)).hexdigest()
    root = (
        state_root
        / "fixtures"
        / "packwiz-v2"
        / variant_id.removeprefix("sha256:")[:16]
    )
    return variant_id, root, decisions_sha256


def _v2_packwiz_options(
    *,
    payload_root: Path,
    published_payload_root: Path | None = None,
    decisions: Sequence[Mapping[str, Any]],
    decisions_sha256: str,
    initial: Mapping[str, Any],
    final: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    state_uri_root = payload_root if published_payload_root is None else published_payload_root
    return {
        "policy": PACKWIZ_V2_POLICY,
        "policy_version": PACKWIZ_V2_POLICY_VERSION,
        "policy_sha256": "sha256:" + sha256(_canonical_bytes({
            "policy": PACKWIZ_V2_POLICY,
            "policy_version": PACKWIZ_V2_POLICY_VERSION,
        })).hexdigest(),
        "optional_count": len(decisions),
        "enabled_count": sum(
            int(bool(row["declared_default"])) for row in decisions
        ),
        "disabled_count": sum(
            int(not bool(row["declared_default"])) for row in decisions
        ),
        "decisions_sha256": decisions_sha256,
        "installer_state": {
            "relative_path": "packwiz.json",
            "uri": (state_uri_root / "packwiz.json").as_uri(),
            "cached_side": "client",
            "initial": dict(initial),
            "final": dict(final),
        },
        "files": [dict(row) for row in rows],
    }


def _v2_materialization_identity(
    plan: Mapping[str, Any],
    *,
    variant_id: str,
    source: Mapping[str, Any],
    pack: Mapping[str, Any],
    tools: Mapping[str, Any],
    launcher_manifest_sha256: str,
    payload: Mapping[str, Any],
    bootstrap_source: Mapping[str, Any],
    packwiz_options: Mapping[str, Any],
    target: Mapping[str, Any],
    launcher: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _materialization_identity(
        plan,
        source=source,
        pack=pack,
        tools=tools,
        launcher_manifest_sha256=launcher_manifest_sha256,
        payload=payload,
    )
    # V2's self-identity binds the complete retained projections it names,
    # not just their content digests.  In particular, byte lengths and URIs
    # are part of the authority that downstream consumers reopen.  Keeping
    # these full mappings in the additive V2 projection prevents a receipt
    # field from being changed while its materialization ID still verifies.
    identity.update({
        "variant_id": variant_id,
        "target": dict(target),
        "launcher": dict(launcher),
        "payload": dict(payload),
        "bootstrap_source": dict(bootstrap_source),
        "packwiz_options": dict(packwiz_options),
    })
    return identity


def packwiz_materialization_version(
    receipt: Mapping[str, Any],
) -> int | None:
    pair = (receipt.get("format"), receipt.get("schema_version"))
    if pair == ("workbench-packwiz-materialization-receipt-v2", 2):
        return 2
    return None


def verify_packwiz_materialization_receipt_identity(
    receipt: Mapping[str, Any],
) -> bool:
    """Verify the canonical self-identity of a current V2 receipt."""

    version = packwiz_materialization_version(receipt)
    if version != 2:
        return False
    source = receipt.get("source_snapshot")
    pack = receipt.get("refreshed_pack")
    tools = receipt.get("tools")
    launcher = receipt.get("launcher")
    payload = receipt.get("payload")
    if not all(
        isinstance(value, dict)
        for value in (source, pack, tools, launcher, payload)
    ):
        return False
    assert isinstance(tools, dict)
    installer = tools.get("installer")
    packwiz = tools.get("packwiz")
    java = tools.get("java")
    index = pack.get("index") if isinstance(pack, dict) else None
    if not all(
        isinstance(value, dict)
        for value in (installer, packwiz, java, index)
    ) or not isinstance(java.get("identity"), dict):
        return False
    identity = {
        "plan_id": receipt.get("plan_id"),
        "source_tree_sha256": source.get("tree_sha256"),
        "pack_manifest_sha256": pack.get("manifest_sha256"),
        "pack_index_sha256": index.get("actual_sha256"),
        "packwiz": {
            "sha256": packwiz.get("sha256"),
            "size": packwiz.get("size"),
        },
        "installer": {
            field: installer.get(field)
            for field in (
                "url",
                "source_revision",
                "sha256",
                "size",
                "entrypoint",
            )
        },
        "java": dict(java["identity"]),
        "launcher_manifest_sha256": launcher.get(
            "manifest_sha256_before"
        ),
        "payload_tree_sha256": payload.get("tree_sha256"),
    }
    if version == 2:
        target = receipt.get("target")
        bootstrap_source = receipt.get("bootstrap_source")
        options = receipt.get("packwiz_options")
        if (
            not isinstance(target, dict)
            or not isinstance(bootstrap_source, dict)
            or not isinstance(bootstrap_source.get("launcher_tree"), dict)
            or not isinstance(options, dict)
            or not isinstance(options.get("installer_state"), dict)
            or not isinstance(
                options["installer_state"].get("initial"),
                dict,
            )
            or not isinstance(
                options["installer_state"].get("final"),
                dict,
            )
            or not isinstance(options.get("files"), list)
        ):
            return False
        files = options["files"]
        if not all(isinstance(row, dict) for row in files):
            return False
        decisions = [
            {
                field: row.get(field)
                for field in (
                    "metadata_path",
                    "metafile_sha256",
                    "output_path",
                    "name",
                    "side",
                    "declared_default",
                    "applied",
                )
            }
            for row in files
        ]
        if any(
            not isinstance(row.get("metadata_path"), str)
            or not isinstance(row.get("output_path"), str)
            or not isinstance(row.get("name"), str)
            or row.get("side") not in {"client", "both"}
            or type(row.get("declared_default")) is not bool
            or type(row.get("applied")) is not bool
            or type(row.get("present")) is not bool
            for row in files
        ):
            return False
        initial_bytes = _packwiz_initial_state_bytes(decisions)
        expected_policy_sha256 = "sha256:" + sha256(_canonical_bytes({
            "policy": PACKWIZ_V2_POLICY,
            "policy_version": PACKWIZ_V2_POLICY_VERSION,
        })).hexdigest()
        expected_option_policy = {
            "side": "client",
            "optional_files": PACKWIZ_V2_POLICY,
            "launcher_metadata": "isolated-empty-sentinel",
        }
        expected_variant_id = "sha256:" + sha256(_canonical_bytes({
            "plan_id": receipt.get("plan_id"),
            "policy": PACKWIZ_V2_POLICY,
            "policy_version": PACKWIZ_V2_POLICY_VERSION,
            "decisions_sha256": options.get("decisions_sha256"),
        })).hexdigest()
        metadata_paths = [row.get("metadata_path") for row in files]
        output_paths = [row.get("output_path") for row in files]
        if (
            options.get("policy") != PACKWIZ_V2_POLICY
            or options.get("policy_version") != PACKWIZ_V2_POLICY_VERSION
            or options.get("policy_sha256") != expected_policy_sha256
            or receipt.get("option_policy") != expected_option_policy
            or options.get("decisions_sha256")
            != _packwiz_decisions_sha256(decisions)
            or options.get("optional_count") != len(files)
            or options.get("enabled_count")
            != sum(int(row.get("declared_default") is True) for row in files)
            or options.get("disabled_count")
            != sum(int(row.get("declared_default") is False) for row in files)
            or metadata_paths != sorted(metadata_paths)
            or len(metadata_paths) != len(set(metadata_paths))
            or len(output_paths) != len(set(output_paths))
            or any(
                row.get("applied") is not row.get("declared_default")
                or row.get("present") is not row.get("applied")
                for row in files
            )
            or options["installer_state"].get("relative_path")
            != "packwiz.json"
            or options["installer_state"].get("cached_side") != "client"
            or options["installer_state"]["initial"].get("sha256")
            != sha256(initial_bytes).hexdigest()
            or options["installer_state"]["initial"].get("size")
            != len(initial_bytes)
            or target.get("variant") != "packwiz-v2"
            or target.get("variant_id") != expected_variant_id
            or target.get("fixture_root_uri")
            != target.get("variant_root_uri")
            or target.get("receipt_uri")
            != str(target.get("fixture_root_uri", ""))
            + "/receipts/packwiz-materialization-v2.json"
            or target.get("instance_root_uri")
            != str(target.get("fixture_root_uri", "")) + "/instance"
            or launcher.get("manifest_uri")
            != str(target.get("instance_root_uri", ""))
            + "/mmc-pack.json"
            or launcher.get("manifest_sha256_after")
            != launcher.get("manifest_sha256_before")
            or payload.get("root_uri")
            != str(target.get("instance_root_uri", "")) + "/.minecraft"
            or options["installer_state"].get("uri")
            != str(payload.get("root_uri", "")) + "/packwiz.json"
        ):
            return False
        identity.update({
            "variant_id": target.get("variant_id"),
            "target": dict(target),
            "launcher": dict(launcher),
            "payload": dict(payload),
            "bootstrap_source": dict(bootstrap_source),
            "packwiz_options": dict(options),
        })
    return receipt.get("materialization_id") == "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()


def _v2_receipt(
    plan: Mapping[str, Any],
    *,
    variant_id: str,
    fixture_root: Path,
    source: Mapping[str, Any],
    exclusions: Mapping[str, Any],
    pack: Mapping[str, Any],
    staged_tree: Mapping[str, Any],
    seeds: Mapping[str, Any],
    tools: Mapping[str, Any],
    launcher_manifest_sha256: str,
    payload: Mapping[str, Any],
    evidence_root: Path,
    bootstrap_source: Mapping[str, Any],
    packwiz_options: Mapping[str, Any],
) -> dict[str, Any]:
    target = {
        "variant": "packwiz-v2",
        "variant_id": variant_id,
        "variant_root_uri": fixture_root.as_uri(),
        "fixture_root_uri": fixture_root.as_uri(),
        "instance_root_uri": (fixture_root / "instance").as_uri(),
        "receipt_uri": (fixture_root / RECEIPT_V2_PATH).as_uri(),
    }
    receipt = {
        "format": "workbench-packwiz-materialization-receipt-v2",
        "schema_version": 2,
        "operation_class": "local-mutation",
        "state": "materialized",
        "readiness": "pack-payload-installed",
        "plan_id": plan["plan_id"],
        "request": dict(plan["request"]),
        "workspace": dict(plan["workspace"]),
        "project": {
            "name": plan["project"].get("name"),
            "version": plan["project"].get("version"),
            "minecraft_version": plan["project"].get("minecraft_version"),
        },
        "source_snapshot": {
            **dict(source),
            "selection": "current-git-tracked-regular-files",
            "untracked_excluded": dict(exclusions),
        },
        "refreshed_pack": {
            **dict(pack),
            "staged_tree": dict(staged_tree),
        },
        "tools": {
            "packwiz": dict(tools["packwiz"]),
            "installer": dict(tools["installer"]),
            "java": dict(tools["java"]),
        },
        "bootstrap_source": dict(bootstrap_source),
        "packwiz_options": dict(packwiz_options),
        "option_policy": {
            "side": "client",
            "optional_files": PACKWIZ_V2_POLICY,
            "launcher_metadata": "isolated-empty-sentinel",
        },
        "seeds": dict(seeds),
        "launcher": {
            "manifest_uri": (fixture_root / "instance/mmc-pack.json").as_uri(),
            "manifest_sha256_before": launcher_manifest_sha256,
            "manifest_sha256_after": launcher_manifest_sha256,
        },
        "payload": {
            **dict(payload),
            "root_uri": (fixture_root / "instance/.minecraft").as_uri(),
        },
        "target": target,
        "evidence": {
            "refresh_log_uri": (evidence_root / "packwiz-refresh.log").as_uri(),
            "installer_log_uri": (evidence_root / "packwiz-installer.log").as_uri(),
        },
        "warnings": list(plan.get("warnings", [])),
        "limitations": [
            "Untracked workspace files are excluded from the staged source.",
            "Optional choices are the Packwiz metafiles' declared defaults.",
            "No Prism or MultiMC installation or account is modified.",
            "The payload is installed but Minecraft has not been launched.",
        ],
    }
    identity = _v2_materialization_identity(
        plan,
        variant_id=variant_id,
        source=source,
        pack=pack,
        tools=tools,
        launcher_manifest_sha256=launcher_manifest_sha256,
        payload=receipt["payload"],
        bootstrap_source=bootstrap_source,
        packwiz_options=packwiz_options,
        target=target,
        launcher=receipt["launcher"],
    )
    receipt["materialization_id"] = "sha256:" + sha256(
        _canonical_bytes(identity)
    ).hexdigest()
    return receipt


def _reuse_existing_v2(
    plan: Mapping[str, Any],
    *,
    variant_id: str,
    receipt_path: Path,
    fixture_root: Path,
    source: Mapping[str, Any],
    pack: Mapping[str, Any],
    tools: Mapping[str, Any],
    launcher_manifest_sha256: str,
    bootstrap_source: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
    decisions_sha256: str,
) -> dict[str, Any]:
    receipt = _load_json(receipt_path, "Packwiz V2 materialization receipt")
    payload_root = fixture_root / "instance/.minecraft"
    payload, _hashes = _tree_identity(payload_root)
    initial_bytes = _packwiz_initial_state_bytes(decisions)
    initial = {
        "sha256": sha256(initial_bytes).hexdigest(),
        "size": len(initial_bytes),
    }
    final, rows = _verify_packwiz_final_state(
        payload_root,
        decisions,
        pack,
    )
    options = _v2_packwiz_options(
        payload_root=payload_root,
        decisions=decisions,
        decisions_sha256=decisions_sha256,
        initial=initial,
        final=final,
        rows=rows,
    )
    expected_target = {
        "variant": "packwiz-v2",
        "variant_id": variant_id,
        "variant_root_uri": fixture_root.as_uri(),
        "fixture_root_uri": fixture_root.as_uri(),
        "instance_root_uri": (fixture_root / "instance").as_uri(),
        "receipt_uri": receipt_path.as_uri(),
    }
    expected_launcher = {
        "manifest_uri": (
            fixture_root / "instance/mmc-pack.json"
        ).as_uri(),
        "manifest_sha256_before": launcher_manifest_sha256,
        "manifest_sha256_after": launcher_manifest_sha256,
    }
    expected_payload = {
        **payload,
        "root_uri": payload_root.as_uri(),
    }
    expected_identity = _v2_materialization_identity(
        plan,
        variant_id=variant_id,
        source=source,
        pack=pack,
        tools=tools,
        launcher_manifest_sha256=launcher_manifest_sha256,
        payload=expected_payload,
        bootstrap_source=bootstrap_source,
        packwiz_options=options,
        target=expected_target,
        launcher=expected_launcher,
    )
    expected_id = "sha256:" + sha256(
        _canonical_bytes(expected_identity)
    ).hexdigest()
    recorded_payload = receipt.get("payload")
    recorded_target = receipt.get("target")
    if (
        receipt.get("format")
        != "workbench-packwiz-materialization-receipt-v2"
        or receipt.get("schema_version") != 2
        or receipt.get("operation_class") != "local-mutation"
        or receipt.get("state") != "materialized"
        or receipt.get("readiness") != "pack-payload-installed"
        or receipt.get("plan_id") != plan.get("plan_id")
        or receipt.get("materialization_id") != expected_id
        or receipt.get("source_snapshot", {}).get("tree_sha256")
        != source.get("tree_sha256")
        or receipt.get("refreshed_pack", {}).get("manifest_sha256")
        != pack.get("manifest_sha256")
        or receipt.get("refreshed_pack", {}).get("index") != pack.get("index")
        or receipt.get("tools") != dict(tools)
        or receipt.get("bootstrap_source") != dict(bootstrap_source)
        or receipt.get("packwiz_options") != options
        or recorded_target != expected_target
        or receipt.get("launcher") != expected_launcher
        or recorded_payload != expected_payload
        or not verify_packwiz_materialization_receipt_identity(receipt)
    ):
        raise PackwizMaterializationError(
            "existing Packwiz V2 payload belongs to a different "
            "materialization or has drifted"
        )
    return {
        "format": "workbench-packwiz-materialization-result-v2",
        "schema_version": 2,
        "outcome": "reused",
        "receipt": receipt,
    }


def materialize_packwiz_workspace_v2(
    plan: Mapping[str, Any],
    *,
    workspace_root: Path | str,
    state_root: Path | str,
    packwiz_executable: Path | str,
    java_executable: Path | str,
    java_identity: Mapping[str, Any],
    installer_path: Path | str,
    installer_lock: Mapping[str, Any],
    seed_roots: Sequence[Path | str] = (),
    refresh_timeout_seconds: float = 300.0,
    install_timeout_seconds: float = 1800.0,
) -> dict[str, Any]:
    """Install a policy-bound Packwiz client using declared option defaults."""

    workspace = Path(workspace_root).expanduser().resolve()
    state = Path(state_root).expanduser().resolve()
    if not workspace.is_dir() or workspace.is_symlink():
        raise PackwizMaterializationError(
            "Packwiz workspace must be a regular directory"
        )
    base_fixture, base_instance = _validate_plan(
        plan,
        workspace=workspace,
        state_root=state,
    )
    revision = _git_revision(workspace)
    if revision != plan["workspace"].get("revision"):
        raise PackwizMaterializationError(
            "workspace revision changed after runtime planning"
        )
    packwiz_path, packwiz_identity = _tool_identity(
        packwiz_executable,
        "Packwiz executable",
    )
    java_path, java_observed = _tool_identity(
        java_executable,
        "Java executable",
    )
    installer, installer_identity = _validate_installer(
        installer_path,
        installer_lock,
    )
    tools = {
        "packwiz": packwiz_identity,
        "installer": installer_identity,
        "java": {
            "identity": dict(java_identity),
            "sha256": java_observed["sha256"],
            "size": java_observed["size"],
            "source_uri": java_observed["source_uri"],
        },
    }

    bootstrap_receipt_path = base_fixture / BOOTSTRAP_RECEIPT_PATH
    bootstrap_digest, bootstrap_size = sha256_file(bootstrap_receipt_path)
    bootstrap_source = {
        "fixture_root_uri": base_fixture.as_uri(),
        "receipt_uri": bootstrap_receipt_path.as_uri(),
        "receipt_sha256": bootstrap_digest,
        "receipt_size": bootstrap_size,
        "launcher_tree": _launcher_tree_identity(base_instance),
    }

    plan_digest = str(plan["plan_id"]).removeprefix("sha256:")
    evidence_root = _ensure_state_subdirectory(
        state,
        "evidence",
        "runtime",
        plan_digest[:16],
        "packwiz-materialization-v2",
    )
    staging_parent = _ensure_state_subdirectory(
        state,
        "staging",
        "packwiz-v2",
    )
    staging = Path(tempfile.mkdtemp(
        prefix=f".{plan_digest[:16]}.",
        dir=staging_parent,
    ))
    try:
        staged_source = staging / "source"
        source, exclusions = copy_tracked_workspace(
            workspace,
            staged_source,
        )
        if _git_revision(workspace) != revision:
            raise PackwizMaterializationError(
                "workspace revision changed while staging Packwiz source"
            )
        _validate_source_binding(plan, staged_source)

        packwiz_cache = _ensure_state_subdirectory(
            state,
            "cache",
            "packwiz",
        )
        refresh_log = evidence_root / "packwiz-refresh.log"
        _run_logged(
            [
                str(packwiz_path),
                "--cache",
                str(packwiz_cache / "downloads"),
                "--config",
                str(packwiz_cache / "config.toml"),
                "--yes",
                "refresh",
            ],
            cwd=staged_source,
            log_path=refresh_log,
            timeout_seconds=refresh_timeout_seconds,
            label="Packwiz refresh",
        )
        pack, staged_tree = _validate_refreshed_pack(plan, staged_source)
        decisions = _packwiz_optional_decisions(staged_source)
        variant_id, fixture_root, decisions_sha256 = _v2_variant(
            plan,
            state,
            decisions,
        )
        fixture_parent = _ensure_state_subdirectory(
            state,
            "fixtures",
            "packwiz-v2",
        )
        if fixture_root.parent != fixture_parent:
            raise PackwizMaterializationError(
                "Packwiz V2 target escapes its fixture parent"
            )

        receipt_path = fixture_root / RECEIPT_V2_PATH
        instance_root = fixture_root / "instance"
        payload_root = instance_root / ".minecraft"
        if fixture_root.exists() or fixture_root.is_symlink():
            if (
                fixture_root.is_dir()
                and not fixture_root.is_symlink()
                and instance_root.is_dir()
                and not instance_root.is_symlink()
                and payload_root.is_dir()
                and not payload_root.is_symlink()
                and receipt_path.is_file()
                and not receipt_path.is_symlink()
            ):
                launcher_tree = _launcher_tree_identity(instance_root)
                if launcher_tree != bootstrap_source["launcher_tree"]:
                    raise PackwizMaterializationError(
                        "existing Packwiz V2 launcher base has drifted"
                    )
                launcher_digest, _launcher_size = sha256_file(
                    instance_root / "mmc-pack.json"
                )
                return _reuse_existing_v2(
                    plan,
                    variant_id=variant_id,
                    receipt_path=receipt_path,
                    fixture_root=fixture_root,
                    source=source,
                    pack=pack,
                    tools=tools,
                    launcher_manifest_sha256=launcher_digest,
                    bootstrap_source=bootstrap_source,
                    decisions=decisions,
                    decisions_sha256=decisions_sha256,
                )
            raise PackwizMaterializationError(
                "existing Packwiz V2 target is incomplete or unrecorded"
            )

        staged_fixture = staging / "variant"
        staged_instance = staged_fixture / "instance"
        launcher_tree = _copy_launcher_base(base_instance, staged_instance)
        if launcher_tree != bootstrap_source["launcher_tree"]:
            raise PackwizMaterializationError(
                "Cleanroom launcher base changed during V2 projection"
            )
        launcher_manifest = staged_instance / "mmc-pack.json"
        launcher_digest, _launcher_size = sha256_file(launcher_manifest)
        staged_payload = staged_instance / ".minecraft"
        staged_payload.mkdir()
        disabled_outputs = frozenset(
            str(row["output_path"])
            for row in decisions
            if not bool(row["declared_default"])
        )
        seeds = _seed_payload(
            staged_source,
            staged_payload,
            seed_roots,
            excluded_outputs=disabled_outputs,
        )
        initial_state = _write_packwiz_initial_state(
            staged_payload,
            decisions,
        )
        launcher_sentinel = staging / "launcher-sentinel"
        launcher_sentinel.mkdir()
        installer_log = evidence_root / "packwiz-installer.log"
        _run_logged(
            [
                str(java_path),
                "-cp",
                str(installer),
                INSTALLER_MAIN_CLASS,
                "--no-gui",
                "--side",
                "client",
                "--pack-folder",
                str(staged_payload),
                "--multimc-folder",
                str(launcher_sentinel),
                (staged_source / "pack.toml").as_uri(),
            ],
            cwd=staged_payload,
            log_path=installer_log,
            timeout_seconds=install_timeout_seconds,
            label="Packwiz Installer",
        )
        launcher_after, _launcher_size = sha256_file(launcher_manifest)
        if launcher_after != launcher_digest:
            raise PackwizMaterializationError(
                "Packwiz Installer changed Cleanroom launcher metadata"
            )
        final_state, option_rows = _verify_packwiz_final_state(
            staged_payload,
            decisions,
            pack,
        )
        options = _v2_packwiz_options(
            payload_root=staged_payload,
            published_payload_root=payload_root,
            decisions=decisions,
            decisions_sha256=decisions_sha256,
            initial=initial_state,
            final=final_state,
            rows=option_rows,
        )
        payload_identity, _payload_hashes = _tree_identity(staged_payload)
        if payload_identity["file_count"] == 0:
            raise PackwizMaterializationError(
                "Packwiz Installer produced an empty client payload"
            )
        receipt = _v2_receipt(
            plan,
            variant_id=variant_id,
            fixture_root=fixture_root,
            source=source,
            exclusions=exclusions,
            pack=pack,
            staged_tree=staged_tree,
            seeds=seeds,
            tools=tools,
            launcher_manifest_sha256=launcher_digest,
            payload=payload_identity,
            evidence_root=evidence_root,
            bootstrap_source=bootstrap_source,
            packwiz_options=options,
        )
        _write_receipt(staged_fixture / RECEIPT_V2_PATH, receipt)
        _rename_directory_no_replace(staged_fixture, fixture_root)
        return {
            "format": "workbench-packwiz-materialization-result-v2",
            "schema_version": 2,
            "outcome": "installed",
            "receipt": receipt,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _installer_lock(
    profile: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = (
        profile.get("runtime_artifacts")
        if isinstance(profile, Mapping)
        else None
    )
    record = (
        artifacts.get("packwiz_installer")
        if isinstance(artifacts, Mapping)
        else None
    )
    if not isinstance(record, Mapping):
        raise PackwizMaterializationError(
            "selected platform profile lacks packwiz_installer"
        )
    lock = {
        "id": "packwiz_installer",
        "url": record.get("url"),
        "source_revision": record.get("source_revision"),
        "sha256": record.get("sha256"),
        "size": record.get("size"),
    }
    planned_artifacts = plan.get("artifacts")
    planned = next(
        (
            artifact
            for artifact in planned_artifacts
            if isinstance(artifact, dict)
            and artifact.get("id") == "packwiz_installer"
        ),
        None,
    ) if isinstance(planned_artifacts, list) else None
    if (
        not isinstance(planned, dict)
        or planned.get("url") != lock["url"]
        or planned.get("sha256") != lock["sha256"]
    ):
        raise PackwizMaterializationError(
            "Cleanroom platform profile changed during materialization "
            "planning"
        )
    return lock


def _selected_java(
    result: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    source = result.get("source")
    if source == "managed":
        receipt = result.get("receipt")
        if (
            result.get("format") != "workbench-java-runtime-result-v2"
            or result.get("schema_version") != 2
            or not isinstance(receipt, dict)
            or receipt.get("format") != "workbench-java-runtime-receipt-v2"
            or receipt.get("schema_version") != 2
        ):
            raise PackwizMaterializationError(
                "managed Java result is not the current V2 format"
            )
        target = receipt.get("target")
        if not isinstance(target, dict):
            raise PackwizMaterializationError(
                "managed Java receipt lacks a target"
            )
        path = _file_uri_path(
            target.get("java_uri"),
            "managed Java executable",
        )
        identity = {
            "source": "managed",
            "runtime_id": receipt.get("runtime_id"),
            "runtime_identity": (
                receipt.get("policy", {})
            ).get("runtime_identity"),
            "runtime_version": (
                receipt.get("probe", {})
            ).get("runtime_version"),
            "vendor": (
                receipt.get("probe", {})
            ).get("vendor"),
        }
    elif source == "external":
        runtime = result.get("runtime")
        policy = result.get("policy")
        if not isinstance(runtime, dict) or not isinstance(policy, dict):
            raise PackwizMaterializationError(
                "external Java result lacks runtime identity"
            )
        path = _file_uri_path(
            runtime.get("java_uri"),
            "external Java executable",
        )
        probe = runtime.get("probe")
        if not isinstance(probe, dict):
            raise PackwizMaterializationError(
                "external Java result lacks a probe"
            )
        identity = {
            "source": "external",
            "runtime_identity": policy.get("runtime_identity"),
            "runtime_version": probe.get("runtime_version"),
            "vendor": probe.get("vendor"),
        }
    else:
        raise PackwizMaterializationError(
            "Java result has an unsupported source"
        )
    if any(
        not isinstance(value, str) or not value
        for value in identity.values()
        if value is not None
    ):
        raise PackwizMaterializationError(
            "selected Java identity is incomplete"
        )
    return path.resolve(), identity


def _resolve_packwiz(
    workspace: Path,
    explicit: Path | str | None,
) -> Path:
    if explicit is not None:
        candidate = Path(explicit).expanduser()
    else:
        names = (
            ("packwiz.exe", "packwiz")
            if os.name == "nt"
            else ("packwiz", "packwiz.exe")
        )
        candidate = next(
            (
                workspace / name
                for name in names
                if (workspace / name).is_file()
            ),
            None,
        )
        if candidate is None:
            discovered = shutil.which("packwiz")
            if discovered is None:
                raise PackwizMaterializationError(
                    "Packwiz is missing; pass --packwiz or add it to PATH"
                )
            candidate = Path(discovered)
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise PackwizMaterializationError(
            f"Packwiz executable is missing: {resolved}"
        )
    return resolved


def materialize_project_runtime(
    suite_root: Path | str,
    workspace_root: Path | str,
    *,
    launcher: str = "prism",
    state_root: Path | str | None = None,
    packwiz_executable: Path | str | None = None,
    seed_roots: Sequence[Path | str] = (),
    refresh_timeout_seconds: float = 300.0,
    install_timeout_seconds: float = 1800.0,
    configuration: WorkbenchConfiguration | None = None,
    config_path: Path | str | None = None,
    resolved_bindings: ResolvedBindings | None = None,
) -> dict[str, Any]:
    """Provision inputs and install a local Packwiz client payload."""

    suite = Path(suite_root).resolve()
    workspace = Path(workspace_root).resolve()
    if configuration is not None and config_path is not None:
        raise PackwizMaterializationError(
            "configuration and config_path are mutually exclusive"
        )
    try:
        active_configuration = configuration or load_workbench_configuration(
            suite,
            CONFIGURATION_PATH if config_path is None else config_path,
        )
    except WorkbenchConfigurationError as exc:
        raise PackwizMaterializationError(
            f"Workbench configuration cannot be loaded: {exc}"
        ) from exc
    operation_bindings = resolved_bindings
    if operation_bindings is None:
        operation_bindings = active_configuration.resolve_bindings(
            names=("java_candidate_home",),
        )
    try:
        active_configuration.require_binding_snapshot(
            operation_bindings,
            names=("java_candidate_home",),
        )
    except WorkbenchConfigurationError as exc:
        raise PackwizMaterializationError(
            f"resolved bindings are invalid: {exc}"
        ) from exc
    state = (
        default_suite_state_root(suite)
        if state_root is None
        else Path(state_root).expanduser().resolve()
    )
    try:
        plan = plan_project_runtime(
            suite,
            workspace,
            side="client",
            launcher=launcher,
            state_root=state,
            configuration=active_configuration,
        )
    except RuntimePlanError as exc:
        raise PackwizMaterializationError(str(exc)) from exc
    lock = _installer_lock(
        active_configuration.platform_document.values,
        plan,
    )
    blockers = plan.get("blockers")
    if not isinstance(blockers, list) or blockers:
        blocker_ids = [
            str(blocker.get("id"))
            for blocker in blockers or []
            if isinstance(blocker, dict)
        ]
        raise PackwizMaterializationError(
            "runtime plan is blocked"
            + (": " + ", ".join(blocker_ids) if blocker_ids else "")
        )

    try:
        bootstrap = bootstrap_project_runtime(
            suite,
            workspace,
            launcher=launcher,
            state_root=state,
            configuration=active_configuration,
        )
    except RuntimeBootstrapError as exc:
        raise PackwizMaterializationError(str(exc)) from exc
    if bootstrap["receipt"].get("plan_id") != plan.get("plan_id"):
        raise PackwizMaterializationError(
            "workspace changed while bootstrapping the Cleanroom fixture"
        )
    try:
        java_result = ensure_java_runtime(
            suite,
            state_root=state,
            configuration=active_configuration,
            resolved_bindings=operation_bindings,
        )
    except JavaRuntimeError as exc:
        raise PackwizMaterializationError(str(exc)) from exc
    java_path, java_identity = _selected_java(java_result)
    try:
        installer_path, artifact_outcome = fetch_verified_artifact(
            url=str(lock["url"]),
            expected_sha256=str(lock["sha256"]),
            expected_size=lock["size"],
            state_root=state,
            label="Packwiz Installer",
            timeout_seconds=60,
            user_agent="Workbench-Packwiz-Materializer/0.1",
        )
    except ArtifactStoreError as exc:
        raise PackwizMaterializationError(str(exc)) from exc
    packwiz = _resolve_packwiz(workspace, packwiz_executable)
    result = materialize_packwiz_workspace_v2(
        plan,
        workspace_root=workspace,
        state_root=state,
        packwiz_executable=packwiz,
        java_executable=java_path,
        java_identity=java_identity,
        installer_path=installer_path,
        installer_lock=lock,
        seed_roots=seed_roots,
        refresh_timeout_seconds=refresh_timeout_seconds,
        install_timeout_seconds=install_timeout_seconds,
    )
    result["bootstrap_outcome"] = bootstrap["outcome"]
    result["java_outcome"] = java_result["outcome"]
    result["installer_artifact_outcome"] = artifact_outcome
    return result
