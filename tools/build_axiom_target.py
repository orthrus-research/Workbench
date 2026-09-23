#!/usr/bin/env python3
"""Capture a profile-owned immutable Axiom source target; no game or script execution.

Reads Git objects, not filtered working-tree files. Includes original Git tree
objects so the independent Java reader can prove selected-file membership and
selection completeness without Git or a source checkout. Never publishes.
"""

import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "modules/axiom/sources"
POLICY = ROOT / "profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/axiom-target-policy.json"
MAX_BYTES = 256 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def git(root, *args, stdin=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", str(root), *args], input=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, timeout=180, env=environment).stdout


def ordinary_path(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or path.as_posix() != name or
            any(part in {".", ".."} for part in path.parts) or "\\" in name or ":" in name or
            any(ord(character) < 32 for character in name)):
        raise ValueError("unsafe source path: " + repr(name))
    return name


def selected(name, policy):
    return name in policy["paths"] or any(name.startswith(prefix) for prefix in policy["prefixes"])


def objects(root, identifiers):
    identifiers = sorted(set(identifiers))
    if any(not re.fullmatch("[0-9a-f]{40}", value) for value in identifiers):
        raise ValueError("expected SHA-1 Git object identities")
    raw = git(root, "cat-file", "--batch", stdin=("\n".join(identifiers) + "\n").encode())
    if len(raw) > MAX_BYTES:
        raise ValueError("source object response exceeds capture bound")
    result, offset = {}, 0
    for expected in identifiers:
        end = raw.index(b"\n", offset)
        identifier, kind, length = raw[offset:end].decode("ascii").split()
        length = int(length)
        if length < 0 or length > 16 * 1024 * 1024 or identifier != expected or kind not in {"tree", "blob"}:
            raise ValueError("invalid or oversized Git object")
        data = raw[end + 1:end + 1 + length]
        if len(data) != length or raw[end + 1 + length:end + 2 + length] != b"\n":
            raise ValueError("truncated Git object response")
        if sha1(f"{kind} {length}\0".encode() + data).hexdigest() != identifier:
            raise ValueError("Git object identity differs")
        result[identifier] = kind, data
        offset = end + 2 + length
    if offset != len(raw):
        raise ValueError("unexpected Git object response tail")
    return result


def capture(roots, *, policy_raw=None, lock_raw=None, rules_raw=None):
    policy_raw = POLICY.read_bytes() if policy_raw is None else policy_raw
    lock_raw = (SOURCE / "supersymmetry.lock.json").read_bytes() if lock_raw is None else lock_raw
    rules_raw = (SOURCE / "rules.json").read_bytes() if rules_raw is None else rules_raw
    policy, lock, rules = (json.loads(raw) for raw in (policy_raw, lock_raw, rules_raw))
    if policy["schema"] != "axiom.target-policy.v1" or policy["profile"] != "supersymmetry":
        raise ValueError("unsupported profile target policy")
    if set(roots) != set(policy["repositories"]) or set(roots) != {row["id"] for row in lock["repositories"]}:
        raise ValueError("supply exactly the policy/lock repositories")
    archive = {"source-lock.json": lock_raw, "policy.json": policy_raw}
    repositories = []
    total = sum(map(len, archive.values()))
    for repository in lock["repositories"]:
        identifier = repository["id"]
        root = roots[identifier].resolve(strict=True)
        before = git(root, "rev-parse", "HEAD").decode().strip()
        if before != repository["commit"] or git(root, "rev-parse", "HEAD^{tree}").decode().strip() != repository["tree"]:
            raise ValueError(identifier + ": checkout differs from pinned source")
        if git(root, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(identifier + ": tracked checkout is dirty; overlays must be explicit")
        rows = []
        tree_ids = {repository["tree"]}
        for raw in git(root, "ls-tree", "-r", "-t", "-z", "HEAD").split(b"\0"):
            if not raw:
                continue
            header, name = raw.split(b"\t", 1)
            mode, kind, blob = header.decode("ascii").split()
            name = ordinary_path(name.decode("utf-8"))
            if kind == "tree":
                tree_ids.add(blob)
            elif selected(name, policy["repositories"][identifier]):
                if mode not in {"100644", "100755"} or kind != "blob":
                    raise ValueError("selected source is not an ordinary Git file: " + name)
                rows.append((name, blob))
        if len(rows) > 20000 or len(tree_ids) > 20000:
            raise ValueError("source inventory exceeds capture bounds")
        content = objects(root, tree_ids | {blob for _, blob in rows})
        for tree in tree_ids:
            key = "trees/" + tree
            if key not in archive:
                archive[key] = content[tree][1]
                total += len(archive[key])
        files = []
        for name, blob in sorted(rows):
            data = content[blob][1]
            digest = sha256(data).hexdigest()
            files.append({"path": name, "blob": blob, "sha256": digest, "size": len(data)})
            if "blobs/" + digest not in archive:
                archive["blobs/" + digest] = data
                total += len(data)
        if total > MAX_BYTES:
            raise ValueError("source package exceeds 256 MiB expanded bound")
        if git(root, "rev-parse", "HEAD").decode().strip() != before or git(root, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(identifier + ": source changed during capture")
        repositories.append({"id": identifier, "commit": repository["commit"], "tree": repository["tree"], "files": files})
    manifest = {"schema": "axiom.target.v1", "profile": policy["profile"],
                "bindingId": "axiom-target:sha256:" + sha256(canonical({"baseline": lock, "rules": rules})).hexdigest(),
                "sourceLockSha256": sha256(lock_raw).hexdigest(), "policySha256": sha256(policy_raw).hexdigest(),
                "repositories": repositories}
    archive["manifest.json"] = canonical(manifest)
    return archive


def write_candidate(destination, archive):
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("candidate must be new; existing files are never overwritten")
    if any(parent.is_symlink() for parent in destination.parents):
        raise ValueError("candidate parent must not be a symlink")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".axiom-target-", dir=destination.parent) as temporary:
        candidate = Path(temporary) / "target.zip"
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for name, data in sorted(archive.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                output.writestr(info, data)
        # Atomic no-clobber promotion on the same filesystem. A racing existing
        # destination wins; neither user files nor an earlier candidate is erased.
        os.link(candidate, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("supersymmetry", "gtceu", "susy-core", "groovyscript"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    archive = capture({key.replace("_", "-"): value for key, value in vars(args).items() if key != "output"})
    write_candidate(args.output, archive)
    manifest = json.loads(archive["manifest.json"])
    print(json.dumps({"targetId": "axiom-source-target:sha256:" + sha256(archive["manifest.json"]).hexdigest(),
                      "files": sum(len(row["files"]) for row in manifest["repositories"]),
                      "output": str(args.output), "wholePackParity": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
