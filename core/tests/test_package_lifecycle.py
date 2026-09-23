"""Offline package preflight and failure isolation tests."""
from importlib import metadata
from importlib.util import find_spec
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench_api import Module, ModuleError
from workbench_api.profiles import Profile
from workbench_core.dependencies import dependency_errors, reverse_dependency_errors
from workbench_core.module_cli import WheelCandidate, _install, _snapshot_wheel, _wheel, _set_disabled, disabled_profiles, disabled_modules, _profile_status, _pip
from workbench_core.modules import discover


def wheel(root, *, version="0.1.0", name="sample", extra="", entries="sample = sample:module"):
    path = root / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        prefix = f"{name}-{version}.dist-info"
        archive.writestr(prefix + "/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{extra}")
        archive.writestr(prefix + "/entry_points.txt", "[workbench.modules]\n" + entries + "\n")
    return path


class DependencyTests(unittest.TestCase):
    def test_version_constraints_are_enforced(self):
        self.assertEqual([], dependency_errors(["thing>=1,<2"], version_lookup=lambda _: "1.4"))
        self.assertTrue(dependency_errors(["thing>=1,<2"], version_lookup=lambda _: "2.0"))

    def test_missing_and_invalid_dependencies_fail_closed(self):
        def missing(_): raise metadata.PackageNotFoundError("thing")
        self.assertTrue(dependency_errors(["thing>=1"], version_lookup=missing))
        self.assertTrue(dependency_errors(["not a requirement"] ))

    def test_markers_are_evaluated_without_optional_extras(self):
        self.assertEqual([], dependency_errors(['thing; python_version < "1"', 'thing; extra == "optional"']))

    def test_direct_url_cannot_bypass_offline_policy(self):
        self.assertTrue(dependency_errors(["thing @ https://example.invalid/thing.whl"]))

    def test_reverse_consumers_include_disabled_distributions(self):
        consumer = SimpleNamespace(metadata={"Name": "disabled-consumer"}, requires=["sample<2"])
        with patch("workbench_core.dependencies.metadata.distributions", return_value=[consumer]):
            self.assertEqual([], reverse_dependency_errors("sample", "1.0"))
            self.assertTrue(reverse_dependency_errors("sample", "2.0"))
            self.assertTrue(reverse_dependency_errors("sample", None))

    def test_bad_distribution_metadata_is_isolated(self):
        class BadDistribution:
            @property
            def metadata(self): raise ValueError("broken metadata")
        bad = SimpleNamespace(name="broken", value="broken:module", dist=BadDistribution())
        self.assertEqual("unavailable", discover(entries=[bad])[0].state)

    def test_incompatible_distribution_does_not_load_code(self):
        def load(): self.fail("incompatible code must not load")
        entry = SimpleNamespace(name="sample", value="sample:module", load=load,
            dist=SimpleNamespace(metadata={"Name":"sample"}, version="0.1.0", requires=["absent>=2"]))
        with patch("workbench_core.dependencies.metadata.version", side_effect=metadata.PackageNotFoundError("absent")):
            self.assertEqual("unavailable", discover(entries=[entry])[0].state)


