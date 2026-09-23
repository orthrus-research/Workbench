"""Exact profile bridge for the owner-admitted Cleanroom generic mod tree.

The Cleanroom profile owns the admitted fixture bytes and the two explicit
portability substitutions.  Blueprints owns the fresh-target observation,
Git bootstrap, exact application transaction, retained history, and recovery.
No build, runtime, support, or release-qualification claim is made here.
"""

from __future__ import annotations

from urllib.request import url2pathname

import base64
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from typing import Any, Callable, Mapping, NoReturn, Sequence, cast
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from workbench_blueprints import application_transaction
from workbench_blueprints import fresh_project
from workbench_blueprints.interface import AdapterSet


KIND_ID = "workbench-new-project-kind:cleanroom-mod"
OWNER_ID = "cleanroom-platform-profile"
HOOK_ID = "cleanroom-generic-mod-fixture-render-v2"
TRANSFORM_ID = "cleanroom-generic-mod-portable-build-root-v2"

REQUEST_FORMAT = "workbench-cleanroom-mod-construction-request-v2"
REQUEST_KIND = "workbench-cleanroom-mod-construction-request"
PLAN_FORMAT = "workbench-cleanroom-mod-construction-plan-v2"
PLAN_KIND = "workbench-cleanroom-mod-construction-plan"
RESULT_FORMAT = "workbench-cleanroom-mod-construction-result-v2"
RESULT_KIND = "workbench-cleanroom-mod-construction-result"
RECEIPT_FORMAT = "workbench-cleanroom-mod-construction-receipt-v2"
RECEIPT_KIND = "workbench-cleanroom-mod-construction-receipt"

FIXTURE_LOCK_RELATIVE = Path(
    "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop/fixture-lock-v1.json"
)
FIXTURE_SCHEMA_RELATIVE = Path(
    "profiles/platforms/cleanroom/schemas/workbench-cleanroom-generic-mod-fixture-lock-v1.schema.json"
)
FIXTURE_ROOT_RELATIVE = Path(
    "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop"
)
OWNER_RELATIVE = Path(
    "profiles/platforms/cleanroom/new-project-kinds/cleanroom-mod-construction-owner-v2.json"
)
OWNER_SCHEMA_RELATIVE = Path(
    "profiles/platforms/cleanroom/schemas/workbench-cleanroom-mod-construction-owner-v2.schema.json"
)
REQUEST_SCHEMA_RELATIVE = Path(
    "profiles/platforms/cleanroom/schemas/workbench-cleanroom-mod-construction-request-v2.schema.json"
)
PLAN_SCHEMA_RELATIVE = Path(
    "profiles/platforms/cleanroom/schemas/workbench-cleanroom-mod-construction-plan-v2.schema.json"
)
RESULT_SCHEMA_RELATIVE = Path(
    "profiles/platforms/cleanroom/schemas/workbench-cleanroom-mod-construction-result-v2.schema.json"
)

MAXIMUM_SOURCE_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_ID = re.compile(r"^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$")

_BUILD_DIRECTORY_BEFORE = (
    b"layout.buildDirectory = file(\n"
    b"    '../../../../../.workbench/build/cleanroom/0.6.8-alpha/"
    b"generic-mod-daily-loop'\n"
    b")"
)
_BUILD_DIRECTORY_AFTER = (
    b"layout.buildDirectory = layout.projectDirectory.dir('.workbench/build')"
)
_IGNORED_ROOT_BEFORE = (
    b"def ignoredRoot = rootProject.file('../../../../../.workbench').toPath().normalize()"
)
_IGNORED_ROOT_AFTER = (
    b"def ignoredRoot = rootProject.file('.workbench').toPath().normalize()"
)

AUTHORITY_BOUNDARY = {
    "construction_owner": "Blueprints",
    "profile_owner": "cleanroom-platform-profile",
    "publication_authorized": False,
    "scope": "exact-owner-admitted-fresh-project",
    "transaction_owner": "Blueprints",
}


class CleanroomModConstructionError(ValueError):
    """Cleanroom construction input, owner bytes, or target state is invalid."""


def _fail(message: str) -> NoReturn:
    raise CleanroomModConstructionError(message)


def _canonical(value: Any) -> bytes:
    try:
        return application_transaction.canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise CleanroomModConstructionError(
            "Cleanroom construction value is not canonical JSON"
        ) from exc


