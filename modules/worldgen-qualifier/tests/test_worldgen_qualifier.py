from __future__ import annotations

import io
import json
import jsonschema
from copy import deepcopy
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[3]
for source in (
    ROOT / "modules/worldgen-qualifier/src",
    ROOT / "modules/worldgen-cockpit/src",
    ROOT / "modules/subsurface-studio/src",
    ROOT / "modules/crucible/src",
    ROOT / "modules/atlas/src",
    ROOT / "modules/workbench-shell/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_worldgen_qualifier.assessment import assess_matrix  # noqa: E402
from workbench_worldgen_qualifier.cli import run as cli_run  # noqa: E402
from workbench_worldgen_qualifier.model import (  # noqa: E402
    QualifierError,
    load_profile,
    validate_qualification,
)
from workbench_worldgen_qualifier.risk import scan_jars  # noqa: E402


def _class_bytes(*constants: str) -> bytes:
    entries = []
    for value in constants:
        raw = value.encode("utf-8")
        entries.append(b"\x01" + struct.pack(">H", len(raw)) + raw)
    return b"\xca\xfe\xba\xbe" + struct.pack(">HHH", 0, 52, len(entries) + 1) + b"".join(entries)


def _jar(path: Path, classes: dict[str, bytes], *, nested: bool = False) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in classes.items():
            archive.writestr(name, data)
        if nested:
            archive.writestr("META-INF/libraries/nested.jar", b"PK\x03\x04")
    return path


def _cockpit(
    *,
    report_id: str,
    fingerprint: str = "f" * 64,
    changed: int = 0,
    categories: dict[str, int] | None = None,
    semantic_equivalent: bool | None = True,
    aligned: bool = True,
    relation: str = "identical-input-control",
    final_state: str = "observed-exact",
    seed: int = 42,
) -> dict[str, object]:
    checks = [
        {"check_id": "separate-runtime", "aligned": aligned},
        {"check_id": "separate-final-capture", "aligned": aligned},
    ]
    sides = {}
    for side in ("baseline", "candidate"):
        sides[side] = {
            "plan": {"sha256": "a" * 64 if relation == "identical-input-control" or side == "baseline" else "b" * 64},
            "worldgen_artifact": {"sha256": "c" * 64},
            "strata_manifest": {"tile_set_sha256": fingerprint},
            "iteration_report": {"path": f"/{side}/{report_id}.json"},
        }
    summary = None if final_state == "unavailable" else {
        "changed_block_positions": changed,
        "height_changed_columns": 0,
        "biome_changed_columns": 0,
        "category_totals": categories or {},
    }
    status = "unstable" if changed and relation == "identical-input-control" else ("changed" if changed else "equivalent")
    return {
        "format": "workbench-worldgen-cockpit-report-v1",
        "schema_version": 1,
        "report_id": report_id,
        "mode": "fast",
        "status": status,
        "coverage": "complete-for-mode" if final_state != "unavailable" else "partial",
        "alignment": {"aligned": aligned, "seed": seed, "dimension_id": 0, "chunk_region": [-2, -2, 4, 4], "checks": checks},
        "sides": sides,
        "evidence": {
            "semantic": {"state": "observed-diagnostic", "equivalent": semantic_equivalent},
            "final_state": {"state": final_state, "summary": summary},
            "statistical": {"state": "observed-derived" if summary else "unavailable"},
            "subsurface": {"state": "unavailable"},
            "causal": {"state": "unavailable"},
            "performance": {"state": "unavailable"},
            "observer_overhead": {"state": "unavailable"},
        },
        "decision": {"comparison_kind": relation},
    }


class WorldgenQualifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile_path = ROOT / "profiles/packs/supersymmetry/worldgen/worldgen-qualification-profile-v1.json"
        cls.profile, cls.profile_binding = load_profile(cls.profile_path, root=ROOT)

    def _risk(self, directory: Path, *, unstable: bool = False, nested: bool = False):
        constants = (
            ("java/util/HashMap", "java/util/Random", "nextInt", "entrySet")
            if unstable
            else ("java/util/LinkedHashMap", "java/util/Random", "nextInt", "entrySet")
        )
        jar = _jar(directory / "fixture.jar", {"fixture/Generator.class": _class_bytes(*constants)}, nested=nested)
        return scan_jars([jar], limits=self.profile["limits"], dispositions=[])

    def _assess(self, entries, risk, *, suite="smoke", intent="development"):
        return assess_matrix(
            profile=self.profile,
            profile_binding=self.profile_binding,
            cockpit_entries=entries,
            suite_id=suite,
            intent_id=intent,
            risk_scan=risk,
            reproduction_command="fixture",
        )

    def test_profile_and_preview_are_explicit_and_bounded(self) -> None:
        schema = json.loads((ROOT / "modules/worldgen-qualifier/schemas/workbench-worldgen-qualification-profile-v1.schema.json").read_text())
        jsonschema.validate(json.loads(self.profile_path.read_text()), schema)
        out = io.StringIO()
        code = cli_run(["run", "--profile", "supersymmetry", "--show"], root=ROOT, output=out)
        self.assertEqual(0, code)
        rendered = out.getvalue()
        self.assertIn("2 fresh JVM/world launches", rendered)
        self.assertIn("static-risk-scan", rendered)
        self.assertNotIn("Supersymmetry is selected", rendered)

        release = io.StringIO()
        code = cli_run(["run", "--profile", "supersymmetry", "--suite", "release", "--intent", "release", "--show"], root=ROOT, output=release)
        self.assertEqual(0, code)
        self.assertIn("ATTENTION", release.getvalue())
        self.assertIn("capability.traversal-order", release.getvalue())
        self.assertIn("--allow-inconclusive", release.getvalue())

    def test_generic_unordered_rng_rule_finds_weighted_selector_shape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._risk(Path(raw), unstable=True)
        self.assertEqual("complete", report["coverage"])
        self.assertEqual(["unordered-rng-selection"], [row["rule_id"] for row in report["findings"]])
        self.assertEqual("unreviewed", report["findings"][0]["disposition"])

    def test_ordered_weighted_selector_does_not_match_unordered_rule(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report = self._risk(Path(raw), unstable=False)
        self.assertEqual([], report["findings"])

    def test_adversarial_rule_family_covers_entropy_order_io_async_gc_and_numeric_edges(self) -> None:
        classes = {
            "fixture/IdentityWorldGenerator.class": _class_bytes("java/util/IdentityHashMap", "iterator"),
            "fixture/EntropyWorldGenerator.class": _class_bytes("java/lang/System", "nanoTime"),
            "fixture/FilesystemWorldGenerator.class": _class_bytes("java/io/File", "listFiles"),
            "fixture/ReflectionWorldGenerator.class": _class_bytes("getDeclaredMethods"),
            "fixture/AsyncChunkGenerator.class": _class_bytes("java/util/concurrent/CompletableFuture"),
            "fixture/CacheWorldGenerator.class": _class_bytes("java/util/WeakHashMap", "iterator"),
            "fixture/HostWorldGenerator.class": _class_bytes("java/util/Locale", "getDefault"),
            "fixture/NumericWorldGenerator.class": _class_bytes("java/util/HashMap", "doubleValue", "reduce", "parallel"),
        }
        with tempfile.TemporaryDirectory() as raw:
            jar = _jar(Path(raw) / "edges.jar", classes)
            report = scan_jars([jar], limits=self.profile["limits"], dispositions=[])
        self.assertEqual(
            {
                "identity-order-dependence",
                "ambient-entropy",
                "filesystem-enumeration-order",
                "reflection-enumeration-order",
                "asynchronous-worldgen",
                "gc-sensitive-cache",
                "host-default-dependence",
                "nonassociative-parallel-reduction",
            },
            {row["rule_id"] for row in report["findings"]},
        )

    def test_static_dispositions_are_exact_jar_class_rule_tuples(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            jar = _jar(directory / "fixture.jar", {"fixture/Generator.class": _class_bytes("java/util/HashMap", "java/util/Random", "nextInt", "entrySet")})
            import hashlib
            digest = hashlib.sha256(jar.read_bytes()).hexdigest()
            disposition = [{"jar_sha256": digest, "class_name": "fixture.Generator", "rule_id": "unordered-rng-selection", "disposition": "accepted", "rationale": "fixture reviewed"}]
            accepted = scan_jars([jar], limits=self.profile["limits"], dispositions=disposition)
            jar.write_bytes(jar.read_bytes() + b"drift")
            # Appending after the ZIP end changes identity; the old exact
            # disposition cannot match even if a tolerant ZIP reader opens it.
            drifted = scan_jars([jar], limits=self.profile["limits"], dispositions=disposition)
            self.assertEqual("unreviewed", drifted["findings"][0]["disposition"])
        self.assertEqual("accepted", accepted["findings"][0]["disposition"])

    def test_unsafe_zip_member_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jar = Path(raw) / "unsafe.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("../escape.class", _class_bytes("fixture"))
            with self.assertRaisesRegex(QualifierError, "unsafe ZIP member"):
                scan_jars([jar], limits=self.profile["limits"], dispositions=[])

    def test_nested_or_malformed_byte_surface_is_partial_not_silently_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            jar = _jar(directory / "fixture.jar", {"fixture/Broken.class": b"not-a-class"}, nested=True)
            report = scan_jars([jar], limits=self.profile["limits"], dispositions=[])
        self.assertEqual("partial", report["coverage"])
        self.assertEqual(1, report["jars"][0]["malformed_class_count"])
        self.assertEqual(1, report["jars"][0]["nested_archive_count"])

    def test_exact_controls_receive_empirical_acceptance_in_smoke_scope(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            risk = self._risk(Path(raw))
            cockpit = _cockpit(report_id="control-clean")
            report = self._assess(
                [{"report": cockpit, "path": "control.json", "sha256": "1" * 64, "run_paths": ["a", "b"], "perturbations": {"kind": "within-cell", "seed": 42, "region": "-2,-2,4,4", "order": "baseline-first", "heap": "4096M"}}],
                risk,
            )
        self.assertEqual("accepted-empirical", report["status"])
        self.assertEqual("empirical-in-declared-matrix", report["assurance"])
        validate_qualification(report)
        schema = json.loads((ROOT / "modules/worldgen-qualifier/schemas/workbench-worldgen-qualification-report-v1.schema.json").read_text())
        jsonschema.validate(report, schema)

    def test_bop_like_vegetation_delta_is_critical_and_never_masked_as_noise(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            risk = self._risk(Path(raw), unstable=True)
            cockpit = _cockpit(report_id="control-bop", changed=1521, categories={"other-block": 1521})
            report = self._assess(
                [{"report": cockpit, "path": "bop.json", "sha256": "2" * 64, "run_paths": ["a", "b"], "perturbations": {}}],
                risk,
            )
        self.assertEqual("rejected-unstable-critical", report["status"])
        self.assertEqual("unstable", report["decision"]["required_domain_states"]["decoration"])
        self.assertNotIn("noise budget", json.dumps(report["decision"]))

    def test_cross_heap_fingerprint_divergence_rejects_even_when_each_pair_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            risk = self._risk(Path(raw))
            left = _cockpit(report_id="heap-left", fingerprint="a" * 64)
            right = _cockpit(report_id="heap-right", fingerprint="b" * 64)
            entries = [
                {"report": left, "path": "left.json", "sha256": "3" * 64, "run_paths": ["a", "b"], "perturbations": {"seed": 42, "region": "r", "order": "baseline-first", "heap": "3072M"}},
                {"report": right, "path": "right.json", "sha256": "4" * 64, "run_paths": ["c", "d"], "perturbations": {"seed": 42, "region": "r", "order": "candidate-first", "heap": "4096M"}},
            ]
            report = self._assess(entries, risk)
        self.assertEqual("rejected-unstable-critical", report["status"])
        self.assertIn("across perturbations", " ".join(report["decision"]["unstable_reasons"]))

    def test_stage_exact_intent_accepts_equal_checkpoints_and_rejects_transient_divergence(self) -> None:
        profile = deepcopy(self.profile)
        profile["capabilities"]["stage-checkpoints"] = "supported"
        profile["intents"]["development"]["required_capabilities"] = ["stage-checkpoints"]
        profile["intents"]["development"]["required_evidence"].append("causal")
        profile["intents"]["development"]["assurance"] = "stage-exact"
        with tempfile.TemporaryDirectory() as raw:
            risk = self._risk(Path(raw))
            equal = _cockpit(report_id="stage-equal")
            equal["evidence"]["causal"] = {"state": "evidence-closed", "summary": {"status": "equal"}}
            accepted = assess_matrix(profile=profile, profile_binding=self.profile_binding, cockpit_entries=[{"report": equal, "path": "equal.json", "sha256": "7" * 64, "run_paths": ["a", "b"], "perturbations": {}}], suite_id="smoke", intent_id="development", risk_scan=risk, reproduction_command="fixture")
            diverged = _cockpit(report_id="stage-diverged")
            diverged["evidence"]["causal"] = {"state": "evidence-closed", "summary": {"status": "diverged"}}
            rejected = assess_matrix(profile=profile, profile_binding=self.profile_binding, cockpit_entries=[{"report": diverged, "path": "diverged.json", "sha256": "8" * 64, "run_paths": ["c", "d"], "perturbations": {}}], suite_id="smoke", intent_id="development", risk_scan=risk, reproduction_command="fixture")
        self.assertEqual("accepted-exact", accepted["status"])
        self.assertEqual("rejected-unstable-critical", rejected["status"])
        self.assertTrue(any("stage.causal-checkpoints" in item for item in rejected["decision"]["unstable_reasons"]))

    def test_missing_final_evidence_and_changed_input_controls_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            risk = self._risk(Path(raw))
            missing = _cockpit(report_id="missing", final_state="unavailable")
            report = self._assess([{"report": missing, "path": "missing.json", "sha256": "5" * 64, "run_paths": ["a", "b"], "perturbations": {}}], risk)
            self.assertEqual("inconclusive", report["status"])
            changed_input = _cockpit(report_id="ab", relation="before-after")
            report = self._assess([{"report": changed_input, "path": "ab.json", "sha256": "6" * 64, "run_paths": ["c", "d"], "perturbations": {}}], risk)
        self.assertEqual("inconclusive", report["status"])
        self.assertTrue(any("not a byte-identical" in item for item in report["decision"]["blockers"]))

    def test_release_intent_exposes_unsupported_coverage_instead_of_accepting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            risk = self._risk(Path(raw))
            controls = []
            for index, seed in enumerate((1, 2, 3)):
                report = _cockpit(report_id=f"release-{index}", seed=seed)
                controls.append({"report": report, "path": f"{index}.json", "sha256": str(index + 1) * 64, "run_paths": [f"a{index}", f"b{index}"], "perturbations": {"seed": seed, "region": "r", "order": "baseline-first", "heap": "4096M"}})
            result = self._assess(controls, risk, suite="release", intent="release")
        self.assertEqual("inconclusive", result["status"])
        blockers = " ".join(result["decision"]["blockers"])
        self.assertIn("traversal-order", blockers)
        self.assertIn("scheduled-settle", blockers)
        self.assertIn("warm-cache-replay", blockers)


if __name__ == "__main__":
    unittest.main()
