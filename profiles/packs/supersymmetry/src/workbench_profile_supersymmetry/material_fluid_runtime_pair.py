"""Profile-owned runtime pairs for one Supersymmetry material-fluid plan.

The Shell owns feature-change ordering.  This module owns the exact staged
Packwiz roles and the physical client/server observations used by each pair.
It deliberately does not reuse the frozen Stage-5 case authority or the SUSY
mod-candidate server owner: both describe different runtime subjects.
"""

from __future__ import annotations

from urllib.request import url2pathname

PROFILE_API_VERSION = 1

from abc import ABC, abstractmethod
import base64
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, NoReturn, Sequence
from urllib.parse import urlparse

from workbench_blueprints.reviewed_plan import ReviewedPlanPorts
from workbench_api.profile_extensions import require_profile_extension
from workbench_api.resources import repository_root
from workbench_blueprints.reviewed_stage import stage_reviewed_feature_plan
from workbench_crucible.runtime_pair import (
    RuntimeExecutionServices,
    FeatureRuntimePairPorts,
    PAIR_REQUEST_FORMAT,
    PAIR_RESULT_FORMAT,
)


STAGE_FORMAT = "workbench-supersymmetry-staged-material-fluid-pack-v1"
EXECUTION_REQUEST_FORMAT = "workbench-supersymmetry-staged-pack-execution-request-v1"
EXECUTION_RESULT_FORMAT = "workbench-supersymmetry-staged-pack-execution-v1"
OBSERVATION_FORMAT = "workbench-supersymmetry-material-fluid-runtime-observation-v1"
PAIR_OWNER_FORMAT = "workbench-supersymmetry-material-fluid-runtime-pair-owner-v1"
INSTALLED_RUNTIME_CONFIG_FORMAT = (
    "workbench-supersymmetry-installed-material-fluid-runtime-config-v1"
)
STAGE_ID_PREFIX = "workbench-supersymmetry-staged-material-fluid-pack:sha256:"
EXECUTION_REQUEST_ID_PREFIX = (
    "workbench-supersymmetry-staged-pack-execution-request:sha256:"
)
EXECUTION_ID_PREFIX = "workbench-supersymmetry-staged-pack-execution:sha256:"
OBSERVATION_ID_PREFIX = (
    "workbench-supersymmetry-material-fluid-runtime-observation:sha256:"
)
PAIR_OWNER_ID_PREFIX = (
    "workbench-supersymmetry-material-fluid-runtime-pair-owner:sha256:"
)
MARKER_PREFIX = "[WORKBENCH-MATERIAL-FLUID-RECIPE-V1]"

_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_CONTENT_ID = re.compile(r"[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}\Z")
_MAX_OWNER_BYTES = 64 * 1024 * 1024
_ACTUAL_NAMES = frozenset(
    {
        "chanced_fluid_output_count",
        "chanced_item_output_count",
        "color_rgb",
        "duration",
        "eut",
        "exact_recipe_match_count",
        "find_recipe_identity",
        "fluid_input_count",
        "fluid_name",
        "fluid_output_count",
        "forge_registry_roundtrip",
        "groovy_recipe",
        "groovy_target_count",
        "has_flammable_flag",
        "has_fluid_property",
        "input_amount",
        "input_fluid",
        "item_input_count",
        "item_output_count",
        "localized_name",
        "manager_phase",
        "material_id",
        "material_resource",
        "output_amount",
        "output_fluid",
        "recipe_map_alias_identity",
        "recipe_map_alias_registry_name",
        "recipe_map_registry_name",
    }
)
_REQUIRED_ASSERTIONS = {
    "client": (
        "material_registration",
        "fluid_registration",
        "recipe_registration",
        "unification_identity",
        "localization",
        "forbidden_delta_absent",
    ),
    "server": (
        "material_registration",
        "fluid_registration",
        "recipe_registration",
        "unification_identity",
        "dedicated_server_safe",
        "forbidden_delta_absent",
    ),
}
_EXPECTED_COMPARISON = {
    "aa": "stable",
    "ab": "observed-change",
    "post-apply": "matches-candidate",
    "post-rollback": "matches-baseline",
}
_EXPECTED_ROLES = {
    ("aa", "baseline-first"): ("baseline", "baseline"),
    ("ab", "baseline-first"): ("baseline", "candidate"),
    ("ab", "candidate-first"): ("candidate", "baseline"),
    ("post-apply", "baseline-first"): ("candidate", "candidate"),
    ("post-rollback", "baseline-first"): ("baseline", "baseline"),
}
_VOLTAGE_EUT = {
    "ULV": 7,
    "LV": 30,
    "MV": 120,
    "HV": 480,
    "EV": 1920,
    "IV": 7680,
    "LuV": 30720,
    "ZPM": 122880,
    "UV": 491520,
    "UHV": 1966080,
    "UEV": 7864320,
    "UIV": 31457280,
}


class MaterialFluidRuntimePairError(ValueError):
    """The exact profile-owned runtime pair could not be retained safely."""


def _fail(message: str) -> NoReturn:
    raise MaterialFluidRuntimePairError(message)


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
        raise MaterialFluidRuntimePairError(
            "runtime-pair value is not canonical JSON data"
        ) from exc


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + sha256(_canonical(value)).hexdigest()


def _decoded(operation: Mapping[str, Any], prefix: str) -> bytes:
    try:
        raw = base64.b64decode(operation[f"{prefix}_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise MaterialFluidRuntimePairError(
            f"feature {prefix} bytes are malformed"
        ) from exc
    if len(raw) != operation.get(f"{prefix}_size") or sha256(
        raw
    ).hexdigest() != operation.get(f"{prefix}_sha256"):
        _fail(f"feature {prefix} byte identity changed")
    return raw


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} must be a portable relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail(f"{label} must be a normalized relative path")
    return relative


def _regular_bytes(root: Path, relative_value: Any, label: str) -> bytes:
    relative = _safe_relative(relative_value, label)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            state = current.lstat()
        except OSError as exc:
            raise MaterialFluidRuntimePairError(f"cannot read {label}") from exc
        if stat.S_ISLNK(state.st_mode):
            _fail(f"{label} traverses a symbolic link")
    if not stat.S_ISREG(current.lstat().st_mode):
        _fail(f"{label} is not a regular file")
    try:
        raw = current.read_bytes()
    except OSError as exc:
        raise MaterialFluidRuntimePairError(f"cannot read {label}") from exc
    if len(raw) > 4 * 1024 * 1024:
        _fail(f"{label} exceeds its byte bound")
    return raw


