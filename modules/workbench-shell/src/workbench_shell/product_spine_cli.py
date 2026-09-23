"""User-facing ports for the additive V2 product spine.

This module is intentionally thin.  Home composition, capability truth, and
Work Session durability stay with their owner modules; this file only parses
CLI input, selects those ports, and renders their returned records.
"""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
from datetime import datetime, timezone
import json
from importlib import metadata
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING, Any, Mapping, Sequence, TextIO

if TYPE_CHECKING:
    # These names are populated by the lazy loaders below. Type-only imports
    # keep Home inspection independent of session/process runtime loading.
    from .console_cli import build_command_review
    from .work_session import (
        WorkSessionConflictError, WorkSessionError, WorkSessionStore,
        work_session_recovery_action,
    )
    from workbench_core.render import make_renderer
    from workbench_core.runner import RunnerError, supervise_process
    from workbench_core.sessions import (
        RetainedSession, SessionError, live_console_execution_reference,
        live_console_owner_reference, read_live_console_owner_artifacts,
        resolve_live_console_owner_reference,
    )

from .catalog import build_catalog, redact_argv
from .product_capability_catalog import (
    ProductCapabilityCatalogError,
    load_product_capability_catalog,
)
from workbench_api.state_paths import default_product_spine_state_root
from .workspace_dashboard import (
    WorkspaceHomeV2Error,
    adopt_workspace_home_v2,
    build_workspace_home_v2,
    load_product_capability_owner_port,
    load_workspace_home_adoption,
    render_workspace_home_v2,
    reopen_workspace_home_v2,
    work_session_summary_owner_port,
    workspace_home_adoption_exists,
    workspace_home_binding_id,
)


CLI_FORMAT = "workbench-product-spine-cli-v1"
SESSION_FRONTEND_KINDS = ("cli", "vscode", "intellij-community")
SUITE_ROOT = _repository_resource_root(__file__)


class ProductSpineCliError(RuntimeError):
    """A V2 product-spine request could not be served safely."""


def _load_work_session_runtime() -> None:
    """Load C01 journal custody only for routes that consume a session."""

    if "WorkSessionStore" in globals():
        return
    from .work_session import (
        WorkSessionConflictError as loaded_conflict_error,
    )
    from .work_session import WorkSessionError as loaded_session_error
    from .work_session import WorkSessionStore as loaded_store
    from .work_session import (
        work_session_recovery_action as loaded_recovery_action,
    )

    globals().update(
        {
            "WorkSessionConflictError": loaded_conflict_error,
            "WorkSessionError": loaded_session_error,
            "WorkSessionStore": loaded_store,
            "work_session_recovery_action": loaded_recovery_action,
        }
    )


def _load_console_runtime() -> None:
    """Load process custody only for session routes, never read-only Home."""

    _load_work_session_runtime()
    if "RetainedSession" in globals():
        return
    from .console_cli import build_command_review as loaded_build_command_review
    from workbench_core.render import make_renderer as loaded_make_renderer
    from workbench_core.runner import RunnerError as loaded_runner_error
    from workbench_core.runner import supervise_process as loaded_supervise_process
    from workbench_core.sessions import RetainedSession as loaded_retained_session
    from workbench_core.sessions import SessionError as loaded_session_error
    from workbench_core.sessions import (
        live_console_execution_reference as loaded_live_console_execution_reference,
    )
    from workbench_core.sessions import live_console_owner_reference as loaded_live_console_owner_reference
    from workbench_core.sessions import (
        read_live_console_owner_artifacts as loaded_read_live_console_owner_artifacts,
    )
    from workbench_core.sessions import (
        resolve_live_console_owner_reference as loaded_resolve_live_console_owner_reference,
    )

    globals().update(
        {
            "build_command_review": loaded_build_command_review,
            "make_renderer": loaded_make_renderer,
            "RunnerError": loaded_runner_error,
            "supervise_process": loaded_supervise_process,
            "RetainedSession": loaded_retained_session,
            "SessionError": loaded_session_error,
            "live_console_execution_reference": loaded_live_console_execution_reference,
            "live_console_owner_reference": loaded_live_console_owner_reference,
            "read_live_console_owner_artifacts": loaded_read_live_console_owner_artifacts,
            "resolve_live_console_owner_reference": loaded_resolve_live_console_owner_reference,
        }
    )


def _json(value: Any, output: TextIO) -> None:
    output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_base(root: Path, override: Path | None) -> Path:
    if override is None:
        return default_product_spine_state_root(root)
    supplied = override.expanduser()
    if supplied.is_symlink():
        raise ProductSpineCliError("state root cannot be a symbolic link")
    return supplied.resolve()


def _home_state_root(state_base: Path) -> Path:
    return state_base / ".workbench"


def _frontend(kind: str = "cli") -> dict[str, Any]:
    release_track = os.environ.get("WORKBENCH_RELEASE_TRACK")
    if release_track not in {None, "", "public-v1"}:
        raise ProductSpineCliError(
            f"unsupported release track {release_track!r}; only public-v1 is available"
        )
    version = _product_version()
    return {
        "frontend_id": f"workbench-{kind}",
        "kind": kind,
        "version": version,
        "instance_id": f"{kind}-{os.getpid()}",
        "process_id": os.getpid(),
    }


