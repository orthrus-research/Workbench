"""Focused tests for the IDE-neutral retained-result snapshot seam."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch


TEST_ROOT = Path(__file__).resolve().parent
MODULE_ROOT = TEST_ROOT.parent
SUITE_ROOT = MODULE_ROOT.parents[1]
for source in (
    TEST_ROOT,
    MODULE_ROOT / "src",
    SUITE_ROOT / "modules/project-intelligence/src",
    SUITE_ROOT / "modules/atlas/src",
    SUITE_ROOT / "modules/blueprints/src",
    SUITE_ROOT / "modules/crucible/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from test_feature_studio import _owner_result, _plan, _validated_runtime  # noqa: E402
from workbench_shell.cli import main  # noqa: E402
from workbench_shell.feature_studio import (  # noqa: E402
    FeatureStudioError,
    _content_id,
    inspect_feature,
)
from workbench_shell.feature_studio_snapshot import (  # noqa: E402
    open_feature_snapshot,
    project_feature_snapshot,
    refresh_feature_snapshot,
    refresh_feature_snapshot_file,
    validate_feature_snapshot,
)


PLAN_BACKED_FIXTURE = (
    TEST_ROOT / "fixtures/feature-studio-plan-backed-v2"
)
PLAN_BACKED_SNAPSHOT_ID = (
    "feature-studio-snapshot:sha256:"
    "63aaf7e583167b978ca103f3f5df50b2920ba8fcb06827bc61806897f71599da"
)
PLAN_BACKED_SNAPSHOT_FILE_SHA256 = (
    "a937d4f7501f8e5b0b7ff80c769246cd94904bae813306f053db9fdd63a2b4e2"
)
_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


def _apply_fixture_unified_diff(source: bytes, unified_diff: str) -> bytes:
    """Reconstruct fixture planned bytes while checking every hunk context."""

    source_lines = source.decode("utf-8").splitlines(keepends=True)
    diff_lines = unified_diff.splitlines(keepends=True)
    if (
        len(diff_lines) < 3
        or not diff_lines[0].startswith("--- a/")
        or not diff_lines[1].startswith("+++ b/")
    ):
        raise AssertionError("fixture unified diff lacks exact file headers")
    output: list[str] = []
    source_cursor = 0
    line_index = 2
    while line_index < len(diff_lines):
        header = _HUNK_HEADER.match(diff_lines[line_index].rstrip("\n"))
        if header is None:
            raise AssertionError("fixture unified diff has an invalid hunk header")
        old_start = int(header.group(1))
        old_count = int(header.group(2) or "1")
        new_count = int(header.group(4) or "1")
        hunk_start = old_start if old_count == 0 else old_start - 1
        if hunk_start < source_cursor:
            raise AssertionError("fixture unified diff hunks overlap")
        output.extend(source_lines[source_cursor:hunk_start])
        source_cursor = hunk_start
        consumed = 0
        produced = 0
        line_index += 1
        while line_index < len(diff_lines) and not diff_lines[line_index].startswith("@@ "):
            row = diff_lines[line_index]
            if not row or row[0] not in {" ", "+", "-"}:
                raise AssertionError("fixture unified diff has an invalid row")
            payload = row[1:]
            if row[0] in {" ", "-"}:
                if source_cursor >= len(source_lines) or source_lines[source_cursor] != payload:
                    raise AssertionError("fixture unified diff context drifted")
                source_cursor += 1
                consumed += 1
            if row[0] in {" ", "+"}:
                output.append(payload)
                produced += 1
            line_index += 1
        if consumed != old_count or produced != new_count:
            raise AssertionError("fixture unified diff hunk counts disagree")
    output.extend(source_lines[source_cursor:])
    return "".join(output).encode("utf-8")


def _reseal_snapshot(snapshot: dict) -> dict:
    material = {key: item for key, item in snapshot.items() if key != "snapshot_id"}
    snapshot["snapshot_id"] = _content_id("feature-studio-snapshot", material)
    return snapshot


class FeatureStudioSnapshotTests(unittest.TestCase):
    def _ready_result(self, root: Path) -> tuple[dict, dict, Path]:
        workspace = root / "source"
        workspace.mkdir()
        owner_plan = _plan(workspace)
        with patch(
            "workbench_shell.feature_studio.plan_material_fluid_trial",
            return_value=owner_plan,
        ):
            result = inspect_feature(
                SUITE_ROOT,
                workspace,
                name="Pilot Coolant",
                color="0x425d73",
            )
        return result, owner_plan, workspace

    def test_value_projection_is_closed_bounded_and_honestly_not_refreshable(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            result, _owner_plan, _workspace = self._ready_result(Path(temporary))
            first = project_feature_snapshot(result, suite_root=SUITE_ROOT)
            second = project_feature_snapshot(result, suite_root=SUITE_ROOT)

        self.assertEqual(first, second)
        self.assertEqual(first["format"], "workbench-feature-studio-snapshot-v2")
        self.assertEqual(first["result"]["result_id"], result["result_id"])
        self.assertEqual(
            first["identities"]["semantic_projection_id"],
            result["semantic_equivalence"]["projection_id"],
        )
        self.assertEqual(first["feature"], result["feature"])
        self.assertEqual(
            first["source_locations"][0]["range"]["coordinate_system"],
            "unified-diff-lines-one-based-zero-for-empty-inclusive",
        )
        self.assertEqual(first["source_locations"][0]["range"]["start_line"], 1)
        self.assertEqual(first["source_locations"][0]["range"]["start_line"] - 1, 0)
        self.assertEqual(
            result["source_locations"][0]["range"]["coordinate_system"],
            "unified-diff-lines-zero-for-empty-inclusive",
        )
        self.assertEqual(first["planned_changes"], result["planned_changes"])
        self.assertEqual(first["assertions"], result["assertions"])
        self.assertEqual(first["actions"], result["actions"])
        self.assertEqual(first["availability"]["runtime_state"], "pending")
        self.assertEqual(first["origin"]["refresh"]["state"], "unavailable")
        self.assertNotIn("canonical_json", first["owner_links"][0])
        self.assertEqual(
            first["owner_links"][0]["owner_id"],
            first["identities"]["flow_plan_id"],
        )
        self.assertEqual(validate_feature_snapshot(first), first)
        with self.assertRaisesRegex(FeatureStudioError, "no retained refresh source"):
            refresh_feature_snapshot(SUITE_ROOT, first)

    def test_retained_result_file_opens_and_refreshes_without_semantic_reinterpretation(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            result, _owner_plan, _workspace = self._ready_result(root)
            retained = root / "feature-result.json"
            raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
            retained.write_bytes(raw)

            snapshot = open_feature_snapshot(SUITE_ROOT, result_path=retained)
            refreshed = refresh_feature_snapshot(SUITE_ROOT, snapshot)
            snapshot_path = root / "feature-snapshot.json"
            snapshot_path.write_text(
                json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
            )
            refreshed_from_file = refresh_feature_snapshot_file(
                SUITE_ROOT, snapshot_path
            )
            output = StringIO()
            with redirect_stdout(output):
                refresh_status = main(
                    [
                        "feature",
                        "inspect",
                        "--refresh-snapshot",
                        str(snapshot_path),
                        "--snapshot",
                        "--json",
                        "--suite-root",
                        str(SUITE_ROOT),
                    ]
                )

            self.assertEqual(snapshot, refreshed)
            self.assertEqual(snapshot, refreshed_from_file)
            self.assertEqual(refresh_status, 0)
            self.assertEqual(json.loads(output.getvalue()), snapshot)
            self.assertEqual(snapshot["origin"]["kind"], "feature-studio-result-file")
            self.assertEqual(snapshot["origin"]["uri"], retained.as_uri())
            self.assertEqual(snapshot["origin"]["sha256"], sha256(raw).hexdigest())
            self.assertEqual(snapshot["origin"]["size"], len(raw))
            self.assertEqual(snapshot["origin"]["refresh"]["state"], "available")

            retained.write_bytes(raw + b"\n")
            with self.assertRaisesRegex(FeatureStudioError, "refresh source drifted"):
                refresh_feature_snapshot(SUITE_ROOT, snapshot)

            linked = root / "linked-result.json"
            linked.symlink_to(retained)
            with self.assertRaisesRegex(FeatureStudioError, "symbolic link"):
                open_feature_snapshot(SUITE_ROOT, result_path=linked)

    def test_retained_receipt_projects_exact_owner_links_and_observed_states(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            workspace = root / "source"
            workspace.mkdir()
            owner_plan = _plan(workspace)
            owner_result = _owner_result("d", root, workspace, owner_plan)
            receipt = Path(
                owner_result["receipt"]["target"]["receipt_uri"].removeprefix(
                    "file://"
                )
            )
            with (
                patch(
                    "workbench_shell.feature_studio.plan_material_fluid_trial",
                    return_value=owner_plan,
                ),
                patch(
                    "workbench_shell.feature_studio.validate_retained_material_fluid_success",
                    side_effect=_validated_runtime,
                ),
            ):
                snapshot = open_feature_snapshot(
                    SUITE_ROOT,
                    receipt_path=receipt,
                )
                refreshed = refresh_feature_snapshot(SUITE_ROOT, snapshot)
                output = StringIO()
                with redirect_stdout(output):
                    cli_status = main(
                        [
                            "feature",
                            "inspect",
                            "--receipt",
                            str(receipt),
                            "--snapshot",
                            "--json",
                            "--suite-root",
                            str(SUITE_ROOT),
                        ]
                    )
                cli_snapshot = json.loads(output.getvalue())

            receipt_link = next(
                link
                for link in snapshot["owner_links"]
                if link["role"] == "material-fluid-receipt"
            )
            self.assertEqual(snapshot, refreshed)
            self.assertEqual(cli_status, 0)
            self.assertEqual(cli_snapshot, snapshot)
            self.assertEqual(snapshot["origin"]["kind"], "material-flow-receipt-v2")
            self.assertEqual(snapshot["result"]["operation"], "inspect")
            self.assertEqual(snapshot["result"]["state"], "complete")
            self.assertEqual(snapshot["availability"]["runtime_state"], "observed")
            self.assertEqual(
                snapshot["identities"]["runtime_session_id"],
                owner_result["receipt"]["runtime"]["session_id"],
            )
            self.assertEqual(
                snapshot["identities"]["material_flow_receipt_id"],
                owner_result["receipt"]["receipt_id"],
            )
            self.assertEqual(
                snapshot["identities"]["blueprint_stage_id"],
                owner_result["receipt"]["blueprint_stage"]["stage_id"],
            )
            self.assertEqual(
                receipt_link["owner_id"],
                snapshot["identities"]["material_flow_receipt_id"],
            )
            self.assertEqual(receipt_link["retained"]["uri"], receipt.as_uri())
            self.assertEqual(
                receipt_link["retained"]["sha256"], snapshot["origin"]["sha256"]
            )
            self.assertTrue(
                any(link["retained"] is not None for link in snapshot["owner_links"])
            )
            profile_runtime_keys = (
                "platform_profile_id",
                "pack_profile_id",
                "profile_assessment_format",
                "profile_assessment_id",
                "crucible_snapshot_id",
                "runtime_session_id",
                "runtime_plan_id",
                "materialization_id",
                "final_launch_id",
            )
            self.assertTrue(
                all(snapshot["context"][key] is not None for key in profile_runtime_keys)
            )
            self.assertEqual(
                snapshot["context"]["pack_profile_family_id"],
                snapshot["context"]["pack_profile_id"],
            )

            missing_runtime_plan = deepcopy(snapshot)
            missing_runtime_plan["context"]["runtime_plan_id"] = None
            missing_runtime_plan["identities"]["runtime_plan_id"] = None
            _reseal_snapshot(missing_runtime_plan)
            with self.assertRaisesRegex(
                FeatureStudioError, "profile/runtime identity tuple disagrees"
            ):
                validate_feature_snapshot(missing_runtime_plan)

            rebound_family = deepcopy(snapshot)
            rebound_family["context"]["pack_profile_family_id"] = (
                "workbench-pack:foreign"
            )
            rebound_family["identities"]["pack_profile_family_id"] = (
                "workbench-pack:foreign"
            )
            _reseal_snapshot(rebound_family)
            with self.assertRaisesRegex(
                FeatureStudioError, "profile/runtime identity tuple disagrees"
            ):
                validate_feature_snapshot(rebound_family)

    def test_plan_backed_intelligence_fixture_binds_exact_sources_hunks_and_states(self):
        snapshot_path = PLAN_BACKED_FIXTURE / "snapshot.json"
        snapshot_raw = snapshot_path.read_bytes()
        snapshot = json.loads(snapshot_raw)
        validated = validate_feature_snapshot(snapshot)

        self.assertEqual(validated, snapshot)
        self.assertEqual(snapshot["snapshot_id"], PLAN_BACKED_SNAPSHOT_ID)
        self.assertEqual(
            sha256(snapshot_raw).hexdigest(),
            PLAN_BACKED_SNAPSHOT_FILE_SHA256,
        )
        self.assertEqual(
            snapshot["context"]["workspace_uri"],
            "file:///tmp/workbench-fixtures/feature-studio-plan-backed-v2/workspace",
        )
        self.assertEqual(snapshot["result"]["operation"], "plan")
        self.assertEqual(snapshot["result"]["state"], "ready")
        self.assertEqual(snapshot["plan"]["state"], "ready")
        self.assertEqual(snapshot["availability"]["plan_state"], "ready")
        self.assertEqual(snapshot["availability"]["runtime_state"], "pending")
        self.assertEqual(
            snapshot["identities"]["flow_plan_id"],
            snapshot["plan"]["flow_plan_id"],
        )
        self.assertEqual(
            snapshot["identities"]["source_snapshot_id"],
            snapshot["review_binding"]["source_snapshot_id"],
        )
        self.assertEqual(
            {
                assertion["assertion_key"]: assertion["state"]
                for assertion in snapshot["assertions"]
            },
            {
                "fluid_registration": "pending",
                "fml_client_load": "pending",
                "groovy_compilation": "pending",
                "localization": "pending",
                "material_registration": "pending",
            },
        )

        workspace = PLAN_BACKED_FIXTURE / "workspace"
        changes = {
            change["relative_path"]: change
            for change in snapshot["planned_changes"]
        }
        self.assertEqual(
            set(changes),
            {
                "groovy/material/PetrochemistryMaterials.groovy",
                "groovy/material/SuSyMaterials.groovy",
                "resources/langfiles/lang/en_us.lang",
            },
        )
        current_bytes: dict[str, bytes] = {}
        planned_bytes: dict[str, bytes] = {}
        for relative_path, change in changes.items():
            current = (workspace / relative_path).read_bytes()
            planned = _apply_fixture_unified_diff(current, change["unified_diff"])
            current_bytes[relative_path] = current
            planned_bytes[relative_path] = planned
            self.assertEqual(sha256(current).hexdigest(), change["source_sha256"])
            self.assertEqual(sha256(planned).hexdigest(), change["planned_sha256"])
            self.assertEqual(len(planned), change["planned_size"])

            hunk_headers = [
                match
                for row in change["unified_diff"].splitlines()
                if (match := _HUNK_HEADER.match(row)) is not None
            ]
            locations = [
                location
                for location in snapshot["source_locations"]
                if location["relative_path"] == relative_path
            ]
            self.assertEqual(len(locations), len(hunk_headers))
            for location, header in zip(locations, hunk_headers, strict=True):
                old_start = int(header.group(1))
                old_count = int(header.group(2) or "1")
                new_start = int(header.group(3))
                new_count = int(header.group(4) or "1")
                self.assertEqual(location["hunk_index"], locations.index(location))
                self.assertEqual(location["workspace_uri"], snapshot["context"]["workspace_uri"])
                self.assertEqual(location["source_sha256"], change["source_sha256"])
                self.assertEqual(location["planned_sha256"], change["planned_sha256"])
                self.assertEqual(
                    (location["range"]["start_line"], location["range"]["end_line"]),
                    (
                        old_start,
                        old_start if old_count == 0 else old_start + old_count - 1,
                    ),
                )
                self.assertEqual(
                    (
                        location["planned_range"]["start_line"],
                        location["planned_range"]["end_line"],
                    ),
                    (
                        new_start,
                        new_start if new_count == 0 else new_start + new_count - 1,
                    ),
                )

        color = snapshot["feature"]["color"].encode("ascii")
        symbol = snapshot["feature"]["symbol_name"].encode("ascii")
        self.assertEqual(sum(value.count(color) for value in current_bytes.values()), 0)
        self.assertEqual(sum(value.count(symbol) for value in current_bytes.values()), 0)
        self.assertEqual(sum(value.count(color) for value in planned_bytes.values()), 1)
        self.assertEqual(sum(value.count(symbol) for value in planned_bytes.values()), 2)
        self.assertEqual(snapshot["origin"]["refresh"]["state"], "unavailable")
        self.assertEqual(len(snapshot["owner_links"]), 1)
        owner_link = snapshot["owner_links"][0]
        self.assertEqual(owner_link["role"], "material-fluid-plan")
        self.assertEqual(owner_link["owner_id"], snapshot["plan"]["flow_plan_id"])
        self.assertEqual(owner_link["projection_encoding"], "workbench-canonical-json-v2")
        self.assertRegex(owner_link["canonical_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(owner_link["canonical_size"], 0)
        self.assertIsNone(owner_link["retained"])

    def test_schema_identity_and_cross_links_fail_closed_after_mutation(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            result, _owner_plan, _workspace = self._ready_result(Path(temporary))
            baseline = project_feature_snapshot(result, suite_root=SUITE_ROOT)

        unsealed = deepcopy(baseline)
        unsealed["availability"]["runtime_state"] = "observed"
        with self.assertRaisesRegex(FeatureStudioError, "identity is invalid"):
            validate_feature_snapshot(unsealed)

        rebound = deepcopy(baseline)
        rebound["identities"]["source_snapshot_id"] = "sha256:" + "f" * 64
        _reseal_snapshot(rebound)
        with self.assertRaisesRegex(FeatureStudioError, "identity links disagree"):
            validate_feature_snapshot(rebound)

        foreign_owner = deepcopy(baseline)
        foreign_owner["owner_links"][0]["owner_id"] = "sha256:" + "e" * 64
        _reseal_snapshot(foreign_owner)
        with self.assertRaisesRegex(FeatureStudioError, "owner identities disagree"):
            validate_feature_snapshot(foreign_owner)

        duplicate_hunk = deepcopy(baseline)
        duplicate_hunk["source_locations"][1] = deepcopy(
            duplicate_hunk["source_locations"][0]
        )
        _reseal_snapshot(duplicate_hunk)
        with self.assertRaisesRegex(FeatureStudioError, "repeats a planned source hunk"):
            validate_feature_snapshot(duplicate_hunk)

        omitted_path = deepcopy(baseline)
        replacement = deepcopy(omitted_path["source_locations"][0])
        replacement["hunk_index"] = 1
        omitted_path["source_locations"][2] = replacement
        _reseal_snapshot(omitted_path)
        with self.assertRaisesRegex(FeatureStudioError, "source paths are incomplete"):
            validate_feature_snapshot(omitted_path)

        pending_with_profile = deepcopy(baseline)
        pending_with_profile["context"]["profile_assessment_format"] = (
            "workbench-assessment-v1"
        )
        _reseal_snapshot(pending_with_profile)
        with self.assertRaisesRegex(
            FeatureStudioError, "profile/runtime identity tuple disagrees"
        ):
            validate_feature_snapshot(pending_with_profile)

        invalid_range = deepcopy(baseline)
        invalid_range["source_locations"][0]["range"]["start_line"] = 0
        with self.assertRaisesRegex(FeatureStudioError, "invalid at"):
            validate_feature_snapshot(invalid_range)

        extra = deepcopy(baseline)
        extra["client_guess"] = "not part of V1"
        with self.assertRaisesRegex(FeatureStudioError, "invalid at"):
            validate_feature_snapshot(extra)

    def test_cli_inspect_result_snapshot_emits_only_the_v1_snapshot(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            result, _owner_plan, _workspace = self._ready_result(root)
            retained = root / "feature-result.json"
            retained.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            output = StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "feature",
                        "inspect",
                        "--json",
                        "--result",
                        str(retained),
                        "--suite-root",
                        str(SUITE_ROOT),
                        "--snapshot",
                    ]
                )
            emitted = json.loads(output.getvalue())

            self.assertEqual(status, 0)
            self.assertEqual(emitted["format"], "workbench-feature-studio-snapshot-v2")
            self.assertEqual(emitted["result"]["result_id"], result["result_id"])
            self.assertNotIn("owner_artifacts", emitted)

            error = StringIO()
            with redirect_stderr(error):
                status = main(
                    [
                        "feature",
                        "inspect",
                        "--result",
                        str(retained),
                        "--suite-root",
                        str(SUITE_ROOT),
                        "--json",
                    ]
                )
            self.assertEqual(status, 2)
            self.assertIn("requires --snapshot", error.getvalue())


if __name__ == "__main__":
    unittest.main()