def _local_uri(value: Any, label: str) -> Path:
    if type(value) is not str:
        _fail(f"{label} must be one local file URI")
    parsed = urlparse(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        _fail(f"{label} must be one local file URI")
    return Path(url2pathname(parsed.path)).resolve()


def _write_immutable(
    path: Path, value: Mapping[str, Any]
) -> tuple[bytes, dict[str, Any]]:
    raw = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    if len(raw) > _MAX_OWNER_BYTES:
        _fail("runtime-pair owner record exceeds its byte bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise MaterialFluidRuntimePairError(
            f"cannot retain immutable runtime-pair owner: {path}"
        ) from exc
    return raw, {
        "uri": path.resolve().as_uri(),
        "sha256": "sha256:" + sha256(raw).hexdigest(),
        "size": len(raw),
    }


def _validate_owner_ref(value: Any) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value)
        != {
            "owner_id",
            "record_id",
            "record_kind",
            "uri",
            "sha256",
            "size",
            "outcome",
        }
        or any(
            type(value.get(field)) is not str or not value[field]
            for field in ("owner_id", "record_id", "record_kind", "uri")
        )
        or type(value.get("sha256")) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value["sha256"]) is None
        or type(value.get("size")) is not int
        or not 0 <= value["size"] <= _MAX_OWNER_BYTES
        or value.get("outcome") not in {"passed", "failed", "incomplete"}
    ):
        _fail("runtime execution owner reference changed shape")
    path = _local_uri(value["uri"], "runtime execution owner record")
    try:
        state = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise MaterialFluidRuntimePairError(
            "runtime execution owner record is unavailable"
        ) from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or len(raw) != value["size"]
        or "sha256:" + sha256(raw).hexdigest() != value["sha256"]
    ):
        _fail("runtime execution owner record byte identity changed")
    return deepcopy(value)


def _source_state(plan: Mapping[str, Any], workspace: Path) -> str:
    before = True
    after = True
    for operation in plan["operations"]:
        current = _regular_bytes(
            workspace,
            operation["path"],
            "feature operation source",
        )
        before = before and current == _decoded(operation, "before")
        after = after and current == _decoded(operation, "after")
    if before and not after:
        return "before"
    if after and not before:
        return "after"
    _fail("feature source has mixed, unknown, or identity-equal operation bytes")


