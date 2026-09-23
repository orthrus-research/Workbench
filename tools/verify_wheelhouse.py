"""Standard-library verifier for a trusted native wheelhouse manifest."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import stat


class WheelhouseError(ValueError):
    pass


def verify(root: Path):
    root = root.absolute()
    if root.is_symlink() or not root.is_dir():
        raise WheelhouseError("wheelhouse must be a direct directory")
    manifest_path = root / "wheelhouse.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or not 0 < manifest_path.stat().st_size <= 1024 * 1024:
        raise WheelhouseError("missing direct wheelhouse manifest")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WheelhouseError("duplicate manifest key")
            result[key] = value
        return result
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "source_sha256", "selected_components", "native_versions", "target", "wheels", "qualified"} or manifest["format"] != "workbench-native-wheelhouse-v1" or manifest["qualified"] is not False:
        raise WheelhouseError("invalid native wheelhouse boundary")
    if not isinstance(manifest["source_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["source_sha256"]):
        raise WheelhouseError("source identity must bind the staged build input")
    records = manifest["wheels"]
    if not isinstance(manifest["target"], dict) or set(manifest["target"]) != {"python", "platform", "machine"} or any(not isinstance(value, str) or not value for value in manifest["target"].values()):
        raise WheelhouseError("invalid target identity")
    if not isinstance(records, list) or not 0 < len(records) <= 4096:
        raise WheelhouseError("empty wheelhouse")
    directory = root / "wheels"
    if directory.is_symlink() or not directory.is_dir():
        raise WheelhouseError("wheel directory must be direct")
    names, distributions = set(), set()
    for row in records:
        if not isinstance(row, dict) or set(row) != {"filename", "name", "version", "size", "sha256"}:
            raise WheelhouseError("invalid wheel record")
        filename = row["filename"]
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", filename) or filename in names:
            raise WheelhouseError("unsafe or duplicate wheel filename")
        if not isinstance(row["name"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["name"]) or row["name"] in distributions:
            raise WheelhouseError("invalid or duplicate distribution")
        if not isinstance(row["version"], str) or not re.fullmatch(r"[A-Za-z0-9_.+!]+", row["version"]):
            raise WheelhouseError("unsafe version")
        if type(row["size"]) is not int or not 0 < row["size"] <= 512 * 1024 * 1024 or not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
            raise WheelhouseError("invalid wheel size or digest")
        path = directory / filename
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise WheelhouseError(f"wheel differs: {filename}")
        names.add(filename)
        distributions.add(row["name"])
    if {path.name for path in directory.iterdir()} != names:
        raise WheelhouseError("wheelhouse contains missing or extra files")
    versions = {row["name"]: row["version"] for row in records}
    if not isinstance(manifest["native_versions"], dict) or not manifest["native_versions"] or any(not name.startswith("workbench-") or versions.get(name) != version for name, version in manifest["native_versions"].items()):
        raise WheelhouseError("native component lock differs")
    if {name for name in versions if name.startswith("workbench-")} != set(manifest["native_versions"]):
        raise WheelhouseError("native component closure contains undeclared Workbench wheels")
    pip_rows = [row for row in records if row["name"] == "pip"]
    if len(pip_rows) != 1 or (pip_rows[0]["version"], pip_rows[0]["filename"]) != ("26.1.2", "pip-26.1.2-py3-none-any.whl"):
        raise WheelhouseError("assembly requires the supported hash-locked pip bootstrap wheel")
    selected = manifest["selected_components"]
    if not isinstance(selected, list) or not selected or any(not isinstance(name, str) for name in selected) or len(selected) != len(set(selected)) or not set(selected) <= manifest["native_versions"].keys():
        raise WheelhouseError("selection is not in the native closure")
    expected = "".join(f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n" for row in records)
    lock = root / "requirements.lock"
    if lock.is_symlink() or not lock.is_file() or lock.read_text(encoding="utf-8") != expected:
        raise WheelhouseError("requirements lock differs from authenticated wheels")
    return manifest
