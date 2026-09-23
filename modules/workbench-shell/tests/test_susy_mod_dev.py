from __future__ import annotations

from hashlib import sha1, sha256
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/workbench-shell/src"
PROJECT_INTELLIGENCE = ROOT / "modules/project-intelligence/src"
ATLAS = ROOT / "modules/atlas/src"
BLUEPRINTS = ROOT / "modules/blueprints/src"
import sys

for path in (ATLAS, BLUEPRINTS, PROJECT_INTELLIGENCE, SOURCE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.path.insert(0, str(Path(__file__).parent))

from packwiz_v2_fixture import seal_packwiz_v2_receipt  # noqa: E402

from workbench_shell.susy_mod_dev import (  # noqa: E402
    SusyModDevError,
    _canonical_packwiz_seed_roots,
    _runtime_tree,
    execute_susy_mod_build,
    plan_susy_mod_dev,
    validate_susy_mod_plan,
)


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _java(home: Path, major: int = 17) -> None:
    version = "17.0.19" if major == 17 else "1.8.0_492"
    _write(home / "release", f'JAVA_VERSION="{version}"\n')
    _write(home / "bin/java", "#!/bin/sh\nexit 0\n", executable=True)


def _pack(root: Path, *, duplicate: bool = False, stale_index: bool = False) -> None:
    index = b"\n"
    _write(root / "index.toml", index.decode("utf-8"))
    digest = "0" * 64 if stale_index else sha256(index).hexdigest()
    _write(
        root / "pack.toml",
        "\n".join(
            (
                'name = "Supersymmetry"',
                'author = "SymmetricDevs"',
                'version = "test"',
                'pack-format = "packwiz:1.1.0"',
                "[index]",
                'file = "index.toml"',
                'hash-format = "sha256"',
                f'hash = "{digest}"',
                "[versions]",
                'forge = "14.23.5.2860"',
                'minecraft = "1.12.2"',
                "",
            )
        ),
    )
    rows = ["sample"] + (["sample-copy"] if duplicate else [])
    for name in rows:
        _write(
            root / f"mods/{name}.pw.toml",
            "\n".join(
                (
                    'name = "sample"',
                    'filename = "sample-1.0.jar"',
                    'side = "both"',
                    "[download]",
                    'hash-format = "sha1"',
                    f'hash = "{sha1(b"baseline").hexdigest()}"',
                    "[update.curseforge]",
                    "project-id = 12345",
                    "file-id = 67890",
                    "",
                )
            ),
        )


def _wrapper(kind: str) -> str:
    if kind == "failure":
        return "#!/bin/sh\necho compile failed >&2\nexit 42\n"
    if kind == "missing":
        return "#!/bin/sh\nexit 0\n"
    payload = "not a jar" if kind == "corrupt" else None
    if payload is not None:
        return (
            "#!/bin/sh\nmkdir -p build/libs\n"
            f"printf '{payload}' > build/libs/sample-1.0.jar\nexit 0\n"
        )
    mod_id = "wrong" if kind == "wrong-id" else "sample"
    artifact_name = "sample-null.jar" if kind == "poison-version" else "sample-1.0.jar"
    python = (
        "from pathlib import Path; from zipfile import ZipFile; import json; "
        f"p=Path('build/libs/{artifact_name}'); p.parent.mkdir(parents=True,exist_ok=True); "
        "z=ZipFile(p,'w'); "
        f"z.writestr('mcmod.info',json.dumps([{{'modid':'{mod_id}','name':'Sample','version':'1.0','mcversion':'1.12.2'}}])); "
        "z.writestr('sample/Main.class',bytes.fromhex('cafebabe00000034')); "
        "z.writestr('mixins.sample.json','{}'); "
        "z.writestr('mixins.sample.refmap.json','{}'); z.close()"
    )
    return (
        "#!/bin/sh\n"
        "mkdir -p build/libs\n"
        f"/usr/bin/python3 -c \"{python}\"\n"
        "exit $?\n"
    )


def _property_version_wrapper() -> str:
    python = (
        "from pathlib import Path; from zipfile import ZipFile; import json,shutil; "
        "props=dict(tuple(map(str.strip,line.split('=',1))) for line in "
        "Path('buildscript.properties').read_text().splitlines() if '=' in line); "
        "version=props['modVersion']; "
        "p=Path('build/libs/sample-'+version+'.jar'); p.parent.mkdir(parents=True,exist_ok=True); "
        "z=ZipFile(p,'w'); "
        "z.writestr('mcmod.info',json.dumps([{'modid':'sample','name':'Sample','version':version,'mcversion':'1.12.2'}])); "
        "z.writestr('sample/Main.class',bytes.fromhex('cafebabe00000034')); z.close(); "
        "shutil.copy2(p,p.with_name('sample-'+version+'-downgraded.jar')); "
        "shutil.copy2(p,p.with_name('sample-'+version+'-dev-undowngraded.jar'))"
    )
    return (
        "#!/bin/sh\n"
        "mkdir -p build/libs\n"
        f"/usr/bin/python3 -c \"{python}\"\n"
        "exit $?\n"
    )


def _project(root: Path, *, wrapper_kind: str = "success", curseforge: int = 12345) -> None:
    _write(root / "settings.gradle", "rootProject.name = 'sample'\n")
    _write(root / "build.gradle", "plugins { id 'java' }\n")
    _write(
        root / "buildscript.properties",
        "\n".join(
            (
                "modName = Sample",
                "modId = sample",
                "modVersion = 1.0",
                "modArchivesBaseName = sample",
                "minecraftVersion = 1.12.2",
                f"curseForgeProjectId = {curseforge}",
                "usesMixins = true",
                "",
            )
        ),
    )
    _write(
        root / "gradle/wrapper/gradle-wrapper.properties",
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.9-bin.zip\n",
    )
    _write(root / "gradlew", _wrapper(wrapper_kind), executable=True)
    _write(
        root / "src/main/resources/mcmod.info",
        json.dumps(
            [
                {
                    "modid": "sample",
                    "name": "Sample",
                    "version": "1.0",
                    "mcversion": "1.12.2",
                }
            ]
        ),
    )
    _write(root / "src/main/java/sample/Source.java", "package sample; class Source {}\n")


def _materialization(
    root: Path,
    pack_root: Path,
) -> dict:
    instance = root / "instance"
    payload = instance / ".minecraft"
    _write(instance / "mmc-pack.json", '{"formatVersion":1}\n')
    (payload / "mods").mkdir(parents=True)
    (payload / "mods/sample-1.0.jar").write_bytes(b"baseline")
    _write(payload / "config/unrelated.cfg", "unchanged\n")
    summary, _records = _runtime_tree(payload)
    manifest_sha256 = sha256((instance / "mmc-pack.json").read_bytes()).hexdigest()
    receipt_path = root / "receipts/packwiz-materialization-v2.json"
    result = {
        "format": "workbench-packwiz-materialization-result-v2",
        "schema_version": 2,
        "outcome": "installed",
        "receipt": {
            "format": "workbench-packwiz-materialization-receipt-v2",
            "schema_version": 2,
            "state": "materialized",
            "readiness": "pack-payload-installed",
            "materialization_id": "sha256:" + "2" * 64,
            "plan_id": "sha256:" + "3" * 64,
            "request": {"side": "client", "launcher": "prism"},
            "workspace": {"root_uri": pack_root.resolve().as_uri()},
            "target": {
                "instance_root_uri": instance.resolve().as_uri(),
                "receipt_uri": receipt_path.resolve().as_uri(),
            },
            "payload": summary,
            "launcher": {"manifest_sha256_after": manifest_sha256},
        },
    }
    result["receipt"] = seal_packwiz_v2_receipt(result["receipt"])
    return result


def _canonical_seed_fixture(
    state_root: Path,
    fixture_id: str = "seed-fixture",
) -> Path:
    fixture_parent = state_root / "fixtures/packwiz-v2"
    fixture = fixture_parent / fixture_id
    instance = fixture / "instance"
    payload = instance / ".minecraft"
    receipt_path = fixture / "receipts/packwiz-materialization-v2.json"
    _write(payload / "mods/restricted.jar", "trusted bytes\n")
    payload_summary, _records = _runtime_tree(payload)
    receipt = {
        "format": "workbench-packwiz-materialization-receipt-v2",
        "schema_version": 2,
        "state": "materialized",
        "readiness": "pack-payload-installed",
        "request": {"side": "client", "launcher": "prism"},
        "target": {
            "instance_root_uri": instance.resolve().as_uri(),
            "receipt_uri": receipt_path.resolve().as_uri(),
        },
        "payload": {
            **payload_summary,
            "root_uri": payload.resolve().as_uri(),
        },
        "launcher": {"manifest_sha256_after": "5" * 64},
        "plan_id": "sha256:" + "3" * 64,
    }
    receipt = seal_packwiz_v2_receipt(receipt)
    _write(receipt_path, json.dumps(receipt))
    return payload.resolve()


class SusyModDevTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.suite = self.root / "suite"
        self.project = self.root / "project"
        self.pack = self.root / "pack"
        self.java = self.root / "jdk17"
        self.suite.mkdir()
        _java(self.java)
        _project(self.project)
        _pack(self.pack)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def plan(self, **kwargs):
        return plan_susy_mod_dev(
            self.suite,
            self.project,
            self.pack,
            java_home=self.java,
            **kwargs,
        )

    def test_real_plan_binds_project_pack_entry_java_and_source_without_writes(self) -> None:
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        plan = self.plan()
        validate_susy_mod_plan(plan)
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(
            plan["replacement"]["match"]["selected"]["metadata_path"],
            "mods/sample.pw.toml",
        )
        self.assertEqual(plan["replacement"]["applicable_sides"], ["client", "server"])
        self.assertEqual(plan["build"]["java"]["major"], 17)
        self.assertEqual(plan["build"]["task"], "assemble")
        self.assertEqual(plan["build"]["adapter"]["id"], "generic-gradle-assemble-v1")
        self.assertRegex(plan["project"]["source_fingerprint"]["digest"], r"^sha256:")
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(after, before)

    def test_adapter_disables_unguarded_publish_credentials_without_reading_host_secrets(self) -> None:
        _write(
            self.project / "build.gradle",
            "plugins { id 'java' }\n"
            "def user = project.getProperty('publishUsername')\n"
            "def password = project.getProperty('publishPassword')\n"
            "def buildNumber = System.getenv().BUILD_NUMBER\n",
        )
        plan = self.plan()
        defaults = plan["build"]["adapter"]["configuration_defaults"]
        self.assertEqual(
            [row["property"] for row in defaults],
            ["publishPassword", "publishUsername"],
        )
        self.assertIn("-PpublishPassword=workbench-disabled", plan["build"]["argv"])
        self.assertIn("-PpublishUsername=workbench-disabled", plan["build"]["argv"])
        self.assertEqual(
            plan["build"]["environment_overrides"][0]["name"], "BUILD_NUMBER"
        )
        self.assertRegex(
            plan["build"]["environment_overrides"][0]["value"],
            r"^workbench-[0-9a-f]{12}$",
        )

    def test_build_uses_snapshot_verifies_jar_and_prepares_exact_overlay(self) -> None:
        original = (self.project / "src/main/java/sample/Source.java").read_bytes()
        result = execute_susy_mod_build(self.suite, self.plan(), timeout_seconds=30)
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(result["artifact_set"][0]["mod_ids"], ["sample"])
        self.assertEqual(result["artifact_set"][0]["classfile_majors"], [52])
        self.assertEqual(result["overlay"]["baseline_filename"], "sample-1.0.jar")
        overlay = Path(result["overlay"]["root_uri"].removeprefix("file://"))
        self.assertTrue((overlay / "mods/sample-1.0.jar").is_file())
        self.assertEqual((self.project / "src/main/java/sample/Source.java").read_bytes(), original)
        self.assertFalse((self.project / "build").exists())

    def test_nonzero_build_is_retained_as_failure_without_overlay(self) -> None:
        _write(self.project / "gradlew", _wrapper("failure"), executable=True)
        result = execute_susy_mod_build(self.suite, self.plan(), timeout_seconds=30)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["stages"][0]["exit_code"], 42)
        self.assertEqual(result["problems"][0]["code"], "GRADLE_BUILD_FAILED")
        self.assertIn("compile failed", result["problems"][0]["detail"])
        self.assertEqual(
            result["problems"][0]["evidence_uri"],
            result["stages"][0]["stderr_uri"],
        )
        self.assertIsNone(result["overlay"])

    def test_exit_zero_without_artifact_is_failure(self) -> None:
        _write(self.project / "gradlew", _wrapper("missing"), executable=True)
        result = execute_susy_mod_build(self.suite, self.plan(), timeout_seconds=30)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["problems"][0]["code"], "BUILD_ARTIFACT_INVALID")

    def test_corrupt_and_wrong_mod_artifacts_fail_closed(self) -> None:
        for kind in ("corrupt", "wrong-id", "poison-version"):
            with self.subTest(kind=kind):
                isolated = self.root / kind
                project = isolated / "project"
                suite = isolated / "suite"
                suite.mkdir(parents=True)
                _project(project, wrapper_kind=kind)
                plan = plan_susy_mod_dev(
                    suite, project, self.pack, java_home=self.java
                )
                result = execute_susy_mod_build(suite, plan, timeout_seconds=30)
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(result["problems"][0]["code"], "BUILD_ARTIFACT_INVALID")

    def test_pack_match_ambiguity_and_missing_match_are_blocked(self) -> None:
        duplicate_pack = self.root / "duplicate-pack"
        _pack(duplicate_pack, duplicate=True)
        ambiguous = plan_susy_mod_dev(
            self.suite, self.project, duplicate_pack, java_home=self.java
        )
        self.assertEqual(ambiguous["state"], "blocked")
        self.assertEqual(ambiguous["replacement"]["match"]["state"], "ambiguous")

        missing_project = self.root / "missing-project"
        _project(missing_project, curseforge=99999)
        _write(
            missing_project / "buildscript.properties",
            (missing_project / "buildscript.properties")
            .read_text(encoding="utf-8")
            .replace("modName = Sample", "modName = Unknown")
            .replace("modId = sample", "modId = unknown")
            .replace("modArchivesBaseName = sample", "modArchivesBaseName = unknown"),
        )
        _write(
            missing_project / "src/main/resources/mcmod.info",
            '[{"modid":"unknown","name":"Unknown","version":"1.0"}]',
        )
        missing = plan_susy_mod_dev(
            self.suite, missing_project, self.pack, java_home=self.java
        )
        self.assertEqual(missing["state"], "blocked")
        self.assertEqual(missing["replacement"]["match"]["state"], "blocked")

    def test_explicit_pack_entry_resolves_metadata_only_project(self) -> None:
        _write(
            self.project / "buildscript.properties",
            (self.project / "buildscript.properties")
            .read_text(encoding="utf-8")
            .replace("curseForgeProjectId = 12345", "curseForgeProjectId = 99999"),
        )
        plan = self.plan(pack_mod="sample.pw.toml")
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(plan["replacement"]["match"]["reason"], "explicit-pack-entry")
        result = execute_susy_mod_build(self.suite, plan, timeout_seconds=30)
        for action in result["next_actions"][:2]:
            self.assertEqual(action["argv"][-2:], ["--pack-mod", "sample.pw.toml"])

    def test_stale_pack_index_is_visible_but_does_not_block_source_build(self) -> None:
        stale = self.root / "stale-pack"
        _pack(stale, stale_index=True)
        plan = plan_susy_mod_dev(
            self.suite, self.project, stale, java_home=self.java
        )
        self.assertEqual(plan["state"], "ready")
        self.assertEqual([row["code"] for row in plan["warnings"]], ["PACK_INDEX_STALE"])

    def test_empty_version_property_is_overlaid_only_in_managed_custody(self) -> None:
        properties = self.project / "buildscript.properties"
        original = properties.read_text(encoding="utf-8").replace(
            "modVersion = 1.0", "modVersion ="
        )
        _write(properties, original)
        _write(self.project / "gradlew", _property_version_wrapper(), executable=True)

        plan = self.plan()
        self.assertEqual(plan["state"], "ready")
        candidate_version = plan["build"]["candidate_version"]
        self.assertRegex(candidate_version, r"^workbench-dev-[0-9a-f]{12}$")
        self.assertEqual(
            plan["build"]["managed_overlays"],
            [
                {
                    "kind": "empty-property",
                    "path": "buildscript.properties",
                    "key": "modVersion",
                    "value": candidate_version,
                    "purpose": "bind the candidate artifact to the exact source fingerprint inside managed build custody",
                }
            ],
        )

        result = execute_susy_mod_build(self.suite, plan, timeout_seconds=30)
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(
            result["artifact_set"][0]["mod_metadata"][0]["version"],
            candidate_version,
        )
        self.assertEqual(
            result["source_snapshot"]["managed_build_overlays"][0]["value"],
            candidate_version,
        )
        self.assertEqual(properties.read_text(encoding="utf-8"), original)

    def test_retired_rfg_plugin_is_upgraded_only_in_managed_custody(self) -> None:
        build_file = self.project / "build.gradle"
        original = (
            "plugins {\n"
            "    id 'java'\n"
            "    id 'com.gtnewhorizons.retrofuturagradle' version '1.4.0'\n"
            "}\n"
        )
        _write(build_file, original)

        plan = self.plan()
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(
            plan["build"]["adapter"]["plugin"]["effective_version"], "1.4.0"
        )
        self.assertEqual(
            [row["code"] for row in plan["warnings"]],
            ["RFG_PLUGIN_COMPATIBILITY_OVERLAY"],
        )
        plugin_overlay = plan["build"]["managed_overlays"][0]
        self.assertEqual(plugin_overlay["kind"], "gradle-plugin-version")
        self.assertEqual(plugin_overlay["from_version"], "1.4.0")
        self.assertEqual(plugin_overlay["value"], "1.4.9")

        result = execute_susy_mod_build(self.suite, plan, timeout_seconds=30)
        self.assertEqual(result["outcome"], "passed")
        copied_source = Path(
            result["source_snapshot"]["root_uri"].removeprefix("file://")
        )
        self.assertIn(
            "retrofuturagradle' version '1.4.9'",
            (copied_source / "build.gradle").read_text(encoding="utf-8"),
        )
        self.assertEqual(build_file.read_text(encoding="utf-8"), original)

    def test_unavailable_launchwrapper_coordinate_is_repaired_only_in_copy(self) -> None:
        build_file = self.project / "build.gradle"
        original = (
            "plugins {\n"
            "    id 'java'\n"
            "    id 'com.gtnewhorizons.retrofuturagradle' version '1.4.9'\n"
            "}\n"
            "dependencies { patchedMinecraft('net.minecraft:launchwrapper:1.17.2') }\n"
        )
        _write(build_file, original)

        plan = self.plan()
        self.assertEqual(plan["state"], "ready")
        self.assertEqual(
            [row["code"] for row in plan["warnings"]],
            ["LAUNCHWRAPPER_COMPATIBILITY_OVERLAY"],
        )
        dependency_overlay = plan["build"]["managed_overlays"][0]
        self.assertEqual(dependency_overlay["kind"], "gradle-dependency-version")
        self.assertEqual(dependency_overlay["from_version"], "1.17.2")
        self.assertEqual(dependency_overlay["value"], "1.12")

        result = execute_susy_mod_build(self.suite, plan, timeout_seconds=30)
        self.assertEqual(result["outcome"], "passed")
        copied_source = Path(
            result["source_snapshot"]["root_uri"].removeprefix("file://")
        )
        self.assertIn(
            "net.minecraft:launchwrapper:1.12",
            (copied_source / "build.gradle").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "net.minecraft:launchwrapper:1.17.2",
            (copied_source / "build.gradle").read_text(encoding="utf-8"),
        )
        self.assertEqual(build_file.read_text(encoding="utf-8"), original)

    def test_missing_version_and_safe_overlay_property_blocks_build(self) -> None:
        properties = self.project / "buildscript.properties"
        _write(
            properties,
            properties.read_text(encoding="utf-8").replace("modVersion = 1.0\n", ""),
        )
        plan = self.plan()
        self.assertEqual(plan["state"], "blocked")
        self.assertIn(
            "CANDIDATE_VERSION_UNRESOLVED",
            [problem["code"] for problem in plan["problems"]],
        )

    def test_stage_composes_exact_client_replacement_without_mutating_fixture(self) -> None:
        materialization = _materialization(self.root / "materialized", self.pack)
        source_instance = Path(
            materialization["receipt"]["target"]["instance_root_uri"].removeprefix(
                "file://"
            )
        )
        baseline = source_instance / ".minecraft/mods/sample-1.0.jar"
        unrelated = source_instance / ".minecraft/config/unrelated.cfg"
        plan = plan_susy_mod_dev(
            self.suite,
            self.project,
            self.pack,
            java_home=self.java,
            stage_client=True,
        )
        result = execute_susy_mod_build(
            self.suite,
            plan,
            timeout_seconds=30,
            stage_client=True,
            materialization=materialization,
        )
        self.assertEqual(result["outcome"], "passed")
        client = result["runtime"]["client"]
        self.assertEqual(client["target"]["changed_paths"], ["mods/sample-1.0.jar"])
        target = Path(client["target"]["instance_uri"].removeprefix("file://"))
        self.assertEqual(
            sha256((target / ".minecraft/mods/sample-1.0.jar").read_bytes()).hexdigest(),
            result["artifact_set"][0]["sha256"],
        )
        self.assertEqual((target / ".minecraft/config/unrelated.cfg").read_bytes(), unrelated.read_bytes())
        self.assertEqual(baseline.read_bytes(), b"baseline")
        self.assertFalse(client["canonical_materialization_mutated"])
        launch_action = next(
            action
            for action in result["next_actions"]
            if action["id"] == "launch-client"
        )
        self.assertTrue(launch_action["available"])
        self.assertEqual(
            launch_action["argv"],
            ["workbench", "dev", "launch", "--run", result["run_id"]],
        )
        self.assertIsNone(launch_action["reason"])

    def test_stage_preserves_materialization_receipt_binding(self) -> None:
        materialization_root = self.root / "materialized-v2"
        materialization = _materialization(materialization_root, self.pack)
        plan = plan_susy_mod_dev(
            self.suite,
            self.project,
            self.pack,
            java_home=self.java,
            stage_client=True,
        )

        result = execute_susy_mod_build(
            self.suite,
            plan,
            timeout_seconds=30,
            stage_client=True,
            materialization=materialization,
        )

        self.assertEqual(result["outcome"], "passed")
        stage = result["runtime"]["client"]
        self.assertEqual(
            stage["source"]["receipt_uri"],
            (
                materialization_root
                / "receipts/packwiz-materialization-v2.json"
            ).resolve().as_uri(),
        )
        self.assertEqual(
            stage["materialization_id"],
            materialization["receipt"]["materialization_id"],
        )

    def test_stage_automatically_offers_verified_canonical_seed_roots(self) -> None:
        seed = _canonical_seed_fixture(self.suite / ".workbench")
        materialization = _materialization(self.root / "materialized-auto", self.pack)
        plan = plan_susy_mod_dev(
            self.suite,
            self.project,
            self.pack,
            java_home=self.java,
            stage_client=True,
        )
        with patch(
            "workbench_shell.runtime_materialize.materialize_project_runtime",
            return_value=materialization,
        ) as materialize:
            result = execute_susy_mod_build(
                self.suite,
                plan,
                timeout_seconds=30,
                stage_client=True,
            )

        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(materialize.call_args.kwargs["seed_roots"], [seed])

    def test_canonical_seed_discovery_rejects_unsafe_or_misbound_fixtures(self) -> None:
        state = self.suite / ".workbench"
        seed = _canonical_seed_fixture(state)
        self.assertEqual(_canonical_packwiz_seed_roots(state), [seed])

        receipt = (
            state
            / "fixtures/packwiz-v2/seed-fixture/receipts"
            / "packwiz-materialization-v2.json"
        )
        record = json.loads(receipt.read_text(encoding="utf-8"))
        record["payload"]["root_uri"] = (self.root / "outside").as_uri()
        _write(receipt, json.dumps(record))
        with self.assertRaisesRegex(SusyModDevError, "does not bind its payload"):
            _canonical_packwiz_seed_roots(state)

        fixture = state / "fixtures/packwiz-v2/seed-fixture"
        receipt.unlink()
        (fixture / "receipts").rmdir()
        outside = self.root / "outside-receipts"
        outside.mkdir()
        (fixture / "receipts").symlink_to(outside, target_is_directory=True)
        _write(outside / "packwiz-materialization-v2.json", json.dumps(record))
        with self.assertRaisesRegex(SusyModDevError, "receipt directory"):
            _canonical_packwiz_seed_roots(state)

    def test_canonical_seed_discovery_accepts_verified_receipts(self) -> None:
        state = self.suite / ".workbench"
        first = _canonical_seed_fixture(state, "first")
        second = _canonical_seed_fixture(state, "second")

        self.assertEqual(
            _canonical_packwiz_seed_roots(state),
            [first, second],
        )

    def test_canonical_seed_discovery_rejects_symlink_root_and_ancestor(self) -> None:
        real_state = self.root / "real-state"
        _canonical_seed_fixture(real_state)
        linked_state = self.root / "linked-state"
        linked_state.symlink_to(real_state, target_is_directory=True)
        with self.assertRaisesRegex(
            SusyModDevError,
            "seed state must be a regular directory",
        ):
            _canonical_packwiz_seed_roots(linked_state)

        state = self.root / "ancestor-state"
        state.mkdir()
        (state / "fixtures").symlink_to(
            real_state / "fixtures",
            target_is_directory=True,
        )
        with self.assertRaisesRegex(
            SusyModDevError,
            "seed state must be a regular directory",
        ):
            _canonical_packwiz_seed_roots(state)

    def test_canonical_seed_discovery_ignores_incomplete_fixtures(self) -> None:
        state = self.suite / ".workbench"
        fixture_parent = state / "fixtures/packwiz-v2"
        (fixture_parent / "missing-receipt/instance/.minecraft").mkdir(
            parents=True
        )
        receipt_only = fixture_parent / "missing-payload"
        _write(
            receipt_only / "receipts/packwiz-materialization-v2.json",
            "{}\n",
        )
        seed = _canonical_seed_fixture(state, "ready")

        self.assertEqual(_canonical_packwiz_seed_roots(state), [seed])

    def test_stage_rejects_drifted_materialization_after_candidate_build(self) -> None:
        materialization = _materialization(self.root / "drifted", self.pack)
        instance = Path(
            materialization["receipt"]["target"]["instance_root_uri"].removeprefix(
                "file://"
            )
        )
        (instance / ".minecraft/config/unrelated.cfg").write_text(
            "drifted\n", encoding="utf-8"
        )
        plan = plan_susy_mod_dev(
            self.suite,
            self.project,
            self.pack,
            java_home=self.java,
            stage_client=True,
        )
        result = execute_susy_mod_build(
            self.suite,
            plan,
            timeout_seconds=30,
            stage_client=True,
            materialization=materialization,
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["failed_stage"], "client-materialization-and-replacement")
        self.assertEqual(result["problems"][0]["code"], "CLIENT_STAGE_FAILED")
        self.assertIn("payload has drifted", result["problems"][0]["detail"])
        retry = next(
            action for action in result["next_actions"] if action["id"] == "stage-client"
        )
        self.assertTrue(retry["available"])
        self.assertIsNone(retry["reason"])
        self.assertEqual(retry["argv"][0:3], ["workbench", "dev", "stage"])

    def test_source_drift_and_escaping_symlink_are_rejected(self) -> None:
        plan = self.plan()
        _write(self.project / "src/main/java/sample/Source.java", "changed\n")
        with self.assertRaisesRegex(SusyModDevError, "changed after planning"):
            execute_susy_mod_build(self.suite, plan, timeout_seconds=30)

        escaped = self.root / "escaped"
        _project(escaped)
        (escaped / "outside-link").symlink_to(self.root / "outside", target_is_directory=True)
        with self.assertRaisesRegex(SusyModDevError, "symlink escapes"):
            plan_susy_mod_dev(self.suite, escaped, self.pack, java_home=self.java)


if __name__ == "__main__":
    unittest.main()
