"""Portable observer preparation and strict launch declarations; no game launch."""

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from workbench_profile_supersymmetry import recipe_capture_inputs as inputs
from workbench_profile_supersymmetry import recipe_capture_runtime as runtime


def encoded(value, *, ascii_only=False):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii_only).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_row(path, raw=b"fixture"):
    return {"path": path, "mode": 0o100644, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def candidate():
    files = [file_row("config/new.cfg"), file_row("groovy/postInit/new_é.groovy"),
             file_row("groovy/runConfig.json"), file_row("pack.toml")]
    value = {"format": "workbench-saved-candidate-v1", "source": {
        "root_uri": "file:///private/pack", "revision": "1" * 40, "dirty": True,
        "file_count": len(files) + 1, "source_sha256": "2" * 64, "index_sha256": "3" * 64},
        "files": files}
    value["id"] = "candidate:sha256:" + hashlib.sha256(encoded(value, ascii_only=True)).hexdigest()
    return value


def input_record():
    return inputs.build_capture_input(candidate(), platform=deepcopy(inputs.QUALIFIED_PLATFORM),
        runtime_artifacts=deepcopy(inputs.QUALIFIED_ARTIFACTS), capture_id="fixture-capture",
        launch_id="fixture-launch", candidate_lock_sha256="4" * 64, adapter_profile_sha256="5" * 64,
        deleted_paths=["scripts/deleted.zs"], observation_preparation=deepcopy(runtime.PREPARATION))


def receipt(root):
    request = {"format": "workbench-forge-observer-build-request-v1", "inputs": [],
               "sources": runtime.observer_source_manifest(), "source_count": 10}
    request["binding"] = digest(request)
    return {"format": "workbench-forge-observer-build-v1", "request": request,
            "compiler": {"format": "workbench-process-capture-v1", "id": "fixture-compiler",
                         "binding": request["binding"]},
            "exit_code": 0, "state": "compiled-not-runtime-qualified", "class_count": 1,
            "artifact": {"path": str(root / "observer.jar"), "size": 20, "sha256": "6" * 64}}


def launch_fixture(root):
    capture = input_record()
    roles = {key: (key + ".jar" if key in {"forge_sha256", "minecraft_sha256"}
                   else "mods/" + key + ".jar") for key in inputs.QUALIFIED_ARTIFACTS}
    files = [{"path": roles[key], "size": 1, "sha256": value, "mode": 0o644}
             for key, value in inputs.QUALIFIED_ARTIFACTS.items()]
    files += [deepcopy(row) for row in capture["pack_source"]["candidate"]["files"] if row["path"] != "pack.toml"]
    files.append({"path": runtime.OBSERVER_PATH, "size": 20, "sha256": "6" * 64, "mode": 0o644})
    files.append(file_row("mods/locally-built-extra.jar"))
    return capture, dict(runtime_files=files, artifact_paths=roles, observer_path=runtime.OBSERVER_PATH,
        observer_build=receipt(root), java={"path": str(root / "Java 資料/bin/java"),
            "size": 10, "sha256": "7" * 64, "major": 8}, runtime_root=root / "Runtime é",
        input_manifest_path=root / "input.json", input_manifest_sha256="8" * 64,
        output=root / "capture", heap_mib=2048)


def native_test_path(path):
    """Create test fixtures independently of the profile's spelling helper."""
    if os.name != "nt":
        return path
    value = str(path.absolute())
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


@contextmanager
def deep_fixture_root():
    temporary = tempfile.TemporaryDirectory(prefix="observer-long-")
    base = Path(temporary.name)
    root = base / ("a" * 80) / ("b" * 80) / ("Unicode 資料 " + "c" * 80)
    try:
        native_test_path(root).mkdir(parents=True)
        yield root
    finally:
        # The Windows test host may have legacy MAX_PATH enabled. Cleanup must
        # address the same tree without leaving a failing fixture behind.
        tempfile.TemporaryDirectory._rmtree(str(native_test_path(base)))
        temporary.cleanup()


class RecipeCaptureLaunchTests(unittest.TestCase):
    def test_complete_saved_root_mapping_and_distinct_build_game_java(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture, kwargs = launch_fixture(root)
            before = deepcopy((capture, kwargs))
            plan = runtime.plan_capture_launch(capture, **kwargs)
            self.assertEqual(plan, runtime.plan_capture_launch(capture, **kwargs))
            self.assertEqual(before, (capture, kwargs))
            self.assertFalse((root / "Runtime é").exists())  # Planning is value-only.
            self.assertEqual(kwargs["java"]["path"], plan["argv"][0])
            self.assertEqual(["pack.toml"], plan["unmapped_source_files"])
            self.assertEqual(3, len(plan["mapped_source_files"]))
            self.assertIn("-Dworkbench.runtimeGraph.enabled=true", plan["argv"])
            self.assertIn("-Dworkbench.runtimeGraph.input_manifest_sha256=" + "8" * 64, plan["argv"])
            self.assertEqual("nogui", plan["argv"][-1])
            self.assertFalse(plan["native_executed"])

    def test_complete_root_replacement_refuses_stale_deleted_or_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            for change in ("deleted", "stale", "missing", "changed", "mode"):
                with self.subTest(change=change):
                    capture, kwargs = launch_fixture(Path(directory))
                    if change == "deleted":
                        kwargs["runtime_files"].append(file_row("scripts/deleted.zs"))
                    elif change == "stale":
                        kwargs["runtime_files"].append(file_row("resources/template-only.json"))
                    elif change == "missing":
                        kwargs["runtime_files"] = [row for row in kwargs["runtime_files"] if row["path"] != "config/new.cfg"]
                    elif change == "changed":
                        next(row for row in kwargs["runtime_files"] if row["path"] == "config/new.cfg")["sha256"] = "a" * 64
                    else:
                        next(row for row in kwargs["runtime_files"] if row["path"] == "config/new.cfg")["mode"] = 0o755
                    with self.assertRaisesRegex(ValueError, "source"):
                        runtime.plan_capture_launch(capture, **kwargs)

    def test_changed_pinned_runtime_major_observer_or_preparation_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            for change in ("runtime", "java", "observer", "preparation", "receipt", "compiler"):
                with self.subTest(change=change):
                    capture, kwargs = launch_fixture(Path(directory))
                    if change == "runtime":
                        kwargs["runtime_files"][0]["sha256"] = "a" * 64
                    elif change == "java":
                        kwargs["java"]["major"] = 21
                    elif change == "observer":
                        next(row for row in kwargs["runtime_files"] if row["path"] == runtime.OBSERVER_PATH)["sha256"] = "b" * 64
                    elif change == "preparation":
                        capture.pop("observation_preparation")
                    elif change == "receipt":
                        kwargs["observer_build"]["request"]["source_count"] = 9
                    else:
                        kwargs["observer_build"]["compiler"]["binding"] = "c" * 64
                    with self.assertRaises(ValueError):
                        runtime.plan_capture_launch(capture, **kwargs)

    def test_runtime_role_lookup_accepts_original_library_copy_but_rejects_duplicate_mod(self):
        with tempfile.TemporaryDirectory() as directory:
            _, kwargs = launch_fixture(Path(directory))
            rows = kwargs["runtime_files"]
            rows.append({"path": "libraries/forge.jar", "size": 1,
                         "sha256": inputs.QUALIFIED_ARTIFACTS["forge_sha256"]})
            self.assertEqual(kwargs["artifact_paths"], runtime.select_runtime_artifacts(rows))
            rows.append({"path": "mods/duplicate-gt.jar", "size": 1,
                         "sha256": inputs.QUALIFIED_ARTIFACTS["gregtech_sha256"]})
            with self.assertRaisesRegex(ValueError, "gregtech_sha256.*found 2"):
                runtime.select_runtime_artifacts(rows)

    def test_compiled_groovy_cache_cannot_survive_saved_source_replacement(self):
        self.assertIn("cache/groovy", runtime.descriptor()["runtime_exclusions"])
        with tempfile.TemporaryDirectory() as directory:
            capture, kwargs = launch_fixture(Path(directory))
            kwargs["runtime_files"].append(file_row("cache/groovy/postInit/deletedRecipe.clz"))
            with self.assertRaisesRegex(ValueError, "previous compiled Groovy script cache"):
                runtime.plan_capture_launch(capture, **kwargs)

    def test_runtime_portability_checks_directories_as_well_as_leaf_names(self):
        with tempfile.TemporaryDirectory() as directory:
            for extra in ("Mods/different.jar", "mods", "mods/../escaped.jar"):
                with self.subTest(extra=extra):
                    _, kwargs = launch_fixture(Path(directory))
                    kwargs["runtime_files"].append(file_row(extra))
                    with self.assertRaises(ValueError):
                        runtime.select_runtime_artifacts(kwargs["runtime_files"])

    def test_observer_launch_identifier_and_path_rules_match_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            capture, kwargs = launch_fixture(Path(directory))
            capture["capture_id"] = "Unicode-é"
            with self.assertRaisesRegex(ValueError, "capture_id"):
                runtime.plan_capture_launch(capture, **kwargs)
            capture["capture_id"] = "ordinary-id"
            kwargs["output"] = kwargs["runtime_root"] / "capture"
            with self.assertRaisesRegex(ValueError, "outside"):
                runtime.plan_capture_launch(capture, **kwargs)

    def test_host_materialized_modes_preserve_original_git_source_declarations(self):
        cases = (("groovy/run.groovy", 0o100755, 0o644, 0o755),
                 ("scripts/helper.sh", 0o100755, 0o644, 0o755),
                 ("scripts/tool.EXE", 0o100644, 0o755, 0o644),
                 ("scripts/tool.com", 0o100644, 0o755, 0o644),
                 ("scripts/tool.bat", 0o100644, 0o755, 0o644),
                 ("scripts/tool.cmd", 0o100644, 0o755, 0o644))
        for name, declared, windows, posix in cases:
            with self.subTest(name=name):
                self.assertEqual(windows, runtime._materialized_source_mode(name, declared, windows=True))
                self.assertEqual(posix, runtime._materialized_source_mode(name, declared, windows=False))
        with tempfile.TemporaryDirectory() as directory:
            capture, kwargs = launch_fixture(Path(directory))
            source = deepcopy(capture["pack_source"]["candidate"])
            next(row for row in source["files"] if row["path"] == "groovy/postInit/new_é.groovy")["mode"] = 0o100755
            source.pop("id")
            source["id"] = "candidate:sha256:" + hashlib.sha256(encoded(source, ascii_only=True)).hexdigest()
            capture["pack_source"] = inputs.build_source_binding(source, deleted_paths=["scripts/deleted.zs"])
            capture["pack_binding_id"] = capture["pack_source"]["id"]
            original = runtime._materialized_source_mode
            with patch.object(runtime, "_materialized_source_mode", side_effect=lambda name, declared: original(name, declared, windows=True)):
                runtime.plan_capture_launch(capture, **kwargs)
            self.assertEqual(0o100755, next(row for row in capture["pack_source"]["candidate"]["files"]
                                           if row["path"] == "groovy/postInit/new_é.groovy")["mode"])


class ObserverBuildTests(unittest.TestCase):
    def setUp(self):
        private = patch.object(runtime, "secure_private_path", side_effect=lambda path, **_: path)
        self.private = private.start()
        self.addCleanup(private.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="observer é ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jdk = self.root / "Build JDK"
        (self.jdk / "bin").mkdir(parents=True)
        (self.jdk / "release").write_text('JAVA_VERSION="1.8.0_292"\n')
        runtime._java_tool(self.jdk, "javac", windows=os.name == "nt").write_bytes(b"compiler")
        self.classpath = [self.root / "original.jar"]
        self.classpath[0].write_bytes(b"original classpath")

    @staticmethod
    def compiler(argv, **kwargs):
        target = kwargs["cwd"] / argv[argv.index("-d") + 1] / "dev/workbench/crucible/forgerecipes/ForgeRecipeObserverMod.class"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34fixture")
        return SimpleNamespace(exit_code=0, reference={"format": "workbench-process-capture-v1",
            "id": "test-process", "binding": kwargs["binding"]}, stderr=SimpleNamespace(path="stderr"))

    def test_exact_packaged_sources_compile_via_api_with_reproducible_jar(self):
        with patch.object(runtime, "capture_process", side_effect=self.compiler) as process:
            first = runtime.build_observer(self.jdk, self.classpath, self.root / "build 1", cancelled=Event())
            second = runtime.build_observer(self.jdk, self.classpath, self.root / "build 2", cancelled=Event())
        self.assertEqual(2, process.call_count)
        self.assertEqual(first["artifact"]["sha256"], second["artifact"]["sha256"])
        self.assertEqual(first["request"]["binding"], second["request"]["binding"])
        self.assertEqual(10, first["request"]["source_count"])
        self.assertEqual("compiled-not-runtime-qualified", first["state"])
        self.assertEqual(first["artifact"], runtime._observer_receipt(first))
        modified = deepcopy(first)
        modified["request"]["staged_sources"][0]["sha256"] = "f" * 64
        modified["request"].pop("binding")
        modified["request"]["binding"] = digest(modified["request"])
        modified["compiler"]["binding"] = modified["request"]["binding"]
        with self.assertRaisesRegex(ValueError, "staged source mapping"):
            runtime._observer_receipt(modified)
        self.private.assert_any_call(self.root / "build 1", directory=True)
        self.private.assert_any_call(self.root / "build 1/request.json", directory=False)
        self.private.assert_any_call(Path(first["artifact"]["path"]), directory=False)
        argv, kwargs = process.call_args
        self.assertEqual(str(runtime._java_tool(self.jdk, "javac", windows=os.name == "nt")), argv[0][0])
        self.assertIsNone(kwargs["timeout_seconds"])
        self.assertIsNone(kwargs["output_limit"])
        self.assertIn("classes", argv[0][argv[0].index("-d") + 1])
        source_args = [arg for arg in argv[0] if arg.endswith(".java")]
        self.assertEqual(10, len(source_args))
        self.assertTrue(all(arg.startswith("sources/") and arg.isascii() for arg in source_args))
        self.assertEqual("../original.jar".replace("/", os.sep), argv[0][argv[0].index("-classpath") + 1])
        for row in first["request"]["staged_sources"]:
            self.assertEqual(row["sha256"], hashlib.sha256((self.root / "build 1" / row["path"]).read_bytes()).hexdigest())
        with zipfile.ZipFile(first["artifact"]["path"]) as jar:
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in jar.infolist()))

    def test_java_tool_selection_includes_windows_exe(self):
        self.assertEqual(self.jdk / "bin/javac.exe", runtime._java_tool(self.jdk, "javac", windows=True))
        self.assertEqual(self.jdk / "bin/javac", runtime._java_tool(self.jdk, "javac", windows=False))

    def test_build_refuses_changed_inputs_failed_compiler_and_wrong_bytecode(self):
        def changed(argv, **kwargs):
            result = self.compiler(argv, **kwargs)
            self.classpath[0].write_bytes(b"changed input")
            return result

        def wrong_bytecode(argv, **kwargs):
            result = self.compiler(argv, **kwargs)
            target = kwargs["cwd"] / argv[argv.index("-d") + 1] / "dev/workbench/crucible/forgerecipes/ForgeRecipeObserverMod.class"
            target.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x41fixture")
            return result

        failed = SimpleNamespace(exit_code=1, reference={"id": "failed"}, stderr=SimpleNamespace(path="compiler-stderr"))
        for label, mock in (("changed", changed), ("bytecode", wrong_bytecode), ("compiler", lambda *a, **k: failed)):
            with self.subTest(label=label), patch.object(runtime, "capture_process", side_effect=mock):
                with self.assertRaises(ValueError):
                    runtime.build_observer(self.jdk, self.classpath, self.root / label, cancelled=Event())
                self.assertTrue((self.root / label / "result.json").is_file())
                self.assertFalse((self.root / label / "workbench-forge-recipe-observer-0.1.0.jar").exists())

    def test_changed_staged_source_or_added_source_refuses_successful_compiler(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                def compiler(argv, **kwargs):
                    result = self.compiler(argv, **kwargs)
                    target = kwargs["cwd"] / "sources" / ("Unexpected.java" if extra else "ForgeRecipeObserverMod.java")
                    target.write_bytes(b"changed staged source")
                    return result
                with patch.object(runtime, "capture_process", side_effect=compiler):
                    with self.assertRaisesRegex(ValueError, "staged observer source"):
                        runtime.build_observer(self.jdk, self.classpath, self.root / ("extra" if extra else "changed-staged"), cancelled=Event())

    def test_windows_relative_classpath_refuses_non_ascii_dependency_names(self):
        self.assertEqual([os.path.join("..", "original.jar")], runtime._compiler_classpath(self.classpath, self.root / "build", windows=True))
        with self.assertRaisesRegex(ValueError, "ASCII relative paths"):
            runtime._compiler_classpath([self.root / "nonascii-é.jar"], self.root / "build", windows=True)

    def test_cancelled_and_wrong_jdk_builds_do_not_create_attempt(self):
        stopped = Event()
        stopped.set()
        with patch.object(runtime, "capture_process") as process:
            with self.assertRaisesRegex(ValueError, "cancelled"):
                runtime.build_observer(self.jdk, self.classpath, self.root / "cancelled", cancelled=stopped)
            (self.jdk / "release").write_text('JAVA_VERSION="21.0.1"\n')
            with self.assertRaisesRegex(ValueError, "Java 8"):
                runtime.build_observer(self.jdk, self.classpath, self.root / "wrong", cancelled=Event())
        process.assert_not_called()
        self.assertFalse((self.root / "cancelled").exists())
        self.assertFalse((self.root / "wrong").exists())


class ForgeClasspathTests(unittest.TestCase):
    def fixture(self, root, entries):
        forge = root / "forge.jar"
        with zipfile.ZipFile(forge, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\nMain-Class: net.minecraftforge.fml.relauncher.ServerLaunchWrapper\r\nClass-Path: " + entries + "\r\n\r\nName: ignored.class\r\nClass-Path: ignored.jar\r\n")
        (root / "minecraft.jar").write_bytes(b"minecraft")
        (root / "libraries").mkdir()
        (root / "libraries/library.jar").write_bytes(b"library")
        rows = [file_row(name, (root / name).read_bytes()) for name in
                ("forge.jar", "minecraft.jar", "libraries/library.jar")]
        return rows, {"forge_sha256": "forge.jar", "minecraft_sha256": "minecraft.jar"}

    def test_folded_forge_launch_classpath_is_verified_in_selected_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, roles = self.fixture(root, "libraries/lib\r\n rary.jar minecraft.jar")
            with patch.object(runtime, "select_runtime_artifacts", return_value=roles):
                paths = runtime.observer_classpath(root, rows, roles)
                self.assertEqual([root / name for name in ("forge.jar", "libraries/library.jar", "minecraft.jar")], paths)
                (root / "libraries/library.jar").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "bytes changed"):
                    runtime.observer_classpath(root, rows, roles)

    def test_external_missing_and_duplicate_launch_dependencies_are_refused(self):
        for entries in ("https://example.invalid/library.jar minecraft.jar", "missing.jar minecraft.jar",
                        "libraries/../escape.jar minecraft.jar", "minecraft.jar minecraft.jar"):
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rows, roles = self.fixture(root, entries)
                with patch.object(runtime, "select_runtime_artifacts", return_value=roles):
                    with self.assertRaises(ValueError):
                        runtime.observer_classpath(root, rows, roles)


class LongPathPreparationTests(unittest.TestCase):
    def test_windows_access_spelling_keeps_ordinary_paths_as_the_contract(self):
        for root in ("C:\\Users\\developer\\", "\\\\server\\share\\"):
            short = root + "file.jar"
            long = root + ("component\\" * 30) + "file.jar"
            self.assertEqual(short, runtime._windows_access_path(short))
            expected = "\\\\?\\UNC\\" + long[2:] if root.startswith("\\\\") else "\\\\?\\" + long
            self.assertEqual(expected, runtime._windows_access_path(long))
        for value in ("relative.jar", "C:\\root\\..\\file.jar", "\\\\?\\C:\\file.jar", "\\\\.\\C:\\file.jar"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime._windows_access_path(value)

    def test_deep_forge_manifest_and_classpath_hashes_retain_ordinary_paths(self):
        with deep_fixture_root() as root:
            with zipfile.ZipFile(native_test_path(root / "forge.jar"), "w") as jar:
                jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\nMain-Class: net.minecraftforge.fml.relauncher.ServerLaunchWrapper\r\nClass-Path: libraries/library.jar minecraft.jar\r\n\r\n")
            native_test_path(root / "libraries").mkdir()
            for name in ("libraries/library.jar", "minecraft.jar"):
                native_test_path(root / name).write_bytes(name.encode())
            names = ("forge.jar", "libraries/library.jar", "minecraft.jar")
            rows = [file_row(name, native_test_path(root / name).read_bytes()) for name in names]
            roles = {"forge_sha256": "forge.jar", "minecraft_sha256": "minecraft.jar"}
            with patch.object(runtime, "select_runtime_artifacts", return_value=roles):
                self.assertEqual([root / name for name in names], runtime.observer_classpath(root, rows, roles))
                native_test_path(root / "libraries/library.jar").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "selected runtime bytes changed"):
                    runtime.observer_classpath(root, rows, roles)

    def test_class_descendants_cross_limit_from_short_build_root(self):
        with deep_fixture_root() as deep:
            root = deep.parents[2]
            output = root / ("build-" + "d" * (229 - len(str(root)) - 7))
            self.assertLess(len(str(output / "classes")), 240)
            jdk = root / "jdk"
            native_test_path(jdk / "bin").mkdir(parents=True)
            native_test_path(jdk / "release").write_text('JAVA_VERSION="1.8.0_492"\n')
            native_test_path(runtime._java_tool(jdk, "javac", windows=os.name == "nt")).write_bytes(b"compiler")
            dependency = root / "original.jar"
            native_test_path(dependency).write_bytes(b"dependency")
            names = ("dev/workbench/crucible/forgerecipes/ForgeRecipeObserverMod.class",
                     "longpackage/" + "e" * 80 + "/nested/Additional.class")
            self.assertTrue(all(len(str(output / "classes" / name)) > 260 for name in names))

            def compiler(argv, **kwargs):
                for name in names:
                    target = native_test_path(output / "classes" / name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34fixture")
                return SimpleNamespace(exit_code=0, reference={"format": "workbench-process-capture-v1",
                    "id": "test-process", "binding": kwargs["binding"]}, stderr=SimpleNamespace(path="stderr"))

            with patch.object(runtime, "secure_private_path", side_effect=lambda path, **_: path), \
                    patch.object(runtime, "capture_process", side_effect=compiler):
                result = runtime.build_observer(jdk, [dependency], output, cancelled=Event())
            self.assertEqual(2, result["class_count"])
            with zipfile.ZipFile(native_test_path(Path(result["artifact"]["path"]))) as jar:
                self.assertEqual(set(names) | {"META-INF/MANIFEST.MF"}, set(jar.namelist()))

    def test_deep_packaged_sources_tools_build_outputs_and_tamper_checks(self):
        original_root = runtime._profile_root()
        with deep_fixture_root() as root:
            resource_root = root / "installed 資料"
            for name in runtime._SOURCE_PATHS:
                target = native_test_path(resource_root / name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(native_test_path(original_root / name).read_bytes())
            jdk = root / "Build JDK"
            native_test_path(jdk / "bin").mkdir(parents=True)
            native_test_path(jdk / "release").write_text('JAVA_VERSION="1.8.0_492"\n')
            native_test_path(runtime._java_tool(jdk, "javac", windows=os.name == "nt")).write_bytes(b"compiler")
            dependency = root / "original.jar"
            native_test_path(dependency).write_bytes(b"original dependency")

            def compiler(argv, **kwargs):
                self.assertTrue(all(not arg.startswith("\\\\?\\") for arg in argv))
                self.assertTrue(all(arg.isascii() for arg in argv if arg.endswith(".java")))
                self.assertEqual(os.path.join("..", "original.jar"), argv[argv.index("-classpath") + 1])
                target = native_test_path(kwargs["cwd"] / "classes/dev/workbench/crucible/forgerecipes/ForgeRecipeObserverMod.class")
                target.parent.mkdir(parents=True)
                target.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34fixture")
                if kwargs["cwd"].name == "changed-classpath":
                    native_test_path(dependency).write_bytes(b"changed")
                if kwargs["cwd"].name == "changed-staged":
                    native_test_path(kwargs["cwd"] / "sources/ForgeRecipeObserverMod.java").write_bytes(b"changed")
                return SimpleNamespace(exit_code=0, reference={"format": "workbench-process-capture-v1",
                    "id": "test-process", "binding": kwargs["binding"]}, stderr=SimpleNamespace(path="stderr"))

            with patch.object(runtime, "_profile_root", return_value=resource_root), \
                    patch.object(runtime, "secure_private_path", side_effect=lambda path, **_: path), \
                    patch.object(runtime, "capture_process", side_effect=compiler):
                result = runtime.build_observer(jdk, [dependency], root / "build", cancelled=Event())
                self.assertEqual("compiled-not-runtime-qualified", result["state"])
                self.assertEqual(str(root / "build" / Path(runtime.OBSERVER_PATH).name), result["artifact"]["path"])
                self.assertEqual(result, json.loads(native_test_path(root / "build/result.json").read_bytes()))
                self.assertTrue(all(not row["path"].startswith("\\\\?\\") for row in result["request"]["inputs"]))
                self.assertEqual(runtime._identity(root / "build" / Path(runtime.OBSERVER_PATH).name), result["artifact"])
                with zipfile.ZipFile(native_test_path(Path(result["artifact"]["path"]))) as jar:
                    self.assertIn("dev/workbench/crucible/forgerecipes/ForgeRecipeObserverMod.class", jar.namelist())
                for name, message in (("changed-classpath", "build input changed"), ("changed-staged", "staged observer source bytes changed")):
                    with self.subTest(name=name), self.assertRaisesRegex(ValueError, message):
                        runtime.build_observer(jdk, [dependency], root / name, cancelled=Event())
                    self.assertTrue(native_test_path(root / name / "result.json").is_file())
                    self.assertFalse(native_test_path(root / name / Path(runtime.OBSERVER_PATH).name).exists())


class CaptureServerPropertiesTests(unittest.TestCase):
    def test_only_capture_properties_are_replaced_and_online_mode_survives(self):
        original = (b"# keep this comment\r\nonline-mode=true\r\ncustom=value\r\n"
                    b"server-port:25565\r\nmax-tick-time=60000\r\n"
                    b"server\\u002dport = 25566\r\nlevel-\\\r\n name=old-world\r\n")
        prepared = runtime.prepare_server_properties(original)
        self.assertTrue(prepared.startswith(b"# keep this comment\r\nonline-mode=true\r\ncustom=value\r\n"))
        self.assertEqual(1, prepared.count(b"server-port="))
        self.assertNotIn(b"25565", prepared)
        self.assertNotIn(b"25566", prepared)
        self.assertNotIn(b"old-world", prepared)
        self.assertIn(b"max-tick-time=-1\n", prepared)
        self.assertEqual(prepared, runtime.prepare_server_properties(prepared))

    def test_java_escape_key_parsing_preserves_unrelated_records(self):
        original = b"# server-port=comment\\\ncustom\\ key=a\\\n b\nserver\\-port=99\nserver\\ port=other\n"
        prepared = runtime.prepare_server_properties(original)
        self.assertTrue(prepared.startswith(b"# server-port=comment\\\ncustom\\ key=a\\\n b\nserver\\ port=other\n"))
        self.assertNotIn(b"99", prepared)
        self.assertIn(b"server-port=0\n", prepared)

    def test_trailing_continuation_cannot_consume_an_appended_setting(self):
        prepared = runtime.prepare_server_properties(b"custom=trailing\\")
        self.assertTrue(prepared.startswith(b"custom=trailing\\\n\nenable-query=false\n"))
        with self.assertRaisesRegex(ValueError, "Unicode escape"):
            runtime.prepare_server_properties(b"custom=bad\\uXX00")


if __name__ == "__main__":
    unittest.main()