def _sealed(kind: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return application_transaction.seal(kind, body)


def _absolute(value: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(Path(value).expanduser())))


def _ordinary_directory(path: Path, label: str) -> Path:
    try:
        state = path.lstat()
    except OSError as exc:
        raise CleanroomModConstructionError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail(f"{label} must be an ordinary non-symlink directory")
    return path.resolve()


def _read_regular(path: Path, label: str, maximum: int = MAXIMUM_SOURCE_BYTES) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise CleanroomModConstructionError(f"cannot open {label}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            _fail(f"{label} must be a bounded ordinary file")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
        )
        if len(raw) > maximum or len(raw) != before.st_size or identity(before) != identity(after):
            _fail(f"{label} changed while being read")
        return raw
    finally:
        os.close(descriptor)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    raw = _read_regular(path, label)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomModConstructionError(f"cannot decode {label}") from exc
    if type(value) is not dict:
        _fail(f"{label} must be one ordinary object")
    return cast(dict[str, Any], value)


def _suite_file(suite: Path, relative: Path, label: str) -> Path:
    path = suite / relative
    try:
        state = path.lstat()
    except OSError as exc:
        raise CleanroomModConstructionError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
        _fail(f"{label} must be an ordinary non-symlink file")
    return path


def _validate_schema(suite: Path, relative: Path, value: Mapping[str, Any]) -> None:
    schema = _load_json(_suite_file(suite, relative, "construction schema"), "construction schema")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = "/" + "/".join(str(item) for item in errors[0].absolute_path)
        _fail(f"construction schema rejected {location}: {errors[0].message}")


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] in {".git", ".deconstruction", ".workbench"}
    ):
        _fail(f"{label} must be a safe project source path")
    return path


def _target_from_uri(value: Any) -> Path:
    if type(value) is not str:
        _fail("target_uri must be a file URI")
    parsed = urlparse(value)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
        _fail("target_uri must be one local file URI")
    return _absolute(url2pathname(parsed.path))


def _fixture_lock(suite: Path) -> tuple[dict[str, Any], bytes]:
    lock_path = _suite_file(suite, FIXTURE_LOCK_RELATIVE, "fixture lock")
    raw = _read_regular(lock_path, "fixture lock")
    lock = _load_json(lock_path, "fixture lock")
    schema = _load_json(
        _suite_file(suite, FIXTURE_SCHEMA_RELATIVE, "fixture lock schema"),
        "fixture lock schema",
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(lock)
    )
    if errors:
        _fail(f"fixture lock schema rejected owner bytes: {errors[0].message}")
    rows = lock.get("declared_values", {}).get("files")
    if type(rows) is not list or len(rows) != 19:
        _fail("fixture lock must retain exactly 19 owner-admitted source rows")
    if rows != sorted(rows, key=lambda row: row["path"].encode("utf-8")):
        _fail("fixture lock rows are not bytewise path sorted")
    seen: set[str] = set()
    fixture_root = _ordinary_directory(suite / FIXTURE_ROOT_RELATIVE, "fixture root")
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256", "size"}:
            _fail("fixture lock row fields changed")
        relative = _safe_relative(row["path"], "fixture source path")
        if relative.as_posix() in seen:
            _fail("fixture lock contains duplicate source paths")
        seen.add(relative.as_posix())
        source = fixture_root.joinpath(*relative.parts)
        source_raw = _read_regular(source, f"fixture source {relative}")
        if (
            row["sha256"] != f"sha256:{sha256(source_raw).hexdigest()}"
            or row["size"] != len(source_raw)
        ):
            _fail(f"fixture source identity changed: {relative}")
    canonical_tree = _canonical({"algorithm": "sha256-file-tree-v1", "files": rows})
    tree_digest = f"sha256:{sha256(canonical_tree).hexdigest()}"
    declared = lock["declared_values"]
    if (
        declared.get("tree_digest_algorithm") != "sha256-file-tree-v1"
        or declared.get("tree_digest") != tree_digest
        or declared.get("identity", {}).get("digest") != tree_digest
    ):
        _fail("fixture tree identity changed")
    return lock, raw


