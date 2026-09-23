"""Read an exact local Git baseline without checkout, filters, fetch, or index writes."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

from .git_observation import require_configured_git_executable
from .working_tree import SourceInputs, WorkingTreeError, _safe_relative


def capture_baseline(workspace: Path, reference: str) -> SourceInputs:
    if (
        not isinstance(reference, str)
        or not reference
        or len(reference) > 1024
        or "\0" in reference
    ):
        raise WorkingTreeError("select an explicit bounded local Git baseline")
    # Disable replacement objects, optional index locks, and promisor fetching.
    # Object reads do not execute checkout/smudge filters or user diff drivers.
    environment = {
        **os.environ,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    git = require_configured_git_executable()

    def run(*args, payload=None):
        result = subprocess.run(
            [git, "-C", str(workspace), *args],
            input=payload,
            capture_output=True,
            timeout=60,
            env=environment,
            check=False,
        )
        if result.returncode:
            raise WorkingTreeError(
                "local baseline unavailable: "
                + result.stderr.decode("utf-8", "replace")[:2000]
            )
        return result.stdout

    revision = (
        run("rev-parse", "--verify", "--end-of-options", reference + "^{commit}")
        .decode("ascii")
        .strip()
    )
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise WorkingTreeError("baseline did not resolve to one exact commit")
    entries = []
    total = 0
    for row in run("ls-tree", "-r", "-z", "-l", revision).split(b"\0"):
        if not row:
            continue
        header, raw_path = row.split(b"\t", 1)
        mode, kind, oid, size = header.split()
        path = raw_path.decode("utf-8", "strict")
        if (
            _safe_relative(path, "baseline source").as_posix() != path
            or "\n" in path
            or "\r" in path
        ):
            raise WorkingTreeError("baseline path is not portable")
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise WorkingTreeError("baseline contains a symlink or submodule")
        size = int(size)
        total += size
        if (
            size > 64 * 1024 * 1024
            or total > 512 * 1024 * 1024
            or len(entries) >= 100_000
        ):
            raise WorkingTreeError("baseline exceeds source observation bounds")
        entries.append((path, int(mode, 8), oid, size))
    payload = (
        run("cat-file", "--batch", payload=b"".join(row[2] + b"\n" for row in entries))
        if entries
        else b""
    )
    offset, files, modes, identities = 0, [], [], []
    for path, mode, oid, size in entries:
        end = payload.find(b"\n", offset)
        if end == -1 or payload[offset:end] != oid + b" blob " + str(size).encode():
            raise WorkingTreeError("baseline object stream changed")
        raw = payload[end + 1 : end + 1 + size]
        offset = end + 2 + size
        if len(raw) != size or payload[offset - 1 : offset] != b"\n":
            raise WorkingTreeError("baseline object stream is incomplete")
        files.append((path, raw))
        modes.append((path, mode))
        identities.append(
            {
                "path": path,
                "mode": mode,
                "sha256": sha256(raw).hexdigest(),
                "size": size,
            }
        )
    if offset != len(payload):
        raise WorkingTreeError("baseline object stream contains unexpected bytes")
    observation = {
        "root_uri": workspace.resolve().as_uri(),
        "revision": revision,
        "dirty": False,
        "kind": "local-git-commit",
        "file_count": len(files),
        "source_sha256": sha256(
            json.dumps(identities, sort_keys=True).encode()
        ).hexdigest(),
    }
    return SourceInputs(
        json.dumps(observation, sort_keys=True), tuple(files), tuple(modes)
    )
