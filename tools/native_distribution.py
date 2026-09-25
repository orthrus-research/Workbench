"""Build and authenticate target-specific native wheel assemblies.

Assemblies are dependency closures, not Python distributions. A manifest locks
every wheel by name, version, size and SHA-256. It describes build inputs, never
asserts release qualification. Installers must receive trusted manifests; hashes
detect corruption but do not authenticate a publisher.
"""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from component_versions import load_authority

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "workbench-native-wheelhouse-v1"
MANIFEST = "wheelhouse.json"
INSTALLER_PIP_VERSION = "26.1.2"


class DistributionError(ValueError):
    """An assembly or an input cannot be safely admitted."""


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv, *, cwd=None):
    subprocess.run([str(value) for value in argv], cwd=cwd, check=True)


def selected_components(components=None, *, suite=False, root=ROOT):
    _, inventory = load_authority(root)
    native = {name: row for name, row in inventory.items() if row["kind"] in {"python", "python-client"}}
    # Python presentation clients are selected explicitly. `--suite` remains
    # Core, modules and profiles, with no Textual dependency by default.
    requested = set(
        (name for name, row in native.items() if row["kind"] == "python")
        if suite else components or ["workbench-core"]
    )
    if not requested or requested - native.keys():
        raise DistributionError("unknown or non-Python component selection: " + ", ".join(sorted(requested - native.keys())))
    selected = set(requested)
    pending = list(selected)
    while pending:
        name = pending.pop()
        for text in native[name]["dependencies"]:
            requirement = Requirement(text)
            dependency = canonicalize_name(requirement.name)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            if dependency.startswith("workbench-") and dependency not in native:
                raise DistributionError(f"{name}: missing native dependency {dependency}")
            if dependency in native:
                if Version(native[dependency]["version"]) not in requirement.specifier:
                    raise DistributionError(f"{name}: incompatible local dependency {requirement}")
                if dependency not in selected:
                    selected.add(dependency)
                    pending.append(dependency)
    return sorted(requested), [native[name] for name in sorted(selected)]


