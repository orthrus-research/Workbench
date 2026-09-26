#!/usr/bin/env python3

"""Provision the exact ignored-local toolchains used by IDE validation."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import argparse
import json
from pathlib import Path
import platform
import re
import stat
import sys
from typing import Any

from core_run_custody import (
    admit_ide_toolchain_directory, allocate_ide_toolchain_stage,
    extract_ide_toolchain_archive, promote_ide_toolchain_directory,
    hold_ide_toolchain_directory,
    reject_existing_ide_toolchain_stage, review_ide_toolchain_stages_on_reuse,
)


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path(__file__).with_name("ide-toolchains-v1.json")
TOOLCHAIN_ROOT = ROOT / ".workbench/toolchains/ide-validation-v1"
DOWNLOAD_ROOT = ROOT / ".workbench/downloads/ide-validation-v1"


class ProvisionFailure(RuntimeError):
    pass


_SUFFIXES = {
    "java": ".tar.gz", "java_platform": ".tar.gz",
    "gradle": "-bin.zip", "node": ".tar.xz", "npm": ".tgz",
}


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


def provision_entry(
    entry: dict[str, str],
    *,
    suffix: str,
    extracted_root: str | None = None,
) -> Path:
    archive_root = entry["archive_root"]
    expected_root = extracted_root or archive_root
    if any(
        not isinstance(name, str) or name in {"", ".", ".."}
        or Path(name).name != name or "\\" in name or "\0" in name
        for name in (archive_root, expected_root)
    ) or (
        not isinstance(entry["archive_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", entry["archive_sha256"]) is None
    ):
        raise ProvisionFailure("IDE toolchain lock has an invalid extraction root or archive digest")
    destination = TOOLCHAIN_ROOT / archive_root
    marker = destination / ".workbench-provisioned-sha256"
    archive_format = "zip" if suffix.endswith(".zip") else "tar"
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
                    try:
                        review_ide_toolchain_stages_on_reuse(
                            destination.absolute(), entry["archive_sha256"],
                        )
                    except OSError as exc:
                        raise ProvisionFailure(str(exc)) from exc
                    archive = download(entry, suffix)
                    try:
                        admit_ide_toolchain_directory(
                            archive.absolute(), destination.absolute(),
                            archive_sha256=entry["archive_sha256"],
                            archive_size=entry["archive_size"],
                            extracted_root=expected_root,
                            archive_format=archive_format,
                        )
                    except OSError as exc:
                        raise ProvisionFailure(
                            f"existing IDE toolchain needs exact readback; retain for review: {destination}: {exc}"
                        ) from exc
                    return destination
            except (OSError, UnicodeError):
                pass
        raise ProvisionFailure(f"existing IDE toolchain differs from its lock; retain for review: {destination}")

    if TOOLCHAIN_ROOT.exists():
        prefix = archive_root + "."
        if any(member.name.startswith(prefix) for member in TOOLCHAIN_ROOT.iterdir()):
            raise ProvisionFailure(f"interrupted IDE toolchain extraction requires review: {destination}")
    try:
        reject_existing_ide_toolchain_stage(destination.absolute(), entry["archive_sha256"])
    except OSError as exc:
        raise ProvisionFailure(str(exc)) from exc

    archive = download(entry, suffix)
    try:
        stage_host, stage = allocate_ide_toolchain_stage(
            destination.absolute(), entry["archive_sha256"],
        )
        with stage_host.execution(stage):
            try:
                try:
                    extract_ide_toolchain_archive(
                        stage_host, stage, archive.absolute(),
                        archive_sha256=entry["archive_sha256"],
                        archive_size=entry["archive_size"],
                        extracted_root=expected_root,
                        archive_format=archive_format,
                    )
                except Exception as exc:
                    raise ProvisionFailure(
                        f"IDE extraction failed; retain stage for review: {stage.path}: {exc}"
                    ) from exc
                extracted = stage.path / expected_root
                if not extracted.is_dir():
                    raise ProvisionFailure(
                        f"archive lacks expected root {expected_root}; retain stage for review: {stage.path}"
                    )
                promote_ide_toolchain_directory(
                    extracted, destination,
                    (entry["archive_sha256"] + "\n").encode("ascii"),
                    stage_host=stage_host, stage_reference=stage,
                )
                admit_ide_toolchain_directory(
                    archive.absolute(), destination.absolute(),
                    archive_sha256=entry["archive_sha256"],
                    archive_size=entry["archive_size"],
                    extracted_root=expected_root, archive_format=archive_format,
                    stage_lease_id=stage.lease_id,
                )
            except BaseException as exc:
                try:
                    stage_host.retain(stage, outcome="failed")
                except (OSError, ValueError) as retention_error:
                    exc.add_note("Core IDE stage retention could not be recorded: " + str(retention_error))
                raise
            else:
                stage_host.retain(stage, outcome="completed")
    except ProvisionFailure:
        raise
    except (OSError, ValueError) as exc:
        raise ProvisionFailure(f"Core IDE toolchain stage or publication needs review: {exc}") from exc
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
    java_home = provision_entry(lock["java"], suffix=".tar.gz")
    java_platform_home = provision_entry(lock["java_platform"], suffix=".tar.gz")
    gradle_home = provision_entry(lock["gradle"], suffix="-bin.zip")
    return java_home, java_platform_home, gradle_home


def provision_node() -> Path:
    if platform.system() != "Linux" or platform.machine() not in {
        "x86_64",
        "AMD64",
    }:
        raise ProvisionFailure(
            "the current Node toolchain lock supports Linux x86_64 only"
        )
    return provision_entry(load_lock()["node"], suffix=".tar.xz")


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
        extracted_root="package",
    )


@contextmanager
def hold_provisioned_toolchains(selected: dict[str, Path]):
    """Hold exact Core admissions while Workbench clients use locked tools."""

    if not selected or not set(selected).issubset(_SUFFIXES):
        raise ProvisionFailure("IDE toolchain hold selection is unsupported")
    lock = load_lock()
    client_error = False
    try:
        with ExitStack() as held:
            for key in sorted(selected):
                entry = lock[key]
                destination = TOOLCHAIN_ROOT / entry["archive_root"]
                if selected[key] != destination:
                    raise ProvisionFailure("IDE toolchain hold target differs from the locked selection")
                suffix = _SUFFIXES[key]
                archive = download(entry, suffix)
                held.enter_context(hold_ide_toolchain_directory(
                    archive.absolute(), destination.absolute(),
                    archive_sha256=entry["archive_sha256"],
                    archive_size=entry["archive_size"],
                    extracted_root="package" if key == "npm" else entry["archive_root"],
                    archive_format="zip" if suffix.endswith(".zip") else "tar",
                ))
            try:
                yield
            except BaseException:
                client_error = True
                raise
    except OSError as exc:
        if client_error:
            raise
        raise ProvisionFailure(f"IDE toolchain hold needs review: {exc}") from exc


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
