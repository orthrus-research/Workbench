"""Prepare an exact recipe pair without installing or launching a runtime."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from workbench_blueprints.profile_construction import recipe_change_authority
from workbench_blueprints.reviewed_stage import stage_reviewed_feature_plan
from workbench_api.host_filesystem import fsync_directory

from .developer_context import (
    DeveloperContextError,
    DeveloperSelection,
    observe_developer_context,
    require_private_storage,
)
from .developer_feature_runtime import prepare_feature_runtime_attempt_parent
from .developer_recipe_runtime import _observer, _observer_protocol


def prepare_recipe_comparison(
    selection: DeveloperSelection,
    plan: Mapping[str, Any],
    *,
    suite_root: Path,
    state_root: Path,
) -> dict[str, Any]:
    state_root = require_private_storage(selection, state_root)
    operation = observe_developer_context(selection)
    owner = recipe_change_authority(selection.pack_profile)
    reviewed = owner.validate_recipe_change_plan(plan)
    observed = operation.as_dict()["workspace_context"]
    expected = reviewed["profile_context"]
    if (
        reviewed["workspace_uri"] != selection.pack_uri
        or expected["pack_profile_id"] != observed["pack"]["profile_family_id"]
        or expected["pack_selected_profile"] != selection.variant
        or expected["platform_profile_id"] != observed["platform"]["profile_id"]
        or expected["pack_document_sha256"] != observed["pack"]["document_sha256"]
        or expected["platform_document_sha256"]
        != observed["platform"]["document_sha256"]
    ):
        raise DeveloperContextError(
            "reviewed recipe plan belongs to different selected inputs"
        )
    if owner.verify_recipe_change_plan(suite_root, reviewed).get("state") != "ready":
        raise DeveloperContextError("reviewed recipe plan is stale")
    protocol = _observer_protocol(suite_root, _observer(suite_root))
    parent = prepare_feature_runtime_attempt_parent(
        state_root, selection.workspace, lane="recipe-preparation"
    )
    attempt = parent / uuid4().hex
    attempt.mkdir(mode=0o700)
    stages = {}
    for role in ("baseline", "candidate"):
        operation.require_fresh()
        stages[role] = stage_reviewed_feature_plan(
            reviewed,
            attempt / role,
            workspace=selection.workspace,
            verify=lambda: owner.verify_recipe_change_plan(suite_root, reviewed),
            apply_operations=role == "candidate",
            result_format="workbench-recipe-prepared-stage-v1",
        )
    operation.require_fresh()
    if stages["baseline"]["source_tree"] != stages["candidate"]["source_tree"]:
        raise DeveloperContextError("recipe stages do not share exact source inputs")
    body = {
        "format": "workbench-recipe-comparison-preparation-v1",
        "state": "prepared-not-run",
        "operation_id": operation.id,
        "selection_id": selection.id,
        "plan_id": reviewed["id"],
        "workspace_uri": selection.pack_uri,
        "context": operation.as_dict(),
        "observer_protocol": protocol,
        "stages": stages,
        "authority": {
            "runtime_launched": False,
            "source_mutated": False,
            "execution_authorized": False,
        },
        "limitations": [
            "Preparation proves staged bytes, not runtime behavior.",
            "The existing close-observed recipe comparison is client-only; dedicated-server parity remains unproved.",
        ],
    }
    record_id = (
        "recipe-preparation:sha256:"
        + sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    result = {**body, "id": record_id}
    path = attempt / "preparation.json"
    raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)
    reference = {
        "owner_id": "workbench-shell",
        "record_id": record_id,
        "record_kind": body["format"],
        "uri": path.as_uri(),
        "digest": "sha256:" + sha256(raw).hexdigest(),
        "last_verified_state": None,
        "verified_at": None,
    }
    return {"preparation": result, "owner_record_ref": reference}
