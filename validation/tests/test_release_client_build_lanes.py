from __future__ import annotations

import importlib.util
import inspect
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from xml.etree import ElementTree
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "workbench_build_release_clients",
    ROOT / "tools/build_release_clients.py",
)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import setup failure
    raise RuntimeError("could not load the developer-client builder")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ReleaseClientBuildLaneTest(unittest.TestCase):
    def test_public_v1_is_the_only_lane_and_versions_are_component_owned(self) -> None:
        lane_default = inspect.signature(builder.build).parameters["lane"].default
        self.assertEqual(builder.PUBLIC_LANE, lane_default)
        self.assertEqual(
            "public-v1",
            builder.RELEASE_DESCRIPTOR["package"]["release_track"],
        )
        self.assertEqual("workbench-vscode", builder.VSCODE_COMPONENT["id"])
        self.assertEqual(
            "workbench-intellij-community",
            builder.INTELLIJ_COMPONENT["id"],
        )
        self.assertEqual(
            builder.VSCODE_COMPONENT["version"],
            builder.VSCODE_VERSION,
        )
        self.assertEqual(
            builder.INTELLIJ_COMPONENT["version"],
            builder.INTELLIJ_VERSION,
        )
        self.assertEqual(
            f"workbench-vscode-{builder.VSCODE_VERSION}.vsix",
            builder.VSCODE_ARTIFACT_NAME,
        )
        self.assertEqual(
            f"workbench-intellij-community-{builder.INTELLIJ_VERSION}.zip",
            builder.INTELLIJ_ARTIFACT_NAME,
        )
        fixture = {
            "format": builder.FORMAT,
            "schema_version": builder.SCHEMA_VERSION,
            "client_artifact_manifest_id": "pending",
            "lane": builder.PUBLIC_LANE,
            "artifacts": [],
        }
        identity = builder.manifest_id(fixture)
        self.assertRegex(identity, r"^workbench-developer-clients:sha256:[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                builder.ClientBuildError,
                "unsupported developer-client build lane",
            ):
                builder.build(Path(temporary), lane="candidate-preview")

    def test_public_clients_are_canonical(self) -> None:
        builder.verify_client_source_boundaries()
        self.assertEqual(
            {
                "clients/intellij-community",
                "clients/vscode",
            },
            set(builder.CANONICAL_CLIENTS.values()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                builder.ClientBuildError,
                "unsupported developer-client component",
            ):
                builder.build(Path(temporary), component="workbench-unknown")

    def test_intellij_exact_action_contract_matches_the_source_descriptor(self) -> None:
        descriptor = ElementTree.fromstring(
            (
                ROOT
                / "clients/intellij-community/src/main/resources/"
                "META-INF/plugin.xml"
            ).read_bytes()
        )
        action_rows = descriptor.findall(".//action")
        action_ids = [row.get("id") for row in action_rows]
        self.assertEqual(len(builder.EXPECTED_INTELLIJ_ACTION_IDS), len(action_ids))
        self.assertEqual(builder.EXPECTED_INTELLIJ_ACTION_IDS, set(action_ids))
        self.assertIn("Workbench.QualifyProject", action_ids)
        self.assertEqual(
            builder.EXPECTED_INTELLIJ_ACTION_CLASSES,
            {row.get("id"): row.get("class") for row in action_rows},
        )
        self.assertIn(
            "dev/cleanroommc/workbench/intellij/community/"
            "OpenProjectQualificationAction.class",
            builder.EXPECTED_INTELLIJ_ACTION_CLASS_FILES,
        )
        builder._verify_intellij_action_contract(descriptor)

        qualify = next(
            row for row in action_rows if row.get("id") == "Workbench.QualifyProject"
        )
        qualify.set(
            "class",
            "dev.cleanroommc.workbench.intellij.community.OpenWorkspaceHomeAction",
        )
        with self.assertRaisesRegex(
            builder.ClientBuildError, "exact developer action-to-class mapping"
        ):
            builder._verify_intellij_action_contract(descriptor)

    def test_vscode_exact_command_contract_matches_the_source_descriptor(self) -> None:
        descriptor = json.loads(
            (
                ROOT / "clients/vscode/package.json"
            ).read_text(encoding="utf-8")
        )
        command_ids = {
            row["command"] for row in descriptor["contributes"]["commands"]
        }
        self.assertEqual(builder.EXPECTED_VSCODE_COMMAND_IDS, command_ids)
        self.assertIn("workbench.project.qualify", command_ids)

    def test_vscode_stage_contains_every_local_commonjs_dependency(self) -> None:
        source_root = ROOT / "clients/vscode"
        pending = ["extension.js"]
        closure: set[str] = set()
        local_require = re.compile(r'''require\(["']\./([^"']+)["']\)''')
        while pending:
            name = pending.pop()
            if name in closure:
                continue
            closure.add(name)
            source = (source_root / name).read_text(encoding="utf-8")
            for dependency in local_require.findall(source):
                dependency_name = (
                    dependency if dependency.endswith(".js") else dependency + ".js"
                )
                if (source_root / dependency_name).is_file():
                    pending.append(dependency_name)

        self.assertIn("projectQualificationClient.js", closure)
        self.assertLessEqual(closure, set(builder.VSCODE_STAGE_FILES))

    def test_intellij_package_verifier_requires_project_qualification_class(self) -> None:
        descriptor = (
            ROOT
            / "clients/intellij-community/src/main/resources/"
            "META-INF/plugin.xml"
        ).read_bytes()
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("META-INF/plugin.xml", descriptor)
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "client.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr("workbench/lib/workbench.jar", nested.getvalue())
            with self.assertRaises(builder.ClientBuildError) as raised:
                builder.verify_intellij(artifact)
        self.assertIn("OpenProjectQualificationAction.class", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
