"""Public CLI adapter for evidence-bounded diagnosis and capsules."""

from __future__ import annotations

from urllib.request import url2pathname

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence, TextIO
from urllib.parse import urlsplit

from .cleanroom_dev_loop import (
    CleanroomDevLoopError,
    execute_cleanroom_dev_loop,
    find_cleanroom_dev_loop_stage,
    plan_cleanroom_dev_loop,
    recover_cleanroom_dev_loop,
)
from .diagnose_reproduce import (
    DiagnoseReproduceV2Error,
    create_reproduction_capsule,
    diagnose_live_console,
    inspect_reproduction_capsule,
    replay_reproduction_capsule,
)
from workbench_api.state_paths import default_product_spine_state_root
from workbench_core.sessions import (
    SessionError,
    list_sessions,
    live_console_owner_reference,
)
from .work_session import WorkSessionError, WorkSessionStore


LIVE_CONSOLE_RECORD_KIND = "workbench-live-console-session-v1"
_DEV_LOOP_PLAN_ID = re.compile(
    r"workbench-cleanroom-dev-loop-plan:sha256:[0-9a-f]{64}\Z"
)


class DiagnoseReproduceCliV2Error(RuntimeError):
    """The public diagnosis adapter could not resolve an exact owner record."""


def _json(value: Any, output: TextIO) -> None:
    output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _state_base(root: Path, supplied: Path | None) -> Path:
    if supplied is None:
        return default_product_spine_state_root(root)
    candidate = supplied.expanduser()
    if candidate.is_symlink():
        raise DiagnoseReproduceCliV2Error("state root cannot be a symbolic link")
    return candidate.resolve()


def _local_file_uri(value: Any, label: str) -> Path:
    if not isinstance(value, str) or len(value) > 8192:
        raise DiagnoseReproduceCliV2Error(f"{label} is not a bounded file URI")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "file"
        or parsed.netloc not in {"", "localhost"}
        or parsed.query
        or parsed.fragment
    ):
        raise DiagnoseReproduceCliV2Error(f"{label} is not a local file URI")
    path = Path(url2pathname(parsed.path))
    if not path.is_absolute():
        raise DiagnoseReproduceCliV2Error(f"{label} is not an absolute file URI")
    return path


