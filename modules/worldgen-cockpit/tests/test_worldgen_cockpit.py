from __future__ import annotations

from copy import deepcopy
import importlib.util
import io
import json
import jsonschema
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/worldgen-cockpit/src",
    ROOT / "modules/subsurface-studio/src",
    ROOT / "modules/crucible/src",
    ROOT / "modules/atlas/src",
    ROOT / "modules/workbench-shell/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_worldgen_cockpit.analysis import analyze_pair  # noqa: E402
from workbench_worldgen_cockpit.cli import run as cli_run  # noqa: E402
from workbench_worldgen_cockpit.model import (  # noqa: E402
    load_profile,
    load_report,
    sha256_file,
    validate_report,
    write_json_atomic,
)
from workbench_worldgen_cockpit.orchestrator import (  # noqa: E402
    build_run_plan,
    render_run_plan,
)
from workbench_worldgen_cockpit.render import render_html, render_report  # noqa: E402


SUBSURFACE_TEST = ROOT / "modules/subsurface-studio/tests/test_subsurface_studio.py"
SUBSURFACE_SPEC = importlib.util.spec_from_file_location(
    "cockpit_subsurface_fixture_helpers", SUBSURFACE_TEST
)
assert SUBSURFACE_SPEC and SUBSURFACE_SPEC.loader
subsurface_helpers = importlib.util.module_from_spec(SUBSURFACE_SPEC)
SUBSURFACE_SPEC.loader.exec_module(subsurface_helpers)

ATLAS_TEST = ROOT / "modules/atlas/tests/test_worldgen_observatory_query.py"
ATLAS_SPEC = importlib.util.spec_from_file_location(
    "cockpit_atlas_fixture_helpers", ATLAS_TEST
)
assert ATLAS_SPEC and ATLAS_SPEC.loader
atlas_helpers = importlib.util.module_from_spec(ATLAS_SPEC)
ATLAS_SPEC.loader.exec_module(atlas_helpers)


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_log(path: Path, *, changed: bool = False) -> Path:
    records = [
        {"event": "plan.publish", "profile": "fixture", "plan_version": 2, "plan_hash": "fixture-plan"},
        {
            "event": "generator.construct",
            "seed": 42,
            "dimension": 0,
            "world_type": "wb_proto",
            "provider": "fixture.Provider",
            "generator": "fixture.Generator",
            "profile": "fixture",
            "plan_version": 2,
            "plan_hash": "fixture-plan",
            "cave_generator": "fixture.Caves",
            "ravine_generator": "fixture.Ravines",
            "watershed_algorithm": "fixture-d8",
            "watershed_cell_size_blocks": 16,
            "watershed_tile_size_cells": 32,
            "watershed_halo_cells": 4,
        },
        {
            "event": "chunk.generate",
            "dimension": 0,
            "chunk_x": 0,
            "chunk_z": 0,
            "height_hash": "candidate" if changed else "baseline",
            "underground_air": 1,
            "elapsed_us": 10,
        },
        {
            "event": "chunk.populate",
            "dimension": 0,
            "chunk_x": 0,
            "chunk_z": 0,
            "placed": 1,
            "elapsed_us": 20,
        },
    ]
    text = ["[Server thread/INFO] Done (1.000s)! For help, type help"]
    text.extend("WORLDGEN_PROTOTYPE " + json.dumps(row, sort_keys=True) for row in records)
    text.append("[Server thread/INFO] Stopping the server")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    return path


