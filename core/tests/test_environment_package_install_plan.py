"""Core reviews a stable isolated target and complete wheel targets before install."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch
from zipfile import ZipFile

from workbench_core import environment_package_install_plan as preflight
from workbench_core.environment_reconstruction import ReconstructionError

import test_environment_package_import as package_fixture


def _wheel(path: Path, name: str, *, members: dict[str, str] | None = None,
           scripts: tuple[str, ...] = ()) -> dict:
    stem = name.replace("-", "_") + "-1.0"
    with ZipFile(path, "w") as archive:
        archive.writestr(f"{stem}.dist-info/WHEEL",
                         "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{stem}.dist-info/METADATA",
                         f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
        if scripts:
            archive.writestr(f"{stem}.dist-info/entry_points.txt",
                             "[console_scripts]\n" + "".join(f"{script} = {name}:main\n" for script in scripts))
        for member, data in (members or {}).items():
            archive.writestr(member, data)
    payload = path.read_bytes()
    return {"name": name, "filename": path.name,
            "size": len(payload), "sha256": sha256(payload).hexdigest()}


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL isolated preflight")
class EnvironmentPackageInstallPlanTests(TestCase):
    def setUp(self) -> None:
        package_fixture.EnvironmentPackageImportTests.setUp(self)
        import_plan = package_fixture.EnvironmentPackageImportTests._plan(self)
        self.package = package_fixture.EnvironmentPackageImportTests._apply(self, import_plan)
        # Test fixtures live on /tmp, whose ephemeral mount is not a durable
        # installation destination. Exercise the ext4 review path explicitly.
        mounted = patch.object(preflight, "_mount_type", return_value="ext4")
        mounted.start()
        self.addCleanup(mounted.stop)

    def _plan(self) -> dict:
        return preflight.plan_package_install_preflight(
            self.suite, self.share, self.candidate, self.closure,
            workspace=self.workspace,
            package_result_resource_id=self.package["resource"]["resource_id"],
            environment=self.environment,
        )

    def test_reopens_retained_bytes_and_reviews_new_stable_path_without_install(self) -> None:
        self.wheelhouse.rename(self.root / "old-wheelhouse")
        with (patch("workbench_core.module_cli._pip", side_effect=AssertionError("pip ran")),
              patch("venv.EnvBuilder.create", side_effect=AssertionError("venv ran"))):
            plan = self._plan()
            again = self._plan()
        self.assertEqual(plan, again)
        self.assertEqual("reviewed", plan["state"])
        self.assertEqual("read-only-isolated-install-preflight-only", plan["coverage"])
        self.assertEqual(self.package["tree_id"], plan["package_tree_id"])
        self.assertEqual(self.closure["unresolved_inputs"], plan["unresolved_inputs"])
        self.assertIn("optional-module-packages", plan["unresolved_inputs"])
        self.assertEqual(len(self.closure["wheels"]), plan["targets"]["wheel_count"])
        self.assertFalse(Path(plan["destination"]).exists())
        self.assertEqual(Path(plan["install_root"]), Path(plan["destination"]).parent)
        self.assertTrue(plan["interpreter"]["base"]["sha256"].startswith("sha256:"))

    def test_occupied_destination_and_redirect_require_review(self) -> None:
        plan = self._plan()
        target = Path(plan["destination"])
        target.mkdir(mode=0o700)
        marker = target / "interrupted.txt"
        marker.write_text("keep", encoding="utf-8")
        occupied = self._plan()
        self.assertEqual("blocked", occupied["state"])
        self.assertIn("already exists", occupied["blockers"][0])
        self.assertEqual("keep", marker.read_text(encoding="utf-8"))
        marker.unlink()
        target.rmdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        target.symlink_to(elsewhere, target_is_directory=True)
        linked = self._plan()
        self.assertEqual("blocked", linked["state"])
        self.assertFalse((elsewhere / "workbench-install.json").exists())

    def test_wsl_windows_mount_and_nonprivate_root_block_review(self) -> None:
        with patch.object(preflight, "_mount_type", return_value="9p"):
            plan = self._plan()
        self.assertEqual("blocked", plan["state"])
        self.assertIn("Core evidence root has an unqualified Linux/WSL filesystem", plan["blockers"])
        with patch.object(preflight, "private_path", return_value=False):
            plan = self._plan()
        self.assertEqual("blocked", plan["state"])
        self.assertIn("Core evidence root is not an existing owner-private directory", plan["blockers"])

    def test_changed_retained_wheel_and_interpreter_refuse(self) -> None:
        path = Path(self.package["tree_path"]) / "wheels/helper-1.0-py3-none-any.whl"
        with path.open("ab") as output:
            output.write(b"changed")
        with self.assertRaises(ReconstructionError):
            self._plan()
        executable = self.root / "python"
        executable.write_bytes(b"old")
        original = preflight._executable(str(executable))
        executable.write_bytes(b"new")
        self.assertNotEqual(original["sha256"], preflight._executable(str(executable))["sha256"])


@skipIf(not sys.platform.startswith("linux"), "Linux/WSL isolated preflight")
class AggregateWheelTargetTests(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "wheels").mkdir()
        self.destination = self.root / "new-environment"
        self.paths = preflight._scheme(self.destination)

    def _scan(self, *rows: dict) -> dict:
        return preflight._wheel_targets(self.root, list(rows), self.destination, self.paths)

    def test_mount_review_uses_most_specific_linux_mount(self) -> None:
        mountinfo = (
            "1 0 0:1 / / rw - ext4 /dev/root rw\n"
            "2 1 0:2 / /mnt/c rw - 9p drvfs rw\n"
        )
        with patch.object(Path, "read_text", return_value=mountinfo):
            self.assertEqual("ext4", preflight._mount_type(Path("/home/workbench")))
            self.assertEqual("9p", preflight._mount_type(Path("/mnt/c/Users/example")))

    def test_distinct_wheels_have_one_aggregate_inventory(self) -> None:
        first = _wheel(self.root / "wheels/first.whl", "first", members={"one/__init__.py": "1"}, scripts=("first",))
        second = _wheel(self.root / "wheels/second.whl", "second", members={"two/__init__.py": "2"})
        observed = self._scan(first, second)
        self.assertEqual(2, observed["wheel_count"])
        self.assertEqual([{"name": "first", "owner": "first", "group": "console_scripts"}], observed["launchers"])
        self.assertTrue(observed["target_inventory_sha256"].startswith("sha256:"))

    def test_pip_minor_alias_is_reserved_even_when_not_declared(self) -> None:
        pip = _wheel(self.root / "wheels/pip.whl", "pip", scripts=("pip", "pip3"))
        alias = f"pip{sys.version_info.major}.{sys.version_info.minor}"
        observed = self._scan(pip)
        self.assertIn(
            {"name": alias, "owner": "pip", "group": "pip-versioned-alias"},
            observed["launchers"],
        )
        mapped = preflight._wheel_targets(
            self.root, [pip], self.destination, self.paths, include_file_map=True,
        )["file_map"]
        self.assertEqual("pip", mapped[f"bin/{alias}"]["owner"])
        self.assertIsNone(mapped[f"bin/{alias}"]["wheel_member"])

    def test_duplicate_file_and_file_directory_collision_refuse(self) -> None:
        first = _wheel(self.root / "wheels/first.whl", "first", members={"shared.py": "1", "occupied": "1"})
        second = _wheel(self.root / "wheels/second.whl", "second", members={"shared.py": "2"})
        with self.assertRaisesRegex(ReconstructionError, "target collides"):
            self._scan(first, second)
        second = _wheel(self.root / "wheels/second.whl", "second", members={"occupied/nested.py": "2"})
        with self.assertRaisesRegex(ReconstructionError, "directory collides"):
            self._scan(first, second)

    def test_duplicate_or_reserved_launcher_refuses(self) -> None:
        first = _wheel(self.root / "wheels/first.whl", "first", scripts=("same",))
        second = _wheel(self.root / "wheels/second.whl", "second", scripts=("same",))
        with self.assertRaisesRegex(ReconstructionError, "target collides"):
            self._scan(first, second)
        second = _wheel(self.root / "wheels/second.whl", "second", scripts=("python",))
        with self.assertRaisesRegex(ReconstructionError, "reserved"):
            self._scan(second)
        second = _wheel(self.root / "wheels/second.whl", "second", scripts=("workbench",))
        with self.assertRaisesRegex(ReconstructionError, "reserved"):
            self._scan(second)

    def test_unsupported_data_scheme_and_bytecode_refuse(self) -> None:
        first = _wheel(self.root / "wheels/first.whl", "first", members={"first-1.0.data/scripts/hook": "run"})
        with self.assertRaisesRegex(ReconstructionError, "unsupported data"):
            self._scan(first)
        first = _wheel(self.root / "wheels/first.whl", "first", members={"first/__pycache__/code.pyc": "bad"})
        with self.assertRaisesRegex(ReconstructionError, "noncanonical"):
            self._scan(first)

    def test_purelib_data_mapping_collides_with_root_member(self) -> None:
        first = _wheel(self.root / "wheels/first.whl", "first", members={"shared.py": "1"})
        second = _wheel(self.root / "wheels/second.whl", "second",
                        members={"second-1.0.data/purelib/shared.py": "2"})
        with self.assertRaisesRegex(ReconstructionError, "target collides"):
            self._scan(first, second)

    def test_directory_entry_under_file_and_uncanonical_member_refuse(self) -> None:
        first = _wheel(self.root / "wheels/first.whl", "first", members={"taken": "1", "taken/subdir/": ""})
        with self.assertRaisesRegex(ReconstructionError, "directory collides"):
            self._scan(first)
        first = _wheel(self.root / "wheels/first.whl", "first", members={"one//two.py": "1"})
        with self.assertRaisesRegex(ReconstructionError, "noncanonical"):
            self._scan(first)
