"""Validated, authority-preserving developer-feature projection for IDE clients."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, NoReturn, cast

from .developer_feature import (
    DeveloperFeatureError,
    PLAN_KIND as MATERIAL_PLAN_KIND,
    RECEIPT_KIND as MATERIAL_RECEIPT_KIND,
    RECOVERY_KIND as MATERIAL_RECOVERY_KIND,
    ROLLBACK_KIND as MATERIAL_ROLLBACK_KIND,
    RUN_KIND as MATERIAL_RUN_KIND,
    resolve_feature_record,
    transaction_state_root,
    validate_material_fluid_recipe_plan,
    validate_material_fluid_recipe_receipt,
    validate_material_fluid_recipe_recovery,
    validate_material_fluid_recipe_rollback,
    verify_material_fluid_recipe_plan,
)
from .developer_feature_runtime import validate_material_fluid_recipe_run
from workbench_blueprints.profile_construction import recipe_change_authority
RECIPE_FAMILY = "recipe-change"
from .developer_recipe_runtime import (
    KIND as RECIPE_RUNTIME_COMPARISON_KIND,
    RecipeRuntimeComparisonError,
    validate_recipe_change_runtime_comparison,
)
from .developer_source_feature import (
    PLAN_KIND as SOURCE_PLAN_KIND,
    QUEST_FAMILY,
    RECEIPT_KIND as SOURCE_RECEIPT_KIND,
    RECOVERY_KIND as SOURCE_RECOVERY_KIND,
    ROLLBACK_KIND as SOURCE_ROLLBACK_KIND,
    validate_source_feature_plan,
    validate_source_feature_receipt,
    validate_source_feature_recovery,
    validate_source_feature_rollback,
    verify_source_feature_plan,
)


FORMAT = "workbench-developer-feature-presentation-v1"
FORMAT_V2 = "workbench-developer-feature-presentation-v2"
KIND = "workbench-developer-feature-presentation"
RECORD_CATALOG_FORMAT = "workbench-developer-feature-record-catalog-v1"
RECORD_CATALOG_KIND = "workbench-developer-feature-record-catalog"
MATERIAL_FAMILY = "material-fluid-recipe"
FAMILIES = (MATERIAL_FAMILY, RECIPE_FAMILY, QUEST_FAMILY)
COLLECTIONS = ("plans", "receipts", "rollbacks", "recoveries", "runs")
_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_DISCOVERED_RECORDS = 4096
_UNAVAILABLE_RECIPE_RUNTIME_MESSAGE = "recipe runtime observer protocol changed"
_RECIPE_KINDS = {
    "plans": "workbench-supersymmetry-recipe-change-plan",
    "receipts": "workbench-supersymmetry-recipe-change-receipt",
    "rollbacks": "workbench-supersymmetry-recipe-change-rollback",
    "recoveries": "workbench-supersymmetry-recipe-change-recovery",
}
_KIND_FAMILIES = {
    "plans": {
        MATERIAL_PLAN_KIND: MATERIAL_FAMILY,
        _RECIPE_KINDS["plans"]: RECIPE_FAMILY,
        SOURCE_PLAN_KIND: QUEST_FAMILY,
    },
    "receipts": {
        MATERIAL_RECEIPT_KIND: MATERIAL_FAMILY,
        _RECIPE_KINDS["receipts"]: RECIPE_FAMILY,
        SOURCE_RECEIPT_KIND: QUEST_FAMILY,
    },
    "rollbacks": {
        MATERIAL_ROLLBACK_KIND: MATERIAL_FAMILY,
        _RECIPE_KINDS["rollbacks"]: RECIPE_FAMILY,
        SOURCE_ROLLBACK_KIND: QUEST_FAMILY,
    },
    "recoveries": {
        MATERIAL_RECOVERY_KIND: MATERIAL_FAMILY,
        _RECIPE_KINDS["recoveries"]: RECIPE_FAMILY,
        SOURCE_RECOVERY_KIND: QUEST_FAMILY,
    },
    "runs": {
        MATERIAL_RUN_KIND: MATERIAL_FAMILY,
        RECIPE_RUNTIME_COMPARISON_KIND: RECIPE_FAMILY,
    },
}


def _fail(message: str) -> NoReturn:
    raise DeveloperFeatureError(message)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _seal(
    body: Mapping[str, Any],
    *,
    kind: str = KIND,
) -> dict[str, Any]:
    return {
        **dict(body),
        "id": f"{kind}:sha256:{sha256(_canonical_bytes(body)).hexdigest()}",
    }


def _record_uri(
    state_root: Path,
    collection: str,
    reference: Path | str,
) -> str:
    text = str(reference)
    if _CONTENT_ID.fullmatch(text):
        path = state_root / collection / text.rsplit(":", 1)[1] / "record.json"
    else:
        path = Path(reference).expanduser().resolve()
    return path.absolute().as_uri()


def _validate_plan(
    suite_root: Path,
    family: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if family == MATERIAL_FAMILY:
        return validate_material_fluid_recipe_plan(value)
    if family == RECIPE_FAMILY:
        return cast(
            dict[str, Any],
            recipe_change_authority("supersymmetry").validate_recipe_change_plan(value),
        )
    if family == QUEST_FAMILY:
        return validate_source_feature_plan(value, suite_root=suite_root)
    _fail("developer feature presentation family is unsupported")


def _verify_plan(
    suite_root: Path,
    family: str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    if family == MATERIAL_FAMILY:
        return verify_material_fluid_recipe_plan(suite_root, plan)
    if family == RECIPE_FAMILY:
        return cast(
            dict[str, Any],
            recipe_change_authority("supersymmetry").verify_recipe_change_plan(
                suite_root, plan
            ),
        )
    return verify_source_feature_plan(suite_root, plan)


def _presentation_verification(
    suite_root: Path,
    family: str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return _verify_plan(suite_root, family, plan)
    except (DeveloperFeatureError, OSError, ValueError) as exc:
        reason = str(exc) or exc.__class__.__name__
        return {
            "format": "workbench-developer-feature-presentation-verification-v1",
            "plan_id": plan["id"],
            "reason": reason[:4096],
            "schema_version": 1,
            "state": "stale",
        }


def _validate_related_record(
    suite_root: Path,
    family: str,
    collection: str,
    value: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    if collection == "plans":
        return dict(plan)
    if collection == "runs":
        if family == MATERIAL_FAMILY:
            return validate_material_fluid_recipe_run(value, plan)
        if family == RECIPE_FAMILY:
            return validate_recipe_change_runtime_comparison(
                suite_root, value, plan
            )
        _fail("the selected feature family has no runtime record")
    if family == MATERIAL_FAMILY:
        validators = {
            "receipts": validate_material_fluid_recipe_receipt,
            "rollbacks": validate_material_fluid_recipe_rollback,
            "recoveries": validate_material_fluid_recipe_recovery,
        }
        return validators[collection](value, plan)
    if family == RECIPE_FAMILY:
        authority = recipe_change_authority("supersymmetry")
        validators = {
            "receipts": authority.validate_recipe_change_receipt,
            "rollbacks": authority.validate_recipe_change_rollback,
            "recoveries": authority.validate_recipe_change_recovery,
        }
        return cast(dict[str, Any], validators[collection](value, plan))
    validators = {
        "receipts": validate_source_feature_receipt,
        "rollbacks": validate_source_feature_rollback,
        "recoveries": validate_source_feature_recovery,
    }
    return validators[collection](value, plan, suite_root=suite_root)


def _operation_projection(operation: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "after_base64",
        "after_sha256",
        "after_size",
        "before_base64",
        "before_sha256",
        "before_size",
        "diff",
        "operation",
        "ordinal",
        "outcome",
        "path",
        "role",
    )
    if any(field not in operation for field in fields):
        _fail("developer feature operation cannot be presented")
    return {field: operation[field] for field in fields}


def _recovery_available(state_root: Path, plan_id: str) -> bool:
    try:
        root = transaction_state_root(state_root, plan_id, create=False)
        metadata = (root / "active-transaction.json").lstat()
    except (DeveloperFeatureError, OSError):
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _actions(
    family: str,
    collection: str,
    plan: Mapping[str, Any],
    record: Mapping[str, Any],
    verification: Mapping[str, Any],
    *,
    recovery_available: bool,
) -> list[dict[str, Any]]:
    ready = verification.get("state") == "ready"
    plan_id = str(plan["id"])
    if collection == "plans":
        return [
            {
                "action": "check",
                "available": True,
                "consent_id": None,
                "reason": None,
            },
            {
                "action": "apply",
                "available": ready,
                "consent_id": plan_id if ready else None,
                "reason": None if ready else "plan is stale",
            },
            {
                "action": "run",
                "available": ready and family in {MATERIAL_FAMILY, RECIPE_FAMILY},
                "consent_id": (
                    plan_id
                    if ready and family in {MATERIAL_FAMILY, RECIPE_FAMILY}
                    else None
                ),
                "reason": (
                    None
                    if ready and family in {MATERIAL_FAMILY, RECIPE_FAMILY}
                    else "runtime execution is unavailable for this family"
                    if family not in {MATERIAL_FAMILY, RECIPE_FAMILY}
                    else "plan is stale"
                ),
            },
            {
                "action": "recover",
                "available": recovery_available,
                "consent_id": None,
                "reason": None if recovery_available else "no interrupted transaction is retained",
            },
        ]
    if collection == "receipts":
        applied = record.get("state") == "applied"
        return [
            {
                "action": "rollback",
                "available": applied,
                "consent_id": None,
                "reason": None if applied else "application was not successful",
            },
            {
                "action": "recover",
                "available": recovery_available,
                "consent_id": None,
                "reason": None if recovery_available else "no interrupted transaction is retained",
            },
        ]
    return []


def _runtime_projection(
    family: str,
    plan: Mapping[str, Any],
    collection: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    if collection == "runs":
        projection = {
            "action_available": True,
            "outcome": record.get("outcome"),
            "record_id": record.get("id"),
            "state": record.get("state"),
            "requirement": None,
        }
        owner_sides = record.get("sides")
        if (
            type(owner_sides) is dict
            and owner_sides
            and all(
                type(side) is dict and "retained_evidence" in side
                for side in owner_sides.values()
            )
        ):
            sides: list[dict[str, Any]] = []
            for role in sorted(owner_sides):
                side = owner_sides[role]
                if type(side) is not dict:
                    _fail("developer feature runtime side cannot be presented")
                assessment = side.get("assessment")
                evidence = side.get("retained_evidence")
                probe = side.get("probe")
                sides.append(
                    {
                        "assertion": (
                            {
                                "id": assessment.get("assessment_id"),
                                "state": assessment.get("state"),
                            }
                            if type(assessment) is dict
                            else None
                        ),
                        "error": side.get("error"),
                        "outcome": side.get("outcome"),
                        "probe": (
                            {
                                "id": probe.get("probe_id"),
                                "overlay_id": probe.get("overlay_id"),
                                "overlay_uri": probe.get("overlay_spec_uri"),
                                "script_uri": probe.get("script_uri"),
                            }
                            if type(probe) is dict
                            else None
                        ),
                        "receipt": (
                            {
                                "final_launch": evidence.get(
                                    "final_launch_receipt"
                                ),
                                "groovy_log": evidence.get("groovy_log"),
                                "runtime_session": evidence.get(
                                    "runtime_session_receipt"
                                ),
                            }
                            if type(evidence) is dict
                            else None
                        ),
                        "role": role,
                        "state": side.get("state"),
                    }
                )
            projection["sides"] = sides
        return projection
    requirement = plan.get("runtime_observation")
    return {
        "action_available": family in {MATERIAL_FAMILY, RECIPE_FAMILY},
        "outcome": None,
        "record_id": None,
        "state": (
            "available-not-observed"
            if family in {MATERIAL_FAMILY, RECIPE_FAMILY}
            else "required-not-available"
        ),
        "requirement": requirement,
    }


def _limitations_projection(
    plan: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    include_owner: bool,
) -> list[str]:
    limitations = list(plan.get("limitations", []))
    if include_owner:
        for limitation in record.get("limitations", []):
            if limitation not in limitations:
                limitations.append(limitation)
    return limitations


def present_feature_record(
    suite_root: Path | str,
    state_root: Path | str,
    *,
    family: str,
    collection: str,
    reference: Path | str,
) -> dict[str, Any]:
    """Validate one retained owner record and return one bounded IDE projection."""

    if family not in FAMILIES or collection not in COLLECTIONS:
        _fail("developer feature presentation selection is unsupported")
    if collection == "runs" and family == QUEST_FAMILY:
        _fail("quest-for-process has no retained runtime records")
    suite = Path(suite_root).expanduser().resolve()
    state = Path(os.path.abspath(os.fspath(Path(state_root).expanduser())))
    raw = resolve_feature_record(state, collection, reference)
    if collection == "plans":
        plan = _validate_plan(suite, family, raw)
    else:
        plan_id = raw.get("plan_id")
        if type(plan_id) is not str:
            _fail("developer feature record has no plan identity")
        plan = _validate_plan(
            suite,
            family,
            resolve_feature_record(state, "plans", plan_id),
        )
    record = _validate_related_record(suite, family, collection, raw, plan)
    verification = _presentation_verification(suite, family, plan)
    interrupted = _recovery_available(state, str(plan["id"]))
    runtime = _runtime_projection(family, plan, collection, record)
    presentation_format = FORMAT_V2 if "sides" in runtime else FORMAT
    limitations = _limitations_projection(
        plan, record, include_owner=presentation_format == FORMAT_V2
    )
    body = {
        "actions": _actions(
            family,
            collection,
            plan,
            record,
            verification,
            recovery_available=interrupted,
        ),
        "authority_boundary": plan["authority_boundary"],
        "collection": collection,
        "family": family,
        "format": presentation_format,
        "kind": KIND,
        "limitations": limitations,
        "operations": [
            _operation_projection(operation) for operation in plan["operations"]
        ],
        "owner_record": {
            "diagnostic_code": record.get("diagnostic_code"),
            "id": record["id"],
            "kind": record["kind"],
            "state": record["state"],
            "uri": _record_uri(state, collection, reference),
        },
        "plan_id": plan["id"],
        "request": plan["request"],
        "review": plan["review"],
        "runtime": runtime,
        "schema_version": 2 if presentation_format == FORMAT_V2 else 1,
        "verification": verification,
        "workspace_uri": plan["workspace_uri"],
    }
    return validate_feature_presentation(_seal(body))


def _discover_record_paths(
    state_root: Path,
    collections: tuple[str, ...],
) -> list[tuple[str, Path]]:
    try:
        root_state = state_root.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise DeveloperFeatureError("cannot inspect retained feature state") from exc
    if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
        _fail("feature state root is not an ordinary directory")
    result: list[tuple[str, Path]] = []
    for collection in collections:
        collection_root = state_root / collection
        try:
            collection_state = collection_root.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DeveloperFeatureError(
                "cannot inspect retained feature collection"
            ) from exc
        if stat.S_ISLNK(collection_state.st_mode) or not stat.S_ISDIR(
            collection_state.st_mode
        ):
            _fail("retained feature collection is not an ordinary directory")
        try:
            entries = sorted(os.scandir(collection_root), key=lambda row: row.name)
        except OSError as exc:
            raise DeveloperFeatureError(
                "cannot enumerate retained feature collection"
            ) from exc
        if len(result) + len(entries) > _MAX_DISCOVERED_RECORDS:
            _fail("retained feature discovery exceeds its record bound")
        for entry in entries:
            try:
                entry_state = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise DeveloperFeatureError(
                    "cannot inspect retained feature identity"
                ) from exc
            if (
                _DIGEST.fullmatch(entry.name) is None
                or stat.S_ISLNK(entry_state.st_mode)
                or not stat.S_ISDIR(entry_state.st_mode)
            ):
                _fail("retained feature identity directory is invalid")
            record_path = Path(entry.path) / "record.json"
            try:
                record_state = record_path.lstat()
            except OSError as exc:
                raise DeveloperFeatureError(
                    "retained feature identity has no readable record"
                ) from exc
            if stat.S_ISLNK(record_state.st_mode) or not stat.S_ISREG(
                record_state.st_mode
            ):
                _fail("retained feature record is not an ordinary file")
            result.append((collection, record_path))
    return result


def discover_feature_records(
    suite_root: Path | str,
    state_root: Path | str,
    *,
    family: str | None = None,
    collection: str | None = None,
) -> dict[str, Any]:
    """Discover and validate retained owner records for native IDE navigation."""

    if family is not None and family not in FAMILIES:
        _fail("developer feature record family filter is unsupported")
    if collection is not None and collection not in COLLECTIONS:
        _fail("developer feature record collection filter is unsupported")
    suite = Path(suite_root).expanduser().resolve()
    state = Path(os.path.abspath(os.fspath(Path(state_root).expanduser())))
    selected_collections = COLLECTIONS if collection is None else (collection,)
    records: list[dict[str, Any]] = []
    omitted_historical_runtime_records = 0
    for selected_collection, record_path in _discover_record_paths(
        state, selected_collections
    ):
        raw = resolve_feature_record(state, selected_collection, record_path)
        record_kind = raw.get("kind")
        selected_family = _KIND_FAMILIES[selected_collection].get(record_kind)
        record_id = raw.get("id")
        if (
            selected_family is None
            or type(record_id) is not str
            or _CONTENT_ID.fullmatch(record_id) is None
            or record_id.rsplit(":", 1)[1] != record_path.parent.name
        ):
            _fail("retained feature record identity or family is invalid")
        if family is not None and selected_family != family:
            continue
        try:
            presentation = present_feature_record(
                suite,
                state,
                family=selected_family,
                collection=selected_collection,
                reference=record_id,
            )
        except RecipeRuntimeComparisonError as exc:
            if (
                selected_collection == "runs"
                and record_kind == RECIPE_RUNTIME_COMPARISON_KIND
                and str(exc) == _UNAVAILABLE_RECIPE_RUNTIME_MESSAGE
            ):
                omitted_historical_runtime_records += 1
                continue
            raise
        owner = presentation["owner_record"]
        records.append(
            {
                "collection": selected_collection,
                "diagnostic_code": owner["diagnostic_code"],
                "family": selected_family,
                "operation_count": len(presentation["operations"]),
                "plan_id": presentation["plan_id"],
                "record_id": owner["id"],
                "record_kind": owner["kind"],
                "record_state": owner["state"],
                "reference": owner["id"],
                "uri": owner["uri"],
                "verification_state": presentation["verification"]["state"],
                "workspace_uri": presentation["workspace_uri"],
            }
        )
    records.sort(
        key=lambda row: (
            row["family"],
            COLLECTIONS.index(row["collection"]),
            row["record_id"],
        )
    )
    body = {
        "filters": {"collection": collection, "family": family},
        "format": RECORD_CATALOG_FORMAT,
        "kind": RECORD_CATALOG_KIND,
        "limitations": [
            "Discovery covers only records retained in the selected state root.",
            "Every record must be reopened through its owner-validated presentation before action.",
            "A stale verification state does not invalidate the retained before/after bytes.",
            *(
                [
                    f"{omitted_historical_runtime_records} retained recipe runtime record(s) were omitted because their observer protocol is unavailable in this installation."
                ]
                if omitted_historical_runtime_records
                else []
            ),
        ],
        "records": records,
        "schema_version": 1,
        "state_root_uri": state.absolute().as_uri(),
    }
    return validate_feature_record_catalog(
        _seal(body, kind=RECORD_CATALOG_KIND)
    )


def validate_feature_record_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact retained-record discovery envelope."""

    if type(value) is not dict:
        _fail("developer feature record catalog must be one ordinary object")
    result = dict(value)
    body = dict(result)
    supplied = body.pop("id", None)
    fields = {
        "filters",
        "format",
        "id",
        "kind",
        "limitations",
        "records",
        "schema_version",
        "state_root_uri",
    }
    filters = result.get("filters")
    if (
        set(result) != fields
        or result.get("format") != RECORD_CATALOG_FORMAT
        or result.get("kind") != RECORD_CATALOG_KIND
        or result.get("schema_version") != 1
        or type(result.get("schema_version")) is bool
        or type(supplied) is not str
        or supplied != _seal(body, kind=RECORD_CATALOG_KIND)["id"]
        or type(filters) is not dict
        or set(filters) != {"collection", "family"}
        or filters["family"] not in (*FAMILIES, None)
        or filters["collection"] not in (*COLLECTIONS, None)
        or type(result.get("state_root_uri")) is not str
        or not result["state_root_uri"].startswith("file:")
        or type(result.get("limitations")) is not list
        or not all(type(row) is str and row for row in result["limitations"])
        or type(result.get("records")) is not list
        or len(result["records"]) > _MAX_DISCOVERED_RECORDS
    ):
        _fail("developer feature record catalog identity or shape changed")
    record_fields = {
        "collection",
        "diagnostic_code",
        "family",
        "operation_count",
        "plan_id",
        "record_id",
        "record_kind",
        "record_state",
        "reference",
        "uri",
        "verification_state",
        "workspace_uri",
    }
    observed: set[tuple[str, str]] = set()
    for row in result["records"]:
        if (
            type(row) is not dict
            or set(row) != record_fields
            or row.get("family") not in FAMILIES
            or row.get("collection") not in COLLECTIONS
            or type(row.get("record_id")) is not str
            or _CONTENT_ID.fullmatch(row["record_id"]) is None
            or row.get("reference") != row["record_id"]
            or type(row.get("record_kind")) is not str
            or not row["record_kind"]
            or _KIND_FAMILIES[row["collection"]].get(row["record_kind"])
            != row["family"]
            or not row["record_id"].startswith(f"{row['record_kind']}:sha256:")
            or type(row.get("record_state")) is not str
            or not row["record_state"]
            or type(row.get("plan_id")) is not str
            or _CONTENT_ID.fullmatch(row["plan_id"]) is None
            or type(row.get("operation_count")) is not int
            or type(row.get("operation_count")) is bool
            or row["operation_count"] < 1
            or row.get("verification_state") not in {"ready", "stale"}
            or type(row.get("uri")) is not str
            or not row["uri"].startswith("file:")
            or type(row.get("workspace_uri")) is not str
            or not row["workspace_uri"].startswith("file:")
            or row.get("diagnostic_code") is not None
            and type(row.get("diagnostic_code")) is not str
        ):
            _fail("developer feature record catalog row changed")
        if (
            filters["family"] is not None
            and row["family"] != filters["family"]
        ) or (
            filters["collection"] is not None
            and row["collection"] != filters["collection"]
        ):
            _fail("developer feature record catalog filter changed")
        if row["collection"] == "plans" and row["plan_id"] != row["record_id"]:
            _fail("developer feature plan catalog identity changed")
        key = (row["collection"], row["record_id"])
        if key in observed:
            _fail("developer feature record catalog contains a duplicate")
        observed.add(key)
    expected_order = sorted(
        result["records"],
        key=lambda row: (
            row["family"],
            COLLECTIONS.index(row["collection"]),
            row["record_id"],
        ),
    )
    if result["records"] != expected_order:
        _fail("developer feature record catalog order changed")
    return cast(dict[str, Any], result)


