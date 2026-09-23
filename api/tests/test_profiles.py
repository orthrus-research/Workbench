from importlib import metadata
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from workbench_api import ModuleError
from workbench_api.profiles import Profile, profile_resources, profile_status, profile_scope, profiles


def entry_for(profile, *, name=None, requirements=()):
    return SimpleNamespace(name=name or profile.id, load=lambda: lambda: profile,
        dist=SimpleNamespace(metadata={"Name": f"profile-{profile.id}"}, version="1.0.0", requires=requirements))


class ProfileTests(unittest.TestCase):
    def test_missing_profile_is_not_replaced_by_a_default(self):
        with patch.object(metadata, 'entry_points', return_value=()):
            self.assertEqual({}, profile_resources('acquisition'))

    def test_exact_profile_resource_is_owner_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'policy.json').write_text('{}')
            profile = Profile('example', 'pack', root, {'policy':'policy.json'})
            entry = entry_for(profile)
            with patch.object(metadata, 'entry_points', return_value=(entry,)):
                self.assertEqual({'example':root/'policy.json'}, profile_resources('policy'))
            for relative in ('../policy.json', '/policy.json', 'absent.json'):
                with self.subTest(relative=relative), self.assertRaises(ModuleError):
                    Profile('example','pack',root,{'policy':relative}).resource('policy')

    def test_duplicate_or_incompatible_profile_is_rejected(self):
        for values in ((Profile('example','pack',Path('/unused'),{}),)*2,
                       (Profile('different','pack',Path('/unused'),{}),),
                       (Profile('example','pack',Path('/unused'),{},api_version=2),)):
            entries = tuple(entry_for(value, name='example') for value in values)
            with patch.object(metadata, 'entry_points', return_value=entries):
                self.assertEqual((), profiles())
                self.assertTrue(all(row.state == 'unavailable' for row in profile_status()))

    def test_broken_profile_does_not_disable_unrelated_profile(self):
        valid = entry_for(Profile('valid', 'pack', Path('/unused'), {}))
        broken = entry_for(Profile('broken', 'pack', Path('/unused'), {}, api_version=9))
        with patch.object(metadata, 'entry_points', return_value=(valid, broken)):
            self.assertEqual(['valid'], [profile.id for profile in profiles()])

    def test_disabled_profile_does_not_execute_factory_and_scope_is_reset(self):
        entry = entry_for(Profile('example', 'pack', Path('/unused'), {}))
        with patch.object(metadata, 'entry_points', return_value=(entry,)):
            with profile_scope(disabled=('example',)), patch.object(entry, 'load', side_effect=AssertionError('must not load')):
                self.assertEqual((), profiles())
                self.assertEqual('disabled', profile_status()[0].state)
            self.assertEqual(1, len(profiles()))

    def test_disabled_profile_dependency_is_not_admitted(self):
        base = entry_for(Profile('base', 'platform', Path('/unused'), {}))
        dependent = entry_for(Profile('dependent', 'pack', Path('/unused'), {}), requirements=('profile-base>=1',))
        with patch.object(metadata, 'entry_points', return_value=(base, dependent)), patch.object(metadata, 'version', return_value='1.0.0'):
            self.assertEqual(['available', 'available'], [row.state for row in profile_status()])
            self.assertEqual(['disabled', 'unavailable'], [row.state for row in profile_status(disabled=('base',))])

    def test_host_disabled_module_dependency_blocks_profile_without_loading_it(self):
        entry = entry_for(Profile('example', 'pack', Path('/unused'), {}), requirements=('domain-module>=1',))
        with patch.object(metadata, 'entry_points', return_value=(entry,)), patch.object(metadata, 'version', return_value='1.0.0'):
            with profile_scope(unavailable_distributions=('domain-module',)), patch.object(entry, 'load', side_effect=AssertionError('must not load')):
                self.assertEqual('unavailable', profile_status()[0].state)
                self.assertIn('required module', profile_status()[0].reason)
            self.assertEqual('available', profile_status()[0].state)