def _jfr(path: Path, *, p95: int) -> Path:
    return _write_json(
        path,
        {
            "tool_version": "world-studio-jfr-summary-v2",
            "invalid_events": 0,
            "chunk_stages": {
                "sample.chunk": {
                    "count": 1,
                    "latency": {"p50_us": p95 // 2, "p95_us": p95, "maximum_us": p95},
                }
            },
        },
    )


def _iteration_report(
    root: Path,
    *,
    side: str,
    manifest: Path,
    mode: str = "fast",
    seed: int = 42,
    semantic_changed: bool = False,
    jfr_p95: int | None = None,
    artifact_bytes: bytes | None = None,
) -> Path:
    profile_path = ROOT / "profiles/packs/supersymmetry/worldgen/worldgen-iteration-profile-v2.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    side_root = root / side
    runtime = side_root / "runtime"
    runtime.mkdir(parents=True)
    summary = _write_json(
        side_root / "summary.json",
        {
            "runtime": {
                "ready": True,
                "clean_stop_observed": True,
                "invalid_prototype_records": 0,
            },
            "prototype_failures": [],
            "generation": {"chunks": 1},
        },
    )
    launch_log = _write_log(side_root / "launch.log", changed=semantic_changed)
    handoff = _write_json(
        side_root / "viewer-handoff.json",
        {
            "cwd": str(root),
            "externalArtifactRoot": str(manifest.parent.resolve()),
            "manifest": str(manifest.resolve()),
            "port": 40000 if side == "baseline" else 40001,
            "url": f"http://127.0.0.1:{40000 if side == 'baseline' else 40001}/?view=region",
        },
    )
    artifact = side_root / "worldgen.jar"
    artifact.write_bytes(artifact_bytes or ("artifact-" + side).encode("utf-8"))
    plan = side_root / "plan.groovy"
    plan.write_text("// frozen fixture plan\n", encoding="utf-8")
    outputs = {
        "runtime": str(runtime),
        "world_studio_summary": str(summary),
        "cleanroom_launch_log": str(launch_log),
        "viewer_handoff": str(handoff),
        "viewer_url": json.loads(handoff.read_text())["url"],
        "strata": str(manifest.parent),
    }
    if jfr_p95 is not None:
        outputs["jfr_summary"] = str(_jfr(side_root / "jfr.json", p95=jfr_p95))
        recording = side_root / "capture.jfr"
        recording.write_bytes(b"fixture-jfr")
        outputs["jfr_recording"] = str(recording)
    stage_ids = ["preflight", "build", "provision", "configure", "capture", "summarize", "handoff"]
    if jfr_p95 is not None:
        stage_ids.append("performance")
    stages = []
    for stage_id in stage_ids:
        details = {}
        if stage_id == "provision":
            details = {
                "template_audit": {
                    "server_jar": {"sha256": "c" * 64}
                }
            }
        stages.append(
            {
                "id": stage_id,
                "purpose": stage_id,
                "status": "complete",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:00:01Z",
                "duration_seconds": 1,
                "details": details,
                "error": None,
            }
        )
    report = {
        "format": "workbench-worldgen-iteration-report-v1",
        "schema_version": 1,
        "label": f"fixture-{side}",
        "profile": "supersymmetry",
        "mode": mode,
        "status": "complete",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:00:10Z",
        "failure": None,
        "invocation": [],
        "reproduction_command": "fixture",
        "inputs": {
            "profile": {
                "file": str(profile_path),
                "sha256": sha256_file(profile_path),
                "profile_id": profile["profile_id"],
                "platform_profile_id": profile["platform_profile_id"],
            },
            "toolchains": {
                "java": {"path": "/fixture/java", "sha256": "a" * 64, "version_output": "25"},
                "gradle": None,
            },
            "sample": {"seed": seed, "region": [0, 0, 1, 1], "mode": mode},
            "worldgen_artifact": {
                "path": str(artifact),
                "sha256": sha256_file(artifact),
                "size_bytes": artifact.stat().st_size,
                "build_skipped": True,
            },
            "plan": {"source": str(plan), "sha256": sha256_file(plan)},
            "runtime_mods": [
                {"file": "pack.jar", "mod_ids": ["fixture_pack"], "sha256": "b" * 64, "size_bytes": 1},
                {"file": "worldgen.jar", "mod_ids": ["workbench_worldgen_prototype"], "sha256": sha256_file(artifact), "size_bytes": artifact.stat().st_size},
            ],
            "runtime_template": str(root / "template"),
            "integration_boundaries": {},
        },
        "outputs": outputs,
        "stages": stages,
    }
    return _write_json(side_root / "iteration-report-v1.json", report)


class WorldgenCockpitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile_path = ROOT / "profiles/packs/supersymmetry/worldgen/worldgen-cockpit-profile-v1.json"
        cls.profile, cls.profile_binding = load_profile(cls.profile_path, root=ROOT)

    def _pair(
        self,
        root: Path,
        *,
        seed: int = 42,
        baseline_ore: int = 1,
        candidate_ore: int = 2,
        mode: str = "fast",
        semantic_changed: bool = False,
        baseline_jfr: int | None = None,
        candidate_jfr: int | None = None,
        identical_inputs: bool = False,
    ) -> tuple[Path, Path]:
        left_manifest = subsurface_helpers._build_strata(
            root / "baseline-capture", ore_count=baseline_ore, world_seed=seed
        )
        right_manifest = subsurface_helpers._build_strata(
            root / "candidate-capture", ore_count=candidate_ore, world_seed=seed
        )
        left = _iteration_report(
            root,
            side="baseline",
            manifest=left_manifest,
            mode=mode,
            seed=seed,
            jfr_p95=baseline_jfr,
            artifact_bytes=b"identical-artifact" if identical_inputs else None,
        )
        right = _iteration_report(
            root,
            side="candidate",
            manifest=right_manifest,
            mode=mode,
            seed=seed,
            semantic_changed=semantic_changed,
            jfr_p95=candidate_jfr,
            artifact_bytes=b"identical-artifact" if identical_inputs else None,
        )
        return left, right

    def test_profile_matches_schema_and_run_plan_is_previewable(self) -> None:
        schema = json.loads(
            (ROOT / "modules/worldgen-cockpit/schemas/workbench-worldgen-cockpit-profile-v1.schema.json").read_text()
        )
        jsonschema.validate(json.loads(self.profile_path.read_text()), schema)
        plan = build_run_plan(
            root=ROOT,
            cockpit_profile=self.profile,
            cockpit_profile_binding=self.profile_binding,
            profile_name="supersymmetry",
            mode="fast",
            label="fixture-cockpit-plan",
            seed=42,
            region="0,0,1,1",
            order="candidate-first",
            baseline_plan=None,
            candidate_plan=None,
            artifact=None,
            baseline_artifact=None,
            candidate_artifact=None,
            runtime_template=None,
            strata_root=None,
            java_cmd=None,
            gradle_cmd=None,
            heap=None,
            diagnostic_sample_modulo=None,
            startup_timeout=30,
            scan_timeout=60,
            stop_timeout=10,
            baseline_observatory_bundle=None,
            candidate_observatory_bundle=None,
            comparison_scope_sha256=None,
            baseline_inventory=None,
            candidate_inventory=None,
            baseline_impact=None,
            candidate_impact=None,
            baseline_trace=None,
            candidate_trace=None,
            baseline_observer_off_jfr=None,
            candidate_observer_off_jfr=None,
        )
        self.assertEqual(plan["artifact_policy"], "build-first-side-once-and-freeze-for-second")
        self.assertEqual(plan["experiment"]["comparison_intent"], "identical-input-control")
        self.assertIn("two fresh disposable worlds", render_run_plan(plan))

    def test_exact_changed_pair_is_content_addressed_and_visual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(root)
            review = root / "review.html"
            report = analyze_pair(
                root=ROOT,
                cockpit_profile=self.profile,
                cockpit_profile_binding=self.profile_binding,
                baseline_report=baseline,
                candidate_report=candidate,
                reproduction_command="fixture reproduce",
                review_path=review,
            )
            self.assertEqual(report["status"], "changed")
            self.assertEqual(report["coverage"], "complete-for-mode")
            self.assertTrue(report["alignment"]["aligned"])
            self.assertEqual(report["evidence"]["final_state"]["summary"]["category_totals"]["ore"], 1)
            self.assertTrue(report["evidence"]["semantic"]["equivalent"])
            self.assertEqual(report["visual"]["width"], 1)
            validate_report(report)
            schema = json.loads(
                (ROOT / "modules/worldgen-cockpit/schemas/workbench-worldgen-cockpit-report-v1.schema.json").read_text()
            )
            jsonschema.validate(report, schema)
            self.assertIn("Changed-chunk heatmap", render_report(report))
            self.assertIn("Aligned changed-chunk map", render_html(report))
            path = root / "report.json"
            write_json_atomic(path, report)
            loaded, _ = load_report(path)
            self.assertEqual(loaded["report_id"], report["report_id"])

    def test_equivalent_pair_closes_fast_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(root, candidate_ore=1)
            report = analyze_pair(
                root=ROOT,
                cockpit_profile=self.profile,
                cockpit_profile_binding=self.profile_binding,
                baseline_report=baseline,
                candidate_report=candidate,
                reproduction_command="fixture reproduce",
            )
            self.assertEqual(report["status"], "equivalent")
            self.assertEqual(report["coverage"], "complete-for-mode")
            self.assertEqual(report["evidence"]["final_state"]["summary"]["changed_block_positions"], 0)

    def test_identical_input_delta_is_reproducibility_failure_not_candidate_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(root, identical_inputs=True)
            report = analyze_pair(
                root=ROOT,
                cockpit_profile=self.profile,
                cockpit_profile_binding=self.profile_binding,
                baseline_report=baseline,
                candidate_report=candidate,
                reproduction_command="fixture reproduce",
            )
            self.assertEqual(report["status"], "unstable")
            self.assertEqual(report["decision"]["comparison_kind"], "identical-input-control")
            self.assertEqual(report["decision"]["reproducibility_status"], "failed-in-scope")
            self.assertIsNone(report["decision"]["behavior_changed"])
            self.assertIn("reproducibility failure", report["decision"]["headline"])
            validate_report(report)

    def test_seed_mismatch_is_retained_as_incomparable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(root)
            value = json.loads(candidate.read_text())
            value["inputs"]["sample"]["seed"] = 43
            _write_json(candidate, value)
            report = analyze_pair(
                root=ROOT,
                cockpit_profile=self.profile,
                cockpit_profile_binding=self.profile_binding,
                baseline_report=baseline,
                candidate_report=candidate,
                reproduction_command="fixture reproduce",
            )
            self.assertEqual(report["status"], "incomparable")
            self.assertIn("seed", report["alignment"]["failed_checks"])

    def test_atlas_first_divergence_closes_debug_causality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(
                root, mode="debug", seed=-571123474424848392
            )
            left_bundle, _, scope = atlas_helpers.fixture_bundle("cockpit-left")
            right_bundle, _, right_scope = atlas_helpers.fixture_bundle(
                "cockpit-right", rng_result="9"
            )
            self.assertEqual(scope, right_scope)
            left_path = _write_json(root / "left-bundle.json", left_bundle)
            right_path = _write_json(root / "right-bundle.json", right_bundle)
            report = analyze_pair(
                root=ROOT,
                cockpit_profile=self.profile,
                cockpit_profile_binding=self.profile_binding,
                baseline_report=baseline,
                candidate_report=candidate,
                reproduction_command="fixture reproduce",
                baseline_observatory_bundle=left_path,
                candidate_observatory_bundle=right_path,
                comparison_scope_sha256=scope,
            )
            self.assertEqual(report["coverage"], "complete-for-mode")
            self.assertEqual(report["evidence"]["causal"]["authority"], "Atlas")
            self.assertEqual(report["evidence"]["causal"]["summary"]["status"], "diverged")
            relation = report["evidence"]["causal"]["summary"]["experiment_relation"]
            self.assertEqual(relation["state"], "seed-platform-dimension-window-aligned")
            self.assertFalse(relation["baseline"]["same_process_bound"])

    def test_atlas_scope_from_another_seed_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(root, mode="debug", seed=42)
            left_bundle, _, scope = atlas_helpers.fixture_bundle("wrong-seed-left")
            right_bundle, _, _ = atlas_helpers.fixture_bundle("wrong-seed-right")
            left_path = _write_json(root / "left-bundle.json", left_bundle)
            right_path = _write_json(root / "right-bundle.json", right_bundle)
            with self.assertRaisesRegex(ValueError, "seed does not match"):
                analyze_pair(
                    root=ROOT,
                    cockpit_profile=self.profile,
                    cockpit_profile_binding=self.profile_binding,
                    baseline_report=baseline,
                    candidate_report=candidate,
                    reproduction_command="fixture reproduce",
                    baseline_observatory_bundle=left_path,
                    candidate_observatory_bundle=right_path,
                    comparison_scope_sha256=scope,
                )

    def test_performance_and_observer_overhead_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(
                root,
                mode="performance",
                baseline_jfr=100,
                candidate_jfr=250,
            )
            baseline_off = _jfr(root / "baseline-off.json", p95=50)
            candidate_off = _jfr(root / "candidate-off.json", p95=100)
            report = analyze_pair(
                root=ROOT,
                cockpit_profile=self.profile,
                cockpit_profile_binding=self.profile_binding,
                baseline_report=baseline,
                candidate_report=candidate,
                reproduction_command="fixture reproduce",
                baseline_observer_off_jfr=baseline_off,
                candidate_observer_off_jfr=candidate_off,
            )
            self.assertEqual(report["coverage"], "complete-for-mode")
            self.assertEqual(report["evidence"]["performance"]["state"], "observed-jfr")
            self.assertEqual(report["evidence"]["observer_overhead"]["state"], "observed-jfr")
            self.assertGreaterEqual(report["evidence"]["performance"]["summary"]["regression_count"], 1)

    def test_cli_compare_writes_report_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, candidate = self._pair(root, candidate_ore=1)
            report_path = root / "cockpit.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            code = cli_run(
                [
                    "compare",
                    "--profile",
                    "supersymmetry",
                    "--baseline-report",
                    str(baseline),
                    "--candidate-report",
                    str(candidate),
                    "--output",
                    str(report_path),
                ],
                root=ROOT,
                output=stdout,
                error=stderr,
            )
            self.assertEqual(code, 0, stderr.getvalue())
            self.assertTrue(report_path.is_file())
            self.assertTrue(report_path.with_suffix(".html").is_file())
            self.assertIn("WORLDGEN COCKPIT", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