class WheelPreflightTests(unittest.TestCase):
    def test_profile_wheel_has_independent_registration_kind(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sample-0.1.0-py3-none-any.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('sample-0.1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: sample\nVersion: 0.1.0\n')
                archive.writestr('sample-0.1.0.dist-info/entry_points.txt', '[workbench.profiles]\nsample = sample:profile\n')
            candidate = _wheel(path)
            self.assertEqual((), candidate.modules)
            self.assertEqual(('sample',), candidate.profiles)

    def test_profile_enable_state_does_not_change_module_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _set_disabled(root, 'sample', True, kind='profiles')
            self.assertEqual(('sample',), disabled_profiles(root))
            self.assertEqual((), disabled_modules(root))
            _set_disabled(root, 'sample', False, kind='profiles')
            self.assertEqual((), disabled_profiles(root))

    def test_enable_state_is_scoped_to_the_selected_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('workbench_core.package_guard.environment_id', return_value='first'):
                _set_disabled(root, 'sample', True, kind='profiles')
            with patch('workbench_core.package_guard.environment_id', return_value='second'):
                self.assertEqual((), disabled_profiles(root))
            with patch('workbench_core.package_guard.environment_id', return_value='first'):
                self.assertEqual(('sample',), disabled_profiles(root))

    def test_disabled_module_propagates_to_dependent_profile_without_rewriting_state(self):
        module = SimpleNamespace(name='domain', value='domain:module', load=lambda:lambda:Module('domain', '1.0.0'),
            dist=SimpleNamespace(metadata={'Name':'domain-package'}, version='1.0.0', requires=()))
        profile = SimpleNamespace(name='example', value='example:profile', load=lambda:lambda:Profile('example', 'pack', Path('/unused'), {}),
            dist=SimpleNamespace(metadata={'Name':'example-package'}, version='1.0.0', requires=('domain-package>=1',)))
        def entries(*, group, **kwargs):
            return {'workbench.modules':(module,), 'workbench.profiles':(profile,)}.get(group, ())
        with tempfile.TemporaryDirectory() as temporary, patch.object(metadata, 'entry_points', side_effect=entries), patch.object(metadata, 'version', return_value='1.0.0'):
            root = Path(temporary)
            self.assertEqual('available', _profile_status(root)[0].state)
            _set_disabled(root, 'domain', True)
            self.assertEqual('unavailable', _profile_status(root)[0].state)
            self.assertEqual((), disabled_profiles(root))
            _set_disabled(root, 'domain', False)
            self.assertEqual('available', _profile_status(root)[0].state)

    def test_snapshot_is_private_and_independent_of_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = wheel(Path(temporary))
            original = source.read_bytes()
            with _snapshot_wheel(source) as candidate:
                self.assertNotEqual(source, candidate.path)
                source.write_bytes(b"changed after snapshot")
                self.assertEqual(original, candidate.path.read_bytes())
                self.assertEqual(("sample",), candidate.modules)
            self.assertFalse(candidate.path.exists())

    def test_core_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ModuleError):
                _wheel(wheel(Path(temporary), name="workbench_core"))

    def test_empty_registration_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ModuleError):
                _wheel(wheel(Path(temporary), entries=""))

    def test_unsupported_python_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ModuleError):
                _wheel(wheel(Path(temporary), extra="Requires-Python: <1\n"))

    def test_source_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alias = root / "alias-0.1.0-py3-none-any.whl"
            alias.symlink_to(wheel(root))
            with self.assertRaises(ModuleError), _snapshot_wheel(alias): pass

    def test_preflight_failure_never_calls_pip(self):
        candidate = WheelCandidate(Path("sample.whl"), "sample", "0.1.0", ("sample",), ("absent>=2",))
        with patch("workbench_core.module_cli.dependency_errors", return_value=["missing dependency"]), \
             patch("workbench_core.module_cli.reverse_dependency_errors", return_value=[]), \
             patch("workbench_core.module_cli.metadata.entry_points", return_value=[]), \
             patch("workbench_core.module_cli._pip") as pip:
            with self.assertRaisesRegex(ModuleError, "preflight"):
                _install(candidate, update=False, state=Path("unused"))
            pip.assert_not_called()

    def test_post_install_admission_failure_is_reported_without_rollback_claim(self):
        candidate = WheelCandidate(Path("sample.whl"), "sample", "0.1.0", ("sample",), ())
        with tempfile.TemporaryDirectory() as temporary, \
             patch("workbench_core.module_cli.dependency_errors", return_value=[]), \
             patch("workbench_core.module_cli.reverse_dependency_errors", return_value=[]), \
             patch("workbench_core.module_cli.metadata.entry_points", return_value=[]), \
             patch("workbench_core.module_cli.metadata.version", return_value="0.1.0"), \
             patch("workbench_core.module_cli.validate_wheel_ownership"), \
             patch("workbench_core.module_cli._pip", return_value=0), \
             patch("workbench_core.module_cli.discover", return_value=()):
            with self.assertRaisesRegex(ModuleError, "no automatic rollback"):
                _install(candidate, update=True, state=Path(temporary))


class InstallerConfinementTests(unittest.TestCase):
    def test_install_and_remove_use_only_the_current_isolated_interpreter(self):
        poisoned = {'PIP_TARGET':'/unexpected/target', 'PIP_PREFIX':'/unexpected/prefix',
                    'PIP_USER':'1', 'PIP_CONFIG_FILE':'/unexpected/pip.conf',
                    'PYTHONPATH':'/injected', 'PYTHONHOME':'/injected',
                    'PIP_PYTHON':'/other/python', 'WORKBENCH_TEST_KEEP':'preserved'}
        for arguments in (['install', '--no-index', '--no-deps', 'sample.whl'],
                          ['uninstall', '--yes', 'sample']):
            with self.subTest(arguments=arguments), patch.dict(os.environ, poisoned), \
                    patch('workbench_core.module_cli.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
                self.assertEqual(0, _pip(arguments))
                self.assertEqual([sys.executable, '-I', '-m', 'pip', '--isolated', *arguments], run.call_args.args[0])
                environment = run.call_args.kwargs['env']
                self.assertEqual(os.devnull, environment['PIP_CONFIG_FILE'])
                self.assertEqual('preserved', environment['WORKBENCH_TEST_KEEP'])
                self.assertEqual({'PIP_CONFIG_FILE'}, {key for key in environment if key.upper().startswith(('PIP_', 'PYTHON'))})

    @unittest.skipUnless(find_spec('pip') is not None, 'pip is optional for Core-only installations')
    def test_real_pip_ignores_injected_module_and_all_configuration(self):
        run = subprocess.run
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'pip.py').write_text('raise RuntimeError("injected pip was loaded")\n', encoding='utf-8')
            configuration = root/'pip.conf'
            configuration.write_text('[global]\ntarget = /unexpected/target\nprefix = /unexpected/prefix\nuser = true\n', encoding='utf-8')
            observations = []
            def observe(command, **options):
                result = run(command, **options, cwd=root, capture_output=True, text=True)
                observations.append(result)
                return result
            with patch.dict(os.environ, {'PIP_CONFIG_FILE':str(configuration), 'PIP_TARGET':'/unexpected/target',
                                        'PIP_PREFIX':'/unexpected/prefix', 'PIP_USER':'1',
                                        'PYTHONPATH':str(root), 'PYTHONHOME':str(root/'invalid-home')}), \
                    patch('workbench_core.module_cli.subprocess.run', side_effect=observe):
                self.assertEqual(0, _pip(['config', 'list']))
            self.assertEqual('', observations[0].stdout.strip())
            self.assertEqual('', observations[0].stderr.strip())


if __name__ == "__main__":
    unittest.main()