def _portable_bytes(path: str, raw: bytes) -> tuple[bytes, str | None]:
    if path != "build.gradle":
        return raw, None
    if raw.count(_BUILD_DIRECTORY_BEFORE) != 1 or raw.count(_IGNORED_ROOT_BEFORE) != 1:
        _fail("fixture build portability source markers changed")
    transformed = raw.replace(_BUILD_DIRECTORY_BEFORE, _BUILD_DIRECTORY_AFTER).replace(
        _IGNORED_ROOT_BEFORE, _IGNORED_ROOT_AFTER
    )
    if b"../../../../../.workbench" in transformed:
        _fail("portable build transform left a location-bound Workbench path")
    return transformed, TRANSFORM_ID


def _rendered_outputs(suite: Path) -> tuple[dict[str, bytes], dict[str, Any], bytes]:
    lock, lock_raw = _fixture_lock(suite)
    fixture_root = suite / FIXTURE_ROOT_RELATIVE
    outputs: dict[str, bytes] = {}
    files: list[dict[str, Any]] = []
    for row in lock["declared_values"]["files"]:
        relative = _safe_relative(row["path"], "fixture source path")
        source_raw = _read_regular(
            fixture_root.joinpath(*relative.parts), f"fixture source {relative}"
        )
        output, _ = _portable_bytes(relative.as_posix(), source_raw)
        outputs[relative.as_posix()] = output
        files.append(
            {
                "path": relative.as_posix(),
                "sha256": f"sha256:{sha256(output).hexdigest()}",
                "size": len(output),
            }
        )
    manifest_body = {"algorithm": "sha256-file-tree-v1", "files": files}
    manifest = {
        **manifest_body,
        "tree_digest": f"sha256:{sha256(_canonical(manifest_body)).hexdigest()}",
    }
    return outputs, manifest, lock_raw


def cleanroom_mod_adapter_set(suite_root: Path | str) -> AdapterSet:
    """Return the explicit non-serializable profile render bridge."""

    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    outputs, _, _ = _rendered_outputs(suite)

    def render(hook: dict[str, Any], values: dict[str, Any], output_path: str) -> bytes:
        if hook != {
            "id": HOOK_ID,
            "owner_id": OWNER_ID,
            "schema_version": 2,
        }:
            _fail("Cleanroom render hook identity changed")
        if set(values) != {"kind_id", "owner_record_id", "request_id"}:
            _fail("Cleanroom render hook values changed")
        if values.get("kind_id") != KIND_ID or not _CONTENT_ID.fullmatch(
            str(values.get("owner_record_id", ""))
        ) or not _CONTENT_ID.fullmatch(str(values.get("request_id", ""))):
            _fail("Cleanroom render hook owner values changed")
        _safe_relative(output_path, "render output path")
        try:
            return outputs[output_path]
        except KeyError as exc:
            raise CleanroomModConstructionError(
                "Cleanroom render hook requested an unowned output"
            ) from exc

    return AdapterSet(hook_runner=render)


