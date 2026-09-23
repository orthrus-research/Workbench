#!/usr/bin/env python3

"""Blueprints A01 release, transaction, history, verification, and proof core."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Callable, NoReturn

from jsonschema import Draft202012Validator

from workbench_blueprints import planner, simulation, standards
from workbench_blueprints.layout import SCHEMA_ROOT, WORKBENCH_ROOT


REPO_ROOT = WORKBENCH_ROOT
RELEASE_BUNDLE_SCHEMA = (
    SCHEMA_ROOT / "blueprints-release-bundle-v1.schema.json"
)
HISTORY_SCHEMA = (
    SCHEMA_ROOT / "blueprints-history-manifest-v1.schema.json"
)
PROOF_EXPORT_SCHEMA = (
    SCHEMA_ROOT / "blueprints-proof-export-v1.schema.json"
)
ENGINE_CONTRACT_ID = "BLUEPRINTS-EXECUTABLE-ENGINE-V1"
RETENTION_POLICY_ID = "BLUEPRINTS-LOCAL-RETENTION-V1"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

MutationHook = Callable[[int, str], None]
PostCheck = Callable[[Path], tuple[bool, Any]]


class LifecycleDiagnostic(Exception):
    """A stable fail-closed A01 diagnostic."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.location = location
        self.message = message

    def __str__(self) -> str:
        return f"{self.code} {self.location}: {self.message}"


def _fail(code: str, location: str, message: str) -> NoReturn:
    raise LifecycleDiagnostic(code, location, message)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(standards.canonical_json(value).encode("utf-8"))


def _identity(prefix: str, value: dict[str, Any], field: str) -> str:
    projected = copy.deepcopy(value)
    projected.pop(field, None)
    return prefix + _digest_json(projected)


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail("BPA100_SCHEMA_READ", str(path), str(exc))
    if not isinstance(schema, dict):
        _fail("BPA100_SCHEMA_READ", str(path), "schema root is not an object")
    return schema


def _pointer(parts: Any) -> str:
    encoded = [
        str(part).replace("~", "~0").replace("/", "~1") for part in parts
    ]
    return "/" + "/".join(encoded) if encoded else "/"


def _validate(value: dict[str, Any], path: Path, source: str) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema(path)).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        _fail(
            "BPA101_SCHEMA",
            f"{source}#{_pointer(error.absolute_path)}",
            error.message,
        )


def _read_regular(path: Path, code: str) -> bytes:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
    except OSError as exc:
        _fail(code, str(path), str(exc))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
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


def _fsync_directory(path: Path, code: str) -> None:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
    except OSError as exc:
        _fail(code, str(path), str(exc))
    try:
        os.fsync(descriptor)
    except OSError as exc:
        _fail(code, str(path), str(exc))
    finally:
        os.close(descriptor)


def _safe_target(
    root: Path, relative: str, *, create_parents: bool
) -> tuple[Path, list[Path]]:
    planner._safe_path(relative, directory=False, location=relative)
    cursor = root
    created: list[Path] = []
    for part in PurePosixPath(relative).parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail(
                "BPA102_PATH_SYMLINK",
                relative,
                "operation path contains a symlinked parent",
            )
        if cursor.exists():
            if not cursor.is_dir():
                _fail(
                    "BPA103_PATH_KIND",
                    relative,
                    "operation parent is not a directory",
                )
        elif create_parents:
            cursor.mkdir(mode=0o755)
            _fsync_directory(cursor.parent, "BPA104_PARENT_MISSING")
            created.append(cursor)
        else:
            _fail(
                "BPA104_PARENT_MISSING",
                relative,
                "operation parent is missing",
            )
    return root.joinpath(*PurePosixPath(relative).parts), created


def _path_content(path: Path, row: dict[str, Any]) -> bytes:
    if row["kind"] == "symlink":
        try:
            content = os.readlink(os.fsencode(path))
        except OSError as exc:
            _fail("BPA105_TARGET_RACE", row["path"], str(exc))
    else:
        content = _read_regular(path, "BPA105_TARGET_RACE")
    if _digest_bytes(content) != row["worktree_sha256"]:
        _fail(
            "BPA105_TARGET_RACE",
            row["path"],
            "target bytes differ from the accepted manifest",
        )
    return content


