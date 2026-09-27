"""Textual-facing Core steps for official fresh setup."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from workbench_core import pack_release_fresh_setup as fresh
from workbench_core.pack_release_curseforge import CurseForgeAccessUnavailable

from core.tests import test_pack_release_curseforge_composition as fixtures


class _SavedRelease:
    def __init__(self, fixture: fixtures.PackReleaseCurseForgeCompositionTests) -> None:
        self.fixture = fixture
        self.state_root = fixture.state
        self.choice_path = fixture.config / "pack-release-supersymmetry.json"
        self.available = True

    def inputs(self) -> dict:
        if not self.available:
            return {"status": "unavailable", "artifact_state": "missing",
                    "reason": "selected archive is missing", "input_plan": None}
        return {"status": "planned", "input_plan": self.fixture.input_plan,
                "selected": {"artifact_path": str(self.fixture.root / "release.zip")}}


class OfficialFreshReleaseServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.PackReleaseCurseForgeCompositionTests(
            methodName="test_compose_exact_official_payload_and_reopen",
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.saved = _SavedRelease(self.fixture)
        self.service = fresh.OfficialFreshReleaseService(
            self.saved, authority_path=self.fixture.root / "authority.json",
            layout_policy_path=self.fixture.layout_policy,
            resourcepack_policy_path=self.fixture.rp_policy,
        )

    def test_status_reports_selected_ids_and_offline_ready_bytes(self) -> None:
        policy = self.service.policy_status()
        self.assertEqual("ready", policy["status"])
        self.assertEqual("baseline", policy["source"])
        status = self.service.status(override_plan_id=self.fixture.override_plan_id)
        self.assertEqual("ready", status["status"])
        self.assertEqual("unavailable", status["provider_state"])
        self.assertEqual(3, status["selected_file_count"])
        self.assertEqual(3, status["ready_file_count"])
        self.assertEqual("ready", status["override_state"])
        self.assertEqual({(10, 100), (20, 200), (30, 300)}, {
            (row["project_id"], row["file_id"]) for row in status["files"]})
        reused = self.service.acquire_file(10, 100)
        self.assertEqual("reused", reused["outcome"])
        self.assertNotIn("artifact_path", reused)

    def test_missing_provider_refuses_new_download_before_acquisition(self) -> None:
        other = fresh.OfficialFreshReleaseService(
            self.saved, authority_path=self.fixture.root / "authority.json",
            layout_policy_path=self.fixture.layout_policy,
            resourcepack_policy_path=self.fixture.rp_policy,
            optional_selected=(),
        )
        status = other.status()
        self.assertEqual("pending", status["status"])
        self.assertEqual(2, status["selected_file_count"])
        self.assertEqual(0, status["ready_file_count"])
        with patch.object(fresh, "acquire_curseforge_file") as acquire:
            with self.assertRaises(CurseForgeAccessUnavailable):
                other.acquire_file(10, 100)
            acquire.assert_not_called()

    def test_provider_preflight_needs_no_prepared_release(self) -> None:
        with patch.object(self.saved, "inputs", side_effect=AssertionError(
                "provider preflight must not open the release")):
            self.assertEqual(
                {"status": "unavailable",
                 "reason": "Workbench CurseForge access is not configured"},
                self.service.provider_status(),
            )
            configured = fresh.OfficialFreshReleaseService(
                self.saved, authority_path=self.fixture.root / "authority.json",
                layout_policy_path=self.fixture.layout_policy,
                resourcepack_policy_path=self.fixture.rp_policy,
                credential_provider=lambda: "synthetic-app-key",
            )
            self.assertEqual(
                {"status": "configured", "reason": None},
                configured.provider_status(),
            )

    def test_injected_provider_is_only_passed_to_exact_file_acquisition(self) -> None:
        service = fresh.OfficialFreshReleaseService(
            self.saved, authority_path=self.fixture.root / "authority.json",
            layout_policy_path=self.fixture.layout_policy,
            resourcepack_policy_path=self.fixture.rp_policy,
            optional_selected=(), credential_provider=lambda: "synthetic-app-key",
        )
        with patch.object(fresh, "acquire_curseforge_file", return_value={
            "filename": "Alpha.jar", "size": 5, "sha256": "sha256:" + "a" * 64,
            "outcome": "downloaded",
        }) as acquire:
            result = service.acquire_file(10, 100)
        self.assertEqual("downloaded", result["outcome"])
        self.assertNotIn("synthetic-app-key", str(result))
        self.assertEqual("synthetic-app-key", acquire.call_args.kwargs["provider_key"])

    def test_publish_and_reopen_official_install_source(self) -> None:
        published = self.service.publish(override_plan_id=self.fixture.override_plan_id)
        self.assertEqual("ready", published["status"])
        self.assertEqual("official-release-curseforge",
                         published["composition_result"]["source_kind"])
        reopened = self.service.reopen_composition(
            override_plan_id=self.fixture.override_plan_id,
            expected_plan_id=published["composition_plan_id"],
        )
        self.assertEqual("reopened", reopened["composition_result"]["outcome"])
        self.assertEqual(published["composition_result"]["tree_id"],
                         reopened["composition_result"]["tree_id"])
        status = self.service.status(
            override_plan_id=self.fixture.override_plan_id,
            composition_plan_id=published["composition_plan_id"],
        )
        self.assertEqual("ready", status["composition_state"])
        self.saved.available = False
        recovered = self.service.reopen_by_plan_id(published["composition_plan_id"])
        self.assertEqual("ready", recovered["status"])
        self.assertEqual(published["composition_result"]["tree_id"],
                         recovered["composition_result"]["tree_id"])

    def test_missing_saved_release_is_reported_without_provider_request(self) -> None:
        self.saved.available = False
        with patch.object(self.service, "_provider_key") as provider:
            status = self.service.status()
        self.assertEqual("unavailable", status["status"])
        self.assertEqual([], status["files"])
        provider.assert_not_called()

    def test_override_retention_step_returns_stable_ids(self) -> None:
        with (patch.object(fresh, "plan_release_override_custody", return_value={
                  "plan_id": self.fixture.override_plan_id,
              }) as plan,
              patch.object(fresh, "apply_release_override_custody", return_value={
                  "tree_id": "workbench-tree-v1:" + "a" * 32,
                  "override_file_count": 1, "outcome": "reused",
              }) as apply):
            result = self.service.retain_overrides()
        self.assertEqual("ready", result["status"])
        self.assertEqual(self.fixture.override_plan_id, result["override_plan_id"])
        self.assertEqual("reused", result["outcome"])
        self.assertEqual(Path(self.fixture.root / "release.zip"),
                         plan.call_args.kwargs["archive_path"])
        self.assertEqual(self.fixture.override_plan_id,
                         apply.call_args.kwargs["expected_plan_id"])


if __name__ == "__main__":
    unittest.main()
