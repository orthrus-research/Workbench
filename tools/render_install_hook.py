#!/usr/bin/env python3
"""Bind the one-command Linux installer to one exact assembled release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlsplit

from assemble_install_bundle import BundleError, _check_output_location, _direct_file, verify_bundle_archive
from build_tree_custody import publish_build_tree


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "packaging/installer/install-linux-x64.sh.in"
PYTHON_PIN = ROOT / "packaging/installer/python-linux-x64.json"
TARGET = {"python": "3.14", "platform": "linux", "machine": "x86_64"}
REPOSITORY_RELEASES = "https://github.com/orthrus-research/Workbench/releases/download"


class HookError(ValueError):
    """The hook cannot be bound to these release inputs."""


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _render_into(descriptor_path: Path, output: Path, *, python_pin: Path = PYTHON_PIN) -> dict:
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    pin = json.loads(python_pin.read_text(encoding="utf-8"))
    descriptor_format = descriptor.get("format")
    if (descriptor_format not in {
            "workbench-install-bundle-descriptor-v1",
            "workbench-install-bundle-descriptor-v2",
        } or descriptor.get("qualified") is not False):
        raise HookError("bundle descriptor must describe an unqualified assembled candidate")
    edition = ("full-suite" if descriptor_format == "workbench-install-bundle-descriptor-v1"
               else descriptor.get("edition"))
    if edition not in {"full-suite", "supersymmetry-client"} or (
        descriptor_format == "workbench-install-bundle-descriptor-v2"
        and edition != "supersymmetry-client"
    ):
        raise HookError("unsupported install-bundle edition")
    if descriptor.get("target") != TARGET or pin.get("target") != TARGET or pin.get("format") != "workbench-python-bootstrap-v1":
        raise HookError("bundle or managed Python target differs from Linux x64/Python 3.14")
    tag = descriptor.get("release_tag")
    filename = descriptor.get("archive_filename")
    directory = descriptor.get("bundle_directory")
    sha256 = descriptor.get("archive_sha256")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tag):
        raise HookError("invalid release tag")
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz", filename):
        raise HookError("invalid archive filename")
    if directory != "workbench-linux-x64-py314":
        raise HookError("unexpected bundle root directory")
    if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise HookError("invalid archive digest")
    archive = descriptor_path.parent / filename
    if archive.is_symlink() or not archive.is_file() or digest(archive) != sha256:
        raise HookError("descriptor does not bind the actual archive bytes")
    verify_bundle_archive(archive, descriptor)
    version = pin.get("version")
    runtime_id = pin.get("runtime_id")
    url = pin.get("url")
    python_sha256 = pin.get("sha256")
    if not isinstance(version, str) or not re.fullmatch(r"3\.14\.\d+", version):
        raise HookError("invalid managed Python version")
    if not isinstance(runtime_id, str) or not re.fullmatch(r"cpython-3\.14\.\d+\+[0-9]{8}", runtime_id):
        raise HookError("invalid managed Python runtime identity")
    if not isinstance(url, str):
        raise HookError("invalid managed Python URL")
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc) != ("https", "github.com") or not parts.path.startswith("/astral-sh/python-build-standalone/releases/download/") or parts.query or parts.fragment or "'" in url:
        raise HookError("managed Python must come from a fixed upstream release URL")
    if not isinstance(python_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", python_sha256):
        raise HookError("invalid managed Python digest")
    values = {
        "BUNDLE_EDITION": edition,
        "RELEASE_TAG": tag,
        "BUNDLE_URL": f"{REPOSITORY_RELEASES}/{quote(tag)}/{quote(filename)}",
        "BUNDLE_SHA256": sha256,
        "BUNDLE_DIRECTORY": directory,
        "PYTHON_URL": url,
        "PYTHON_SHA256": python_sha256,
        "PYTHON_VERSION": version,
        "PYTHON_RUNTIME_ID": runtime_id,
    }
    script = TEMPLATE.read_text(encoding="utf-8")
    for key, value in values.items():
        marker = f"@{key}@"
        if script.count(marker) != 1 or "'" in value or "\n" in value:
            raise HookError(f"invalid hook substitution: {key}")
        script = script.replace(marker, value)
    if re.search(r"@[A-Z_]+@", script):
        raise HookError("unbound hook value")
    output = output.absolute()
    checksums = output.parent / "SHA256SUMS"
    if output.exists() or output.is_symlink() or checksums.exists() or checksums.is_symlink():
        raise HookError("hook and checksum outputs must be new")
    output.write_text(script, encoding="utf-8")
    output.chmod(0o755)
    try:
        subprocess.run(["sh", "-n", str(output)], check=True, capture_output=True)
        hook_sha256 = digest(output)
        checksums.write_text(f"{sha256}  {filename}\n{hook_sha256}  {output.name}\n", encoding="utf-8")
    except BaseException:
        output.unlink(missing_ok=True)
        checksums.unlink(missing_ok=True)
        raise
    return {"hook": str(output), "hook_sha256": hook_sha256, "archive": str(archive), "archive_sha256": sha256, "checksums": str(checksums)}


def _verify_output(directory: Path, result: dict, hook_name: str) -> None:
    if {entry.name for entry in directory.iterdir()} != {hook_name, "SHA256SUMS"}:
        raise HookError("install hook output has missing or extra files")
    hook = _direct_file(directory / hook_name, limit=1024 * 1024)
    checksums = _direct_file(directory / "SHA256SUMS", limit=1024 * 1024)
    if digest(hook) != result["hook_sha256"]:
        raise HookError("install hook changed during Core publication")
    expected = (f'{result["archive_sha256"]}  {Path(result["archive"]).name}\n'
                f'{result["hook_sha256"]}  {hook_name}\n')
    if checksums.read_text(encoding="utf-8") != expected:
        raise HookError("install hook checksums changed during Core publication")
    subprocess.run(["sh", "-n", str(hook)], check=True, capture_output=True)


def render_managed(descriptor_path: Path, output: Path, *, python_pin: Path = PYTHON_PIN,
                   configuration_home: Path | None = None):
    """Publish the hook and checksum as a separate exact Core-managed tree."""
    descriptor_path = _direct_file(Path(descriptor_path), limit=1024 * 1024)
    output = Path(output).absolute()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.sh", output.name):
        raise HookError("invalid install hook filename")
    directory = output.parent
    if directory.exists() or directory.is_symlink() or any(parent.is_symlink() for parent in directory.parents):
        raise HookError("hook output must be in a new direct directory")
    _check_output_location(directory, ROOT)
    def produce(staged: Path) -> dict:
        staged.mkdir(parents=True)
        return _render_into(descriptor_path, staged / output.name, python_pin=python_pin)

    raw, reference = publish_build_tree(
        directory,
        produce,
        lambda path, result: _verify_output(path, result, output.name),
        lambda path, _result: "workbench-install-hook-v1:sha256:"
        + digest(path / "SHA256SUMS"),
        owner_id="install-hook-build",
        configuration_home=configuration_home,
    )
    result = {**raw, "hook": str(reference.path / output.name),
              "checksums": str(reference.path / "SHA256SUMS")}
    return result, reference


def render(descriptor_path: Path, output: Path, *, python_pin: Path = PYTHON_PIN,
           configuration_home: Path | None = None) -> dict:
    result, _reference = render_managed(
        descriptor_path, output, python_pin=python_pin,
        configuration_home=configuration_home,
    )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-pin", type=Path, default=PYTHON_PIN)
    args = parser.parse_args(argv)
    try:
        result, reference = render_managed(
            args.descriptor, args.output, python_pin=args.python_pin,
        )
    except (HookError, BundleError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"install hook rendering failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({**result, "artifact_tree_id": reference.tree_id,
                      "artifact_path": str(reference.path)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
