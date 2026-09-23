"""Exact tracked working-tree projection; no pack or launcher policy."""

from __future__ import annotations
from dataclasses import dataclass
from functools import cached_property
from hashlib import sha256
import json
import os
import stat
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Sequence
from types import MappingProxyType
from .git_observation import (
    GitObservationError,
    observation_environment,
    safe_git_prefix,
)

DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class WorkingTreeError(ValueError):
    """The selected source cannot be observed or copied safely."""


@dataclass(frozen=True)
class SourceInputs:
    """Immutable bytes from the same two-pass observation, never a live reader."""

    observation_json: str
    files: tuple[tuple[str, bytes], ...]
    modes: tuple[tuple[str, int], ...] = ()
    deleted_paths: tuple[str, ...] = ()

    @property
    def observation(self) -> dict[str, Any]:
        return json.loads(self.observation_json)

    @cached_property
    def sources(self):
        return MappingProxyType(dict(self.files))


def observe_source(workspace: Path | str) -> dict[str, Any]:
    return _observe_source(workspace, retain_bytes=False)[0]


def capture_source_inputs(workspace: Path | str) -> SourceInputs:
    """Retain present bytes and missing indexed paths from one stable observation.

    ``deleted_paths`` supplements, but does not change, the existing observation
    or saved-candidate V1 identity. Staged deletions are already absent from the
    observed index and are therefore absent from both its count and this tuple.
    """
    observation, files, modes, deleted_paths = _observe_source(
        workspace, retain_bytes=True
    )
    return SourceInputs(
        json.dumps(observation, sort_keys=True),
        tuple(files.items()),
        tuple(modes.items()),
        tuple(deleted_paths),
    )


def revision_labels(workspace: Path, revision: str) -> dict[str, Any]:
    """Local Git labels are presentation evidence, never upstream release proof."""
    if _git_bytes(workspace, ("rev-parse", "HEAD")).decode().strip() != revision:
        raise WorkingTreeError("source revision changed before label observation")
    tags = _git_bytes(workspace, ("tag", "--points-at", revision)).decode().splitlines()
    if len(tags) > 128 or any(len(tag) > 256 for tag in tags):
        raise WorkingTreeError("source labels exceed their bound")
    return {"local_tags": sorted(tags), "release_verified": False,
            "meaning": "Local tags at the captured revision; not proof of an official or latest release"}


def _observe_source(workspace: Path | str, *, retain_bytes: bool):
    """Observe current tracked and non-ignored untracked bytes, without Git filters.

    A revision and dirty flag alone cannot distinguish successive editor saves.
    Two equal bounded passes reject a moving tree instead of publishing a mixed
    observation. Missing tracked files are explicit; links and submodules fail
    closed. Ignored build/runtime state is outside this source observation.
    """
    root = Path(workspace).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise WorkingTreeError("source must be an ordinary checkout directory")
    root = root.resolve()
    top = Path(
        os.fsdecode(_git_bytes(root, ("rev-parse", "--show-toplevel"))).strip()
    ).resolve()
    if top != root:
        raise WorkingTreeError(
            "select the checkout root, not a child or an ancestor repository"
        )

    retained: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    deleted_paths: list[str] = []

    def capture(*, retain: bool = False) -> dict[str, Any]:
        revision = _git_bytes(root, ("rev-parse", "HEAD")).decode("ascii").strip()
        index = _git_bytes(root, ("ls-files", "--stage", "-z"))
        untracked = _git_bytes(
            root, ("ls-files", "--others", "--exclude-standard", "-z")
        )
        paths: dict[str, str] = {}
        for record in index.split(b"\0"):
            if not record:
                continue
            header, path = record.split(b"\t", 1)
            mode, _oid, stage = header.split(b" ")
            if stage != b"0" or mode not in {b"100644", b"100755"}:
                raise WorkingTreeError(
                    "source has unmerged, linked or submodule index entries"
                )
            paths[os.fsdecode(path)] = mode.decode("ascii")
        for path in untracked.split(b"\0"):
            if path:
                paths[os.fsdecode(path)] = "untracked"
        if len(paths) > 100_000:
            raise WorkingTreeError("source observation exceeds its path bound")
        entries = []
        total = 0
        for name, index_mode in sorted(paths.items()):
            relative = _safe_relative(name, "observed source")
            path = root
            for part in relative.parts[:-1]:
                path = path / part
                if path.is_symlink():
                    raise WorkingTreeError("source parent is a symbolic link")
            path = root.joinpath(*relative.parts)
            try:
                before = path.lstat()
            except FileNotFoundError:
                if index_mode == "untracked":
                    raise WorkingTreeError("source changed during observation")
                if retain:
                    deleted_paths.append(name)
                entries.append(
                    {"path": name, "index_mode": index_mode, "state": "missing"}
                )
                continue
            except OSError as exc:
                raise WorkingTreeError(
                    "source became unavailable during observation"
                ) from exc
            if not stat.S_ISREG(before.st_mode) or before.st_size > 64 * 1024 * 1024:
                raise WorkingTreeError(
                    "source contains a non-regular or over-bound file"
                )
            total += before.st_size
            if total > 512 * 1024 * 1024:
                raise WorkingTreeError("source observation exceeds its byte bound")
            digest = sha256()
            size = 0
            chunks = []
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise WorkingTreeError("source custody changed during observation")
                    while chunk := stream.read(DOWNLOAD_CHUNK_BYTES):
                        digest.update(chunk)
                        if retain:
                            chunks.append(chunk)
                        size += len(chunk)
                        if size > before.st_size:
                            raise WorkingTreeError("source grew during observation")
                after = path.lstat()
            except OSError as exc:
                raise WorkingTreeError(
                    "source changed or became unavailable during observation"
                ) from exc

            def token(metadata):
                # Reading can update atime; it is not source drift.
                return (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )

            if token(before) != token(after) or size != before.st_size:
                raise WorkingTreeError("source changed during observation")
            if retain:
                retained[name] = b"".join(chunks)
                modes[name] = 0o100755 if before.st_mode & stat.S_IXUSR else 0o100644
            entries.append(
                {
                    "path": name,
                    "index_mode": index_mode,
                    "mode": stat.S_IMODE(before.st_mode),
                    "state": "present",
                    "size": size,
                    "sha256": digest.hexdigest(),
                }
            )
        return {
            "root_uri": root.as_uri(),
            "revision": revision,
            "dirty": bool(
                _git_bytes(root, ("status", "--porcelain", "--untracked-files=normal"))
            ),
            "index_sha256": sha256(index).hexdigest(),
            "file_count": len(entries),
            "source_sha256": sha256(_canonical_bytes(entries)).hexdigest(),
        }

    first = capture(retain=retain_bytes)
    if capture() != first:
        raise WorkingTreeError("source changed during observation")
    return first, retained, modes, deleted_paths


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise WorkingTreeError(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or ":" in path.parts[0]
    ):
        raise WorkingTreeError(f"{label} must be a portable relative path")
    return path


