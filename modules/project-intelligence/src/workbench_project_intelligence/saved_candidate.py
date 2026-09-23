"""Portable saved candidates, independent of construction and runtime policy."""

from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from .git_tree import _windows_extended_path
from .working_tree import SourceInputs, WorkingTreeError, _safe_relative


def candidate_manifest(inputs: SourceInputs) -> dict:
    modes = dict(inputs.modes)
    entries, seen = [], set()
    for name, raw in sorted(inputs.files):
        path = _safe_relative(name, "candidate path")
        if (
            path.as_posix() != name
            or any(ord(c) < 32 for c in name)
            or any(part.casefold() == ".git" for part in path.parts)
            or name.casefold() in seen
        ):
            raise WorkingTreeError(
                "candidate paths are not unique portable source paths"
            )
        seen.add(name.casefold())
        mode = modes.get(name)
        if mode not in (0o100644, 0o100755) or not isinstance(raw, bytes):
            raise WorkingTreeError(
                "candidate requires exact ordinary file bytes and modes"
            )
        entries.append(
            {
                "path": name,
                "mode": mode,
                "size": len(raw),
                "sha256": sha256(raw).hexdigest(),
            }
        )
    if (
        len(entries) > 100_000
        or any(row["size"] > 64 * 1024**2 for row in entries)
        or sum(row["size"] for row in entries) > 512 * 1024**2
    ):
        raise WorkingTreeError("candidate exceeds its source bounds")
    body = {
        "format": "workbench-saved-candidate-v1",
        "source": inputs.observation,
        "files": entries,
    }
    return {
        **body,
        "id": "candidate:sha256:"
        + sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def stage_candidate(inputs: SourceInputs, destination: Path) -> dict:
    """Write only captured bytes into a fresh caller-owned destination."""
    manifest = candidate_manifest(inputs)
    source = Path(url2pathname(urlparse(inputs.observation["root_uri"]).path))
    target = Path(os.path.abspath(destination))
    for path in (*reversed(target.parents), target):
        native = _windows_extended_path(path)
        if native.is_symlink() or getattr(native, "is_junction", lambda: False)():
            raise WorkingTreeError("candidate destination traverses a link")
    # Core allocates the parent; this owner never selects or cleans storage.
    if (
        target == source
        or target.is_relative_to(source)
        or source.is_relative_to(target)
    ):
        raise WorkingTreeError("candidate storage overlaps developer source")
    # The native spelling is only an I/O detail. Manifest paths and overlap
    # checks retain the caller's ordinary source and destination identities.
    _windows_extended_path(target).mkdir(mode=0o700)
    for row in manifest["files"]:
        path = _windows_extended_path(target / row["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(inputs.sources[row["path"]])
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(row["mode"] & 0o777)
    return manifest
