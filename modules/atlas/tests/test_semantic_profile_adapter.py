from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from workbench_api.profile_extensions import ProfileExtension
from workbench_atlas_projection import cli


class SemanticProfileAdapterTests(unittest.TestCase):
    def invoke(self, profile="example"):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = cli.main(["check", "--profile", profile, "--json"], root=Path("/explicit/resources"))
        return status, output.getvalue(), errors.getvalue()

    def test_missing_broken_duplicate_and_incompatible_profile_return_actionable_failure(self):
        cases = (
            (),
            (ProfileExtension("example", "unavailable", reason="required dependency disabled"),),
            (ProfileExtension("example", "available"), ProfileExtension("example", "available")),
            (ProfileExtension("example", "available", SimpleNamespace(PROFILE_API_VERSION=2)),),
        )
        for rows in cases:
            with self.subTest(rows=rows), patch(
                "workbench_api.profile_extensions.profile_extensions", return_value=rows
            ):
                status, output, errors = self.invoke()
                self.assertEqual(2, status)
                self.assertEqual("", output)
                self.assertIn("example", errors)
                self.assertNotIn("Traceback", errors)

    def test_domain_version_and_required_operations_are_explicit(self):
        for adapter in (
            SimpleNamespace(),
            SimpleNamespace(SEMANTIC_PROJECTION_API_VERSION=2),
            SimpleNamespace(SEMANTIC_PROJECTION_API_VERSION=True),
            SimpleNamespace(SEMANTIC_PROJECTION_API_VERSION=1, run_acceptance_gate=lambda: None),
        ):
            with self.subTest(adapter=adapter), patch.object(
                cli, "require_profile_extension", return_value=adapter
            ):
                status, _, errors = self.invoke()
                self.assertEqual(2, status)
                self.assertIn("semantic projection API 1", errors)

    def test_explicit_profile_adapter_does_not_change_import_paths_or_choose_a_pack(self):
        gate = Mock(return_value={"summary": {"status": "pass"}, "fixtures": []})
        adapter = SimpleNamespace(
            SEMANTIC_PROJECTION_API_VERSION=1,
            run_acceptance_gate=gate,
            build_fixture_projection=lambda root, name: None,
        )
        paths = list(sys.path)
        with patch.object(cli, "require_profile_extension", return_value=adapter) as resolve:
            status, _, errors = self.invoke("another-profile")
        self.assertEqual((0, ""), (status, errors))
        self.assertEqual(paths, sys.path)
        resolve.assert_called_once_with("workbench.semantic_projections", "another-profile")
        gate.assert_called_once_with(
            Path("/explicit/resources"), fixture_names=None, include_next=False
        )


if __name__ == "__main__":
    unittest.main()
