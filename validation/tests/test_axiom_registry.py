"""Native registry input custody, source bindings and public packaging boundary."""

from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
import zipfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import axiom_registry_conformance as conformance
import axiom_registry_runtime as runtime
from workbench_core import artifact_store
import axiom_registry_sources as sources
import build_axiom


class RegistryInputTests(unittest.TestCase):
    def test_engine_archive_keeps_notices_inside_the_distributable_folder(self):
        files = ("LICENSE", "NOTICE.md", "UPSTREAM-NOTICE.md", "third-party/fastutil/LICENSE",
                 "third-party/groovy-and-tomlj/LICENSE", "third-party/groovy-and-tomlj/NOTICE",
                 "third-party/checker-qual/LICENSE.txt", "third-party/forge/LGPL-2.1.txt", "third-party/asm.txt",
                 "third-party/guava-failureaccess-jspecify/LICENSE", "third-party/log4j-api/LICENSE",
                 "third-party/log4j-api/NOTICE", "third-party/log4j-core/LICENSE", "third-party/log4j-core/NOTICE",
                 "third-party/commons-lang3/LICENSE.txt", "third-party/commons-lang3/NOTICE.txt")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "engine.zip"
            for variant in ("valid", "orphan", "missing", "missing-forge"):
                with zipfile.ZipFile(path, "w") as archive:
                    for name in files:
                        if not ((variant == "missing" and name == "third-party/fastutil/LICENSE") or
                                (variant == "missing-forge" and name == "third-party/forge/LGPL-2.1.txt")):
                            archive.writestr("engine/" + name, "notice")
                    if variant == "orphan": archive.writestr("third-party/orphan", "outside")
                if variant == "valid": build_axiom.verify_archive(path)
                else:
                    with self.assertRaises(ValueError): build_axiom.verify_archive(path)

    def fixture(self, root, **changes):
        row = {"path": "lib/utility.jar", "size": 6, "sha256": sha256(b"pinned").hexdigest(),
               "url": "https://repo.maven.apache.org/maven2/lib/utility.jar"}
        row.update(changes)
        policy = root / "policy.json"
        policy.write_text(json.dumps({"schema": "axiom.registry-runtime.v1", "profile": "cleanroom", "runtimeFiles": [row]}))
        return policy, root / "inputs", row

    def test_provision_verifies_before_promotion_and_reuses_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, root, row = self.fixture(Path(temporary))
            with patch.object(runtime, "POLICY", policy), patch.object(artifact_store, "urlopen", return_value=io.BytesIO(b"pinned")) as download:
                self.assertEqual(root, runtime.provision(root))
                self.assertEqual(b"pinned", (root / row["path"]).read_bytes())
                runtime.provision(root)
                self.assertEqual(1, download.call_count)
                self.assertEqual([root / row["path"]], list((root / "lib").iterdir()))

    def test_wrong_size_or_digest_never_promotes(self):
        for payload in (b"short", b"oversized", b"change"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary:
                policy, root, row = self.fixture(Path(temporary))
                with patch.object(runtime, "POLICY", policy), patch.object(artifact_store, "urlopen", return_value=io.BytesIO(payload)):
                    with self.assertRaises(ValueError): runtime.provision(root)
                    self.assertFalse((root / row["path"]).exists())
                    self.assertEqual([], list((root / "lib").iterdir()))

    def test_invalid_policy_fails_before_any_provisioning(self):
        for change in ("schema", "empty", "duplicate"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                policy, root, row = self.fixture(Path(temporary))
                value = json.loads(policy.read_bytes())
                if change == "schema": value["schema"] = "unknown"
                else: value["runtimeFiles"] = [] if change == "empty" else [row, row]
                policy.write_text(json.dumps(value))
                with patch.object(runtime, "POLICY", policy), patch.object(artifact_store, "urlopen") as download:
                    with self.assertRaises(ValueError): runtime.provision(root)
                    download.assert_not_called()
                    self.assertFalse(root.exists())

    def test_existing_changed_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, root, row = self.fixture(Path(temporary))
            path = root / row["path"]; path.parent.mkdir(parents=True); path.write_bytes(b"change")
            with patch.object(runtime, "POLICY", policy), patch.object(artifact_store, "urlopen") as download:
                with self.assertRaisesRegex(ValueError, "changed"): runtime.provision(root)
                download.assert_not_called()
                self.assertEqual(b"change", path.read_bytes())

    def test_invalid_origin_paths_and_symlink_roots_fail_closed(self):
        for changes in ({"url": "https://untrusted.invalid/utility.jar"}, {"path": "../outside.jar"}, {"path": "/outside.jar"}, {"path": "lib\\utility.jar"}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as temporary:
                policy, root, _ = self.fixture(Path(temporary), **changes)
                with patch.object(runtime, "POLICY", policy), patch.object(artifact_store, "urlopen") as download:
                    with self.assertRaises(ValueError): runtime.provision(root)
                    download.assert_not_called()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / "alias").symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "indirect"): runtime.verify(root / "alias")

    def test_concurrent_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, root, row = self.fixture(Path(temporary))
            def race(source, destination):
                destination.write_bytes(b"concurrent")
                raise FileExistsError(destination)
            with patch.object(artifact_store, 'urlopen', return_value=io.BytesIO(b'pinned')):
                artifact_store.fetch_verified_artifact(url=row['url'], expected_sha256=row['sha256'],
                    expected_size=row['size'], state_root=root / '.workbench', label='Axiom registry input')
            with patch.object(runtime, "POLICY", policy), patch.object(runtime.os, "link", side_effect=race):
                with self.assertRaises(FileExistsError): runtime.provision(root)
                self.assertEqual(b"concurrent", (root / row["path"]).read_bytes())

    def test_profile_policy_is_packaged_and_native_dependency_hash_matches(self):
        profile = ROOT / "profiles/platforms/cleanroom"
        policy = json.loads(runtime.POLICY.read_bytes())
        package = tomllib.loads((profile / "pyproject.toml").read_text())
        self.assertIn("registry-runtime.json", package["tool"]["setuptools"]["package-data"]["workbench_resources.profiles.platforms.cleanroom"])
        self.assertIn("'registry-runtime': 'registry-runtime.json'", (profile / "src/workbench_profile_cleanroom/__init__.py").read_text())
        build = ROOT / "modules/axiom/jvm"
        self.assertIn(policy["collectionImplementation"]["coordinate"], (build / "gradle.lockfile").read_text())
        tree = ET.fromstring((build / "gradle/verification-metadata.xml").read_bytes())
        namespace = {"m": "https://schema.gradle.org/dependency-verification"}
        component = next(c for c in tree.findall(".//m:component", namespace) if c.attrib["name"] == "fastutil")
        jar = next(a for a in component if a.attrib["name"].endswith(".jar"))
        self.assertEqual(policy["collectionImplementation"]["sha256"], next(iter(jar)).attrib["value"])
        self.assertEqual("qz", policy["classes"]["IntIdentityHashBiMap"]["name"])
        self.assertIn("from(\"../../../profiles/platforms/cleanroom/registry-runtime.json\")", (build / "build.gradle.kts").read_text())
        self.assertNotIn("minecraft-1.12.2", (build / "build.gradle.kts").read_text())

    def test_every_extraction_has_an_immutable_source_binding(self):
        lock = json.loads(conformance.LOCK.read_bytes())
        for path in (*sources.PATHS.values(), sources.FLUID_PATH):
            rows = [r for r in lock["references"] if r["repository"] == "gtceu" and r["path"] == path]
            self.assertEqual(1, len(rows), path)
            self.assertEqual(64, len(rows[0]["sha256"]))
        with self.assertRaisesRegex(ValueError, "unadmitted"): sources.extract("FluidBuilder", "")
        with self.assertRaisesRegex(ValueError, "boundary differs"): sources.fluid_storage("package changed;")

    def test_comparison_never_overwrites_a_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"; path.write_bytes(b"previous")
            args = ["--report", str(path)]
            for name in ("gtceu", "java-home", "engine-home", "registry-root"): args += ["--" + name, temporary]
            with self.assertRaisesRegex(ValueError, "already exists"): conformance.main(args)
            self.assertEqual(b"previous", path.read_bytes())
