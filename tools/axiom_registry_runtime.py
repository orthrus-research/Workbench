"""Provision or verify profile-pinned registry utilities for native tests; never bundle Minecraft."""

import json
import os
from pathlib import Path
import tempfile
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / 'api/src', ROOT / 'core/src'):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
from workbench_core.artifact_store import fetch_verified_artifact, ArtifactStoreError

from axiom_runtime import checked_path

POLICY = ROOT / "profiles/platforms/cleanroom/registry-runtime.json"


def ordinary_root(root):
    root = root.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("indirect registry runtime root")
    return root


def load_policy():
    policy = json.loads(POLICY.read_bytes())
    if policy.get("schema") != "axiom.registry-runtime.v1" or policy.get("profile") != "cleanroom":
        raise ValueError("unknown registry runtime policy")
    if not policy.get("runtimeFiles") or len({row["path"] for row in policy["runtimeFiles"]}) != len(policy["runtimeFiles"]):
        raise ValueError("empty or duplicate registry runtime input")
    return policy


def verify(root):
    root = ordinary_root(root)
    policy = load_policy()
    for row in policy["runtimeFiles"]:
        checked_path(root, row)
    return root


def provision(root):
    root = ordinary_root(root)
    policy = load_policy()
    for row in policy["runtimeFiles"]:
        relative = Path(row["path"])
        if relative.is_absolute() or relative.as_posix() != row["path"] or ".." in relative.parts or "\\" in row["path"]:
            raise ValueError("unsafe registry artifact path")
        path = root / relative
        ordinary_root(path)
        if path.exists():
            checked_path(root, row)  # Changed cache files are not overwritten.
            continue
        if not row["url"].startswith(("https://piston-data.mojang.com/v1/objects/", "https://repo.maven.apache.org/maven2/")):
            raise ValueError("unadmitted registry artifact origin")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cached, _ = fetch_verified_artifact(url=row['url'], expected_sha256=row['sha256'],
                expected_size=row['size'], state_root=root / '.workbench', label='Axiom registry input')
        except ArtifactStoreError as exc:
            raise ValueError(str(exc)) from exc
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".axiom-download-") as temporary:
            with cached.open('rb') as source:
                shutil.copyfileobj(source, temporary, 65536)
            temporary.flush()
            os.link(temporary.name, path)  # Atomic no-clobber promotion of verified bytes.
    return verify(root)
