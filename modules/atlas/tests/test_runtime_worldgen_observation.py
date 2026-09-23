"""Focused tests for Atlas-owned retained worldgen normalization."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_atlas.runtime_worldgen_observation import (  # noqa: E402
    AtlasWorldgenObservationError,
    _EvidenceIndex,
    _class_literal_states,
    observe_runtime_worldgen,
)


def _profile() -> dict[str, object]:
    return {
        "format": "workbench-runtime-worldgen-audit-profile-v1",
        "schema_version": 1,
        "profile_id": "workbench-pack:test:worldgen-v1",
        "discovery": {
            "script_globs": [],
            "biome_report_globs": [
                "config/BiomeTweaker/output/biome/*.json"
            ],
            "log_globs": [],
            "configuration_literal_audits": [],
            "artifact_inventory": [],
            "source_alignment_paths": [],
        },
        "selector_expectations": [],
        "known_non_overworld_biomes": [],
        "expected_event_reports": [],
        "guidance": {},
    }


class RuntimeWorldgenObservationTest(unittest.TestCase):
    def test_unsupported_zip_compression_becomes_audit_error(self) -> None:
        jar = BytesIO()
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("example/Setting.class", b"ignored")

        with patch.object(
            zipfile.ZipFile,
            "read",
            side_effect=NotImplementedError("unsupported compression"),
        ):
            with self.assertRaisesRegex(
                AtlasWorldgenObservationError,
                "cannot inspect runtime artifact classes",
            ):
                _class_literal_states(
                    Path("example.jar"),
                    jar.getvalue(),
                    ["patchBiome"],
                )

    def test_class_literal_matching_uses_constant_pool_equality(self) -> None:
        constant = b"notExactlypatchBiomeSuffix"
        class_payload = (
            b"\xca\xfe\xba\xbe"
            + b"\x00\x00\x00\x34"
            + b"\x00\x02"
            + b"\x01"
            + len(constant).to_bytes(2, "big")
            + constant
        )
        jar = BytesIO()
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("example/Setting.class", class_payload)

        self.assertEqual(
            _class_literal_states(
                Path("example.jar"),
                jar.getvalue(),
                ["patchBiome"],
            ),
            {"patchBiome": "not-observed"},
        )

    def test_evidence_index_enforces_aggregate_byte_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"abc")
            second.write_bytes(b"def")
            evidence = _EvidenceIndex(maximum_total_bytes=5)

            evidence.add(first, "test-evidence", 4)
            with self.assertRaisesRegex(
                AtlasWorldgenObservationError,
                "aggregate byte audit limit",
            ):
                evidence.add(second, "test-evidence", 4)

    def test_empty_evidence_is_insufficient_and_atlas_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            runtime = root / "runtime"
            workspace.mkdir()
            runtime.mkdir()

            result = observe_runtime_worldgen(
                workspace,
                runtime_root=runtime,
                profile=_profile(),
            )

            self.assertEqual(result["state"], "insufficient-evidence")
            self.assertEqual(result["authority"], {
                "classification": "atlas-derived-observation",
                "normative": False,
                "atlas_publication": False,
                "sentinel_policy_finding": False,
            })
            self.assertEqual(result["evidence"], [])

    def test_event_report_absence_is_not_treated_as_event_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            runtime = root / "runtime"
            logs = runtime / "logs"
            workspace.mkdir()
            logs.mkdir(parents=True)
            (logs / "latest.log").write_text(
                "[00:00:00] [main/INFO] [example]: Client initialized\n",
                encoding="utf-8",
            )
            profile = _profile()
            profile["discovery"]["log_globs"] = ["logs/*.log"]
            profile["expected_event_reports"] = [{
                "reporter": "dimstack",
                "generator": "example.Generator",
                "event": "example.Event",
            }]

            result = observe_runtime_worldgen(
                workspace,
                runtime_root=runtime,
                profile=profile,
            )

            self.assertTrue(any(
                "not proof that the expected event fired" in limitation
                for limitation in result["limitations"]
            ))
            self.assertTrue(any(
                "do not evidence RTG world entry" in limitation
                for limitation in result["limitations"]
            ))

    def test_missing_configuration_audit_is_inconclusive_and_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            runtime = root / "runtime"
            logs = runtime / "logs"
            workspace.mkdir()
            logs.mkdir(parents=True)
            (logs / "latest.log").write_text(
                "[00:00:00] [main/INFO] [example]: initialized\n",
                encoding="utf-8",
            )
            profile = _profile()
            profile["discovery"]["log_globs"] = ["logs/*.log"]
            profile["discovery"]["configuration_literal_audits"] = [{
                "config_path": "config/missing.cfg",
                "artifact_glob": "mods/*.jar",
                "keys": ["flag"],
            }]

            result = observe_runtime_worldgen(
                workspace,
                runtime_root=runtime,
                profile=profile,
            )

            self.assertEqual(result["state"], "inconclusive")
            self.assertTrue(any(
                "configuration binding could not be evaluated" in limitation
                for limitation in result["limitations"]
            ))

    def test_configuration_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            runtime = root / "runtime"
            config = runtime / "config"
            workspace.mkdir()
            config.mkdir(parents=True)
            outside = root / "outside.cfg"
            outside.write_text("B:flag=true\n", encoding="utf-8")
            (config / "test.cfg").symlink_to(outside)
            profile = _profile()
            profile["discovery"]["configuration_literal_audits"] = [{
                "config_path": "config/test.cfg",
                "artifact_glob": "mods/*.jar",
                "keys": ["flag"],
            }]

            with self.assertRaisesRegex(
                AtlasWorldgenObservationError,
                "config_path is a symbolic link",
            ):
                observe_runtime_worldgen(
                    workspace,
                    runtime_root=runtime,
                    profile=profile,
                )

    def test_invalid_dictionary_types_fail_before_schema_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            reports = (
                root
                / "runtime/config/BiomeTweaker/output/biome"
            )
            workspace.mkdir()
            reports.mkdir(parents=True)
            (reports / "invalid.json").write_text(
                json.dumps({
                    "Resource Location": "example:invalid",
                    "Dictionary Types": ["NETHER", "NETHER"],
                    "BiomeManager Entries": {"WARM Weights": [1]},
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                AtlasWorldgenObservationError,
                "unsupported shape",
            ):
                observe_runtime_worldgen(
                    workspace,
                    runtime_root=root / "runtime",
                    profile=_profile(),
                )

    def test_negative_weight_fails_before_derivation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            reports = (
                root
                / "runtime/config/BiomeTweaker/output/biome"
            )
            workspace.mkdir()
            reports.mkdir(parents=True)
            (reports / "invalid.json").write_text(
                json.dumps({
                    "Resource Location": "example:invalid",
                    "Dictionary Types": [],
                    "BiomeManager Entries": {
                        "WARM Weights": [-1],
                    },
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                AtlasWorldgenObservationError,
                "weights are invalid",
            ):
                observe_runtime_worldgen(
                    workspace,
                    runtime_root=root / "runtime",
                    profile=_profile(),
                )


if __name__ == "__main__":
    unittest.main()
