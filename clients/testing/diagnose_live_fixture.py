#!/usr/bin/env python3

"""Create one retained failed Work Session for IDE diagnosis client tests."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import subprocess
import sys


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: diagnose_live_fixture.py REPOSITORY_ROOT OUTPUT_ROOT")
    repository = Path(sys.argv[1]).resolve(strict=True)
    output = Path(sys.argv[2]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = output / "state"
    workspace = output / "workspace"
    state.mkdir()
    workspace.mkdir()

    runpy.run_path(
        str(repository / "tools" / "workbench.py"),
        run_name="workbench_diagnose_native_fixture",
    )
    from workbench_shell.catalog import build_catalog, redact_argv
    from workbench_api.events import EventNormalizer, RawLocator
    from workbench_core.sessions import (
        RetainedSession,
        live_console_execution_reference,
        live_console_owner_reference,
    )
    from workbench_shell.diagnose_reproduce import diagnose_live_console
    from workbench_shell.work_session import WorkSessionStore

    catalog = build_catalog(repository)
    frontend = {
        "frontend_id": "workbench-native-diagnose-fixture",
        "kind": "cli",
        "version": "test",
    }
    store = WorkSessionStore(state)
    created = store.create(
        task={"task_id": "task:native-diagnose", "owner_id": "workbench-shell"},
        workspace={
            "identity_id": "workspace:native-diagnose",
            "canonical_root": str(workspace),
            "source_revision": "fixture:native-diagnose",
            "dirty_fingerprint": None,
        },
        identities={
            "core_id": "core:native-diagnose",
            "catalog_id": catalog.catalog_digest,
            "host_adapter_id": "host:native",
            "platform_profile_id": "platform:cleanroom",
            "pack_profile_id": None,
        },
        frontend=frontend,
    )
    session_id = created["session_id"]
    command = catalog.command("doctor.inspect")
    action = {
        "action_id": command.command_id,
        "action_digest": command.action_digest(root=catalog.root),
        "owner_id": "project-intelligence",
        "availability": command.availability,
        "mutation_budget": command.risk,
        "arguments": {"workspace": str(workspace)},
    }
    ranked = store.append(
        session_id,
        expected_sequence=0,
        frontend=frontend,
        kind="actions-ranked",
        next_actions=[action],
        catalog=catalog,
    )
    prepared = store.prepare_catalog_action(
        session_id,
        expected_sequence=ranked["summary"]["latest_sequence"],
        catalog=catalog,
        expected_catalog_digest=catalog.catalog_digest,
        action_id=command.command_id,
        expected_action_digest=action["action_digest"],
        arguments=action["arguments"],
        workspace_observation=ranked["summary"]["workspace"],
        execute=True,
    )
    retained = RetainedSession(
        root=state,
        command_id=command.command_id,
        argv=redact_argv(prepared["argv"], command.fields),
        cwd=workspace,
        intent=prepared["intent"],
        label="native diagnosis client fixture",
    )
    historical = live_console_owner_reference(state, retained.session_id)
    store.bind_owner_execution(
        prepared,
        catalog=catalog,
        frontend=frontend,
        owner_record_refs=[historical],
        owner_execution_verifier=lambda row: live_console_execution_reference(state, row),
    )
    message = "Mixin target example.Target was not found — café"
    raw = (message + "\n").encode("utf-8")
    locator = retained.write_raw("stderr", raw)
    event = EventNormalizer(root=workspace).normalize(
        message,
        source=command.command_id,
        stream="stderr",
        raw_locator=RawLocator(
            artifact=locator.path,
            byte_start=locator.byte_start,
            byte_end=locator.byte_end,
            line=1,
            boundary="lf",
        ),
    ).as_dict()
    event["severity"] = "fatal"
    event["outcome_failure"] = True
    retained.record_event(event)
    retained.finish(
        state="failed",
        process_exit_code=None,
        effective_exit_code=1,
        outcome="observed-required-failure",
    )
    receipt_digest = "sha256:" + "d" * 64
    classified_diagnosis = diagnose_live_console(
        state,
        historical,
        work_session_id=session_id,
        owner_classifications=[
            {
                "classification_id": "cleanroom-dev-loop-stage",
                "claim_state": "observed",
                "owner_id": "workbench-shell",
                "owner_record_id": (
                    "workbench-cleanroom-dev-loop-receipt:" + receipt_digest
                ),
                "owner_record_kind": "workbench-cleanroom-dev-loop-receipt",
                "owner_record_uri": (output / "dev-loop/receipt.json").as_uri(),
                "owner_record_digest": receipt_digest,
                "stage": "server",
                "state": "failed",
                "required_markers": [
                    "dedicated-server-ready",
                    "common-registry-ready",
                ],
                "observed_markers": [],
                "effective_exit_code": 0,
                "cleanup_contained": True,
                "artifact_digest": "sha256:" + "e" * 64,
                "detail": "The required dedicated-server markers were not observed.",
            }
        ],
    )

    capsule = output / "failure.wb-repro"
    created_capsule = subprocess.run(
        [
            sys.executable,
            str(repository / "tools" / "workbench.py"),
            "diagnose", "reproduce", "create", session_id,
            "--state-root", str(state),
            "--output", str(capsule),
            "--action-id", "doctor.inspect",
            "--arguments-json", json.dumps({"workspace": str(workspace)}),
            "--mutation", "read-only",
            "--approve-privacy", "--json",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if created_capsule.returncode != 0 or created_capsule.stderr:
        raise RuntimeError(
            "public capsule fixture creation failed: "
            + (created_capsule.stderr or created_capsule.stdout)
        )
    capsule_result = json.loads(created_capsule.stdout)
    print(
        json.dumps(
            {
                "capsule_id": capsule_result["capsule_id"],
                "capsule_path": str(capsule),
                "classified_diagnosis": classified_diagnosis,
                "diagnosis_id": capsule_result["diagnosis_id"],
                "session_id": session_id,
                "state_root": str(state),
                "workspace": str(workspace),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
