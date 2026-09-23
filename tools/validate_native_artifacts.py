#!/usr/bin/env python3
"""Audit native Workbench wheel contents independently of successful builds."""
from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from validate_public_tree import private_reason, private_markdown_reason

ROOT = Path(__file__).resolve().parents[1]
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_WHEEL_BYTES = 128 * 1024 * 1024


def audit(path: Path, *, root: Path = ROOT) -> list[str]:
    errors = []
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [entry.filename for entry in members]
        if len(names) != len(set(names)):
            return ["duplicate wheel member"]
        if sum(entry.file_size for entry in members) > MAX_WHEEL_BYTES:
            return ["wheel exceeds expanded size bound"]
        contents = {}
        for entry in members:
            name = entry.filename
            relative = PurePosixPath(name)
            if relative.is_absolute() or any(part in {"..", ".", ""} for part in name.split("/")) or "\\" in name or "\x00" in name:
                errors.append(f"unsafe wheel member: {name!r}")
                continue
            if entry.file_size > MAX_MEMBER_BYTES or stat.S_ISLNK(entry.external_attr >> 16):
                errors.append(f"unbounded or symlink wheel member: {name}")
                continue
            if private_reason(name) or any(part in {".workbench", "__pycache__", "node_modules", ".git", ".pixi", ".gradle"} for part in relative.parts) or name.endswith(".pyc"):
                errors.append(f"private/generated wheel member: {name}")
            if "workbench_portable" in relative.parts:
                errors.append(f"retired implementation in native artifact: {name}")
            if name.endswith(".py") and any(re.search(r"_v\d+$", part.removesuffix(".py")) for part in relative.parts):
                errors.append(f"versioned implementation name: {name}")
            raw = archive.read(entry)
            if name.endswith(".md") and private_markdown_reason(name, raw.decode("utf-8", errors="replace")):
                errors.append(f"private coordination content: {name}")
            contents[name] = raw
        metadata_names = [name for name in contents if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
        if len(metadata_names) != 1:
            return errors + ["wheel must contain one native metadata owner"]
        metadata_name, = metadata_names
        prefix = metadata_name.rsplit("/", 1)[0]
        metadata = BytesParser().parsebytes(contents[metadata_name])
        name = metadata.get("Name", "")
        if not name.startswith("workbench-"):
            return errors + ["not a Workbench distribution"]
        if metadata.get("License-Expression") != "LGPL-3.0-only":
            errors.append("missing exact native license expression")
        for filename in ("LICENSE", "NOTICE.md"):
            expected = (root / filename).read_bytes()
            candidates = [value for member, value in contents.items() if member.startswith(prefix + "/licenses/") and PurePosixPath(member).name == filename]
            if len(candidates) != 1 or candidates[0] != expected:
                errors.append(f"missing or altered {filename}")
        record_name = prefix + "/RECORD"
        if record_name not in contents:
            return errors + ["wheel has no RECORD"]
        try:
            rows = list(csv.reader(io.StringIO(contents[record_name].decode("utf-8"))))
            indexed = {}
            for row in rows:
                if len(row) != 3 or row[0] in indexed:
                    raise ValueError("malformed or duplicated RECORD row")
                indexed[row[0]] = row[1:]
            if set(indexed) != set(contents):
                raise ValueError("RECORD does not account for every wheel member")
            for member, raw in contents.items():
                digest, size = indexed[member]
                if member == record_name:
                    if digest or size:
                        raise ValueError("RECORD cannot hash itself")
                    continue
                expected_digest = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
                if digest != expected_digest or size != str(len(raw)):
                    errors.append(f"RECORD identity mismatch: {member}")
        except (UnicodeError, ValueError, csv.Error) as exc:
            errors.append(f"invalid RECORD: {exc}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path)
    args = parser.parse_args(argv)
    paths = sorted(args.wheelhouse.glob("workbench_*.whl"))
    if not paths:
        parser.error("wheelhouse contains no Workbench wheels")
    failures = [(path.name, error) for path in paths for error in audit(path)]
    for name, error in failures:
        print(f"{name}: {error}")
    if not failures:
        print(f"Native contents, licenses, notices and RECORD: PASS ({len(paths)} wheels)")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
