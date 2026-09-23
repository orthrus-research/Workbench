#!/usr/bin/env python3
"""Reproduce the pinned Packwiz source-and-vendor bundle from a prepared clone."""

from __future__ import annotations

import argparse
import base64
import gzip
from hashlib import sha256
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core/src"))
sys.path.insert(0, str(ROOT / "api/src"))
from workbench_core import tooling_provision as policy  # noqa: E402


def _git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


def build(source: Path, output_dir: Path) -> None:
    source = source.expanduser().resolve(strict=True)
    if _git(source, "rev-parse", "HEAD") != policy.SOURCE_COMMIT:
        raise ValueError("Packwiz source revision is not the pinned commit")
    if _git(source, "rev-parse", "HEAD^{tree}") != policy.SOURCE_TREE:
        raise ValueError("Packwiz source tree is not the pinned tree")
    if _git(source, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Packwiz tracked source is modified")
    for name, expected in (
        ("go.mod", "594d699ba3863ed70b1339bdb535afaf63eee0066ae3f59ea89e56543c79263d"),
        ("go.sum", "dbac35acf64a4258999359d40d57a95ad46f76a8dec25ef104bd125040bce52e"),
    ):
        if sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Packwiz {name} changed")
    vendor = source / "vendor"
    if not (vendor / "modules.txt").is_file():
        raise ValueError("run the pinned Go toolchain's `go mod vendor` first")
    tracked = subprocess.check_output(
        ["git", "-C", str(source), "ls-files", "-z"]
    ).split(b"\0")
    paths = {Path(os.fsdecode(raw)) for raw in tracked if raw}
    paths.update(path.relative_to(source) for path in vendor.rglob("*") if path.is_file())
    paths = {path for path in paths
             if not any(part.startswith(".") for part in path.parts)}
    if not any(path.name == "LICENSE" and "vendor" in path.parts for path in paths):
        raise ValueError("vendored dependency licenses are missing")
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / f".{policy.SOURCE_BUNDLE}.{uuid4().hex}.tmp"
    parts: list[tuple[Path, Path]] = []
    try:
        with temporary.open("xb") as raw, gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9
        ) as compressed, tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
        ) as archive:
            for relative in sorted(paths, key=lambda value: value.as_posix()):
                candidate = source / relative
                if not candidate.is_file() or candidate.is_symlink():
                    raise ValueError(f"Packwiz bundle has an unsupported path: {relative}")
                data = candidate.read_bytes()
                item = tarfile.TarInfo(relative.as_posix())
                item.size = len(data)
                # The pinned source tree and its vendored modules contain no
                # executable files. Fixed modes keep the bundle host-neutral.
                item.mode = 0o644
                item.uid = item.gid = item.mtime = 0
                item.uname = item.gname = ""
                archive.addfile(item, io.BytesIO(data))
        digest, size = policy.sha256_file(temporary)
        if (digest, size) != (policy.SOURCE_SHA256, policy.SOURCE_SIZE):
            raise ValueError(
                "recreated Packwiz bundle differs from Core's pinned bytes: "
                f"{digest} {size}"
            )
        raw_bundle = temporary.read_bytes()
        midpoint = (len(raw_bundle) + 1) // 2
        for raw_part, (name, expected_digest, expected_size) in zip(
            (raw_bundle[:midpoint], raw_bundle[midpoint:]), policy.SOURCE_PARTS,
            strict=True,
        ):
            encoded = base64.b64encode(raw_part) + b"\n"
            if (sha256(encoded).hexdigest(), len(encoded)) != (expected_digest, expected_size):
                raise ValueError(f"recreated Packwiz source part differs from Core's pinned bytes: {name}")
            output = output_dir / name
            part_temp = output_dir / f".{name}.{uuid4().hex}.tmp"
            part_temp.write_bytes(encoded)
            parts.append((part_temp, output))
        for part_temp, output in parts:
            os.replace(part_temp, output)
    finally:
        temporary.unlink(missing_ok=True)
        for part_temp, _ in parts:
            part_temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="clean upstream clone at the pinned commit with vendor/ prepared")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "core/src/workbench_core/data")
    args = parser.parse_args()
    try:
        build(args.source, args.output_dir)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Packwiz bundle rebuild failed: {exc}", file=sys.stderr)
        return 2
    print(f"Packwiz source parts verified: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