def wheel_record(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 512 * 1024 * 1024:
        raise DistributionError(f"wheel is not a bounded direct file: {path.name}")
    name, version, _, _ = parse_wheel_filename(path.name)
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > 2 * 1024 * 1024 * 1024:
            raise DistributionError(f"duplicate or excessive wheel members: {path.name}")
        for entry in entries:
            relative = Path(entry.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise DistributionError(f"unsafe wheel member: {entry.filename}")
        metadata_names = [value for value in names if value.endswith('.dist-info/METADATA')]
        if len(metadata_names) != 1:
            raise DistributionError(f"wheel requires exactly one metadata record: {path.name}")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        if canonicalize_name(metadata["Name"]) != name or Version(metadata["Version"]) != version:
            raise DistributionError(f"wheel identity differs: {path.name}")
    return {"filename": path.name, "name": str(name), "version": str(version), "size": path.stat().st_size, "sha256": _digest(path)}


def lock_text(records):
    return "".join(f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n" for row in records)


def source_identity(root=ROOT):
    """Use the same repository-input identity as a clean staged native build."""
    digest = hashlib.sha256()
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).split(b"\0")
    for raw in sorted(set(tracked) - {b""}):
        source = root / Path(os.fsdecode(raw))
        if source.is_symlink():
            raise DistributionError("source input must not be a symlink")
        if source.is_file():
            digest.update(raw + b"\0" + bytes.fromhex(_digest(source)))
    return digest.hexdigest()


def current_assembly(wheelhouse: Path, *, root=ROOT):
    manifest = verify(wheelhouse)
    target = {"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": sys.platform, "machine": platform.machine()}
    if manifest["target"] != target:
        raise DistributionError("reused wheelhouse target differs from this Python/OS/architecture")
    if manifest["source_sha256"] != source_identity(root):
        raise DistributionError("reused wheelhouse source differs from the current repository inputs")
    return manifest


def _write_assembly(output, manifest, *, root):
    (output / "requirements.lock").write_text(lock_text(manifest["wheels"]), encoding="utf-8")
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name in ("install_workbench.py", "verify_wheelhouse.py"):
        (output / name).write_bytes((root / "tools" / name).read_bytes())
    for name in ("LICENSE", "NOTICE.md"):
        (output / name).write_bytes((root / name).read_bytes())


def derive(wheelhouse: Path, output: Path, components=None, *, suite=False, root=ROOT):
    """Select an offline dependency closure from exact already-built wheels."""
    manifest = current_assembly(wheelhouse, root=root)
    requested, selected = selected_components(components, suite=suite, root=root)
    expected_native = {row["id"]: row["version"] for row in selected}
    if any(manifest["native_versions"].get(name) != version for name, version in expected_native.items()):
        raise DistributionError("reused native versions differ from selected authorities")
    records = {row["name"]: row for row in manifest["wheels"]}
    required = {}
    pending = [Requirement(name) for name in (*requested, "pip")]
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        row = records.get(name)
        if requirement.url or row is None or Version(row["version"]) not in requirement.specifier:
            raise DistributionError(f"reused wheelhouse cannot satisfy {requirement}")
        extras = set(requirement.extras) | {""}
        previous = required.get(name, set())
        if name in required and extras <= previous:
            continue
        required[name] = previous | extras
        wheel = wheelhouse / "wheels" / row["filename"]
        if wheel_record(wheel) != row:
            raise DistributionError(f"reused wheel changed before dependency inspection: {name}")
        with zipfile.ZipFile(wheel) as archive:
            metadata_name, = (entry for entry in archive.namelist() if entry.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(archive.read(metadata_name))
        for text in metadata.get_all("Requires-Dist", []):
            dependency = Requirement(text)
            if dependency.marker is None or any(dependency.marker.evaluate({"extra": extra}) for extra in required[name]):
                pending.append(dependency)
    observed_native = {name: records[name]["version"] for name in required if name.startswith("workbench-")}
    if observed_native != expected_native:
        raise DistributionError("wheel metadata native dependency closure differs from selected authorities")
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise DistributionError("derived output must be a new directory")
    (output / "wheels").mkdir(parents=True)
    selected_records = [records[name] for name in sorted(required)]
    for row in selected_records:
        shutil.copyfile(wheelhouse / "wheels" / row["filename"], output / "wheels" / row["filename"])
    result = {**manifest, "selected_components": requested, "native_versions": expected_native, "wheels": selected_records}
    _write_assembly(output, result, root=root)
    if verify(output) != result or current_assembly(wheelhouse, root=root) != manifest:
        raise DistributionError("wheelhouse changed during closure derivation")
    return result


def build(output: Path, components=None, *, suite=False, root=ROOT, command_runner=_run):
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise DistributionError("build output must be a new directory")
    requested, selected = selected_components(components, suite=suite, root=root)
    output.mkdir(parents=True)
    wheels = output / "wheels"
    wheels.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix="wb-") as scratch:
            native = Path(scratch) / "w"
            native.mkdir()
            # Preserve each component's own layout without nesting a second
            # repository path or copying unrelated clients/probes into its build.
            # In particular, Windows may still enforce legacy path lengths.
            sources = {row["id"]: (Path(row["manifest"]).parent, Path(scratch) / str(index))
                       for index, row in enumerate(selected)}
            for _, destination in sources.values():
                destination.mkdir()
            source_identity = hashlib.sha256()
            tracked = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root).split(b"\0")
            for raw in sorted(set(tracked) - {b""}):
                relative = Path(os.fsdecode(raw))
                original = root / relative
                if original.is_symlink():
                    raise DistributionError(f"source input must not be a symlink: {relative}")
                if original.is_file():
                    content = original.read_bytes()
                    source_identity.update(raw + b"\0" + hashlib.sha256(content).digest())
                    for owner, destination in sources.values():
                        if relative.is_relative_to(owner):
                            target = destination / relative.relative_to(owner)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(content)
            # Build just the selected in-repository closure, never a second suite/Core wheel.
            for row in selected:
                command_runner([sys.executable, "-I", "-m", "pip", "--isolated", "wheel", "--no-deps", "--wheel-dir", native, sources[row["id"]][1]])
            # Direct wheel inputs prevent an index package with the same name and
            # version from replacing our just-built local package.
            inputs = sorted(native.glob("*.whl"))
            command_runner([sys.executable, "-I", "-m", "pip", "--isolated", "download", "--only-binary=:all:", "--dest", wheels, f"pip=={INSTALLER_PIP_VERSION}", *inputs])
        records = sorted((wheel_record(path) for path in wheels.iterdir()), key=lambda row: row["name"])
        observed = {row["name"]: row["version"] for row in records}
        if any(observed.get(row["id"]) != row["version"] for row in selected):
            raise DistributionError("built native versions differ from selected authorities")
        manifest = {"format": FORMAT, "source_sha256": source_identity.hexdigest(), "selected_components": requested,
                    "native_versions": {row["id"]: row["version"] for row in selected},
                    "target": {"python": f"{sys.version_info.major}.{sys.version_info.minor}", "platform": sys.platform, "machine": platform.machine()},
                    "wheels": records, "qualified": False}
        _write_assembly(output, manifest, root=root)
        return manifest
    except BaseException:
        # Deliberately retain partial artifacts for inspection. Never claim success.
        raise


def verify(wheelhouse: Path):
    from verify_wheelhouse import verify as verify_standalone
    return verify_standalone(wheelhouse)
