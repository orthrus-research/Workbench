#!/usr/bin/env python3
"""Capture explicit platform inputs offline; Java alone selects libraries from original metadata."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from build_axiom_artifacts import MAX_ARTIFACT, MAX_TOTAL, hash_file, ordinary
from build_axiom_target import canonical, ordinary_path


def bounded_file(path, limit):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected an ordinary input file: " + str(path))
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input exceeds byte bound")
    return raw


def write_bundle(path, manifest, policy, bootstrap, root, blobs):
    """Write a new private candidate; recheck every library while copying it."""
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, raw in (("manifest.json", canonical(manifest)), ("policy.json", policy), ("bootstrap.zip", bootstrap)):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
        for digest, name in sorted(blobs.items()):
            info = zipfile.ZipInfo("blobs/" + digest, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            hasher, size = hashlib.sha256(), 0
            with ordinary(root, name).open("rb") as source, archive.open(info, "w") as destination:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARTIFACT:
                        raise ValueError("platform artifact changed beyond byte bound")
                    hasher.update(chunk)
                    destination.write(chunk)
            if hasher.hexdigest() != digest:
                raise ValueError("platform artifact changed during capture")
    if path.stat().st_size > MAX_TOTAL:
        raise ValueError("encoded platform bundle exceeds bound")


def inspected_body(value):
    if not isinstance(value, dict) or value.get("schema") != "axiom.result.v1" or value.get("operation") != "platform" or value.get("status") != "accepted":
        raise ValueError("platform capture requires an accepted Java metadata inspection")
    body = value["result"]
    if body.get("schema") != "axiom.platform-inspection.v1" or not body["metadata"].get("selectionComplete"):
        raise ValueError("platform declaration selection is incomplete")
    return body


def capture(policy_path, bootstrap_path, selection, root, output, inspect, *, allow_partial=False):
    policy = bounded_file(policy_path, 1024 * 1024)
    bootstrap = bounded_file(bootstrap_path, 16 * 1024 * 1024)
    root, output = Path(root).absolute(), Path(output).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("library root must be an explicit ordinary directory")
    if output.exists() or output.is_symlink():
        raise ValueError("platform candidate must be new")
    manifest = {"schema": "axiom.platform.v1", "profile": "cleanroom", "policySha256": hashlib.sha256(policy).hexdigest(),
                "bootstrapSha256": hashlib.sha256(bootstrap).hexdigest(), "selection": selection, "artifacts": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Same-filesystem private staging permits atomic no-clobber promotion only after Java verifies the final bundle.
    with tempfile.TemporaryDirectory(prefix=".axiom-platform-", dir=output.parent) as temporary:
        staging = Path(temporary)
        metadata_input = staging / "metadata.zip"
        write_bundle(metadata_input, manifest, policy, bootstrap, root, {})
        body = inspected_body(inspect(metadata_input))
        if body["policySha256"] != manifest["policySha256"] or body["bootstrapSha256"] != manifest["bootstrapSha256"] or body["metadata"]["selection"] != selection:
            raise ValueError("Java inspection differs from explicit platform inputs")
        missing, blobs, seen, total = [], {}, set(), 0
        for declaration in body["metadata"]["libraries"]:
            name = ordinary_path(declaration["path"])
            if name in seen:
                raise ValueError("repeated selected platform storage path")
            seen.add(name)
            path = ordinary(root, name)
            if not path.exists():
                missing.append(name)
                continue
            size, hashes = hash_file(path, "sha1")
            if size != declaration["size"] or hashes["sha1"] != declaration["sha1"] or \
                    declaration.get("policySha256", hashes["sha256"]) != hashes["sha256"]:
                raise ValueError("local library differs from original platform declaration: " + name)
            total += size
            if total > MAX_TOTAL or len(manifest["artifacts"]) >= 2000:
                raise ValueError("platform capture exceeds artifact bounds")
            manifest["artifacts"].append({"path": name, "sha256": hashes["sha256"], "size": size})
            blobs[hashes["sha256"]] = name
        if missing and not allow_partial:
            raise ValueError("missing platform libraries (explicit --allow-partial permits incomplete inspection only): " + ", ".join(missing))
        manifest["artifacts"].sort(key=lambda row: row["path"])
        candidate = staging / "candidate.zip"
        write_bundle(candidate, manifest, policy, bootstrap, root, blobs)
        verified = inspected_body(inspect(candidate))
        expected_id = "axiom-platform:sha256:" + hashlib.sha256(canonical(manifest)).hexdigest()
        if verified["platformId"] != expected_id or verified["missingArtifacts"] != missing or not verified["providedArtifactBytesVerified"]:
            raise ValueError("final Java inspection does not bind the captured platform")
        os.link(candidate, output)
    return {"path": str(output), "platformId": expected_id, "artifacts": len(manifest["artifacts"]), "missing": missing,
            "bytes": total, "installedCompositionQualified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, required=True, help="Profile-owned native platform lock")
    parser.add_argument("--bootstrap", type=Path, required=True, help="Original bootstrap ZIP, not an exported runtime")
    parser.add_argument("--library-root", type=Path, required=True, help="Explicit local Maven-layout byte inputs")
    parser.add_argument("--side", required=True)
    parser.add_argument("--os", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--java-major", type=int, required=True, help="Target's Java major, distinct from the Axiom worker's JDK")
    parser.add_argument("--engine-home", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    java = args.java_home.resolve(strict=True) / "bin/java"
    engine = args.engine_home.resolve(strict=True)

    def inspect(path):
        process = subprocess.run([str(java), "-Xmx256m", "-cp", str(engine / "lib/*"), "research.orthrus.axiom.Main",
                                  "platform", "--platform", str(path)], input=b"", capture_output=True, timeout=45, env={"LANG": "C.UTF-8"})
        if process.returncode:
            raise ValueError("Java platform inspection failed: " + process.stdout.decode("utf-8", errors="replace")[:4096])
        return json.loads(process.stdout)

    selection = {"side": args.side, "os": args.os, "architecture": args.architecture, "javaMajor": args.java_major}
    result = capture(args.policy, args.bootstrap, selection, args.library_root, args.output, inspect, allow_partial=args.allow_partial)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