def _verify_after_source(plan: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    try:
        if _source_state(plan, workspace) != "after":
            raise MaterialFluidRuntimePairError(
                "feature source is not exact after-bytes"
            )
        for dependency in plan["dependencies"]:
            raw = _regular_bytes(
                workspace,
                dependency["path"],
                "feature dependency source",
            )
            if (
                len(raw) != dependency["size"]
                or sha256(raw).hexdigest() != dependency["sha256"]
            ):
                raise MaterialFluidRuntimePairError(
                    "feature dependency changed after application"
                )
    except (OSError, ValueError) as exc:
        return {
            "state": "stale",
            "reason": str(exc),
            "plan_id": plan["id"],
        }
    return {"state": "ready", "reason": None, "plan_id": plan["id"]}


def stage_material_fluid_runtime_role(
    suite_root: Path | str,
    plan: Mapping[str, Any],
    role: str,
    destination: Path | str,
    *,
    construction: ReviewedPlanPorts,
) -> dict[str, Any]:
    """Stage exact baseline/candidate bytes from an exact before/after source."""

    require_profile_extension("workbench.runtime_pairs", "supersymmetry")
    suite = Path(suite_root).resolve()
    reviewed = construction.validate(plan)
    if role not in {"baseline", "candidate"}:
        _fail("staged material-fluid role must be baseline or candidate")
    workspace = construction.workspace(reviewed)
    current = _source_state(reviewed, workspace)
    if role == "baseline" and current == "after":
        _fail("baseline cannot be reconstructed from an after-byte source")

    selected = deepcopy(reviewed)
    apply_operations = role == "candidate" and current == "before"
    if current == "after":
        for operation in selected["operations"]:
            for suffix in ("base64", "sha256", "size"):
                operation[f"before_{suffix}"] = operation[f"after_{suffix}"]
        verify = lambda: _verify_after_source(reviewed, workspace)
    else:
        verify = lambda: construction.verify(suite, reviewed)
    generic = stage_reviewed_feature_plan(
        selected,
        destination,
        workspace=workspace,
        verify=verify,
        apply_operations=apply_operations,
        result_format=("workbench-supersymmetry-generic-material-fluid-stage-v1"),
    )
    expected_prefix = "before" if role == "baseline" else "after"
    expected_outputs = [
        {
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "role": operation["role"],
            "sha256": operation[f"{expected_prefix}_sha256"],
            "size": operation[f"{expected_prefix}_size"],
        }
        for operation in reviewed["operations"]
    ]
    if generic.get("outputs") != expected_outputs:
        _fail("staged Packwiz role differs from the feature plan")
    body = {
        "format": STAGE_FORMAT,
        "schema_version": 1,
        "plan_id": reviewed["id"],
        "role": role,
        "source_operation_state": current,
        "selected_operation_state": expected_prefix,
        "source_workspace_uri": workspace.as_uri(),
        "workspace_uri": generic["workspace_uri"],
        "revision": generic["revision"],
        "tracked_tree_id": generic["tracked_tree_id"],
        "outputs": expected_outputs,
        "generic_stage": generic,
        "authority": {
            "owner": "Supersymmetry profile runtime owner",
            "claim": "exact role-specific staged Packwiz source",
            "construction_authority": "reviewed material-fluid feature plan",
        },
        "limitations": [
            "The stage is a disposable Git-tracked copy and grants no support or release authority.",
            "An after-byte source may reconstruct only the candidate role; baseline bytes are never inferred.",
        ],
    }
    return {**body, "stage_id": _content_id(STAGE_ID_PREFIX, body)}


class StagedPackRuntimeExecutionPorts(ABC):
    """Physical owner for one staged Packwiz role on one runtime side."""

    @abstractmethod
    def execute(
        self,
        request: dict[str, Any],
        *,
        suite_root: Path,
        plan: Mapping[str, Any],
        stage: Mapping[str, Any],
        execution_root: Path,
    ) -> Mapping[str, Any]:
        raise NotImplementedError


def _validated_pair_request(
    value: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("feature runtime pair request must be one ordinary object")
    request = deepcopy(value)
    body = dict(request)
    supplied = body.pop("request_id", None)
    expected_roles = _EXPECTED_ROLES.get(
        (request.get("comparison"), request.get("order"))
    )
    if (
        set(request)
        != {
            "format",
            "schema_version",
            "change_id",
            "plan_id",
            "comparison",
            "side",
            "order",
            "attempt_id",
            "roles",
            "required_assertions",
            "request_id",
        }
        or request.get("format") != PAIR_REQUEST_FORMAT
        or request.get("schema_version") != 1
        or request.get("plan_id") != plan["id"]
        or request.get("side") not in _REQUIRED_ASSERTIONS
        or expected_roles is None
        or tuple(request.get("roles", ())) != expected_roles
        or tuple(request.get("required_assertions", ()))
        != _REQUIRED_ASSERTIONS[request["side"]]
        or supplied
        != _content_id("workbench-feature-runtime-pair-request:sha256:", body)
    ):
        _fail("feature runtime pair request identity changed")
    return request


def _execution_request(
    pair: Mapping[str, Any], stage: Mapping[str, Any], ordinal: int
) -> dict[str, Any]:
    body = {
        "format": EXECUTION_REQUEST_FORMAT,
        "schema_version": 1,
        "pair_request_id": pair["request_id"],
        "plan_id": pair["plan_id"],
        "stage_id": stage["stage_id"],
        "ordinal": ordinal,
        "role": stage["role"],
        "side": pair["side"],
        "required_assertions": list(pair["required_assertions"]),
    }
    return {
        **body,
        "request_id": _content_id(EXECUTION_REQUEST_ID_PREFIX, body),
    }


def _validate_observation(
    value: Any,
    *,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("staged Packwiz execution lacks one typed observation")
    observation = deepcopy(value)
    body = dict(observation)
    supplied = body.pop("observation_id", None)
    if (
        set(observation)
        != {
            "format",
            "schema_version",
            "observation_id",
            "plan_id",
            "role",
            "side",
            "state",
            "semantic_fingerprint",
            "assertions",
            "observed",
            "source_refs",
            "limitations",
        }
        or observation.get("format") != OBSERVATION_FORMAT
        or observation.get("schema_version") != 1
        or observation.get("plan_id") != plan["id"]
        or observation.get("role") != request["role"]
        or observation.get("side") != request["side"]
        or observation.get("state") not in {"observed", "failed", "incomplete"}
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(observation.get("semantic_fingerprint")),
        )
        is None
        or type(observation.get("assertions")) is not dict
        or set(observation["assertions"]) != set(request["required_assertions"])
        or any(
            state not in {"observed", "failed", "unavailable"}
            for state in observation["assertions"].values()
        )
        or type(observation.get("observed")) is not dict
        or type(observation.get("source_refs")) is not list
        or type(observation.get("limitations")) is not list
        or not observation["limitations"]
        or supplied != _content_id(OBSERVATION_ID_PREFIX, body)
    ):
        _fail("staged Packwiz observation identity or shape changed")
    observation["source_refs"] = [
        _validate_owner_ref(row) for row in observation["source_refs"]
    ]
    return observation


def validate_staged_pack_execution(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    stage: Mapping[str, Any],
) -> dict[str, Any]:
    """Reopen one physical execution result without reinterpreting its data."""

    if type(value) is not dict:
        _fail("staged Packwiz execution result must be one ordinary object")
    result = deepcopy(value)
    body = dict(result)
    supplied = body.pop("execution_id", None)
    if (
        set(result)
        != {
            "format",
            "schema_version",
            "execution_id",
            "request_id",
            "plan_id",
            "stage_id",
            "role",
            "side",
            "state",
            "outcome",
            "observation",
            "cleanup",
            "owner_refs",
            "limitations",
        }
        or result.get("format") != EXECUTION_RESULT_FORMAT
        or result.get("schema_version") != 1
        or result.get("request_id") != request["request_id"]
        or result.get("plan_id") != plan["id"]
        or result.get("stage_id") != stage["stage_id"]
        or result.get("role") != request["role"] != stage["role"]
        or result.get("side") != request["side"]
        or result.get("state") not in {"complete", "failed", "incomplete"}
        or result.get("outcome") not in {"passed", "failed", "incomplete"}
        or type(result.get("cleanup")) is not dict
        or set(result["cleanup"]) != {"contained", "owned_processes_running"}
        or any(
            type(result["cleanup"].get(key)) is not bool for key in result["cleanup"]
        )
        or type(result.get("owner_refs")) is not list
        or type(result.get("limitations")) is not list
        or not result["limitations"]
        or supplied != _content_id(EXECUTION_ID_PREFIX, body)
    ):
        _fail("staged Packwiz execution identity or shape changed")
    result["observation"] = _validate_observation(
        result["observation"], request=request, plan=plan
    )
    result["owner_refs"] = [_validate_owner_ref(row) for row in result["owner_refs"]]
    passed = (
        result["state"] == "complete"
        and result["outcome"] == "passed"
        and result["cleanup"] == {"contained": True, "owned_processes_running": False}
        and result["observation"]["state"] == "observed"
        and all(
            state == "observed"
            for state in result["observation"]["assertions"].values()
        )
    )
    if passed != (result["outcome"] == "passed"):
        _fail("staged Packwiz execution outcome contradicts its custody")
    return result


def _validate_staged_role(
    value: Any,
    *,
    plan: Mapping[str, Any],
    role: str,
    source_operation_state: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("runtime pair stage must be one ordinary object")
    stage = deepcopy(value)
    body = dict(stage)
    supplied = body.pop("stage_id", None)
    selected_state = "before" if role == "baseline" else "after"
    expected_outputs = [
        {
            "ordinal": operation["ordinal"],
            "path": operation["path"],
            "role": operation["role"],
            "sha256": operation[f"{selected_state}_sha256"],
            "size": operation[f"{selected_state}_size"],
        }
        for operation in plan["operations"]
    ]
    generic = stage.get("generic_stage")
    if (
        set(stage)
        != {
            "format",
            "schema_version",
            "stage_id",
            "plan_id",
            "role",
            "source_operation_state",
            "selected_operation_state",
            "source_workspace_uri",
            "workspace_uri",
            "revision",
            "tracked_tree_id",
            "outputs",
            "generic_stage",
            "authority",
            "limitations",
        }
        or stage.get("format") != STAGE_FORMAT
        or stage.get("schema_version") != 1
        or stage.get("plan_id") != plan["id"]
        or stage.get("role") != role
        or stage.get("source_operation_state") != source_operation_state
        or stage.get("selected_operation_state") != selected_state
        or stage.get("outputs") != expected_outputs
        or type(stage.get("revision")) is not str
        or re.fullmatch(r"[0-9a-f]{40}", stage["revision"]) is None
        or type(stage.get("tracked_tree_id")) is not str
        or re.fullmatch(r"[0-9a-f]{40}", stage["tracked_tree_id"]) is None
        or stage.get("authority")
        != {
            "owner": "Supersymmetry profile runtime owner",
            "claim": "exact role-specific staged Packwiz source",
            "construction_authority": "reviewed material-fluid feature plan",
        }
        or type(stage.get("limitations")) is not list
        or not stage["limitations"]
        or supplied != _content_id(STAGE_ID_PREFIX, body)
        or type(generic) is not dict
        or set(generic)
        != {
            "format",
            "schema_version",
            "state",
            "plan_id",
            "source_workspace_uri",
            "workspace_uri",
            "source_tree",
            "untracked_excluded",
            "baseline_revision",
            "revision",
            "tracked_tree_id",
            "outputs",
        }
        or generic.get("format")
        != "workbench-supersymmetry-generic-material-fluid-stage-v1"
        or generic.get("schema_version") != 1
        or generic.get("state") != "staged"
        or generic.get("plan_id") != plan["id"]
        or generic.get("source_workspace_uri") != stage.get("source_workspace_uri")
        or generic.get("workspace_uri") != stage.get("workspace_uri")
        or generic.get("revision") != stage.get("revision")
        or generic.get("tracked_tree_id") != stage.get("tracked_tree_id")
        or generic.get("outputs") != expected_outputs
    ):
        _fail("runtime pair stage identity or semantics changed")
    _local_uri(stage["source_workspace_uri"], "runtime pair stage source")
    _local_uri(stage["workspace_uri"], "runtime pair stage workspace")
    return stage


def validate_material_fluid_runtime_pair_owner(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    construction: ReviewedPlanPorts,
) -> dict[str, Any]:
    """Reopen a retained profile pair and revalidate every staged execution."""

    reviewed = construction.validate(plan)
    expected_request = _validated_pair_request(request, reviewed)
    if type(value) is not dict:
        _fail("material-fluid runtime pair owner must be one ordinary object")
    owner = deepcopy(value)
    body = dict(owner)
    supplied = body.pop("pair_owner_id", None)
    if (
        set(owner)
        != {
            "format",
            "schema_version",
            "pair_owner_id",
            "request",
            "plan_id",
            "source_operation_state",
            "stages",
            "executions",
            "comparison",
            "cleanup",
            "state",
            "outcome",
            "errors",
            "authority",
            "limitations",
        }
        or owner.get("format") != PAIR_OWNER_FORMAT
        or owner.get("schema_version") != 1
        or owner.get("plan_id") != reviewed["id"]
        or owner.get("source_operation_state") not in {"before", "after"}
        or type(owner.get("stages")) is not list
        or not 1 <= len(owner["stages"]) <= 2
        or type(owner.get("executions")) is not list
        or len(owner["executions"]) > len(owner["stages"])
        or type(owner.get("errors")) is not list
        or type(owner.get("limitations")) is not list
        or not owner["limitations"]
        or owner.get("authority")
        != {
            "owner": "Supersymmetry profile runtime owner",
            "claim": "exact staged material-fluid client/server pair",
            "support_authority": "none",
        }
        or supplied != _content_id(PAIR_OWNER_ID_PREFIX, body)
    ):
        _fail("material-fluid runtime pair owner identity or shape changed")
    retained_request = _validated_pair_request(owner["request"], reviewed)
    if retained_request != expected_request:
        _fail("material-fluid runtime pair owner request changed")

    stages = [
        _validate_staged_role(
            row,
            plan=reviewed,
            role=expected_request["roles"][ordinal],
            source_operation_state=owner["source_operation_state"],
        )
        for ordinal, row in enumerate(owner["stages"])
    ]
    executions: list[dict[str, Any]] = []
    for ordinal, raw_execution in enumerate(owner["executions"]):
        execution_request = _execution_request(
            expected_request, stages[ordinal], ordinal
        )
        executions.append(
            validate_staged_pack_execution(
                raw_execution,
                request=execution_request,
                plan=reviewed,
                stage=stages[ordinal],
            )
        )
    errors = owner["errors"]
    if any(
        type(row) is not dict
        or set(row) != {"ordinal", "role", "message"}
        or type(row.get("ordinal")) is not int
        or not 0 <= row["ordinal"] < 2
        or row.get("role") != expected_request["roles"][row["ordinal"]]
        or type(row.get("message")) is not str
        or not row["message"]
        for row in errors
    ):
        _fail("material-fluid runtime pair errors changed shape")

    observed_comparison = _comparison_state(expected_request, executions)
    expected_fingerprints = [
        row["observation"]["semantic_fingerprint"] for row in executions
    ]
    expected_cleanup = {"contained": True, "owned_processes_running": False}
    for execution in executions:
        expected_cleanup["contained"] = (
            expected_cleanup["contained"] and execution["cleanup"]["contained"]
        )
        expected_cleanup["owned_processes_running"] = (
            expected_cleanup["owned_processes_running"]
            or execution["cleanup"]["owned_processes_running"]
        )
    if errors:
        expected_cleanup = {"contained": False, "owned_processes_running": True}
    passed = (
        observed_comparison == _EXPECTED_COMPARISON[expected_request["comparison"]]
        and len(executions) == 2
        and expected_cleanup == {"contained": True, "owned_processes_running": False}
        and all(row["outcome"] == "passed" for row in executions)
    )
    if (
        owner.get("comparison")
        != {
            "expected": _EXPECTED_COMPARISON[expected_request["comparison"]],
            "observed": observed_comparison,
            "fingerprints": expected_fingerprints,
        }
        or owner.get("cleanup") != expected_cleanup
        or owner.get("state") != ("complete" if passed else "failed")
        or owner.get("outcome") != ("passed" if passed else "failed")
        or bool(errors) != (len(executions) < len(stages))
    ):
        _fail("material-fluid runtime pair outcome contradicts retained executions")
    return owner


def _unavailable_observation(
    request: Mapping[str, Any], plan: Mapping[str, Any], message: str
) -> dict[str, Any]:
    body = {
        "format": OBSERVATION_FORMAT,
        "schema_version": 1,
        "plan_id": plan["id"],
        "role": request["role"],
        "side": request["side"],
        "state": "incomplete",
        "semantic_fingerprint": "sha256:" + sha256(b"unavailable").hexdigest(),
        "assertions": {name: "unavailable" for name in request["required_assertions"]},
        "observed": {"error": message[:2000]},
        "source_refs": [],
        "limitations": [
            "The physical execution owner failed before a typed observation was available."
        ],
    }
    return {**body, "observation_id": _content_id(OBSERVATION_ID_PREFIX, body)}


def _comparison_state(
    request: Mapping[str, Any], executions: Sequence[Mapping[str, Any]]
) -> str:
    if len(executions) != 2 or any(
        row.get("outcome") != "passed" for row in executions
    ):
        return "unavailable"
    first = executions[0]["observation"]["semantic_fingerprint"]
    second = executions[1]["observation"]["semantic_fingerprint"]
    if request["comparison"] == "ab":
        return "observed-change" if first != second else "mismatch"
    expected = _EXPECTED_COMPARISON[request["comparison"]]
    return expected if first == second else "mismatch"


class SupersymmetryMaterialFluidRuntimePairPorts(FeatureRuntimePairPorts):
    """Stage and compose exact role runs from one physical execution owner."""

    def __init__(
        self,
        execution_ports: StagedPackRuntimeExecutionPorts,
        *,
        construction: ReviewedPlanPorts,
    ) -> None:
        if not isinstance(construction, ReviewedPlanPorts):
            _fail("runtime pairs require an explicit construction contract")
        self.construction = construction
        if not isinstance(execution_ports, StagedPackRuntimeExecutionPorts):
            _fail("runtime-pair composition requires staged execution ports")
        self.execution_ports = execution_ports

    def run_pair(
        self,
        request: dict[str, Any],
        *,
        suite_root: Path,
        plan: Mapping[str, Any],
        attempt_root: Path,
    ) -> Mapping[str, Any]:
        require_profile_extension("workbench.runtime_pairs", "supersymmetry")
        suite = Path(suite_root).resolve()
        reviewed = self.construction.validate(plan)
        pair = _validated_pair_request(request, reviewed)
        attempt = Path(attempt_root).resolve()
        if attempt.is_symlink() or not attempt.is_dir():
            _fail("runtime pair attempt root is unavailable or unsafe")
        source_state = _source_state(reviewed, self.construction.workspace(reviewed))
        if source_state == "after" and "baseline" in pair["roles"]:
            _fail("baseline roles cannot execute from an after-byte source")

        stages: list[dict[str, Any]] = []
        executions: list[dict[str, Any]] = []
        projected: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        cleanup = {"contained": True, "owned_processes_running": False}
        roles_root = attempt / "roles"
        try:
            roles_root.mkdir(mode=0o700)
        except OSError as exc:
            raise MaterialFluidRuntimePairError(
                "cannot create runtime pair role custody"
            ) from exc

        for ordinal, role in enumerate(pair["roles"]):
            role_root = roles_root / f"{ordinal + 1:02d}-{role}"
            try:
                role_root.mkdir(mode=0o700)
            except OSError as exc:
                raise MaterialFluidRuntimePairError(
                    "cannot create runtime role custody"
                ) from exc
            stage = stage_material_fluid_runtime_role(
                suite,
                reviewed,
                role,
                role_root / "workspace",
                construction=self.construction,
            )
            stages.append(stage)
            _write_immutable(role_root / "stage-v1.json", stage)
            execution_request = _execution_request(pair, stage, ordinal)
            _write_immutable(role_root / "execution-request-v1.json", execution_request)
            execution_root = role_root / "execution"
            execution_root.mkdir(mode=0o700)
            try:
                raw_result = self.execution_ports.execute(
                    deepcopy(execution_request),
                    suite_root=suite,
                    plan=deepcopy(reviewed),
                    stage=deepcopy(stage),
                    execution_root=execution_root,
                )
                execution = validate_staged_pack_execution(
                    raw_result,
                    request=execution_request,
                    plan=reviewed,
                    stage=stage,
                )
                executions.append(execution)
                observation = execution["observation"]
                projected.append(
                    {
                        "role": role,
                        "observation_id": observation["observation_id"],
                        "assertions": deepcopy(observation["assertions"]),
                    }
                )
                cleanup["contained"] = (
                    cleanup["contained"] and execution["cleanup"]["contained"]
                )
                cleanup["owned_processes_running"] = (
                    cleanup["owned_processes_running"]
                    or execution["cleanup"]["owned_processes_running"]
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {str(exc)[:1800]}"
                errors.append({"ordinal": ordinal, "role": role, "message": message})
                unavailable = _unavailable_observation(
                    execution_request, reviewed, message
                )
                projected.append(
                    {
                        "role": role,
                        "observation_id": unavailable["observation_id"],
                        "assertions": deepcopy(unavailable["assertions"]),
                    }
                )
                cleanup = {"contained": False, "owned_processes_running": True}
            if errors:
                break

        while len(projected) < 2:
            ordinal = len(projected)
            role = pair["roles"][ordinal]
            synthetic_request = {
                **_execution_request(pair, stages[-1], ordinal),
                "role": role,
            }
            unavailable = _unavailable_observation(
                synthetic_request,
                reviewed,
                "not run after prior physical execution failure",
            )
            projected.append(
                {
                    "role": role,
                    "observation_id": unavailable["observation_id"],
                    "assertions": deepcopy(unavailable["assertions"]),
                }
            )

        comparison = _comparison_state(pair, executions)
        passed = (
            comparison == _EXPECTED_COMPARISON[pair["comparison"]]
            and len(executions) == 2
            and cleanup == {"contained": True, "owned_processes_running": False}
            and all(row["outcome"] == "passed" for row in executions)
        )
        outcome = "passed" if passed else "failed"
        owner_body = {
            "format": PAIR_OWNER_FORMAT,
            "schema_version": 1,
            "request": pair,
            "plan_id": reviewed["id"],
            "source_operation_state": source_state,
            "stages": stages,
            "executions": executions,
            "comparison": {
                "expected": _EXPECTED_COMPARISON[pair["comparison"]],
                "observed": comparison,
                "fingerprints": [
                    row["observation"]["semantic_fingerprint"] for row in executions
                ],
            },
            "cleanup": cleanup,
            "state": "complete" if passed else "failed",
            "outcome": outcome,
            "errors": errors,
            "authority": {
                "owner": "Supersymmetry profile runtime owner",
                "claim": "exact staged material-fluid client/server pair",
                "support_authority": "none",
            },
            "limitations": [
                "The owner compares exact profile observations and does not grant release admission.",
                "The Cleanroom server seed, when used, supplies bootstrap artifacts only and never feature semantics.",
            ],
        }
        owner = {
            **owner_body,
            "pair_owner_id": _content_id(PAIR_OWNER_ID_PREFIX, owner_body),
        }
        owner_raw, owner_identity = _write_immutable(
            attempt / "supersymmetry-material-fluid-runtime-pair-v1.json", owner
        )
        del owner_raw
        owner_ref = {
            "owner_id": "supersymmetry-material-fluid-runtime-pair",
            "record_id": owner["pair_owner_id"],
            "record_kind": PAIR_OWNER_FORMAT,
            **owner_identity,
            "outcome": outcome,
        }
        return {
            "format": PAIR_RESULT_FORMAT,
            "request_id": pair["request_id"],
            "side": pair["side"],
            "comparison": pair["comparison"],
            "order": pair["order"],
            "state": "complete" if passed else "failed",
            "outcome": outcome,
            "comparison_state": comparison,
            "observations": projected,
            "cleanup": cleanup,
            "owner_refs": [owner_ref],
        }


def _probe_spec(suite_root: Path, plan: Mapping[str, Any]) -> tuple[Any, Any]:
    try:
        from . import material_fluid_recipe_observation as authority

        request = plan["request"]
        spec = authority.MaterialFluidRecipeProbeSpec(
            registry_name=request["registry_name"],
            symbol_name=request["symbol"],
            material_id=request["material_id"],
            color_rgb=int(request["color"].removeprefix("0x"), 16),
            translation=request["translation"],
            recipe_map_alias=request["recipe_map"],
            recipe_map_registry_name=request["recipe_map_registry_name"],
            input_fluid=request["input_fluid"],
            input_amount=request["input_amount"],
            output_amount=request["output_amount"],
            duration=request["duration"],
            voltage_tier=request["voltage_tier"],
            source_plan_id=plan["id"],
        )
    except (ImportError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise MaterialFluidRuntimePairError(
            f"Supersymmetry material-fluid probe authority is unavailable: {exc}"
        ) from exc
    return authority, spec


def _candidate_marker_checks(
    request: Mapping[str, Any], actual: Mapping[str, Any], error_kind: Any
) -> dict[str, bool]:
    expected_eut = _VOLTAGE_EUT[request["voltage_tier"]]
    return {
        "color_rgb": actual.get("color_rgb")
        == int(request["color"].removeprefix("0x"), 16),
        "duration": actual.get("duration") == request["duration"],
        "eut": actual.get("eut") == expected_eut,
        "find_recipe_identity": actual.get("find_recipe_identity") is True,
        "fluid_input": actual.get("fluid_input_count") == 1
        and actual.get("input_fluid") == request["input_fluid"]
        and actual.get("input_amount") == request["input_amount"],
        "fluid_name": actual.get("fluid_name") == request["registry_name"],
        "fluid_output": actual.get("fluid_output_count") == 1
        and actual.get("chanced_fluid_output_count") == 0
        and actual.get("output_fluid") == request["registry_name"]
        and actual.get("output_amount") == request["output_amount"],
        "forge_registry_roundtrip": actual.get("forge_registry_roundtrip") is True,
        "groovy_origin": actual.get("groovy_recipe") is True,
        "has_flammable_flag": actual.get("has_flammable_flag") is True,
        "has_fluid_property": actual.get("has_fluid_property") is True,
        "localized_name": actual.get("localized_name") == request["translation"],
        "manager_frozen": actual.get("manager_phase") == "FROZEN",
        "material_id": actual.get("material_id") == request["material_id"],
        "material_resource": actual.get("material_resource")
        == "susy:" + request["registry_name"],
        "no_item_io": actual.get("item_input_count") == 0
        and actual.get("item_output_count") == 0
        and actual.get("chanced_item_output_count") == 0,
        "probe_execution": error_kind is None,
        "recipe_map_alias_binding": actual.get("recipe_map_alias_identity") is True
        and actual.get("recipe_map_alias_registry_name")
        == request["recipe_map_registry_name"],
        "recipe_map_registry_name": actual.get("recipe_map_registry_name")
        == request["recipe_map_registry_name"],
        "unique_exact_groovy_recipe": actual.get("exact_recipe_match_count") == 1,
    }


def _decode_marker(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > 16 * 1024 * 1024:
        _fail("material-fluid Groovy log is absent or too large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterialFluidRuntimePairError(
            "material-fluid Groovy log is not UTF-8"
        ) from exc
    tokens = []
    for line in text.splitlines():
        index = line.find(MARKER_PREFIX)
        if index >= 0:
            tokens.append(line[index + len(MARKER_PREFIX) :].strip())
    if len(tokens) != 1 or re.fullmatch(r"[A-Za-z0-9_-]+", tokens[0]) is None:
        _fail("Groovy log must contain exactly one canonical material-fluid marker")
    try:
        token = tokens[0]
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialFluidRuntimePairError(
            "material-fluid marker payload is invalid"
        ) from exc
    if not isinstance(value, dict) or _canonical(value) != decoded:
        _fail("material-fluid marker is not canonical JSON")
    return value


def interpret_material_fluid_runtime_marker(
    plan: Mapping[str, Any],
    *,
    role: str,
    side: str,
    groovy_log_bytes: bytes,
    source_refs: Sequence[Mapping[str, Any]],
    dedicated_server_ready: bool = False,
    suite_root: Path | str | None = None,
    construction: ReviewedPlanPorts,
) -> dict[str, Any]:
    """Interpret one dynamic plan-bound marker for a baseline or candidate."""

    reviewed = construction.validate(plan)
    if role not in {"baseline", "candidate"} or side not in _REQUIRED_ASSERTIONS:
        _fail("material-fluid marker role or side is unsupported")
    suite = (
        Path(suite_root).resolve()
        if suite_root is not None
        else repository_root(__file__)
    )
    _authority, spec = _probe_spec(suite, reviewed)
    marker = _decode_marker(groovy_log_bytes)
    actual = marker.get("actual")
    checks = marker.get("checks")
    error_kind = marker.get("error_kind")
    if (
        set(marker)
        != {
            "actual",
            "checks",
            "error_kind",
            "format",
            "probe_id",
            "source_plan_id",
            "stage",
            "state",
        }
        or marker.get("format") != "workbench-material-fluid-recipe-observation-v1"
        or marker.get("probe_id") != spec.probe_id
        or marker.get("source_plan_id") != reviewed["id"]
        or marker.get("stage") != "postInit"
        or marker.get("state") not in {"observed", "mismatch"}
        or not isinstance(actual, dict)
        or set(actual) != _ACTUAL_NAMES
        or not isinstance(checks, dict)
    ):
        _fail("material-fluid marker does not bind the reviewed feature plan")
    marker_request = deepcopy(reviewed["request"])
    if side == "server":
        marker_request["translation"] = (
            "susy.material." + reviewed["request"]["registry_name"]
        )
    candidate_checks = _candidate_marker_checks(marker_request, actual, error_kind)
    if checks != candidate_checks or marker["state"] != (
        "observed" if all(candidate_checks.values()) else "mismatch"
    ):
        _fail("material-fluid marker contradicts its observed state")

    if role == "candidate":
        material_ok = all(
            candidate_checks[name]
            for name in (
                "color_rgb",
                "has_flammable_flag",
                "has_fluid_property",
                "manager_frozen",
                "material_id",
                "material_resource",
                "probe_execution",
            )
        )
        fluid_ok = all(
            candidate_checks[name]
            for name in (
                "fluid_name",
                "forge_registry_roundtrip",
                "has_fluid_property",
                "manager_frozen",
                "probe_execution",
            )
        )
        recipe_ok = all(
            candidate_checks[name]
            for name in (
                "duration",
                "eut",
                "find_recipe_identity",
                "fluid_input",
                "fluid_output",
                "groovy_origin",
                "no_item_io",
                "probe_execution",
                "recipe_map_alias_binding",
                "recipe_map_registry_name",
                "unique_exact_groovy_recipe",
            )
        )
        identity_ok = all(
            candidate_checks[name]
            for name in (
                "material_id",
                "material_resource",
                "fluid_name",
                "forge_registry_roundtrip",
                "probe_execution",
            )
        )
        presentation_ok = candidate_checks["localized_name"]
        forbidden_ok = (
            candidate_checks["unique_exact_groovy_recipe"]
            and candidate_checks["no_item_io"]
            and actual.get("groovy_target_count") == 1
            and error_kind is None
        )
    else:
        material_ok = (
            actual.get("material_id") is None
            and actual.get("material_resource") is None
            and actual.get("color_rgb") is None
            and actual.get("has_flammable_flag") is False
            and actual.get("has_fluid_property") is False
            and actual.get("manager_phase") == "FROZEN"
            and error_kind is None
        )
        fluid_ok = (
            actual.get("fluid_name") is None
            and actual.get("forge_registry_roundtrip") is False
            and material_ok
        )
        recipe_ok = (
            actual.get("groovy_target_count") == 0
            and actual.get("exact_recipe_match_count") == 0
            and actual.get("find_recipe_identity") is False
            and actual.get("recipe_map_alias_identity") is True
            and actual.get("recipe_map_alias_registry_name")
            == reviewed["request"]["recipe_map_registry_name"]
            and actual.get("recipe_map_registry_name")
            == reviewed["request"]["recipe_map_registry_name"]
            and error_kind is None
        )
        identity_ok = material_ok and fluid_ok
        presentation_ok = actual.get("localized_name") is None
        forbidden_ok = recipe_ok

    booleans = {
        "material_registration": material_ok,
        "fluid_registration": fluid_ok,
        "recipe_registration": recipe_ok,
        "unification_identity": identity_ok,
        (
            "localization" if side == "client" else "dedicated_server_safe"
        ): presentation_ok if side == "client" else bool(dedicated_server_ready),
        "forbidden_delta_absent": forbidden_ok,
    }
    assertion_order = _REQUIRED_ASSERTIONS[side]
    assertions = {
        name: "observed" if booleans[name] else "failed" for name in assertion_order
    }
    if not all(booleans.values()):
        _fail(f"{role} material-fluid runtime assertions failed")
    validated_refs = [_validate_owner_ref(dict(row)) for row in source_refs]
    semantic = {
        "plan_id": reviewed["id"],
        "role": role,
        "side": side,
        "actual": actual,
        "assertions": assertions,
    }
    body = {
        "format": OBSERVATION_FORMAT,
        "schema_version": 1,
        "plan_id": reviewed["id"],
        "role": role,
        "side": side,
        "state": "observed",
        "semantic_fingerprint": "sha256:" + sha256(_canonical(semantic)).hexdigest(),
        "assertions": assertions,
        "observed": {
            "actual": deepcopy(actual),
            "candidate_oriented_marker_checks": candidate_checks,
            "producer_marker_state": marker["state"],
            "dedicated_server_ready": dedicated_server_ready
            if side == "server"
            else None,
        },
        "source_refs": validated_refs,
        "limitations": [
            "The observation applies only to this exact staged role and physical side.",
            "A baseline is asserted as exact absence; candidate-oriented marker mismatch is not treated as process failure.",
        ],
    }
    return {**body, "observation_id": _content_id(OBSERVATION_ID_PREFIX, body)}


@dataclass(frozen=True)
class InstalledSupersymmetryRuntimeConfig:
    """Explicit installed tool and seed paths for physical pair execution."""

    client_launcher_executable: Path
    client_launcher_root: Path
    server_seed_receipt: Path
    server_java: Path
    packwiz_installer_jar: Path
    client_launcher_profile: str | None = None
    client_launcher_java: Path | None = None
    client_launcher_java_state: Path | None = None
    client_seed_roots: tuple[Path, ...] = ()
    launcher: str = "prism"
    memory_mib: int = 8192
    client_timeout_seconds: float = 600.0
    client_attach_timeout_seconds: float = 120.0
    client_session_timeout_seconds: float = 900.0
    server_timeout_seconds: float = 900.0
    server_shutdown_timeout_seconds: float = 180.0


def _validate_installed_runtime_config(
    config: InstalledSupersymmetryRuntimeConfig,
) -> InstalledSupersymmetryRuntimeConfig:
    if not isinstance(config, InstalledSupersymmetryRuntimeConfig):
        _fail("installed runtime execution requires exact configuration")
    path_fields = (
        "client_launcher_executable",
        "client_launcher_root",
        "server_seed_receipt",
        "server_java",
        "packwiz_installer_jar",
    )
    optional_paths = ("client_launcher_java", "client_launcher_java_state")
    if any(not isinstance(getattr(config, key), Path) for key in path_fields):
        _fail("installed runtime configuration paths must be pathlib Paths")
    if any(
        value is not None and not isinstance(value, Path)
        for value in (getattr(config, key) for key in optional_paths)
    ):
        _fail("optional installed runtime paths must be pathlib Paths or null")
    if (
        type(config.client_seed_roots) is not tuple
        or any(not isinstance(path, Path) for path in config.client_seed_roots)
        or len(set(config.client_seed_roots)) != len(config.client_seed_roots)
    ):
        _fail("client seed roots must be one duplicate-free tuple of Paths")
    if config.launcher not in {"prism", "multimc"}:
        _fail("installed runtime launcher must be prism or multimc")
    if config.client_launcher_profile is not None and (
        type(config.client_launcher_profile) is not str
        or not config.client_launcher_profile.strip()
        or any(ord(character) < 32 for character in config.client_launcher_profile)
    ):
        _fail("installed launcher profile must be a non-empty text value or null")
    if type(config.memory_mib) is not int or not 1024 <= config.memory_mib <= 131072:
        _fail("installed runtime memory_mib must be between 1024 and 131072")
    for key in (
        "client_timeout_seconds",
        "client_attach_timeout_seconds",
        "client_session_timeout_seconds",
        "server_timeout_seconds",
        "server_shutdown_timeout_seconds",
    ):
        value = getattr(config, key)
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            _fail(f"installed runtime {key} must be one positive finite number")
    return config


def _config_local_uri(value: Any, label: str, *, nullable: bool = False) -> Path | None:
    if nullable and value is None:
        return None
    return _local_uri(value, label)


def load_installed_supersymmetry_runtime_config(
    config_path: Path | str,
) -> InstalledSupersymmetryRuntimeConfig:
    """Load the exact URI-only installed runtime configuration contract."""

    selected = Path(config_path).expanduser()
    try:
        state = selected.lstat()
        raw = selected.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterialFluidRuntimePairError(
            "installed runtime configuration is unavailable or invalid"
        ) from exc
    if (
        stat.S_ISLNK(state.st_mode)
        or not stat.S_ISREG(state.st_mode)
        or len(raw) > 1024 * 1024
        or type(value) is not dict
        or set(value) != {"client", "format", "memory_mib", "schema_version", "server"}
        or value.get("format") != INSTALLED_RUNTIME_CONFIG_FORMAT
        or value.get("schema_version") != 1
    ):
        _fail("installed runtime configuration shape or identity changed")
    client = value.get("client")
    server = value.get("server")
    client_fields = {
        "attach_timeout_seconds",
        "launcher",
        "launcher_executable_uri",
        "launcher_java_state_uri",
        "launcher_java_uri",
        "launcher_profile",
        "launcher_root_uri",
        "seed_root_uris",
        "session_timeout_seconds",
        "timeout_seconds",
    }
    server_fields = {
        "java_uri",
        "packwiz_installer_jar_uri",
        "seed_receipt_uri",
        "shutdown_timeout_seconds",
        "timeout_seconds",
    }
    if (
        type(client) is not dict
        or set(client) != client_fields
        or type(server) is not dict
        or set(server) != server_fields
        or type(client.get("seed_root_uris")) is not list
        or not client["seed_root_uris"]
        or any(type(uri) is not str for uri in client["seed_root_uris"])
        or len(set(client["seed_root_uris"])) != len(client["seed_root_uris"])
    ):
        _fail("installed runtime client or server configuration is malformed")
    profile = client.get("launcher_profile")
    if profile is not None and type(profile) is not str:
        _fail("installed launcher profile must be text or null")
    config = InstalledSupersymmetryRuntimeConfig(
        client_launcher_executable=_config_local_uri(
            client["launcher_executable_uri"], "client launcher executable"
        ),
        client_launcher_root=_config_local_uri(
            client["launcher_root_uri"], "client launcher root"
        ),
        client_launcher_profile=profile,
        client_launcher_java=_config_local_uri(
            client["launcher_java_uri"], "client launcher Java", nullable=True
        ),
        client_launcher_java_state=_config_local_uri(
            client["launcher_java_state_uri"],
            "client launcher Java state",
            nullable=True,
        ),
        client_seed_roots=tuple(
            _config_local_uri(uri, "client seed root")
            for uri in client["seed_root_uris"]
        ),
        launcher=client["launcher"],
        memory_mib=value["memory_mib"],
        client_timeout_seconds=client["timeout_seconds"],
        client_attach_timeout_seconds=client["attach_timeout_seconds"],
        client_session_timeout_seconds=client["session_timeout_seconds"],
        server_seed_receipt=_config_local_uri(
            server["seed_receipt_uri"], "server seed receipt"
        ),
        server_java=_config_local_uri(server["java_uri"], "server Java"),
        packwiz_installer_jar=_config_local_uri(
            server["packwiz_installer_jar_uri"], "Packwiz installer JAR"
        ),
        server_timeout_seconds=server["timeout_seconds"],
        server_shutdown_timeout_seconds=server["shutdown_timeout_seconds"],
    )
    return _validate_installed_runtime_config(config)


def runtime_pair_from_config(
    config_path: Path | str,
    *,
    construction: ReviewedPlanPorts,
    services: RuntimeExecutionServices,
) -> SupersymmetryMaterialFluidRuntimePairPorts:
    """Load a V1 installed config and return the concrete runtime-pair port."""

    return runtime_pair(
        load_installed_supersymmetry_runtime_config(config_path),
        construction=construction,
        services=services,
    )


# The concrete adapter is defined below the generic contracts so its many
# physical imports cannot change or weaken pair validation.
class InstalledSupersymmetryStagedPackExecutionPorts(StagedPackRuntimeExecutionPorts):
    """Execute staged roles through installed Prism and a fresh SUSY server."""

    def __init__(
        self,
        config: InstalledSupersymmetryRuntimeConfig,
        *,
        construction: ReviewedPlanPorts,
        services: RuntimeExecutionServices,
    ) -> None:
        self.construction = construction
        self.services = services
        self.config = _validate_installed_runtime_config(config)

    def execute(
        self,
        request: dict[str, Any],
        *,
        suite_root: Path,
        plan: Mapping[str, Any],
        stage: Mapping[str, Any],
        execution_root: Path,
    ) -> Mapping[str, Any]:
        if request.get("side") == "client":
            return _execute_installed_client(
                config=self.config,
                services=self.services,
                construction=self.construction,
                request=request,
                suite_root=suite_root,
                plan=plan,
                stage=stage,
                execution_root=execution_root,
            )
        if request.get("side") == "server":
            return _execute_installed_server(
                config=self.config,
                services=self.services,
                construction=self.construction,
                request=request,
                suite_root=suite_root,
                plan=plan,
                stage=stage,
                execution_root=execution_root,
            )
        _fail("installed runtime execution side is unsupported")


def runtime_pair(
    config: InstalledSupersymmetryRuntimeConfig,
    *,
    construction: ReviewedPlanPorts,
    services: RuntimeExecutionServices,
) -> SupersymmetryMaterialFluidRuntimePairPorts:
    """Return the stable F01 pair port backed by installed physical runtimes."""

    return SupersymmetryMaterialFluidRuntimePairPorts(
        InstalledSupersymmetryStagedPackExecutionPorts(
            config, construction=construction, services=services
        ),
        construction=construction,
    )


def _execute_installed_client(**_arguments: Any) -> Mapping[str, Any]:
    from .installed_material_fluid_runtime import execute_installed_client

    return execute_installed_client(**_arguments)


def _execute_installed_server(**_arguments: Any) -> Mapping[str, Any]:
    from .installed_material_fluid_runtime import execute_installed_server

    return execute_installed_server(**_arguments)


__all__ = [
    "EXECUTION_REQUEST_FORMAT",
    "EXECUTION_RESULT_FORMAT",
    "INSTALLED_RUNTIME_CONFIG_FORMAT",
    "InstalledSupersymmetryRuntimeConfig",
    "InstalledSupersymmetryStagedPackExecutionPorts",
    "MaterialFluidRuntimePairError",
    "OBSERVATION_FORMAT",
    "PAIR_OWNER_FORMAT",
    "STAGE_FORMAT",
    "StagedPackRuntimeExecutionPorts",
    "SupersymmetryMaterialFluidRuntimePairPorts",
    "runtime_pair",
    "runtime_pair_from_config",
    "interpret_material_fluid_runtime_marker",
    "load_installed_supersymmetry_runtime_config",
    "stage_material_fluid_runtime_role",
    "validate_material_fluid_runtime_pair_owner",
    "validate_staged_pack_execution",
]