def _validate_v2_runtime_projection(value: Any) -> None:
    if type(value) is not dict or set(value) != {
        "action_available",
        "outcome",
        "record_id",
        "requirement",
        "sides",
        "state",
    }:
        _fail("developer feature V2 runtime projection changed")
    sides = value.get("sides")
    if type(sides) is not list or not 1 <= len(sides) <= 16:
        _fail("developer feature V2 runtime sides changed")
    roles: set[str] = set()
    for side in sides:
        if type(side) is not dict or set(side) != {
            "assertion", "error", "outcome", "probe", "receipt", "role", "state"
        }:
            _fail("developer feature V2 runtime side changed")
        role = side.get("role")
        if (
            type(role) is not str
            or not role
            or len(role.encode("utf-8")) > 128
            or role in roles
            or type(side.get("state")) is not str
            or not side["state"]
            or type(side.get("outcome")) is not str
            or not side["outcome"]
        ):
            _fail("developer feature V2 runtime side identity changed")
        roles.add(role)
        assertion = side.get("assertion")
        if assertion is not None and (
            type(assertion) is not dict
            or set(assertion) != {"id", "state"}
            or type(assertion.get("id")) is not str
            or not assertion["id"]
            or type(assertion.get("state")) is not str
            or not assertion["state"]
        ):
            _fail("developer feature V2 runtime assertion changed")
        error = side.get("error")
        if error is not None and (
            type(error) is not dict
            or set(error) != {"kind", "message", "phase"}
            or any(type(error.get(key)) is not str or not error[key] for key in error)
        ):
            _fail("developer feature V2 runtime error changed")
        probe = side.get("probe")
        if probe is not None and (
            type(probe) is not dict
            or set(probe) != {"id", "overlay_id", "overlay_uri", "script_uri"}
            or any(type(probe.get(key)) is not str or not probe[key] for key in probe)
            or not probe["overlay_uri"].startswith("file:")
            or not probe["script_uri"].startswith("file:")
        ):
            _fail("developer feature V2 runtime probe changed")
        receipt = side.get("receipt")
        if receipt is None:
            continue
        if type(receipt) is not dict or set(receipt) != {
            "final_launch", "groovy_log", "runtime_session"
        }:
            _fail("developer feature V2 runtime receipt changed")
        for key in ("final_launch", "runtime_session"):
            reference = receipt.get(key)
            if (
                type(reference) is not dict
                or set(reference) != {"id", "sha256", "size", "uri"}
                or type(reference.get("id")) is not str
                or not reference["id"]
                or type(reference.get("sha256")) is not str
                or _DIGEST.fullmatch(reference["sha256"]) is None
                or type(reference.get("size")) is not int
                or type(reference.get("size")) is bool
                or reference["size"] < 1
                or type(reference.get("uri")) is not str
                or not reference["uri"].startswith("file:")
            ):
                _fail("developer feature V2 retained receipt reference changed")
        log = receipt.get("groovy_log")
        if log is not None and (
            type(log) is not dict
            or set(log) != {"sha256", "size", "uri"}
            or type(log.get("sha256")) is not str
            or _DIGEST.fullmatch(log["sha256"]) is None
            or type(log.get("size")) is not int
            or type(log.get("size")) is bool
            or log["size"] < 0
            or type(log.get("uri")) is not str
            or not log["uri"].startswith("file:")
        ):
            _fail("developer feature V2 retained Groovy reference changed")


