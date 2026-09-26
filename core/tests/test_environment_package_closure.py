"""An offline wheelhouse review cannot be mistaken for package installation."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import platform
import sys
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core.environment_package_closure import plan_package_closure
from workbench_core.environment_reconstruction import ReconstructionError, _seal
from workbench_core.environment_wheel_import import apply_wheel_import, plan_wheel_import

import test_environment_wheel_import as wheel_fixture


def _wheel(path: Path, name: str, version: str, *, requires: tuple[str, ...] = (),
           extra: str | None = None, python: str = ">=3.12") -> Path:
    stem = name.replace("-", "_") + "-" + version
    metadata = [
        "Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}",
        f"Requires-Python: {python}",
        *(f"Requires-Dist: {row}" for row in requires),
        *((f"Provides-Extra: {extra}",) if extra else ()),
        "",
    ]
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{stem}.dist-info/METADATA", "\n".join(metadata))
        archive.writestr(
            f"{stem}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
    return path


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL exact wheel custody required")
class EnvironmentPackageClosureTests(TestCase):
    def setUp(self) -> None:
        wheel_fixture.EnvironmentWheelImportTests.setUp(self)
        self.owner_source = b"fixture owner\n"
        self.owner_init = b""
        source_rows = [
            ("__init__.py", sha256(self.owner_init).hexdigest()),
            ("owner.py", sha256(self.owner_source).hexdigest()),
        ]
        owner_code = self.candidate["profile_fixture"]["owner_code"]
        owner_code["module"] = "fixture_owner.owner"
        owner_code["sha256"] = sha256(self.owner_source).hexdigest()
        owner_code["size"] = len(self.owner_source)
        owner_code["package_source_sha256"] = sha256(json.dumps(
            source_rows, separators=(",", ":"),
        ).encode()).hexdigest()
        self.candidate = _seal(
            {key: value for key, value in self.candidate.items() if key != "candidate_id"},
            "workbench-environment-input-candidate", "candidate_id",
        )

    def _import(self) -> None:
        plan = plan_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            wheels=(self.wheel,), environment=self.environment,
        )
        self.retained = apply_wheel_import(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            wheels=(self.wheel,), expected_plan_id=plan["plan_id"],
            environment=self.environment,
        )

    def _assembly(self, *, include_leaf: bool = True, owner_version: str = "0.1.1",
                  core_requires: tuple[str, ...] = ("workbench-api>=0.1.3,<0.2", "helper[fast]==1.0"),
                  helper_requires: tuple[str, ...] = ("leaf==1.0; extra == 'fast'",),
                  helper_python: str = ">=3.12", owner_source: bytes | None = None,
                  demo: Path | None = None) -> None:
        root = self.root / "wheelhouse"
        wheels = root / "wheels"
        wheels.mkdir(parents=True)
        files = [
            _wheel(wheels / "workbench_core-0.1.9-py3-none-any.whl", "workbench-core", "0.1.9", requires=core_requires),
            _wheel(wheels / "workbench_api-0.1.3-py3-none-any.whl", "workbench-api", "0.1.3"),
            _wheel(wheels / f"workbench_profile_cleanroom-{owner_version}-py3-none-any.whl",
                   "workbench-profile-cleanroom", owner_version, requires=("workbench-api>=0.1.3",)),
            _wheel(wheels / "helper-1.0-py3-none-any.whl", "helper", "1.0", extra="fast", requires=helper_requires, python=helper_python),
            _wheel(wheels / "pip-26.1.2-py3-none-any.whl", "pip", "26.1.2"),
        ]
        if include_leaf:
            files.append(_wheel(wheels / "leaf-1.0-py3-none-any.whl", "leaf", "1.0"))
        with ZipFile(files[2], "a") as archive:
            archive.writestr("fixture_owner/__init__.py", self.owner_init)
            archive.writestr("fixture_owner/owner.py", self.owner_source if owner_source is None else owner_source)
            archive.writestr(
                f"workbench_profile_cleanroom-{owner_version}.dist-info/entry_points.txt",
                "[workbench.workspace_home_fixtures]\ncleanroom = fixture_owner.owner:fixture\n",
            )
        selected_demo = self.wheel if demo is None else demo
        copied = wheels / selected_demo.name
        copied.write_bytes(selected_demo.read_bytes())
        files.append(copied)
        rows = []
        for file in files:
            with ZipFile(file) as archive:
                metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
                from email.parser import BytesParser
                parsed = BytesParser().parsebytes(archive.read(metadata_name))
            rows.append({
                "filename": file.name, "name": parsed["Name"], "version": parsed["Version"],
                "size": file.stat().st_size, "sha256": sha256(file.read_bytes()).hexdigest(),
            })
        rows.sort(key=lambda row: row["name"])
        manifest = {
            "format": "workbench-native-wheelhouse-v1", "source_sha256": "a" * 64,
            "selected_components": ["workbench-core", "workbench-demo", "workbench-profile-cleanroom"],
            "native_versions": {row["name"]: row["version"] for row in rows if row["name"].startswith("workbench-")},
            "target": {
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                "platform": sys.platform, "machine": platform.machine(),
            },
            "wheels": rows, "qualified": False,
        }
        (root / "wheelhouse.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "requirements.lock").write_text("".join(
            f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
            for row in rows
        ), encoding="utf-8")
        self.wheelhouse = root
        self.manifest = manifest

    def _plan(self) -> dict:
        return plan_package_closure(
            self.suite, self.share, self.candidate, workspace=self.workspace,
            wheel_resource_id=self.retained["resource"]["resource_id"],
            wheelhouse=self.wheelhouse, environment=self.environment,
        )

    def test_exact_offline_closure_is_review_only(self) -> None:
        self._import()
        self._assembly()
        with patch("workbench_core.module_cli._pip", side_effect=AssertionError("pip must not run")):
            plan = self._plan()
        self.assertEqual("reviewed", plan["state"])
        self.assertEqual("reviewed-offline-dependency-closure-only", plan["coverage"])
        self.assertEqual(sorted(row["name"] for row in self.manifest["wheels"]), plan["closure"])
        self.assertIn("optional-module-packages", plan["unresolved_inputs"])
        self.assertEqual(self.retained["tree_id"], plan["wheel_tree_id"])
        owner = next(row for row in self.manifest["wheels"]
                     if row["name"] == "workbench-profile-cleanroom")
        self.assertEqual("sha256:" + owner["sha256"], plan["fixture_owner"]["wheel_sha256"])
        self.assertEqual(owner["size"], plan["fixture_owner"]["wheel_size"])
        self.assertEqual(plan["plan_id"], self._plan()["plan_id"])

    def test_missing_transitive_or_extra_wheel_refuses_closure(self) -> None:
        self._import()
        self._assembly(include_leaf=False)
        with self.assertRaisesRegex(ReconstructionError, "cannot satisfy"):
            self._plan()
        self.wheelhouse.rename(self.root / "old-wheelhouse")
        self._assembly(helper_requires=())
        with self.assertRaisesRegex(ReconstructionError, "outside the exact closure"):
            self._plan()

    def test_direct_url_unknown_extra_and_incompatible_requirement_refuse(self) -> None:
        self._import()
        for requirements, pattern in (
            (("helper @ https://example.invalid/helper.whl",), "direct URL"),
            (("helper[unknown]==1.0",), "lacks an extra"),
            (("helper>=2",), "cannot satisfy"),
        ):
            with self.subTest(requirements=requirements):
                if hasattr(self, "wheelhouse"):
                    self.wheelhouse.rename(self.root / f"old-{len(list(self.root.glob('old-*')))}")
                self._assembly(core_requires=("workbench-api>=0.1.3", *requirements))
                with self.assertRaisesRegex(ReconstructionError, pattern):
                    self._plan()

    def test_fixture_owner_and_optional_bytes_are_bound(self) -> None:
        self._import()
        self._assembly(owner_version="0.1.2")
        with self.assertRaisesRegex(ReconstructionError, "fixture owner version"):
            self._plan()
        self.wheelhouse.rename(self.root / "old-wheelhouse")
        changed = self.root / "changed" / self.wheel.name
        changed.parent.mkdir()
        changed.write_bytes(self.wheel.read_bytes() + b"changed")
        self._assembly(demo=changed)
        with self.assertRaisesRegex(ReconstructionError, "Core-retained optional wheels"):
            self._plan()
        self.wheelhouse.rename(self.root / "old-wheelhouse-2")
        self._assembly(owner_source=b"changed owner\n")
        with self.assertRaisesRegex(ReconstructionError, "fixture owner code"):
            self._plan()

    def test_changed_wheel_manifest_lock_and_retained_bytes_refuse(self) -> None:
        self._import()
        self._assembly()
        target = self.wheelhouse / "wheels" / "helper-1.0-py3-none-any.whl"
        with target.open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaisesRegex(ReconstructionError, "size differs"):
            self._plan()
        self.wheelhouse.rename(self.root / "old-wheelhouse")
        self._assembly()
        (self.wheelhouse / "requirements.lock").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "requirements lock differs"):
            self._plan()
        self.wheelhouse.rename(self.root / "old-wheelhouse-2")
        self._assembly()
        retained = Path(self.retained["tree_path"]) / self.wheel.name
        retained.write_bytes(b"changed")
        with self.assertRaises(ReconstructionError):
            self._plan()

    def test_target_and_redirects_refuse_before_plan(self) -> None:
        self._import()
        self._assembly()
        self.manifest["target"]["platform"] = "win32"
        (self.wheelhouse / "wheelhouse.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "another Python/OS/architecture"):
            self._plan()
        self.wheelhouse.rename(self.root / "real-wheelhouse")
        self.wheelhouse.symlink_to(self.root / "real-wheelhouse", target_is_directory=True)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            self._plan()

    def test_native_selection_and_python_requirement_refuse(self) -> None:
        self._import()
        self._assembly()
        self.manifest["selected_components"].remove("workbench-demo")
        (self.wheelhouse / "wheelhouse.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "component selection differs"):
            self._plan()
        self.wheelhouse.rename(self.root / "old-wheelhouse")
        self._assembly(helper_python=">=99")
        with self.assertRaisesRegex(ReconstructionError, "another Python version"):
            self._plan()

    def test_member_crc_is_checked_after_manifest_hashes(self) -> None:
        self._import()
        self._assembly()
        target = self.wheelhouse / "wheels" / "workbench_profile_cleanroom-0.1.1-py3-none-any.whl"
        payload = target.read_bytes()
        self.assertIn(self.owner_source, payload)
        target.write_bytes(payload.replace(self.owner_source, b"Fixture owner\n", 1))
        for row in self.manifest["wheels"]:
            if row["name"] == "workbench-profile-cleanroom":
                row["sha256"] = sha256(target.read_bytes()).hexdigest()
        (self.wheelhouse / "wheelhouse.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.wheelhouse / "requirements.lock").write_text("".join(
            f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
            for row in self.manifest["wheels"]
        ), encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "CRC-32"):
            self._plan()

    def test_malformed_or_unsafe_wheel_refuses(self) -> None:
        self._import()
        self._assembly()
        target = self.wheelhouse / "wheels" / "helper-1.0-py3-none-any.whl"
        with ZipFile(target, "a") as archive:
            archive.writestr("../unsafe", "bad")
        for row in self.manifest["wheels"]:
            if row["name"] == "helper":
                row["size"] = target.stat().st_size
                row["sha256"] = sha256(target.read_bytes()).hexdigest()
        (self.wheelhouse / "wheelhouse.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.wheelhouse / "requirements.lock").write_text("".join(
            f"{row['name']}=={row['version']} --hash=sha256:{row['sha256']}\n"
            for row in self.manifest["wheels"]
        ), encoding="utf-8")
        with self.assertRaisesRegex(ReconstructionError, "unsafe member"):
            self._plan()

    def test_wheelhouse_admission_agrees_with_native_verifier(self) -> None:
        self._import()
        self._assembly()
        import importlib.util
        verifier = Path(__file__).resolve().parents[2] / "tools/verify_wheelhouse.py"
        specification = importlib.util.spec_from_file_location("workbench_standalone_wheel_verifier", verifier)
        self.assertIsNotNone(specification)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        self.assertEqual(self.manifest, module.verify(self.wheelhouse))
        self.assertEqual("reviewed", self._plan()["state"])
