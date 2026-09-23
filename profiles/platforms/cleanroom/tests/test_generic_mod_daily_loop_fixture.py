from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[4]
PROFILE = ROOT / "profiles/platforms/cleanroom"
CANDIDATE = PROFILE / "candidates/0.6.8-alpha"
CANDIDATE_LOCK = CANDIDATE / "candidate-lock-v1.json"
CANDIDATE_LOCK_SCHEMA = (
    PROFILE / "schemas/workbench-cleanroom-candidate-lock-v1.schema.json"
)
FIXTURE = PROFILE / "fixtures/generic-mod-daily-loop"
FIXTURE_LOCK = FIXTURE / "fixture-lock-v1.json"
FIXTURE_LOCK_SCHEMA = (
    PROFILE / "schemas/workbench-cleanroom-generic-mod-fixture-lock-v1.schema.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FIXTURE_RUNNER = PROFILE / "tools/run_generic_mod_fixture_build.py"
FIXTURE_CLEANUP_INIT = PROFILE / "tools/clean_generic_mod_fixture.gradle"
sys.path.insert(0, str(ROOT / "api/src"))
sys.path.insert(0, str(PROFILE / "src"))
from workbench_profile_cleanroom import fixture_build as RUNNER  # noqa: E402


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, str], ...] | None:
    """Retain exact tree bytes/modes without requiring ignored state to be empty."""

    if not root.exists():
        return None
    rows: list[tuple[str, str, int, str]] = []
    for path in [
        root,
        *sorted(root.rglob("*"), key=lambda item: item.as_posix().encode("utf-8")),
    ]:
        metadata = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        if path.is_symlink():
            kind = "symlink"
            payload = sha256(os.readlink(path).encode("utf-8")).hexdigest()
        elif path.is_dir():
            kind = "directory"
            payload = ""
        elif path.is_file():
            kind = "file"
            payload = _digest(path)
        else:
            kind = "special"
            payload = ""
        rows.append((relative, kind, metadata.st_mode, payload))
    return tuple(rows)


