from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_worldgen_iteration import doctor  # noqa: E402
from workbench_crucible_worldgen_iteration.iteration import (  # noqa: E402
    sha256_file,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_mod(path: Path, mod_id: str, payload: bytes = b"fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mcmod.info",
            json.dumps([{"modid": mod_id, "name": mod_id, "version": "1"}]),
        )
        archive.writestr("payload.bin", payload)


def snapshot_tree(root: Path) -> list[tuple[str, str, bytes | str | None]]:
    snapshot: list[tuple[str, str, bytes | str | None]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "symlink", os.readlink(path)))
        elif path.is_file():
            snapshot.append((relative, "file", path.read_bytes()))
        else:
            snapshot.append((relative, "directory", None))
    return snapshot


class WorldgenDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Workbench"
        self.root.mkdir()

    def _create_exact_target(self) -> dict[str, Path]:
        cleanroom = "0.6.8-alpha"
        fixture = (
            self.root
            / "profiles/platforms/cleanroom/candidates"
            / cleanroom
            / "worldgen-prototype-fixture"
        )
        plan = fixture / "examples/groovy/postInit/world_studio.groovy"
        plan.parent.mkdir(parents=True)
        plan.write_text("mods.worldStudio.profile = 'doctor-test'\n", encoding="utf-8")

        profile_path = (
            self.root
            / "profiles/packs/supersymmetry/worldgen"
            / "worldgen-iteration-profile-v2.json"
        )
        profile = {
            "artifact": {
                "exclude_suffixes": ["-dev.jar", "-sources.jar"],
                "glob": ".workbench/build/worldgen/libs/worldgen-prototype-*.jar",
                "mod_id": "workbench_worldgen_prototype",
            },
            "cleanroom": cleanroom,
            "defaults": {
                "debug_region": [-24, -4, 16, 16],
                "diagnostic_sample_modulo": 1,
                "fast_region": [-2, -2, 4, 4],
                "halo_chunks": 1,
                "heap": "4096M",
                "seed": 8675309,
            },
            "fixture": fixture.relative_to(self.root).as_posix(),
            "format": "workbench-worldgen-iteration-profile-v2",
            "minecraft_version": "1.12.2",
            "plan": plan.relative_to(self.root).as_posix(),
            "platform_profile_id": "workbench-platform:cleanroom:0.6.8-alpha",
            "profile_id": "workbench-pack:supersymmetry:worldgen-iteration-v2",
            "runtime": {
                "discovery_roots": [".workbench/templates"],
                "passthrough_integrations": [],
                "required_mod_filename_tokens": [
                    "BiomesOPlenty",
                    "groovyscript",
                    "CaveGenerator",
                ],
                "required_mod_ids": [
                    "biomesoplenty",
                    "groovyscript",
                    "cavegenerator",
                ],
                "server_jar_glob": "cleanroom-0.6.8-alpha.jar",
            },
            "schema_version": 2,
            "toolchains": {
                "minimum_cleanroom_java_major": 25,
                "proven_cleanroom_java": "25.0.4",
                "proven_gradle": "9.6.1",
            },
            "world_type": "wb_proto",
        }
        write_json(profile_path, profile)

        candidate_path = (
            self.root
            / "profiles/platforms/cleanroom/candidates"
            / cleanroom
            / "candidate-lock-v1.json"
        )
        write_json(
            candidate_path,
            {
                "candidate_id": (
                    "workbench-platform:cleanroom:0.6.8-alpha+mc-1.12.2+"
                    "forge-14.23.5.2864+mcp-9.42-stable_39"
                ),
                "cleanroom": {
                    "release": {
                        "sha256": (
                            "64e4d8af4f117224f7b69efb5e270f75b05feef7fd034fc"
                            "ce185ae2e5b5ec9eb"
                        ),
                        "size": 58129,
                        "url": (
                            "https://github.com/CleanroomMC/Cleanroom/releases/"
                            "download/0.6.8-alpha/cleanroom-0.6.8-alpha.zip"
                        ),
                    },
                    "source_repository": (
                        "https://github.com/CleanroomMC/Cleanroom.git"
                    ),
                    "source_revision": "9946eb1f17a66a72d518e5e4a92d45c62c6d33fc",
                    "version": cleanroom,
                },
                "format": "workbench-cleanroom-candidate-lock-v1",
                "forge": {"version": "14.23.5.2864"},
                "mappings": {
                    "channel": "stable",
                    "coordinate": "stable_39",
                    "mcp_version": "9.42",
                    "version": "39",
                },
                "minecraft": {"version": "1.12.2"},
                "maturity": "experimental",
                "schema_version": 1,
            },
        )

        runtime = self.root / ".workbench/templates/exact"
        runtime.mkdir(parents=True)
        write_mod(runtime / "cleanroom-0.6.8-alpha.jar", "cleanroom_server")
        write_mod(runtime / "mods/BiomesOPlenty.jar", "biomesoplenty")
        write_mod(runtime / "mods/groovyscript.jar", "groovyscript")
        write_mod(runtime / "mods/CaveGenerator.jar", "cavegenerator")

        artifact = (
            self.root
            / ".workbench/build/worldgen/libs/worldgen-prototype-doctor.jar"
        )
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"current-remapped-artifact")

        strata = self.root / ".workbench/integrations/strata"
        strata_entrypoint = strata / "tools/capture_dense_chunk_package.py"
        strata_entrypoint.parent.mkdir(parents=True)
        strata_entrypoint.write_text("# fixture entrypoint\n", encoding="utf-8")

        mixin_policy = (
            self.root
            / "profiles/platforms/cleanroom/mixins"
            / "cleanroom-mixin-doctor-policy-v1.json"
        )
        write_json(mixin_policy, {"format": "fixture-policy"})
        mixin_tool = (
            self.root
            / "profiles/platforms/cleanroom/tools/run_mixin_doctor.py"
        )
        mixin_tool.parent.mkdir(parents=True, exist_ok=True)
        mixin_tool.write_text("# fixture tool\n", encoding="utf-8")

        java = self.root / ".workbench/toolchains/jdk-25/bin/java"
        gradle = self.root / ".workbench/toolchains/gradle-9.6.1/bin/gradle"
        for executable in (java, gradle):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"fake executable\n")
            executable.chmod(0o755)

        return {
            "artifact": artifact,
            "candidate": candidate_path,
            "fixture": fixture,
            "gradle": gradle,
            "java": java,
            "mixin_policy": mixin_policy,
            "mixin_tool": mixin_tool,
            "plan": plan,
            "profile": profile_path,
            "runtime": runtime,
            "strata": strata,
        }

    def _identity(
        self,
        executable: Path,
        arguments: object,
        *,
        environment: object = None,
    ) -> dict[str, str]:
        del arguments, environment
        output = (
            'openjdk version "25.0.4" 2026-07-21'
            if executable.name == "java"
            else "Gradle 9.6.1"
        )
        return {
            "path": str(executable),
            "sha256": sha256_file(executable),
            "version_output": output,
        }

    def _inspect(
        self,
        target: dict[str, Path],
        *,
        runtime_template: Path | None = None,
        identity=None,
    ) -> dict[str, object]:
        identity = identity or self._identity
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(doctor, "executable_identity", side_effect=identity),
        ):
            return doctor.inspect_worldgen_development_target(
                self.root,
                profile_name="supersymmetry",
                runtime_template=runtime_template,
                strata_root=target["strata"],
                java_cmd=str(target["java"]),
                gradle_cmd=str(target["gradle"]),
            )

    @staticmethod
    def _finding_ids(result: dict[str, object]) -> set[str]:
        return {row["id"] for row in result["findings"]}  # type: ignore[index]

    def test_profile_selection_is_explicit_and_read_only(self) -> None:
        before = snapshot_tree(self.root)

        result = doctor.inspect_worldgen_development_target(
            self.root, profile_name=None
        )

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertEqual(result["profile"]["selection"], "none")
        self.assertEqual(self._finding_ids(result), {"PROFILE_SELECTION_REQUIRED"})
        finding = result["findings"][0]
        self.assertEqual(finding["severity"], "blocker")
        self.assertFalse(finding["repair"]["mutates"])
        self.assertEqual(result["next_commands"], [])

    def test_malformed_profile_file_is_a_blocker_not_an_exception(self) -> None:
        profile = self.root / "broken-profile.json"
        profile.write_text("{not-json\n", encoding="utf-8")
        before = snapshot_tree(self.root)

        result = doctor.inspect_worldgen_development_target(
            self.root,
            profile_name=None,
            profile_file=profile,
        )

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertEqual(result["profile"]["state"], "unresolved")
        self.assertEqual(self._finding_ids(result), {"PROFILE_INVALID"})
        self.assertIn("cannot parse profile", result["findings"][0]["detail"])

    def test_invalid_profile_name_is_a_blocker_not_an_exception(self) -> None:
        before = snapshot_tree(self.root)

        result = doctor.inspect_worldgen_development_target(
            self.root,
            profile_name="../not-a-profile",
        )

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertEqual(result["profile"]["state"], "unresolved")
        self.assertEqual(self._finding_ids(result), {"PROFILE_INVALID"})
        self.assertIn("invalid profile name", result["findings"][0]["detail"])

    def test_candidate_lock_identity_fails_closed(self) -> None:
        target = self._create_exact_target()
        candidate = json.loads(target["candidate"].read_text(encoding="utf-8"))
        candidate.pop("format")
        write_json(target["candidate"], candidate)

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertIn("CLEANROOM_CANDIDATE_LOCK_INVALID", self._finding_ids(result))
        self.assertEqual(result["platform"]["state"], "unresolved")
        json.loads(json.dumps(result))

    def test_candidate_lock_symlink_is_not_profile_authority(self) -> None:
        target = self._create_exact_target()
        outside = self.root / "mutable-candidate-lock.json"
        target["candidate"].replace(outside)
        try:
            target["candidate"].symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        before = snapshot_tree(self.root)

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertIn("CLEANROOM_CANDIDATE_LOCK_MISSING", self._finding_ids(result))
        self.assertEqual(result["platform"]["state"], "unresolved")

    def test_invalid_nested_profile_values_are_profile_blockers(self) -> None:
        target = self._create_exact_target()
        profile = json.loads(target["profile"].read_text(encoding="utf-8"))
        profile["defaults"]["seed"] = "not-an-integer"
        profile["artifact"]["glob"] = ""
        write_json(target["profile"], profile)

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertEqual(self._finding_ids(result), {"PROFILE_INVALID"})
        self.assertEqual(result["next_commands"], [])

    def test_exact_target_yields_json_safe_structured_fragment_without_writes(
        self,
    ) -> None:
        target = self._create_exact_target()
        before = snapshot_tree(self.root)

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertEqual(snapshot_tree(self.root), before)
        json.loads(json.dumps(result))
        self.assertEqual(result["capability"], "worldgen-dev")
        self.assertEqual(result["workspace"], str(target["fixture"]))
        self.assertEqual(result["profile"]["state"], "declared")
        self.assertEqual(result["profile"]["selection"], "explicit-name")
        self.assertEqual(result["platform"]["state"], "declared")
        self.assertEqual(result["platform"]["forge_version"], "14.23.5.2864")
        self.assertEqual(result["platform"]["mappings"]["coordinate"], "stable_39")
        self.assertEqual(
            [role["state"] for role in result["java_roles"][:3]],
            ["observed", "observed", "observed"],
        )
        self.assertEqual(result["build_tool"]["state"], "observed")
        self.assertEqual(result["runtime"]["state"], "observed")
        self.assertTrue(result["runtime"]["safe_to_provision"])
        integrations = {
            row["id"]: row for row in result["integrations"]["items"]
        }
        self.assertEqual(integrations["current-worldgen-artifact"]["state"], "observed")
        self.assertEqual(integrations["strata"]["state"], "observed")
        self.assertEqual(integrations["cleanroom-mixin-doctor"]["state"], "bounded")
        self.assertEqual(
            self._finding_ids(result), {"CLEANROOM_PROFILE_EXPERIMENTAL"}
        )
        self.assertTrue(
            all(not finding["repair"]["mutates"] for finding in result["findings"])
        )
        worldgen_command = next(
            command
            for command in result["next_commands"]
            if command["id"] == "worldgen-dev"
        )
        self.assertEqual(worldgen_command["blocked_by"], [])
        self.assertIn("--profile supersymmetry", worldgen_command["command"])
        self.assertIn("--runtime-template", worldgen_command["command"])
        self.assertEqual(len(result["limitations"]), 3)

    def test_runtime_discovery_distinguishes_missing_and_ambiguous(self) -> None:
        target = self._create_exact_target()
        runtime = target["runtime"]
        moved = self.root / ".workbench/not-discoverable/exact"
        moved.parent.mkdir(parents=True)
        runtime.rename(moved)

        missing = self._inspect(target)

        self.assertEqual(missing["runtime"]["state"], "unavailable")
        self.assertIn("RUNTIME_TEMPLATE_UNAVAILABLE", self._finding_ids(missing))

        first = runtime.parent / "first"
        second = runtime.parent / "second"
        moved.rename(first)
        second.mkdir(parents=True)
        write_mod(second / "cleanroom-0.6.8-alpha.jar", "cleanroom_server")
        write_mod(second / "mods/BiomesOPlenty.jar", "biomesoplenty")
        write_mod(second / "mods/groovyscript.jar", "groovyscript")
        write_mod(second / "mods/CaveGenerator.jar", "cavegenerator")
        before = snapshot_tree(self.root)

        ambiguous = self._inspect(target)

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertEqual(ambiguous["runtime"]["state"], "ambiguous")
        self.assertIn("RUNTIME_TEMPLATE_AMBIGUOUS", self._finding_ids(ambiguous))

    def test_java_below_profile_minimum_is_a_blocker(self) -> None:
        target = self._create_exact_target()

        def java_17_identity(
            executable: Path,
            arguments: object,
            *,
            environment: object = None,
        ) -> dict[str, str]:
            result = self._identity(
                executable, arguments, environment=environment
            )
            if executable.name == "java":
                result["version_output"] = 'openjdk version "17.0.12" 2024-07-16'
            return result

        before = snapshot_tree(self.root)

        result = self._inspect(
            target,
            runtime_template=target["runtime"],
            identity=java_17_identity,
        )

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertIn("CLEANROOM_JAVA_TOO_OLD", self._finding_ids(result))

    def test_corrupt_required_mod_archive_blocks_the_run(self) -> None:
        target = self._create_exact_target()
        (target["runtime"] / "mods/BiomesOPlenty.jar").write_bytes(b"not-a-jar")

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertEqual(result["runtime"]["state"], "bounded")
        self.assertFalse(result["runtime"]["safe_to_provision"])
        self.assertIn("RUNTIME_TEMPLATE_UNSAFE", self._finding_ids(result))
        worldgen = next(
            row for row in result["next_commands"] if row["id"] == "worldgen-dev"
        )
        self.assertIn("RUNTIME_TEMPLATE_UNSAFE", worldgen["blocked_by"])

    def test_missing_mixin_policy_warns_without_offering_a_scan(self) -> None:
        target = self._create_exact_target()
        target["mixin_policy"].unlink()
        before = snapshot_tree(self.root)

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertEqual(snapshot_tree(self.root), before)
        integration = next(
            row
            for row in result["integrations"]["items"]
            if row["id"] == "cleanroom-mixin-doctor"
        )
        self.assertEqual(integration["state"], "unavailable")
        warnings = [
            row for row in result["findings"] if row["severity"] == "warning"
        ]
        self.assertTrue(
            any("Mixin" in row["title"] for row in warnings),
            warnings,
        )
        self.assertNotIn(
            "cleanroom-mixin-doctor",
            {row["id"] for row in result["next_commands"]},
        )

    def test_duplicate_runtime_mod_ids_are_reported_without_writes(self) -> None:
        target = self._create_exact_target()
        write_mod(target["runtime"] / "mods/duplicate-a.jar", "duplicate")
        write_mod(target["runtime"] / "mods/duplicate-b.jar", "duplicate")
        before = snapshot_tree(self.root)

        result = self._inspect(target, runtime_template=target["runtime"])

        self.assertEqual(snapshot_tree(self.root), before)
        self.assertIn("RUNTIME_DUPLICATE_MOD_IDS", self._finding_ids(result))
        self.assertEqual(
            result["runtime"]["duplicate_mod_ids"],
            [
                {
                    "mod_id": "duplicate",
                    "paths": ["mods/duplicate-a.jar", "mods/duplicate-b.jar"],
                }
            ],
        )
        duplicate_finding = next(
            row
            for row in result["findings"]
            if row["id"] == "RUNTIME_DUPLICATE_MOD_IDS"
        )
        self.assertEqual(duplicate_finding["severity"], "blocker")
        self.assertFalse(duplicate_finding["repair"]["mutates"])


if __name__ == "__main__":
    unittest.main()
