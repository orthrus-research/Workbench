#!/usr/bin/env python3

"""Resumable deterministic core façade for the Blueprints CLI lifecycle."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Iterator, NoReturn

from jsonschema import Draft202012Validator

from workbench_blueprints import lifecycle, planner, simulation, standards
from workbench_blueprints.layout import SCHEMA_ROOT, WORKBENCH_ROOT


REPO_ROOT = WORKBENCH_ROOT
SESSION_SCHEMA = SCHEMA_ROOT / "blueprints-session-v1.schema.json"
RESULT_SCHEMA = SCHEMA_ROOT / "blueprints-interface-result-v1.schema.json"
ENGINE_CONTRACT_ID = "BLUEPRINTS-EXECUTABLE-ENGINE-V1"
SESSION_FORMAT = "susy-blueprints-session-v1"
RESULT_FORMAT = "susy-blueprints-interface-result-v1"

COMMANDS = (
    "init",
    "plan",
    "simulate",
    "generate",
    "apply",
    "verify",
    "history",
    "export-proof",
)
RUN_STATES = {
    "initialized",
    "planned",
    "simulation-failed",
    "simulated",
    "released",
    "application-rejected",
    "applied",
    "verification-failed",
    "verified",
    "invalidated",
}


class InterfaceDiagnostic(Exception):
    """A stable fail-closed I01 diagnostic with a public exit class."""

    def __init__(
        self,
        code: str,
        location: str,
        message: str,
        *,
        exit_code: int = 4,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.location = location
        self.message = message
        self.exit_code = exit_code

    def __str__(self) -> str:
        return f"{self.code} {self.location}: {self.message}"


def _fail(
    code: str,
    location: str,
    message: str,
    *,
    exit_code: int = 4,
) -> NoReturn:
    raise InterfaceDiagnostic(
        code, location, message, exit_code=exit_code
    )


def _read_regular(path: Path, code: str) -> bytes:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
    except OSError as exc:
        _fail(code, str(path), str(exc))
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            _fail(code, str(path), "path is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("BPI102_JSON_DUPLICATE", key, "duplicate JSON object key")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    """Load one strict JSON object without following the final symlink."""

    content = _read_regular(path, "BPI101_INPUT_READ")
    try:
        value = json.loads(content, object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail("BPI103_JSON", str(path), str(exc))
    if not isinstance(value, dict):
        _fail("BPI103_JSON", str(path), "JSON root must be an object")
    return value


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("BPI104_SCHEMA", str(path), str(exc))
    if not isinstance(value, dict):
        _fail("BPI104_SCHEMA", str(path), "schema root is not an object")
    return value


def _pointer(parts: Any) -> str:
    encoded = [
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    ]
    return "/" + "/".join(encoded) if encoded else "/"


def _validate(value: dict[str, Any], schema_path: Path, source: str) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema(schema_path)).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        _fail(
            "BPI104_SCHEMA",
            f"{source}#{_pointer(error.absolute_path)}",
            error.message,
        )


def _diagnostic(
    code: str, location: str, message: str
) -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


def result_envelope(
    command: str,
    *,
    run: dict[str, Any] | None,
    status: str,
    exit_code: int,
    data: dict[str, Any] | None = None,
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "format": RESULT_FORMAT,
        "contract_id": ENGINE_CONTRACT_ID,
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "run_id": None if run is None else run["run_id"],
        "state": None if run is None else run["state"],
        "data": {} if data is None else copy.deepcopy(data),
        "diagnostics": (
            [] if diagnostics is None else copy.deepcopy(diagnostics)
        ),
    }
    _validate(value, RESULT_SCHEMA, "interface-result")
    return value


def rejected_result(
    command: str,
    diagnostic: InterfaceDiagnostic,
    *,
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return result_envelope(
        command,
        run=run,
        status="rejected",
        exit_code=diagnostic.exit_code,
        diagnostics=[
            _diagnostic(
                diagnostic.code, diagnostic.location, diagnostic.message
            )
        ],
    )


@dataclass
class AdapterSet:
    """Explicit non-serializable adapter boundary shared by core and CLI."""

    formatter_runner: planner.FormatterRunner | None = None
    hook_runner: planner.HookRunner | None = None
    dependency_provider: simulation.DependencyProvider | None = None
    post_checks: dict[str, lifecycle.PostCheck] = field(default_factory=dict)
    mutation_hook: lifecycle.MutationHook | None = None


def dependency_directory_provider(
    root: Path,
) -> simulation.DependencyProvider:
    """Return a local-only provider; the X01 cache still verifies lock digests."""

    if root.is_symlink():
        _fail(
            "BPI105_DEPENDENCY_SOURCE",
            str(root),
            "dependency source must be a non-symlink directory",
        )
    source_root = root.resolve()
    if not source_root.is_dir():
        _fail(
            "BPI105_DEPENDENCY_SOURCE",
            str(root),
            "dependency source must be a non-symlink directory",
        )

    def provide(definition: dict[str, Any]) -> bytes:
        identifier = definition.get("id")
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[a-z][a-z0-9._-]*", identifier) is None
        ):
            _fail(
                "BPI105_DEPENDENCY_SOURCE",
                "/dependency/id",
                "dependency id is unsafe",
            )
        return _read_regular(
            source_root / identifier, "BPI105_DEPENDENCY_SOURCE"
        )

    return provide


def _initial_run(request: dict[str, Any]) -> dict[str, Any]:
    run = {
        "schema_version": 1,
        "format": "susy-blueprints-run-v1",
        "contract_id": ENGINE_CONTRACT_ID,
        "request_id": request["request_id"],
        "plan_id": None,
        "target_state_id": request["target"]["target_state_id"],
        "output_mode": request["output_mode"],
        "state": "initialized",
        "candidate": None,
        "simulation": None,
        "release": None,
        "application": None,
        "verification": None,
        "invalidation": {
            "invalidated": False,
            "sequence": None,
            "cause": "none",
            "invalidated_ids": [],
        },
        "events": [
            {
                "sequence": 0,
                "phase": "init",
                "from_state": None,
                "to_state": "initialized",
                "result": "succeeded",
                "record_ids": [request["request_id"]],
            }
        ],
        "errors": [],
    }
    run["run_id"] = lifecycle._identity(
        "blueprints-run:sha256:", run, "run_id"
    )
    lifecycle._validate_run(run)
    return run


def _add_error(
    run: dict[str, Any],
    code: str,
    phase: str,
    summary: str,
    detail: Any,
    record_ids: list[str] | None = None,
) -> None:
    run["errors"].append(
        lifecycle._error(
            code,
            phase,
            summary,
            [] if record_ids is None else record_ids,
            detail,
        )
    )


def _admit(run: dict[str, Any], command: str, states: set[str]) -> None:
    lifecycle._validate_run(run)
    if run["state"] not in states:
        _fail(
            "BPI120_ILLEGAL_PREDECESSOR",
            f"/run/state/{run['state']}",
            f"{command} is not admitted from {run['state']}",
            exit_code=3,
        )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    content = standards.canonical_json(value).encode("utf-8")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=".interface.",
            delete=False,
        ) as handle:
            temporary = handle.name
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        lifecycle._fsync_directory(path.parent, "BPA109_STORE_COLLISION")
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class SessionStore:
    """Atomic current pointer over canonical content-addressed session states."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(os.path.abspath(os.fspath(workspace)))
        self.artifacts = lifecycle.ArtifactStore(
            self.workspace / "session-cas"
        )
        self.pointer_path = self.workspace / "current.json"
        self.lock_path = self.workspace / "interface.lock"
        if self.workspace.is_symlink():
            _fail(
                "BPI106_WORKSPACE",
                str(self.workspace),
                "workspace is a symlink",
            )
        if self.workspace.exists() and not self.workspace.is_dir():
            _fail(
                "BPI106_WORKSPACE",
                str(self.workspace),
                "workspace is not a directory",
            )

    @contextmanager
    def lock(self, *, create: bool = False) -> Iterator[None]:
        if create:
            self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not self.workspace.is_dir():
            _fail(
                "BPI108_SESSION_MISSING",
                str(self.workspace),
                "session has not been initialized",
                exit_code=3,
            )
        if self.workspace.is_symlink():
            _fail(
                "BPI106_WORKSPACE",
                str(self.workspace),
                "workspace is a symlink",
            )
        os.chmod(self.workspace, 0o700)
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            _fail("BPI107_SESSION_LOCK", str(self.lock_path), str(exc))
        try:
            lifecycle._fsync_directory(
                self.workspace, "BPA109_STORE_COLLISION"
            )
            yield
        finally:
            os.close(descriptor)
            self.lock_path.unlink(missing_ok=True)
            lifecycle._fsync_directory(
                self.workspace, "BPA109_STORE_COLLISION"
            )

    def exists(self) -> bool:
        return self.pointer_path.exists() or self.pointer_path.is_symlink()

    def save(self, session: dict[str, Any]) -> str:
        _validate_session(session)
        locator = self.artifacts.put_json(session)
        pointer = {
            "schema_version": 1,
            "format": "susy-blueprints-session-pointer-v1",
            "session_sha256": locator.rsplit(":", 1)[1],
        }
        _atomic_json(self.pointer_path, pointer)
        return locator

    def load(self) -> dict[str, Any]:
        if not self.exists():
            _fail(
                "BPI108_SESSION_MISSING",
                str(self.pointer_path),
                "session has not been initialized",
                exit_code=3,
            )
        content = _read_regular(self.pointer_path, "BPI109_SESSION_POINTER")
        try:
            pointer = json.loads(content, object_pairs_hook=_strict_object)
        except (UnicodeError, json.JSONDecodeError) as exc:
            _fail("BPI109_SESSION_POINTER", str(self.pointer_path), str(exc))
        expected_keys = {"schema_version", "format", "session_sha256"}
        if (
            not isinstance(pointer, dict)
            or set(pointer) != expected_keys
            or pointer["schema_version"] != 1
            or pointer["format"] != "susy-blueprints-session-pointer-v1"
            or not isinstance(pointer["session_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", pointer["session_sha256"]) is None
            or standards.canonical_json(pointer).encode("utf-8") != content
        ):
            _fail(
                "BPI109_SESSION_POINTER",
                str(self.pointer_path),
                "session pointer is not canonical or valid",
            )
        session = self.artifacts.read_json(
            "local-blueprints-artifact:sha256:"
            + pointer["session_sha256"]
        )
        _validate_session(session)
        return session


def _validate_session(session: dict[str, Any]) -> None:
    _validate(session, SESSION_SCHEMA, "session")
    planner._validate_target_manifest(session["target_manifest"])
    planner._validate_schema(
        session["request"], planner.REQUEST_SCHEMA, "request"
    )
    expected_request = lifecycle._identity(
        "blueprints-request:sha256:", session["request"], "request_id"
    )
    if session["request"]["request_id"] != expected_request:
        _fail("BPI110_SESSION_BINDING", "/request", "request identity drift")
    if (
        session["request"]["target"]
        != planner.target_from_manifest(session["target_manifest"])
        or session["run"]["request_id"] != session["request"]["request_id"]
        or session["run"]["target_state_id"]
        != session["target_manifest"]["target_state_id"]
    ):
        _fail(
            "BPI110_SESSION_BINDING",
            "/",
            "session request, target, and run bindings differ",
        )
    lifecycle._validate_run(session["run"])
    run = session["run"]
    state = run["state"]
    required = {
        "initialized": (False, False, False, False, False),
        "planned": (True, False, False, False, False),
        "simulation-failed": (True, True, False, False, False),
        "simulated": (True, True, False, False, False),
        "released": (True, True, True, False, False),
        "application-rejected": (True, True, True, True, False),
        "applied": (True, True, True, True, False),
        "verification-failed": (True, True, True, True, True),
        "verified": (True, True, True, True, True),
    }
    if state in required:
        observed = (
            run["candidate"] is not None,
            run["simulation"] is not None,
            run["release"] is not None,
            run["application"] is not None,
            run["verification"] is not None,
        )
        if observed != required[state]:
            _fail(
                "BPI110_SESSION_BINDING",
                "/run",
                "run records do not close its current state",
            )
        if (run["plan_id"] is not None) != required[state][0]:
            _fail(
                "BPI110_SESSION_BINDING",
                "/run/plan_id",
                "plan identity does not close the current state",
            )
    planning_result = session["planning_result"]
    if planning_result is not None:
        if planning_result.get("request") != session["request"]:
            _fail(
                "BPI110_SESSION_BINDING",
                "/planning_result/request",
                "planning result request drift",
            )
        plan = planning_result.get("plan")
        if isinstance(plan, dict):
            planner._validate_schema(plan, planner.PLAN_SCHEMA, "plan")
            if plan["status"] == "ready" and (
                session["run"]["plan_id"] != plan["plan_id"]
                or planning_result.get("candidate")
                != session["run"]["candidate"]
            ):
                _fail(
                    "BPI110_SESSION_BINDING",
                    "/planning_result",
                    "ready plan/candidate differs from the run",
                )
    simulation_result = session["simulation_result"]
    if simulation_result is not None:
        if simulation_result.get("simulation") != session["run"]["simulation"]:
            _fail(
                "BPI110_SESSION_BINDING",
                "/simulation_result",
                "simulation result differs from run",
            )
    lifecycle_context = session["lifecycle_context"]
    if (
        lifecycle_context is not None
        and lifecycle_context.get("run") != session["run"]
    ):
        _fail(
            "BPI110_SESSION_BINDING",
            "/lifecycle_context/run",
            "lifecycle context differs from current run",
        )


class BlueprintsCore:
    """One stateful core used without semantic changes by every I01 adapter."""

    def __init__(
        self,
        workspace: Path,
        *,
        adapters: AdapterSet | None = None,
    ) -> None:
        self.workspace = Path(os.path.abspath(os.fspath(workspace)))
        self.store = SessionStore(self.workspace)
        self.adapters = AdapterSet() if adapters is None else adapters

    @staticmethod
    def _configuration(
        *,
        target_repository: Path,
        repository_id: str,
        registry_root: Path,
        asset_root: Path,
        ledger_path: Path,
    ) -> dict[str, str]:
        target = target_repository.resolve()
        registry = registry_root.resolve()
        assets = asset_root.resolve()
        ledger = ledger_path.resolve()
        if not target.is_dir():
            _fail(
                "BPI111_CONFIGURATION",
                str(target),
                "target repository is not a directory",
            )
        if not registry.is_dir() or not assets.is_dir() or not ledger.is_file():
            _fail(
                "BPI111_CONFIGURATION",
                "/configuration",
                "registry, asset root, or ledger path is unavailable",
            )
        if re.fullmatch(r"[a-z][a-z0-9._-]*", repository_id) is None:
            _fail(
                "BPI111_CONFIGURATION",
                "/repository-id",
                "repository id is invalid",
            )
        return {
            "target_repository": str(target),
            "repository_id": repository_id,
            "registry_root": str(registry),
            "asset_root": str(assets),
            "ledger_path": str(ledger),
        }

    def _require_protected_workspace(self, target_repository: Path) -> None:
        protected = (
            target_repository.resolve() / ".workbench/blueprints"
        )
        try:
            relative = self.workspace.relative_to(protected)
        except ValueError:
            _fail(
                "BPI106_WORKSPACE",
                str(self.workspace),
                "workspace must be under target/.workbench/blueprints",
            )
        if not relative.parts:
            _fail(
                "BPI106_WORKSPACE",
                str(self.workspace),
                "workspace must name one session below the protected root",
            )
        cursor = target_repository.resolve()
        for part in (
            Path(".workbench/blueprints") / relative
        ).parts:
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                _fail(
                    "BPI106_WORKSPACE",
                    str(cursor),
                    "workspace path contains a symlink",
                )

    def _paths(
        self, session: dict[str, Any]
    ) -> tuple[dict[str, str], Path]:
        configuration = session["configuration"]
        target = Path(configuration["target_repository"])
        self._require_protected_workspace(target)
        return configuration, target

    def _planner(self, session: dict[str, Any]) -> planner.Planner:
        configuration, target = self._paths(session)
        return planner.Planner(
            registry_root=Path(configuration["registry_root"]),
            asset_root=Path(configuration["asset_root"]),
            ledger_path=Path(configuration["ledger_path"]),
            target_repository=target,
            sealed_store=planner.SealedStore(self.workspace / "sealed"),
            formatter_runner=self.adapters.formatter_runner,
            hook_runner=self.adapters.hook_runner,
        )

    def _simulator(self, session: dict[str, Any]) -> simulation.Simulator:
        configuration, target = self._paths(session)
        return simulation.Simulator(
            registry_root=Path(configuration["registry_root"]),
            asset_root=Path(configuration["asset_root"]),
            ledger_path=Path(configuration["ledger_path"]),
            target_repository=target,
            sealed_store=planner.SealedStore(self.workspace / "sealed"),
            dependency_cache=simulation.DependencyCache(
                self.workspace / "dependencies"
            ),
            evidence_store=simulation.SimulationEvidenceStore(
                self.workspace / "simulation-evidence"
            ),
            workspace_root=self.workspace / "simulation-workspaces",
            dependency_provider=self.adapters.dependency_provider,
            formatter_runner=self.adapters.formatter_runner,
            hook_runner=self.adapters.hook_runner,
        )

    def _lifecycle(self, session: dict[str, Any]) -> lifecycle.LifecycleEngine:
        configuration, target = self._paths(session)
        return lifecycle.LifecycleEngine(
            registry_root=Path(configuration["registry_root"]),
            asset_root=Path(configuration["asset_root"]),
            ledger_path=Path(configuration["ledger_path"]),
            target_repository=target,
            sealed_store=planner.SealedStore(self.workspace / "sealed"),
            simulation_evidence_store=simulation.SimulationEvidenceStore(
                self.workspace / "simulation-evidence"
            ),
            artifact_store=lifecycle.ArtifactStore(
                self.workspace / "release"
            ),
            history_store=lifecycle.HistoryStore(
                self.workspace / "history"
            ),
            mutation_hook=self.adapters.mutation_hook,
        )

    def init(
        self,
        *,
        target_repository: Path,
        repository_id: str,
        registry_root: Path,
        asset_root: Path,
        ledger_path: Path,
        intake: dict[str, Any],
    ) -> dict[str, Any]:
        configuration = self._configuration(
            target_repository=target_repository,
            repository_id=repository_id,
            registry_root=registry_root,
            asset_root=asset_root,
            ledger_path=ledger_path,
        )
        self._require_protected_workspace(
            Path(configuration["target_repository"])
        )
        if self.store.exists():
            _fail(
                "BPI112_ALREADY_INITIALIZED",
                str(self.workspace),
                "session is already initialized",
                exit_code=3,
            )
        target_manifest = planner.capture_target_state(
            Path(configuration["target_repository"]), repository_id
        )
        request = planner.compile_request(intake, target_manifest)
        run = _initial_run(request)
        with self.store.lock(create=True):
            if self.store.exists():
                _fail(
                    "BPI112_ALREADY_INITIALIZED",
                    str(self.workspace),
                    "session is already initialized",
                    exit_code=3,
                )
            observed = planner.capture_target_state(
                Path(configuration["target_repository"]), repository_id
            )
            if observed != target_manifest:
                _fail(
                    "BPI114_INIT_TARGET_RACE",
                    configuration["target_repository"],
                    "target changed while the session was initialized",
                )
            session = {
                "schema_version": 1,
                "format": SESSION_FORMAT,
                "contract_id": ENGINE_CONTRACT_ID,
                "configuration": configuration,
                "intake": copy.deepcopy(intake),
                "target_manifest": target_manifest,
                "request": request,
                "planning_evidence": None,
                "choices": {},
                "environment_lock": None,
                "planning_result": None,
                "simulation_result": None,
                "lifecycle_context": None,
                "run": run,
                "last_history_locator": None,
            }
            self.store.save(session)
        return result_envelope(
            "init",
            run=run,
            status="succeeded",
            exit_code=0,
            data={"request": request},
        )

    def plan(
        self,
        planning_evidence: dict[str, Any],
        *,
        choices: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(
                run,
                "plan",
                {
                    "initialized",
                    "simulation-failed",
                    "application-rejected",
                    "verification-failed",
                },
            )
            normalized_choices = {} if choices is None else copy.deepcopy(choices)
            planning_result = self._planner(session).execute(
                session["intake"],
                session["target_manifest"],
                planning_evidence,
                choices=normalized_choices,
                edit_generation=(
                    0
                    if run["candidate"] is None
                    else run["candidate"]["edit_generation"]
                ),
            )
            plan = planning_result["plan"]
            candidate = planning_result["candidate"]
            session["planning_evidence"] = copy.deepcopy(planning_evidence)
            session["choices"] = normalized_choices
            session["planning_result"] = planning_result
            if plan is None or plan["status"] != "ready" or candidate is None:
                diagnostics = planning_result["diagnostics"]
                _add_error(
                    run,
                    "BPI130_PLAN_BLOCKED",
                    "plan",
                    "Planning did not produce a ready candidate.",
                    diagnostics,
                    [] if plan is None else [plan["plan_id"]],
                )
                lifecycle._append_event(
                    run,
                    "plan",
                    run["state"],
                    "failed",
                    [] if plan is None else [plan["plan_id"]],
                )
                status = "failed"
                exit_code = 5
                public_diagnostics = [
                    _diagnostic(
                        "BPI130_PLAN_BLOCKED",
                        "/plan",
                        "Planning did not produce a ready candidate.",
                    )
                ]
            else:
                if (
                    run["candidate"] is not None
                    and run["candidate"]["candidate_id"]
                    != candidate["candidate_id"]
                ):
                    _fail(
                        "BPI121_REINITIALIZATION_REQUIRED",
                        "/candidate",
                        "a different candidate requires a new initialized session",
                        exit_code=3,
                    )
                run["plan_id"] = plan["plan_id"]
                run["candidate"] = copy.deepcopy(candidate)
                run["simulation"] = None
                run["release"] = None
                run["application"] = None
                run["verification"] = None
                session["simulation_result"] = None
                session["environment_lock"] = None
                session["lifecycle_context"] = None
                lifecycle._append_event(
                    run,
                    "plan",
                    "planned",
                    "succeeded",
                    [plan["plan_id"], candidate["candidate_id"]],
                )
                status = "succeeded"
                exit_code = 0
                public_diagnostics = []
            session["run"] = run
            self.store.save(session)
        return result_envelope(
            "plan",
            run=run,
            status=status,
            exit_code=exit_code,
            data={
                "plan": plan,
                "candidate": candidate,
                "compliant_revisions": planning_result[
                    "compliant_revisions"
                ],
            },
            diagnostics=public_diagnostics,
        )

    def simulate(self, environment_lock: dict[str, Any]) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(run, "simulate", {"planned"})
            planning_result = session["planning_result"]
            planning_evidence = session["planning_evidence"]
            if planning_result is None or planning_evidence is None:
                _fail(
                    "BPI110_SESSION_BINDING",
                    "/planning",
                    "planned session inputs are missing",
                )
            simulation_result = self._simulator(session).execute(
                planning_result,
                intake=session["intake"],
                target_manifest=session["target_manifest"],
                planning_evidence=planning_evidence,
                environment_lock=environment_lock,
                choices=session["choices"],
                edit_generation=run["candidate"]["edit_generation"],
            )
            simulation_record = simulation_result["simulation"]
            run["simulation"] = copy.deepcopy(simulation_record)
            passed = simulation_record["status"] == "passed"
            if not passed:
                failed_gates = [
                    row["stage_id"]
                    for row in simulation_record["gates"]
                    if row["status"] != "passed"
                ]
                _add_error(
                    run,
                    "BPI131_SIMULATION_FAILED",
                    "simulate",
                    "One or more required simulation gates did not pass.",
                    failed_gates,
                    [simulation_record["simulation_id"]],
                )
            lifecycle._append_event(
                run,
                "simulate",
                "simulated" if passed else "simulation-failed",
                "succeeded" if passed else "failed",
                [simulation_record["simulation_id"]],
            )
            session["environment_lock"] = simulation.compile_environment_lock(
                environment_lock
            )
            session["simulation_result"] = simulation_result
            session["run"] = run
            self.store.save(session)
        return result_envelope(
            "simulate",
            run=run,
            status="succeeded" if passed else "failed",
            exit_code=0 if passed else 5,
            data={"simulation": simulation_record},
            diagnostics=(
                []
                if passed
                else [
                    _diagnostic(
                        "BPI131_SIMULATION_FAILED",
                        "/simulation/gates",
                        "One or more required simulation gates did not pass.",
                    )
                ]
            ),
        )

    def generate(self) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(run, "generate", {"simulated"})
            if (
                session["planning_result"] is None
                or session["simulation_result"] is None
                or session["environment_lock"] is None
            ):
                _fail(
                    "BPI110_SESSION_BINDING",
                    "/",
                    "release inputs are missing",
                )
            context = self._lifecycle(session).release(
                session["planning_result"],
                session["simulation_result"],
                target_manifest=session["target_manifest"],
                environment_lock=session["environment_lock"],
                prior_run=run,
            )
            run = context["run"]
            released = context.get("release") is not None
            session["lifecycle_context"] = context
            session["run"] = run
            session["last_history_locator"] = context.get("history_locator")
            self.store.save(session)
        return result_envelope(
            "generate",
            run=run,
            status="succeeded" if released else "failed",
            exit_code=0 if released else 5,
            data=(
                {
                    "release": context["release"],
                    "delivery": context["delivery"],
                    "proof": context["proof"],
                }
                if released
                else {}
            ),
            diagnostics=[
                _diagnostic(code, "/generate", "Release was not admitted.")
                for code in context.get("diagnostics", [])
            ],
        )

    def apply(self) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(run, "apply", {"released"})
            if run["output_mode"] != "direct-apply":
                _fail(
                    "BPI122_OUTPUT_MODE",
                    "/run/output_mode",
                    "apply requires a direct-apply release",
                    exit_code=3,
                )
            context = session["lifecycle_context"]
            if context is None:
                _fail(
                    "BPI110_SESSION_BINDING",
                    "/lifecycle_context",
                    "released context is missing",
                )
            context = self._lifecycle(session).apply(context)
            run = context["run"]
            applied = context["application"]["status"] == "applied"
            session["lifecycle_context"] = context
            session["run"] = run
            session["last_history_locator"] = context.get("history_locator")
            self.store.save(session)
        return result_envelope(
            "apply",
            run=run,
            status="succeeded" if applied else "failed",
            exit_code=0 if applied else 5,
            data={"application": context["application"]},
            diagnostics=[
                _diagnostic(code, "/apply", "Application was rejected.")
                for code in context.get("diagnostics", [])
            ],
        )

    def verify(self) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(run, "verify", {"applied", "verification-failed"})
            context = session["lifecycle_context"]
            if context is None:
                _fail(
                    "BPI110_SESSION_BINDING",
                    "/lifecycle_context",
                    "application context is missing",
                )
            context = self._lifecycle(session).verify(
                context, post_checks=self.adapters.post_checks
            )
            run = context["run"]
            passed = context["verification"]["status"] == "passed"
            session["lifecycle_context"] = context
            session["run"] = run
            session["last_history_locator"] = context.get("history_locator")
            self.store.save(session)
        data: dict[str, Any] = {
            "verification": context["verification"]
        }
        if passed:
            data["proof"] = context["proof"]
        return result_envelope(
            "verify",
            run=run,
            status="succeeded" if passed else "failed",
            exit_code=0 if passed else 5,
            data=data,
            diagnostics=[
                _diagnostic(code, "/verify", "Verification did not pass.")
                for code in context.get("diagnostics", [])
            ],
        )

    def _history_descriptors(
        self,
        session: dict[str, Any],
        store: lifecycle.HistoryStore,
    ) -> list[dict[str, Any]]:
        descriptors: dict[str, dict[str, Any]] = {}
        context = session["lifecycle_context"]
        if context is not None:
            for row in self._lifecycle(session)._history_descriptors(context):
                descriptors[row["artifact_id"]] = row
        planning_evidence = session["planning_evidence"]
        if planning_evidence is not None:
            locator = store.artifacts.put_json(planning_evidence)
            descriptors["planning-evidence"] = lifecycle.HistoryStore.descriptor(
                "planning-evidence",
                "evidence",
                locator.rsplit(":", 1)[1],
                "local-private",
            )
        environment_lock = session["environment_lock"]
        if environment_lock is not None:
            locator = store.artifacts.put_json(environment_lock)
            descriptors["environment-lock"] = lifecycle.HistoryStore.descriptor(
                "environment-lock",
                "manifest",
                locator.rsplit(":", 1)[1],
                "local-private",
            )
        candidate = session["run"]["candidate"]
        if candidate is not None and "sealed-candidate" not in descriptors:
            descriptors["sealed-candidate"] = lifecycle.HistoryStore.descriptor(
                "sealed-candidate",
                "source",
                candidate["sealed_locator"].rsplit(":", 1)[1],
                "local-private",
            )
        simulation_result = session["simulation_result"]
        if (
            simulation_result is not None
            and "simulation-evidence" not in descriptors
        ):
            locator = simulation_result["evidence_locator"]
            descriptors["simulation-evidence"] = (
                lifecycle.HistoryStore.descriptor(
                    "simulation-evidence",
                    "evidence",
                    locator.rsplit(":", 1)[1],
                    "local-private",
                )
            )
        return [descriptors[key] for key in sorted(descriptors)]

    def history(self) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(run, "history", RUN_STATES)
            lifecycle._append_event(
                run, "history", run["state"], "no-op", []
            )
            session["run"] = run
            context = session["lifecycle_context"]
            if context is not None:
                context["run"] = run
                session["lifecycle_context"] = context
            history_store = lifecycle.HistoryStore(
                self.workspace / "history"
            )
            _history_sha, locator = history_store.record(
                run, self._history_descriptors(session, history_store)
            )
            session["last_history_locator"] = locator
            self.store.save(session)
            manifest = history_store.artifacts.read_json(locator)
        return result_envelope(
            "history",
            run=run,
            status="succeeded",
            exit_code=0,
            data={"history": manifest},
        )

    def export_proof(self) -> dict[str, Any]:
        with self.store.lock():
            session = self.store.load()
            run = session["run"]
            _admit(run, "export-proof", {"released", "verified"})
            if (
                run["state"] == "released"
                and run["output_mode"] == "direct-apply"
            ):
                _fail(
                    "BPI122_OUTPUT_MODE",
                    "/run/output_mode",
                    "direct-apply proof requires verified state",
                    exit_code=3,
                )
            context = session["lifecycle_context"]
            if context is None:
                _fail(
                    "BPI110_SESSION_BINDING",
                    "/lifecycle_context",
                    "proof context is missing",
                )
            context = self._lifecycle(session).export_proof(context)
            run = context["run"]
            session["lifecycle_context"] = context
            session["run"] = run
            session["last_history_locator"] = context.get("history_locator")
            self.store.save(session)
        return result_envelope(
            "export-proof",
            run=run,
            status="succeeded",
            exit_code=0,
            data={
                "proof": context["proof"],
                "export_bundle": context["export_bundle"],
            },
        )
