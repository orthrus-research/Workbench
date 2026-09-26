#!/usr/bin/env python3

"""Provision the exact ignored-local toolchains used by IDE validation."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path(__file__).with_name("ide-toolchains-v1.json")
TOOLCHAIN_ROOT = ROOT / ".workbench/toolchains/ide-validation-v1"
DOWNLOAD_ROOT = ROOT / ".workbench/downloads/ide-validation-v1"


class ProvisionFailure(RuntimeError):
    pass


def load_lock() -> dict[str, Any]:
    value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if value.get("format") != "workbench-ide-validation-toolchain-lock-v1":
        raise ProvisionFailure("unexpected IDE toolchain lock format")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(entry: dict[str, Any], suffix: str) -> Path:
    """Use Core's bounded artifact cache, adopting only exact legacy bytes."""

    cache_root = DOWNLOAD_ROOT / "artifacts" / "sha256"
    for component in (cache_root, *cache_root.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ProvisionFailure("IDE archive cache traverses a redirect")
    legacy = DOWNLOAD_ROOT / f"{entry['archive_root']}{suffix}"
    source_url = entry["archive_url"]
    if legacy.exists() or legacy.is_symlink():
        if (legacy.is_symlink() or not legacy.is_file()
                or legacy.stat().st_size != entry["archive_size"]
                or sha256(legacy) != entry["archive_sha256"]):
            raise ProvisionFailure(
                f"existing IDE archive differs from its lock; retain for review: {legacy}"
            )
        source_url = legacy.absolute().as_uri()

    source_root = Path(__file__).resolve().parents[1]
    for source in (source_root / "api/src", source_root / "core/src"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
    from workbench_core.artifact_store import ArtifactStoreError, fetch_verified_artifact

    try:
        archive, _status = fetch_verified_artifact(
            url=source_url, expected_sha256=entry["archive_sha256"],
            expected_size=entry["archive_size"], state_root=DOWNLOAD_ROOT,
            label="IDE validation toolchain", timeout_seconds=60,
        )
    except ArtifactStoreError as exc:
        raise ProvisionFailure(f"IDE archive acquisition needs review: {exc}") from exc
    return archive


def validate_member_name(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ProvisionFailure(f"unsafe archive member: {name}")


def extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            validate_member_name(member.name)
        bundle.extractall(destination, filter="data")


def extract_tar_xz(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:xz") as bundle:
        for member in bundle.getmembers():
            validate_member_name(member.name)
        bundle.extractall(destination, filter="data")


def extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            validate_member_name(member.filename)
        bundle.extractall(destination)
        for member in bundle.infolist():
            mode = (member.external_attr >> 16) & 0o777
            if mode:
                (destination / member.filename).chmod(mode)


def provision_entry(
    entry: dict[str, str],
    *,
    suffix: str,
    extractor: Any,
    extracted_root: str | None = None,
) -> Path:
    destination = TOOLCHAIN_ROOT / entry["archive_root"]
    marker = destination / ".workbench-provisioned-sha256"
    for component in (TOOLCHAIN_ROOT, *TOOLCHAIN_ROOT.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise ProvisionFailure("IDE toolchain destination traverses a redirect")
    if destination.is_symlink() or getattr(destination, "is_junction", lambda: False)():
        raise ProvisionFailure(f"IDE toolchain destination is redirected; retain for review: {destination}")
    if destination.exists():
        if destination.is_dir() and not marker.is_symlink() and not getattr(marker, "is_junction", lambda: False)():
            try:
                marker_info = marker.lstat()
                if (
                    stat.S_ISREG(marker_info.st_mode)
                    and marker.read_text(encoding="ascii").strip() == entry["archive_sha256"]
                ):
                    return destination
            except (OSError, UnicodeError):
                pass
        raise ProvisionFailure(f"existing IDE toolchain differs from its lock; retain for review: {destination}")

    archive = download(entry, suffix)
    TOOLCHAIN_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=entry["archive_root"] + ".", dir=TOOLCHAIN_ROOT)
    )
    try:
        extractor(archive, temporary)
        expected_root = extracted_root or entry["archive_root"]
        extracted = temporary / expected_root
        if not extracted.is_dir():
            raise ProvisionFailure(
                f"archive lacks expected root {expected_root}"
            )
        if destination.exists() or destination.is_symlink():
            raise ProvisionFailure(f"IDE toolchain destination appeared during extraction; retain for review: {destination}")
        extracted.replace(destination)
        marker.write_text(entry["archive_sha256"] + "\n", encoding="ascii")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return destination


def provision() -> tuple[Path, Path, Path]:
    if platform.system() != "Linux" or platform.machine() not in {
        "x86_64",
        "AMD64",
    }:
        raise ProvisionFailure(
            "the current IDE toolchain lock supports Linux x86_64 only"
        )
    lock = load_lock()
    java_home = provision_entry(
        lock["java"], suffix=".tar.gz", extractor=extract_tar
    )
    java_platform_home = provision_entry(
        lock["java_platform"], suffix=".tar.gz", extractor=extract_tar
    )
    gradle_home = provision_entry(
        lock["gradle"], suffix="-bin.zip", extractor=extract_zip
    )
    return java_home, java_platform_home, gradle_home


def provision_node() -> Path:
    if platform.system() != "Linux" or platform.machine() not in {
        "x86_64",
        "AMD64",
    }:
        raise ProvisionFailure(
            "the current Node toolchain lock supports Linux x86_64 only"
        )
    return provision_entry(
        load_lock()["node"], suffix=".tar.xz", extractor=extract_tar_xz
    )


def provision_npm() -> Path:
    if platform.system() != "Linux" or platform.machine() not in {
        "x86_64",
        "AMD64",
    }:
        raise ProvisionFailure(
            "the current npm toolchain lock supports Linux x86_64 only"
        )
    return provision_entry(
        load_lock()["npm"],
        suffix=".tgz",
        extractor=extract_tar,
        extracted_root="package",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-only", action="store_true", help="prepare the locked Node runtime for canonical Python checks")
    parser.add_argument("--path-file", type=Path, help="append the prepared Node bin directory to a CI path file")
    parser.add_argument("--physical-env-file", type=Path, help="write the locked Gradle/Java25 fixture inputs to a CI environment file")
    args = parser.parse_args(argv)
    if args.node_only:
        node_home = provision_node()
        if args.path_file:
            with args.path_file.open("a", encoding="utf-8") as output:
                output.write(str(node_home / "bin") + "\n")
        print(f"Node.js: {node_home}")
        return 0
    java_home, java_platform_home, gradle_home = provision()
    if args.physical_env_file:
        with args.physical_env_file.open("a", encoding="utf-8") as output:
            output.write(f"WORKBENCH_CLEANROOM_FIXTURE_GRADLEW={gradle_home / 'bin/gradle'}\n")
            output.write(f"WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME={java_platform_home}\n")
    node_home = provision_node()
    npm_home = provision_npm()
    print(f"Java control process: {java_home}")
    print(f"Java platform toolchain: {java_platform_home}")
    print(f"Gradle: {gradle_home}")
    print(f"Node.js: {node_home}")
    print(f"npm: {npm_home}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisionFailure as exc:
        print(f"PROVISION FAILED: {exc}")
        raise SystemExit(1)