def _content_record(kind: str, mode: str, content: bytes) -> dict[str, Any]:
    return {
        "kind": kind,
        "mode": mode,
        "sha256": _digest_bytes(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _decode_content(record: dict[str, Any]) -> bytes:
    try:
        content = base64.b64decode(record["content_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        _fail("BPA106_CONTENT_ENCODING", "/", str(exc))
    if _digest_bytes(content) != record["sha256"]:
        _fail("BPA107_CONTENT_DIGEST", "/", "released content digest drift")
    return content


class ArtifactStore:
    """Mode-safe content-addressed storage for released and history artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.is_symlink():
            _fail("BPA108_STORE_ROOT", str(self.root), "store root is a symlink")
        if self.root.exists() and not self.root.is_dir():
            _fail(
                "BPA108_STORE_ROOT", str(self.root), "store root is not a directory"
            )

    def _path(self, digest: str) -> Path:
        return self.root / "objects" / digest[:2] / digest

    def put_bytes(self, content: bytes) -> str:
        digest = _digest_bytes(content)
        path = self._path(digest)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        objects = self.root / "objects"
        objects.mkdir(mode=0o700, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if any(item.is_symlink() for item in (self.root, objects, path.parent)):
            _fail(
                "BPA108_STORE_ROOT",
                str(path.parent),
                "store path contains a symlink",
            )
        if path.exists():
            if path.is_symlink() or _read_regular(
                path, "BPA109_STORE_COLLISION"
            ) != content:
                _fail(
                    "BPA109_STORE_COLLISION",
                    str(path),
                    "content-addressed artifact collision",
                )
        else:
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=".artifact.",
                    delete=False,
                ) as handle:
                    temporary = handle.name
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                _fsync_directory(path.parent, "BPA109_STORE_COLLISION")
                temporary = None
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        os.chmod(self.root, 0o700)
        os.chmod(path, 0o600)
        return "local-blueprints-artifact:sha256:" + digest

    def put_json(self, value: dict[str, Any]) -> str:
        return self.put_bytes(standards.canonical_json(value).encode("utf-8"))

    def read_bytes(self, locator: str) -> bytes:
        match = re.fullmatch(
            r"local-blueprints-artifact:sha256:([0-9a-f]{64})", locator
        )
        if match is None:
            _fail("BPA110_ARTIFACT_LOCATOR", "/", "invalid artifact locator")
        digest = match.group(1)
        path = self._path(digest)
        if (
            self.root.is_symlink()
            or path.parent.is_symlink()
            or path.is_symlink()
        ):
            _fail("BPA108_STORE_ROOT", str(path), "artifact path is symlinked")
        content = _read_regular(path, "BPA111_ARTIFACT_MISSING")
        if _digest_bytes(content) != digest:
            _fail("BPA112_ARTIFACT_DIGEST", locator, "artifact digest drift")
        return content

    def read_json(self, locator: str) -> dict[str, Any]:
        content = self.read_bytes(locator)
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            _fail("BPA113_ARTIFACT_JSON", locator, str(exc))
        if (
            not isinstance(value, dict)
            or standards.canonical_json(value).encode("utf-8") != content
        ):
            _fail(
                "BPA114_ARTIFACT_CANONICAL",
                locator,
                "artifact is not canonical JSON",
            )
        return value

    def delete_digest(self, digest: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            _fail("BPA112_ARTIFACT_DIGEST", digest, "invalid artifact digest")
        path = self._path(digest)
        if path.is_symlink():
            _fail("BPA108_STORE_ROOT", str(path), "artifact path is symlinked")
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent, "BPA109_STORE_COLLISION")


class HistoryStore:
    """Content-addressed local history with compact/bulky retention classes."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        self.artifacts = ArtifactStore(self.root / "cas")
        if self.root.is_symlink():
            _fail("BPA115_HISTORY_ROOT", str(self.root), "history root is a symlink")
        if self.root.exists() and not self.root.is_dir():
            _fail(
                "BPA115_HISTORY_ROOT",
                str(self.root),
                "history root is not a directory",
            )

    @staticmethod
    def descriptor(
        artifact_id: str,
        kind: str,
        sha256: str,
        privacy: str,
        *,
        retention_class: str = "compact",
        retained: bool = True,
    ) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "kind": kind,
            "sha256": sha256,
            "privacy": privacy,
            "retention_class": retention_class,
            "retained": retained,
        }

    def record(
        self,
        run: dict[str, Any],
        descriptors: list[dict[str, Any]],
    ) -> tuple[str, str]:
        run_locator = self.artifacts.put_json(run)
        rows = [
            self.descriptor(
                "run-record",
                "manifest",
                run_locator.rsplit(":", 1)[1],
                "local-private",
            ),
            *copy.deepcopy(descriptors),
        ]
        rows.sort(key=lambda row: row["artifact_id"])
        if len({row["artifact_id"] for row in rows}) != len(rows):
            _fail(
                "BPA116_HISTORY_ARTIFACT",
                "/artifacts",
                "history artifact ids must be unique",
            )
        record_ids = sorted(
            {
                value[key]
                for value, key in (
                    (run.get("candidate"), "candidate_id"),
                    (run.get("simulation"), "simulation_id"),
                    (run.get("release"), "release_id"),
                    (run.get("application"), "application_id"),
                    (run.get("verification"), "verification_id"),
                )
                if isinstance(value, dict)
            }
            | {run["request_id"]}
            | ({run["plan_id"]} if run["plan_id"] is not None else set())
        )
        manifest = {
            "schema_version": 1,
            "format": "susy-blueprints-history-manifest-v1",
            "contract_id": ENGINE_CONTRACT_ID,
            "run_id": run["run_id"],
            "state": run["state"],
            "record_ids": record_ids,
            "artifacts": rows,
            "retention_policy_id": RETENTION_POLICY_ID,
        }
        _validate(manifest, HISTORY_SCHEMA, "history-manifest")
        locator = self.artifacts.put_json(manifest)
        return locator.rsplit(":", 1)[1], locator

    def prune_bulky(self, manifest_locator: str) -> tuple[str, str]:
        manifest = self.artifacts.read_json(manifest_locator)
        _validate(manifest, HISTORY_SCHEMA, manifest_locator)
        protected = {
            row["sha256"]
            for row in manifest["artifacts"]
            if row["retention_class"] == "compact" and row["retained"]
        }
        successor = copy.deepcopy(manifest)
        for row in successor["artifacts"]:
            if row["retention_class"] == "bulky" and row["retained"]:
                if row["sha256"] not in protected:
                    self.artifacts.delete_digest(row["sha256"])
                row["retained"] = False
        locator = self.artifacts.put_json(successor)
        return locator.rsplit(":", 1)[1], locator

    def acquire_transaction(self) -> tuple[int, Path]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.root / "active-transaction.lock"
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except OSError as exc:
            _fail("BPA117_TRANSACTION_LOCK", str(lock_path), str(exc))
        _fsync_directory(self.root, "BPA117_TRANSACTION_LOCK")
        return descriptor, lock_path

    def write_journal(self, journal: dict[str, Any]) -> Path:
        path = self.root / "active-transaction.json"
        content = standards.canonical_json(journal).encode("utf-8")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.root,
                prefix=".transaction.",
                delete=False,
            ) as handle:
                temporary = handle.name
                os.fchmod(handle.fileno(), 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _fsync_directory(self.root, "BPA117_TRANSACTION_LOCK")
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return path


def _validate_release_bundle(bundle: dict[str, Any]) -> None:
    _validate(bundle, RELEASE_BUNDLE_SCHEMA, "release-bundle")
    if [row["ordinal"] for row in bundle["operations"]] != list(
        range(len(bundle["operations"]))
    ):
        _fail("BPA118_BUNDLE_ORDER", "/operations", "ordinals are not contiguous")
    paths = [row["path"] for row in bundle["operations"]]
    if len(paths) != len(set(paths)) or paths != sorted(paths):
        _fail(
            "BPA118_BUNDLE_ORDER",
            "/operations",
            "operation paths are not unique and sorted",
        )
    operation_projection = [
        {
            "ordinal": row["ordinal"],
            "operation": row["operation"],
            "path": row["path"],
            "content_sha256": (
                None if row["after"] is None else row["after"]["sha256"]
            ),
        }
        for row in bundle["operations"]
    ]
    if _digest_json(operation_projection) != bundle["operations_sha256"]:
        _fail(
            "BPA118_BUNDLE_ORDER",
            "/operations_sha256",
            "operation projection digest drift",
        )
    for row in bundle["operations"]:
        try:
            planner._safe_path(
                row["path"], directory=False, location=row["path"]
            )
        except planner.PlannerDiagnostic:
            _fail(
                "BPA119_BUNDLE_SEMANTICS",
                row["path"],
                "operation path is unsafe or protected",
            )
        operation = row["operation"]
        if (operation == "create") != (row["before"] is None):
            _fail(
                "BPA119_BUNDLE_SEMANTICS",
                row["path"],
                "only create operations have no baseline content",
            )
        if (operation == "delete") != (row["after"] is None):
            _fail(
                "BPA119_BUNDLE_SEMANTICS",
                row["path"],
                "only delete operations have no released content",
            )
        for content in (row["before"], row["after"]):
            if content is not None:
                _decode_content(content)
                if (
                    content["kind"] == "symlink"
                    and content["mode"] != "120000"
                ) or (
                    content["kind"] == "file"
                    and content["mode"] not in {"100644", "100755"}
                ):
                    _fail(
                        "BPA119_BUNDLE_SEMANTICS",
                        row["path"],
                        "content kind and mode disagree",
                    )


def _instructions(bundle: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for row in bundle["operations"]:
        after = row["after"]
        text = None
        if after is not None:
            try:
                text = _decode_content(after).decode("utf-8")
            except UnicodeDecodeError:
                pass
        steps.append(
            {
                "ordinal": row["ordinal"],
                "action": row["operation"],
                "path": row["path"],
                "mode": None if after is None else after["mode"],
                "content_base64": (
                    None if after is None else after["content_base64"]
                ),
                "content_utf8": text,
            }
        )
    return {
        "schema_version": 1,
        "format": "susy-blueprints-placement-instructions-v1",
        "candidate_id": bundle["candidate_id"],
        "target_state_id": bundle["target_state_id"],
        "operations_sha256": bundle["operations_sha256"],
        "steps": steps,
    }


def _direct_diff(bundle: dict[str, Any]) -> dict[str, Any]:
    def text(content: dict[str, Any] | None) -> str | None:
        if content is None:
            return None
        try:
            return _decode_content(content).decode("utf-8")
        except UnicodeDecodeError:
            return None

    return {
        "schema_version": 1,
        "format": "susy-blueprints-direct-diff-v1",
        "candidate_id": bundle["candidate_id"],
        "target_state_id": bundle["target_state_id"],
        "operations_sha256": bundle["operations_sha256"],
        "changes": [
            {
                "ordinal": row["ordinal"],
                "operation": row["operation"],
                "path": row["path"],
                "before_sha256": (
                    None if row["before"] is None else row["before"]["sha256"]
                ),
                "after_sha256": (
                    None if row["after"] is None else row["after"]["sha256"]
                ),
                "before_mode": (
                    None if row["before"] is None else row["before"]["mode"]
                ),
                "after_mode": (
                    None if row["after"] is None else row["after"]["mode"]
                ),
                "before_content_base64": (
                    None
                    if row["before"] is None
                    else row["before"]["content_base64"]
                ),
                "after_content_base64": (
                    None
                    if row["after"] is None
                    else row["after"]["content_base64"]
                ),
                "before_content_utf8": text(row["before"]),
                "after_content_utf8": text(row["after"]),
            }
            for row in bundle["operations"]
        ],
    }


def _error(
    code: str,
    phase: str,
    summary: str,
    record_ids: list[str],
    detail: Any,
) -> dict[str, Any]:
    return {
        "code": code,
        "phase": phase,
        "summary": summary,
        "record_ids": sorted(set(record_ids)),
        "evidence_sha256": _digest_json(detail),
    }


def _append_event(
    run: dict[str, Any],
    phase: str,
    to_state: str,
    result: str,
    record_ids: list[str],
) -> None:
    previous = run["state"]
    run["events"].append(
        {
            "sequence": len(run["events"]),
            "phase": phase,
            "from_state": previous,
            "to_state": to_state,
            "result": result,
            "record_ids": list(dict.fromkeys(record_ids)),
        }
    )
    run["state"] = to_state
    run["errors"].sort(
        key=lambda row: (row["phase"], row["code"], row["record_ids"])
    )
    run["run_id"] = _identity("blueprints-run:sha256:", run, "run_id")


def _base_run(
    request: dict[str, Any],
    plan: dict[str, Any],
    candidate: dict[str, Any],
    sim: dict[str, Any],
) -> dict[str, Any]:
    passed = sim["status"] == "passed" and all(
        row["status"] == "passed" for row in sim["gates"]
    )
    state = "simulated" if passed else "simulation-failed"
    run = {
        "schema_version": 1,
        "format": "susy-blueprints-run-v1",
        "contract_id": ENGINE_CONTRACT_ID,
        "request_id": request["request_id"],
        "plan_id": plan["plan_id"],
        "target_state_id": plan["target_state_id"],
        "output_mode": request["output_mode"],
        "state": state,
        "candidate": copy.deepcopy(candidate),
        "simulation": copy.deepcopy(sim),
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
            },
            {
                "sequence": 1,
                "phase": "plan",
                "from_state": "initialized",
                "to_state": "planned",
                "result": "succeeded",
                "record_ids": [plan["plan_id"], candidate["candidate_id"]],
            },
            {
                "sequence": 2,
                "phase": "simulate",
                "from_state": "planned",
                "to_state": state,
                "result": "succeeded" if passed else "failed",
                "record_ids": [sim["simulation_id"]],
            },
        ],
        "errors": [],
    }
    run["run_id"] = _identity("blueprints-run:sha256:", run, "run_id")
    _validate_run(run)
    return run


def _validate_run(run: dict[str, Any]) -> None:
    planner._validate_schema(run, planner.RUN_SCHEMA, "run")
    expected = _identity("blueprints-run:sha256:", run, "run_id")
    if run["run_id"] != expected:
        _fail("BPA120_RUN_ID", "/run_id", f"expected {expected}")
    if [row["sequence"] for row in run["events"]] != list(
        range(len(run["events"]))
    ):
        _fail("BPA121_EVENT_CHAIN", "/events", "event sequence is not contiguous")
    for index, event in enumerate(run["events"]):
        previous = None if index == 0 else run["events"][index - 1]["to_state"]
        if event["from_state"] != previous:
            _fail("BPA121_EVENT_CHAIN", f"/events/{index}", "event chain drift")
    if run["events"][-1]["to_state"] != run["state"]:
        _fail("BPA122_STATE", "/state", "run state differs from the event chain")
    for field, prefix in (
        ("candidate", "blueprints-candidate:sha256:"),
        ("simulation", "blueprints-simulation:sha256:"),
        ("release", "blueprints-release:sha256:"),
        ("application", "blueprints-application:sha256:"),
        ("verification", "blueprints-verification:sha256:"),
    ):
        value = run[field]
        if value is not None:
            identity_field = field + "_id"
            if value[identity_field] != _identity(
                prefix, value, identity_field
            ):
                _fail(
                    "BPA123_RECORD_ID",
                    f"/{field}/{identity_field}",
                    "record identity is not recomputable",
                )
    candidate = run["candidate"]
    simulation_record = run["simulation"]
    release = run["release"]
    application = run["application"]
    verification = run["verification"]
    if candidate is not None and (
        candidate["plan_id"] != run["plan_id"]
        or candidate["target_state_id"] != run["target_state_id"]
    ):
        _fail(
            "BPA123_RECORD_ID",
            "/candidate",
            "candidate is not bound to this run plan and target",
        )
    if simulation_record is not None and candidate is not None and (
        simulation_record["candidate_id"] != candidate["candidate_id"]
    ):
        _fail(
            "BPA123_RECORD_ID",
            "/simulation",
            "simulation is not bound to this run candidate",
        )
    if release is not None and (
        candidate is None
        or simulation_record is None
        or release["candidate_id"] != candidate["candidate_id"]
        or release["simulation_id"] != simulation_record["simulation_id"]
    ):
        _fail(
            "BPA123_RECORD_ID",
            "/release",
            "release is not bound to this run candidate and simulation",
        )
    if application is not None and (
        release is None
        or application["release_id"] != release["release_id"]
        or application["expected_target_state_id"] != run["target_state_id"]
    ):
        _fail(
            "BPA123_RECORD_ID",
            "/application",
            "application is not bound to this run release and target",
        )
    if verification is not None and (
        application is None
        or release is None
        or verification["application_id"] != application["application_id"]
        or verification["release_id"] != release["release_id"]
    ):
        _fail(
            "BPA123_RECORD_ID",
            "/verification",
            "verification is not bound to this run application and release",
        )


def _validate_request_plan(
    request: dict[str, Any], plan: dict[str, Any]
) -> None:
    planner._validate_schema(request, planner.REQUEST_SCHEMA, "request")
    planner._validate_schema(plan, planner.PLAN_SCHEMA, "plan")
    expected_request = _identity(
        "blueprints-request:sha256:", request, "request_id"
    )
    if request["request_id"] != expected_request:
        _fail("BPA123_RECORD_ID", "/request/request_id", "request identity drift")
    expected_plan = _identity("blueprints-plan:sha256:", plan, "plan_id")
    if plan["plan_id"] != expected_plan:
        _fail("BPA123_RECORD_ID", "/plan/plan_id", "plan identity drift")
    if (
        plan["request_id"] != request["request_id"]
        or plan["target_state_id"] != request["target"]["target_state_id"]
    ):
        _fail(
            "BPA123_RECORD_ID",
            "/plan",
            "plan is not bound to this request and target",
        )


def _validate_proof(proof: dict[str, Any]) -> None:
    planner._validate_schema(
        proof, SCHEMA_ROOT / "blueprints-proof-v1.schema.json", "proof"
    )
    expected = _identity("blueprints-proof:sha256:", proof, "proof_id")
    if proof["proof_id"] != expected:
        _fail("BPA124_PROOF_ID", "/proof_id", f"expected {expected}")
    exported = (
        set() if proof["export"] is None else set(proof["export"]["artifact_ids"])
    )
    included = {
        row["artifact_id"]
        for row in proof["artifacts"]
        if row["included_in_export"]
    }
    if exported != included:
        _fail(
            "BPA125_PROOF_EXPORT",
            "/artifacts",
            "included artifacts differ from the export manifest",
        )
    if any(
        row["privacy"] == "local-private"
        and (row["included_in_export"] or row["artifact_id"] in exported)
        for row in proof["artifacts"]
    ):
        _fail("BPA126_PRIVATE_EXPORT", "/artifacts", "private artifact exported")


class LifecycleEngine:
    """A01 deterministic release and target-transaction engine."""

    def __init__(
        self,
        *,
        registry_root: Path,
        asset_root: Path,
        ledger_path: Path,
        target_repository: Path,
        sealed_store: planner.SealedStore,
        simulation_evidence_store: simulation.SimulationEvidenceStore,
        artifact_store: ArtifactStore,
        history_store: HistoryStore,
        mutation_hook: MutationHook | None = None,
    ) -> None:
        self.registry_root = registry_root
        self.asset_root = asset_root
        self.ledger_path = ledger_path
        self.target_repository = target_repository.resolve()
        self.sealed_store = sealed_store
        self.simulation_evidence_store = simulation_evidence_store
        self.artifact_store = artifact_store
        self.history_store = history_store
        self.mutation_hook = mutation_hook

    def _release_admission(
        self,
        planning_result: dict[str, Any],
        simulation_result: dict[str, Any],
        target_manifest: dict[str, Any],
        environment_lock: dict[str, Any],
        prior_run: dict[str, Any] | None = None,
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        request = planning_result.get("request")
        plan = planning_result.get("plan")
        candidate = planning_result.get("candidate")
        sim = simulation_result.get("simulation")
        if not all(
            isinstance(value, dict)
            for value in (request, plan, candidate, sim)
        ):
            _fail(
                "BPA127_RELEASE_INPUT",
                "/",
                "release requires request, plan, candidate, and simulation",
            )
        _validate_request_plan(request, plan)
        planner._validate_candidate(candidate)
        planner._validate_target_manifest(target_manifest)
        simulation_schema = json.loads(
            planner.RUN_SCHEMA.read_text(encoding="utf-8")
        )["properties"]["simulation"]["oneOf"][1]
        errors = list(Draft202012Validator(simulation_schema).iter_errors(sim))
        if errors:
            _fail("BPA101_SCHEMA", "simulation", errors[0].message)
        if prior_run is None:
            run = _base_run(request, plan, candidate, sim)
        else:
            run = copy.deepcopy(prior_run)
            _validate_run(run)
            if (
                run["state"] not in {"simulated", "simulation-failed"}
                or run["request_id"] != request["request_id"]
                or run["plan_id"] != plan["plan_id"]
                or run["target_state_id"] != target_manifest["target_state_id"]
                or run["output_mode"] != request["output_mode"]
                or run["candidate"] != candidate
                or run["simulation"] != sim
                or run["release"] is not None
                or run["application"] is not None
                or run["verification"] is not None
            ):
                _fail(
                    "BPA127_RELEASE_INPUT",
                    "/prior-run",
                    "persisted run does not bind the admitted simulation",
                )
        if run["state"] != "simulated":
            return request, plan, candidate, sim, run
        expected_stages = [
            row["stage_id"]
            for row in plan["validation_stages"]
            if row["required"]
        ]
        if (
            sim["candidate_id"] != candidate["candidate_id"]
            or [row["stage_id"] for row in sim["gates"]] != expected_stages
            or [row["ordinal"] for row in sim["gates"]]
            != list(range(len(expected_stages)))
            or any(row["status"] != "passed" for row in sim["gates"])
        ):
            _fail(
                "BPA128_SIMULATION_BINDING",
                "/simulation",
                "simulation is not a complete pass for the exact candidate",
            )
        evidence = self.simulation_evidence_store.read(
            simulation_result.get("evidence_locator", "")
        )
        if (
            evidence["candidate_id"] != candidate["candidate_id"]
            or evidence["plan_id"] != plan["plan_id"]
            or evidence["target_state_id"] != target_manifest["target_state_id"]
            or evidence["environment_lock_sha256"]
            != simulation.environment_lock_sha256(environment_lock)
            or evidence["environment_lock_sha256"]
            != sim["environment_lock_sha256"]
            or [
                row["evidence_sha256"] for row in evidence["gates"]
            ]
            != [row["evidence_sha256"] for row in sim["gates"]]
        ):
            _fail(
                "BPA129_SIMULATION_EVIDENCE",
                "/simulation",
                "private simulation evidence does not close the public pass",
            )
        authority = simulation.authority_state_sha256(
            self.registry_root, self.asset_root, self.ledger_path
        )
        if evidence["authority_state_sha256"] != authority:
            _fail(
                "BPA130_AUTHORITY_DRIFT",
                str(self.registry_root),
                "standard registry or allocation ledger changed after simulation",
            )
        return request, plan, candidate, sim, run

    def _bundle(
        self,
        candidate: dict[str, Any],
        plan: dict[str, Any],
        target_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.sealed_store.read(candidate["sealed_locator"])
        public_operations = [
            {
                "ordinal": row["ordinal"],
                "operation": row["operation"],
                "path": row["path"],
                "content_sha256": row["content_sha256"],
            }
            for row in payload["operations"]
        ]
        operations_sha256 = _digest_json(public_operations)
        if (
            public_operations != plan["operations"]
            or operations_sha256 != candidate["content_manifest_sha256"]
            or payload["plan_id"] != plan["plan_id"]
            or payload["target_state_id"] != target_manifest["target_state_id"]
        ):
            _fail(
                "BPA131_SEALED_BINDING",
                candidate["sealed_locator"],
                "sealed operations differ from the plan/candidate",
            )
        entries = {row["path"]: row for row in target_manifest["entries"]}
        released = []
        for public, sealed in zip(public_operations, payload["operations"]):
            baseline = entries.get(public["path"])
            before = None
            if public["operation"] != "create":
                if baseline is None or baseline["kind"] == "deleted":
                    _fail(
                        "BPA132_BASELINE_MISSING",
                        public["path"],
                        "released update/delete has no baseline path",
                    )
                path, _ = _safe_target(
                    self.target_repository,
                    public["path"],
                    create_parents=False,
                )
                before = _content_record(
                    baseline["kind"],
                    baseline["mode"],
                    _path_content(path, baseline),
                )
            after = None
            if public["operation"] != "delete":
                content = base64.b64decode(
                    sealed["content_base64"], validate=True
                )
                mode = (
                    "100644"
                    if before is None or before["kind"] == "symlink"
                    else before["mode"]
                )
                after = _content_record("file", mode, content)
            released.append(
                {
                    "ordinal": public["ordinal"],
                    "operation": public["operation"],
                    "path": public["path"],
                    "before": before,
                    "after": after,
                }
            )
        bundle = {
            "schema_version": 1,
            "format": "susy-blueprints-release-bundle-v1",
            "contract_id": ENGINE_CONTRACT_ID,
            "candidate_id": candidate["candidate_id"],
            "target_state_id": target_manifest["target_state_id"],
            "operations_sha256": operations_sha256,
            "operations": released,
        }
        _validate_release_bundle(bundle)
        return bundle

    def _proof_artifacts(
        self,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        output_mode = context["run"]["output_mode"]
        selected = {
            "instructions": (
                "placement-instructions",
                "source",
                context["projection_locators"]["instructions"],
            ),
            "patch-bundle": (
                "patch-bundle",
                "patch",
                context["projection_locators"]["patch"],
            ),
            "direct-apply": (
                "direct-diff",
                "patch",
                context["projection_locators"]["direct_diff"],
            ),
        }[output_mode]
        release_locator = context["release_locator"]
        simulation_digest = context["simulation_evidence_locator"].rsplit(
            ":", 1
        )[1]
        sealed_digest = context["candidate"]["sealed_locator"].rsplit(":", 1)[1]
        return [
            {
                "artifact_id": "release-manifest",
                "kind": "manifest",
                "sha256": release_locator.rsplit(":", 1)[1],
                "privacy": "exportable",
                "included_in_export": False,
            },
            {
                "artifact_id": selected[0],
                "kind": selected[1],
                "sha256": selected[2].rsplit(":", 1)[1],
                "privacy": "exportable",
                "included_in_export": False,
            },
            {
                "artifact_id": "simulation-evidence",
                "kind": "evidence",
                "sha256": simulation_digest,
                "privacy": "local-private",
                "included_in_export": False,
            },
            {
                "artifact_id": "sealed-candidate",
                "kind": "source",
                "sha256": sealed_digest,
                "privacy": "local-private",
                "included_in_export": False,
            },
        ]

    def _history_descriptors(
        self, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return [
            HistoryStore.descriptor(
                row["artifact_id"],
                row["kind"],
                row["sha256"],
                row["privacy"],
            )
            for row in context["proof_artifacts"]
        ] + copy.deepcopy(context.get("additional_history_artifacts", []))

    def _build_proof(
        self,
        context: dict[str, Any],
        history_sha256: str,
        closure: str,
    ) -> dict[str, Any]:
        run = context["run"]
        application = run["application"]
        verification = run["verification"]
        target_verified = closure == "target-verified"
        applied_target = (
            application["post_target"]["target_state_id"]
            if target_verified
            and isinstance(application, dict)
            and isinstance(application["post_target"], dict)
            else None
        )
        proof = {
            "schema_version": 1,
            "format": "susy-blueprints-proof-v1",
            "contract_id": ENGINE_CONTRACT_ID,
            "closure": closure,
            "run_id": run["run_id"],
            "request_id": run["request_id"],
            "plan_id": run["plan_id"],
            "candidate_id": run["candidate"]["candidate_id"],
            "simulation_id": run["simulation"]["simulation_id"],
            "release_id": run["release"]["release_id"],
            "patch_id": run["release"]["projections"]["patch"]["patch_id"],
            "application_id": (
                application["application_id"] if target_verified else None
            ),
            "verification_id": (
                verification["verification_id"] if target_verified else None
            ),
            "standard_ids": run["candidate"]["standard_ids"],
            "target": {
                "baseline_target_state_id": run["target_state_id"],
                "applied_target_state_id": applied_target,
                "verified_target_state_id": applied_target,
            },
            "gate_evidence": [
                {
                    "stage_id": row["stage_id"],
                    "status": "passed",
                    "evidence_sha256": row["evidence_sha256"],
                }
                for row in run["simulation"]["gates"]
            ],
            "artifacts": copy.deepcopy(context["proof_artifacts"]),
            "history": {
                "local_manifest_sha256": history_sha256,
                "retention_policy_id": RETENTION_POLICY_ID,
            },
            "export": None,
        }
        proof["proof_id"] = _identity(
            "blueprints-proof:sha256:", proof, "proof_id"
        )
        _validate_proof(proof)
        return proof

    def release(
        self,
        planning_result: dict[str, Any],
        simulation_result: dict[str, Any],
        *,
        target_manifest: dict[str, Any],
        environment_lock: dict[str, Any],
        invalidated_ids: list[str] | None = None,
        prior_run: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request, plan, candidate, sim, run = self._release_admission(
            planning_result,
            simulation_result,
            target_manifest,
            environment_lock,
            prior_run,
        )
        context: dict[str, Any] = {
            "run": run,
            "request": request,
            "plan": plan,
            "candidate": candidate,
            "simulation_evidence_locator": simulation_result.get(
                "evidence_locator", ""
            ),
        }
        if run["state"] != "simulated":
            context.update(
                {
                    "release": None,
                    "delivery": None,
                    "proof": None,
                    "diagnostics": ["BPA133_SIMULATION_NOT_PASSED"],
                }
            )
            return context
        if invalidated_ids:
            run["errors"].append(
                _error(
                    "BPA134_INVALIDATED_INPUT",
                    "generate",
                    "Candidate or simulation was invalidated.",
                    invalidated_ids,
                    sorted(invalidated_ids),
                )
            )
            _append_event(run, "generate", "simulated", "failed", [])
            context.update(
                {
                    "release": None,
                    "delivery": None,
                    "proof": None,
                    "diagnostics": ["BPA134_INVALIDATED_INPUT"],
                }
            )
            return context
        observed = planner.capture_target_state(
            self.target_repository, target_manifest["repository_id"]
        )
        if observed != target_manifest:
            run["errors"].append(
                _error(
                    "BPA135_STALE_TARGET",
                    "generate",
                    "Target changed after simulation.",
                    [candidate["candidate_id"], sim["simulation_id"]],
                    observed["target_state_id"],
                )
            )
            _append_event(run, "generate", "simulated", "failed", [])
            context.update(
                {
                    "release": None,
                    "delivery": None,
                    "proof": None,
                    "diagnostics": ["BPA135_STALE_TARGET"],
                }
            )
            return context

        bundle = self._bundle(candidate, plan, target_manifest)
        instructions = _instructions(bundle)
        direct_diff = _direct_diff(bundle)
        bundle_locator = self.artifact_store.put_json(bundle)
        instructions_locator = self.artifact_store.put_json(instructions)
        direct_diff_locator = self.artifact_store.put_json(direct_diff)
        patch_projection = {
            "bundle_sha256": bundle_locator.rsplit(":", 1)[1],
            "operations_sha256": bundle["operations_sha256"],
        }
        patch_projection["patch_id"] = _identity(
            "blueprints-patch:sha256:", patch_projection, "patch_id"
        )
        release = {
            "candidate_id": candidate["candidate_id"],
            "simulation_id": sim["simulation_id"],
            "operations_sha256": bundle["operations_sha256"],
            "projections": {
                "instructions": {
                    "manifest_sha256": instructions_locator.rsplit(":", 1)[1],
                    "operations_sha256": bundle["operations_sha256"],
                },
                "patch": patch_projection,
                "direct_diff": {
                    "diff_sha256": direct_diff_locator.rsplit(":", 1)[1],
                    "operations_sha256": bundle["operations_sha256"],
                },
            },
            "released_sequence": len(run["events"]),
        }
        release["release_id"] = _identity(
            "blueprints-release:sha256:", release, "release_id"
        )
        run["release"] = copy.deepcopy(release)
        _append_event(
            run,
            "generate",
            "released",
            "succeeded",
            [release["release_id"], patch_projection["patch_id"]],
        )
        _validate_run(run)
        release_locator = self.artifact_store.put_json(release)
        context.update(
            {
                "release": release,
                "bundle": bundle,
                "bundle_locator": bundle_locator,
                "release_locator": release_locator,
                "projection_locators": {
                    "instructions": instructions_locator,
                    "patch": bundle_locator,
                    "direct_diff": direct_diff_locator,
                },
                "delivery": {
                    "output_mode": request["output_mode"],
                    "artifact": {
                        "instructions": instructions,
                        "patch-bundle": bundle,
                        "direct-apply": direct_diff,
                    }[request["output_mode"]],
                },
                "diagnostics": [],
                "additional_history_artifacts": [],
            }
        )
        context["proof_artifacts"] = self._proof_artifacts(context)
        history_sha, history_locator = self.history_store.record(
            run, self._history_descriptors(context)
        )
        proof = self._build_proof(
            context, history_sha, "release-validated"
        )
        proof_locator = self.history_store.artifacts.put_json(proof)
        context.update(
            {
                "history_manifest_sha256": history_sha,
                "history_locator": history_locator,
                "proof": proof,
                "proof_locator": proof_locator,
            }
        )
        return context

    @staticmethod
    def _restore_path(path: Path, content: dict[str, Any] | None) -> None:
        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                _fail("BPA103_PATH_KIND", str(path), "target path is a directory")
            path.unlink()
        if content is None:
            return
        decoded = _decode_content(content)
        if content["kind"] == "symlink":
            os.symlink(os.fsdecode(decoded), path)
            return
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            remaining = memoryview(decoded)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    _fail(
                        "BPA141_TRANSACTION_TEMP",
                        str(path),
                        "short write while staging released content",
                    )
                remaining = remaining[written:]
            os.fchmod(
                descriptor,
                0o755 if content["mode"] == "100755" else 0o644,
            )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _application_record(
        self,
        release_id: str,
        expected_target_state_id: str,
        observed_manifest: dict[str, Any],
        *,
        status: str,
        post_manifest: dict[str, Any] | None,
        atomic: bool,
        rollback: str,
    ) -> dict[str, Any]:
        record = {
            "release_id": release_id,
            "status": status,
            "expected_target_state_id": expected_target_state_id,
            "observed_target": planner.target_from_manifest(observed_manifest),
            "post_target": (
                None
                if post_manifest is None
                else planner.target_from_manifest(post_manifest)
            ),
            "atomic": atomic,
            "rollback": rollback,
        }
        record["application_id"] = _identity(
            "blueprints-application:sha256:", record, "application_id"
        )
        return record

    @staticmethod
    def _validate_applied_manifest(
        before: dict[str, Any],
        after: dict[str, Any],
        bundle: dict[str, Any],
    ) -> None:
        if simulation._changed_paths(before, after) != {
            row["path"] for row in bundle["operations"]
        }:
            _fail(
                "BPA151_APPLICATION_PLACEMENT",
                "/operations",
                "transaction changed a path outside the released operation set",
            )
        rows = {row["path"]: row for row in after["entries"]}
        for operation in bundle["operations"]:
            observed = rows.get(operation["path"])
            expected = operation["after"]
            if expected is None:
                if observed is not None and observed["kind"] != "deleted":
                    _fail(
                        "BPA151_APPLICATION_PLACEMENT",
                        operation["path"],
                        "released delete remains present",
                    )
            elif (
                observed is None
                or observed["kind"] == "deleted"
                or observed["worktree_sha256"] != expected["sha256"]
                or observed["mode"] != expected["mode"]
            ):
                _fail(
                    "BPA151_APPLICATION_PLACEMENT",
                    operation["path"],
                    "applied bytes or mode differ from the release",
                )

    def apply(self, context: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(context)
        run = result["run"]
        _validate_run(run)
        _validate_request_plan(result["request"], result["plan"])
        planner._validate_candidate(result["candidate"])
        if (
            run["state"] != "released"
            or run["output_mode"] != "direct-apply"
            or not result["request"]["consent"]["allow_direct_apply"]
            or result.get("release") is None
        ):
            _fail(
                "BPA136_APPLY_NOT_AUTHORIZED",
                "/run",
                "apply requires a consented released direct-apply run",
            )
        bundle = self.artifact_store.read_json(result["bundle_locator"])
        _validate_release_bundle(bundle)
        planned_operations_sha256 = _digest_json(result["plan"]["operations"])
        if (
            bundle["operations_sha256"] != run["release"]["operations_sha256"]
            or bundle["operations_sha256"]
            != run["candidate"]["content_manifest_sha256"]
            or bundle["operations_sha256"] != planned_operations_sha256
            or bundle["candidate_id"] != run["candidate"]["candidate_id"]
            or bundle["target_state_id"] != run["target_state_id"]
            or result["candidate"] != run["candidate"]
            or result["release"] != run["release"]
            or result["bundle_locator"].rsplit(":", 1)[1]
            != run["release"]["projections"]["patch"]["bundle_sha256"]
        ):
            _fail("BPA137_RELEASE_DRIFT", "/release", "release bundle drift")
        expected = run["target_state_id"]
        observed = planner.capture_target_state(
            self.target_repository, result["target_repository_id"]
            if "target_repository_id" in result
            else result["request"]["target"]["repository_id"]
        )
        if observed["target_state_id"] != expected:
            application = self._application_record(
                run["release"]["release_id"],
                expected,
                observed,
                status="rejected",
                post_manifest=None,
                atomic=False,
                rollback="not-needed",
            )
            run["application"] = application
            run["errors"].append(
                _error(
                    "BPA138_STALE_APPLICATION_TARGET",
                    "apply",
                    "Target changed before application.",
                    [run["release"]["release_id"]],
                    observed["target_state_id"],
                )
            )
            _append_event(
                run,
                "apply",
                "application-rejected",
                "failed",
                [application["application_id"]],
            )
            _validate_run(run)
            result["application"] = application
            result["diagnostics"] = ["BPA138_STALE_APPLICATION_TARGET"]
            self._record_context_history(result)
            return result

        authorized = result["plan"]["authorized_paths"]
        if any(
            not any(row["path"].startswith(prefix) for prefix in authorized)
            for row in bundle["operations"]
        ):
            _fail(
                "BPA139_PATH_AUTHORITY",
                "/bundle/operations",
                "released operation lies outside plan authority",
            )
        lock_descriptor, lock_path = self.history_store.acquire_transaction()
        journal_path: Path | None = None
        created_dirs: list[Path] = []
        staged: dict[int, Path] = {}
        touched: list[dict[str, Any]] = []
        mutation_error: Exception | None = None
        rollback = "not-needed"
        atomic = True
        post: dict[str, Any] | None = None
        try:
            journal = {
                "schema_version": 1,
                "format": "susy-blueprints-active-transaction-v1",
                "release_id": run["release"]["release_id"],
                "expected_target_state_id": expected,
                "bundle_sha256": result["bundle_locator"].rsplit(":", 1)[1],
                "operations_sha256": bundle["operations_sha256"],
            }
            os.write(
                lock_descriptor,
                standards.canonical_json(journal).encode("utf-8"),
            )
            os.fsync(lock_descriptor)
            journal_path = self.history_store.write_journal(journal)
            for row in bundle["operations"]:
                path, made = _safe_target(
                    self.target_repository,
                    row["path"],
                    create_parents=row["operation"] == "create",
                )
                created_dirs.extend(made)
                exists = path.exists() or path.is_symlink()
                if (row["operation"] == "create") == exists:
                    _fail(
                        "BPA140_APPLICATION_COLLISION",
                        row["path"],
                        "released operation baseline no longer matches",
                    )
                if row["before"] is not None:
                    kind = "symlink" if path.is_symlink() else "file"
                    status = path.lstat()
                    mode = (
                        "120000"
                        if kind == "symlink"
                        else (
                            "100755"
                            if status.st_mode & stat.S_IXUSR
                            else "100644"
                        )
                    )
                    current = (
                        os.readlink(os.fsencode(path))
                        if kind == "symlink"
                        else _read_regular(path, "BPA105_TARGET_RACE")
                    )
                    if (
                        kind != row["before"]["kind"]
                        or mode != row["before"]["mode"]
                        or _digest_bytes(current) != row["before"]["sha256"]
                    ):
                        _fail(
                            "BPA140_APPLICATION_COLLISION",
                            row["path"],
                            "released baseline bytes no longer match",
                        )
                if row["after"] is not None:
                    temporary = path.parent / (
                        f".blueprints-{run['release']['release_id'][-12:]}-"
                        f"{row['ordinal']}"
                    )
                    if temporary.exists() or temporary.is_symlink():
                        _fail(
                            "BPA141_TRANSACTION_TEMP",
                            str(temporary),
                            "transaction temporary path already exists",
                        )
                    self._restore_path(temporary, row["after"])
                    staged[row["ordinal"]] = temporary
            for row in bundle["operations"]:
                path, _ = _safe_target(
                    self.target_repository,
                    row["path"],
                    create_parents=False,
                )
                if row["after"] is None:
                    path.unlink()
                else:
                    os.replace(staged[row["ordinal"]], path)
                touched.append(row)
                _fsync_directory(path.parent, "BPA141_TRANSACTION_TEMP")
                if self.mutation_hook is not None:
                    self.mutation_hook(row["ordinal"], row["path"])
            post = planner.capture_target_state(
                self.target_repository, result["request"]["target"]["repository_id"]
            )
            self._validate_applied_manifest(observed, post, bundle)
        except Exception as exc:
            mutation_error = exc
            rollback = "succeeded"
            try:
                for row in reversed(touched):
                    path, _ = _safe_target(
                        self.target_repository,
                        row["path"],
                        create_parents=True,
                    )
                    self._restore_path(path, row["before"])
                    _fsync_directory(path.parent, "BPA142_ROLLBACK_MISMATCH")
                for temporary in staged.values():
                    if temporary.exists() or temporary.is_symlink():
                        temporary.unlink()
                for directory in reversed(created_dirs):
                    try:
                        directory.rmdir()
                        _fsync_directory(
                            directory.parent, "BPA142_ROLLBACK_MISMATCH"
                        )
                    except OSError:
                        pass
                restored = planner.capture_target_state(
                    self.target_repository,
                    result["request"]["target"]["repository_id"],
                )
                if restored != observed:
                    _fail(
                        "BPA142_ROLLBACK_MISMATCH",
                        str(self.target_repository),
                        "rollback did not restore the exact target",
                    )
            except Exception:
                rollback = "failed"
                atomic = False
        finally:
            os.close(lock_descriptor)
            if rollback != "failed":
                if journal_path is not None:
                    journal_path.unlink(missing_ok=True)
                lock_path.unlink(missing_ok=True)
                _fsync_directory(
                    self.history_store.root, "BPA117_TRANSACTION_LOCK"
                )

        if mutation_error is None:
            application = self._application_record(
                run["release"]["release_id"],
                expected,
                observed,
                status="applied",
                post_manifest=post,
                atomic=True,
                rollback="not-needed",
            )
            to_state = "applied"
            event_result = "succeeded"
            diagnostics: list[str] = []
        else:
            application = self._application_record(
                run["release"]["release_id"],
                expected,
                observed,
                status="rejected",
                post_manifest=None,
                atomic=atomic,
                rollback=rollback,
            )
            code = (
                "BPA143_ROLLBACK_FAILED"
                if rollback == "failed"
                else "BPA144_APPLICATION_ROLLED_BACK"
            )
            run["errors"].append(
                _error(
                    code,
                    "apply",
                    (
                        "Application failed and rollback failed."
                        if rollback == "failed"
                        else "Application failed; exact rollback succeeded."
                    ),
                    [run["release"]["release_id"]],
                    type(mutation_error).__name__,
                )
            )
            to_state = "application-rejected"
            event_result = "failed"
            diagnostics = [code]
        run["application"] = application
        _append_event(
            run,
            "apply",
            to_state,
            event_result,
            [application["application_id"]],
        )
        _validate_run(run)
        result["application"] = application
        result["diagnostics"] = diagnostics
        self._record_context_history(result)
        return result

    def _record_context_history(self, context: dict[str, Any]) -> None:
        history_sha, history_locator = self.history_store.record(
            context["run"], self._history_descriptors(context)
        )
        context["history_manifest_sha256"] = history_sha
        context["history_locator"] = history_locator

    def verify(
        self,
        context: dict[str, Any],
        *,
        post_checks: dict[str, PostCheck] | None = None,
    ) -> dict[str, Any]:
        result = copy.deepcopy(context)
        run = result["run"]
        _validate_run(run)
        if (
            run["state"] not in {"applied", "verification-failed"}
            or not isinstance(run["application"], dict)
            or run["application"]["status"] != "applied"
        ):
            _fail(
                "BPA145_VERIFY_NOT_ADMITTED",
                "/run",
                "verification requires an applied direct transaction",
            )
        checks: list[dict[str, Any]] = []
        current = planner.capture_target_state(
            self.target_repository, result["request"]["target"]["repository_id"]
        )
        expected = run["application"]["post_target"]["target_state_id"]
        consistency = current["target_state_id"] == expected
        checks.append(
            {
                "check_id": "applied-target-consistency",
                "status": "passed" if consistency else "failed",
                "evidence_sha256": _digest_json(
                    {
                        "expected": expected,
                        "observed": current["target_state_id"],
                    }
                ),
            }
        )
        bundle = self.artifact_store.read_json(result["bundle_locator"])
        _validate_release_bundle(bundle)
        if (
            bundle["candidate_id"] != run["candidate"]["candidate_id"]
            or bundle["target_state_id"] != run["target_state_id"]
            or bundle["operations_sha256"]
            != run["release"]["operations_sha256"]
        ):
            _fail("BPA137_RELEASE_DRIFT", "/release", "release bundle drift")
        placement_passed = True
        rows = {row["path"]: row for row in current["entries"]}
        for operation in bundle["operations"]:
            observed = rows.get(operation["path"])
            after = operation["after"]
            if after is None:
                if observed is not None and observed["kind"] != "deleted":
                    placement_passed = False
            elif (
                observed is None
                or observed["kind"] == "deleted"
                or observed["worktree_sha256"] != after["sha256"]
                or observed["mode"] != after["mode"]
            ):
                placement_passed = False
        checks.append(
            {
                "check_id": "released-content-placement",
                "status": "passed" if placement_passed else "failed",
                "evidence_sha256": _digest_json(
                    {
                        "operations_sha256": bundle["operations_sha256"],
                        "observed_manifest_sha256": current["manifest_sha256"],
                    }
                ),
            }
        )
        before_callbacks = current
        for check_id, callback in sorted((post_checks or {}).items()):
            if not isinstance(check_id, str) or not check_id:
                _fail("BPA146_POST_CHECK_ID", "/", "post-check id is invalid")
            try:
                passed, evidence = callback(self.target_repository)
            except Exception as exc:
                passed = False
                evidence = {"exception": type(exc).__name__}
            checks.append(
                {
                    "check_id": check_id,
                    "status": "passed" if passed is True else "failed",
                    "evidence_sha256": _digest_json(evidence),
                }
            )
        after_callbacks = planner.capture_target_state(
            self.target_repository, result["request"]["target"]["repository_id"]
        )
        if after_callbacks != before_callbacks:
            checks.append(
                {
                    "check_id": "verification-side-effect",
                    "status": "failed",
                    "evidence_sha256": _digest_json(
                        {
                            "before": before_callbacks["target_state_id"],
                            "after": after_callbacks["target_state_id"],
                        }
                    ),
                }
            )
        passed = all(row["status"] == "passed" for row in checks)
        verification = {
            "application_id": run["application"]["application_id"],
            "release_id": run["release"]["release_id"],
            "status": "passed" if passed else "failed",
            "target_state_id": expected,
            "checks": checks,
        }
        verification["verification_id"] = _identity(
            "blueprints-verification:sha256:",
            verification,
            "verification_id",
        )
        run["verification"] = verification
        if not passed:
            run["errors"].append(
                _error(
                    "BPA147_VERIFICATION_FAILED",
                    "verify",
                    "One or more post-application checks failed.",
                    [verification["verification_id"]],
                    [
                        row["check_id"]
                        for row in checks
                        if row["status"] == "failed"
                    ],
                )
            )
        _append_event(
            run,
            "verify",
            "verified" if passed else "verification-failed",
            "succeeded" if passed else "failed",
            [verification["verification_id"]],
        )
        _validate_run(run)
        evidence_locator = self.history_store.artifacts.put_json(
            {
                "verification_id": verification["verification_id"],
                "checks": checks,
                "observed_target_manifest_sha256": current["manifest_sha256"],
            }
        )
        descriptor = HistoryStore.descriptor(
            "verification-evidence",
            "evidence",
            evidence_locator.rsplit(":", 1)[1],
            "local-private",
        )
        result["additional_history_artifacts"] = [
            *result.get("additional_history_artifacts", []),
            descriptor,
        ]
        result["verification"] = verification
        result["diagnostics"] = [] if passed else ["BPA147_VERIFICATION_FAILED"]
        self._record_context_history(result)
        if passed:
            proof = self._build_proof(
                result,
                result["history_manifest_sha256"],
                "target-verified",
            )
            result["proof"] = proof
            result["proof_locator"] = self.history_store.artifacts.put_json(proof)
        return result

    def export_proof(self, context: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(context)
        run = result["run"]
        _validate_run(run)
        direct = run["output_mode"] == "direct-apply"
        if (
            (direct and run["state"] != "verified")
            or (
                not direct
                and (
                    run["state"] != "released"
                    or run["output_mode"] not in {"instructions", "patch-bundle"}
                )
            )
        ):
            _fail(
                "BPA148_EXPORT_NOT_ADMITTED",
                "/run",
                "proof export is not admitted from the current state",
            )
        closure = "target-verified" if direct else "release-validated"
        _append_event(run, "export-proof", run["state"], "no-op", [])
        _validate_run(run)
        self._record_context_history(result)
        proof = self._build_proof(
            result, result["history_manifest_sha256"], closure
        )
        exportable = [
            row for row in proof["artifacts"] if row["privacy"] == "exportable"
        ]
        manifest = {
            "closure": closure,
            "run_id": run["run_id"],
            "excludes_private": True,
            "artifacts": [
                {
                    "artifact_id": row["artifact_id"],
                    "kind": row["kind"],
                    "sha256": row["sha256"],
                }
                for row in exportable
            ],
        }
        manifest_sha256 = _digest_json(manifest)
        export_ids = [row["artifact_id"] for row in exportable]
        for row in proof["artifacts"]:
            row["included_in_export"] = row["artifact_id"] in export_ids
        proof["export"] = {
            "manifest_sha256": manifest_sha256,
            "excludes_private": True,
            "artifact_ids": export_ids,
        }
        proof["proof_id"] = _identity(
            "blueprints-proof:sha256:", proof, "proof_id"
        )
        _validate_proof(proof)
        locators = {
            "release-manifest": result["release_locator"],
            {
                "instructions": "placement-instructions",
                "patch-bundle": "patch-bundle",
                "direct-apply": "direct-diff",
            }[run["output_mode"]]: result["projection_locators"][
                {
                    "instructions": "instructions",
                    "patch-bundle": "patch",
                    "direct-apply": "direct_diff",
                }[run["output_mode"]]
            ],
        }
        artifact_payloads = []
        for row in exportable:
            content = self.artifact_store.read_bytes(locators[row["artifact_id"]])
            if _digest_bytes(content) != row["sha256"]:
                _fail(
                    "BPA149_EXPORT_ARTIFACT",
                    row["artifact_id"],
                    "exportable artifact digest drift",
                )
            artifact_payloads.append(
                {
                    "artifact_id": row["artifact_id"],
                    "sha256": row["sha256"],
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        bundle = {
            "schema_version": 1,
            "format": "susy-blueprints-proof-export-v1",
            "contract_id": ENGINE_CONTRACT_ID,
            "manifest": manifest,
            "proof": proof,
            "artifacts": artifact_payloads,
        }
        _validate(bundle, PROOF_EXPORT_SCHEMA, "proof-export")
        if _digest_json(bundle["manifest"]) != proof["export"]["manifest_sha256"]:
            _fail("BPA150_EXPORT_MANIFEST", "/manifest", "manifest digest drift")
        export_locator = self.artifact_store.put_json(bundle)
        proof_locator = self.history_store.artifacts.put_json(proof)
        result.update(
            {
                "proof": proof,
                "proof_locator": proof_locator,
                "export_bundle": bundle,
                "export_locator": export_locator,
            }
        )
        return result
