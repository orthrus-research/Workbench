from __future__ import annotations

from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/atlas/src",
    ROOT / "modules/crucible/src",
    ROOT / "modules/project-intelligence/src",
    ROOT / "modules/runtime-explorer/src",
    ROOT / "modules/workbench-shell/src",
    ROOT / "modules/relay/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_crucible_runtime_snapshot import (  # noqa: E402
    build_runtime_snapshot,
    capability_status,
)
from workbench_relay.cli import main, validate_location  # noqa: E402
from workbench_runtime_explorer import (  # noqa: E402
    Explorer,
    ExplorerRecord,
    ExplorerRequest,
    ExplorerSource,
)
from workbench_runtime_explorer.query import InterpretedIdentity  # noqa: E402


def _result(*, ambiguous: bool = False, runtime: bool = True) -> dict:
    static_source = ExplorerSource(
        source_id="source:workspace",
        source_kind="workspace-declarations",
        authority="Project Intelligence",
        state="complete",
        identity={"workspace": "/retained/project"},
    )
    records = [
        ExplorerRecord(
            record_id="record:source",
            source_id=static_source.source_id,
            authority=static_source.authority,
            state="static-possible",
            kind="machine",
            name="Example Press",
            identities=(
                {
                    "kind": "machine-id",
                    "value": "example:press",
                    "basis": "source declaration",
                },
            ),
            owner={
                "state": "declared",
                "actors": [{"kind": "mod", "id": "example"}],
                "basis": "source declaration",
            },
            scope={"profile": "fixture-profile", "physical_side": "common"},
            navigation=(
                {
                    "kind": "source",
                    "path": "/retained/project/src/Press.java",
                    "line": 42,
                    "column": None,
                    "label": "machine declaration",
                    "resolution": "exact-workspace-file",
                },
            ),
        )
    ]
    sources = [static_source]
    if runtime:
        runtime_source = ExplorerSource(
            source_id="source:atlas",
            source_kind="atlas-runtime-graph",
            authority="Atlas",
            state="complete",
            identity={"snapshot_id": "atlas-snapshot:fixture"},
            coverage={"matching_nodes": 2 if ambiguous else 1, "truncated": False},
        )
        sources.append(runtime_source)
        count = 2 if ambiguous else 1
        for index in range(count):
            records.append(
                ExplorerRecord(
                    record_id=f"rg:machine:{index}",
                    source_id=runtime_source.source_id,
                    authority=runtime_source.authority,
                    state="observed",
                    kind="machine",
                    name="Example Press",
                    identities=(
                        {
                            "kind": "machine-id",
                            "value": "example:press",
                            "basis": "Atlas typed identity key",
                        },
                    ),
                    owner={
                        "state": "observed",
                        "actors": [{"kind": "mod", "id": "example"}],
                        "basis": "Atlas belongs_to_mod relationship",
                    },
                    scope={
                        "profile": "cleanroom:fixture",
                        "physical_side": "CLIENT",
                        "session_id": "crucible-session:fixture",
                        "snapshot_id": "atlas-snapshot:fixture",
                    },
                    navigation=(
                        {
                            "kind": "resource",
                            "path": "assets/example/machines/press.json",
                            "line": 1,
                            "column": None,
                            "label": "Atlas defined resource",
                            "resolution": "runtime-recorded-locator",
                        },
                    ),
                )
            )
    request = ExplorerRequest(
        raw="machine:example:press",
        text="example:press",
        terms=("example:press",),
        filters={},
        interpreted=(
            InterpretedIdentity(
                "machine-id",
                "example:press",
                "explicit machine: query",
            ),
        ),
        limit=100,
    )
    return Explorer(sources, records).search(request)


