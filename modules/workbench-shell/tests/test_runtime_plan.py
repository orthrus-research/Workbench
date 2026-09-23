"""Focused tests for the useful read-only runtime planner."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_ROOT = (
    REPOSITORY_ROOT / "modules/project-intelligence/src"
)
sys.path.insert(0, str(PROJECT_INTELLIGENCE_ROOT))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell import (  # noqa: E402
    RuntimePlanError,
    build_runtime_plan,
)


CONTEXT = {
    "workspace": {
        "root_uri": "file:///workspace/supersymmetry",
        "revision": "1" * 40,
        "dirty": False,
        "dirty_entries": [],
    },
    "project": {
        "kind": "packwiz-modpack",
        "name": "Supersymmetry",
        "version": "test",
        "author": "SymmetricDevs",
        "pack_format": "packwiz:1.1.0",
        "manifest_sha256": "2" * 64,
        "minecraft_version": "1.12.2",
        "loaders": [{"id": "cleanroom", "version": "0.6.8-alpha"}],
        "index": {
            "file": "index.toml",
            "hash_format": "sha256",
            "declared_hash": "3" * 64,
            "actual_sha256": "3" * 64,
            "matches_declared_hash": True,
        },
        "matched_paths": ["pack.toml", "index.toml"],
    },
    "platform": {
        "profile_id": "workbench-platform:cleanroom:test",
        "status": "provisional",
        "minecraft_version": "1.12.2",
        "cleanroom_version": "0.6.8-alpha",
        "document_sha256": "4" * 64,
    },
    "pack": {
        "profile_family_id": "workbench-pack:supersymmetry",
        "display_name": "Supersymmetry",
        "status": "active",
        "selected_profile": "cleanroom-test",
        "maturity": "experimental",
        "platform_profile_id": "workbench-platform:cleanroom:test",
        "permitted_operations": ["observe", "construct"],
        "document_sha256": "5" * 64,
    },
}

PROFILE = {
    "java": {"runtime": "temurin-21.0.8+9"},
    "runtime_artifacts": {
        "packwiz_installer": {
            "url": "https://example.invalid/packwiz-installer.jar",
            "sha256": "6" * 64,
        },
        "cleanroom_client": {
            "url": "https://example.invalid/cleanroom-client.zip",
            "sha256": "7" * 64,
        },
        "cleanroom_server": {
            "url": "https://example.invalid/cleanroom-server.jar",
            "sha256": "8" * 64,
        },
    },
}

class RuntimePlanTest(unittest.TestCase):
    def test_ready_client_plan_is_deterministic_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_root = Path(temporary) / "state"
            first = build_runtime_plan(
                deepcopy(CONTEXT),
                deepcopy(PROFILE),
                state_root=state_root,
                side="client",
                launcher="prism",
            )
            second = build_runtime_plan(
                deepcopy(CONTEXT),
                deepcopy(PROFILE),
                state_root=state_root,
                side="client",
                launcher="prism",
            )

            self.assertEqual(first["state"], "ready")
            self.assertEqual(first["blockers"], [])
            self.assertEqual(first["plan_id"], second["plan_id"])
            self.assertEqual(first["request"]["launcher"], "prism")
            self.assertFalse(state_root.exists())
            self.assertEqual(
                first["plan_id"],
                "sha256:48f62ec16e530f5c6a6e632d6ce832a0"
                "ed5d9a451f5ed6c3cb0c954fe14aff67",
            )
            self.assertNotIn("configuration", first)
            self.assertNotIn("semantic_plan_id", first)

            schema = json.loads(
                (
                    MODULE_ROOT / "schemas/runtime-plan-v1.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(first)

    def test_blocked_server_plan_explains_real_missing_inputs(self) -> None:
        context = deepcopy(CONTEXT)
        context["workspace"]["dirty"] = True
        context["workspace"]["dirty_entries"] = [" M pack.toml"]
        context["project"]["loaders"] = [
            {"id": "forge", "version": "14.23.5.2860"}
        ]
        context["project"]["index"]["matches_declared_hash"] = False
        context["platform"]["cleanroom_version"] = "unresolved"
        profile = deepcopy(PROFILE)
        profile["java"]["runtime"] = "unresolved"
        profile["runtime_artifacts"]["packwiz_installer"] = {
            "url": "unresolved",
            "sha256": "unresolved",
        }
        profile["runtime_artifacts"]["cleanroom_server"] = {
            "url": "unresolved",
            "sha256": "unresolved",
        }

        with tempfile.TemporaryDirectory() as temporary:
            plan = build_runtime_plan(
                context,
                profile,
                state_root=Path(temporary) / "state",
                side="server",
            )

        self.assertEqual(plan["state"], "blocked")
        self.assertEqual(
            plan["request"]["launcher"],
            "dedicated-server",
        )
        self.assertEqual(
            {blocker["id"] for blocker in plan["blockers"]},
            {
                "cleanroom-version-unresolved",
                "runtime-java-unresolved",
                "packwiz-installer-unresolved",
                "cleanroom-server-unresolved",
            },
        )
        self.assertTrue(
            any(
                "disposable tracked-file copy" in warning
                for warning in plan["warnings"]
            )
        )
        self.assertTrue(
            any("legacy loader" in warning for warning in plan["warnings"])
        )

    def test_invalid_side_and_launcher_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimePlanError, "server launcher"):
                build_runtime_plan(
                    deepcopy(CONTEXT),
                    deepcopy(PROFILE),
                    state_root=Path(temporary),
                    side="server",
                    launcher="prism",
                )


if __name__ == "__main__":
    unittest.main()
