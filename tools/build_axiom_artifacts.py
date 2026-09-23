#!/usr/bin/env python3
"""Capture exact local artifact bytes selected by Axiom; no download or mod execution."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from build_axiom_target import canonical, ordinary_path

MAX_ARTIFACT = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024


def ordinary(root, name):
    candidate = root
    for part in ordinary_path(name).split("/"):
        candidate /= part
        if candidate.is_symlink():
            raise ValueError("indirect artifact path: " + name)
    if candidate.exists() and not candidate.is_file():
        raise ValueError("artifact is not a regular file: " + name)
    return candidate


def hash_file(path, algorithm):
    if algorithm not in {"sha256", "sha512", "sha1", "md5"}:
        raise ValueError("unsupported declared artifact hash")
    hashes = {name: hashlib.new(name) for name in {algorithm, "sha256"}}
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT:
                raise ValueError("artifact exceeds capture byte bound")
            for digest in hashes.values():
                digest.update(chunk)
    if not size:
        raise ValueError("empty artifact")
    return size, {key: value.hexdigest() for key, value in hashes.items()}


def capture(result, root, output, *, allow_partial=False):
    """Data custody only. Java independently rechecks source selection and every byte."""
    root, output = Path(root).absolute(), Path(output).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("artifact root must be a direct directory")
    if output.exists() or output.is_symlink():
        raise ValueError("artifact candidate must be new")
    if result.get("schema") != "axiom.result.v1" or result.get("operation") != "target" or result.get("status") != "accepted":
        raise ValueError("capture requires an accepted Axiom target inspection")
    body = result["result"]
    composition = body["composition"]
    if composition["side"] not in {"client", "server"} or not composition["declarationInventoryComplete"]:
        raise ValueError("capture requires complete declarations and an explicit physical side")
    records, paths, missing, seen, total = [], {}, [], set(), 0
    for row in composition["artifacts"]:
        if row["selection"] != "included-declaration":
            continue
        metadata = ordinary_path(row["path"])
        output_name = ordinary_path(row["outputPath"])
        if metadata in seen:
            raise ValueError("repeated selected artifact declaration")
        seen.add(metadata)
        path = ordinary(root, output_name)
        if not path.exists():
            missing.append(metadata)
            continue
        declared = row["downloadHash"]
        size, hashes = hash_file(path, declared["algorithm"])
        if hashes[declared["algorithm"]] != declared["value"]:
            raise ValueError("local artifact differs from selected declaration: " + output_name)
        total += size
        if total > MAX_TOTAL or len(records) >= 2000:
            raise ValueError("artifact bundle exceeds capture bounds")
        digest = hashes["sha256"]
        records.append({"metadataPath": metadata, "outputPath": output_name, "sha256": digest, "size": size})
        paths[digest] = path
    if missing and not allow_partial:
        raise ValueError("missing selected artifacts (use --allow-partial only for explicit incomplete inspection): " + ", ".join(missing))
    if not records:
        raise ValueError("no selected artifact bytes to capture")
    manifest = {"schema": "axiom.artifacts.v1", "targetId": body["targetId"], "candidateId": body["candidateId"],
                "compositionId": composition["compositionId"], "side": composition["side"], "options": composition["options"],
                "artifacts": sorted(records, key=lambda row: row["metadataPath"])}
    raw_manifest = canonical(manifest)
    if len(raw_manifest) > 1024 * 1024:
        raise ValueError("artifact manifest exceeds bound")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".axiom-artifacts-", suffix=".zip", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w+b") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
            info = zipfile.ZipInfo("manifest.json", (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw_manifest)
            for digest, path in sorted(paths.items()):
                if not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ValueError("invalid artifact identity")
                info = zipfile.ZipInfo("blobs/" + digest, (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                hasher, size = hashlib.sha256(), 0
                with ordinary(root, str(path.relative_to(root))).open("rb") as source, archive.open(info, "w") as destination:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_ARTIFACT:
                            raise ValueError("artifact changed beyond byte bound")
                        hasher.update(chunk)
                        destination.write(chunk)
                if hasher.hexdigest() != digest:
                    raise ValueError("artifact changed during capture")
        if temporary.stat().st_size > MAX_TOTAL:
            raise ValueError("encoded artifact bundle exceeds bound")
        os.link(temporary, output)  # atomic no-clobber; never replace an existing candidate
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(output), "artifactBundleId": "axiom-artifacts:sha256:" + hashlib.sha256(raw_manifest).hexdigest(),
            "artifacts": len(records), "missing": sorted(missing), "bytes": total, "installedCompositionQualified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True, help="Exact-base target request with explicit composition side/options")
    parser.add_argument("--engine-home", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True, help="Explicit local root containing declared output paths; never auto-discovered")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    request = args.request.read_bytes()
    if len(request) > 1024 * 1024:
        raise ValueError("request exceeds byte bound")
    java = args.java_home.resolve(strict=True) / "bin/java"
    engine = args.engine_home.resolve(strict=True)
    completed = subprocess.run([str(java), "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main",
                                "target", "--target", str(args.target.resolve(strict=True))], input=request, capture_output=True,
                               timeout=45, env={"LANG": "C.UTF-8"}, check=True)
    print(json.dumps(capture(json.loads(completed.stdout), args.artifact_root, args.output, allow_partial=args.allow_partial), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
