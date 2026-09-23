"""Generic Cleanroom fixture build and physical-side daily-loop composition.

The Cleanroom profile owns the exact fixture and build preflight.  This module
only joins that owner to the existing live-console process supervisor and
retains one result across independent client and dedicated-server attempts.
"""

from __future__ import annotations

from urllib.request import url2pathname

from abc import ABC, abstractmethod
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import time
from typing import Any, Mapping, NoReturn, Sequence
from urllib.parse import urlparse
import zipfile

from workbench_api.profile_extensions import ProfileExtensionError, require_profile_extension
from workbench_core.render import Renderer
from workbench_core.runner import RunResult, RunnerError, supervise_process
from workbench_core.sessions import (
    RetainedSession,
    SessionError,
    live_console_owner_reference,
)


PLAN_FORMAT = "workbench-cleanroom-dev-loop-plan-v1"
PLAN_KIND = "workbench-cleanroom-dev-loop-plan"
RECEIPT_FORMAT = "workbench-cleanroom-dev-loop-receipt-v1"
RECEIPT_KIND = "workbench-cleanroom-dev-loop-receipt"
RECOVERY_FORMAT = "workbench-cleanroom-dev-loop-recovery-v1"
_PLAN_ID = re.compile(rf"{re.escape(PLAN_KIND)}:sha256:[0-9a-f]{{64}}\Z")
_RECEIPT_ID = re.compile(rf"{re.escape(RECEIPT_KIND)}:sha256:[0-9a-f]{{64}}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RUN_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_OWNER_RECORD_ID = re.compile(r"[A-Za-z0-9._-]{8,120}\Z")
_SIDES = ("client", "server")
_RUNTIME_TASKS = {"client": "runClient", "server": "runServer"}
_REQUIRED_MARKERS = {
    "client": ("client-resource-ready",),
    "server": ("dedicated-server-ready", "common-registry-ready"),
}
_MARKER_TEXT = {
    "client-resource-ready": "WORKBENCH_DAILY_LOOP_CLIENT_RESOURCE_READY",
    "dedicated-server-ready": "Done (",
    "common-registry-ready": "WORKBENCH_DAILY_LOOP_COMMON_READY",
}
D01_JAR_ONLY_INIT_SCRIPT = Path(
    "clients/testing/d01_jar_only_runtime_v1.gradle"
)
D01_CODE_SOURCE_AGENT_SOURCE = Path(
    "clients/testing/d01-code-source-agent/src/main/java/"
    "dev/cleanroommc/workbench/d01/D01CodeSourceAgent.java"
)
D01_CODE_SOURCE_PROBE_CLASS = "dev.workbench.dailyloop.DailyLoopProbe"
_D01_CODE_SOURCE_PROBE_ENTRY = D01_CODE_SOURCE_PROBE_CLASS.replace(".", "/") + ".class"
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_MAX_RETAINED_RUNS = 4096
_MAX_RECEIPT_SCAN_BYTES = 64 * 1024 * 1024
_SERVER_EULA_BYTES = b"eula=true\n"
_SERVER_EULA_SHA256 = "sha256:" + sha256(_SERVER_EULA_BYTES).hexdigest()
_SERVER_EULA_OPERATION = "materialize-isolated-server-eula"
_SERVER_EULA_SCOPE = "fresh-isolated-run-target"


class CleanroomDevLoopError(RuntimeError):
    """The exact generic fixture daily loop could not proceed safely."""


class CleanroomDevLoopStageCustodyPorts(ABC):
    """Optional C01 bridge around each existing live-console execution.

    ``stage_allocated`` runs after the RetainedSession owner exists and before
    the process supervisor (and therefore before Popen). ``stage_terminal``
    runs after the stage-specific outcome has been decided. Implementations
    may bind those exact references into Work Session; they do not launch or
    reinterpret the underlying stage.
    """

    @abstractmethod
    def stage_allocated(
        self,
        *,
        plan: Mapping[str, Any],
        stage: str,
        owner_ref: Mapping[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def stage_terminal(
        self,
        *,
        plan: Mapping[str, Any],
        stage: str,
        owner_ref: Mapping[str, Any],
        stage_result: Mapping[str, Any],
    ) -> None:
        raise NotImplementedError


def _fail(message: str) -> NoReturn:
    raise CleanroomDevLoopError(message)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CleanroomDevLoopError("dev-loop record is not canonical JSON") from exc


def _seal(kind: str, body: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = deepcopy(dict(body))
    result[field] = f"{kind}:sha256:{sha256(_canonical(body)).hexdigest()}"
    return result


def _local_uri(value: Any, label: str) -> Path:
    if type(value) is not str:
        _fail(f"{label} is not a local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} is not a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        _fail(f"{label} is not absolute")
    return path


def _ordinary_file(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CleanroomDevLoopError(f"{label} is unavailable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        _fail(f"{label} must be an ordinary non-symlink file")
    return path.resolve()


def _validate_state_location(suite: Path, state: Path) -> Path:
    """Keep generated state external or below the checkout's ignored lane."""

    current = Path(state.anchor)
    for part in state.parts[1:]:
        current = current / part
        if current.is_symlink():
            _fail(f"dev-loop state cannot traverse a symbolic link: {current}")
        if current.exists() and current != state and not current.is_dir():
            _fail(f"dev-loop state traverses a non-directory: {current}")
    if state.exists() and not state.is_dir():
        _fail("dev-loop state root is not an ordinary directory")
    ignored = suite / ".workbench"
    if state.is_relative_to(suite) and not state.is_relative_to(ignored):
        _fail("dev-loop state inside the checkout must remain under .workbench")
    return state


def _fixture_owner(suite: Path) -> Any:
    try:
        owner = require_profile_extension("workbench.cleanroom_fixture_builds", "cleanroom")
    except ProfileExtensionError as exc:
        raise CleanroomDevLoopError(
            f"Cleanroom fixture build owner is unavailable: {exc}"
        ) from exc
    for name in ("inspect_build_inputs", "build_argv", "entry_point_path"):
        if not callable(getattr(owner, name, None)):
            _fail("Cleanroom fixture build owner API is incomplete")
    _ordinary_file(Path(owner.entry_point_path()), "Cleanroom fixture build owner")
    return owner


def _inspect_owner_inputs(
    owner: Any, *, gradle_cmd: Path, java_home: Path
) -> Mapping[str, Any]:
    try:
        value = owner.inspect_build_inputs(
            gradle_cmd=gradle_cmd,
            java_home=java_home,
        )
    except Exception as exc:
        raise CleanroomDevLoopError(
            f"Cleanroom fixture owner input inspection failed: {exc}"
        ) from exc
    if not isinstance(value, Mapping):
        _fail("Cleanroom fixture owner input inspection returned no record")
    return value


def _owner_build_argv(
    owner: Any,
    *,
    gradle_cmd: Path,
    java_home: Path,
    state_root: Path,
    materialize: bool,
) -> tuple[list[str], Mapping[str, str]]:
    try:
        value = owner.build_argv(
            gradle_cmd=gradle_cmd,
            java_home=java_home,
            state_root=state_root,
            materialize=materialize,
        )
    except Exception as exc:
        action = "execution" if materialize else "planning"
        raise CleanroomDevLoopError(
            f"Cleanroom fixture owner {action} command failed: {exc}"
        ) from exc
    if (
        type(value) is not tuple
        or len(value) != 2
        or type(value[0]) is not list
        or not isinstance(value[1], Mapping)
    ):
        _fail("Cleanroom fixture owner command shape changed")
    return value[0], value[1]


def _runtime_argv(
    build_argv: Sequence[str], side: str, target_root: Path
) -> list[str]:
    try:
        boundary = list(build_argv).index("clean")
    except ValueError as exc:
        raise CleanroomDevLoopError(
            "Cleanroom fixture owner build command has no task boundary"
        ) from exc
    return [
        *list(build_argv)[:boundary],
        f"-PworkbenchDailyLoopRunDir={target_root}",
        _RUNTIME_TASKS[side],
    ]


def _artifact_path(project: Path) -> Path:
    return (
        project.parents[4]
        / ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop"
        / "libs/workbench-daily-loop-1.0.0.jar"
    )


def _server_eula_declaration(target_root: Path) -> dict[str, Any]:
    return {
        "operation": _SERVER_EULA_OPERATION,
        "scope": _SERVER_EULA_SCOPE,
        "target_uri": (target_root / "eula.txt").as_uri(),
        "sha256": _SERVER_EULA_SHA256,
        "size": len(_SERVER_EULA_BYTES),
    }


def plan_cleanroom_dev_loop(
    suite_root: Path | str,
    *,
    gradle_cmd: Path | str,
    java_home: Path | str,
    state_root: Path | str,
    sides: Sequence[str] = _SIDES,
    debug: bool = False,
) -> dict[str, Any]:
    """Return the exact generic fixture build/run plan without creating state."""

    suite = Path(suite_root).resolve()
    selected = tuple(sides)
    if (
        not selected
        or len(selected) != len(set(selected))
        or any(side not in _SIDES for side in selected)
        or tuple(side for side in _SIDES if side in selected) != selected
    ):
        _fail("dev-loop sides must be the canonical client/server subset")
    if type(debug) is not bool:
        _fail("dev-loop debug selection must be boolean")
    if debug:
        _fail(
            "generic fixture debug execution is unavailable until an IDE-owned "
            "JDWP attach and breakpoint receipt can be retained"
        )
    state = _validate_state_location(
        suite,
        Path(os.path.abspath(os.fspath(Path(state_root).expanduser()))),
    )
    owner = _fixture_owner(suite)
    inputs = _inspect_owner_inputs(
        owner,
        gradle_cmd=Path(gradle_cmd),
        java_home=Path(java_home),
    )
    build, environment = _owner_build_argv(
        owner,
        gradle_cmd=Path(gradle_cmd),
        java_home=Path(java_home),
        state_root=state,
        materialize=False,
    )
    project = Path(build[build.index("-p") + 1])
    identity = {
        "fixture_digest": inputs["fixture_digest"],
        "gradle_sha256": inputs["gradle"]["sha256"],
        "java_release_sha256": inputs["java"]["release_sha256"],
        "sides": list(selected),
        "debug": debug,
        "attempt_nonce": secrets.token_hex(16),
    }
    token = sha256(_canonical(identity)).hexdigest()
    target = state / "dev-loop" / token
    stages: dict[str, Any] = {
        "build": {
            "argv": list(build),
            "cwd_uri": project.as_uri(),
            "expected_artifact_uri": _artifact_path(project).as_uri(),
        }
    }
    for side in selected:
        side_target = target / side
        stages[side] = {
            "argv": _runtime_argv(build, side, side_target),
            "cwd_uri": project.as_uri(),
            "required_markers": list(_REQUIRED_MARKERS[side]),
            "target_root_uri": side_target.as_uri(),
        }
        if side == "server":
            stages[side]["preparation"] = _server_eula_declaration(side_target)
    body = {
        "format": PLAN_FORMAT,
        "schema_version": 1,
        "kind": PLAN_KIND,
        "operation_class": "read-only-plan",
        "state": "ready",
        "request": {"sides": list(selected), "debug": debug},
        "fixture": {
            "fixture_id": "workbench-fixture:cleanroom:generic-mod-daily-loop",
            "digest": inputs["fixture_digest"],
            "source_uri": Path(inputs["fixture"]).as_uri(),
        },
        "toolchain": {
            "gradle": deepcopy(inputs["gradle"]),
            "java": deepcopy(inputs["java"]),
            "cleanup_init": deepcopy(inputs["cleanup_init"]),
            "environment": {
                "JAVA_HOME": environment["JAVA_HOME"],
                "GRADLE_USER_HOME": environment["GRADLE_USER_HOME"],
            },
        },
        "state_root_uri": state.as_uri(),
        "target_root_uri": target.as_uri(),
        "stages": stages,
        "limitations": [
            "This exact experimental fixture does not establish support for arbitrary mods or Cleanroom profiles.",
            "Client and dedicated-server attempts are independent process smokes, not connected gameplay.",
            "Unimined's exact development classpath is exercised after JAR inspection, but the current fixture marker does not yet prove the loaded JAR digest.",
            "A debug request remains unavailable until an IDE proves a real fixture breakpoint and endpoint cleanup.",
        ],
    }
    return validate_cleanroom_dev_loop_plan(_seal(PLAN_KIND, body, "plan_id"))


def validate_cleanroom_dev_loop_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("dev-loop plan must be one ordinary object")
    plan = deepcopy(dict(value))
    body = dict(plan)
    supplied = body.pop("plan_id", None)
    required = {
        "format", "schema_version", "kind", "plan_id", "operation_class",
        "state", "request", "fixture", "toolchain", "state_root_uri",
        "target_root_uri", "stages", "limitations",
    }
    request = plan.get("request")
    stages = plan.get("stages")
    fixture = plan.get("fixture")
    toolchain = plan.get("toolchain")
    if (
        set(plan) != required
        or plan.get("format") != PLAN_FORMAT
        or plan.get("schema_version") != 1
        or plan.get("kind") != PLAN_KIND
        or plan.get("operation_class") != "read-only-plan"
        or plan.get("state") != "ready"
        or type(supplied) is not str
        or _PLAN_ID.fullmatch(supplied) is None
        or supplied != _seal(PLAN_KIND, body, "plan_id")["plan_id"]
        or type(request) is not dict
        or set(request) != {"sides", "debug"}
        or type(request.get("debug")) is not bool
        or type(request.get("sides")) is not list
        or not request["sides"]
        or type(stages) is not dict
        or type(fixture) is not dict
        or type(toolchain) is not dict
        or type(plan.get("limitations")) is not list
        or not plan["limitations"]
    ):
        _fail("dev-loop plan identity or shape changed")
    selected = request["sides"]
    if (
        any(type(side) is not str for side in selected)
        or len(selected) != len(set(selected))
        or selected != [side for side in _SIDES if side in selected]
        or set(stages) != {"build", *selected}
        or any(type(item) is not str or not item for item in plan["limitations"])
    ):
        _fail("dev-loop plan identity or shape changed")
    if (
        set(fixture) != {"fixture_id", "digest", "source_uri"}
        or fixture.get("fixture_id")
        != "workbench-fixture:cleanroom:generic-mod-daily-loop"
        or type(fixture.get("digest")) is not str
        or _DIGEST.fullmatch(fixture["digest"]) is None
    ):
        _fail("dev-loop fixture binding changed")
    _local_uri(fixture.get("source_uri"), "dev-loop fixture source")
    if set(toolchain) != {"gradle", "java", "cleanup_init", "environment"}:
        _fail("dev-loop toolchain shape changed")
    gradle = toolchain.get("gradle")
    java = toolchain.get("java")
    cleanup = toolchain.get("cleanup_init")
    environment = toolchain.get("environment")
    if (
        type(gradle) is not dict
        or set(gradle) != {"path", "sha256", "size"}
        or type(gradle.get("path")) is not str
        or not Path(gradle["path"]).is_absolute()
        or type(gradle.get("sha256")) is not str
        or _DIGEST.fullmatch(gradle["sha256"]) is None
        or type(gradle.get("size")) is not int
        or gradle["size"] < 1
        or type(java) is not dict
        or set(java) != {
            "home", "release_sha256", "executable_sha256",
        }
        or type(java.get("home")) is not str
        or not Path(java["home"]).is_absolute()
        or any(
            type(java.get(field)) is not str
            or _DIGEST.fullmatch(java[field]) is None
            for field in ("release_sha256", "executable_sha256")
        )
        or type(cleanup) is not dict
        or set(cleanup) != {"path", "sha256", "size"}
        or type(cleanup.get("path")) is not str
        or not Path(cleanup["path"]).is_absolute()
        or type(cleanup.get("sha256")) is not str
        or _DIGEST.fullmatch(cleanup["sha256"]) is None
        or type(cleanup.get("size")) is not int
        or cleanup["size"] < 1
        or type(environment) is not dict
        or set(environment) != {"JAVA_HOME", "GRADLE_USER_HOME"}
        or any(type(item) is not str or not item for item in environment.values())
    ):
        _fail("dev-loop toolchain shape changed")
    state = _local_uri(plan["state_root_uri"], "dev-loop state root")
    target = _local_uri(plan["target_root_uri"], "dev-loop target root")
    if target.parent != state / "dev-loop":
        _fail("dev-loop target is outside its exact retained state lane")
    build_stage = stages["build"]
    for name, stage in stages.items():
        if type(stage) is not dict or type(stage.get("argv")) is not list or not stage["argv"]:
            _fail(f"dev-loop {name} stage command is invalid")
        if any(type(item) is not str or not item for item in stage["argv"]):
            _fail(f"dev-loop {name} stage command is invalid")
        cwd = _local_uri(stage.get("cwd_uri"), f"dev-loop {name} cwd")
        if name == "build":
            if set(stage) != {"argv", "cwd_uri", "expected_artifact_uri"}:
                _fail("dev-loop build stage shape changed")
            _local_uri(
                stage.get("expected_artifact_uri"),
                "dev-loop expected artifact",
            )
        else:
            required_fields = {
                "argv", "cwd_uri", "required_markers", "target_root_uri",
            }
            if name == "server":
                required_fields.add("preparation")
            side_target = _local_uri(
                stage.get("target_root_uri"), f"dev-loop {name} target"
            )
            if (
                set(stage) != required_fields
                or cwd != _local_uri(build_stage["cwd_uri"], "dev-loop build cwd")
                or stage.get("required_markers") != list(_REQUIRED_MARKERS[name])
                or side_target.parent != target
                or stage["argv"] != _runtime_argv(build_stage["argv"], name, side_target)
            ):
                _fail(f"dev-loop {name} stage policy changed")
            if name == "server" and stage.get("preparation") != _server_eula_declaration(side_target):
                _fail("dev-loop server EULA preparation changed")
    return plan


class _MarkerStopRenderer(Renderer):
    """Observe exact fixture markers and request bounded owned-process stop."""

    def __init__(self, required: Sequence[str]) -> None:
        self.required = tuple(required)
        self.observed: set[str] = set()
        self.cancellation_requested = False
        self.force_requested = False

    def consume(self, event: Mapping[str, Any]) -> None:
        message = str(event.get("message", ""))
        for marker in self.required:
            if _MARKER_TEXT[marker] in message:
                self.observed.add(marker)
        if self.observed == set(self.required):
            self.cancellation_requested = True


class _QuietRenderer(Renderer):
    def consume(self, event: Mapping[str, Any]) -> None:
        del event


def _run_console(
    *,
    state: Path,
    suite: Path,
    command_id: str,
    argv: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    renderer: Renderer,
    plan: Mapping[str, Any],
    stage: str,
    stage_custody_ports: CleanroomDevLoopStageCustodyPorts | None,
) -> dict[str, Any]:
    session = RetainedSession(
        root=state,
        command_id=command_id,
        argv=list(argv),
        cwd=cwd,
        intent="execute",
    )
    allocated_owner_ref = live_console_owner_reference(state, session.session_id)
    if stage_custody_ports is not None:
        try:
            stage_custody_ports.stage_allocated(
                plan=deepcopy(dict(plan)),
                stage=stage,
                owner_ref=deepcopy(allocated_owner_ref),
            )
        except Exception as exc:
            session.abort(f"prelaunch stage custody failed: {exc}")
            return {
                "result": None,
                "session": session,
                "allocated_owner_ref": allocated_owner_ref,
                "owner_ref": live_console_owner_reference(state, session.session_id),
                "error": f"prelaunch stage custody failed: {type(exc).__name__}: {str(exc)[:512]}",
            }
    try:
        result = supervise_process(
            argv,
            cwd=cwd,
            root=suite,
            session=session,
            renderer=renderer,
            source=command_id,
            environment=environment,
        )
    except (RunnerError, SessionError, OSError, ValueError) as exc:
        return {
            "result": None,
            "session": session,
            "allocated_owner_ref": allocated_owner_ref,
            "owner_ref": live_console_owner_reference(state, session.session_id),
            "error": str(exc),
        }
    return {
        "result": result,
        "session": session,
        "allocated_owner_ref": allocated_owner_ref,
        "owner_ref": live_console_owner_reference(state, session.session_id),
        "error": None,
    }


def _notify_stage_terminal(
    *,
    plan: Mapping[str, Any],
    stage: str,
    execution: Mapping[str, Any],
    stage_result: dict[str, Any],
    stage_custody_ports: CleanroomDevLoopStageCustodyPorts | None,
) -> dict[str, Any]:
    """Publish the semantic stage outcome to optional external custody."""

    if stage_custody_ports is None:
        return stage_result
    try:
        stage_custody_ports.stage_terminal(
            plan=deepcopy(dict(plan)),
            stage=stage,
            owner_ref=deepcopy(dict(execution["owner_ref"])),
            stage_result=deepcopy(stage_result),
        )
    except Exception as exc:
        previous = stage_result.get("problem")
        detail = f"terminal stage custody failed: {type(exc).__name__}: {str(exc)[:512]}"
        stage_result["state"] = "failed"
        stage_result["problem"] = f"{previous}; {detail}" if previous else detail
    return stage_result


def _inspect_artifact(path: Path) -> dict[str, Any]:
    artifact = _ordinary_file(path, "Cleanroom fixture artifact")
    raw_digest = sha256()
    size = 0
    with artifact.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            raw_digest.update(chunk)
            size += len(chunk)
    required = {
        "mcmod.info",
        "pack.mcmeta",
        "mixins.workbench_daily_loop.json",
        "mixins.workbench_daily_loop.refmap.json",
        "assets/workbench_daily_loop/lang/en_us.lang",
        "dev/workbench/dailyloop/DailyLoopMod.class",
        "dev/workbench/dailyloop/DailyLoopContent.class",
        "dev/workbench/dailyloop/DailyLoopProbe.class",
        "dev/workbench/dailyloop/mixin/MixinBlock.class",
    }
    try:
        with zipfile.ZipFile(artifact) as jar:
            names = set(jar.namelist())
            missing = sorted(required - names)
            if missing:
                _fail("fixture artifact is missing required entries: " + ", ".join(missing))
            metadata = json.loads(jar.read("mcmod.info").decode("utf-8"))
            refmap = json.loads(
                jar.read("mixins.workbench_daily_loop.refmap.json").decode("utf-8")
            )
            bytecode = jar.read("dev/workbench/dailyloop/DailyLoopMod.class")
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, KeyError) as exc:
        raise CleanroomDevLoopError(f"fixture artifact is corrupt: {exc}") from exc
    if (
        not isinstance(metadata, list)
        or len(metadata) != 1
        or metadata[0].get("modid") != "workbench_daily_loop"
        or metadata[0].get("version") != "1.0.0"
        or not isinstance(refmap, dict)
        or not isinstance(refmap.get("mappings"), dict)
        or not refmap["mappings"]
        or not bytecode.startswith(b"\xca\xfe\xba\xbe")
    ):
        _fail("fixture artifact identity or compiled outputs are invalid")
    return {
        "uri": artifact.as_uri(),
        "sha256": "sha256:" + raw_digest.hexdigest(),
        "size": size,
        "mod_id": "workbench_daily_loop",
        "version": "1.0.0",
        "zip_integrity": "verified",
        "refmap": "present",
        "bytecode": "verified",
    }


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _d01_source_tree_sha256(root: Path) -> str:
    digest = sha256()
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if path.is_symlink():
            _fail(f"D01 disposable source contains a symbolic link: {relative}")
        if not path.is_file() or any(
            part in {".gradle", "build", "out", "__pycache__"}
            for part in relative.parts
        ):
            continue
        raw = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(raw)).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw)
    return "sha256:" + digest.hexdigest()


def materialize_cleanroom_dev_loop_workspace_pair_v2(
    suite_root: Path | str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Create baseline/candidate fixture workspaces without changing authority.

    The profile-owned V1 plan continues to bind the installed suite, exact
    fixture lock, toolchain, and commands.  V2 copies that already materialized
    source projection into ignored disposable workspaces and applies one
    bounded candidate-only Java string edit there.  It never treats the copy
    as an installed suite and never changes the checkout or package tree.
    """

    suite = Path(suite_root).resolve()
    reviewed = validate_cleanroom_dev_loop_plan(plan)
    state = _local_uri(reviewed["state_root_uri"], "dev-loop state root")
    build = list(reviewed["stages"]["build"]["argv"])
    try:
        project_index = build.index("-p") + 1
    except ValueError as exc:
        raise CleanroomDevLoopError(
            "reviewed dev-loop build has no exact project argument"
        ) from exc
    source_candidate = Path(build[project_index])
    if not source_candidate.exists():
        owner = _fixture_owner(suite)
        materialized, _environment = _owner_build_argv(
            owner,
            gradle_cmd=Path(reviewed["toolchain"]["gradle"]["path"]),
            java_home=Path(reviewed["toolchain"]["java"]["home"]),
            state_root=state,
            materialize=True,
        )
        if materialized != build:
            _fail("profile owner materialized a different D01 build command")
    source = source_candidate.resolve(strict=True)
    if not source.is_relative_to(state / "source-projections/cleanroom"):
        _fail("D01 source copy is not the profile-owned fixture projection")
    authority_owner = _ordinary_file(
        Path(_fixture_owner(suite).entry_point_path()), "Cleanroom fixture build owner"
    )
    token = reviewed["plan_id"].rsplit(":", 1)[-1]
    pair_root = state / "dev-loop-v2/workspaces" / token
    if pair_root.exists() or pair_root.is_symlink():
        _fail("D01 disposable workspace pair already exists")
    relative_project = Path(
        "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
    )
    baseline = pair_root / "baseline" / relative_project
    candidate = pair_root / "candidate" / relative_project
    baseline.parent.mkdir(mode=0o700, parents=True)
    candidate.parent.mkdir(mode=0o700, parents=True)
    shutil.copytree(source, baseline, symlinks=False)
    shutil.copytree(source, candidate, symlinks=False)
    before = _d01_source_tree_sha256(baseline)
    if before != _d01_source_tree_sha256(candidate):
        _fail("D01 baseline and candidate source copies differ before the edit")
    relative_edit = Path("src/main/java/dev/workbench/dailyloop/DailyLoopProbe.java")
    edit_path = candidate / relative_edit
    raw = edit_path.read_text(encoding="utf-8")
    old = "fixture=1.0.0"
    new = "fixture=1.0.0-d01-candidate"
    if raw.count(old) != 1 or new in raw:
        _fail("D01 bounded candidate source token changed")
    edit_path.write_text(raw.replace(old, new), encoding="utf-8", newline="")
    after = _d01_source_tree_sha256(candidate)
    if after == before:
        _fail("D01 candidate source edit did not change its disposable tree")

    def variant(name: str, project: Path) -> dict[str, Any]:
        argv = list(build)
        argv[project_index] = str(project)
        cache_index = argv.index("--project-cache-dir") + 1
        argv[cache_index] = str(pair_root / name / ".gradle-project-cache")
        artifact = _artifact_path(project)
        return {
            "variant": name,
            "workspace_uri": project.as_uri(),
            "build_argv": argv,
            "expected_artifact_uri": artifact.as_uri(),
            "source_sha256": _d01_source_tree_sha256(project),
        }

    return {
        "format": "workbench-cleanroom-dev-loop-workspace-pair-v2",
        "schema_version": 2,
        "authority": {
            "installed_suite_uri": suite.as_uri(),
            "fixture_owner_uri": authority_owner.as_uri(),
            "fixture_digest": reviewed["fixture"]["digest"],
            "source_projection_uri": source.as_uri(),
        },
        "baseline": variant("baseline", baseline),
        "candidate": variant("candidate", candidate),
        "candidate_edit": {
            "path": relative_edit.as_posix(),
            "before_sha256": before,
            "after_sha256": after,
            "old_token": old,
            "new_token": new,
            "scope": "disposable-candidate-workspace-only",
        },
        "claims": {
            "authority_replaced": False,
            "qualification_authorized": False,
            "release_authorized": False,
            "support_claimed": False,
        },
    }


def _classpath_entry_contains_fixture_class(path: Path) -> bool:
    """Return whether one ordinary classpath entry owns the D01 probe class."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CleanroomDevLoopError(
            f"JAR-only runtime classpath entry is unavailable: {path}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        _fail(f"JAR-only runtime classpath entry is a symlink: {path}")
    if stat.S_ISDIR(metadata.st_mode):
        return (path / _D01_CODE_SOURCE_PROBE_ENTRY).is_file()
    if not stat.S_ISREG(metadata.st_mode):
        _fail(f"JAR-only runtime classpath entry is not ordinary: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            return _D01_CODE_SOURCE_PROBE_ENTRY in archive.namelist()
    except zipfile.BadZipFile:
        return False


def validate_cleanroom_dev_loop_jar_only_classpath_v2(
    classpath: Sequence[Path | str],
    *,
    installed_artifact: Path | str,
    cleanroom_extra_path: Sequence[Path | str],
) -> dict[str, Any]:
    """Validate the exact split between JavaExec and Cleanroom mod loading.

    This is the product-side oracle used after the Gradle launch seam has
    removed ``sourceSets.main.output``.  The raw Java classpath must contain no
    fixture origin, while Cleanroom Loader's extra path must contain exactly
    the installed CAS JAR.  A development directory, second JAR, symlink, or
    byte-different artifact is a hard failure.
    """

    artifact = _ordinary_file(Path(installed_artifact), "installed fixture artifact")
    inspected = _inspect_artifact(artifact)
    entries: list[Path] = []
    seen: set[Path] = set()
    for raw in classpath:
        candidate = Path(raw)
        if not candidate.is_absolute():
            _fail("JAR-only runtime classpath entries must be absolute")
        resolved = candidate.resolve(strict=False)
        if resolved in seen:
            _fail("JAR-only runtime classpath repeats one entry")
        seen.add(resolved)
        entries.append(resolved)
    raw_origins = [
        entry for entry in entries if _classpath_entry_contains_fixture_class(entry)
    ]
    if raw_origins:
        rendered = ", ".join(str(entry) for entry in raw_origins)
        _fail(
            "JAR-only runtime retained a raw-classpath fixture origin: "
            f"{rendered}"
        )
    extra_paths = [Path(raw).resolve(strict=False) for raw in cleanroom_extra_path]
    if extra_paths != [artifact]:
        rendered = ", ".join(str(entry) for entry in extra_paths) or "none"
        _fail(
            "JAR-only runtime must have the installed artifact as its sole "
            f"Cleanroom extra-path fixture origin; observed {rendered}"
        )
    return {
        "runtime_mode": "jar-only",
        "fixture_class": D01_CODE_SOURCE_PROBE_CLASS,
        "fixture_origin_count": 1,
        "raw_classpath_fixture_origin_count": 0,
        "extra_path_fixture_origin_count": 1,
        "installed_artifact_uri": artifact.as_uri(),
        "installed_artifact_sha256": inspected["sha256"],
        "classpath_entry_count": len(entries),
    }


def stage_cleanroom_dev_loop_artifact_v2(
    suite_root: Path | str,
    *,
    state_root: Path | str,
    built_artifact: Path | str,
) -> dict[str, Any]:
    """Install one exact built fixture JAR into ignored content-addressed state."""

    suite = Path(suite_root).resolve()
    state = _validate_state_location(
        suite, Path(os.path.abspath(os.fspath(Path(state_root).expanduser())))
    )
    source = _ordinary_file(Path(built_artifact), "built fixture artifact")
    inspected = _inspect_artifact(source)
    digest_hex = inspected["sha256"].removeprefix("sha256:")
    destination = state / "dev-loop-v2/artifacts" / digest_hex / source.name
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        installed = _ordinary_file(destination, "installed fixture artifact")
        if _sha256_file(installed) != inspected["sha256"]:
            _fail("content-addressed installed fixture artifact changed")
    else:
        temporary = destination.with_name(
            f".{destination.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_:
                shutil.copyfileobj(input_, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CleanroomDevLoopError(
                f"cannot install content-addressed fixture artifact: {exc}"
            ) from exc
    installed = _ordinary_file(destination, "installed fixture artifact")
    if _sha256_file(installed) != inspected["sha256"]:
        _fail("installed fixture artifact bytes differ from the exact build")
    return {
        **inspected,
        "built_uri": source.as_uri(),
        "installed_uri": installed.as_uri(),
        "built_sha256": inspected["sha256"],
        "installed_sha256": inspected["sha256"],
        "runtime_mode": "jar-only",
    }


def cleanroom_dev_loop_jar_only_runtime_argv_v2(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    *,
    side: str,
    installed_artifact: Path | str,
    code_source_agent: Path | str,
    probe_capture: Path | str,
    debug_port: int | None = None,
    workspace_root: Path | str | None = None,
) -> list[str]:
    """Compose the additive Gradle seam for one exact installed-JAR launch."""

    suite = Path(suite_root).resolve()
    reviewed = validate_cleanroom_dev_loop_plan(plan)
    if side not in reviewed["request"]["sides"]:
        _fail("JAR-only runtime side is outside the reviewed plan")
    artifact = _ordinary_file(Path(installed_artifact), "installed fixture artifact")
    agent = _ordinary_file(Path(code_source_agent), "D01 CodeSource agent")
    capture = Path(probe_capture)
    if not capture.is_absolute() or capture.exists():
        _fail("D01 CodeSource capture must be one absent absolute path")
    state = _local_uri(reviewed["state_root_uri"], "dev-loop state root")
    if not capture.parent.resolve(strict=False).is_relative_to(state):
        _fail("D01 CodeSource capture escaped the reviewed state root")
    init_script = _ordinary_file(
        suite / D01_JAR_ONLY_INIT_SCRIPT, "D01 JAR-only Gradle init script"
    )
    if debug_port is not None and (
        type(debug_port) is not int or not 1 <= debug_port <= 65535
    ):
        _fail("D01 debug port is outside the admitted TCP range")
    base = list(reviewed["stages"][side]["argv"])
    if workspace_root is not None:
        workspace = Path(workspace_root).resolve(strict=True)
        admitted = state / "dev-loop-v2/workspaces"
        if (
            not workspace.is_dir()
            or workspace.is_symlink()
            or not workspace.is_relative_to(admitted)
            or not (workspace / "build.gradle").is_file()
        ):
            _fail("D01 runtime workspace is not one admitted disposable clone")
        project_index = base.index("-p") + 1
        base[project_index] = str(workspace)
        cache_index = base.index("--project-cache-dir") + 1
        variant_root = workspace.parents[4]
        base[cache_index] = str(variant_root / ".gradle-project-cache")
    task = base.pop()
    expected_task = _RUNTIME_TASKS[side]
    if task != expected_task:
        _fail("reviewed dev-loop runtime task changed")
    result = [
        *base,
        "--init-script",
        str(init_script),
        f"-PworkbenchD01JarOnlyArtifact={artifact}",
        f"-PworkbenchD01CodeSourceAgent={agent}",
        f"-PworkbenchD01ProbeCapture={capture}",
        f"-PworkbenchD01ExpectedArtifactSha256={_sha256_file(artifact)}",
    ]
    if debug_port is not None:
        result.append(f"-PworkbenchD01DebugPort={debug_port}")
    result.append(task)
    return result


def stop_cleanroom_dev_loop_runtime_v2(
    *,
    frontend_pid: int,
    frontend_pgid: int,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Stop one exact owned D01 runtime group through installed core code.

    The caller retains and waits the frontend ``Popen`` handle.  This seam
    verifies Linux start identity (PID is still the selected group leader),
    snapshots every live member, sends signals only to that group, and refuses
    to report cleanup while a non-zombie member remains.  It cannot target the
    current Workbench group or a broad/unresolved identity.
    """

    if (
        type(frontend_pid) is not int
        or type(frontend_pgid) is not int
        or frontend_pid <= 1
        or frontend_pgid <= 1
        or frontend_pid != frontend_pgid
        or type(timeout_seconds) not in {int, float}
        or not 1.0 <= float(timeout_seconds) <= 300.0
    ):
        _fail("D01 owned runtime stop identity or timeout is invalid")
    if frontend_pgid == os.getpgrp():
        _fail("D01 owned runtime stop cannot target the Workbench process group")

    def stat_row(pid: int) -> tuple[str, int, int] | None:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            fields = raw[raw.rfind(")") + 2 :].split()
            return fields[0], int(fields[1]), int(fields[2])
        except (OSError, UnicodeError, ValueError, IndexError):
            return None

    def members() -> list[int]:
        rows: list[int] = []
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            observed = stat_row(int(path.name))
            if observed is not None and observed[0] != "Z" and observed[2] == frontend_pgid:
                rows.append(int(path.name))
        return sorted(rows)

    leader = stat_row(frontend_pid)
    before = members()
    if (
        leader is None
        or leader[2] != frontend_pgid
        or frontend_pid not in before
        or len(before) < 2
    ):
        _fail("D01 owned runtime stop cannot reproduce its live frontend/child group")
    started_at = time.time_ns()
    signals: list[str] = []
    try:
        os.killpg(frontend_pgid, signal.SIGTERM)
        signals.append("SIGTERM")
    except ProcessLookupError:
        _fail("D01 owned runtime group disappeared before controlled stop")
    deadline = time.monotonic() + float(timeout_seconds)
    while time.monotonic() < deadline and members():
        time.sleep(0.05)
    after = members()
    if after:
        os.killpg(frontend_pgid, signal.SIGKILL)
        signals.append("SIGKILL")
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline and members():
            time.sleep(0.05)
        after = members()
    if after:
        _fail(f"D01 owned runtime stop retained live group members: {after}")
    return {
        "format": "workbench-cleanroom-dev-loop-owned-stop-v2",
        "schema_version": 2,
        "frontend_pid": frontend_pid,
        "frontend_pgid": frontend_pgid,
        "process_inventory_before_stop": before,
        "process_inventory_after_stop": after,
        "remaining_descendant_count": 0,
        "signals": signals,
        "started_at_unix_ns": started_at,
        "finished_at_unix_ns": time.time_ns(),
        "stopped_through_workbench": True,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | (getattr(os, "O_NOFOLLOW", 0)),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise CleanroomDevLoopError(f"cannot retain dev-loop record: {exc}") from exc


def _materialize_server_eula(
    stage: Mapping[str, Any], target_root: Path
) -> dict[str, Any]:
    """Create the exact EULA only inside one freshly allocated server target."""

    declaration = _server_eula_declaration(target_root)
    if stage.get("preparation") != declaration:
        _fail("dev-loop server EULA preparation changed after review")
    try:
        target_metadata = target_root.lstat()
    except OSError as exc:
        raise CleanroomDevLoopError(
            f"isolated server target is unavailable for EULA preparation: {exc}"
        ) from exc
    if stat.S_ISLNK(target_metadata.st_mode) or not stat.S_ISDIR(
        target_metadata.st_mode
    ):
        _fail("isolated server target is not an ordinary directory")
    eula_path = _local_uri(
        declaration["target_uri"], "isolated server EULA target"
    )
    if eula_path.parent != target_root or eula_path.name != "eula.txt":
        _fail("isolated server EULA escaped its fresh target")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            eula_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(_SERVER_EULA_BYTES)
            output.flush()
            os.fsync(output.fileno())
        directory_descriptor = os.open(
            target_root,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        eula_path.unlink(missing_ok=True)
        raise CleanroomDevLoopError(
            f"cannot materialize isolated server EULA: {exc}"
        ) from exc
    return {**declaration, "state": "materialized"}


def execute_cleanroom_dev_loop(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    *,
    stage_custody_ports: CleanroomDevLoopStageCustodyPorts | None = None,
) -> dict[str, Any]:
    """Execute an exact plan, stopping immediately at the first failed stage."""

    suite = Path(suite_root).resolve()
    if stage_custody_ports is not None and not isinstance(
        stage_custody_ports, CleanroomDevLoopStageCustodyPorts
    ):
        _fail("dev-loop stage custody requires exact custody ports")
    reviewed = validate_cleanroom_dev_loop_plan(plan)
    state = _validate_state_location(
        suite,
        _local_uri(reviewed["state_root_uri"], "dev-loop state root"),
    )
    owner = _fixture_owner(suite)
    gradle = Path(reviewed["toolchain"]["gradle"]["path"])
    java = Path(reviewed["toolchain"]["java"]["home"])
    inputs = _inspect_owner_inputs(owner, gradle_cmd=gradle, java_home=java)
    if (
        inputs["fixture_digest"] != reviewed["fixture"]["digest"]
        or inputs["gradle"] != reviewed["toolchain"]["gradle"]
        or inputs["java"] != reviewed["toolchain"]["java"]
        or inputs["cleanup_init"] != reviewed["toolchain"]["cleanup_init"]
    ):
        _fail("dev-loop fixture or toolchain changed after review")
    build_argv, environment = _owner_build_argv(
        owner,
        gradle_cmd=gradle,
        java_home=java,
        state_root=state,
        materialize=True,
    )
    if build_argv != reviewed["stages"]["build"]["argv"]:
        _fail("dev-loop build command changed after review")
    project = Path(build_argv[build_argv.index("-p") + 1])
    run_root = _local_uri(reviewed["target_root_uri"], "dev-loop target root")
    try:
        run_root.mkdir(parents=True, mode=0o700)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise CleanroomDevLoopError(f"cannot create retained dev-loop state: {exc}") from exc
    plan_path = run_root / "plan.json"
    _write_json(plan_path, reviewed)
    rows: dict[str, Any] = {
        "build": {"state": "running"},
        **{side: {"state": "not-run"} for side in reviewed["request"]["sides"]},
    }
    receipt_body: dict[str, Any] = {
        "format": RECEIPT_FORMAT,
        "schema_version": 1,
        "kind": RECEIPT_KIND,
        "plan_id": reviewed["plan_id"],
        "outcome": "running",
        "artifact": None,
        "stages": rows,
        "target": {
            "run_root_uri": run_root.as_uri(),
            "plan_uri": plan_path.as_uri(),
            "receipt_uri": (run_root / "receipt.json").as_uri(),
        },
        "limitations": list(reviewed["limitations"]),
    }
    receipt_path = run_root / "receipt.json"

    def finish(outcome: str) -> dict[str, Any]:
        receipt_body["outcome"] = outcome
        receipt = _seal(RECEIPT_KIND, receipt_body, "receipt_id")
        _write_json(receipt_path, receipt)
        return {"outcome": outcome, "receipt": receipt, "receipt_path": str(receipt_path)}

    try:
        build_execution = _run_console(
            state=state,
            suite=suite,
            command_id="cleanroom-dev-build",
            argv=build_argv,
            cwd=project,
            environment=environment,
            renderer=_QuietRenderer(),
            plan=reviewed,
            stage="build",
            stage_custody_ports=stage_custody_ports,
        )
    except (RunnerError, SessionError, OSError, ValueError) as exc:
        rows["build"] = {"state": "failed", "problem": str(exc)}
        return finish("failed")
    build_result = build_execution["result"]
    build_session = build_execution["session"]
    if build_result is None:
        rows["build"] = _notify_stage_terminal(
            plan=reviewed,
            stage="build",
            execution=build_execution,
            stage_result={
                "state": "failed",
                "problem": build_execution["error"],
                "console_session_id": build_session.session_id,
                "console_session_uri": build_session.directory.as_uri(),
                "owner_ref": deepcopy(build_execution["owner_ref"]),
            },
            stage_custody_ports=stage_custody_ports,
        )
        return finish("failed")
    rows["build"] = {
        "state": "passed" if build_result.effective_exit_code == 0 else "failed",
        "effective_exit_code": build_result.effective_exit_code,
        "console_session_id": build_session.session_id,
        "console_session_uri": build_session.directory.as_uri(),
        "owner_ref": deepcopy(build_execution["owner_ref"]),
    }
    if rows["build"]["state"] != "passed":
        rows["build"] = _notify_stage_terminal(
            plan=reviewed,
            stage="build",
            execution=build_execution,
            stage_result=rows["build"],
            stage_custody_ports=stage_custody_ports,
        )
        return finish("failed")
    try:
        artifact = _inspect_artifact(
            _local_uri(
                reviewed["stages"]["build"]["expected_artifact_uri"],
                "dev-loop artifact",
            )
        )
    except CleanroomDevLoopError as exc:
        rows["build"]["state"] = "failed"
        rows["build"]["problem"] = str(exc)
        rows["build"] = _notify_stage_terminal(
            plan=reviewed,
            stage="build",
            execution=build_execution,
            stage_result=rows["build"],
            stage_custody_ports=stage_custody_ports,
        )
        return finish("failed")
    receipt_body["artifact"] = artifact
    rows["build"] = _notify_stage_terminal(
        plan=reviewed,
        stage="build",
        execution=build_execution,
        stage_result=rows["build"],
        stage_custody_ports=stage_custody_ports,
    )
    if rows["build"]["state"] != "passed":
        return finish("failed")

    for side in reviewed["request"]["sides"]:
        side_stage = reviewed["stages"][side]
        target = _local_uri(
            side_stage["target_root_uri"], f"dev-loop {side} target"
        )
        expected_argv = _runtime_argv(build_argv, side, target)
        if expected_argv != side_stage["argv"]:
            _fail(f"dev-loop {side} command changed after review")
        preparation: dict[str, Any] | None = None
        try:
            target.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            problem = f"cannot create isolated {side} target: {exc}"
            rows[side] = {
                "state": "failed",
                "problem": problem,
                "required_markers": list(side_stage["required_markers"]),
                "observed_markers": [],
                "cleanup": {"contained": False},
            }
            if side == "server":
                rows[side]["preparation"] = {
                    **deepcopy(side_stage["preparation"]),
                    "state": "failed",
                    "problem": problem,
                }
            break
        if side == "server":
            try:
                preparation = _materialize_server_eula(side_stage, target)
            except CleanroomDevLoopError as exc:
                rows[side] = {
                    "state": "failed",
                    "problem": str(exc),
                    "required_markers": list(side_stage["required_markers"]),
                    "observed_markers": [],
                    "cleanup": {"contained": True},
                    "preparation": {
                        **deepcopy(side_stage["preparation"]),
                        "state": "failed",
                        "problem": str(exc),
                    },
                }
                break
        marker_renderer = _MarkerStopRenderer(side_stage["required_markers"])
        try:
            side_execution = _run_console(
                state=state,
                suite=suite,
                command_id=f"cleanroom-dev-{side}",
                argv=expected_argv,
                cwd=project,
                environment=environment,
                renderer=marker_renderer,
                plan=reviewed,
                stage=side,
                stage_custody_ports=stage_custody_ports,
            )
        except (RunnerError, SessionError, OSError, ValueError) as exc:
            rows[side] = {
                "state": "failed",
                "problem": str(exc),
                "required_markers": list(side_stage["required_markers"]),
                "observed_markers": [
                    marker for marker in side_stage["required_markers"]
                    if marker in marker_renderer.observed
                ],
                "cleanup": {"contained": False},
            }
            if preparation is not None:
                rows[side]["preparation"] = preparation
            break
        run_result = side_execution["result"]
        run_session = side_execution["session"]
        if run_result is None:
            rows[side] = _notify_stage_terminal(
                plan=reviewed,
                stage=side,
                execution=side_execution,
                stage_result={
                    "state": "failed",
                    "problem": side_execution["error"],
                    "required_markers": list(side_stage["required_markers"]),
                    "observed_markers": [
                        marker for marker in side_stage["required_markers"]
                        if marker in marker_renderer.observed
                    ],
                    "console_session_id": run_session.session_id,
                    "console_session_uri": run_session.directory.as_uri(),
                    "owner_ref": deepcopy(side_execution["owner_ref"]),
                    "cleanup": {"contained": True},
                    **(
                        {"preparation": preparation}
                        if preparation is not None
                        else {}
                    ),
                },
                stage_custody_ports=stage_custody_ports,
            )
            break
        marker_complete = marker_renderer.observed == set(side_stage["required_markers"])
        # The supervisor calls this cancellation because it owns the generic
        # stop mechanism.  At this layer it is the planned controlled stop,
        # and only counts after every exact game marker was observed.
        controlled_stop = run_result.cancellation == "interrupt-requested"
        passed = marker_complete and (
            controlled_stop or run_result.effective_exit_code == 0
        )
        rows[side] = {
            "state": "passed" if passed else "failed",
            "effective_exit_code": run_result.effective_exit_code,
            "required_markers": list(side_stage["required_markers"]),
            "observed_markers": [
                marker for marker in side_stage["required_markers"]
                if marker in marker_renderer.observed
            ],
            "controlled_stop": controlled_stop,
            "console_session_id": run_session.session_id,
            "console_session_uri": run_session.directory.as_uri(),
            "owner_ref": deepcopy(side_execution["owner_ref"]),
            "artifact_sha256": artifact["sha256"],
            "cleanup": {"contained": True},
            **(
                {"preparation": preparation}
                if preparation is not None
                else {}
            ),
        }
        rows[side] = _notify_stage_terminal(
            plan=reviewed,
            stage=side,
            execution=side_execution,
            stage_result=rows[side],
            stage_custody_ports=stage_custody_ports,
        )
        if not passed:
            break
    passed = all(
        rows[side]["state"] == "passed" for side in reviewed["request"]["sides"]
    )
    return finish("passed" if passed else "failed")


def _validate_receipt_artifact(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {
        "uri", "sha256", "size", "mod_id", "version", "zip_integrity",
        "refmap", "bytecode",
    }:
        _fail("dev-loop receipt artifact shape changed")
    if (
        type(value.get("sha256")) is not str
        or _DIGEST.fullmatch(value["sha256"]) is None
        or type(value.get("size")) is not int
        or value["size"] < 1
        or value.get("mod_id") != "workbench_daily_loop"
        or value.get("version") != "1.0.0"
        or value.get("zip_integrity") != "verified"
        or value.get("refmap") != "present"
        or value.get("bytecode") != "verified"
    ):
        _fail("dev-loop receipt artifact identity changed")
    _local_uri(value.get("uri"), "dev-loop receipt artifact")
    return value


def _validate_receipt_owner(
    row: Mapping[str, Any], *, state_root: Path
) -> None:
    custody_fields = {
        "console_session_id", "console_session_uri", "owner_ref",
    }
    present = custody_fields.intersection(row)
    if not present:
        return
    if present != custody_fields:
        _fail("dev-loop receipt stage custody is incomplete")
    session_id = row["console_session_id"]
    owner = row["owner_ref"]
    if (
        type(session_id) is not str
        or _OWNER_RECORD_ID.fullmatch(session_id) is None
        or type(owner) is not dict
        or set(owner) != {
            "owner_id", "record_id", "record_kind", "uri", "digest",
            "last_verified_state", "verified_at",
        }
        or owner.get("owner_id") != "workbench-shell"
        or owner.get("record_id") != session_id
        or owner.get("record_kind") != "workbench-live-console-session-v1"
        or type(owner.get("digest")) is not str
        or _DIGEST.fullmatch(owner["digest"]) is None
        or owner.get("last_verified_state")
        not in {
            "allocated", "starting", "running", "complete", "failed",
            "cancelled", "incomplete",
        }
        or type(owner.get("verified_at")) is not str
        or not owner["verified_at"]
        or len(owner["verified_at"]) > 128
    ):
        _fail("dev-loop receipt owner reference changed")
    session_uri = _local_uri(
        row["console_session_uri"], "dev-loop receipt console session"
    )
    owner_uri = _local_uri(owner["uri"], "dev-loop receipt console owner")
    expected_session = (
        state_root / ".workbench/sessions/live-console" / session_id
    )
    if (
        session_uri != expected_session
        or owner_uri != session_uri / "session-v1.json"
    ):
        _fail("dev-loop receipt owner reference escaped its state root")


def _validate_receipt_preparation(
    value: Any, *, run_root: Path
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("dev-loop server preparation is not one object")
    state = value.get("state")
    expected_keys = {
        "operation", "scope", "target_uri", "sha256", "size", "state",
    }
    if state == "failed":
        expected_keys.add("problem")
    if (
        set(value) != expected_keys
        or {
            key: value.get(key)
            for key in ("operation", "scope", "target_uri", "sha256", "size")
        }
        != _server_eula_declaration(run_root / "server")
        or state not in {"materialized", "failed"}
        or (
            state == "failed"
            and (
                type(value.get("problem")) is not str
                or not value["problem"]
                or len(value["problem"]) > 4096
            )
        )
    ):
        _fail("dev-loop server preparation identity changed")
    return value


def _validate_receipt_stage(
    name: str,
    value: Any,
    *,
    run_root: Path,
    artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    if type(value) is not dict or value.get("state") not in {
        "not-run", "passed", "failed",
    }:
        _fail(f"dev-loop receipt {name} stage state changed")
    if value["state"] == "not-run":
        if value != {"state": "not-run"}:
            _fail(f"dev-loop receipt {name} not-run state changed")
        return value
    allowed = {
        "state", "problem", "effective_exit_code", "console_session_id",
        "console_session_uri", "owner_ref", "required_markers",
        "observed_markers", "controlled_stop", "artifact_sha256", "cleanup",
        "preparation",
    }
    if not set(value).issubset(allowed):
        _fail(f"dev-loop receipt {name} stage shape changed")
    problem = value.get("problem")
    if problem is not None and (
        type(problem) is not str or not problem or len(problem) > 4096
    ):
        _fail(f"dev-loop receipt {name} stage problem changed")
    exit_code = value.get("effective_exit_code")
    if exit_code is not None and (
        type(exit_code) is not int or not 0 <= exit_code <= 255
    ):
        _fail(f"dev-loop receipt {name} exit code changed")
    state_root = run_root.parents[1]
    _validate_receipt_owner(value, state_root=state_root)
    has_custody = "console_session_id" in value
    if name == "build":
        if set(value).intersection(
            {
                "required_markers", "observed_markers", "controlled_stop",
                "artifact_sha256", "cleanup", "preparation",
            }
        ):
            _fail("dev-loop receipt build stage has runtime-only fields")
        if value["state"] == "passed" and (
            exit_code != 0 or not has_custody or problem is not None
        ):
            _fail("dev-loop receipt build success changed")
        return value
    required = value.get("required_markers")
    observed = value.get("observed_markers")
    if (
        required != list(_REQUIRED_MARKERS[name])
        or type(observed) is not list
        or any(type(marker) is not str for marker in observed)
        or observed
        != [marker for marker in _REQUIRED_MARKERS[name] if marker in observed]
    ):
        _fail(f"dev-loop receipt {name} markers changed")
    cleanup = value.get("cleanup")
    if (
        type(cleanup) is not dict
        or set(cleanup) != {"contained"}
        or type(cleanup.get("contained")) is not bool
    ):
        _fail(f"dev-loop receipt {name} cleanup changed")
    controlled_stop = value.get("controlled_stop")
    if controlled_stop is not None and type(controlled_stop) is not bool:
        _fail(f"dev-loop receipt {name} stop classification changed")
    artifact_sha256 = value.get("artifact_sha256")
    if artifact_sha256 is not None and (
        artifact is None or artifact_sha256 != artifact["sha256"]
    ):
        _fail(f"dev-loop receipt {name} artifact binding changed")
    preparation = value.get("preparation")
    if name == "client" and preparation is not None:
        _fail("dev-loop client stage cannot prepare a server EULA")
    if preparation is not None:
        _validate_receipt_preparation(preparation, run_root=run_root)
    if value["state"] == "passed" and (
        problem is not None
        or not has_custody
        or artifact_sha256 is None
        or observed != required
        or cleanup["contained"] is not True
        or (controlled_stop is not True and exit_code != 0)
        or (
            name == "server"
            and (
                preparation is None
                or preparation.get("state") != "materialized"
            )
        )
    ):
        _fail(f"dev-loop receipt {name} success changed")
    return value


def validate_cleanroom_dev_loop_receipt(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the closed V1 receipt shape and its content identity."""

    if type(value) is not dict:
        _fail("dev-loop receipt is not one ordinary object")
    receipt = deepcopy(dict(value))
    required = {
        "format", "schema_version", "kind", "receipt_id", "plan_id",
        "outcome", "artifact", "stages", "target", "limitations",
    }
    body = dict(receipt)
    supplied = body.pop("receipt_id", None)
    if (
        set(receipt) != required
        or receipt.get("format") != RECEIPT_FORMAT
        or receipt.get("kind") != RECEIPT_KIND
        or receipt.get("schema_version") != 1
        or type(receipt.get("plan_id")) is not str
        or _PLAN_ID.fullmatch(receipt["plan_id"]) is None
        or receipt.get("outcome") not in {"passed", "failed"}
        or type(supplied) is not str
        or _RECEIPT_ID.fullmatch(supplied) is None
        or supplied != _seal(RECEIPT_KIND, body, "receipt_id")["receipt_id"]
    ):
        _fail("dev-loop receipt identity or shape changed")
    target = receipt.get("target")
    if type(target) is not dict or set(target) not in (
        {"run_root_uri", "receipt_uri"},
        {"run_root_uri", "plan_uri", "receipt_uri"},
    ):
        _fail("dev-loop receipt target shape changed")
    run_root = _local_uri(target.get("run_root_uri"), "dev-loop receipt run root")
    if (
        run_root.parent.name != "dev-loop"
        or _RUN_TOKEN.fullmatch(run_root.name) is None
        or _local_uri(target.get("receipt_uri"), "dev-loop receipt target")
        != run_root / "receipt.json"
    ):
        _fail("dev-loop receipt target identity changed")
    if "plan_uri" in target and (
        _local_uri(target["plan_uri"], "dev-loop retained plan")
        != run_root / "plan.json"
    ):
        _fail("dev-loop receipt retained plan escaped its run root")
    artifact = _validate_receipt_artifact(receipt.get("artifact"))
    stages = receipt.get("stages")
    if (
        type(stages) is not dict
        or "build" not in stages
        or not 2 <= len(stages) <= 3
        or not set(stages).issubset({"build", *_SIDES})
    ):
        _fail("dev-loop receipt stage set changed")
    for name, stage in stages.items():
        _validate_receipt_stage(
            name,
            stage,
            run_root=run_root,
            artifact=artifact,
        )
    if stages["build"]["state"] != "passed" and any(
        stages.get(side, {}).get("state") != "not-run"
        for side in _SIDES
        if side in stages
    ):
        _fail("dev-loop receipt ran a side after build failure")
    if (
        "client" in stages
        and "server" in stages
        and stages["client"]["state"] != "passed"
        and stages["server"]["state"] != "not-run"
    ):
        _fail("dev-loop receipt ran the server after client failure")
    all_passed = all(stage["state"] == "passed" for stage in stages.values())
    if (receipt["outcome"] == "passed") != all_passed:
        _fail("dev-loop receipt outcome changed")
    limitations = receipt.get("limitations")
    if (
        type(limitations) is not list
        or not limitations
        or len(limitations) > 32
        or any(
            type(item) is not str or not item or len(item) > 4096
            for item in limitations
        )
    ):
        _fail("dev-loop receipt limitations changed")
    return receipt


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON value {value}")


def _lexical_existing_path(value: Path | str, label: str) -> Path:
    supplied = Path(value).expanduser()
    absolute = supplied if supplied.is_absolute() else Path.cwd() / supplied
    lexical = Path(os.path.abspath(os.fspath(absolute)))
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise CleanroomDevLoopError(f"{label} is unavailable: {lexical}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            _fail(f"{label} cannot traverse a symbolic link: {current}")
    return lexical


def _read_bounded_json(path: Path, label: str) -> Any:
    ordinary = _lexical_existing_path(path, label)
    before = ordinary.lstat()
    if not stat.S_ISREG(before.st_mode):
        _fail(f"{label} must be an ordinary non-symlink file")
    if before.st_size > _MAX_RECORD_BYTES:
        _fail(f"{label} exceeds its record bound")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(ordinary, flags | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                _fail(f"{label} is not an ordinary file")
            chunks: list[bytes] = []
            remaining = _MAX_RECORD_BYTES + 1
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
    except OSError as exc:
        raise CleanroomDevLoopError(f"cannot read {label}: {exc}") from exc
    if len(raw) > _MAX_RECORD_BYTES:
        _fail(f"{label} exceeds its record bound")
    identities = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
    )
    if identities(before) != identities(opened) or identities(opened) != identities(after):
        _fail(f"{label} changed while it was read")
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise CleanroomDevLoopError(f"cannot parse {label}: {exc}") from exc


def load_cleanroom_dev_loop_receipt(
    receipt_path: Path | str,
) -> dict[str, Any]:
    """Load one exact retained receipt without following caller-controlled links."""

    path = _lexical_existing_path(receipt_path, "dev-loop receipt")
    receipt = validate_cleanroom_dev_loop_receipt(
        _read_bounded_json(path, "dev-loop receipt")
    )
    if _local_uri(
        receipt["target"]["receipt_uri"], "dev-loop receipt target"
    ) != path:
        _fail("dev-loop receipt content does not identify its storage path")
    return receipt


def find_cleanroom_dev_loop_stage(
    state_root: Path | str,
    owner_record_id: str,
) -> dict[str, Any]:
    """Join one live-console owner ID to its exact retained semantic stage."""

    if (
        type(owner_record_id) is not str
        or _OWNER_RECORD_ID.fullmatch(owner_record_id) is None
    ):
        _fail("dev-loop owner record ID is invalid")
    state = _lexical_existing_path(state_root, "dev-loop owner state root")
    state_metadata = state.lstat()
    if not stat.S_ISDIR(state_metadata.st_mode):
        _fail("dev-loop owner state root is not an ordinary directory")
    lane = _lexical_existing_path(state / "dev-loop", "dev-loop owner state lane")
    before = lane.lstat()
    if not stat.S_ISDIR(before.st_mode):
        _fail("dev-loop owner state lane is not an ordinary directory")
    try:
        with os.scandir(lane) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError as exc:
        raise CleanroomDevLoopError(
            f"cannot scan dev-loop owner state lane: {exc}"
        ) from exc
    if len(entries) > _MAX_RETAINED_RUNS:
        _fail("dev-loop owner state lane exceeds its retained-run bound")
    matches: list[dict[str, Any]] = []
    scanned_bytes = 0
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise CleanroomDevLoopError(
                f"cannot inspect dev-loop retained run: {entry.name}"
            ) from exc
        if (
            _RUN_TOKEN.fullmatch(entry.name) is None
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            _fail("dev-loop owner state lane contains an unsafe run entry")
        receipt_path = Path(entry.path) / "receipt.json"
        try:
            receipt_metadata = receipt_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise CleanroomDevLoopError(
                f"cannot inspect retained dev-loop receipt: {entry.name}"
            ) from exc
        if stat.S_ISLNK(receipt_metadata.st_mode) or not stat.S_ISREG(
            receipt_metadata.st_mode
        ):
            _fail("dev-loop owner state lane contains an unsafe receipt")
        scanned_bytes += receipt_metadata.st_size
        if scanned_bytes > _MAX_RECEIPT_SCAN_BYTES:
            _fail("dev-loop owner receipt scan exceeds its byte bound")
        receipt = load_cleanroom_dev_loop_receipt(receipt_path)
        for stage_name, stage_result in receipt["stages"].items():
            if stage_result.get("console_session_id") == owner_record_id:
                matches.append(
                    {
                        "receipt": receipt,
                        "stage": stage_name,
                        "stage_result": deepcopy(stage_result),
                    }
                )
    after = lane.lstat()
    lane_identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mtime_ns, item.st_ctime_ns,
    )
    if lane_identity(before) != lane_identity(after):
        _fail("dev-loop owner state lane changed while it was scanned")
    if not matches:
        _fail("dev-loop owner record has no retained semantic stage")
    if len(matches) != 1:
        _fail("dev-loop owner record is ambiguous across retained stages")
    return deepcopy(matches[0])


def recover_cleanroom_dev_loop(receipt_path: Path | str) -> dict[str, Any]:
    """Classify exact retained stage custody and return the retry boundary."""

    path = _lexical_existing_path(receipt_path, "dev-loop receipt")
    receipt = load_cleanroom_dev_loop_receipt(path)
    stages = receipt.get("stages")
    if type(stages) is not dict:
        _fail("dev-loop receipt has no stage state")
    # The live-console roots are two levels below the caller's state root:
    # <state>/.workbench/sessions/live-console/<session>.
    for row in stages.values():
        if not isinstance(row, Mapping) or not isinstance(row.get("console_session_id"), str):
            continue
        session_dir = _local_uri(row.get("console_session_uri"), "console session")
        try:
            state = session_dir.parents[3]
            owner = live_console_owner_reference(state, row["console_session_id"])
        except (IndexError, SessionError) as exc:
            raise CleanroomDevLoopError(
                f"cannot revalidate retained console custody: {exc}"
            ) from exc
        if owner["last_verified_state"] in {"allocated", "starting", "running", "incomplete"}:
            return {
                "format": RECOVERY_FORMAT,
                "schema_version": 1,
                "state": "blocked",
                "reason": "one exact owned process remains unresolved",
                "remaining_sides": [
                    side for side in _SIDES if stages.get(side, {}).get("state") != "passed"
                ],
                "next_action": None,
            }
    remaining = [
        side for side in _SIDES if side in stages and stages[side].get("state") != "passed"
    ]
    if not remaining:
        return {
            "format": RECOVERY_FORMAT,
            "schema_version": 1,
            "state": "clean",
            "reason": None,
            "remaining_sides": [],
            "next_action": None,
        }
    plan_uri = receipt["target"].get("plan_uri")
    if type(plan_uri) is not str:
        return {
            "format": RECOVERY_FORMAT,
            "schema_version": 1,
            "state": "blocked",
            "reason": "the failed run predates retained reconstruction inputs",
            "remaining_sides": remaining,
            "next_action": None,
        }
    plan_path = _local_uri(plan_uri, "dev-loop retained plan")
    plan = validate_cleanroom_dev_loop_plan(
        _read_bounded_json(plan_path, "dev-loop retained plan")
    )
    if (
        plan_path != _local_uri(plan_uri, "dev-loop retained plan")
        or plan["plan_id"] != receipt["plan_id"]
        or _local_uri(plan["target_root_uri"], "dev-loop retained plan target")
        != _local_uri(receipt["target"]["run_root_uri"], "dev-loop run root")
    ):
        _fail("dev-loop retained plan does not own this receipt")
    return {
        "format": RECOVERY_FORMAT,
        "schema_version": 1,
        "state": "retry-safe",
        "reason": None,
        "remaining_sides": remaining,
        "next_action": {
            "action_id": "dev.fixture-run",
            "mutation_posture": "creates-fresh-isolated-target",
            "source_plan_id": plan["plan_id"],
            "source_plan_uri": plan_uri,
            "reconstruction_inputs": {
                "gradle_cmd": plan["toolchain"]["gradle"]["path"],
                "java_home": plan["toolchain"]["java"]["home"],
                "state_root": str(
                    _local_uri(plan["state_root_uri"], "dev-loop state root")
                ),
                "sides": remaining,
                "debug": False,
            },
        },
    }


__all__ = [
    "D01_CODE_SOURCE_AGENT_SOURCE",
    "D01_CODE_SOURCE_PROBE_CLASS",
    "D01_JAR_ONLY_INIT_SCRIPT",
    "PLAN_FORMAT",
    "RECEIPT_FORMAT",
    "RECOVERY_FORMAT",
    "CleanroomDevLoopStageCustodyPorts",
    "CleanroomDevLoopError",
    "cleanroom_dev_loop_jar_only_runtime_argv_v2",
    "execute_cleanroom_dev_loop",
    "find_cleanroom_dev_loop_stage",
    "load_cleanroom_dev_loop_receipt",
    "materialize_cleanroom_dev_loop_workspace_pair_v2",
    "plan_cleanroom_dev_loop",
    "recover_cleanroom_dev_loop",
    "stage_cleanroom_dev_loop_artifact_v2",
    "stop_cleanroom_dev_loop_runtime_v2",
    "validate_cleanroom_dev_loop_jar_only_classpath_v2",
    "validate_cleanroom_dev_loop_plan",
    "validate_cleanroom_dev_loop_receipt",
]
