"""Focused assembly and refusal checks for the downloadable Linux bundle."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import assemble_install_bundle as bundle  # noqa: E402
import render_install_hook as hook  # noqa: E402
from component_versions import load_authority  # noqa: E402
from release_track import artifact_filename  # noqa: E402
from workbench_axiom.cli import ENGINE_NOTICES  # noqa: E402
from workbench_core.configuration import (  # noqa: E402
    default_client_configuration_path,
    load_workbench_configuration,
)
from workbench_core.runtime_java import (  # noqa: E402
    ensure_java_runtime,
    load_java_runtime_policy,
)


SOURCE_SHA = "a" * 64


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class InstallBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory(prefix="workbench-bundle-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.verified = {
            "vscode": {"client_id": "vscode", "package_artifact_id": "vscode-vsix", "version": "test", "host_boundary": "test"},
            "intellij-community": {"client_id": "intellij-community", "package_artifact_id": "intellij-community-zip", "version": "test", "host_boundary": "test"},
        }

    def _inputs(self, *, tui: bool = True, unsafe_engine: bool = False,
                edition: str = bundle.FULL_EDITION) -> dict[str, Path]:
        wheelhouse = self.inputs / "wheelhouse"
        wheels = wheelhouse / "wheels"
        wheels.mkdir(parents=True)
        _, authority = load_authority(ROOT)
        if edition == bundle.CLIENT_EDITION:
            _roots, closure = bundle.selected_components(bundle.CLIENT_ROOT_COMPONENTS, root=ROOT)
            native_versions = {row["id"]: row["version"] for row in closure}
            selected = list(bundle.CLIENT_ROOT_COMPONENTS)
            if not tui:
                native_versions.pop("workbench-tui")
                selected.remove("workbench-tui")
        else:
            native_versions = {name: row["version"] for name, row in authority.items() if row["kind"] == "python"}
            if tui:
                native_versions["workbench-tui"] = authority["workbench-tui"]["version"]
            selected = sorted(native_versions)
        records = []
        for name, version in sorted({**native_versions, "pip": "26.1.2"}.items()):
            filename = ("pip-26.1.2-py3-none-any.whl" if name == "pip"
                        else f"{name.replace('-', '_')}-{version}-py3-none-any.whl")
            raw = f"synthetic {name} {version}\n".encode()
            (wheels / filename).write_bytes(raw)
            records.append({"filename": filename, "name": name, "version": version,
                            "size": len(raw), "sha256": _sha(raw)})
        native = {"format": "workbench-native-wheelhouse-v1", "source_sha256": SOURCE_SHA,
                  "selected_components": selected, "native_versions": native_versions,
                  "target": bundle.TARGET, "wheels": records, "qualified": False}
        _write_json(wheelhouse / "wheelhouse.json", native)
        (wheelhouse / "requirements.lock").write_text(
            "".join(f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n" for row in records),
            encoding="utf-8")
        for name in ("install_workbench.py", "verify_wheelhouse.py"):
            (wheelhouse / name).write_bytes((ROOT / "tools" / name).read_bytes())
        for name in ("LICENSE", "NOTICE.md"):
            (wheelhouse / name).write_bytes((ROOT / name).read_bytes())
        guide = self.inputs / "GETTING-STARTED.md"
        guide.write_text("# Install Workbench\n", encoding="utf-8")
        if edition == bundle.CLIENT_EDITION:
            return {"wheelhouse": wheelhouse, "guide": guide}
        engine = self.inputs / artifact_filename(ROOT, "workbench-axiom-engine.distribution")
        with io.BytesIO() as memory:
            with zipfile.ZipFile(memory, "w") as jar:
                jar.writestr("test.txt", "synthetic engine\n")
            jar_bytes = memory.getvalue()
        with zipfile.ZipFile(engine, "w") as archive:
            prefix = engine.stem + "/"
            for name in ENGINE_NOTICES:
                archive.writestr(prefix + name, "test notice\n")
            jar_name = "workbench-axiom-engine-" + authority["workbench-axiom-engine"]["version"] + ".jar"
            archive.writestr(prefix + "lib/" + jar_name, jar_bytes)
            archive.writestr(prefix + "engine-manifest.json", json.dumps({
                "schema": "axiom.installation.v1", "component": "workbench-axiom-engine",
                "version": authority["workbench-axiom-engine"]["version"],
                "jars": {jar_name: _sha(jar_bytes)},
            }))
            if unsafe_engine:
                archive.writestr("../escape", "bad")
        vscode = self.inputs / artifact_filename(ROOT, "workbench-vscode.vsix")
        intellij = self.inputs / artifact_filename(ROOT, "workbench-intellij-community.plugin-zip")
        for path in (vscode, intellij):
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("package.txt", "synthetic client\n")
        rows = []
        for client_id, path in (("intellij-community", intellij), ("vscode", vscode)):
            digest = bundle._sha256(path)
            rows.append({**self.verified[client_id], "path": path.name, "size": path.stat().st_size,
                         "sha256": digest, "artifact_identity": "artifact:sha256:" + digest,
                         "build_tools": {}})
        clients = {"format": "workbench-developer-client-artifact-manifest-v1", "schema_version": 1,
                   "client_artifact_manifest_id": "pending", "lane": "public-v1", "artifacts": rows}
        clients["client_artifact_manifest_id"] = bundle.client_manifest_id(clients)
        clients_manifest = self.inputs / bundle.CLIENT_MANIFEST
        _write_json(clients_manifest, clients)
        return {"wheelhouse": wheelhouse, "engine_zip": engine, "vscode_vsix": vscode,
                "intellij_zip": intellij, "clients_manifest": clients_manifest, "guide": guide}

    def _assemble(self, inputs: dict[str, Path], output: Path,
                  *, edition: str = bundle.FULL_EDITION) -> dict:
        with (patch.object(bundle, "_git_metadata", return_value=("b" * 40, "c" * 40)),
              patch.object(bundle, "source_identity", return_value=SOURCE_SHA),
              patch.object(bundle, "verify_vscode", return_value=self.verified["vscode"]),
              patch.object(bundle, "verify_intellij", return_value=self.verified["intellij-community"])):
            return bundle.assemble(
                **inputs, edition=edition, release_tag="candidate-v1", output_dir=output,
                configuration_home=self.root / "config",
            )

    def test_roundtrip_and_repeatable_archive_with_required_tui(self) -> None:
        inputs = self._inputs()
        first = self._assemble(inputs, self.root / "first")
        second = self._assemble(inputs, self.root / "second")
        self.assertEqual("workbench-install-bundle-descriptor-v1", first["format"])
        self.assertIs(first["qualified"], False)
        self.assertEqual(first, second)
        archive = self.root / "first" / first["archive_filename"]
        manifest = bundle.verify_bundle_archive(archive, first)
        self.assertIn("workbench-tui", manifest["native_versions"])
        self.assertEqual(SOURCE_SHA, manifest["source_sha256"])
        self.assertIn("verify_install_bundle.py", {row["path"] for row in manifest["files"]})
        descriptor_path = self.root / "first" / "workbench-linux-x64-py314-install.json"
        self.assertEqual(first, json.loads(descriptor_path.read_text()))
        rendered, hook_reference = hook.render_managed(
            descriptor_path, self.root / "first-hook" / "workbench-install-linux-x64.sh",
            configuration_home=self.root / "config",
        )
        self.assertEqual(first["archive_sha256"], rendered["archive_sha256"])
        self.assertEqual("install-hook-build", hook_reference.owner_id)
        self.assertEqual(self.root / "first-hook", hook_reference.path)
        self.assertTrue(hook_reference.domain_id.startswith("workbench-install-hook-v1:sha256:"))
        self.assertTrue((self.root / "first-hook" / "SHA256SUMS").is_file())
        self.assertEqual(
            {first["archive_filename"], "workbench-linux-x64-py314-install.json"},
            {path.name for path in (self.root / "first").iterdir()},
        )

    def test_core_catalog_retains_the_exact_assembled_candidate(self) -> None:
        inputs = self._inputs()
        with (patch.object(bundle, "_git_metadata", return_value=("b" * 40, "c" * 40)),
              patch.object(bundle, "source_identity", return_value=SOURCE_SHA),
              patch.object(bundle, "verify_vscode", return_value=self.verified["vscode"]),
              patch.object(bundle, "verify_intellij", return_value=self.verified["intellij-community"])):
            descriptor, reference = bundle.assemble_managed(
                **inputs, release_tag="candidate-v1", output_dir=self.root / "cataloged",
                configuration_home=self.root / "config",
            )
        self.assertEqual(self.root / "cataloged", reference.path)
        self.assertEqual("install-bundle-build", reference.owner_id)
        self.assertEqual("workbench-install-bundle-v1:sha256:" + descriptor["archive_sha256"],
                         reference.domain_id)
        bundle._verify_output(reference.path, descriptor)

    def test_supersymmetry_client_edition_has_only_its_native_closure(self) -> None:
        inputs = self._inputs(edition=bundle.CLIENT_EDITION)
        descriptor = self._assemble(
            inputs, self.root / "client", edition=bundle.CLIENT_EDITION,
        )
        self.assertEqual(bundle.CLIENT_DESCRIPTOR_FORMAT, descriptor["format"])
        self.assertEqual(bundle.CLIENT_EDITION, descriptor["edition"])
        archive = self.root / "client" / descriptor["archive_filename"]
        manifest = bundle.verify_bundle_archive(archive, descriptor)
        self.assertEqual(bundle.CLIENT_BUNDLE_FORMAT, manifest["format"])
        self.assertEqual(list(bundle.CLIENT_ROOT_COMPONENTS), manifest["selected_components"])
        self.assertEqual(
            {row["id"] for row in bundle.selected_components(bundle.CLIENT_ROOT_COMPONENTS)[1]},
            set(manifest["native_versions"]),
        )
        paths = {row["path"] for row in manifest["files"]}
        self.assertFalse(any(path.startswith(("axiom/", "clients/")) for path in paths))
        rendered = hook.render(
            self.root / "client" / "workbench-linux-x64-py314-install.json",
            self.root / "client-hook" / "workbench-install-linux-x64.sh",
            configuration_home=self.root / "config",
        )
        script = Path(rendered["hook"]).read_text()
        self.assertIn("BUNDLE_EDITION='supersymmetry-client'", script)
        self.assertNotIn("@BUNDLE_EDITION@", script)
        wrong_descriptor = {**descriptor, "format": bundle.DESCRIPTOR_FORMAT}
        wrong_descriptor.pop("edition")
        with self.assertRaisesRegex(bundle.BundleError, "edition differs"):
            bundle.verify_bundle_archive(archive, wrong_descriptor)

    def test_installed_client_resources_resolve_linux_managed_java_before_network(self) -> None:
        core_package = tomllib.loads((ROOT / "core/pyproject.toml").read_text(encoding="utf-8"))
        cleanroom_package = tomllib.loads(
            (ROOT / "profiles/platforms/cleanroom/pyproject.toml").read_text(encoding="utf-8")
        )
        supersymmetry_package = tomllib.loads(
            (ROOT / "profiles/packs/supersymmetry/pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertIn("data/*.toml", core_package["tool"]["setuptools"]["package-data"]["workbench_core"])
        self.assertIn(
            "provisional.yaml",
            cleanroom_package["tool"]["setuptools"]["package-data"]["workbench_resources.profiles.platforms.cleanroom"],
        )
        self.assertIn(
            "profile.yaml",
            supersymmetry_package["tool"]["setuptools"]["package-data"]["workbench_resources.profiles.packs.supersymmetry"],
        )
        self.assertIn(
            "workbench-profile-cleanroom",
            {row["id"] for row in bundle.selected_components(bundle.CLIENT_ROOT_COMPONENTS)[1]},
        )

        resources = self.root / "site-packages/workbench_resources"
        for relative in (
            "profiles/packs/supersymmetry/profile.yaml",
            "profiles/platforms/cleanroom/provisional.yaml",
        ):
            destination = resources / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        self.assertFalse((resources / "workbench.toml").exists())

        manifest = default_client_configuration_path(resources)
        configuration = load_workbench_configuration(resources, manifest)
        policy = load_java_runtime_policy(resources, configuration=configuration)
        self.assertEqual("jdk-25.0.4+7", policy["release_name"])

        host = {
            "os": "linux", "architecture": "x64", "system": "Linux", "machine": "x86_64",
        }
        for feature, release in ((None, "jdk-25.0.4+7"), (8, "jdk8u504-b01")):
            with self.subTest(feature=feature), patch(
                "workbench_core.runtime_java.resolve_temurin_asset",
                return_value={"release_name": release},
            ) as resolve, patch(
                "workbench_core.runtime_java.materialize_temurin_runtime",
                return_value={"outcome": "provisioned"},
            ) as materialize:
                result = ensure_java_runtime(
                    resources,
                    config_path=manifest,
                    state_root=self.root / f"java-{feature or 25}",
                    host=host,
                    candidates=(),
                    managed_feature_version=feature,
                )
            self.assertEqual("provisioned", result["outcome"])
            self.assertEqual(release, resolve.call_args.args[0]["release_name"])
            self.assertEqual(host, resolve.call_args.args[1])
            self.assertEqual(release, materialize.call_args.args[2]["release_name"])

    def test_supersymmetry_client_rejects_extras_and_missing_tui(self) -> None:
        inputs = self._inputs(edition=bundle.CLIENT_EDITION)
        with self.assertRaisesRegex(bundle.BundleError, "excludes engine and IDE"):
            self._assemble(
                {**inputs, "engine_zip": self.inputs / "unused.zip"},
                self.root / "extra-artifact", edition=bundle.CLIENT_EDITION,
            )
        manifest_path = inputs["wheelhouse"] / "wheelhouse.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["selected_components"].remove("workbench-tui")
        _write_json(manifest_path, manifest)
        with self.assertRaisesRegex(bundle.BundleError, "Supersymmetry client closure"):
            self._assemble(inputs, self.root / "missing-tui", edition=bundle.CLIENT_EDITION)

    def test_changed_wheelhouse_installer_is_rejected(self) -> None:
        inputs = self._inputs()
        (inputs["wheelhouse"] / "install_workbench.py").write_text("# changed after build\n")
        with self.assertRaisesRegex(bundle.BundleError, "differs from reviewed source"):
            self._assemble(inputs, self.root / "changed-installer")

    def test_changed_client_and_extra_wheelhouse_file_are_rejected(self) -> None:
        inputs = self._inputs()
        inputs["vscode_vsix"].write_bytes(b"changed")
        with self.assertRaisesRegex(bundle.BundleError, "artifact differs"):
            self._assemble(inputs, self.root / "changed-client")
        with zipfile.ZipFile(inputs["vscode_vsix"], "w") as archive:
            archive.writestr("package.txt", "synthetic client\n")
        (inputs["wheelhouse"] / "unexpected.txt").write_text("extra")
        with self.assertRaisesRegex(bundle.BundleError, "extra top-level"):
            self._assemble(inputs, self.root / "extra-wheelhouse")

    def test_unsafe_engine_member_is_rejected(self) -> None:
        inputs = self._inputs(unsafe_engine=True)
        with self.assertRaisesRegex(bundle.BundleError, "unsafe archive path"):
            self._assemble(inputs, self.root / "unsafe")

    def test_missing_tui_is_rejected(self) -> None:
        inputs = self._inputs(tui=False)
        with self.assertRaisesRegex(bundle.BundleError, "Suite and TUI"):
            self._assemble(inputs, self.root / "missing-tui")

    def test_tui_must_be_explicitly_selected(self) -> None:
        inputs = self._inputs()
        manifest_path = inputs["wheelhouse"] / "wheelhouse.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["selected_components"].remove("workbench-tui")
        _write_json(manifest_path, manifest)
        with self.assertRaisesRegex(bundle.BundleError, "select the current full native Suite and TUI"):
            self._assemble(inputs, self.root / "unselected-tui")

    def test_wrong_target_and_linked_wheel_are_rejected(self) -> None:
        inputs = self._inputs()
        manifest_path = inputs["wheelhouse"] / "wheelhouse.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["target"] = {"python": "3.13", "platform": "linux", "machine": "x86_64"}
        _write_json(manifest_path, manifest)
        with self.assertRaisesRegex(bundle.BundleError, "must target Linux"):
            self._assemble(inputs, self.root / "wrong-target")
        manifest["target"] = bundle.TARGET
        _write_json(manifest_path, manifest)
        wheel = inputs["wheelhouse"] / "wheels" / manifest["wheels"][0]["filename"]
        replacement = self.inputs / "retained-wheel"
        replacement.write_bytes(wheel.read_bytes())
        wheel.unlink()
        wheel.symlink_to(replacement)
        with self.assertRaisesRegex(ValueError, "wheel differs"):
            self._assemble(inputs, self.root / "linked-wheel")

    def test_outer_archive_digest_rejects_changed_download(self) -> None:
        inputs = self._inputs()
        descriptor = self._assemble(inputs, self.root / "output")
        archive = self.root / "output" / descriptor["archive_filename"]
        with archive.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(bundle.BundleError, "outer archive differs"):
            bundle.verify_bundle_archive(archive, descriptor)

    def test_clean_source_requires_ignored_in_checkout_outputs(self) -> None:
        checkout = self.root / "checkout"
        checkout.mkdir()
        for command in (("init", "-q"), ("config", "user.name", "Bundle Test"),
                        ("config", "user.email", "bundle@example.invalid")):
            subprocess.run(["git", *command], cwd=checkout, check=True, capture_output=True)
        (checkout / ".gitignore").write_text(".workbench/\n", encoding="utf-8")
        (checkout / "tracked.txt").write_text("source\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=checkout, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=checkout, check=True, capture_output=True)
        revision, tree = bundle._git_metadata(checkout)
        self.assertEqual(40, len(revision))
        self.assertEqual(40, len(tree))
        bundle._check_output_location(checkout / ".workbench" / "bundle", checkout)
        bundle._check_output_location(self.root / "external-bundle", checkout)
        with self.assertRaisesRegex(bundle.BundleError, "must be ignored by Git"):
            bundle._check_output_location(checkout / "release-out", checkout)
        ignored = checkout / ".workbench" / "bundle"
        ignored.mkdir(parents=True)
        (ignored / "archive.tar.gz").write_bytes(b"candidate")
        self.assertEqual((revision, tree), bundle._git_metadata(checkout))
        (checkout / "uncommitted.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(bundle.BundleError, "clean Git checkout"):
            bundle._git_metadata(checkout)


if __name__ == "__main__":
    unittest.main()
