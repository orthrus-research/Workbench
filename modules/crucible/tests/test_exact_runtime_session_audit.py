#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from typing import Any
import unittest
from unittest import mock


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_crucible_observatory import (  # noqa: E402
    CaptureValidationError,
    canonical_json_bytes,
    canonical_json_sha256,
)
from workbench_crucible_observatory.cleanroom_matrix import (  # noqa: E402
    FIXED_WORLD_SEED,
    FIXED_WORLD_SEED_SHA256,
    FORWARD_ROUTE,
    FORWARD_ROUTE_SHA256,
    REVERSE_ROUTE,
    REVERSE_ROUTE_SHA256,
    SELECTOR_SHA256,
    load_fixture_result,
)


TOOL_PATH = MODULE_ROOT / "tools" / "audit_exact_runtime_session.py"
SPEC = importlib.util.spec_from_file_location("exact_runtime_session_audit_test_module", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: str) -> str:
    return digest_bytes(value.encode("utf-8"))


def file_digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


class ExactRuntimeSessionAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.candidate = self.root / "candidate-lock.json"
        self.candidate.write_bytes(canonical_json_bytes({"candidate": "exact-cleanroom"}) + b"\n")
        self.fixture = self.root / "fixture.jar"
        self.fixture.write_bytes(b"exact-fixture-jar")
        self.runtime_source_sha = digest("exact-cleanroom-runtime")
        self.fixture_sha = file_digest(self.fixture)
        self.mod_manifest = self.root / "installed-mod-set.json"
        self.config_manifest = self.root / "configuration-set.json"
        self.output = self.root / "receipts" / "session.json"

    def fixture_document(self, route_order: str) -> dict[str, Any]:
        route = FORWARD_ROUTE if route_order == "forward" else REVERSE_ROUTE
        return {
            "schema": "workbench.worldgen-observatory.fixture-result.v1",
            "fixture": "dedicated_server_fixed_region_v1",
            "completion_state": "complete",
            "save_state": "flushed",
            "shutdown_state": "requested",
            "world_seed_sha256": FIXED_WORLD_SEED_SHA256,
            "dimension": 0,
            "route_order": route_order,
            "selector_sha256": SELECTOR_SHA256,
            "route_sha256": FORWARD_ROUTE_SHA256 if route_order == "forward" else REVERSE_ROUTE_SHA256,
            "selected_chunks": [
                {
                    "chunk_x": chunk_x,
                    "chunk_z": chunk_z,
                    "semantic_state_sha256": digest(f"semantic:{chunk_x}:{chunk_z}"),
                }
                for chunk_x, chunk_z in route
            ],
            "runtime_mod_inventory": [
                {
                    "mod_id": "cleanroom",
                    "source_sha256": self.runtime_source_sha,
                    "mod_class_name": "com.cleanroommc.CleanroomMod",
                },
                {
                    "mod_id": "workbench_fixture",
                    "source_sha256": self.fixture_sha,
                    "mod_class_name": "dev.workbench.fixture.FixtureMod",
                },
            ],
        }

    def build_case(
        self,
        case_id: str,
        *,
        outcome: str,
        observer_enabled: bool,
        route_order: str = "forward",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        case = self.root / case_id
        server = case / "server"
        (server / "config").mkdir(parents=True)
        (server / "mods").mkdir()
        (server / "CLASS_DUMP" / "1").mkdir(parents=True)
        (server / "server.properties").write_text("level-type=wb_observe\n", encoding="utf-8")
        (server / "config" / "fixture.cfg").write_text("enabled=true\n", encoding="utf-8")
        (server / "mods" / "fixture.jar").write_bytes(self.fixture.read_bytes())

        candidate_sha = file_digest(self.candidate)
        mod_manifest = {
            "format": audit.INSTALLED_MOD_SET_FORMAT,
            "candidate_lock_sha256": candidate_sha,
            "runtime_class_source_sha256": self.runtime_source_sha,
            "installed_artifacts": [
                {"relative_path": "mods/fixture.jar", "sha256": self.fixture_sha}
            ],
        }
        self.mod_manifest.write_bytes(canonical_json_bytes(mod_manifest) + b"\n")

        capture_id = f"capture-{case_id}" if observer_enabled else None
        settings = {
            "foundation_dump": True,
            "main_class": "com.cleanroommc.boot.MainServer",
            "fixture_driver_enabled": True,
            "fixture_expected_seed": str(FIXED_WORLD_SEED),
            "fixture_expected_seed_sha256": FIXED_WORLD_SEED_SHA256,
            "fixture_route_order": route_order,
            "fixture_result_relative_path": "fixture-result.json",
            "probe_enabled": observer_enabled,
            "probe_capture_id": capture_id,
            "probe_mode": "lossless-fixture" if observer_enabled else None,
            "probe_output_relative_path": "raw.ndjson" if observer_enabled else None,
        }
        config_manifest = {
            "format": audit.CONFIGURATION_FORMAT,
            "case_id": case_id,
            "files": [
                {
                    "relative_path": "config/fixture.cfg",
                    "sha256": file_digest(server / "config" / "fixture.cfg"),
                },
                {
                    "relative_path": "server.properties",
                    "sha256": file_digest(server / "server.properties"),
                },
            ],
            "runtime_settings": settings,
        }
        self.config_manifest.write_bytes(canonical_json_bytes(config_manifest) + b"\n")

        result_sha: str | None = None
        inventory_sha: str | None = None
        semantic_sha: str | None = None
        if outcome == "completed":
            result = case / "fixture-result.json"
            result.write_bytes(canonical_json_bytes(self.fixture_document(route_order)) + b"\n")
            admitted = load_fixture_result(result)
            result_sha = admitted.artifact_sha256
            inventory_sha = admitted.inventory_sha256()
            semantic_sha = admitted.semantic_map_sha256()

        raw_sha: str | None = None
        if observer_enabled:
            raw_rows: list[dict[str, Any]] = [
                {
                    "record_type": "capture_control",
                    "sequence": 0,
                    "capture_id": capture_id,
                    "payload": {
                        "control": "start",
                        "requested_mode": "lossless-fixture",
                        "world_seed_sha256": FIXED_WORLD_SEED_SHA256,
                        "route_order": route_order,
                    },
                }
            ]
            if outcome == "completed":
                raw_rows.extend(
                    [
                        {
                            "record_type": "fixture_driver",
                            "sequence": 1,
                            "capture_id": capture_id,
                            "payload": {
                                "action": "complete",
                                "completion_marker": "dedicated_server_fixture_complete_v1",
                                "save_state": "flushed",
                                "route_order": route_order,
                                "result_identity_sha256": result_sha,
                            },
                        },
                        {
                            "record_type": "capture_control",
                            "sequence": 2,
                            "capture_id": capture_id,
                            "payload": {
                                "control": "stop",
                                "completion_state": "complete",
                                "open_span_count": 0,
                                "open_write_count": 0,
                            },
                        },
                    ]
                )
            else:
                raw_rows.append(
                    {
                        "record_type": "block_write",
                        "sequence": 1,
                        "capture_id": capture_id,
                        "payload": {"position_x": 1028},
                    }
                )
            raw = case / "raw.ndjson"
            raw.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in raw_rows))
            raw_sha = file_digest(raw)

        properties = [
            "-Djava.awt.headless=true",
            "-Dfoundation.dump=true",
            "-Dworkbench.worldgen.observatory.fixture_driver.enabled=true",
            f"-Dworkbench.worldgen.observatory.fixture_driver.order={route_order}",
            f"-Dworkbench.worldgen.observatory.fixture_driver.expected_seed={FIXED_WORLD_SEED}",
            f"-Dworkbench.worldgen.observatory.fixture_driver.result={case / 'fixture-result.json'}",
            f"-Dworkbench.worldgen.observatory.probe.enabled={'true' if observer_enabled else 'false'}",
        ]
        if observer_enabled:
            properties.extend(
                [
                    f"-Dworkbench.worldgen.observatory.probe.capture_id={capture_id}",
                    "-Dworkbench.worldgen.observatory.probe.mode=lossless-fixture",
                    f"-Dworkbench.worldgen.observatory.probe.output={case / 'raw.ndjson'}",
                ]
            )
        java_line = audit._JAVA_OPTIONS_PREFIX + " ".join(properties)
        if outcome == "completed":
            terminal = "BUILD SUCCESSFUL in 1s"
        else:
            terminal = (
                "> Process 'command '/exact/java'' finished with non-zero exit value 137 "
                "(the saved log does not establish a signal)\nBUILD FAILED in 1s"
            )
        launch = case / "launch.log"
        launch.write_text(java_line + "\nconfiguration\n" + java_line + "\n" + terminal + "\n", encoding="utf-8")

        arguments = {
            "case_id": case_id,
            "outcome": outcome,
            "case_directory": case,
            "candidate_lock": self.candidate,
            "expected_candidate_lock_sha256": candidate_sha,
            "fixture_artifact": self.fixture,
            "expected_fixture_artifact_sha256": self.fixture_sha,
            "installed_mod_set_manifest": self.mod_manifest,
            "expected_installed_mod_set_sha256": canonical_json_sha256(mod_manifest),
            "configuration_set_manifest": self.config_manifest,
            "expected_configuration_set_sha256": canonical_json_sha256(config_manifest),
            "expected_launch_log_sha256": file_digest(launch),
            "expected_raw_sha256": raw_sha,
            "expected_result_sha256": result_sha,
            "expected_runtime_inventory_sha256": inventory_sha,
            "expected_semantic_result_sha256": semantic_sha,
            "output_path": self.output,
        }
        return arguments, {"case": case, "launch": launch, "settings": settings}

    def dump_receipt(self) -> SimpleNamespace:
        return SimpleNamespace(
            format="workbench-foundation-class-dump-manifest-v1",
            class_count=3305,
            total_size_bytes=14_400_000,
            manifest_sha256=digest("whole-foundation-dump"),
        )

    def run_audit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with mock.patch.object(
            audit,
            "build_foundation_class_dump_manifest",
            return_value=self.dump_receipt(),
        ):
            return audit.audit_session(**arguments)

    def test_completed_observer_on_binds_all_exact_inputs_and_result_digests(self) -> None:
        arguments, _ = self.build_case("aa-1", outcome="completed", observer_enabled=True)
        receipt = self.run_audit(arguments)

        self.assertEqual(receipt["outcome"], "completed")
        self.assertTrue(receipt["observer_enabled"])
        self.assertEqual(receipt["raw_capture"]["terminal_state"], "complete_and_stopped")
        self.assertEqual(receipt["fixture_result"]["save_state"], "flushed")
        self.assertEqual(
            receipt["fixture_result"]["runtime_inventory_sha256"],
            arguments["expected_runtime_inventory_sha256"],
        )
        self.assertEqual(receipt["foundation_class_dump"]["class_count"], 3305)
        written = json.loads(self.output.read_text(encoding="utf-8"))
        material = dict(written)
        audit_id = material.pop("audit_id")
        self.assertEqual(audit_id, audit.RECEIPT_PREFIX + canonical_json_sha256(material))

    def test_completed_observer_off_requires_raw_absence(self) -> None:
        arguments, context = self.build_case(
            "observer-off", outcome="completed", observer_enabled=False
        )
        receipt = self.run_audit(arguments)
        self.assertFalse(receipt["observer_enabled"])
        self.assertIsNone(receipt["raw_capture"])

        (context["case"] / "raw.ndjson").write_text("unexpected\n", encoding="utf-8")
        with self.assertRaisesRegex(CaptureValidationError, "unexpectedly produced"):
            self.run_audit(arguments)

    def test_crash_records_textual_child_exit_without_signal_claim(self) -> None:
        arguments, _ = self.build_case(
            "crash-before-seal", outcome="crash", observer_enabled=True
        )
        receipt = self.run_audit(arguments)

        self.assertEqual(receipt["raw_capture"]["terminal_state"], "incomplete_without_stop")
        self.assertIsNone(receipt["fixture_result"])
        gradle = receipt["launch_log"]["gradle"]
        self.assertEqual(gradle["child_exit_code_from_text"], 137)
        self.assertEqual(gradle["provenance"], "launch_log_text_only")
        self.assertNotIn("signal", gradle)

    def test_rejects_duplicate_manifest_keys_and_unlisted_configuration(self) -> None:
        arguments, context = self.build_case("aa-2", outcome="completed", observer_enabled=True)
        encoded = self.config_manifest.read_text(encoding="utf-8")
        self.config_manifest.write_text(
            encoded.replace("{", '{"format":"duplicate",', 1), encoding="utf-8"
        )
        with self.assertRaisesRegex(CaptureValidationError, "duplicate JSON key"):
            self.run_audit(arguments)

        arguments, context = self.build_case("aa-3", outcome="completed", observer_enabled=True)
        (context["case"] / "server" / "config" / "unlisted.cfg").write_text(
            "hidden=true\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(CaptureValidationError, "does not exactly inventory"):
            self.run_audit(arguments)

    def test_rejects_differing_java_options_lines_even_when_log_hash_is_pinned(self) -> None:
        arguments, context = self.build_case(
            "order-reverse", outcome="completed", observer_enabled=True, route_order="reverse"
        )
        launch = context["launch"]
        text = launch.read_text(encoding="utf-8")
        second = text.rfind(audit._JAVA_OPTIONS_PREFIX)
        text = text[:second] + text[second:].replace("probe.enabled=true", "probe.enabled=false", 1)
        launch.write_text(text, encoding="utf-8")
        arguments["expected_launch_log_sha256"] = file_digest(launch)
        with self.assertRaisesRegex(CaptureValidationError, "evidence lines differ"):
            self.run_audit(arguments)


if __name__ == "__main__":
    unittest.main()
