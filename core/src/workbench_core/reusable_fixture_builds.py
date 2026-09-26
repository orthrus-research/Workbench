"""Supervise Cleanroom V1 Gradle while holding Core projection and cache custody.

The profile remains the source/toolchain authority. This adapter preserves the
old fixed projection and cache paths, but Core owns the running child, retained
streams, and a lease spanning its complete execution and readback.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from workbench_api.reusable_fixture_builds import (
    ReusableFixtureBuildError, ReusableFixtureBuildResult,
)

from . import check_storage, process_capture, tool_process
from .durable_records import private_record_lock
from .host_filesystem import private_path, secure_private_path
from .output_routing import _private_directory
from .reusable_projections import (
    CoreReusableProjections, _generated, _generated_roots, _scan, _source_rows, _suffixes,
)


_PROJECT_RELATIVE = Path("profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop")
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024  # Per stream; excess terminates and retains partial capture.


def _fail(code: str, message: str) -> None:
    raise ReusableFixtureBuildError(f"fixture.{code}", message)


def _private_directory_at(path: Path) -> None:
    try:
        _private_directory(path)
    except (OSError, ValueError) as exc:
        raise ReusableFixtureBuildError(
            "fixture.unsafe", f"fixture build storage is unsafe: {path}",
        ) from exc
    if not private_path(path, directory=True):
        _fail("unsafe", f"fixture build storage cannot enforce owner-private access: {path}")


def _reuse_private_cache(path: Path) -> None:
    """Keep historical bytes while applying the old runner's leaf chmod."""

    try:
        _private_directory(path)
        secure_private_path(path, directory=True)
    except (OSError, ValueError) as exc:
        raise ReusableFixtureBuildError(
            "fixture.unsafe", f"fixture cache cannot enforce owner-private access: {path}",
        ) from exc


class CoreReusableFixtureBuilds:
    """One direct Core host for the frozen Cleanroom V1 fixture command."""

    def __init__(self, *, workspace: Path, configuration_home: Path):
        self.projections = CoreReusableProjections(
            workspace=workspace, configuration_home=configuration_home,
            owner_id="cleanroom-platform-profile",
        )

    def prepare(self, *, state_root: Path) -> None:
        """Establish private state before the historical publisher creates parents."""

        if not isinstance(state_root, Path) or not state_root.is_absolute():
            _fail("input", "fixture state root must be absolute")
        _private_directory_at(state_root)

    def run(
        self, *, state_root: Path, source_digest: str, project: Path,
        source_files: tuple[dict[str, object], ...],
        generated_parts: tuple[str, ...], generated_suffixes: tuple[str, ...],
        generated_roots: tuple[Path, ...],
        argv: Sequence[str], environment: Mapping[str, str],
        input_digest: str, verify_inputs: Callable[[], str],
        verify_source: Callable[[Path], object],
    ) -> ReusableFixtureBuildResult:
        if (not isinstance(state_root, Path) or not state_root.is_absolute()
                or not isinstance(project, Path) or not project.is_absolute()
                or not isinstance(source_digest, str)
                or not isinstance(input_digest, str) or not input_digest.startswith("sha256:")):
            _fail("input", "fixture build needs absolute state and exact inputs")
        projection_root = state_root / "source-projections/cleanroom" / source_digest.removeprefix("sha256:")
        if project != projection_root / _PROJECT_RELATIVE:
            _fail("input", "fixture build project differs from its exact projection")
        project_cache = state_root / "gradle-project-cache/generic-mod-daily-loop"
        gradle_home = state_root / "gradle-home/generic-mod-daily-loop"
        capture_root = state_root / "fixture-build-attempts"
        command = tuple(argv)
        if (len(command) != 11 or not Path(command[0]).is_absolute()
                or command[1:4] != ("--no-daemon", "-p", str(project))
                or command[4:7] != ("--project-cache-dir", str(project_cache), "--init-script")
                or not Path(command[7]).is_absolute()
                or command[8:] != ("clean", "check", "workbenchCleanFixtureLocalCache")):
            _fail("command", "fixture Gradle argv differs from the frozen V1 command")
        java_home = environment.get("JAVA_HOME")
        if (not isinstance(java_home, str) or not Path(java_home).is_absolute()
                or environment.get("GRADLE_USER_HOME") != str(gradle_home)
                or environment.get("TZ") != "UTC"
                or not environment.get("PATH", "").startswith(str(Path(java_home) / "bin") + ":")):
            _fail("command", "fixture Java 25 and cache environment differs from owner preflight")
        if verify_inputs() != input_digest:
            _fail("changed", "fixture toolchain or owner code changed before launch")

        # The historical publisher has already created or verified the exact
        # path. Core adopts it without rewriting any source or generated cache.
        reference = self.projections.adopt(
            "cleanroom", projection_root, source_digest=source_digest,
            project_relative=_PROJECT_RELATIVE, source_files=source_files,
            generated_parts=generated_parts, generated_suffixes=generated_suffixes,
            validate=verify_source, generated_roots=generated_roots,
        )
        _private_directory_at(state_root)
        for directory in (project_cache, gradle_home):
            _reuse_private_cache(directory)
        _private_directory_at(capture_root)
        binding = "workbench-cleanroom-fixture-build-v1:sha256:" + sha256(
            check_storage.canonical({
                "projection_id": reference.projection_id, "input_digest": input_digest,
                "argv": command, "project_cache": str(project_cache),
                "gradle_home": str(gradle_home),
            })
        ).hexdigest()

        def readback() -> None:
            if verify_inputs() != input_digest:
                _fail("changed", "fixture toolchain or owner code changed during build")
            root_info, parent_info = _scan(
                reference.path, reference.project, _source_rows(source_files),
                _generated(generated_parts), _suffixes(generated_suffixes), verify_source,
                _generated_roots(generated_roots),
            )
            record = self.projections._record(reference.projection_id)
            if ((root_info.st_dev, root_info.st_ino) != (record["device"], record["inode"])
                    or (parent_info.st_dev, parent_info.st_ino) != (
                        record["parent_device"], record["parent_inode"],
                    )):
                _fail("changed", "fixture projection changed during build")

        # The lock in the state root also serializes two Core configurations
        # pointing at the same legacy Gradle homes. Projection lease remains
        # live from source preflight through child termination and readback.
        with private_record_lock(state_root / ".cleanroom-fixture-build.lock", wait=True):
            with self.projections.open(reference.projection_id, validate=verify_source):
                if verify_inputs() != input_digest:
                    _fail("changed", "fixture toolchain or owner code changed before child start")
                capture_directory = capture_root / uuid4().hex
                try:
                    captured = tool_process.capture(
                        command, directory=capture_directory, binding=binding,
                        cwd=reference.project, stdin=b"", environment=environment,
                        cancelled=Event(), timeout_seconds=None,
                        output_limit=_MAX_OUTPUT_BYTES,
                    )
                except BaseException as exc:
                    try:
                        readback()
                    except Exception as readback_error:
                        exc.add_note("Fixture readback also failed: " + str(readback_error))
                    raise
                readback()
                with process_capture.open_output(captured.stdout) as stream:
                    stdout = stream.read()
                with process_capture.open_output(captured.stderr) as stream:
                    stderr = stream.read()
                return ReusableFixtureBuildResult(
                    captured.exit_code, reference.projection_id, captured.capture_id,
                    capture_directory, stdout, stderr,
                )


__all__ = ["CoreReusableFixtureBuilds"]
