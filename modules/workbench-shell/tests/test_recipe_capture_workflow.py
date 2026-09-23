"""Recipe orchestration with real Core custody and explicitly synthetic owners.

These tests execute Python fixtures, never Java, Forge or Minecraft. They cover
saved-source and lifecycle integration; native game admission needs its own run.
"""

from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from workbench_api.processes import ProcessError
from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_core import check_storage, fixture_selection, runtime_java, tool_process
from workbench_shell import recipe_capture as capture


class SyntheticProfile:
    PROFILE_API_VERSION = 1

    def __init__(self):
        self.revision = 1
        self.mode = 'complete'

    def descriptor(self):
        return {
            'id': 'synthetic-recipe-observation', 'api_version': 1,
            'platform': {'java_major': 8}, 'runtime_artifacts': {'fixture': 'not-game-bytes'},
            'source_roots': ['groovy', 'config'],
            'required_source_files': ['groovy/runConfig.json'],
            'runtime_exclusions': ['eula.txt', 'mods/fixture-observer.jar'],
            'observer_path': 'mods/fixture-observer.jar',
            'observation_preparation': {'fixture': True},
        }

    def build_source_binding(self, candidate, *, deleted_paths):
        return check_storage.seal('synthetic-source', {
            'candidate': deepcopy(candidate), 'deleted_paths': list(deleted_paths),
        })

    def observer_source_manifest(self):
        return {'id': 'synthetic-observer-sources', 'revision': self.revision}

    def select_runtime_artifacts(self, rows):
        if 'fixture-runtime.bin' not in {row['path'] for row in rows}:
            raise ValueError('fixture runtime missing')
        return {'fixture': 'fixture-runtime.bin'}

    def observer_classpath(self, root, rows, artifacts):
        return [root / artifacts['fixture']]

    def build_observer(self, java_home, classpath, output, *, cancelled):
        cancelled.check()
        output.mkdir(mode=0o700)
        artifact = output / 'fixture-observer.jar'
        artifact.write_bytes(b'Explicit synthetic observer; not a Java archive.\n')
        (output / 'compiler').mkdir()
        (output / 'compiler/stdout.raw').write_bytes(b'Explicit synthetic build receipt bytes.\n')
        return {'format': 'synthetic-observer-build', 'artifact': capture._file(artifact)}

    def prepare_server_properties(self, raw):
        return raw + b'fixture-only=true\n'

    def build_capture_input(self, candidate, **values):
        return {'format': 'synthetic-capture-input', 'candidate': deepcopy(candidate),
                'physical_side': 'synthetic', **deepcopy(values)}

    def plan_capture_launch(self, manifest, *, output, input_manifest_path, **values):
        script = (
            "import hashlib,json,sys,time\nfrom pathlib import Path\n"
            f"source=Path({str(input_manifest_path)!r})\n"
            "raw=source.read_bytes(); inputs=json.loads(raw)\n"
            f"output=Path({str(output)!r}); output.mkdir()\n"
            "manifest={key:inputs[key] for key in "
            "('capture_id','launch_id','candidate_lock_sha256','adapter_profile_sha256','physical_side')}\n"
            "manifest.update(format='synthetic-runtime-capture',input_manifest_sha256=hashlib.sha256(raw).hexdigest())\n"
            "(output/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))\n"
            "(output/'recipes.json').write_bytes(b'fixture recipe bytes')\n"
            "Path('native-started').write_bytes(b'fixture')\n"
            "sys.stdout.buffer.write(bytes(range(256)));sys.stdout.buffer.flush()\n"
        )
        if self.mode == 'failed':
            script += 'sys.exit(9)\n'
        elif self.mode == 'wait':
            script += 'time.sleep(60)\n'
        else:
            script += "(output/'.capture-complete').write_bytes(b'')\n"
        return check_storage.seal('synthetic-launch', {
            'format': 'synthetic-capture-launch', 'argv': [sys.executable, '-c', script],
            'input_manifest_sha256': values['input_manifest_sha256'],
        })