def _lock_errors(root: Path, lock: dict[str, object]) -> list[str]:
    errors: list[str] = []
    declared_values = lock.get("declared_values", {})
    if not isinstance(declared_values, dict):
        return ["declared_values is not an object"]
    rows = declared_values.get("files", [])
    if not isinstance(rows, list):
        return ["identity_files is not a list"]
    generated_parts = {".gradle", "__pycache__", "build", "out"}
    expected_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.name != "fixture-lock-v1.json"
        and not generated_parts.intersection(path.relative_to(root).parts)
        and path.suffix not in {".class", ".jar", ".pyc"}
    }
    if rows != sorted(rows, key=lambda row: row["path"].encode("utf-8")):
        errors.append("identity rows are not bytewise path sorted")
    declared_paths = {
        row.get("path") for row in rows if isinstance(row, dict)
    }
    if declared_paths != expected_paths:
        errors.append(
            f"identity path drift: declared={sorted(str(item) for item in declared_paths)} "
            f"expected={sorted(expected_paths)}"
        )
    for row in rows:
        if not isinstance(row, dict):
            errors.append("identity row is not an object")
            continue
        relative = row.get("path")
        digest = row.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            errors.append("identity row path/digest is not a string")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"missing identity file: {relative}")
        elif f"sha256:{_digest(path)}" != digest:
            errors.append(f"digest mismatch: {relative}")
        elif path.stat().st_size != row.get("size"):
            errors.append(f"size mismatch: {relative}")
    canonical_tree = json.dumps(
        {"algorithm": "sha256-file-tree-v1", "files": rows},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    expected_tree_digest = f"sha256:{sha256(canonical_tree).hexdigest()}"
    if declared_values.get("tree_digest_algorithm") != "sha256-file-tree-v1":
        errors.append("tree digest algorithm drift")
    if declared_values.get("tree_digest") != expected_tree_digest:
        errors.append("tree digest mismatch")
    identity = declared_values.get("identity", {})
    if not isinstance(identity, dict) or identity.get("digest") != expected_tree_digest:
        errors.append("fixture identity digest mismatch")
    return errors


class GenericModDailyLoopFixtureTests(unittest.TestCase):
    def test_candidate_lock_is_exact_experimental_platform_identity(self) -> None:
        candidate = _load(CANDIDATE_LOCK)

        Draft202012Validator(
            _load(CANDIDATE_LOCK_SCHEMA), format_checker=FormatChecker()
        ).validate(candidate)

        self.assertEqual(
            "workbench-cleanroom-candidate-lock-v1",
            candidate["format"],
        )
        self.assertEqual("experimental", candidate["maturity"])
        self.assertEqual("0.6.8-alpha", candidate["cleanroom"]["version"])
        self.assertEqual("1.12.2", candidate["minecraft"]["version"])
        self.assertEqual("14.23.5.2864", candidate["forge"]["version"])
        self.assertRegex(_digest(CANDIDATE_LOCK), SHA256_RE)

    def test_fixture_lock_binds_candidate_and_every_identity_file(self) -> None:
        candidate = _load(CANDIDATE_LOCK)
        lock = _load(FIXTURE_LOCK)

        Draft202012Validator(
            _load(FIXTURE_LOCK_SCHEMA), format_checker=FormatChecker()
        ).validate(lock)

        self.assertEqual("workbench-v2-boundary-owner-declaration-v1", lock["format"])
        self.assertEqual(1, lock["schema_version"])
        self.assertEqual("cleanroom-platform-profile", lock["owner_id"])
        self.assertEqual(["fixture.cleanroom-mod"], lock["identity_ids"])
        identity = lock["declared_values"]["identity"]
        self.assertEqual("workbench-fixture:cleanroom:generic-mod-daily-loop", identity["fixture_id"])
        self.assertRegex(identity["revision"], re.compile(r"^[0-9a-f]{40}$"))
        self.assertEqual("experimental", candidate["maturity"])
        self.assertEqual([], _lock_errors(FIXTURE, lock))
        for row in lock["declared_values"]["files"]:
            self.assertRegex(row["sha256"].removeprefix("sha256:"), SHA256_RE)

    def test_build_uses_cleanroom_unimined_java25_and_ignored_output(self) -> None:
        build = (FIXTURE / "build.gradle").read_text(encoding="utf-8")
        properties = (FIXTURE / "gradle.properties").read_text(encoding="utf-8")
        settings = (FIXTURE / "settings.gradle").read_text(encoding="utf-8")

        self.assertIn("xyz.wagyourtail.unimined' version '1.4.27-kappa'", build)
        self.assertIn("languageVersion = JavaLanguageVersion.of(25)", build)
        self.assertIn("annotationProcessor 'com.cleanroommc:cleanmix:0.7.0'", build)
        self.assertIn("loader cleanroom_version", build)
        self.assertIn("mcp(mcp_channel, mcp_version)", build)
        self.assertIn("../../../../../.workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop", build)
        self.assertIn("preserveFileTimestamps = false", build)
        self.assertIn("reproducibleFileOrder = true", build)
        self.assertIn("verifyFixtureArtifact", build)
        mixin_mapping = (FIXTURE / "mixin-mcp-to-srg.srg").read_text(encoding="utf-8")
        self.assertIn(
            "Block/getTranslationKey ()Ljava/lang/String; "
            "net/minecraft/block/Block/func_149739_a ()Ljava/lang/String;",
            mixin_mapping,
        )
        self.assertIn("minecraft_version=1.12.2", properties)
        self.assertIn("cleanroom_version=0.6.8-alpha", properties)
        self.assertIn("mcp_version=39-1.12", properties)
        self.assertIn("foojay-resolver-convention' version '1.0.0'", settings)
        self.assertFalse((FIXTURE / "gradle/wrapper/gradle-wrapper.jar").exists())

    def test_source_has_registry_localization_server_probe_and_bounded_mixin(self) -> None:
        java_root = FIXTURE / "src/main/java/dev/workbench/dailyloop"
        common_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(java_root.rglob("*.java"))
            if "/client/" not in path.as_posix()
        )
        content = (java_root / "DailyLoopContent.java").read_text(encoding="utf-8")
        mod = (java_root / "DailyLoopMod.java").read_text(encoding="utf-8")
        probe = (java_root / "DailyLoopProbe.java").read_text(encoding="utf-8")
        mixin = (java_root / "mixin/MixinBlock.java").read_text(encoding="utf-8")
        mixin_config = _load(
            FIXTURE / "src/main/resources/mixins.workbench_daily_loop.json"
        )
        localization = (
            FIXTURE
            / "src/main/resources/assets/workbench_daily_loop/lang/en_us.lang"
        ).read_text(encoding="utf-8")

        self.assertIn("RegistryEvent.Register<Block>", content)
        self.assertIn("RegistryEvent.Register<Item>", content)
        self.assertIn('new ResourceLocation(DailyLoopMod.MOD_ID, "probe_block")', content)
        self.assertIn("FMLServerStartingEvent", mod)
        self.assertIn("assertCommonRegistry", probe)
        self.assertIn("WORKBENCH_DAILY_LOOP_COMMON_READY", probe)
        self.assertNotIn("net.minecraft.client", common_sources)
        self.assertIn("tile.workbench_daily_loop.probe_block.name=", localization)
        self.assertTrue(mixin_config["required"])
        self.assertEqual("JAVA_25", mixin_config["compatibilityLevel"])
        self.assertEqual("mixins.workbench_daily_loop.refmap.json", mixin_config["refmap"])
        self.assertIn("getTranslationKey()Ljava/lang/String;", mixin)
        for bound in ("require = 1", "expect = 1", "allow = 1"):
            self.assertIn(bound, mixin)

    def test_fixture_lock_detects_source_drift(self) -> None:
        lock = _load(FIXTURE_LOCK)
        with tempfile.TemporaryDirectory(prefix="workbench-daily-loop-drift-") as temp:
            copied = Path(temp) / "fixture"
            shutil.copytree(FIXTURE, copied)
            source = copied / "src/main/java/dev/workbench/dailyloop/DailyLoopProbe.java"
            source.write_text(source.read_text(encoding="utf-8") + "\n// drift\n", encoding="utf-8")
            self.assertIn(
                "digest mismatch: src/main/java/dev/workbench/dailyloop/DailyLoopProbe.java",
                _lock_errors(copied, lock),
            )

    def test_build_runner_binds_exact_fixture_and_toolchain_and_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="workbench-fixture-runner-") as temp:
            base = Path(temp)
            gradle = base / "gradle"
            gradle.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            gradle.chmod(0o700)
            java = base / "java-25"
            (java / "bin").mkdir(parents=True)
            (java / "release").write_text(
                'JAVA_VERSION="25.0.1"\n', encoding="utf-8"
            )
            (java / "bin/java").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (java / "bin/java").chmod(0o700)

            inputs = RUNNER.inspect_build_inputs(
                gradle_cmd=gradle,
                java_home=java,
            )
            self.assertEqual(
                inputs["fixture_digest"],
                _load(FIXTURE_LOCK)["declared_values"]["tree_digest"],
            )
            self.assertEqual(inputs["gradle"]["path"], str(gradle))
            self.assertRegex(inputs["gradle"]["sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertRegex(
                inputs["java"]["executable_sha256"], r"^sha256:[0-9a-f]{64}$"
            )
            input_digest = RUNNER.build_input_digest(inputs)
            self.assertRegex(input_digest, r"^sha256:[0-9a-f]{64}$")
            runner_state = base / "state"
            projected = RUNNER.fixture_projection_path(
                inputs["fixture_digest"],
                state_root=runner_state,
            )
            projection_before = _tree_snapshot(projected)

            checked = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURE_RUNNER),
                    "--gradle-cmd",
                    str(gradle),
                    "--java-home",
                    str(java),
                    "--expected-input-digest",
                    input_digest,
                    "--state-root",
                    str(runner_state),
                    "--check-only",
                ],
                cwd=ROOT,
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            preflight = json.loads(checked.stdout)
            self.assertEqual(
                preflight["format"],
                "workbench-cleanroom-fixture-build-preflight-v1",
            )
            self.assertEqual(preflight["inputs"], inputs)
            self.assertEqual(
                preflight["argv"][preflight["argv"].index("-p") + 1],
                str(projected),
            )
            self.assertEqual(
                projection_before,
                _tree_snapshot(projected),
                "read-only preflight changed the retained projection tree",
            )
            isolated_root = base / "isolated-workbench"
            with patch.object(RUNNER, "ROOT", isolated_root):
                absent_projection = RUNNER.fixture_projection_path(
                    inputs["fixture_digest"]
                )
                self.assertFalse(absent_projection.exists())
                with patch.dict(os.environ, {"TZ": "America/Los_Angeles"}):
                    absent_argv, absent_environment = RUNNER.build_argv(
                        gradle_cmd=gradle,
                        java_home=java,
                        expected_input_digest=input_digest,
                        materialize=False,
                    )
                self.assertEqual(
                    absent_argv[absent_argv.index("-p") + 1],
                    str(absent_projection),
                )
                self.assertEqual("UTC", absent_environment["TZ"])
                self.assertFalse(
                    absent_projection.exists(),
                    "read-only preflight published an absent projection",
                )

            (java / "release").write_text(
                'JAVA_VERSION="21.0.8"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(RUNNER.FixtureBuildError, "Java 25"):
                RUNNER.inspect_build_inputs(gradle_cmd=gradle, java_home=java)

            copied = base / "fixture"
            shutil.copytree(FIXTURE, copied)
            source = copied / "src/main/java/dev/workbench/dailyloop/DailyLoopProbe.java"
            source.write_text(
                source.read_text(encoding="utf-8") + "\n// drift\n",
                encoding="utf-8",
            )
            with patch.object(RUNNER, "FIXTURE", copied), patch.object(
                RUNNER,
                "LOCK",
                copied / "fixture-lock-v1.json",
            ):
                with self.assertRaisesRegex(
                    RUNNER.FixtureBuildError,
                    "differs from its exact owner lock",
                ):
                    RUNNER._validate_fixture()

    def test_real_build_and_corrupt_artifact_fail_closed(self) -> None:
        wrapper = os.environ.get("WORKBENCH_CLEANROOM_FIXTURE_GRADLEW")
        java_home = os.environ.get("WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME")
        if not wrapper or not java_home:
            self.skipTest(
                "set WORKBENCH_CLEANROOM_FIXTURE_GRADLEW and "
                "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME to execute the physical build"
            )
        self.addCleanup(shutil.rmtree, FIXTURE / ".gradle", True)

        environment = os.environ.copy()
        environment["JAVA_HOME"] = java_home
        environment["PATH"] = f"{Path(java_home) / 'bin'}:{environment.get('PATH', '')}"
        environment.setdefault(
            "GRADLE_USER_HOME",
            str(
                ROOT
                / ".workbench/cleanroom-fixture-physical/gradle-home/"
                "generic-mod-daily-loop"
            ),
        )
        self.assertFalse((FIXTURE / ".gradle").exists())
        built = subprocess.run(
            [
                sys.executable,
                str(FIXTURE_RUNNER),
                "--gradle-cmd",
                wrapper,
                "--java-home",
                java_home,
                "--state-root",
                str(ROOT / ".workbench/cleanroom-fixture-physical"),
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, built.returncode, built.stdout + built.stderr)
        lock = _load(FIXTURE_LOCK)
        projection = RUNNER.fixture_projection_path(
            lock["declared_values"]["tree_digest"],
            state_root=ROOT / ".workbench/cleanroom-fixture-physical",
        )
        self.assertTrue(projection.is_dir())
        self.assertEqual([], _lock_errors(projection, lock))
        self.assertFalse(
            (FIXTURE / ".gradle").exists(),
            "the canonical owner fixture must never receive Gradle-local state",
        )
        command = [wrapper, "--no-daemon", "-p", str(projection)]
        command.extend([
            "--project-cache-dir",
            str(
                ROOT
                / ".workbench/cleanroom-fixture-physical/gradle-project-cache/"
                "generic-mod-daily-loop"
            ),
        ])
        cleanup_probe = projection / ".gradle/workbench-cleanup-probe"
        cleanup_probe.mkdir(parents=True, exist_ok=True)
        failed = subprocess.run(
            [
                *command,
                "--init-script",
                str(FIXTURE_CLEANUP_INIT),
                "workbenchDeliberateMissingTask",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, failed.returncode, failed.stdout + failed.stderr)
        self.assertFalse(
            (projection / ".gradle").exists(),
            "failed physical fixture builds must remove Unimined's local cache",
        )
        self.assertFalse((FIXTURE / ".gradle").exists())

        output_root = (
            projection.parents[4]
            / ".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop"
        )
        artifacts = sorted(output_root.glob("libs/workbench-daily-loop-1.0.0.jar"))
        self.assertEqual(1, len(artifacts), artifacts)
        built_digest = _digest(artifacts[0])
        corrupt = output_root / "negative/missing-mcmod-info.jar"
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(artifacts[0]) as source, zipfile.ZipFile(corrupt, "w") as target:
            for info in source.infolist():
                if info.filename != "mcmod.info":
                    target.writestr(info, source.read(info.filename))

        rejected = subprocess.run(
            [
                *command,
                "verifyFixtureArtifact",
                f"-PworkbenchFixtureArtifact={corrupt}",
                "--rerun-tasks",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode, rejected.stdout + rejected.stderr)
        self.assertIn("missing required entry mcmod.info", rejected.stdout + rejected.stderr)
        self.assertEqual(
            built_digest,
            _digest(artifacts[0]),
            "the remapped fixture JAR changed across identical rebuilds",
        )


if __name__ == "__main__":
    unittest.main()
