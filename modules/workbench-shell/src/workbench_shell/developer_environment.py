"""Compose selected owner contracts for Core's environment preparation."""

from hashlib import sha256
import json

from workbench_api.profile_extensions import require_profile_extension
from workbench_core import check_storage as storage
from workbench_core import environment_preparation as core
from workbench_project_intelligence.saved_candidate import candidate_manifest
from workbench_project_intelligence.working_tree import (
    capture_source_inputs,
    SourceInputs,
)
from .developer_context import observe_developer_context


def register(actions):
    plan = actions.add_parser("plan-environment")
    for name in ("prism", "java", "packwiz", "accounts"):
        plan.add_argument("--" + name, required=True)
    plan.add_argument("--seed", action="append", default=[])
    for action in (
        "prepare-environment",
        "environment-show",
        "environment-cancel",
        "environment-recover",
    ):
        command = actions.add_parser(action)
        command.add_argument("attempt")
        if action in {"prepare-environment", "environment-recover"}:
            command.add_argument("--confirm", required=True)


def _reference(path, value):
    return {
        "owner_id": "core",
        "record_id": value["id"],
        "record_kind": value["format"],
        "uri": path.as_uri(),
        "digest": "sha256:" + sha256(path.read_bytes()).hexdigest(),
        "last_verified_state": None,
        "verified_at": None,
    }


def run(selection, root, owner, owner_identity, args, cancelled):
    reference = None
    if args.action == "plan-environment":
        operation = observe_developer_context(selection)
        inputs = capture_source_inputs(selection.workspace)
        if inputs.observation != operation.as_dict()["source"]:
            raise ValueError("source changed before environment planning")
        candidate = candidate_manifest(inputs)
        platform = require_profile_extension(
            "workbench.check_platforms", selection.platform_profile
        )
        result = core.plan(
            root,
            source=inputs.sources,
            source_rows=candidate["files"],
            candidate_id=candidate["id"],
            binding=owner.binding(inputs),
            requirements=platform.environment_requirements(),
            dependencies=owner.dependency_requirements(inputs),
            tools={name: getattr(args, name) for name in ("prism", "packwiz", "java")},
            accounts=args.accounts,
            seeds=args.seed,
            context=operation.as_dict(),
            provider=owner_identity,
            excluded_roots=owner.descriptor()["excluded_roots"],
        )
        operation.require_fresh()
        attempt = core.attempt_path(root, result["attempt_id"])
        reference = _reference(attempt / "request.json", result)
    else:
        attempt, request = core.read_request(root, args.attempt)
        if (
            request["workspace_uri"] != selection.pack_uri
            or request["selection_id"] != selection.id
        ):
            raise ValueError(
                "environment attempt belongs to another developer selection"
            )
        if args.action == "prepare-environment":
            operation = observe_developer_context(selection)
            if (
                operation.as_dict() != request["context"]
                or owner_identity != request["provider"]
            ):
                raise ValueError("source or profile changed; plan a fresh environment")
            rows = request["source_files"]
            if storage.tree_manifest(attempt / "source") != rows:
                raise ValueError("retained environment source changed")
            inputs = SourceInputs(
                json.dumps(request["context"]["source"], sort_keys=True),
                tuple(
                    (row["path"], (attempt / "source" / row["path"]).read_bytes())
                    for row in rows
                ),
                tuple((row["path"], 0o100000 | row["mode"]) for row in rows),
            )
            platform = require_profile_extension(
                "workbench.check_platforms", selection.platform_profile
            )
            if (
                platform.environment_requirements() != request["requirements"]
                or owner.binding(inputs) != request["binding"]
                or owner.dependency_requirements(inputs) != request["dependencies"]
            ):
                raise ValueError("environment owner requirements changed")
            operation.require_fresh()
            result = core.execute(
                root,
                args.attempt,
                args.confirm,
                validate_image=lambda runtime, java, arguments: owner.validate_image(
                    inputs, runtime, java, arguments
                ),
                cancelled=cancelled,
            )
            reference = _reference(attempt / "result.json", result)
        elif args.action == "environment-recover":
            result = core.recover(root, args.attempt, args.confirm)
        elif args.action == "environment-cancel":
            if (
                not (attempt / "result.json").exists()
                and not (attempt / "cancel.json").exists()
            ):
                storage.write_json(
                    attempt / "cancel.json", {"request_id": request["id"]}
                )
            result = {
                "format": "workbench-environment-cancellation-v1",
                "state": "requested",
                "attempt_id": args.attempt,
            }
        else:
            result = request
            for filename, kind in (
                ("result.json", "environment-result"),
                ("recovery.json", "environment-recovery"),
            ):
                path = attempt / filename
                if path.exists():
                    value = storage.read_json(path)
                    if (
                        storage.seal(
                            kind, {k: v for k, v in value.items() if k != "id"}
                        )
                        != value
                        or value["request_id"] != request["id"]
                    ):
                        raise ValueError("retained environment record changed")
                    if filename == "result.json":
                        result = value
                        reference = _reference(path, result)
                    else:
                        result = {**result, "recovery": value}
            if result is request and (attempt / "started.json").exists():
                result = {
                    "format": "workbench-environment-status-v1",
                    "attempt_id": args.attempt,
                    "state": "needs-attention",
                    "request": request,
                }
                try:
                    with storage.execution_lock(attempt):
                        pass
                except storage.CheckStorageError:
                    result["state"] = "running"
    return result, reference
