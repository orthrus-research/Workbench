"""Native module admission protects file ownership, not against trusted code."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_api import ModuleError
from workbench_core.package_ownership import validate_wheel_ownership


class PackageOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / 'site-packages'
        self.scripts = self.root / 'bin'
        self.site.mkdir()
        self.scripts.mkdir()
        self.patch_paths = patch('workbench_core.package_ownership.sysconfig.get_paths',
                                 return_value={'purelib':str(self.site), 'platlib':str(self.site), 'scripts':str(self.scripts)})
        self.patch_paths.start()
        self.addCleanup(self.patch_paths.stop)

    def wheel(self, members, *, scripts=''):
        path = self.root / 'sample-1.0.0-py3-none-any.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('sample-1.0.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: sample\nVersion: 1.0.0\n')
            archive.writestr('sample-1.0.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            archive.writestr('sample-1.0.0.dist-info/entry_points.txt', '[workbench.modules]\nsample = sample:module\n' + scripts)
            for name in members:
                archive.writestr(name, 'candidate')
        return path

    def distribution(self, name, *members, entries=()):
        for member in members:
            path = self.site / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('installed', encoding='utf-8')
        return SimpleNamespace(metadata={'Name':name}, files=members,
                               locate_file=lambda member:self.site/member, entry_points=entries)

    def check(self, wheel, *owners):
        with patch('workbench_core.package_ownership.metadata.distributions', return_value=owners):
            validate_wheel_ownership(wheel, 'sample')

    def test_shared_resource_directories_can_merge(self):
        owner = self.distribution('other-profile', 'workbench_resources/profiles/other.json')
        self.check(self.wheel(['workbench_resources/profiles/sample.json']), owner)

    def test_shared_resource_file_collision_is_rejected(self):
        member = 'workbench_resources/profiles/shared.json'
        owner = self.distribution('other-profile', member)
        with self.assertRaisesRegex(ModuleError, 'owned by other-profile'):
            self.check(self.wheel([member]), owner)
        self.assertEqual('installed', (self.site/member).read_text())

    def test_core_file_cannot_be_overwritten_by_renamed_distribution(self):
        member = 'workbench_core/cli.py'
        owner = self.distribution('workbench-core', member)
        with self.assertRaisesRegex(ModuleError, 'owned by workbench-core'):
            self.check(self.wheel([member]), owner)

    def test_own_distribution_update_is_admitted(self):
        owner = self.distribution('sample', 'sample/__init__.py',
                                  'workbench_resources/profiles/sample.json')
        self.check(self.wheel(['sample/__init__.py', 'workbench_resources/profiles/sample.json']), owner)

    def test_workbench_console_launcher_is_reserved(self):
        with self.assertRaisesRegex(ModuleError, 'Workbench launcher'):
            self.check(self.wheel([], scripts='[console_scripts]\nworkbench = sample:main\n'))

    def test_foreign_console_and_gui_launcher_names_are_rejected(self):
        for group in ('console_scripts', 'gui_scripts'):
            entry = SimpleNamespace(group=group, name='shared-tool')
            owner = self.distribution('other', entries=(entry,))
            with self.subTest(group=group), self.assertRaisesRegex(ModuleError, 'launcher is owned'):
                self.check(self.wheel([], scripts=f'[{group}]\nshared-tool = sample:main\n'), owner)

    def test_own_console_launcher_update_is_admitted(self):
        entry = SimpleNamespace(group='console_scripts', name='sample-tool')
        owner = self.distribution('sample', self.scripts/'sample-tool', entries=(entry,))
        self.check(self.wheel([], scripts='[console_scripts]\nsample-tool = sample:main\n'), owner)

    def test_unowned_existing_file_is_not_overwritten(self):
        (self.site/'unowned.py').write_text('retained', encoding='utf-8')
        with self.assertRaisesRegex(ModuleError, 'unowned'):
            self.check(self.wheel(['unowned.py']))

    def test_purelib_data_scheme_checks_the_same_ownership(self):
        member = 'workbench_resources/profiles/shared.json'
        owner = self.distribution('other', member)
        with self.assertRaisesRegex(ModuleError, 'owned by other'):
            self.check(self.wheel(['sample-1.0.0.data/purelib/' + member]), owner)

    def test_unsupported_data_schemes_fail_before_installation(self):
        for scheme in ('scripts', 'headers', 'data'):
            with self.subTest(scheme=scheme), self.assertRaisesRegex(ModuleError, 'purelib/platlib'):
                self.check(self.wheel([f'sample-1.0.0.data/{scheme}/payload']))

    def test_aliasing_wheel_members_are_rejected(self):
        with self.assertRaisesRegex(ModuleError, 'repeats an installation target'):
            self.check(self.wheel(['sample.py', 'sample-1.0.0.data/purelib/sample.py']))