def validate_construction_owner(
    suite_root: Path | str, value: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate the additive owner record and every exact source reference."""

    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    owner_path = _suite_file(suite, OWNER_RELATIVE, "construction owner")
    owner = _load_json(owner_path, "construction owner") if value is None else dict(value)
    _validate_schema(suite, OWNER_SCHEMA_RELATIVE, owner)
    body = dict(owner)
    supplied = body.pop("id", None)
    if supplied != application_transaction.content_id(
        "workbench-cleanroom-mod-construction-owner", body
    ):
        _fail("construction owner content identity changed")
    if owner.get("owner_id") != OWNER_ID or owner.get("kind_id") != KIND_ID:
        _fail("construction owner authority changed")
    lock, lock_raw = _fixture_lock(suite)
    lock_ref = owner.get("template_lock_ref", {})
    if (
        lock_ref.get("path") != FIXTURE_LOCK_RELATIVE.as_posix()
        or lock_ref.get("sha256") != f"sha256:{sha256(lock_raw).hexdigest()}"
        or lock_ref.get("tree_digest") != lock["declared_values"]["tree_digest"]
    ):
        _fail("construction owner fixture lock reference changed")
    outputs, manifest, _ = _rendered_outputs(suite)
    del outputs
    if owner.get("output_manifest") != manifest:
        _fail("construction owner output manifest changed")
    adapter_source = Path(
        "profiles/platforms/cleanroom/src/"
        "workbench_cleanroom_new_project/construction.py"
    )
    adapter_raw = _read_regular(
        _suite_file(suite, adapter_source, "construction adapter source"),
        "construction adapter source",
    )
    if owner.get("adapter_ref") != {
        "adapter_set_contract_id": "BLUEPRINTS-ADAPTER-SET-V1",
        "hook_id": HOOK_ID,
        "source_path": adapter_source.as_posix(),
        "source_sha256": f"sha256:{sha256(adapter_raw).hexdigest()}",
        "symbol": "cleanroom_mod_adapter_set",
    }:
        _fail("construction owner adapter source binding changed")
    expected_mechanics: list[dict[str, str]] = []
    for relative in (
        Path(
            "modules/blueprints/src/workbench_blueprints/"
            "application_transaction.py"
        ),
        Path(
            "modules/blueprints/src/workbench_blueprints/"
            "fresh_project.py"
        ),
    ):
        raw = _read_regular(
            _suite_file(suite, relative, "Blueprints construction mechanics"),
            "Blueprints construction mechanics",
        )
        expected_mechanics.append(
            {"path": relative.as_posix(), "sha256": f"sha256:{sha256(raw).hexdigest()}"}
        )
    if owner.get("blueprints_mechanics_refs") != expected_mechanics:
        _fail("construction owner Blueprints mechanics binding changed")
    transform = owner.get("portable_transform", {})
    if (
        transform.get("transform_id") != TRANSFORM_ID
        or transform.get("path") != "build.gradle"
        or transform.get("replacements")
        != [
            {
                "after_sha256": f"sha256:{sha256(_BUILD_DIRECTORY_AFTER).hexdigest()}",
                "after_size": len(_BUILD_DIRECTORY_AFTER),
                "before_sha256": f"sha256:{sha256(_BUILD_DIRECTORY_BEFORE).hexdigest()}",
                "before_size": len(_BUILD_DIRECTORY_BEFORE),
            },
            {
                "after_sha256": f"sha256:{sha256(_IGNORED_ROOT_AFTER).hexdigest()}",
                "after_size": len(_IGNORED_ROOT_AFTER),
                "before_sha256": f"sha256:{sha256(_IGNORED_ROOT_BEFORE).hexdigest()}",
                "before_size": len(_IGNORED_ROOT_BEFORE),
            },
        ]
    ):
        _fail("construction owner portable transform changed")
    for field, expected_path in (
        ("owner_schema_ref", OWNER_SCHEMA_RELATIVE),
        ("request_schema_ref", REQUEST_SCHEMA_RELATIVE),
        ("plan_schema_ref", PLAN_SCHEMA_RELATIVE),
        ("result_schema_ref", RESULT_SCHEMA_RELATIVE),
    ):
        reference = owner.get("contract_schemas", {}).get(field, {})
        schema_path = _suite_file(suite, expected_path, field)
        schema_raw = _read_regular(schema_path, field)
        if (
            reference.get("path") != expected_path.as_posix()
            or reference.get("sha256") != f"sha256:{sha256(schema_raw).hexdigest()}"
        ):
            _fail(f"construction owner {field} changed")
    return owner


def build_cleanroom_mod_request(
    target: Path | str,
    *,
    output_mode: str = "instructions",
    sequence: int = 0,
    allow_direct_apply: bool = False,
) -> dict[str, Any]:
    """Build one sealed request without observing or mutating the target."""

    path = _absolute(target)
    body = {
        "consent": {"allow_direct_apply": allow_direct_apply},
        "format": REQUEST_FORMAT,
        "kind_id": KIND_ID,
        "operation": "create",
        "output_mode": output_mode,
        "schema_version": 2,
        "sequence": sequence,
        "target_uri": path.as_uri(),
    }
    return _sealed(REQUEST_KIND, body)


def validate_cleanroom_mod_request(
    suite_root: Path | str, value: Mapping[str, Any]
) -> dict[str, Any]:
    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    if type(value) is not dict:
        _fail("construction request must be one ordinary object")
    request = dict(value)
    _validate_schema(suite, REQUEST_SCHEMA_RELATIVE, request)
    body = dict(request)
    supplied = body.pop("id", None)
    if supplied != application_transaction.content_id(REQUEST_KIND, body):
        _fail("construction request content identity changed")
    if request["output_mode"] == "direct-apply" and not request["consent"]["allow_direct_apply"]:
        _fail("direct apply was not explicitly enabled in the request")
    _target_from_uri(request["target_uri"])
    return request


def _plan_operations(
    suite: Path,
    owner: Mapping[str, Any],
    request: Mapping[str, Any],
    adapters: AdapterSet,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lock, _ = _fixture_lock(suite)
    hook = {"id": HOOK_ID, "owner_id": OWNER_ID, "schema_version": 2}
    values = {
        "kind_id": KIND_ID,
        "owner_record_id": owner["id"],
        "request_id": request["id"],
    }
    if adapters.hook_runner is None:
        _fail("Cleanroom construction requires the profile render hook")
    operations: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for ordinal, source_row in enumerate(lock["declared_values"]["files"]):
        path = source_row["path"]
        rendered = adapters.hook_runner(hook, values, path)
        if type(rendered) is not bytes or len(rendered) > MAXIMUM_SOURCE_BYTES:
            _fail("Cleanroom render hook returned invalid bytes")
        digest = sha256(rendered).hexdigest()
        operations.append(
            {
                "after_base64": base64.b64encode(rendered).decode("ascii"),
                "after_sha256": digest,
                "after_size": len(rendered),
                "before_base64": None,
                "before_sha256": None,
                "before_size": 0,
                "ordinal": ordinal,
                "path": path,
                "source_path": path,
                "source_sha256": source_row["sha256"],
                "transform_id": TRANSFORM_ID if path == "build.gradle" else None,
            }
        )
        files.append(
            {"path": path, "sha256": f"sha256:{digest}", "size": len(rendered)}
        )
    manifest_body = {"algorithm": "sha256-file-tree-v1", "files": files}
    return operations, {
        **manifest_body,
        "tree_digest": f"sha256:{sha256(_canonical(manifest_body)).hexdigest()}",
    }


def _portable_transform(owner: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], owner["portable_transform"])


def _source_lock(owner: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], owner["template_lock_ref"])


def _plan_body(
    suite: Path,
    request: Mapping[str, Any],
    owner: Mapping[str, Any],
    observation: Mapping[str, Any],
    adapters: AdapterSet,
) -> dict[str, Any]:
    operations, manifest = _plan_operations(suite, owner, request, adapters)
    if manifest != owner["output_manifest"]:
        _fail("rendered output differs from construction owner")
    return {
        "adapter": {
            "hook_id": HOOK_ID,
            "owner_id": OWNER_ID,
            "render_mode": "locked-fixture-portable-transform-v2",
        },
        "authority_boundary": AUTHORITY_BOUNDARY,
        "direct_apply_authorized": request["consent"]["allow_direct_apply"],
        "format": PLAN_FORMAT,
        "kind": PLAN_KIND,
        "kind_id": KIND_ID,
        "limitations": owner["limitations"],
        "operations": operations,
        "output_manifest": manifest,
        "output_mode": request["output_mode"],
        "owner_record_id": owner["id"],
        "portable_transform": _portable_transform(owner),
        "request_id": request["id"],
        "schema_version": 2,
        "source_lock": _source_lock(owner),
        "target_observation": observation,
        "target_uri": request["target_uri"],
    }


def validate_cleanroom_mod_plan(
    suite_root: Path | str, value: Mapping[str, Any]
) -> dict[str, Any]:
    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    if type(value) is not dict:
        _fail("construction plan must be one ordinary object")
    plan = dict(value)
    _validate_schema(suite, PLAN_SCHEMA_RELATIVE, plan)
    body = dict(plan)
    supplied = body.pop("id", None)
    if supplied != application_transaction.content_id(PLAN_KIND, body):
        _fail("construction plan content identity changed")
    owner = validate_construction_owner(suite)
    if (
        plan.get("owner_record_id") != owner["id"]
        or plan.get("authority_boundary") != AUTHORITY_BOUNDARY
        or plan.get("source_lock") != owner["template_lock_ref"]
        or plan.get("portable_transform") != owner["portable_transform"]
        or plan.get("output_manifest") != owner["output_manifest"]
        or plan.get("limitations") != owner["limitations"]
    ):
        _fail("construction plan owner binding changed")
    observation = fresh_project.validate_fresh_target_observation(
        cast(Mapping[str, Any], plan["target_observation"])
    )
    if plan.get("target_uri") != observation["target_uri"]:
        _fail("construction plan target identity changed")
    request_stub = {"id": plan["request_id"]}
    adapters = cleanroom_mod_adapter_set(suite)
    operations, manifest = _plan_operations(suite, owner, request_stub, adapters)
    if plan.get("operations") != operations or plan.get("output_manifest") != manifest:
        _fail("construction plan rendered bytes changed")
    if plan.get("output_mode") == "direct-apply" and not plan.get("direct_apply_authorized"):
        _fail("construction plan lacks direct-apply consent")
    return plan


def verify_cleanroom_mod_plan(
    suite_root: Path | str, value: Mapping[str, Any]
) -> dict[str, Any]:
    """Revalidate owner bytes, rendered outputs, and the exact target observation."""

    plan = validate_cleanroom_mod_plan(suite_root, value)
    target = _target_from_uri(plan["target_uri"])
    try:
        observed = fresh_project.observe_fresh_target(target)
    except (OSError, ValueError):
        return {"reason": "TARGET_UNVERIFIABLE", "state": "stale"}
    if observed != plan["target_observation"]:
        return {"reason": "TARGET_OBSERVATION_CHANGED", "state": "stale"}
    return {"reason": None, "state": "ready"}


def _result(
    suite: Path,
    *,
    command: str,
    state: str,
    plan: Mapping[str, Any],
    application_receipt: Mapping[str, Any] | None,
    bootstrap_receipt: Mapping[str, Any] | None,
    recovery: Mapping[str, Any] | None,
) -> dict[str, Any]:
    body = {
        "application_receipt": None if application_receipt is None else dict(application_receipt),
        "bootstrap_receipt": None if bootstrap_receipt is None else dict(bootstrap_receipt),
        "command": command,
        "format": RESULT_FORMAT,
        "kind": RESULT_KIND,
        "limitations": plan["limitations"],
        "output_manifest": plan["output_manifest"],
        "plan": dict(plan),
        "plan_id": plan["id"],
        "recovery": None if recovery is None else dict(recovery),
        "request_id": plan["request_id"],
        "schema_version": 2,
        "state": state,
        "target_uri": plan["target_uri"],
    }
    result = _sealed(RESULT_KIND, body)
    return validate_cleanroom_mod_result(suite, result)


def preview_cleanroom_mod_construction(
    suite_root: Path | str,
    request: Mapping[str, Any],
    *,
    adapters: AdapterSet | None = None,
) -> dict[str, Any]:
    """Compile a reviewed plan without creating or changing the destination."""

    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    reviewed = validate_cleanroom_mod_request(suite, request)
    owner = validate_construction_owner(suite)
    target = _target_from_uri(reviewed["target_uri"])
    observation = fresh_project.observe_fresh_target(target)
    bridge = cleanroom_mod_adapter_set(suite) if adapters is None else adapters
    plan = _sealed(
        PLAN_KIND,
        _plan_body(suite, reviewed, owner, observation, bridge),
    )
    validate_cleanroom_mod_plan(suite, plan)
    return _result(
        suite,
        command="preview",
        state="ready",
        plan=plan,
        application_receipt=None,
        bootstrap_receipt=None,
        recovery=None,
    )


def _validate_application_receipt(
    receipt: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    if type(receipt) is not dict:
        _fail("construction application receipt must be one ordinary object")
    value = dict(receipt)
    body = dict(value)
    supplied = body.pop("id", None)
    if (
        value.get("format") != RECEIPT_FORMAT
        or value.get("kind") != RECEIPT_KIND
        or value.get("schema_version") != 1
        or value.get("plan_id") != plan["id"]
        or value.get("authority_boundary") != AUTHORITY_BOUNDARY
        or value.get("state") not in {"applied", "rejected"}
        or supplied != application_transaction.content_id(RECEIPT_KIND, body)
    ):
        _fail("construction application receipt identity changed")
    if value["state"] == "applied":
        try:
            application_transaction.validate_applied_application_receipt(
                value,
                plan,
                receipt_format=RECEIPT_FORMAT,
                receipt_kind=RECEIPT_KIND,
                receipt_content_kind=RECEIPT_KIND,
                success_mutation_state="constructed-owner-admitted-project",
            )
        except ValueError as exc:
            raise CleanroomModConstructionError(
                "construction success receipt changed"
            ) from exc
    return value


def _application_receipt_path(state_root: Path) -> Path:
    return state_root / "construction-application-receipt-v2.json"


def _commit_application_receipt(
    state_root: Path,
    receipt: Mapping[str, Any],
    plan: Mapping[str, Any],
    external: Callable[[Mapping[str, Any]], None] | None,
) -> None:
    reviewed = _validate_application_receipt(receipt, plan)
    path = _application_receipt_path(state_root)
    raw = _canonical(reviewed) + b"\n"
    if path.exists() or path.is_symlink():
        if _read_regular(path, "retained application receipt") != raw:
            _fail("retained application receipt identity changed")
    else:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written < 1:
                    raise OSError("receipt write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if external is not None:
        external(reviewed)


def _load_application_receipt(state_root: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_application_receipt(
        _load_json(_application_receipt_path(state_root), "retained application receipt"),
        plan,
    )


def _verify_constructed_target(target: Path, plan: Mapping[str, Any]) -> bool:
    expected = {row["path"]: row for row in plan["operations"]}
    observed: set[str] = set()
    try:
        _ordinary_directory(target, "constructed target")
        _ordinary_directory(target / ".git", "constructed target .git")
        for candidate in target.rglob("*"):
            relative = candidate.relative_to(target).as_posix()
            if relative == ".git" or relative.startswith(".git/"):
                continue
            state = candidate.lstat()
            if stat.S_ISDIR(state.st_mode) and not stat.S_ISLNK(state.st_mode):
                continue
            if stat.S_ISLNK(state.st_mode) or not stat.S_ISREG(state.st_mode):
                return False
            row = expected.get(relative)
            if row is None:
                return False
            raw = _read_regular(candidate, f"constructed source {relative}")
            if len(raw) != row["after_size"] or sha256(raw).hexdigest() != row["after_sha256"]:
                return False
            observed.add(relative)
        if observed != set(expected):
            return False
        environment = dict(os.environ)
        for key in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
            environment.pop(key, None)
        environment.update({"GIT_CONFIG_NOSYSTEM": "1", "LC_ALL": "C"})
        head = subprocess.run(
            ["git", "-C", os.fspath(target), "rev-parse", "--verify", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        return head.returncode in {1, 128}
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def apply_cleanroom_mod_construction(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    state_root: Path | str,
    *,
    consent_plan_id: str,
    fail_after_ordinal: int | None = None,
    after_preflight: Callable[[Path], None] | None = None,
    commit_receipt: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Apply one exact consented plan through Blueprints-owned mechanics."""

    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    reviewed = validate_cleanroom_mod_plan(suite, plan)
    if consent_plan_id != reviewed["id"]:
        _fail("construction apply requires consent to the exact reviewed plan ID")
    if reviewed["output_mode"] != "direct-apply" or not reviewed["direct_apply_authorized"]:
        _fail("construction plan is inspect-only")
    verification = verify_cleanroom_mod_plan(suite, reviewed)
    if verification["state"] != "ready":
        _fail(f"construction plan is stale: {verification['reason']}")
    target = _target_from_uri(reviewed["target_uri"])
    state = _absolute(state_root)
    fresh_project.prepare_fresh_target(
        target,
        reviewed["target_observation"],
        state,
        plan_id=reviewed["id"],
    )

    def source_preflight(root: Path, candidate: Mapping[str, Any]) -> bool:
        return (
            root == target
            and candidate.get("id") == reviewed["id"]
            and fresh_project.verify_bootstrapped_target(
                target,
                state,
                plan_id=reviewed["id"],
                operations=reviewed["operations"],
            )
        )

    def commit(value: Mapping[str, Any]) -> None:
        _commit_application_receipt(state, value, reviewed, commit_receipt)

    try:
        receipt = application_transaction.apply_application_transaction(
            target,
            reviewed,
            state,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="constructed-owner-admitted-project",
            fail_after_ordinal=fail_after_ordinal,
            after_preflight=after_preflight,
            source_preflight=source_preflight,
            commit_receipt=commit,
        )
        application_receipt = _validate_application_receipt(receipt, reviewed)
    except BaseException:
        # The retained bootstrap journal deliberately remains when exact
        # restoration cannot be established; recover() is then authoritative.
        try:
            if not _application_receipt_path(state).exists():
                fresh_project.restore_fresh_target(
                    target, state, plan_id=reviewed["id"]
                )
        except Exception:
            pass
        raise
    if application_receipt["state"] != "applied":
        recovery = fresh_project.restore_fresh_target(
            target, state, plan_id=reviewed["id"]
        )
        return _result(
            suite,
            command="apply",
            state="rejected",
            plan=reviewed,
            application_receipt=application_receipt,
            bootstrap_receipt=None,
            recovery=recovery,
        )
    if not _verify_constructed_target(target, reviewed):
        _fail("constructed target differs from the exact reviewed output")
    bootstrap_receipt = fresh_project.finalize_fresh_target(
        target, state, plan_id=reviewed["id"]
    )
    return _result(
        suite,
        command="apply",
        state="applied",
        plan=reviewed,
        application_receipt=application_receipt,
        bootstrap_receipt=bootstrap_receipt,
        recovery=None,
    )


def recover_cleanroom_mod_construction(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    state_root: Path | str,
) -> dict[str, Any]:
    """Recover an interrupted bootstrap/application without guessing ownership."""

    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    reviewed = validate_cleanroom_mod_plan(suite, plan)
    target = _target_from_uri(reviewed["target_uri"])
    state = _absolute(state_root)
    transaction_journal = state / "active-transaction.json"
    application_receipt: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None
    if transaction_journal.exists() and not transaction_journal.is_symlink():
        recovered = application_transaction.recover_application_transaction(
            target,
            reviewed,
            state,
            receipt_format=RECEIPT_FORMAT,
            receipt_kind=RECEIPT_KIND,
            receipt_content_kind=RECEIPT_KIND,
            success_mutation_state="constructed-owner-admitted-project",
            commit_receipt=lambda value: _commit_application_receipt(
                state, value, reviewed, None
            ),
        )
        recovery = dict(recovered)
        if recovered["outcome"] == "review-required":
            return _result(
                suite,
                command="recover",
                state="review-required",
                plan=reviewed,
                application_receipt=None,
                bootstrap_receipt=None,
                recovery=recovery,
            )
        if recovered["application_receipt"] is not None:
            application_receipt = _validate_application_receipt(
                recovered["application_receipt"], reviewed
            )
    if application_receipt is None and _application_receipt_path(state).exists():
        application_receipt = _load_application_receipt(state, reviewed)
    if application_receipt is not None and application_receipt["state"] == "applied":
        if not _verify_constructed_target(target, reviewed):
            _fail("recovered constructed target differs from reviewed output")
        bootstrap_receipt = fresh_project.finalize_fresh_target(
            target, state, plan_id=reviewed["id"]
        )
        return _result(
            suite,
            command="recover",
            state="applied",
            plan=reviewed,
            application_receipt=application_receipt,
            bootstrap_receipt=bootstrap_receipt,
            recovery=recovery,
        )
    bootstrap_recovery = fresh_project.restore_fresh_target(
        target, state, plan_id=reviewed["id"]
    )
    return _result(
        suite,
        command="recover",
        state="restored",
        plan=reviewed,
        application_receipt=None,
        bootstrap_receipt=None,
        recovery=bootstrap_recovery if recovery is None else {**recovery, "bootstrap": bootstrap_recovery},
    )


def validate_cleanroom_mod_result(
    suite_root: Path | str, value: Mapping[str, Any]
) -> dict[str, Any]:
    suite = _ordinary_directory(_absolute(suite_root), "suite root")
    if type(value) is not dict:
        _fail("construction result must be one ordinary object")
    result = dict(value)
    _validate_schema(suite, RESULT_SCHEMA_RELATIVE, result)
    body = dict(result)
    supplied = body.pop("id", None)
    if supplied != application_transaction.content_id(RESULT_KIND, body):
        _fail("construction result content identity changed")
    plan = validate_cleanroom_mod_plan(suite, cast(Mapping[str, Any], result["plan"]))
    if (
        result.get("plan_id") != plan["id"]
        or result.get("request_id") != plan["request_id"]
        or result.get("target_uri") != plan["target_uri"]
        or result.get("output_manifest") != plan["output_manifest"]
        or result.get("limitations") != plan["limitations"]
    ):
        _fail("construction result projection changed")
    application_receipt = result.get("application_receipt")
    if application_receipt is not None:
        _validate_application_receipt(application_receipt, plan)
    return result