def _product_version() -> str:
    """Use the admitted native Core distribution, including source metadata."""
    try:
        return metadata.version("workbench-core")
    except metadata.PackageNotFoundError as exc:
        raise ProductSpineCliError("native Workbench Core metadata is unavailable") from exc


def _capability_catalog_port(root: Path):
    try:
        return load_product_capability_owner_port(root), None
    except (OSError, ValueError, ProductCapabilityCatalogError) as exc:
        # Home can still return Project Intelligence facts if the live command
        # catalog itself cannot be composed.
        return None, str(exc)


def _session_port(store: WorkSessionStore, selector: str | None):
    _load_work_session_runtime()
    if selector is None:
        return None, None
    try:
        return work_session_summary_owner_port(store.status(selector)), None
    except (OSError, ValueError, WorkSessionError) as exc:
        return None, str(exc)


def build_home_for_cli(
    root: Path,
    workspace: Path,
    *,
    state_base: Path,
    session_selector: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build Home while isolating an unavailable optional capability catalog."""

    capability_catalog, capability_catalog_error = _capability_catalog_port(root)
    initial_home: dict[str, Any] | None = None
    if session_selector is None:
        try:
            initial_home = build_workspace_home_v2(
                root,
                workspace,
                capability_catalog_record=capability_catalog,
            )
            binding_id = workspace_home_binding_id(
                initial_home["workspace"]["workspace_id"]
            )
            if workspace_home_adoption_exists(
                root,
                binding_id,
                state_root=_home_state_root(state_base),
            ):
                adoption = load_workspace_home_adoption(
                    root,
                    binding_id,
                    state_root=_home_state_root(state_base),
                )
                selected = adoption.get("session_id")
                if isinstance(selected, str):
                    session_selector = selected
        except (OSError, ValueError, WorkspaceHomeV2Error):
            # Optional adoption discovery never hides the read-only Home.
            session_selector = None
    if session_selector is None and initial_home is not None:
        diagnostics = [
            capability_catalog_error
        ] if isinstance(capability_catalog_error, str) and capability_catalog_error else []
        return initial_home, diagnostics
    if session_selector is not None:
        _load_work_session_runtime()
        session, session_error = _session_port(
            WorkSessionStore(state_base), session_selector
        )
    else:
        session, session_error = None, None
    home = build_workspace_home_v2(
        root,
        workspace,
        session_record=session,
        capability_catalog_record=capability_catalog,
    )
    diagnostics = [
        message
        for message in (capability_catalog_error, session_error)
        if isinstance(message, str) and message
    ]
    return home, diagnostics


def _home_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench open",
        description="Open the read-only Workspace Home V2 projection.",
    )
    parser.add_argument("workspace", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--session")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def home_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    args = _home_parser().parse_args(list(argv))
    try:
        state_base = _state_base(root, args.state_root)
        home, diagnostics = build_home_for_cli(
            root,
            args.workspace,
            state_base=state_base,
            session_selector=args.session,
        )
        if args.json:
            # JSON clients require stdout to contain one exact owner record and
            # stderr to remain clean on success. Missing optional C01/T01
            # authority is already represented by unavailable Home jobs and
            # their owner-declared blockers; it is not a transport failure.
            _json(home, output)
        else:
            for diagnostic in diagnostics:
                error.write(
                    "Workbench Home isolated an optional owner record: "
                    f"{diagnostic}\n"
                )
            output.write(render_workspace_home_v2(home, stream=output))
        return 0
    except (OSError, ValueError, ProductSpineCliError, WorkspaceHomeV2Error) as exc:
        error.write(f"Workbench open failed: {exc}\n")
        return 2


def _capabilities_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench capabilities",
        description=(
            "Search capabilities registered by the current Workbench command "
            "catalog."
        ),
    )
    parser.add_argument("query", nargs="?")
    parser.add_argument("--json", action="store_true")
    return parser


def capabilities_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    args = _capabilities_parser().parse_args(list(argv))
    try:
        catalog = load_product_capability_catalog(root)
        rows = catalog["capabilities"]
        if args.query:
            needle = args.query.casefold()
            rows = [
                row
                for row in rows
                if any(
                    needle in str(row[field]).casefold()
                    for field in (
                        "capability_key",
                        "title",
                        "summary",
                        "authority",
                    )
                )
                or needle in row["catalog_action"]["suite_id"].casefold()
            ]
        result = {
            "format": CLI_FORMAT,
            "view": "product-capabilities",
            "catalog_id": catalog["catalog_id"],
            "command_catalog_digest": catalog["command_catalog_digest"],
            "count": len(rows),
            "capabilities": rows,
            "suites": catalog["suites"],
        }
        if args.json:
            _json(result, output)
        else:
            output.write(f"Workbench capabilities — {catalog['catalog_id']}\n")
            if not rows:
                output.write("No matching capabilities.\n")
            for row in rows:
                output.write(
                    f"{row['capability_key']} — "
                    f"{row['availability']} / "
                    f"{row['handler']['kind']} / "
                    f"{row['risk']}\n"
                )
        return 0
    except (OSError, ValueError, ProductCapabilityCatalogError) as exc:
        error.write(f"Workbench capabilities failed: {exc}\n")
        return 2


def _session_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench session",
        description="Open one durable Work Session through the shared C01 port.",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "resume"):
        child = actions.add_parser(name)
        child.add_argument("session_id", nargs="?", default="latest")
        child.add_argument("--json", action="store_true")
        if name == "resume":
            child.add_argument(
                "--workspace",
                type=Path,
                help=(
                    "re-observe an explicitly relocated workspace before "
                    "reopening the retained session"
                ),
            )
            child.add_argument(
                "--expected-sequence",
                type=int,
                help=(
                    "bind this append to a previously observed journal sequence; "
                    "a concurrent writer makes the request fail stale"
                ),
            )
    recover = actions.add_parser("recover")
    recover.add_argument("session_id", nargs="?", default="latest")
    recover.add_argument(
        "--apply",
        action="store_true",
        help="after preview, explicitly reconcile exact current owner custody",
    )
    recover.add_argument("--json", action="store_true")
    timeline = actions.add_parser("timeline")
    timeline.add_argument("session_id")
    timeline.add_argument("--after-sequence", type=int, default=-1)
    timeline.add_argument("--limit", type=int, default=1024)
    timeline.add_argument("--json", action="store_true")
    artifact = actions.add_parser(
        "artifact",
        help="list or read owner-sealed live-console raw event ranges",
    )
    artifact.add_argument("session_id")
    artifact.add_argument("owner_record_id")
    artifact.add_argument("owner_digest")
    artifact.add_argument("event_id", nargs="?")
    artifact.add_argument("--after-sequence", type=int, default=-1)
    artifact.add_argument("--limit", type=int, default=200)
    artifact.add_argument("--json", action="store_true")
    close = actions.add_parser("close")
    close.add_argument("session_id")
    close.add_argument("--json", action="store_true")
    run = actions.add_parser(
        "run",
        help="execute one exact retained catalog action through live-console custody",
    )
    run.add_argument("session_id")
    run.add_argument("action_id")
    run.add_argument(
        "--execute",
        action="store_true",
        help="cross the exact reviewed execution boundary",
    )
    run.add_argument(
        "--expected-action-digest",
        help=(
            "bind execution to the exact action digest previously presented for "
            "review; a changed digest fails before owner allocation"
        ),
    )
    run.add_argument("--json", action="store_true")
    for child in actions.choices.values():
        child.add_argument("--state-root", type=Path)
        child.add_argument(
            "--frontend",
            choices=SESSION_FRONTEND_KINDS,
            default="cli",
            help=(
                "retain the adapter-declared frontend kind as non-authoritative "
                "navigation provenance (default: cli)"
            ),
        )
    return parser


def _process_owner_resolver(owner_ref: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(owner_ref)
    uri = result.get("uri")
    if not isinstance(uri, str) or not uri.startswith("process://"):
        return result
    try:
        process_id = int(uri.removeprefix("process://"))
        os.kill(process_id, 0)
    except (OSError, ValueError):
        state = "dead"
    else:
        state = "running"
        if os.name == "posix":
            try:
                fields = Path(f"/proc/{process_id}/stat").read_text(
                    encoding="utf-8"
                ).split()
                if len(fields) > 2 and fields[2] == "Z":
                    state = "dead"
            except OSError:
                pass
    result["last_verified_state"] = state
    result["verified_at"] = _now()
    return result


def _owner_resolver(
    state_base: Path,
    owner_ref: Mapping[str, Any],
    *,
    root: Path,
    store: WorkSessionStore,
    selector: str,
) -> dict[str, Any]:
    _load_console_runtime()
    if owner_ref.get("record_kind") == "workbench-live-console-session-v1":
        reproduced = live_console_execution_reference(state_base, owner_ref)
        identity = tuple(
            owner_ref.get(key)
            for key in ("owner_id", "record_id", "record_kind", "uri")
        )
        bindings: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        after_sequence = -1
        while True:
            timeline = store.timeline(
                selector,
                after_sequence=after_sequence,
                limit=4096,
            )
            integrity = timeline.get("integrity")
            if (
                not isinstance(integrity, Mapping)
                or integrity.get("journal_state") != "verified"
                or integrity.get("state") == "corrupt"
            ):
                raise ProductSpineCliError(
                    "live-console owner Work Session journal is not verified"
                )
            for event in timeline["events"]:
                if (
                    event["kind"] != "owner-execution-bound"
                    or event.get("action") is None
                ):
                    continue
                for reference in event["owner_record_refs"]:
                    if tuple(
                        reference.get(key)
                        for key in ("owner_id", "record_id", "record_kind", "uri")
                    ) == identity:
                        bindings.append((event, reference))
            if not timeline["has_more"]:
                break
            next_sequence = timeline.get("next_sequence")
            if type(next_sequence) is not int or next_sequence <= after_sequence:
                raise ProductSpineCliError(
                    "live-console owner Work Session timeline did not advance"
                )
            after_sequence = next_sequence
        if not bindings:
            raise ProductSpineCliError(
                "live-console owner has no retained Work Session execution binding"
            )
        binding, bound_reference = bindings[-1]
        action = binding["action"]
        # Reproduce the exact digest retained by the immutable binding event as
        # well as the caller's later digest.  Both must be ancestors of one
        # canonical live-console chain and must reproduce the same immutable
        # prelaunch command.  This preserves historical custody after the
        # workspace moves; the current workspace is relevant to *future*
        # eligibility, not to rewriting the cwd of work already launched.
        bound = live_console_execution_reference(state_base, bound_reference)
        stable_owner_fields = (
            "owner_id",
            "record_id",
            "record_kind",
            "uri",
            "digest",
            "last_verified_state",
        )
        if (
            reproduced["command"] != bound["command"]
            or reproduced["command"].get("command_id") != action["action_id"]
            or any(
                reproduced["owner_record_ref"].get(key)
                != bound["owner_record_ref"].get(key)
                for key in stable_owner_fields
            )
        ):
            raise ProductSpineCliError(
                "live-console owner command differs from retained execution custody"
            )
        return {
            "owner_record_ref": reproduced["owner_record_ref"],
            "prior_digest": owner_ref.get("digest"),
            "transition": "verified-owner-revision",
        }
    uri = owner_ref.get("uri")
    if isinstance(uri, str) and uri.startswith("process://"):
        return _process_owner_resolver(owner_ref)
    raise ProductSpineCliError(
        f"no registered owner resolver for {owner_ref.get('record_id')!r}"
    )


def _workspace_observation(
    root: Path,
    workspace: Path,
) -> dict[str, Any]:
    # Workspace identity is owned by Project Intelligence. Session status and
    # resume remain available without any optional release-planning records.
    home = build_workspace_home_v2(root, workspace)
    observed = home["workspace"]
    return {
        "identity_id": observed["workspace_id"],
        "canonical_root": observed["root"],
        "source_revision": observed["source_revision"],
        "dirty_fingerprint": observed["dirty_fingerprint"],
    }


def _owner_result(
    state_base: Path,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    supplied = candidate.get("owner_record_ref")
    if not isinstance(supplied, Mapping):
        raise ProductSpineCliError("owner result has no live-console reference")
    owner = resolve_live_console_owner_reference(state_base, supplied)
    return {
        "result_id": "workbench-live-console-result:" + owner["digest"],
        "owner_record_ref": owner,
    }


def _read_session_owner_artifact(
    *,
    state_base: Path,
    store: WorkSessionStore,
    session_id: str,
    owner_record_id: str,
    owner_digest: str,
    event_id: str | None,
    after_sequence: int,
    limit: int,
) -> dict[str, Any]:
    """Resolve one Work Session reference through its native owner range port."""

    opened = store.open(session_id)
    if opened["integrity"]["journal_state"] != "verified":
        raise ProductSpineCliError(
            "owner artifact navigation requires a verified Work Session journal"
        )
    matches: list[dict[str, Any]] = []
    for event in opened["events"]:
        for reference in event["owner_record_refs"]:
            if (
                reference["record_id"] == owner_record_id
                and reference["digest"] == owner_digest
            ):
                matches.append(dict(reference))
    identities = {
        json.dumps(value, separators=(",", ":"), sort_keys=True)
        for value in matches
    }
    if len(identities) != 1:
        raise ProductSpineCliError(
            "owner artifact reference was not retained exactly in this Work Session"
        )
    owner_ref = json.loads(next(iter(identities)))
    if owner_ref.get("record_kind") != "workbench-live-console-session-v1":
        raise ProductSpineCliError(
            "owner artifact navigation is unsupported for this owner record kind"
        )
    value = read_live_console_owner_artifacts(
        state_base,
        owner_ref,
        event_id=event_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return {**value, "session_id": opened["summary"]["session_id"]}


def _recovery_action(session_id: str) -> dict[str, Any]:
    return work_session_recovery_action(session_id)


def _run_session_action(
    *,
    root: Path,
    state_base: Path,
    store: WorkSessionStore,
    session_id: str,
    action_id: str,
    output: TextIO,
    error: TextIO,
    json_output: bool,
    frontend: Mapping[str, Any],
    expected_action_digest: str | None = None,
) -> tuple[int, dict[str, Any]]:
    status = store.status(session_id)
    workspace = _workspace_observation(
        root,
        Path(status["workspace"]["canonical_root"]),
    )
    actions = [
        row for row in status["next_actions"] if row["action_id"] == action_id
    ]
    if len(actions) != 1:
        raise ProductSpineCliError("session action is not an exact eligible action")
    action = actions[0]
    catalog = build_catalog(root)
    prepared = store.prepare_catalog_action(
        session_id,
        expected_sequence=status["latest_sequence"],
        catalog=catalog,
        expected_catalog_digest=status["identities"]["catalog_id"],
        action_id=action_id,
        expected_action_digest=(
            action["action_digest"]
            if expected_action_digest is None
            else expected_action_digest
        ),
        arguments=action["arguments"],
        workspace_observation=workspace,
        execute=True,
    )
    command = catalog.command(action_id)
    review = build_command_review(
        catalog,
        command,
        action["arguments"],
        root=root,
    )
    retained = RetainedSession(
        root=state_base,
        command_id=action_id,
        argv=redact_argv(prepared["argv"], command.fields),
        cwd=Path(workspace["canonical_root"]),
        intent=prepared["intent"],
        label=f"Work Session {session_id}",
    )
    owner = live_console_owner_reference(state_base, retained.session_id)
    try:
        bound = store.bind_owner_execution(
            prepared,
            catalog=catalog,
            frontend=frontend,
            owner_record_refs=[owner],
            owner_execution_verifier=lambda row: live_console_execution_reference(
                state_base, row
            ),
        )
    except Exception as exc:
        retained.abort(f"Work Session rejected owner custody before launch: {exc}")
        raise

    child_environment = dict(os.environ)
    console_binding = {
        "WORKBENCH_CONSOLE_CATALOG_DIGEST": review["catalog_digest"],
        "WORKBENCH_CONSOLE_ACTION_DIGEST": review["action_digest"],
        "WORKBENCH_CONSOLE_REVIEW_DIGEST": review["review_digest"],
        "WORKBENCH_CONSOLE_COMMAND_ID": action_id,
    }
    for key in console_binding:
        child_environment.pop(key, None)
    child_environment.update(console_binding)
    renderer = make_renderer(
        "messages",
        input_stream=sys.stdin,
        output=error if json_output else output,
        color="never",
    )
    run_error: RunnerError | None = None
    run_result = None
    try:
        run_result = supervise_process(
            prepared["argv"],
            cwd=Path(workspace["canonical_root"]),
            root=root,
            session=retained,
            renderer=renderer,
            source=action_id,
            environment=child_environment,
        )
    except RunnerError as exc:
        run_error = exc

    current_owner = live_console_owner_reference(state_base, retained.session_id)
    current_state = current_owner["last_verified_state"]
    if current_state in {"complete", "failed", "cancelled"}:
        candidate = {
            "result_id": "workbench-live-console-result:" + current_owner["digest"],
            "owner_record_ref": current_owner,
        }
        try:
            terminal = store.bind_owner_result(
                session_id,
                expected_sequence=bound["summary"]["latest_sequence"],
                frontend=frontend,
                lifecycle=current_state,
                stage_id=action_id,
                result_refs=[candidate],
                owner_result_verifier=lambda row: _owner_result(state_base, row),
                message="Exact live-console owner result was bound after supervised execution.",
            )
        except WorkSessionConflictError:
            # Another frontend may have appended navigation while the owner ran.
            # Retry only through the current retained lineage; bind_owner_result
            # rejects any substituted owner identity.
            latest = store.status(session_id)
            try:
                terminal = store.bind_owner_result(
                    session_id,
                    expected_sequence=latest["latest_sequence"],
                    frontend=frontend,
                    lifecycle=current_state,
                    stage_id=action_id,
                    result_refs=[candidate],
                    owner_result_verifier=lambda row: _owner_result(state_base, row),
                    message=(
                        "Exact live-console owner result was reconciled after a "
                        "cross-frontend navigation append."
                    ),
                )
            except WorkSessionError:
                latest = store.status(session_id)
                terminal = store.mark_owner_recoverable(
                    session_id,
                    expected_sequence=latest["latest_sequence"],
                    frontend=frontend,
                    reason=(
                        "The terminal live-console owner record could not be appended "
                        "after a cross-frontend sequence race."
                    ),
                    owner_record_refs=[current_owner],
                    owner_reference_verifier=lambda row: (
                        resolve_live_console_owner_reference(state_base, row)
                    ),
                    next_actions=[_recovery_action(session_id)],
                )
    else:
        terminal = store.mark_owner_recoverable(
            session_id,
            expected_sequence=bound["summary"]["latest_sequence"],
            frontend=frontend,
            reason="Live-console custody did not publish a terminal owner record.",
            owner_record_refs=[current_owner],
            owner_reference_verifier=lambda row: resolve_live_console_owner_reference(
                state_base, row
            ),
            next_actions=[_recovery_action(session_id)],
        )
    value = {
        "format": "workbench-product-spine-session-run-v1",
        "session_id": session_id,
        "action_id": action_id,
        "review_digest": review["review_digest"],
        "live_console_owner_ref": current_owner,
        "result_id": (
            None
            if current_state not in {"complete", "failed", "cancelled"}
            else "workbench-live-console-result:" + current_owner["digest"]
        ),
        "outcome": None if run_result is None else run_result.outcome,
        "effective_exit_code": (
            2 if run_result is None else run_result.effective_exit_code
        ),
        "work_session": terminal["summary"],
    }
    if run_error is not None:
        raise ProductSpineCliError(
            f"owner execution failed after custody was retained: {run_error}"
        )
    return int(value["effective_exit_code"]), value


def _render_session(value: Mapping[str, Any], output: TextIO) -> None:
    if value.get("format_version") == "workbench-owner-artifact-events-v1":
        output.write(
            f"Owner {value['owner_record_id']} — {len(value['events'])} raw event ranges\n"
        )
        for event in value["events"]:
            output.write(
                f"{event['sequence']:>5} {event['event_id']} {event['stream']} "
                f"[{event['byte_start']},{event['byte_end']}) "
                + json.dumps(event["message"], ensure_ascii=True)
                + "\n"
            )
        return
    if value.get("format_version") == "workbench-owner-artifact-range-v1":
        output.write(
            f"{value['event_id']} {value['stream']} "
            f"[{value['byte_start']},{value['byte_end']}) "
            f"{value['content_sha256']}\n"
        )
        if value["utf8"] is not None:
            output.write(
                "Escaped UTF-8: "
                + json.dumps(value["utf8"], ensure_ascii=True)
                + "\n"
            )
        else:
            output.write("Base64: " + value["content_base64"] + "\n")
        return
    if value.get("format_version") == "workbench-work-session-timeline-v1":
        output.write(
            f"Work Session {value['session_id']} — {len(value['events'])} events\n"
        )
        for event in value["events"]:
            output.write(
                f"{event['sequence']:>5} {event['occurred_at']} "
                f"{event['frontend']['kind']} {event['kind']} {event['lifecycle']}\n"
            )
        return
    if value.get("format_version") == "workbench-work-session-recovery-preview-v1":
        output.write(
            f"Work Session {value['session_id']} recovery: "
            f"{'required' if value['required'] else 'not required'}\n"
        )
        if value.get("reason"):
            output.write(str(value["reason"]) + "\n")
        for action in value.get("safe_actions", []):
            output.write(
                f"Next safe action: {action['action_id']} "
                f"({action['mutation_budget']})\n"
            )
        output.write("Automatic recovery: disabled\n")
        return
    output.write(
        f"Work Session {value['session_id']} — {value['lifecycle']} "
        f"(sequence {value['latest_sequence']}, integrity {value['integrity_state']})\n"
    )


def session_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    _load_console_runtime()
    args = _session_parser().parse_args(list(argv))
    try:
        state_base = _state_base(root, args.state_root)
        store = WorkSessionStore(state_base)
        selector = args.session_id
        frontend = _frontend(args.frontend)
        if args.action == "status":
            value = store.status(selector)
        elif args.action == "timeline":
            value = store.timeline(
                selector,
                after_sequence=args.after_sequence,
                limit=args.limit,
            )
        elif args.action == "artifact":
            value = _read_session_owner_artifact(
                state_base=state_base,
                store=store,
                session_id=selector,
                owner_record_id=args.owner_record_id,
                owner_digest=args.owner_digest,
                event_id=args.event_id,
                after_sequence=args.after_sequence,
                limit=args.limit,
            )
        elif args.action == "recover":
            resolver = lambda row: _owner_resolver(
                state_base,
                row,
                root=root,
                store=store,
                selector=selector,
            )
            value = store.preview_recovery(
                selector,
                owner_reference_resolver=resolver,
            )
            if args.apply:
                if not value["required"]:
                    raise ProductSpineCliError(
                        "recovery is not required; no session state was changed"
                    )
                status = store.status(selector)
                if status["integrity_state"] != "verified":
                    raise ProductSpineCliError(
                        "journal integrity is not verified; recovery cannot append"
                    )
                resolved = []
                for retained in status["owner_record_refs"]:
                    resolution = resolver(retained)
                    if (
                        not isinstance(resolution, Mapping)
                        or resolution.get("transition")
                        != "verified-owner-revision"
                        or not isinstance(resolution.get("owner_record_ref"), Mapping)
                    ):
                        raise ProductSpineCliError(
                            "owner resolver did not verify an exact revision transition"
                        )
                    resolved.append(dict(resolution["owner_record_ref"]))
                if not resolved:
                    raise ProductSpineCliError(
                        "recovery has no exact owner custody to reconcile"
                    )
                states = {row["last_verified_state"] for row in resolved}
                if len(states) == 1 and next(iter(states)) in {
                    "complete",
                    "failed",
                    "cancelled",
                }:
                    lifecycle = next(iter(states))
                    results = [
                        {
                            "result_id": "workbench-live-console-result:"
                            + row["digest"],
                            "owner_record_ref": row,
                        }
                        for row in resolved
                    ]
                    value = store.bind_owner_result(
                        selector,
                        expected_sequence=status["latest_sequence"],
                        frontend=frontend,
                        lifecycle=lifecycle,
                        stage_id="owner-recovery",
                        result_refs=results,
                        owner_result_verifier=lambda row: _owner_result(
                            state_base, row
                        ),
                        message=(
                            "Developer-selected recovery reconciled the exact terminal "
                            "live-console owner revision."
                        ),
                    )["summary"]
                elif states.issubset(
                    {"allocated", "starting", "running", "incomplete"}
                ):
                    value = store.mark_owner_recoverable(
                        selector,
                        expected_sequence=status["latest_sequence"],
                        frontend=frontend,
                        reason=(
                            "Developer selected recovery while owner custody remained "
                            "nonterminal."
                        ),
                        owner_record_refs=resolved,
                        owner_reference_verifier=lambda row: (
                            resolve_live_console_owner_reference(state_base, row)
                        ),
                        next_actions=[_recovery_action(status["session_id"])],
                    )["summary"]
                else:
                    raise ProductSpineCliError(
                        "owner custody states disagree; recovery remains a no-go"
                    )
        elif args.action == "resume":
            status = store.status(selector)
            workspace_path = (
                Path(status["workspace"]["canonical_root"])
                if args.workspace is None
                else args.workspace
            )
            value = store.reopen(
                selector,
                expected_sequence=(
                    status["latest_sequence"]
                    if args.expected_sequence is None
                    else args.expected_sequence
                ),
                frontend=frontend,
                workspace_observation=_workspace_observation(
                    root,
                    workspace_path,
                ),
            )["summary"]
        elif args.action == "close":
            status = store.status(selector)
            value = store.close(
                selector,
                expected_sequence=status["latest_sequence"],
                frontend=frontend,
            )["summary"]
        elif args.action == "run":
            if not args.execute:
                raise ProductSpineCliError(
                    "session run requires --execute after reviewing the exact action"
                )
            code, value = _run_session_action(
                root=root,
                state_base=_state_base(root, args.state_root),
                store=store,
                session_id=selector,
                action_id=args.action_id,
                output=output,
                error=error,
                json_output=args.json,
                frontend=frontend,
                expected_action_digest=args.expected_action_digest,
            )
            if args.json:
                _json(value, output)
            return code
        else:  # pragma: no cover - argparse closes the action set
            raise ProductSpineCliError("unsupported session action")
        if args.json:
            _json(value, output)
        else:
            _render_session(value, output)
        return 0
    except WorkSessionError as exc:
        error.write(f"Workbench session failed [{exc.code}]: {exc}\n")
        return 2
    except (
        OSError,
        ValueError,
        ProductSpineCliError,
        RunnerError,
        SessionError,
        WorkspaceHomeV2Error,
    ) as exc:
        error.write(f"Workbench session failed: {exc}\n")
        return 2


def _session_identities(root: Path, home: Mapping[str, Any]) -> dict[str, str | None]:
    from workbench_core.host_adapter import inspect_local_host_adapter_v3

    catalog = build_catalog(root)
    profile = home["base_home"]["context"]["profile"]
    platform_profile_id = profile.get("platform_profile_id")
    if not isinstance(platform_profile_id, str) or not platform_profile_id:
        platform_profile_id = None
    pack_profile_id = profile.get("profile_family_id")
    if not isinstance(pack_profile_id, str) or not pack_profile_id:
        pack_profile_id = None
    host_adapter = inspect_local_host_adapter_v3()
    return {
        "core_id": f"workbench-core@{_product_version()}",
        "catalog_id": catalog.catalog_digest,
        "host_adapter_id": host_adapter["receipt_id"],
        "platform_profile_id": platform_profile_id,
        "pack_profile_id": pack_profile_id,
    }


def _session_actions(
    home: Mapping[str, Any],
    root: Path,
    *,
    state_base: Path,
) -> list[dict[str, Any]]:
    catalog = build_catalog(root)
    owner_records = {
        row["id"]: row
        for row in home["owner_records"]
        if isinstance(row.get("id"), str) and isinstance(row.get("owner_id"), str)
    }
    result: list[dict[str, Any]] = []
    for job in home["jobs"]:
        command_id = job.get("command_id")
        if (
            not isinstance(command_id, str)
            or not isinstance(job.get("action_digest"), str)
            or not isinstance(job.get("arguments"), Mapping)
        ):
            continue
        command = catalog.command(command_id)
        basis = job.get("availability_basis")
        basis_ref_id = (
            basis.get("owner_ref_id") if isinstance(basis, Mapping) else None
        )
        basis_record = owner_records.get(basis_ref_id)
        if (
            basis_record is None
            or basis.get("command_id") != command_id
            or basis_ref_id not in job.get("owner_ref_ids", [])
        ):
            # Home's exact availability-basis owner is the only authority that
            # may occupy the Work Session action's single owner slot.  Other
            # context/projection refs remain dependencies, never co-owners
            # collapsed by this adapter.
            continue
        owner_id = basis_record["owner_id"]
        arguments = dict(job["arguments"])
        if command_id == "cleanroom.fixture-build":
            arguments["state_root"] = str(state_base / "cleanroom-fixture")
        result.append(
            {
                "action_id": command_id,
                "action_digest": job["action_digest"],
                "owner_id": owner_id,
                "availability": (
                    command.availability
                    if job["state"] == "available"
                    else "unavailable"
                ),
                "mutation_budget": command.risk,
                "arguments": arguments,
            }
        )
    return result


def _adopt_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench adopt",
        description="Create ignored-local Home and Work Session navigation state.",
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def adopt_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    _load_work_session_runtime()
    args = _adopt_parser().parse_args(list(argv))
    try:
        state_base = _state_base(root, args.state_root)
        capability_catalog = load_product_capability_owner_port(root)
        initial = build_workspace_home_v2(
            root,
            args.workspace,
            capability_catalog_record=capability_catalog,
        )
        binding_id = workspace_home_binding_id(
            initial["workspace"]["workspace_id"]
        )
        # Preflight an existing binding before publishing a new Work Session.
        if workspace_home_adoption_exists(
            root,
            binding_id,
            state_root=_home_state_root(state_base),
        ):
            raise ProductSpineCliError(
                f"workspace is already adopted as {binding_id}; use workbench reopen"
            )

        store = WorkSessionStore(state_base)
        workspace = initial["workspace"]
        created = store.create(
            task={
                "task_id": "workbench.workspace-home",
                "owner_id": "workbench-shell",
                "label": "Workspace Home",
            },
            workspace={
                "identity_id": workspace["workspace_id"],
                "canonical_root": workspace["root"],
                "source_revision": workspace["source_revision"],
                "dirty_fingerprint": workspace["dirty_fingerprint"],
            },
            identities=_session_identities(root, initial),
            frontend=_frontend(),
            label=workspace["display_name"],
            lifecycle=(
                "ready" if initial["status"]["state"] == "ready" else "attention"
            ),
            limitations=(
                "Navigation state does not authorize owner actions or release claims.",
            ),
        )
        try:
            projected = store.append(
                created["session_id"],
                expected_sequence=0,
                frontend=_frontend(),
                kind="home-eligibility-projected",
                lifecycle=(
                    "ready" if initial["status"]["state"] == "ready" else "attention"
                ),
                next_actions=_session_actions(
                    initial,
                    root,
                    state_base=state_base,
                ),
                catalog=build_catalog(root),
                message="Home jobs were bound to exact owner, workspace, and capability catalog revisions.",
            )
            session = work_session_summary_owner_port(projected["summary"])
            home = adopt_workspace_home_v2(
                root,
                args.workspace,
                state_root=_home_state_root(state_base),
                session_record=session,
                capability_catalog_record=capability_catalog,
            )
        except Exception as publication_error:
            status = store.status(created["session_id"])
            catalog = build_catalog(root)
            retry_command = catalog.command("workspace.adopt")
            retry_arguments = {
                "workspace": workspace["root"],
                "state_root": str(state_base),
                "json": True,
            }
            retry = {
                "action_id": retry_command.command_id,
                "action_digest": retry_command.action_digest(root=catalog.root),
                "owner_id": "workbench-shell",
                "availability": retry_command.availability,
                "mutation_budget": retry_command.risk,
                "arguments": retry_arguments,
            }
            try:
                store.append(
                    created["session_id"],
                    expected_sequence=status["latest_sequence"],
                    frontend=_frontend(),
                    kind="adoption-publication-failed",
                    lifecycle="attention",
                    problems=[
                        {
                            "problem_id": (
                                "adoption-publication-failed:" + binding_id
                            ),
                            "code": "workbench.adoption-publication-failed",
                            "severity": "error",
                            "message": (
                                "Workspace adoption did not publish after its Work "
                                "Session was created."
                            ),
                            "affected_identity": binding_id,
                            "evidence_refs": [],
                            "next_action_id": retry["action_id"],
                        }
                    ],
                    next_actions=[retry],
                    recovery={
                        "state": "required",
                        "reason": (
                            "Workspace adoption publication was interrupted; retry "
                            "the exact catalog-bound adoption."
                        ),
                        "owner_record_refs": [],
                        "safe_action_ids": [retry["action_id"]],
                    },
                    catalog=catalog,
                    message=(
                        "Interrupted adoption was retained as navigation recovery; "
                        "no owner process custody was claimed."
                    ),
                )
            except Exception as retention_error:
                raise ProductSpineCliError(
                    "workspace adoption publication failed and its exact navigation "
                    f"recovery could not be retained: publication={publication_error}; "
                    f"retention={retention_error}"
                ) from publication_error
            raise publication_error
        if args.json:
            _json(home, output)
        else:
            output.write(render_workspace_home_v2(home, stream=output))
            output.write(f"Session: {home['session']['session_id']}\n")
            output.write(f"Binding: {home['adoption']['binding_id']}\n")
        return 0
    except (
        OSError,
        ValueError,
        ProductCapabilityCatalogError,
        ProductSpineCliError,
        WorkSessionError,
        WorkspaceHomeV2Error,
    ) as exc:
        error.write(f"Workbench adopt failed: {exc}\n")
        return 2


def _reopen_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench reopen",
        description="Reopen one exact ignored-local Home binding.",
    )
    parser.add_argument("binding_id")
    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "explicit relocated checkout to re-observe without rewriting the "
            "retained adoption binding"
        ),
    )
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def reopen_main(
    argv: Sequence[str],
    *,
    root: Path,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    _load_work_session_runtime()
    args = _reopen_parser().parse_args(list(argv))
    try:
        state_base = _state_base(root, args.state_root)
        adoption = load_workspace_home_adoption(
            root,
            args.binding_id,
            state_root=_home_state_root(state_base),
        )
        session_id = adoption.get("session_id")
        session = None
        if session_id is not None:
            store = WorkSessionStore(state_base)
            status = store.status(session_id)
            workspace_path = (
                Path(status["workspace"]["canonical_root"])
                if args.workspace is None
                else args.workspace
            )
            reopened_session = store.reopen(
                session_id,
                expected_sequence=status["latest_sequence"],
                frontend=_frontend(),
                workspace_observation=_workspace_observation(
                    root,
                    workspace_path,
                ),
            )["summary"]
            session = work_session_summary_owner_port(reopened_session)
        home = reopen_workspace_home_v2(
            root,
            args.binding_id,
            state_root=_home_state_root(state_base),
            replacement_workspace_path=args.workspace,
            session_record=session,
            capability_catalog_record=load_product_capability_owner_port(root),
        )
        if args.json:
            _json(home, output)
        else:
            output.write(render_workspace_home_v2(home, stream=output))
        return 0
    except (
        OSError,
        ValueError,
        ProductCapabilityCatalogError,
        ProductSpineCliError,
        WorkSessionError,
        WorkspaceHomeV2Error,
    ) as exc:
        error.write(f"Workbench reopen failed: {exc}\n")
        return 2


__all__ = [
    "CLI_FORMAT",
    "ProductSpineCliError",
    "adopt_main",
    "build_home_for_cli",
    "capabilities_main",
    "home_main",
    "reopen_main",
    "session_main",
]
