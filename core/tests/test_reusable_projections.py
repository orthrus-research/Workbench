"""Historical fixed-path fixture projections can be adopted without replacement."""

from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest

from workbench_api.reusable_projections import ReusableProjectionError
from workbench_core.reusable_projections import CoreReusableProjections


class ReusableProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.workspace = self.home / "workspace"
        self.workspace.mkdir()
        self.content = b"plugin=fixture\n"
        self.digest = "sha256:" + sha256(self.content).hexdigest()
        self.root = self.home / "state/source-projections/cleanroom" / self.digest[7:]
        self.project_relative = Path("profiles/platforms/cleanroom/fixtures/example")
        self.project = self.root / self.project_relative
        self.project.mkdir(parents=True, mode=0o700)
        for parent in (self.root, *self.project.parents):
            if parent.is_relative_to(self.home / "state"):
                parent.chmod(0o700)
        self.source = self.project / "build.gradle"
        self.source.write_bytes(self.content)
        self.source.chmod(0o600)
        self.rows = ({
            "path": "build.gradle", "sha256": self.digest, "size": len(self.content),
        },)
        self.host = CoreReusableProjections(
            workspace=self.workspace, configuration_home=self.home / "config", owner_id="cleanroom",
        )

    def _adopt(self, *, generated_roots: tuple[Path, ...] = ()):
        return self.host.adopt(
            "cleanroom", self.root, source_digest=self.digest,
            project_relative=self.project_relative, source_files=self.rows,
            generated_parts=("build", ".gradle"), generated_suffixes=(".jar", ".class"),
            validate=lambda project: self.assertEqual(project, self.project),
            generated_roots=generated_roots,
        )

    def test_exact_generated_sibling_is_reusable_but_unrelated_sibling_is_rejected(self) -> None:
        generated_root = Path(".workbench/build/cleanroom/0.6.8-alpha/generic-mod-daily-loop")
        generated = self.root / generated_root / "libs/fixture.jar"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"prior Gradle artifact")
        reference = self._adopt(generated_roots=(generated_root,))
        with self.host.open(reference.projection_id, validate=lambda _: None):
            generated.write_bytes(b"new Gradle artifact")
        (self.root / "unrelated").mkdir()
        with self.assertRaisesRegex(ReusableProjectionError, "unexpected member"):
            with self.host.open(reference.projection_id, validate=lambda _: None):
                pass

    def test_historical_tree_is_adopted_in_place_and_generated_cache_reused(self) -> None:
        old_root_inode = self.root.stat().st_ino
        generated = self.project / "build/classes/output.class"
        generated.parent.mkdir(parents=True)
        generated.write_bytes(b"old build")
        loose_generated = self.project / "generated.jar"
        loose_generated.write_bytes(b"old artifact")
        reference = self._adopt()
        self.assertEqual(old_root_inode, self.root.stat().st_ino)
        self.assertEqual(b"old build", generated.read_bytes())
        self.assertEqual(b"old artifact", loose_generated.read_bytes())
        with self.host.open(reference.projection_id, validate=lambda _: None) as opened:
            self.assertEqual(reference, opened)
            generated.write_bytes(b"new build")
        with self.host.open(reference.projection_id, validate=lambda _: None) as opened:
            self.assertEqual(reference, opened)
        self.assertEqual(reference, self._adopt())

    def test_source_tamper_and_undeclared_file_rejected(self) -> None:
        reference = self._adopt()
        self.source.write_bytes(b"changed source")
        with self.assertRaises(ReusableProjectionError):
            with self.host.open(reference.projection_id, validate=lambda _: None):
                pass
        self.source.write_bytes(self.content)
        (self.project / "surprise.txt").write_bytes(b"not generated")
        with self.assertRaisesRegex(ReusableProjectionError, "undeclared source"):
            with self.host.open(reference.projection_id, validate=lambda _: None):
                pass

    def test_owner_validation_cannot_mutate_admitted_source(self) -> None:
        reference = self._adopt()

        def mutate(_: Path) -> None:
            self.source.write_bytes(self.content + b"changed")

        with self.assertRaisesRegex(ReusableProjectionError, "source changed"):
            with self.host.open(reference.projection_id, validate=mutate):
                pass

    def test_owner_validation_cannot_add_undeclared_member(self) -> None:
        reference = self._adopt()

        def mutate(_: Path) -> None:
            (self.project / "unexpected.txt").write_bytes(b"new")

        with self.assertRaisesRegex(ReusableProjectionError, "undeclared source"):
            with self.host.open(reference.projection_id, validate=mutate):
                pass

    def test_declared_source_aggregate_is_bounded(self) -> None:
        rows = (
            {"path": "first", "sha256": self.digest, "size": 16 * 1024 * 1024},
            {"path": "second", "sha256": self.digest, "size": 1},
        )
        with self.assertRaisesRegex(ReusableProjectionError, "exceed 16 MiB"):
            self.host.adopt(
                "cleanroom", self.root, source_digest=self.digest,
                project_relative=self.project_relative, source_files=rows,
                generated_parts=("build",), generated_suffixes=(), validate=lambda _: None,
            )

    def test_replaced_root_and_generated_symlink_rejected(self) -> None:
        reference = self._adopt()
        saved = self.root.with_name(self.root.name + "-saved")
        self.root.rename(saved)
        self.root.mkdir(mode=0o700)
        new_project = self.root / self.project_relative
        new_project.mkdir(parents=True, mode=0o700)
        (new_project / "build.gradle").write_bytes(self.content)
        with self.assertRaisesRegex(ReusableProjectionError, "replaced"):
            with self.host.open(reference.projection_id, validate=lambda _: None):
                pass
        # Restore the exact original directory, then inspect an unsafe cache.
        import shutil
        shutil.rmtree(self.root)
        saved.rename(self.root)
        (self.project / "build").symlink_to(self.home, target_is_directory=True)
        with self.assertRaisesRegex(ReusableProjectionError, "redirect"):
            with self.host.open(reference.projection_id, validate=lambda _: None):
                pass

    def test_replaced_parent_is_rejected(self) -> None:
        reference = self._adopt()
        parent = self.root.parent
        saved = parent.with_name(parent.name + "-saved")
        parent.rename(saved)
        parent.mkdir(mode=0o700)
        import shutil
        shutil.copytree(saved / self.root.name, self.root)
        with self.assertRaisesRegex(ReusableProjectionError, "replaced"):
            with self.host.open(reference.projection_id, validate=lambda _: None):
                pass

    @unittest.skipUnless(os.name == "posix", "Unix private mode check")
    def test_nonprivate_projection_is_rejected_without_chmod_adoption(self) -> None:
        self.root.chmod(0o755)
        with self.assertRaisesRegex(ReusableProjectionError, "owner-private"):
            self._adopt()
        self.assertEqual(0o755, self.root.stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