def validate_feature_presentation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the common client projection without reopening owner state."""

    if type(value) is not dict:
        _fail("developer feature presentation must be one ordinary object")
    result = dict(value)
    body = dict(result)
    supplied = body.pop("id", None)
    version = result.get("schema_version")
    expected_format = FORMAT_V2 if version == 2 else FORMAT if version == 1 else None
    fields = {
        "actions",
        "authority_boundary",
        "collection",
        "family",
        "format",
        "id",
        "kind",
        "limitations",
        "operations",
        "owner_record",
        "plan_id",
        "request",
        "review",
        "runtime",
        "schema_version",
        "verification",
        "workspace_uri",
    }
    if (
        set(result) != fields
        or result.get("format") != expected_format
        or result.get("kind") != KIND
        or version not in {1, 2}
        or type(result.get("schema_version")) is bool
        or result.get("family") not in FAMILIES
        or result.get("collection") not in COLLECTIONS
        or type(supplied) is not str
        or supplied != _seal(body)["id"]
    ):
        _fail("developer feature presentation identity or shape changed")
    if (
        type(result.get("plan_id")) is not str
        or _CONTENT_ID.fullmatch(result["plan_id"]) is None
        or type(result.get("workspace_uri")) is not str
        or not result["workspace_uri"].startswith("file:")
        or type(result.get("operations")) is not list
        or not result["operations"]
        or type(result.get("actions")) is not list
        or type(result.get("limitations")) is not list
        or type(result.get("request")) is not dict
        or type(result.get("review")) is not dict
        or type(result.get("verification")) is not dict
        or type(result.get("runtime")) is not dict
        or type(result.get("owner_record")) is not dict
    ):
        _fail("developer feature presentation fields changed")
    runtime = cast(dict[str, Any], result["runtime"])
    if version == 2:
        if result.get("collection") != "runs":
            _fail("developer feature V2 is reserved for retained runtime runs")
        _validate_v2_runtime_projection(runtime)
    elif set(runtime) != {
        "action_available", "outcome", "record_id", "requirement", "state"
    }:
        _fail("developer feature V1 runtime projection changed")
    return cast(dict[str, Any], result)


__all__ = [
    "COLLECTIONS",
    "FAMILIES",
    "FORMAT",
    "FORMAT_V2",
    "KIND",
    "RECORD_CATALOG_FORMAT",
    "RECORD_CATALOG_KIND",
    "discover_feature_records",
    "present_feature_record",
    "validate_feature_record_catalog",
    "validate_feature_presentation",
]
