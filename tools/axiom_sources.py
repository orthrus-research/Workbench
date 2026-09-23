#!/usr/bin/env python3
"""Verify immutable Axiom references against explicit clean upstream checkouts."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from build_axiom_target import git as capture_git

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "modules/axiom/sources"


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments]).decode().strip()


def verify(roots):
    lock = json.loads((SOURCE / "supersymmetry.lock.json").read_text())
    for repository in lock["repositories"]:
        root = roots[repository["id"]]
        if git(root, "rev-parse", "HEAD") != repository["commit"] or git(root, "rev-parse", "HEAD^{tree}") != repository["tree"]:
            raise ValueError(repository["id"] + ": checkout differs from locked revision")
        if git(root, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(repository["id"] + ": tracked source is modified")
    for entry in lock["references"]:
        root = roots[entry["repository"]]
        path = root / entry["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("not an ordinary source file: " + entry["path"])
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != entry["sha256"] or git(root, "rev-parse", "HEAD:" + entry["path"]) != entry["git_blob"]:
            raise ValueError("source identity changed: " + entry["id"])
    references = {(entry["repository"], entry["path"]): entry for entry in lock["references"]}
    rules = json.loads((SOURCE / "rules.json").read_text())["rules"]
    for rule in rules:
        if (rule["owner"], rule["path"]) not in references:
            raise ValueError("rule is not tied to a hashed source reference: " + rule["id"])
    fixture = ROOT / "modules/axiom/tests/fixtures/Coolants.groovy"
    original = roots["supersymmetry"] / "groovy/postInit/chemistry/organic_chemistry/Coolants.groovy"
    if fixture.read_bytes() != original.read_bytes():
        raise ValueError("Coolants fixture differs from the locked source bytes")
    return {"repositories": len(roots), "sourceReferences": len(references), "mappedRules": len(rules),
            "sourceVerified": True, "installedCompositionQualified": False}


def provision(destination):
    """Fetch exact public source commits into a new owned tree; no floating refs."""
    lock = json.loads((SOURCE / "supersymmetry.lock.json").read_text())
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
        raise ValueError("source provision destination must be new and ordinary")
    import re
    for row in lock["repositories"]:
        if not re.fullmatch(r"[a-z0-9-]+", row["id"]) or not re.fullmatch(r"[0-9a-f]{40}", row["commit"]):
            raise ValueError("invalid pinned source identity")
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", row["url"]):
            raise ValueError("source provisioning admits only explicit public GitHub HTTPS repositories")
    destination.mkdir(parents=True)
    roots = {}
    for row in lock["repositories"]:
        root = destination / row["id"]
        root.mkdir()
        capture_git(root, "init", "--quiet")
        print("Fetching pinned source: " + row["id"] + " " + row["commit"], flush=True)
        capture_git(root, "-c", "credential.helper=", "-c", "core.hooksPath=" + str(root / ".git/axiom-disabled-hooks"),
                    "fetch", "--no-tags", "--depth=1", row["url"], row["commit"])
        capture_git(root, "-c", "core.hooksPath=" + str(root / ".git/axiom-disabled-hooks"), "checkout", "--quiet", "--detach", row["commit"])
        roots[row["id"]] = root
    return roots


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("supersymmetry", "gtceu", "susy-core", "groovyscript"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--provision", type=Path, help="Fetch the four exact commits into a NEW ignored-local source directory")
    args = parser.parse_args(argv)
    explicit = {name.replace("_", "-"): path for name, path in vars(args).items() if name != "provision"}
    if args.provision:
        if any(explicit.values()):
            parser.error("--provision cannot be combined with explicit source roots")
        roots = provision(args.provision)
    else:
        if not all(explicit.values()):
            parser.error("supply all four explicit roots or --provision")
        roots = {name: path.resolve(strict=True) for name, path in explicit.items()}
    print(json.dumps(verify(roots), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