class SyntheticGraphAdapter:
    RECIPE_GRAPH_API_VERSION = 1

    def project_capture(self, root, output, *, input_manifest, check_cancelled):
        check_cancelled()
        raw = (root / 'manifest.json').read_bytes()
        manifest = json.loads(raw)
        if (manifest['format'] != 'synthetic-runtime-capture'
                or manifest['input_manifest_sha256'] != sha256(input_manifest.read_bytes()).hexdigest()
                or not (root / '.capture-complete').is_file()):
            raise ValueError('synthetic capture does not bind the supplied input')
        recipe = node_record('gt-recipe', 'fixture:recipe', {
            'lookup_active': True, 'duration': 20, 'eut': 1,
            'captured_input_counts': {'item': 0, 'fluid': 0},
        })
        resource = node_record('item-variant', 'fixture:result', {'label': 'fixture result'})
        edge = edge_record('produces-gt-item', recipe['id'], resource['id'], {
            'amount': 1, 'ordinal': 0, 'chanced': False, 'output_family': 'item_outputs',
        })
        builder = CategoricalGraphBundleBuilder(output, scope={'fixture': 'synthetic-capture'},
            evidence_binding={'capture_manifest': manifest, 'capture_manifest_sha256': sha256(raw).hexdigest()})
        builder.add_partition('fixture', classification='Synthetic workflow evidence', dependencies=(),
            nodes=[recipe, resource], edges=[edge], evidence_categories=('transformation-recipe',),
            limitations=('This fixture is not a game observation.',))
        graph = builder.close()
        return {'format': 'synthetic-projection', 'state': 'complete', 'root': str(output),
                'graph_set_id': graph['graph_set_id'], 'capture_manifest_sha256': sha256(raw).hexdigest()}