def _snapshot() -> dict:
    return build_runtime_snapshot(
        runtime_identity={
            "launch_id": "launch:relay-fixture",
            "platform_profile_id": "workbench-platform:cleanroom:test",
            "pack_profile_id": "workbench-pack:relay-fixture",
            "physical_side": "dedicated_server",
            "process_outcome": "complete",
            "candidate_lock_sha256": "1" * 64,
            "transformer_toolchain_lock_sha256": "2" * 64,
            "java_runtime_id": "eclipse-temurin-25.0.4+7",
            "session_ids": ["crucible-session:relay-fixture"],
            "capture_ids": [],
        },
        epoch={
            "profile_epoch_id": "cleanroom:test-relay",
            "adapter_id": "workbench-cleanroom-runtime-adapter:test",
            "adapter_version": "1.0.0",
            "axes": [{"name": "cleanroom.version", "value": "test"}],
            "capabilities": ["relay.fixture"],
        },
        receipt_bindings=[],
        capabilities=[
            capability_status(
                "relay.fixture",
                state="not_observed",
                reason_code="fixture-capability-not-run",
            )
        ],
        capture_health={
            "observer_state": "healthy",
            "started": True,
            "completed": True,
            "errors": [],
        },
    )


class RelayCliTests(unittest.TestCase):
    def test_complete_explorer_result_resolves_and_preserves_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "explorer.json"
            path.write_text(json.dumps(_result()) + "\n", encoding="utf-8")
            output = StringIO()
            code = main(
                [
                    "machine:example:press",
                    "--explorer-result",
                    str(path),
                    "--json",
                ],
                output=output,
                error=StringIO(),
            )

        self.assertEqual(0, code)
        value = json.loads(output.getvalue())
        validate_location(value)
        self.assertEqual("resolved", value["resolution"])
        self.assertEqual("exact-owner-location", value["reason_code"])
        self.assertEqual(2, len(value["locations"]))
        facets = value["owner_entity"]["facets"]
        observed = next(row for row in facets if row["state"] == "observed")
        self.assertEqual("cleanroom:fixture", observed["scope"]["profile"])
        self.assertEqual("CLIENT", observed["scope"]["physical_side"])
        self.assertEqual("crucible-session:fixture", observed["scope"]["session_id"])
        self.assertEqual("atlas-snapshot:fixture", observed["scope"]["snapshot_id"])

    def test_direct_crucible_snapshot_receipt_resolves_owner_pointer(self) -> None:
        snapshot = _snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
            output = StringIO()
            code = main(
                [
                    "launch:relay-fixture",
                    "--identity-kind",
                    "launch-id",
                    "--receipt",
                    str(path),
                    "--json",
                ],
                output=output,
                error=StringIO(),
            )

        self.assertEqual(0, code)
        value = json.loads(output.getvalue())
        self.assertEqual("resolved", value["resolution"])
        self.assertEqual("Crucible", value["locations"][0]["authority"])
        facet = value["owner_entity"]["facets"][0]
        self.assertEqual(
            ["crucible-session:relay-fixture"],
            facet["runtime_form"]["runtime_identity"]["session_ids"],
        )
        self.assertEqual(snapshot["snapshot_id"], facet["runtime_form"]["snapshot_id"])

    def test_ambiguous_runtime_identity_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "explorer.json"
            path.write_text(json.dumps(_result(ambiguous=True)) + "\n", encoding="utf-8")
            output = StringIO()
            code = main(
                ["machine:example:press", "--explorer-result", str(path)],
                output=output,
                error=StringIO(),
            )

        self.assertEqual(1, code)
        self.assertIn("Relay · unresolved", output.getvalue())
        self.assertIn("More than one owner-backed entity", output.getvalue())

    def test_static_only_identity_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "explorer.json"
            path.write_text(json.dumps(_result(runtime=False)) + "\n", encoding="utf-8")
            output = StringIO()
            code = main(
                ["machine:example:press", "--explorer-result", str(path)],
                output=output,
                error=StringIO(),
            )

        self.assertEqual(1, code)
        self.assertIn("no observed Atlas or Crucible runtime facet", output.getvalue())

    def test_tampered_explorer_result_fails_closed(self) -> None:
        value = _result()
        forged = deepcopy(value)
        forged["matches"][0]["name"] = "forged"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "explorer.json"
            path.write_text(json.dumps(forged) + "\n", encoding="utf-8")
            error = StringIO()
            code = main(
                ["machine:example:press", "--explorer-result", str(path)],
                output=StringIO(),
                error=error,
            )

        self.assertEqual(2, code)
        self.assertIn("identity is stale or invalid", error.getvalue())


if __name__ == "__main__":
    unittest.main()
