#!/usr/bin/env python3
"""Profile-owned validation and build planning for the frozen Cleanroom fixture."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any

from workbench_api.resources import repository_root

from . import profile

PROFILE_API_VERSION = 1

ROOT = repository_root(__file__)
PROFILE_ROOT = profile().root
FIXTURE = PROFILE_ROOT / "fixtures/generic-mod-daily-loop"
LOCK = FIXTURE / "fixture-lock-v1.json"
CLEANUP_INIT = PROFILE_ROOT / "tools/clean_generic_mod_fixture.gradle"


def entry_point_path() -> Path:
    """Packaged CLI bridge used by Core's exact fixture command catalog."""

    return PROFILE_ROOT / "tools/run_generic_mod_fixture_build.py"


GENERATED_PARTS = frozenset({".gradle", "__pycache__", "build", "out"})
GENERATED_SUFFIXES = frozenset({".class", ".jar", ".pyc"})
MAX_LOCK_BYTES = 2 * 1024 * 1024
MAX_FIXTURE_FILE_BYTES = 16 * 1024 * 1024
MAX_TOOL_RECORD_BYTES = 2 * 1024 * 1024


class FixtureBuildError(RuntimeError):
    """The exact fixture or local build toolchain failed preflight."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _reject_symlink_components(path: Path, *, label: str) -> None:
    candidate = path.absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise FixtureBuildError(f"{label} is unavailable: {path}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise FixtureBuildError(
                f"{label} contains a symlink component: {current}"
            )


def _ordinary_file(path: Path, *, label: str, executable: bool = False) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        raise FixtureBuildError(f"{label} must be an absolute path")
    _reject_symlink_components(candidate, label=label)
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise FixtureBuildError(f"{label} is unavailable: {candidate}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FixtureBuildError(f"{label} must be an ordinary non-symlink file")
    if executable and not os.access(candidate, os.X_OK):
        raise FixtureBuildError(f"{label} is not executable: {candidate}")
    return candidate.resolve(strict=True)


def _read_ordinary_bytes(path: Path, *, label: str, limit: int) -> bytes:
    ordinary = _ordinary_file(path, label=label)
    before = ordinary.stat()
    if before.st_size > limit:
        raise FixtureBuildError(f"{label} exceeds the {limit}-byte limit")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(ordinary, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise FixtureBuildError(f"{label} is not an ordinary file")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise FixtureBuildError(f"{label} exceeds the {limit}-byte limit")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise FixtureBuildError(f"{label} changed while it was read")
    return raw


def _validate_java_home(path: Path) -> Path:
    home = path.expanduser()
    if not home.is_absolute():
        raise FixtureBuildError("Java home must be an absolute ordinary directory")
    _reject_symlink_components(home, label="Java home")
    if not home.is_dir():
        raise FixtureBuildError("Java home must be an absolute ordinary directory")
    release = _ordinary_file(home / "release", label="Java release record")
    try:
        text = _read_ordinary_bytes(
            release,
            label="Java release record",
            limit=MAX_TOOL_RECORD_BYTES,
        ).decode("utf-8")
    except UnicodeError as exc:
        raise FixtureBuildError("Java release record is not UTF-8") from exc
    match = re.search(r'^JAVA_VERSION="([^"]+)"$', text, flags=re.MULTILINE)
    if match is None or match.group(1).split(".", 1)[0] != "25":
        raise FixtureBuildError("the frozen Cleanroom fixture requires Java 25")
    _ordinary_file(home / "bin/java", label="Java executable", executable=True)
    return home.resolve(strict=True)


def _validate_fixture() -> str:
    _reject_symlink_components(FIXTURE, label="canonical Cleanroom fixture root")
    if not FIXTURE.is_dir():
        raise FixtureBuildError("the canonical Cleanroom fixture root is unsafe")
    try:
        lock = json.loads(
            _read_ordinary_bytes(
                LOCK,
                label="canonical Cleanroom fixture lock",
                limit=MAX_LOCK_BYTES,
            ).decode("utf-8")
        )
        declared = lock["declared_values"]
        rows = declared["files"]
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        FixtureBuildError,
    ) as exc:
        raise FixtureBuildError("the canonical Cleanroom fixture lock is invalid") from exc
    if not isinstance(rows, list) or rows != sorted(
        rows, key=lambda row: str(row.get("path", "")).encode("utf-8")
    ):
        raise FixtureBuildError("fixture lock file rows are not bytewise sorted")
    expected: list[dict[str, Any]] = []
    for path in sorted(FIXTURE.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")):
        relative = path.relative_to(FIXTURE)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FixtureBuildError(f"fixture source contains a symlink: {relative}")
        if not stat.S_ISREG(metadata.st_mode) or path.name == LOCK.name:
            continue
        if GENERATED_PARTS.intersection(relative.parts) or path.suffix in GENERATED_SUFFIXES:
            continue
        raw = _read_ordinary_bytes(
            path,
            label=f"fixture source {relative}",
            limit=MAX_FIXTURE_FILE_BYTES,
        )
        expected.append(
            {
                "path": relative.as_posix(),
                "sha256": "sha256:" + sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )
    if rows != expected:
        raise FixtureBuildError("fixture source tree differs from its exact owner lock")
    material = {"algorithm": "sha256-file-tree-v1", "files": rows}
    digest = "sha256:" + sha256(_canonical_bytes(material)).hexdigest()
    if (
        declared.get("tree_digest_algorithm") != "sha256-file-tree-v1"
        or declared.get("tree_digest") != digest
        or declared.get("identity", {}).get("digest") != digest
    ):
        raise FixtureBuildError("fixture tree identity differs from its owner lock")
    return digest


def _managed_state_root(state_root: Path | None) -> Path:
    candidate = ROOT / ".workbench" if state_root is None else state_root.expanduser()
    if not candidate.is_absolute():
        raise FixtureBuildError("fixture build state root must be absolute")
    return candidate.resolve()


def fixture_projection_path(
    fixture_digest: str,
    *,
    state_root: Path | None = None,
) -> Path:
    """Return the ignored content-addressed build projection for one lock."""

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", fixture_digest):
        raise FixtureBuildError("fixture projection digest is invalid")
    return (
        _managed_state_root(state_root)
        / "source-projections/cleanroom"
        / fixture_digest.removeprefix("sha256:")
        / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
    )


def _locked_fixture_rows() -> list[dict[str, Any]]:
    try:
        value = json.loads(
            _read_ordinary_bytes(
                LOCK,
                label="canonical Cleanroom fixture lock",
                limit=MAX_LOCK_BYTES,
            ).decode("utf-8")
        )
        rows = value["declared_values"]["files"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FixtureBuildError("the canonical Cleanroom fixture lock is invalid") from exc
    if not isinstance(rows, list):
        raise FixtureBuildError("the canonical Cleanroom fixture file list is invalid")
    return [dict(row) for row in rows]


def _verify_fixture_projection(
    projection: Path,
    *,
    rows: list[dict[str, Any]],
) -> None:
    _reject_symlink_components(projection, label="Cleanroom fixture projection")
    if not projection.is_dir():
        raise FixtureBuildError("Cleanroom fixture projection is not a directory")
    expected = {str(row["path"]): row for row in rows}
    expected[LOCK.name] = {
        "path": LOCK.name,
        "sha256": "sha256:" + sha256(
            _read_ordinary_bytes(
                LOCK,
                label="canonical Cleanroom fixture lock",
                limit=MAX_LOCK_BYTES,
            )
        ).hexdigest(),
        "size": LOCK.stat().st_size,
    }
    observed: set[str] = set()
    for path in sorted(
        projection.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")
    ):
        relative = path.relative_to(projection)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise FixtureBuildError(
                f"Cleanroom fixture projection contains a symlink: {relative}"
            )
        if not stat.S_ISREG(metadata.st_mode):
            continue
        if GENERATED_PARTS.intersection(relative.parts) or path.suffix in GENERATED_SUFFIXES:
            continue
        relative_text = relative.as_posix()
        row = expected.get(relative_text)
        if row is None:
            raise FixtureBuildError(
                f"Cleanroom fixture projection contains an undeclared source: {relative_text}"
            )
        raw = _read_ordinary_bytes(
            path,
            label=f"projected fixture source {relative_text}",
            limit=MAX_FIXTURE_FILE_BYTES,
        )
        if (
            len(raw) != row["size"]
            or "sha256:" + sha256(raw).hexdigest() != row["sha256"]
        ):
            raise FixtureBuildError(
                f"Cleanroom fixture projection source changed: {relative_text}"
            )
        observed.add(relative_text)
    if observed != set(expected):
        missing = sorted(set(expected) - observed)
        raise FixtureBuildError(
            "Cleanroom fixture projection is incomplete: " + ", ".join(missing)
        )


def _materialize_fixture_projection(
    fixture_digest: str,
    *,
    state_root: Path | None = None,
) -> Path:
    """Publish an immutable source copy under ignored Workbench storage."""

    rows = _locked_fixture_rows()
    projection = fixture_projection_path(fixture_digest, state_root=state_root)
    if projection.exists():
        _verify_fixture_projection(projection, rows=rows)
        return projection
    target_root = projection.parents[4]
    base = target_root.parent
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.chmod(0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".fixture-", dir=base))
    try:
        destination = (
            temporary
            / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
        )
        destination.mkdir(parents=True, mode=0o700)
        for row in [
            *rows,
            {
                "path": LOCK.name,
                "sha256": "sha256:" + sha256(
                    _read_ordinary_bytes(
                        LOCK,
                        label="canonical Cleanroom fixture lock",
                        limit=MAX_LOCK_BYTES,
                    )
                ).hexdigest(),
                "size": LOCK.stat().st_size,
            },
        ]:
            relative = Path(str(row["path"]))
            source = LOCK if relative == Path(LOCK.name) else FIXTURE / relative
            raw = _read_ordinary_bytes(
                source,
                label=f"canonical fixture projection input {relative}",
                limit=MAX_FIXTURE_FILE_BYTES,
            )
            if (
                len(raw) != row["size"]
                or "sha256:" + sha256(raw).hexdigest() != row["sha256"]
            ):
                raise FixtureBuildError(
                    f"canonical fixture changed during projection: {relative}"
                )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                raise
        _verify_fixture_projection(destination, rows=rows)
        for directory in sorted(
            (path for path in temporary.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            directory.chmod(0o700)
            if os.name == "posix":
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        if os.name == "posix":
            descriptor = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        try:
            os.replace(temporary, target_root)
        except OSError:
            # A concurrent publisher may win the content-addressed directory.
            # Its exact bytes are verified below; every other replace failure
            # remains fatal.
            if not projection.exists():
                raise
        if os.name == "posix":
            parent_descriptor = os.open(base, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    _verify_fixture_projection(projection, rows=rows)
    return projection


def inspect_build_inputs(*, gradle_cmd: Path, java_home: Path) -> dict[str, Any]:
    """Return the exact read-only input binding consumed by Home and execution."""

    fixture_digest = _validate_fixture()
    gradle = _ordinary_file(gradle_cmd, label="Gradle executable", executable=True)
    java = _validate_java_home(java_home)
    gradle_bytes = _read_ordinary_bytes(
        gradle,
        label="Gradle executable",
        limit=MAX_TOOL_RECORD_BYTES,
    )
    release_bytes = _read_ordinary_bytes(
        java / "release",
        label="Java release record",
        limit=MAX_TOOL_RECORD_BYTES,
    )
    java_bytes = _read_ordinary_bytes(
        java / "bin/java",
        label="Java executable",
        limit=MAX_TOOL_RECORD_BYTES,
    )
    cleanup_bytes = _read_ordinary_bytes(
        CLEANUP_INIT,
        label="Cleanroom fixture cleanup init script",
        limit=MAX_TOOL_RECORD_BYTES,
    )
    return {
        "format": "workbench-cleanroom-fixture-build-input-v1",
        "fixture": str(FIXTURE),
        "fixture_digest": fixture_digest,
        "gradle": {
            "path": str(gradle),
            "sha256": "sha256:" + sha256(gradle_bytes).hexdigest(),
            "size": len(gradle_bytes),
        },
        "java": {
            "home": str(java),
            "release_sha256": "sha256:" + sha256(release_bytes).hexdigest(),
            "executable_sha256": "sha256:" + sha256(java_bytes).hexdigest(),
        },
        "cleanup_init": {
            "path": str(CLEANUP_INIT),
            "sha256": "sha256:" + sha256(cleanup_bytes).hexdigest(),
            "size": len(cleanup_bytes),
        },
    }


def build_input_digest(inputs: dict[str, Any]) -> str:
    """Bind inspected inputs and the owner code used again at execution."""

    if inputs.get("format") != "workbench-cleanroom-fixture-build-input-v1":
        raise FixtureBuildError("fixture build inputs use an unsupported format")
    implementation = _read_ordinary_bytes(
        Path(__file__), label="Cleanroom fixture build implementation",
        limit=MAX_TOOL_RECORD_BYTES,
    )
    runner = _read_ordinary_bytes(
        PROFILE_ROOT / "tools/run_generic_mod_fixture_build.py",
        label="Cleanroom fixture build entry point", limit=MAX_TOOL_RECORD_BYTES,
    )
    bound = {
        "inputs": inputs,
        "implementation_sha256": "sha256:" + sha256(implementation).hexdigest(),
        "runner_sha256": "sha256:" + sha256(runner).hexdigest(),
    }
    return "sha256:" + sha256(_canonical_bytes(bound)).hexdigest()


def build_argv(
    *,
    gradle_cmd: Path,
    java_home: Path,
    expected_input_digest: str | None = None,
    state_root: Path | None = None,
    materialize: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Return one exact Gradle argv/environment after all owner preflights."""

    inputs = inspect_build_inputs(gradle_cmd=gradle_cmd, java_home=java_home)
    observed_input_digest = build_input_digest(inputs)
    if (
        expected_input_digest is not None
        and expected_input_digest != observed_input_digest
    ):
        raise FixtureBuildError(
            "the selected fixture build inputs changed after Home retained them"
        )
    gradle = Path(inputs["gradle"]["path"])
    java = Path(inputs["java"]["home"])
    managed_state = _managed_state_root(state_root)
    project = (
        _materialize_fixture_projection(
            inputs["fixture_digest"],
            state_root=managed_state,
        )
        if materialize
        else fixture_projection_path(
            inputs["fixture_digest"],
            state_root=managed_state,
        )
    )
    project_cache = managed_state / "gradle-project-cache/generic-mod-daily-loop"
    gradle_home = managed_state / "gradle-home/generic-mod-daily-loop"
    environment = dict(os.environ)
    environment["JAVA_HOME"] = str(java)
    environment["PATH"] = f"{java / 'bin'}:{environment.get('PATH', '')}"
    environment["GRADLE_USER_HOME"] = str(gradle_home)
    # Gradle's reproducible archive timestamp is converted through the JVM's
    # default timezone before the ZIP entry is written.  Bind UTC so identical
    # fixture inputs do not produce different JAR bytes across host or cached
    # Gradle state.
    environment["TZ"] = "UTC"
    argv = [
        str(gradle),
        "--no-daemon",
        "-p",
        str(project),
        "--project-cache-dir",
        str(project_cache),
        "--init-script",
        str(CLEANUP_INIT),
        "clean",
        "check",
        "workbenchCleanFixtureLocalCache",
    ]
    return argv, environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gradle-cmd", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--expected-input-digest")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        command, environment = build_argv(
            gradle_cmd=args.gradle_cmd,
            java_home=args.java_home,
            expected_input_digest=args.expected_input_digest,
            state_root=args.state_root,
            materialize=not args.check_only,
        )
        if args.check_only:
            inputs = inspect_build_inputs(
                gradle_cmd=args.gradle_cmd,
                java_home=args.java_home,
            )
            print(
                json.dumps(
                    {
                        "format": "workbench-cleanroom-fixture-build-preflight-v1",
                        "inputs": inputs,
                        "argv": command,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        for directory in (
            _managed_state_root(args.state_root)
            / "gradle-project-cache/generic-mod-daily-loop",
            _managed_state_root(args.state_root)
            / "gradle-home/generic-mod-daily-loop",
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        os.execve(command[0], command, environment)
    except (FixtureBuildError, OSError) as exc:
        print(f"Cleanroom fixture build rejected: {exc}", file=sys.stderr)
        return 2
    return 2  # pragma: no cover - execve replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