@unittest.skipUnless(shutil.which('git'), 'Git is required for developer-source capture')
class RecipeCaptureWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='recipe-workflow-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        # Source and runtime selections support Unicode on both hosts. Legacy
        # Windows Java additionally needs ASCII execution paths when the volume
        # does not provide usable DOS aliases; test that refusal separately.
        self.state = self.root / ('State' if os.name == 'nt' else 'State é 資料')
        self.source, self.runtime = [self.root / name for name in ('Source é 資料', 'Runtime é 資料')]
        self.java = self.root / ('JDK' if os.name == 'nt' else 'JDK é 資料')
        for path in (self.source / 'groovy', self.source / 'config', self.runtime / 'groovy', self.java / 'bin'):
            path.mkdir(parents=True)
        (self.source / 'groovy/runConfig.json').write_bytes(b'{"fixture":true}\n')
        (self.source / 'groovy/recipes.groovy').write_bytes(b'initial saved recipe\n')
        (self.source / 'config/removed.cfg').write_bytes(b'original\n')
        (self.source / '.gitignore').write_bytes(b'ignored.txt\n')
        self.git('init', '--quiet')
        self.git('config', 'user.name', 'Workbench Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.git('add', '--all')
        self.git('commit', '--quiet', '-m', 'synthetic recipe checkout')
        (self.runtime / 'fixture-runtime.bin').write_bytes(b'selected runtime\n')
        (self.runtime / 'groovy/stale.groovy').write_bytes(b'must be replaced\n')
        (self.runtime / 'eula.txt').write_bytes(b'eula=false\n')
        (self.java / 'bin' / ('java.exe' if os.name == 'nt' else 'java')).write_bytes(b'not executed: synthetic JDK\n')
        self.owner, self.adapter = SyntheticProfile(), SyntheticGraphAdapter()
        self.cancelled = Event()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(capture, 'require_profile_extension', side_effect=self.extension))
        self.stack.enter_context(patch.object(capture, 'profile_extension_identity', side_effect=self.provider_identity))
        self.stack.enter_context(patch.object(capture, 'probe_java', return_value={
            'java_version': '1.8.0-fixture', 'java_home': '/synthetic', 'vendor': 'synthetic',
        }))
        # Keep the public API dispatch seam but use the real Core process host.
        # Do not change global host bindings for other tests in the owner suite.
        self.native = self.stack.enter_context(patch.object(capture, 'capture_process', side_effect=tool_process.capture))
        self.stack.enter_context(patch.object(capture, 'open_process_output', side_effect=tool_process.open_output))

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.source), *arguments], stderr=subprocess.PIPE)

    def extension(self, group, profile):
        self.assertEqual('fixture:pack', profile)
        return self.owner if group == capture.GROUP else self.adapter

    def provider_identity(self, group, profile):
        return {'profile_id': profile, 'group': group, 'revision': self.owner.revision}

    def plan(self):
        return capture.plan(self.state, source=self.source, runtime=self.runtime, java_home=self.java,
                            profile='fixture:pack', heap_mib=128, cancelled=self.cancelled)

    def test_core_fixture_selection_and_explicit_recovery(self):
        registry = self.root / 'User config' / 'recipe-fixtures-v1.json'
        setup = self.root / 'User config' / 'missing-setup.json'
        fixture_selection.register_recipe_fixture('fixture:pack', self.source, self.runtime,
                                                   self.java, path=registry)
        resolver = fixture_selection.resolve_recipe_fixture
        with patch.object(capture, 'resolve_recipe_fixture',
                          side_effect=lambda profile, source, **options: resolver(
                              profile, source, registry_path=registry, setup_path=setup, **options)):
            request = capture.plan(self.state, source=self.source, profile='fixture:pack',
                                   heap_mib=128, cancelled=self.cancelled)
            self.assertEqual('user-registry', request['fixture_selection']['runtime_source'])
            self.assertEqual(str(self.runtime), request['runtime'])
            selected = capture.inspect_fixture_selection(source=self.source, profile='fixture:pack',
                                                          cancelled=self.cancelled)
            self.assertEqual('ready-for-planning', selected['state'])
            registry.write_text('{"invalid":true}\n')
            recovery = capture.inspect_fixture_selection(source=self.source, profile='fixture:pack',
                                                          cancelled=self.cancelled)
            self.assertEqual('needs-recovery', recovery['state'])
            self.assertTrue(recovery['recovery'])
            another = capture.plan(self.state, source=self.source, runtime=self.runtime,
                                   java_home=self.java, profile='fixture:pack', heap_mib=128,
                                   cancelled=self.cancelled)
            self.assertEqual('override', another['fixture_selection']['java_source'])

    def prepare(self, request=None):
        request = request or self.plan()
        return capture.prepare(self.state, request['attempt_id'], request['id'], cancelled=self.cancelled)

    def run_capture(self, prepared=None, **kwargs):
        prepared = prepared or self.prepare()
        return capture.run(self.state, prepared['attempt_id'], prepared['id'], accept_eula=True,
                           cancelled=kwargs.get('cancelled', self.cancelled))

    def attempt(self, value):
        return self.state / '.workbench/check-attempts' / value['attempt_id']

    def test_export_requires_complete_verified_local_custody(self):
        from workbench_atlas_recipe_health import completed_scan

        request = self.plan()
        output = self.root / 'Shared scan.zip'
        with patch.object(completed_scan, 'export_completed_scan') as archive:
            with self.assertRaisesRegex(ValueError, 'only a complete admitted'):
                capture.export(self.state, request['attempt_id'], output)
            archive.assert_not_called()
        result = self.run_capture(self.prepare(request))
        attempt = self.attempt(result)
        process_calls = self.native.call_count
        with patch.object(completed_scan, 'export_completed_scan', return_value={'state': 'exported'}) as archive:
            self.assertEqual({'state': 'exported'}, capture.export(self.state, result['attempt_id'], output))
            self.assertEqual((attempt, output), archive.call_args.args)
            self.assertTrue(callable(archive.call_args.kwargs['check_cancelled']))
            archive.reset_mock()
            (attempt / 'source/groovy/recipes.groovy').write_bytes(b'changed after completion')
            with self.assertRaisesRegex(ValueError, 'retained saved source changed'):
                capture.export(self.state, result['attempt_id'], output)
            archive.assert_not_called()
        self.assertEqual(process_calls, self.native.call_count)
        self.assertFalse(output.exists())

    def test_export_rejects_output_inside_attempt_and_honors_cancellation(self):
        request = self.plan()
        with self.assertRaisesRegex(ValueError, 'outside the retained attempt'):
            capture.export(self.state, request['attempt_id'], self.attempt(request) / 'scan.zip')
        cancelled = Event()
        cancelled.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            capture.export(self.state, request['attempt_id'], self.root / 'scan.zip', cancelled=cancelled)
        self.native.assert_not_called()

    def test_plan_retains_dirty_untracked_and_deleted_source_without_mutating_inputs(self):
        (self.source / 'groovy/recipes.groovy').write_bytes(b'edited saved recipe\r\n')
        (self.source / 'groovy/untracked.groovy').write_bytes(b'new untracked recipe\n')
        (self.source / 'config/removed.cfg').unlink()
        (self.source / 'ignored.txt').write_bytes(b'ignored')
        before_index = (self.source / '.git/index').read_bytes()
        before_runtime = capture.workspace_storage.inventory(self.runtime)
        request = self.plan()
        self.assertTrue(request['candidate']['source']['dirty'])
        self.assertEqual(['config/removed.cfg'], request['deleted_paths'])
        self.assertNotIn('ignored.txt', {row['path'] for row in request['candidate']['files']})
        retained = self.attempt(request) / 'source'
        self.assertEqual(b'edited saved recipe\r\n', (retained / 'groovy/recipes.groovy').read_bytes())
        self.assertEqual(b'new untracked recipe\n', (retained / 'groovy/untracked.groovy').read_bytes())
        self.assertFalse((retained / 'config/removed.cfg').exists())
        self.assertEqual(before_index, (self.source / '.git/index').read_bytes())
        self.assertEqual(before_runtime, capture.workspace_storage.inventory(self.runtime))
        self.native.assert_not_called()
        (self.source / 'groovy/untracked.groovy').write_bytes(b'later edit')
        self.assertEqual(b'new untracked recipe\n', (retained / 'groovy/untracked.groovy').read_bytes())
        with self.assertRaisesRegex(ValueError, 'checkout changed'):
            self.prepare(request)
        fresh = self.plan()
        self.assertNotEqual(request['attempt_id'], fresh['attempt_id'])
        self.assertNotEqual(request['candidate']['id'], fresh['candidate']['id'])
        self.assertEqual(b'new untracked recipe\n', (retained / 'groovy/untracked.groovy').read_bytes())

    def test_state_root_inside_checkout_is_rejected_without_creating_state(self):
        state = self.source / 'local-state'
        with self.assertRaisesRegex(ValueError, 'separate trees'):
            capture.plan(state, source=self.source, runtime=self.runtime, java_home=self.java,
                         profile='fixture:pack', heap_mib=128, cancelled=self.cancelled)
        self.assertFalse(state.exists())
        self.native.assert_not_called()

    def test_long_retained_inputs_complete_and_reopen_without_path_prefixes(self):
        from workbench_core.filesystem_paths import native_path

        # Keep the selected files below the legacy limit; Core's extra custody
        # directories make the retained paths exceed it on Windows.
        def relative(root, prefix, suffix):
            remaining = max(80, 240 - len(str(root)) - len(prefix) - len(suffix) - 3)
            first = 'a' * (remaining // 2)
            second = 'b' * (remaining - len(first))
            return f'{prefix}/{first}/{second}/{suffix}'

        source_name = relative(self.source, 'groovy', 'saved.groovy')
        runtime_name = relative(self.runtime, 'libraries', 'selected.jar')
        source_bytes, runtime_bytes = b'saved branch recipe\n', b'selected library bytes\n'
        for root, name, raw in ((self.source, source_name, source_bytes),
                                (self.runtime, runtime_name, runtime_bytes)):
            path = native_path(root / name)
            path.parent.mkdir(parents=True)
            path.write_bytes(raw)
        # The native prefix is also needed to clean the deliberately long test
        # files when Windows has not enabled its global long-path policy.
        cleanup_root = str(self.root)
        if os.name == 'nt':
            cleanup_root = ('\\\\?\\UNC\\' + cleanup_root[2:] if cleanup_root.startswith('\\\\')
                            else '\\\\?\\' + cleanup_root)
        # Reuse TemporaryDirectory's handling of read-only Git object files.
        self.addCleanup(tempfile.TemporaryDirectory._rmtree, cleanup_root)
        request = self.plan()
        attempt = self.attempt(request)
        self.assertGreater(len(str(attempt / 'runtime' / runtime_name)), 260)
        prepared = self.prepare(request)
        result = self.run_capture(prepared)
        self.assertEqual('complete', result['state'])
        self.assertEqual(result, capture.show(self.state, request['attempt_id']))
        self.assertEqual(source_bytes, check_storage.read_bytes(attempt / 'execution' / source_name))
        self.assertEqual(runtime_bytes, check_storage.read_bytes(attempt / 'execution' / runtime_name))
        for record in (request, prepared, result):
            self.assertNotIn('\\\\?\\', json.dumps(record, ensure_ascii=False))
        native_path(attempt / 'source' / source_name).write_bytes(b'changed retained source')
        with self.assertRaisesRegex(ValueError, 'retained saved source changed'):
            capture.show(self.state, request['attempt_id'])

    def test_retained_source_tampering_refuses_preparation(self):
        request = self.plan()
        (self.attempt(request) / 'source/groovy/recipes.groovy').write_bytes(b'not captured bytes')
        with self.assertRaisesRegex(ValueError, 'retained saved source changed'):
            self.prepare(request)
        self.assertFalse((self.attempt(request) / 'prepare-started.json').exists())
        self.native.assert_not_called()

    def test_exact_confirmation_and_eula_required_before_effects(self):
        request = self.plan()
        with self.assertRaisesRegex(ValueError, 'exact reviewed plan'):
            capture.prepare(self.state, request['attempt_id'], 'wrong', cancelled=self.cancelled)
        self.assertFalse((self.attempt(request) / 'prepare-started.json').exists())
        prepared = self.prepare(request)
        for confirmation, eula, phrase in ((prepared['id'], False, 'EULA'), ('wrong', True, 'exact prepared')):
            with self.subTest(confirmation=confirmation, eula=eula):
                with self.assertRaisesRegex(ValueError, phrase):
                    capture.run(self.state, request['attempt_id'], confirmation, accept_eula=eula, cancelled=self.cancelled)
        self.assertFalse((self.attempt(request) / 'run-started.json').exists())
        self.assertEqual(b'eula=false\n', (self.runtime / 'eula.txt').read_bytes())
        self.native.assert_not_called()

    def test_selected_runtime_drift_fails_and_retains_one_attempt(self):
        request = self.plan()
        (self.runtime / 'fixture-runtime.bin').write_bytes(b'changed runtime')
        with self.assertRaisesRegex(ValueError, 'runtime changed'):
            self.prepare(request)
        failure = capture.show(self.state, request['attempt_id'])
        self.assertEqual(('failed', 'prepare', False), (failure['state'], failure['stage'], failure['native_admitted']))
        self.assertTrue((self.attempt(request) / 'source').is_dir())
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.prepare(request)
        self.native.assert_not_called()

    def test_preparation_replaces_complete_source_roots_and_preserves_original_runtime(self):
        prepared = self.prepare()
        runtime = self.attempt(prepared) / 'runtime'
        self.assertFalse((runtime / 'groovy/stale.groovy').exists())
        self.assertEqual(b'initial saved recipe\n', (runtime / 'groovy/recipes.groovy').read_bytes())
        self.assertTrue((self.runtime / 'groovy/stale.groovy').exists())
        self.assertFalse((self.runtime / 'mods/fixture-observer.jar').exists())
        self.assertTrue((runtime / 'mods/fixture-observer.jar').exists())

    def test_prepared_runtime_drift_refuses_game_process(self):
        prepared = self.prepare()
        (self.attempt(prepared) / 'runtime/fixture-runtime.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'prepared runtime changed'):
            self.run_capture(prepared)
        self.assertFalse((self.attempt(prepared) / 'run-started.json').exists())
        self.native.assert_not_called()

    def test_changed_prepared_execution_alias_refuses_game_process(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        execution = Path(prepared['execution_root'])
        self.assertTrue(os.path.samefile(attempt, execution))
        if os.name == 'nt':
            self.assertTrue(str(execution).isascii())
        with patch.object(capture, 'java_execution_path', return_value=self.runtime):
            with self.assertRaisesRegex(ValueError, 'prepared Java execution path changed'):
                self.run_capture(prepared)
        self.assertFalse((attempt / 'run-started.json').exists())
        self.assertFalse((attempt / 'execution').exists())
        self.assertFalse((attempt / 'result.json').exists())
        self.native.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows Java path admission')
    def test_unavailable_unicode_java_alias_refuses_before_plan_effects(self):
        selected = self.root / 'JDK é 資料'
        self.java.rename(selected)
        self.java = selected
        with patch.object(runtime_java, '_windows_short_path', return_value=None):
            with self.assertRaisesRegex(ValueError, 'ASCII 8.3 alias is unavailable'):
                self.plan()
        self.assertFalse(self.state.exists())
        self.native.assert_not_called()

    def test_java_probe_rejects_executable_changed_during_inspection(self):
        def changing_probe(executable):
            executable.write_bytes(b'changed while inspected')
            return {'java_version': '1.8.0-fixture', 'java_home': '/synthetic', 'vendor': 'synthetic'}

        with patch.object(capture, 'probe_java', side_effect=changing_probe):
            with self.assertRaisesRegex(ValueError, 'Java executable changed during inspection'):
                self.plan()
        self.assertFalse(self.state.exists())
        self.native.assert_not_called()

    def test_java_inventory_must_match_the_inspected_executable(self):
        original = capture.workspace_storage.inventory
        executable = self.java / 'bin' / ('java.exe' if os.name == 'nt' else 'java')

        def changing_inventory(root, **kwargs):
            if Path(root) == self.java:
                executable.write_bytes(b'changed after probe and before inventory')
            return original(root, **kwargs)

        with patch.object(capture.workspace_storage, 'inventory', side_effect=changing_inventory):
            with self.assertRaisesRegex(ValueError, 'Java inventory differs from the inspected executable'):
                self.plan()
        self.assertFalse(self.state.exists())
        self.native.assert_not_called()

    def test_cloned_java_identity_must_match_reviewed_selection_before_compilation(self):
        inspect_java = capture._java
        with patch.object(self.owner, 'build_observer', wraps=self.owner.build_observer) as compiler:
            for key in ('size', 'sha256', 'major'):
                with self.subTest(field=key):
                    request = self.plan()

                    def changed_clone(home):
                        java, properties = inspect_java(home)
                        self.assertEqual(self.attempt(request) / 'java', home)
                        java[key] = '0' * 64 if key == 'sha256' else java[key] + 1
                        return java, properties

                    with patch.object(capture, '_java', side_effect=changed_clone):
                        with self.assertRaisesRegex(ValueError, 'retained Java executable differs'):
                            self.prepare(request)
                    failure = capture.show(self.state, request['attempt_id'])
                    self.assertEqual(('failed', 'prepare', False),
                                     (failure['state'], failure['stage'], failure['native_admitted']))
                    self.assertFalse((self.attempt(request) / 'observer-build').exists())
            compiler.assert_not_called()
        self.native.assert_not_called()

    def test_native_failure_retains_process_bytes_and_cannot_be_retried(self):
        prepared = self.prepare()
        self.owner.mode = 'failed'
        with self.assertRaisesRegex(ValueError, 'status 9'):
            self.run_capture(prepared)
        attempt = self.attempt(prepared)
        failure = capture.show(self.state, prepared['attempt_id'])
        self.assertEqual(('failed', 'native-execution'), (failure['state'], failure['stage']))
        self.assertEqual(bytes(range(256)), (attempt / 'process/stdout.raw').read_bytes())
        self.assertFalse((attempt / 'audit.json').exists())
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.run_capture(prepared)

    def test_cancellation_after_launch_keeps_partial_process_and_capture(self):
        prepared = self.prepare()
        self.owner.mode = 'wait'
        attempt = self.attempt(prepared)

        class CancelAfterStart:
            def is_set(self):
                return (attempt / 'execution/native-started').exists()

        with self.assertRaisesRegex(ProcessError, 'cancelled'):
            self.run_capture(prepared, cancelled=CancelAfterStart())
        record = json.loads((attempt / 'process/capture.json').read_bytes())
        self.assertEqual('incomplete', record['state'])
        self.assertTrue((attempt / 'capture/recipes.json').exists())
        self.assertFalse((attempt / 'capture/.capture-complete').exists())
        self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
        self.assertFalse((attempt / 'audit.json').exists())

    def test_persistent_cancel_prevents_later_preparation(self):
        request = self.plan()
        capture.cancel(self.state, request['attempt_id'])
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.prepare(request)
        self.assertFalse((self.attempt(request) / 'prepare-started.json').exists())
        self.native.assert_not_called()

    def test_complete_audit_reopens_after_checkout_edit_without_profile_loading(self):
        result = self.run_capture()
        self.assertEqual(('complete', 1), (result['state'], result['summary']['recipe_count']))
        attempt = self.attempt(result)
        self.assertEqual(b'eula=true\n', (attempt / 'execution/eula.txt').read_bytes())
        self.assertEqual(b'eula=false\n', (self.runtime / 'eula.txt').read_bytes())
        (self.source / 'groovy/recipes.groovy').write_bytes(b'next saved edit')
        capture.cancel(self.state, result['attempt_id'])
        with patch.object(capture, 'require_profile_extension', side_effect=ValueError('profile removed')):
            self.assertEqual(result, capture.show(self.state, result['attempt_id']))
        self.assertEqual(1, self.native.call_count)
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.run_capture({'attempt_id': result['attempt_id'], 'id': result['prepared_id']})

    def test_profile_change_during_execution_prevents_projection(self):
        prepared = self.prepare()

        def changed_profile(*args, **kwargs):
            result = tool_process.capture(*args, **kwargs)
            self.owner.revision += 1
            return result

        with patch.object(capture, 'capture_process', side_effect=changed_profile):
            with self.assertRaisesRegex(ValueError, 'profile.*changed'):
                self.run_capture(prepared)
        self.assertFalse((self.attempt(prepared) / 'graph').exists())
        self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])

    def test_unsupported_adapter_and_mismatched_projection_never_publish_success(self):
        prepared = self.prepare()
        with patch.object(self.adapter, 'RECIPE_GRAPH_API_VERSION', 2):
            with self.assertRaisesRegex(ValueError, 'API'):
                self.run_capture(prepared)
        self.assertFalse((self.attempt(prepared) / 'graph').exists())
        self.assertFalse(capture.show(self.state, prepared['attempt_id'])['native_admitted'])

        prepared = self.prepare()
        original = self.adapter.project_capture

        def mismatched_receipt(*args, **kwargs):
            return {**original(*args, **kwargs), 'graph_set_id': 'not-the-built-graph'}

        with patch.object(self.adapter, 'project_capture', side_effect=mismatched_receipt):
            with self.assertRaisesRegex(ValueError, 'projection'):
                self.run_capture(prepared)
        self.assertTrue((self.attempt(prepared) / 'graph').exists())
        self.assertFalse((self.attempt(prepared) / 'audit.json').exists())
        self.assertFalse(capture.show(self.state, prepared['attempt_id'])['native_admitted'])

    def test_completed_show_rejects_missing_or_changed_custody_and_raw_capture(self):
        result = self.run_capture()
        attempt = self.attempt(result)
        for relative in ('prepared.json', 'launch.json', 'runtime-lock.json', 'protocol.json',
                         'capture/manifest.json', 'capture/recipes.json',
                         'observer-build/fixture-observer.jar', 'observer-build/compiler/stdout.raw'):
            path = attempt / relative
            original = path.read_bytes()
            for replacement in (None, b'changed retained evidence'):
                with self.subTest(path=relative, missing=replacement is None):
                    if replacement is None:
                        path.unlink()
                    else:
                        path.write_bytes(replacement)
                    try:
                        with self.assertRaises((OSError, ValueError, ProcessError)):
                            capture.show(self.state, result['attempt_id'])
                    finally:
                        path.write_bytes(original)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_show_cancellation_reaches_graph_verification(self):
        result = self.run_capture()
        cancelled = Event()

        def verify(path, *, check_cancelled, **kwargs):
            self.assertIsNotNone(check_cancelled)
            cancelled.set()
            check_cancelled()
            self.fail('reopen ignored cancellation')

        with patch('workbench_atlas_recipe_health.open_recipe_health', side_effect=verify):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                capture.show(self.state, result['attempt_id'], cancelled=cancelled)


if __name__ == '__main__':
    unittest.main()
