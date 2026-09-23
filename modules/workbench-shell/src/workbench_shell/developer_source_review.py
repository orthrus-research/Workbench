"""IDE/CLI-callable local review of saved developer edits, without Blueprints."""

import argparse
from hashlib import sha256
import json

from workbench_atlas.source_review import build_source_review
from workbench_pack_program_studio.source_intelligence import (
    build_navigation_declarations,
)
from workbench_project_intelligence.source_baseline import capture_baseline
from workbench_project_intelligence.working_tree import capture_source_inputs
from .developer_context import DeveloperContextError, observe_developer_context


def run_local_review(selection, argv):
    parser = argparse.ArgumentParser(prog="context run review local")
    parser.add_argument(
        "--baseline-ref",
        required=True,
        help="explicit local revision, resolved to an exact commit; never fetched",
    )
    parser.add_argument(
        "--expect-review",
        help="reject if the exact previously reviewed inputs or interpretation changed",
    )
    args = parser.parse_args(list(argv))
    operation = observe_developer_context(selection)
    candidate = capture_source_inputs(selection.workspace)
    if candidate.observation != operation.as_dict()["source"]:
        raise DeveloperContextError("source changed before local review")
    baseline = capture_baseline(selection.workspace, args.baseline_ref)
    feeds, errors = {}, {}
    for side, inputs in (("before", baseline), ("after", candidate)):
        try:
            feeds[side] = build_navigation_declarations(
                inputs,
                pack_profile=selection.pack_profile,
                platform_profile=selection.platform_profile,
                variant=selection.variant,
            )
        except (ValueError, OSError) as exc:
            # Syntax errors and unavailable interpretations must not hide the
            # developer's exact file diff or be presented as a clean check.
            feeds[side] = None
            errors[side] = f"{type(exc).__name__}: {exc}"[:4000]
    result = build_source_review(
        baseline,
        candidate,
        before_feed=feeds["before"],
        after_feed=feeds["after"],
        analysis_errors=errors,
    )
    operation.require_fresh()
    result["operation_id"] = operation.id
    result["review_id"] = (
        "source-review:sha256:"
        + sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    if args.expect_review is not None and result["review_id"] != args.expect_review:
        raise DeveloperContextError(
            "local review is stale; review the saved changes again"
        )
    return {
        "format": "workbench-developer-action-v1",
        "operation_id": operation.id,
        "context": operation.as_dict(),
        "exit_code": 0,
        "result": result,
        "owner_record_ref": None,
        "diagnostics": "",
    }