def _diagnosis_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench diagnose",
        description=(
            "Diagnose the newest retained live-console owner, preferring exact "
            "Work Session lineage when present, without inventing a causal "
            "conclusion."
        ),
        epilog=(
            "Capsule routes: workbench diagnose reproduce "
            "{create,inspect,verify,run}"
        ),
    )
    parser.add_argument("target", nargs="?", default="latest")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def _reproduce_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workbench diagnose reproduce")
    subparsers = parser.add_subparsers(dest="action", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("target", nargs="?", default="latest")
    create.add_argument("--state-root", type=Path)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--action-id", required=True)
    create.add_argument("--arguments-json", default="{}")
    create.add_argument(
        "--mutation",
        choices=("read-only", "isolated-target-only"),
        required=True,
    )
    create.add_argument("--approve-privacy", action="store_true")
    create.add_argument("--json", action="store_true")

    for name in ("inspect", "verify", "run"):
        child = subparsers.add_parser(name)
        child.add_argument("capsule", type=Path)
        if name == "run":
            child.add_argument("--state-root", type=Path)
            child.add_argument("--gradle-cmd", type=Path)
            child.add_argument("--java-home", type=Path)
        child.add_argument("--json", action="store_true")
    return parser


def _strict_arguments(raw: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DiagnoseReproduceCliV2Error(
            "--arguments-json must be a JSON object with unique keys"
        ) from exc
    if not isinstance(value, dict):
        raise DiagnoseReproduceCliV2Error("--arguments-json must be a JSON object")
    return value


def _select_work_session_owner(
    state_base: Path,
    selector: str,
) -> tuple[str, dict[str, Any]]:
    store = WorkSessionStore(state_base)
    opened = store.open(selector)
    if opened["integrity"]["journal_state"] != "verified":
        raise DiagnoseReproduceCliV2Error(
            "diagnosis requires a verified Work Session journal"
        )
    for event in reversed(opened["events"]):
        for owner_ref in reversed(event["owner_record_refs"]):
            if owner_ref["record_kind"] == LIVE_CONSOLE_RECORD_KIND:
                return opened["summary"]["session_id"], dict(owner_ref)
    raise DiagnoseReproduceCliV2Error(
        "Work Session has no retained live-console owner record to diagnose"
    )


def _select_diagnosis_owner(
    state_base: Path,
    selector: str,
) -> tuple[str | None, dict[str, Any]]:
    """Prefer Work Session lineage, then use the live-console owner itself.

    D01 stages are already independently retained live-console owners before
    W01 can supply a Work Session.  Falling back only after an exact
    ``work-session.not-found`` keeps ``diagnose latest`` useful for those real
    streams without hiding a corrupt Work Session journal.
    """

    if selector.startswith("live:"):
        session_id = selector.removeprefix("live:")
        if not session_id:
            raise DiagnoseReproduceCliV2Error(
                "live-console diagnosis selector has no exact session ID"
            )
        try:
            return None, live_console_owner_reference(state_base, session_id)
        except SessionError as exc:
            raise DiagnoseReproduceCliV2Error(
                f"retained live-console owner is unavailable: {exc}"
            ) from exc

    try:
        return _select_work_session_owner(state_base, selector)
    except WorkSessionError as exc:
        if exc.code != "work-session.not-found":
            raise
    try:
        if selector == "latest":
            retained = list_sessions(state_base)
            if not retained:
                raise DiagnoseReproduceCliV2Error(
                    "no Work Sessions or retained live-console sessions exist"
                )
            session_id = retained[0].get("session_id")
            if not isinstance(session_id, str):
                raise DiagnoseReproduceCliV2Error(
                    "newest retained live-console owner is unreadable"
                )
        else:
            session_id = selector
        return None, live_console_owner_reference(state_base, session_id)
    except SessionError as exc:
        raise DiagnoseReproduceCliV2Error(
            f"retained live-console owner is unavailable: {exc}"
        ) from exc


def _cleanroom_diagnosis_context(
    state_base: Path,
    owner_ref: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    classifications: list[dict[str, Any]] = []
    next_experiments: list[dict[str, Any]] = []
    lane = state_base / "dev-loop"
    if lane.exists() or lane.is_symlink():
        try:
            joined = find_cleanroom_dev_loop_stage(
                state_base,
                owner_ref["record_id"],
            )
        except CleanroomDevLoopError as exc:
            if str(exc) != "dev-loop owner record has no retained semantic stage":
                raise DiagnoseReproduceCliV2Error(
                    f"Cleanroom Dev Loop owner observation is invalid: {exc}"
                ) from exc
        else:
            receipt = joined["receipt"]
            stage_name = joined["stage"]
            stage = joined["stage_result"]
            receipt_id = receipt["receipt_id"]
            receipt_digest = "sha256:" + receipt_id.rsplit(":", 1)[1]
            required = list(stage.get("required_markers", []))
            observed = list(stage.get("observed_markers", []))
            cleanup = stage.get("cleanup")
            problem = stage.get("problem")
            detail = (
                problem
                if isinstance(problem, str) and problem
                else (
                    "The Cleanroom Dev Loop owner classified the exact "
                    f"{stage_name} stage as {stage['state']}."
                )
            )
            classifications.append(
                {
                    "classification_id": "cleanroom-dev-loop-stage",
                    "claim_state": "observed",
                    "owner_id": "workbench-shell",
                    "owner_record_id": receipt_id,
                    "owner_record_kind": receipt["kind"],
                    "owner_record_uri": receipt["target"]["receipt_uri"],
                    "owner_record_digest": receipt_digest,
                    "stage": stage_name,
                    "state": stage["state"],
                    "required_markers": required,
                    "observed_markers": observed,
                    "effective_exit_code": stage.get("effective_exit_code"),
                    "cleanup_contained": (
                        cleanup.get("contained")
                        if isinstance(cleanup, Mapping)
                        else None
                    ),
                    "artifact_digest": stage.get("artifact_sha256"),
                    "detail": detail,
                }
            )
            recovery = recover_cleanroom_dev_loop(
                _local_file_uri(
                    receipt["target"]["receipt_uri"],
                    "Cleanroom Dev Loop receipt URI",
                )
            )
            action = recovery.get("next_action")
            if isinstance(action, Mapping):
                reconstruction = action["reconstruction_inputs"]
                arguments = {
                    "source_plan_id": action["source_plan_id"],
                    "sides": list(reconstruction["sides"]),
                    "debug": reconstruction["debug"],
                }
                next_experiments.append(
                    {
                        "action_id": action["action_id"],
                        "arguments": arguments,
                        "context_digest": receipt_digest,
                        "mutation": "isolated-target-only",
                    }
                )
    return classifications, next_experiments


def _diagnose_target(
    *, root: Path, state_base: Path, target: str
) -> dict[str, Any]:
    work_session_id, owner_ref = _select_diagnosis_owner(state_base, target)
    classifications, next_experiments = _cleanroom_diagnosis_context(
        state_base,
        owner_ref,
    )
    return diagnose_live_console(
        state_base,
        owner_ref,
        work_session_id=work_session_id,
        owner_classifications=classifications,
        owner_next_experiments=next_experiments,
    )


def _dev_fixture_replay_executor(
    *,
    root: Path,
    state_root: Path,
    gradle_cmd: Path,
    java_home: Path,
    expected_fingerprint: str,
) -> Callable[[dict[str, Any]], Mapping[str, Any]]:
    def execute(action: dict[str, Any]) -> Mapping[str, Any]:
        arguments = action.get("arguments")
        if (
            action.get("action_id") != "dev.fixture-run"
            or action.get("mutation") != "isolated-target-only"
            or not isinstance(arguments, Mapping)
            or set(arguments) != {"source_plan_id", "sides", "debug"}
            or not isinstance(arguments.get("source_plan_id"), str)
            or _DEV_LOOP_PLAN_ID.fullmatch(arguments["source_plan_id"]) is None
            or not isinstance(arguments.get("sides"), list)
            or not arguments["sides"]
            or len(arguments["sides"]) != len(set(arguments["sides"]))
            or any(side not in {"client", "server"} for side in arguments["sides"])
            or arguments.get("debug") is not False
        ):
            raise DiagnoseReproduceCliV2Error(
                "dev.fixture-run capsule action does not carry closed reconstruction inputs"
            )
        try:
            plan = plan_cleanroom_dev_loop(
                root,
                gradle_cmd=gradle_cmd,
                java_home=java_home,
                state_root=state_root,
                sides=tuple(arguments["sides"]),
                debug=False,
            )
            result = execute_cleanroom_dev_loop(root, plan)
        except (CleanroomDevLoopError, OSError, ValueError):
            return {"outcome": "blocked-hydration", "fingerprint": None}
        receipt = result.get("receipt")
        stages = receipt.get("stages") if isinstance(receipt, Mapping) else None
        if not isinstance(stages, Mapping):
            return {"outcome": "incomplete", "fingerprint": None}
        selected: Mapping[str, Any] | None = None
        for stage in ("build", *arguments["sides"]):
            row = stages.get(stage)
            if isinstance(row, Mapping) and row.get("state") == "failed":
                selected = row
                break
        if selected is None:
            for stage in reversed(arguments["sides"]):
                row = stages.get(stage)
                if isinstance(row, Mapping) and isinstance(row.get("owner_ref"), Mapping):
                    selected = row
                    break
        if selected is None or not isinstance(selected.get("owner_ref"), Mapping):
            return {"outcome": "incomplete", "fingerprint": None}
        owner_ref = dict(selected["owner_ref"])
        classifications, next_experiments = _cleanroom_diagnosis_context(
            state_root,
            owner_ref,
        )
        diagnosis = diagnose_live_console(
            state_root,
            owner_ref,
            owner_classifications=classifications,
            owner_next_experiments=next_experiments,
        )
        fingerprint = diagnosis["fingerprint"]
        if result.get("outcome") == "passed":
            outcome = "unexpected-success"
        elif fingerprint == expected_fingerprint:
            outcome = "matching-failure"
        else:
            outcome = "divergent-failure"
        return {"outcome": outcome, "fingerprint": fingerprint}

    return execute


def _render_diagnosis(value: Mapping[str, Any], output: TextIO) -> None:
    output.write(
        f"Diagnosis {value['diagnosis_id']} — {value['outcome']}\n"
        f"Work Session: {value['work_session_id']}\n"
    )
    for row in value["observed_failures"]:
        output.write(f"Observed failure: {row['message']}\n")
    for row in value["unknowns"]:
        output.write(f"Unknown: {row['detail']}\n")
    for row in value["next_experiments"]:
        output.write(
            f"Next safe action: {row['action_id']} ({row['mutation']})\n"
        )


def diagnose_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
    replay_executors: Mapping[
        str, Callable[[dict[str, Any]], Mapping[str, Any]]
    ] | None = None,
) -> int:
    """Serve diagnosis/capsule routes through closed owner ports."""

    arguments = list(argv)
    try:
        if arguments[:1] != ["reproduce"]:
            args = _diagnosis_parser().parse_args(arguments)
            state_base = _state_base(root, args.state_root)
            diagnosis = _diagnose_target(
                root=root,
                state_base=state_base,
                target=args.target,
            )
            if args.json:
                _json(diagnosis, output)
            else:
                _render_diagnosis(diagnosis, output)
            return 0

        args = _reproduce_parser().parse_args(arguments[1:])
        if args.action == "create":
            if not args.approve_privacy:
                raise DiagnoseReproduceCliV2Error(
                    "capsule creation requires --approve-privacy after contents review"
                )
            state_base = _state_base(root, args.state_root)
            diagnosis = _diagnose_target(
                root=root,
                state_base=state_base,
                target=args.target,
            )
            value = create_reproduction_capsule(
                diagnosis,
                args.output,
                replay_action={
                    "action_id": args.action_id,
                    "arguments": _strict_arguments(args.arguments_json),
                    "mutation": args.mutation,
                },
                privacy_review={
                    "approved": True,
                    "excluded": [
                        "credentials",
                        "personal-worlds",
                        "protected-binaries",
                    ],
                },
            )
        elif args.action in {"inspect", "verify"}:
            value = inspect_reproduction_capsule(args.capsule)
        elif args.action == "run":
            inspected = inspect_reproduction_capsule(args.capsule)
            action_id = inspected["replay_action"]["action_id"]
            executor = (replay_executors or {}).get(action_id)
            if executor is None and action_id == "dev.fixture-run":
                if (
                    args.state_root is None
                    or args.gradle_cmd is None
                    or args.java_home is None
                ):
                    raise DiagnoseReproduceCliV2Error(
                        "dev.fixture-run replay requires --state-root, --gradle-cmd, and --java-home"
                    )
                executor = _dev_fixture_replay_executor(
                    root=root,
                    state_root=_state_base(root, args.state_root),
                    gradle_cmd=args.gradle_cmd,
                    java_home=args.java_home,
                    expected_fingerprint=inspected["fingerprint"],
                )
            if executor is None:
                raise DiagnoseReproduceCliV2Error(
                    f"no installed typed replay executor owns {action_id!r}"
                )
            value = replay_reproduction_capsule(
                args.capsule,
                allowed_action_ids={action_id},
                execute=executor,
            )
        else:  # pragma: no cover - argparse keeps this closed.
            raise DiagnoseReproduceCliV2Error("unsupported reproduction action")
        if args.json:
            _json(value, output)
        else:
            output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        return 0
    except BrokenPipeError:
        raise
    except (
        DiagnoseReproduceCliV2Error,
        DiagnoseReproduceV2Error,
        WorkSessionError,
        OSError,
        ValueError,
    ) as exc:
        error.write(f"Workbench diagnose failed: {exc}\n")
        return 2


__all__ = ["DiagnoseReproduceCliV2Error", "diagnose_main"]