def _git_bytes(
    workspace: Path,
    arguments: Sequence[str],
) -> bytes:
    try:
        prefix = safe_git_prefix(workspace, timeout=30)
    except GitObservationError as exc:
        raise WorkingTreeError(
            f"cannot inspect Git workspace with {' '.join(arguments)}: {exc}"
        ) from exc
    try:
        completed = subprocess.run(
            [*prefix, "-C", str(workspace), *arguments],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=observation_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkingTreeError(
            f"cannot inspect Git workspace with {' '.join(arguments)}"
        ) from exc
    if completed.returncode:
        detail = completed.stderr.decode(
            "utf-8",
            "replace",
        ).strip()
        raise WorkingTreeError(
            f"Git {' '.join(arguments)} failed: {detail or 'unknown Git failure'}"
        )
    return completed.stdout


def _tree_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tree_sha256": "sha256:" + sha256(_canonical_bytes(entries)).hexdigest(),
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
    }


def copy_tracked_workspace(
    workspace: Path,
    destination: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _git_bytes(
        workspace,
        ("ls-files", "--stage", "-z"),
    ).split(b"\0")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    destination.mkdir(parents=True)
    for raw_record in records:
        if not raw_record:
            continue
        try:
            header, raw_path = raw_record.split(b"\t", 1)
            mode_raw, _object_id, stage_raw = header.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            stage = stage_raw.decode("ascii")
            relative_text = os.fsdecode(raw_path)
        except (ValueError, UnicodeError) as exc:
            raise WorkingTreeError(
                "Git returned a malformed tracked-file record"
            ) from exc
        if stage != "0":
            raise WorkingTreeError("the workspace index contains unresolved Git stages")
        if mode not in {"100644", "100755"}:
            raise WorkingTreeError(
                f"tracked path has unsupported Git mode {mode}: {relative_text}"
            )
        relative = _safe_relative(relative_text, "tracked Git path")
        collision_key = relative.as_posix().casefold()
        if collision_key in seen:
            raise WorkingTreeError(
                f"tracked paths collide by case: {relative.as_posix()}"
            )
        seen.add(collision_key)

        source = workspace.joinpath(*relative.parts)
        try:
            resolved_source = source.resolve(strict=True)
        except OSError as exc:
            raise WorkingTreeError(
                f"tracked file is missing: {relative.as_posix()}"
            ) from exc
        if (
            not resolved_source.is_relative_to(workspace)
            or source.is_symlink()
            or not source.is_file()
        ):
            raise WorkingTreeError(
                f"tracked path is not a regular workspace file: {relative.as_posix()}"
            )
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        size = 0
        try:
            with source.open("rb") as input_stream:
                with target.open("xb") as output_stream:
                    while chunk := input_stream.read(DOWNLOAD_CHUNK_BYTES):
                        digest.update(chunk)
                        size += len(chunk)
                        output_stream.write(chunk)
            target.chmod(0o755 if mode == "100755" else 0o644)
        except OSError as exc:
            raise WorkingTreeError(
                f"cannot stage tracked file: {relative.as_posix()}"
            ) from exc
        entries.append(
            {
                "mode": mode,
                "path": relative.as_posix(),
                "sha256": digest.hexdigest(),
                "size": size,
            }
        )

    if not entries:
        raise WorkingTreeError("the workspace has no tracked files")
    entries.sort(key=lambda entry: entry["path"])
    raw_untracked = _git_bytes(
        workspace,
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ).split(b"\0")
    untracked = sorted(os.fsdecode(path) for path in raw_untracked if path)
    exclusions = {
        "file_count": len(untracked),
        "paths_sha256": "sha256:" + sha256(_canonical_bytes(untracked)).hexdigest(),
    }
    return _tree_summary(entries), exclusions
