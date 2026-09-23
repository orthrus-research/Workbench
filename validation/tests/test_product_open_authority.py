"""Authority-backed smoke test for opening a Cleanroom workspace."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from validation.tests.test_product_smoke import _run_json


class ProductOpenAuthorityTests(unittest.TestCase):
    def test_open_projects_a_cleanroom_workspace_into_developer_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "example-mod"
            (project / "src/main/java/example").mkdir(parents=True)
            (project / "settings.gradle").write_text(
                "rootProject.name = 'example-mod'\n", encoding="utf-8"
            )
            (project / "build.gradle").write_text(
                "plugins { id 'com.cleanroommc.gradle' version '0.3.1' }\n",
                encoding="utf-8",
            )
            (project / "gradle.properties").write_text(
                "minecraft_version=1.12.2\ncleanroom_version=0.3-alpha\n",
                encoding="utf-8",
            )
            (project / "src/main/java/example/Example.java").write_text(
                "package example;\n", encoding="utf-8"
            )

            result = _run_json("open", str(project), "--json")

            self.assertEqual("workbench-workspace-home-v2", result["format"])
            self.assertEqual("cleanroom-mod", result["workspace"]["kind"])
            self.assertFalse(
                result["base_home"]["context"]["profile"]["support_claimed"]
            )
            owner_actions = {
                row["id"]: row for row in result["base_home"]["actions"]
            }
            self.assertTrue(owner_actions["search-workspace"]["available"])
            jobs = {row["id"]: row for row in result["jobs"]}
            self.assertEqual("available", jobs["search-workspace"]["state"])
            self.assertEqual([], jobs["search-workspace"]["blockers"])
            self.assertEqual(
                "unavailable", jobs["run-development-client"]["state"]
            )
            self.assertEqual(
                ["EXACT_RUN_PROFILE_REQUIRED"],
                jobs["run-development-client"]["blockers"],
            )


if __name__ == "__main__":
    unittest.main()
