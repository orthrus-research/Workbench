from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_worldgen_iteration.iteration import (  # noqa: E402
    FORMAT,
    PROFILE_FORMAT,
    IterationReport,
    WorldgenIterationError,
    audit_runtime_template,
    configure_runtime,
    discover_runtime_template,
    find_built_artifact,
    freeze_world_studio_plan,
    gradle_version,
    install_mod,
    inventory_mods,
    jar_mod_ids,
    java_version,
    load_profile,
    parse_region,
    provision_runtime,
    resolve_profile_path,
    sha256_file,
)
from workbench_crucible_worldgen_iteration.cli import (  # noqa: E402
    _passthrough_integrations,
    _reproduction_command,
)


def write_mod(path: Path, mod_id: str, payload: bytes = b"fixture") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mcmod.info",
            json.dumps([{"modid": mod_id, "name": mod_id, "version": "1"}]),
        )
        archive.writestr("payload.bin", payload)


def write_empty_jar(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w"):
        pass


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


class WorldgenIterationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "Workbench"
        self.root.mkdir()

    def test_checked_in_profile_resolves_exact_cleanroom_slice(self) -> None:
        profile = load_profile(
            resolve_profile_path(ROOT, "supersymmetry"), ROOT
        )
        self.assertEqual(profile["cleanroom"], "0.6.8-alpha")
        self.assertEqual(profile["world_type"], "wb_proto")
        self.assertEqual(
            profile["artifact"]["mod_id"], "workbench_worldgen_prototype"
        )
        self.assertEqual(
            profile["runtime"]["passthrough_integrations"][0]["integration_id"],
            "recurrent_complex",
        )
        self.assertEqual(profile["format"], PROFILE_FORMAT)
        self.assertEqual(
            profile["runtime"]["required_mod_ids"],
            ["biomesoplenty", "groovyscript", "cavegenerator"],
        )
        self.assertEqual(parse_region("-24,-4,16,16"), (-24, -4, 16, 16))

    def test_region_is_bounded_and_unambiguous(self) -> None:
        for invalid in ("1,2,3", "x,2,3,4", "0,0,0,1", "0,0,64,64"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(WorldgenIterationError):
                    parse_region(invalid)

    def test_tool_versions_are_parsed_as_exact_tokens(self) -> None:
        self.assertEqual(
            java_version(
                {"version_output": 'openjdk version "25.0.40" 2026-07-21'}
            ),
            "25.0.40",
        )
        self.assertEqual(
            gradle_version(
                {"version_output": "Welcome to Gradle\n\nGradle 9.6.10\n"}
            ),
            "9.6.10",
        )
        self.assertNotEqual("25.0.4", "25.0.40")
        self.assertNotEqual("9.6.1", "9.6.10")

    def test_runtime_discovery_requires_one_exact_candidate(self) -> None:
        runs = self.root / ".workbench/runs"
        candidate = runs / "selected"
        (candidate / "mods").mkdir(parents=True)
        write_mod(candidate / "cleanroom-exact.jar", "cleanroom_server")
        write_mod(candidate / "mods/BiomesOPlenty.jar", "biomesoplenty")
        write_mod(candidate / "mods/groovyscript.jar", "groovyscript")
        profile = {
            "runtime": {
                "discovery_roots": [".workbench/runs"],
                "server_jar_glob": "cleanroom-exact.jar",
                "required_mod_filename_tokens": ["BiomesOPlenty", "groovyscript"],
                "required_mod_ids": ["biomesoplenty", "groovyscript"],
            }
        }
        self.assertEqual(
            discover_runtime_template(self.root, profile), candidate.resolve()
        )
        duplicate = runs / "duplicate"
        (duplicate / "mods").mkdir(parents=True)
        write_mod(duplicate / "cleanroom-exact.jar", "cleanroom_server")
        write_mod(duplicate / "mods/BiomesOPlenty.jar", "biomesoplenty")
        write_mod(duplicate / "mods/groovyscript.jar", "groovyscript")
        with self.assertRaisesRegex(WorldgenIterationError, "exactly one"):
            discover_runtime_template(self.root, profile)

    def test_runtime_template_audit_is_read_only_and_binds_exact_jars(self) -> None:
        template = self.root / ".workbench/template"
        (template / "mods").mkdir(parents=True)
        (template / "world").mkdir()
        (template / "saves").mkdir()
        (template / "logs").mkdir()
        (template / "world/level.dat").write_bytes(b"world")
        (template / "logs/latest.log").write_text("old\n", encoding="utf-8")
        server = template / "cleanroom-exact.jar"
        write_mod(server, "cleanroom_server")
        first = template / "mods/first.jar"
        second = template / "mods/second.jar"
        comparison = template / "baseline-comparison.jar"
        write_mod(first, "duplicate_mod", b"first")
        write_mod(second, "duplicate_mod", b"second")
        write_mod(comparison, "duplicate_mod", b"comparison")
        groovy = template / "groovy/postInit"
        groovy.mkdir(parents=True)
        selected = self.root / "selected.groovy"
        selected.write_text(
            "mods.worldStudio.profile = 'old'\n", encoding="utf-8"
        )
        plan_link = groovy / "old-plan.groovy"
        try:
            plan_link.symlink_to(selected)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        profile = {
            "format": PROFILE_FORMAT,
            "runtime": {
                "required_mod_ids": ["duplicate_mod"],
                "server_jar_glob": "cleanroom-exact.jar",
            },
        }
        before = snapshot_tree(template)

        audit = audit_runtime_template(template, profile)

        self.assertEqual(snapshot_tree(template), before)
        json.dumps(audit)
        self.assertEqual(audit["template"], str(template.resolve()))
        self.assertFalse(audit["safe_to_provision"])
        self.assertEqual(audit["unsafe_entries"], [])
        self.assertEqual(
            [row["relative_path"] for row in audit["jar_inventory"]],
            [
                "baseline-comparison.jar",
                "cleanroom-exact.jar",
                "mods/first.jar",
                "mods/second.jar",
            ],
        )
        self.assertEqual(audit["server_jar"]["path"], str(server.resolve()))
        self.assertEqual(audit["server_jar"]["sha256"], sha256_file(server))
        self.assertEqual(
            audit["duplicate_mod_ids"],
            [
                {
                    "mod_id": "duplicate_mod",
                    "paths": ["mods/first.jar", "mods/second.jar"],
                }
            ],
        )
        skipped = {row["path"]: row["reason"] for row in audit["skipped_entries"]}
        self.assertEqual(skipped["world"], "world_or_save_residue")
        self.assertEqual(skipped["saves"], "world_or_save_residue")
        self.assertEqual(skipped["logs"], "stale_run_residue")
        self.assertEqual(
            skipped["groovy/postInit/old-plan.groovy"],
            "symlinked_world_studio_plan",
        )

    def test_runtime_template_audit_rejects_unsafe_symlink_before_writing(self) -> None:
        template = self.root / ".workbench/template"
        template.mkdir(parents=True)
        write_mod(template / "cleanroom-exact.jar", "cleanroom_server")
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = template / "linked.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        profile = {
            "format": PROFILE_FORMAT,
            "runtime": {
                "required_mod_ids": [],
                "server_jar_glob": "cleanroom-exact.jar",
            },
        }
        destination = self.root / ".workbench/iterations/test/runtime"

        audit = audit_runtime_template(template, profile)

        self.assertFalse(audit["safe_to_provision"])
        self.assertEqual(
            audit["unsafe_entries"],
            [{"path": "linked.txt", "reason": "top_level_symlink"}],
        )
        with self.assertRaisesRegex(WorldgenIterationError, "audit rejected"):
            provision_runtime(template, destination, profile)
        self.assertFalse(destination.exists())

    def test_runtime_template_audit_requires_one_server_jar_binding(self) -> None:
        template = self.root / ".workbench/template"
        template.mkdir(parents=True)
        for name in ("cleanroom-first.jar", "cleanroom-second.jar"):
            write_mod(template / name, "cleanroom_server")
        profile = {
            "format": PROFILE_FORMAT,
            "runtime": {
                "required_mod_ids": [],
                "server_jar_glob": "cleanroom-*.jar",
            },
        }
        destination = self.root / ".workbench/iterations/test/runtime"

        audit = audit_runtime_template(template, profile)

        self.assertFalse(audit["safe_to_provision"])
        self.assertIsNone(audit["server_jar"])
        self.assertEqual(
            [row["relative_path"] for row in audit["server_jar_candidates"]],
            ["cleanroom-first.jar", "cleanroom-second.jar"],
        )
        self.assertEqual(
            audit["problems"],
            [
                {
                    "kind": "server_jar_match_count",
                    "expected": 1,
                    "actual": 2,
                    "glob": "cleanroom-*.jar",
                }
            ],
        )
        with self.assertRaisesRegex(WorldgenIterationError, "audit rejected"):
            provision_runtime(template, destination, profile)
        self.assertFalse(destination.exists())

    def test_runtime_audit_rejects_valid_filename_impostor_mods(self) -> None:
        profile = {
            "format": PROFILE_FORMAT,
            "runtime": {
                "required_mod_ids": [
                    "biomesoplenty",
                    "groovyscript",
                    "cavegenerator",
                ],
                "server_jar_glob": "cleanroom-exact.jar",
            },
        }
        for impostor in ("renamed", "empty"):
            with self.subTest(impostor=impostor):
                template = self.root / f".workbench/{impostor}-template"
                write_mod(template / "cleanroom-exact.jar", "cleanroom_server")
                if impostor == "renamed":
                    write_mod(
                        template / "mods/BiomesOPlenty.jar",
                        "unrelated_mod",
                    )
                else:
                    write_empty_jar(template / "mods/BiomesOPlenty.jar")
                write_mod(template / "mods/groovyscript.jar", "groovyscript")
                write_mod(template / "mods/CaveGenerator.jar", "cavegenerator")
                before = snapshot_tree(template)

                audit = audit_runtime_template(template, profile)

                self.assertEqual(snapshot_tree(template), before)
                self.assertFalse(audit["safe_to_provision"])
                self.assertTrue(
                    all(
                        row["archive_state"] == "valid"
                        for row in audit["jar_inventory"]
                    )
                )
                self.assertEqual(
                    audit["problems"],
                    [
                        {
                            "kind": "missing_required_mod_ids",
                            "mod_ids": ["biomesoplenty"],
                        }
                    ],
                )

    def test_provision_excludes_worlds_and_run_residue(self) -> None:
        template = self.root / ".workbench/template"
        (template / "mods").mkdir(parents=True)
        (template / "config").mkdir()
        (template / "libraries").mkdir()
        (template / "logs").mkdir()
        (template / "world/region").mkdir(parents=True)
        (template / "world/level.dat").write_bytes(b"world")
        (template / "logs/latest.log").write_text("old\n", encoding="utf-8")
        (template / "config/pack.cfg").write_text("value=true\n", encoding="utf-8")
        library = template / "libraries/dependency.jar"
        library.write_bytes(b"immutable")
        destination = self.root / ".workbench/iterations/test/runtime"
        result = provision_runtime(template, destination)
        self.assertFalse((destination / "world").exists())
        self.assertFalse((destination / "logs").exists())
        self.assertEqual(
            (destination / "config/pack.cfg").read_text(encoding="utf-8"),
            "value=true\n",
        )
        self.assertNotEqual(
            os.stat(template / "config/pack.cfg").st_ino,
            os.stat(destination / "config/pack.cfg").st_ino,
        )
        self.assertNotEqual(
            os.stat(library).st_ino,
            os.stat(destination / "libraries/dependency.jar").st_ino,
        )
        (destination / "libraries/dependency.jar").write_bytes(b"mutated")
        self.assertEqual(library.read_bytes(), b"immutable")
        self.assertIn("world", result["skipped_top_level"])
        self.assertIn("logs", result["skipped_top_level"])

    def test_provision_rejects_symlinks_instead_of_following_them(self) -> None:
        template = self.root / ".workbench/template"
        template.mkdir(parents=True)
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        link = template / "linked.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        destination = self.root / ".workbench/iterations/test/runtime"
        with self.assertRaisesRegex(WorldgenIterationError, "symlink"):
            provision_runtime(template, destination)
        self.assertFalse(destination.exists())

    def test_provision_skips_only_a_symlinked_plan_that_will_be_replaced(self) -> None:
        template = self.root / ".workbench/template"
        groovy = template / "groovy/postInit"
        groovy.mkdir(parents=True)
        selected = self.root / "selected.groovy"
        selected.write_text("mods.worldStudio.profile = 'old'\n", encoding="utf-8")
        link = groovy / "old-plan.groovy"
        try:
            link.symlink_to(selected)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        destination = self.root / ".workbench/iterations/test/runtime"
        result = provision_runtime(template, destination)
        self.assertFalse((destination / "groovy/postInit/old-plan.groovy").exists())
        self.assertEqual(result["skipped_symlinked_world_studio_plans"], 1)

    def test_plan_freeze_replaces_only_other_world_studio_plans(self) -> None:
        runtime = self.root / ".workbench/runtime"
        post_init = runtime / "groovy/postInit"
        post_init.mkdir(parents=True)
        (post_init / "old.groovy").write_text(
            "mods.worldStudio.profile = 'old'\n", encoding="utf-8"
        )
        unrelated = post_init / "recipes.groovy"
        unrelated.write_text("println 'recipes'\n", encoding="utf-8")
        plan = self.root / "candidate.groovy"
        plan.write_text("mods.worldStudio.profile = 'new'\n", encoding="utf-8")
        result = freeze_world_studio_plan(runtime, plan)
        self.assertFalse((post_init / "old.groovy").exists())
        self.assertTrue(unrelated.exists())
        installed = Path(result["installed"])
        self.assertEqual(installed.read_text(encoding="utf-8"), plan.read_text())
        selected = [
            path
            for path in runtime.glob("groovy/**/*.groovy")
            if "mods.worldStudio" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(selected, [installed])

    def test_install_replaces_only_own_mod_and_preserves_an_unrelated_mod(self) -> None:
        runtime = self.root / ".workbench/runtime"
        old = runtime / "mods/worldgen-old.jar"
        unrelated = runtime / "mods/independent-decorator.jar"
        observer = runtime / "mods/strata-worldgen-observer-old.jar"
        write_mod(old, "workbench_worldgen_prototype", b"old")
        write_mod(unrelated, "independent_decorator", b"independent")
        write_mod(observer, "strata_worldgen_observer", b"observer")
        unrelated_hash = sha256_file(unrelated)
        artifact = self.root / "worldgen-new.jar"
        write_mod(artifact, "workbench_worldgen_prototype", b"new")
        result = install_mod(runtime, artifact, "workbench_worldgen_prototype")
        self.assertFalse(old.exists())
        self.assertFalse(observer.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(sha256_file(unrelated), unrelated_hash)
        self.assertEqual(
            jar_mod_ids(Path(result["artifact"])), ("workbench_worldgen_prototype",)
        )
        self.assertEqual(
            [row["file"] for row in inventory_mods(runtime)],
            ["independent-decorator.jar", "worldgen-new.jar"],
        )

    def test_runtime_configuration_enforces_fresh_world_and_exact_seed(self) -> None:
        runtime = self.root / ".workbench/runtime"
        runtime.mkdir(parents=True)
        (runtime / "server.properties").write_text(
            "motd=Keep me\nlevel-name=old\nonline-mode=true\n", encoding="utf-8"
        )
        result = configure_runtime(
            runtime, seed=8675309, world_type="wb_proto", server_port=25590
        )
        properties = (runtime / "server.properties").read_text(encoding="utf-8")
        self.assertIn("motd=Keep me\n", properties)
        self.assertIn("level-name=world\n", properties)
        self.assertIn("level-seed=8675309\n", properties)
        self.assertIn("level-type=wb_proto\n", properties)
        self.assertIn("generate-structures=true\n", properties)
        self.assertIn("online-mode=false\n", properties)
        self.assertEqual(result["server_port"], 25590)
        (runtime / "world").mkdir()
        with self.assertRaisesRegex(WorldgenIterationError, "fresh-world"):
            configure_runtime(runtime, seed=1, world_type="wb_proto")

    def test_exact_trace_configuration_uses_external_timeout_not_watchdog(self) -> None:
        runtime = self.root / ".workbench/runtime"
        runtime.mkdir(parents=True)
        (runtime / "server.properties").write_text(
            "max-tick-time=60000\n", encoding="utf-8"
        )
        result = configure_runtime(
            runtime,
            seed=8675309,
            world_type="wb_proto",
            server_port=25591,
            max_tick_time_ms=-1,
        )
        self.assertIn(
            "max-tick-time=-1\n",
            (runtime / "server.properties").read_text(encoding="utf-8"),
        )
        self.assertEqual(result["max_tick_time_ms"], -1)
        with self.assertRaisesRegex(WorldgenIterationError, "max tick time"):
            configure_runtime(
                runtime,
                seed=8675309,
                world_type="wb_proto",
                level_name="another-world",
                max_tick_time_ms=0,
            )

    def test_artifact_selection_rejects_dev_classifier(self) -> None:
        libs = self.root / ".workbench/build/libs"
        libs.mkdir(parents=True)
        production = libs / "worldgen-1.0.jar"
        production.write_bytes(b"production")
        (libs / "worldgen-1.0-dev.jar").write_bytes(b"development")
        profile = {
            "artifact": {
                "glob": ".workbench/build/libs/worldgen-*.jar",
                "exclude_suffixes": ["-dev.jar"],
            }
        }
        self.assertEqual(find_built_artifact(self.root, profile), production.resolve())

    def test_report_retains_exact_failed_stage(self) -> None:
        output = self.root / ".workbench/iteration/report.json"
        report = IterationReport.start(
            output,
            self.root,
            label="test-run",
            profile="supersymmetry",
            mode="debug",
            argv=["--seed", "1"],
        )
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with report.stage("capture", "exercise failure persistence"):
                raise RuntimeError("boom")
        retained = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(retained["format"], FORMAT)
        self.assertEqual(retained["status"], "failed")
        self.assertEqual(retained["failure"]["stage"], "capture")
        self.assertEqual(retained["stages"][0]["status"], "failed")

    def test_reproduction_command_uses_a_fresh_default_label(self) -> None:
        command = _reproduction_command(
            ["--seed", "1", "--label", "consumed", "--mode=fast"]
        )
        self.assertIn("--seed 1", command)
        self.assertIn("--mode=fast", command)
        self.assertNotIn("consumed", command)

    def test_passthrough_detection_is_entirely_profile_declared(self) -> None:
        profile = {
            "runtime": {
                "passthrough_integrations": [
                    {
                        "filename_tokens": ["ExampleDecorator"],
                        "integration_id": "example_decorator",
                        "mod_ids": ["example_decorator"],
                        "policy": "ordinary-loader-lifecycle",
                    }
                ]
            }
        }
        inventory = [
            {
                "file": "ExampleDecorator-1.0.jar",
                "mod_ids": ["example_decorator"],
                "sha256": "0" * 64,
                "size_bytes": 1,
            }
        ]
        result = _passthrough_integrations(profile, inventory)
        self.assertTrue(result["example_decorator"]["present"])
        self.assertFalse(result["example_decorator"]["modified_by_runner"])


if __name__ == "__main__":
    unittest.main()
