from __future__ import annotations

from hashlib import sha1
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator


MODULE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = MODULE_ROOT.parents[1]
PROJECT_INTELLIGENCE_SOURCE = REPOSITORY_ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(PROJECT_INTELLIGENCE_SOURCE))
sys.path.insert(0, str(MODULE_ROOT / "src"))

from workbench_shell import cli  # noqa: E402
from workbench_core.configuration import load_workbench_configuration  # noqa: E402
from workbench_core.manual_artifacts import (  # noqa: E402
    inspect_manual_artifacts,
    load_manual_artifact_profile,
    manual_artifact_profile_for_configuration,
)


class ManualArtifactPreflightTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, dict[str, object], bytes]:
        workspace = root / "workspace"
        (workspace / "mods").mkdir(parents=True)
        payload = b"publisher-hosted fixture\n"
        digest = sha1(payload).hexdigest()
        metadata = workspace / "mods/restricted.pw.toml"
        metadata.write_text(
            """name = "Restricted Fixture"
filename = "restricted.jar"
side = "both"

[download]
hash-format = "sha1"
hash = "{digest}"
mode = "metadata:curseforge"

[update.curseforge]
project-id = 123
file-id = 456
""".format(digest=digest),
            encoding="utf-8",
        )
        profile = {
            "format": "workbench-manual-artifact-profile-v1",
            "schema_version": 1,
            "profile_id": "workbench-pack:fixture:manual-artifacts-v1",
            "project_id": "fixture",
            "seed_layout": "minecraft-root",
            "artifacts": [
                {
                    "id": "restricted-fixture",
                    "display_name": "Restricted Fixture",
                    "metadata_path": "mods/restricted.pw.toml",
                    "filename": "restricted.jar",
                    "output_path": "mods/restricted.jar",
                    "hash_format": "sha1",
                    "hash": digest,
                    "project_id": 123,
                    "file_id": 456,
                    "download_page_url": "https://www.curseforge.com/minecraft/mc-mods/fixture/files/456",
                }
            ],
        }
        profile_path = root / "manual.json"
        profile_path.write_text(json.dumps(profile) + "\n", encoding="utf-8")
        return workspace, profile_path, profile, payload

    def test_checked_in_profile_validates_and_is_selected_by_pack_authority(self) -> None:
        profile_path = (
            REPOSITORY_ROOT
            / "profiles/packs/supersymmetry/runtime/manual-artifacts-v1.json"
        )
        schema = json.loads(
            (
                REPOSITORY_ROOT
                / "profiles/packs/supersymmetry/schemas/workbench-manual-artifact-profile-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        value = load_manual_artifact_profile(profile_path)
        Draft202012Validator(schema).validate(value)
        configuration = load_workbench_configuration(REPOSITORY_ROOT)
        self.assertEqual(
            profile_path.resolve(),
            manual_artifact_profile_for_configuration(configuration),
        )
        self.assertEqual(3, len(value["artifacts"]))

        migration = json.loads(
            (REPOSITORY_ROOT / "MIGRATION-MANIFEST.json").read_text(encoding="utf-8")
        )
        groups = [
            row
            for row in migration["imports"]
            if row["id"]
            == "workbench-mvp-supersymmetry-acquisition-and-manual-payload-2026-08-31"
        ]
        self.assertEqual(1, len(groups))
        source_artifacts = {
            (row["project_id"], row["file_id"], row["filename"], row["sha1"])
            for row in groups[0]["source_artifacts"]
        }
        declared_artifacts = {
            (
                row["project_id"],
                row["file_id"],
                row["filename"],
                row["hash"],
            )
            for row in value["artifacts"]
        }
        self.assertEqual(declared_artifacts, source_artifacts)

    def test_missing_files_are_listed_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, profile_path, _profile, _payload = self._fixture(root)
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            report = inspect_manual_artifacts(
                workspace,
                load_manual_artifact_profile(profile_path),
            )
            after = sorted(path.relative_to(root) for path in root.rglob("*"))

            self.assertEqual(before, after)
            self.assertEqual("attention", report["state"])
            self.assertEqual(1, report["counts"]["missing-manual"])
            row = report["artifacts"][0]
            self.assertEqual("missing-manual", row["state"])
            self.assertIn("files/456", row["download_page_url"])
            self.assertIn("<seed-root>/mods/restricted.jar", row["detail"])

    def test_exact_seed_is_ready_and_wrong_bytes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, profile_path, _profile, payload = self._fixture(root)
            seed = root / "seed"
            (seed / "mods").mkdir(parents=True)
            artifact = seed / "mods/restricted.jar"
            artifact.write_bytes(b"wrong")
            profile = load_manual_artifact_profile(profile_path)

            wrong = inspect_manual_artifacts(workspace, profile, seed_roots=[seed])
            self.assertEqual("incompatible", wrong["state"])
            self.assertIn("hash mismatch", wrong["artifacts"][0]["detail"])

            artifact.write_bytes(payload)
            ready = inspect_manual_artifacts(workspace, profile, seed_roots=[seed])
            self.assertEqual("ready", ready["state"])
            self.assertEqual(str(artifact), ready["artifacts"][0]["source"])
            self.assertEqual([str(seed)], ready["seed_roots"])

    def test_runtime_materialize_cli_stops_at_manual_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            (workspace / "mods").mkdir(parents=True)
            profile = load_manual_artifact_profile(
                REPOSITORY_ROOT
                / "profiles/packs/supersymmetry/runtime/manual-artifacts-v1.json"
            )
            for row in profile["artifacts"]:
                metadata = workspace.joinpath(*Path(row["metadata_path"]).parts)
                metadata.parent.mkdir(parents=True, exist_ok=True)
                metadata.write_text(
                    """name = "Fixture"
filename = "{filename}"
side = "both"

[download]
hash-format = "{hash_format}"
hash = "{hash_value}"
mode = "metadata:curseforge"

[update.curseforge]
project-id = {project_id}
file-id = {file_id}
""".format(
                        filename=row["filename"],
                        hash_format=row["hash_format"],
                        hash_value=row["hash"],
                        project_id=row["project_id"],
                        file_id=row["file_id"],
                    ),
                    encoding="utf-8",
                )
            output = io.StringIO()
            error = io.StringIO()
            with (
                patch.object(cli, "materialize_project_runtime") as materialize,
                patch.object(sys, "stdout", output),
                patch.object(sys, "stderr", error),
            ):
                code = cli.main(
                    [
                        "runtime-materialize",
                        str(workspace),
                        "--suite-root",
                        str(REPOSITORY_ROOT),
                    ]
                )

            self.assertEqual(2, code)
            materialize.assert_not_called()
            self.assertIn("no provisioning or download was started", error.getvalue())
            self.assertIn("Commons0815", error.getvalue())
            self.assertIn("workbench runtime preflight", error.getvalue())


if __name__ == "__main__":
    unittest.main()
