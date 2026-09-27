"""The published pack and the developer branch are separate profile resources."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tomllib
import unittest
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[5]
for source in (ROOT / "api/src", ROOT / "profiles/packs/supersymmetry/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_profile_supersymmetry import profile  # noqa: E402


class ReleaseAuthorityTests(unittest.TestCase):
    def test_packaged_release_resource_is_schema_valid_and_distinct_from_branch_source(self) -> None:
        owner = profile()
        descriptor_path = owner.resource("release-authority")
        self.assertEqual("release-authority-v1.json", descriptor_path.name)
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        schema = json.loads(
            (owner.root / "schemas/workbench-supersymmetry-release-authority-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(descriptor)

        release = descriptor["baseline_release"]
        asset = release["client_asset"]
        match = re.fullmatch(descriptor["release_policy"]["client_asset_name_pattern"], asset["name"])
        self.assertIsNotNone(match)
        self.assertEqual(release["tag"], match.group("tag"))
        self.assertEqual(asset["name"], Path(urlparse(asset["url"]).path).name)
        self.assertIn(f"/releases/download/{release['tag']}/", asset["url"])
        self.assertFalse(re.fullmatch(
            descriptor["release_policy"]["client_asset_name_pattern"],
            f"supersymmetry-{release['tag']}-server.zip",
        ))

        acquisition = json.loads(owner.resource("acquisition").read_text(encoding="utf-8"))
        self.assertEqual("acquisition-v1.json", descriptor["developer_source"]["acquisition_profile"])
        self.assertEqual("refs/heads/master-ceu", acquisition["channels"][0]["remote_ref"])
        self.assertTrue(all("source_lock" not in row for row in acquisition["channels"]))

        package = tomllib.loads((owner.root / "pyproject.toml").read_text(encoding="utf-8"))
        bundled = package["tool"]["setuptools"]["package-data"][
            "workbench_resources.profiles.packs.supersymmetry"
        ]
        self.assertIn("release-authority-v1.json", bundled)
        self.assertIn("schemas/workbench-supersymmetry-release-authority-v1.schema.json", bundled)

    def test_local_client_input_policy_is_pack_owned_and_packaged(self) -> None:
        owner = profile()
        policy_path = owner.resource("release-local-input-policy")
        self.assertEqual("release-local-input-policy-v1.json", policy_path.name)
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        schema = json.loads(
            (owner.root / "schemas/workbench-supersymmetry-release-local-input-policy-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(policy)
        self.assertEqual("mods", policy["destination_root"])
        self.assertEqual("explicit", policy["optional_selection"])
        bundled = tomllib.loads((owner.root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["workbench_resources.profiles.packs.supersymmetry"]
        self.assertIn("runtime/release-local-input-policy-v1.json", bundled)
        self.assertIn("schemas/workbench-supersymmetry-release-local-input-policy-v1.schema.json", bundled)

    def test_resourcepack_placement_is_exact_selected_release_and_packaged(self) -> None:
        owner = profile()
        policy_path = owner.resource("release-resourcepack-input-policy")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        schema = json.loads(
            (owner.root / "schemas/workbench-supersymmetry-release-resourcepack-input-policy-v1.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(policy)
        self.assertEqual("0.1.16.16", policy["version"])
        self.assertEqual("workbench-pack-release-input-plan:sha256:8c188e58c33556c6dae0a5d7de8a069054bc0f45d3bccb6607adca9850673f0f", policy["input_plan_id"])
        self.assertEqual("sha256:a4d67c7961e281c6ba9ecfffa58c9de2d14f3a424c9498537166dd6687b04d7d", policy["manifest_sha256"])
        self.assertEqual({(851152, 6655846), (885673, 6280168), (1290857, 6927766)},
                         {(row["project_id"], row["file_id"]) for row in policy["placements"]})
        self.assertTrue(all(row["destination_root"] == "resourcepacks" and row["required"] is True
                            for row in policy["placements"]))
        bundled = tomllib.loads((owner.root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["workbench_resources.profiles.packs.supersymmetry"]
        self.assertIn("runtime/release-resourcepack-input-policy-v1.json", bundled)
        self.assertIn("schemas/workbench-supersymmetry-release-resourcepack-input-policy-v1.schema.json", bundled)


if __name__ == "__main__":
    unittest.main()
