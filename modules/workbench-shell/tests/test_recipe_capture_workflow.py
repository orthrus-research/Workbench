"""Recipe orchestration with real Core custody and explicitly synthetic owners.

These tests execute Python fixtures, never Java, Forge or Minecraft. They cover
saved-source and lifecycle integration; native game admission needs its own run.
"""

from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from io import StringIO
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

from workbench_api import ExecutionContext, processes as process_port
from workbench_api import host_filesystem as filesystem_port
from workbench_api import capture_workspaces as capture_workspace_port
from workbench_api.processes import ProcessError, read_captured_process as api_read_captured_process
from workbench_api.managed_attempts import managed_attempts_scope
from workbench_api.fixture_selections import fixture_selections_scope
from workbench_atlas_categorical_graph import CategoricalGraphBundleBuilder, edge_record, node_record
from workbench_core import capture_workspace as core_capture_workspace
from workbench_core import check_storage, durable_records, host_filesystem as core_filesystem, runtime_java, tool_process
from workbench_core.capture_workspace_port import (
    HOST as core_capture_workspace_host, _ExecutionWorkspace, _PreparedWorkspace,
)
from workbench_core.fixture_selection_port import CoreFixtureSelections
from workbench_core.managed_attempts import CoreManagedAttempts
from workbench_core.storage.registered import ResourceCatalog
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
        # Exercise the actual public filesystem API with the real Core host,
        # scoped to this direct owner test instead of depending on suite order.
        self.stack.enter_context(patch.object(filesystem_port, '_host', core_filesystem))
        self.stack.enter_context(patch.object(capture_workspace_port, '_host', core_capture_workspace_host))
        self.stack.enter_context(patch.object(process_port, '_host', tool_process))
        self.evidence = self.root / 'Selected evidence'
        self.config = self.root / 'User config'
        self.stack.enter_context(managed_attempts_scope(CoreManagedAttempts(
            workspace=self.source, configuration_home=self.config,
            state_root=self.state, locations={'evidence': self.evidence},
            owner_id='workbench-shell',
        )))
        self.stack.enter_context(fixture_selections_scope(CoreFixtureSelections(
            configuration_home=self.config,
        )))
        self.stack.enter_context(patch.object(capture, 'require_profile_extension', side_effect=self.extension))
        self.stack.enter_context(patch.object(capture, 'profile_extension_identity', side_effect=self.provider_identity))
        self.stack.enter_context(patch.object(capture, 'probe_java', return_value={
            'java_version': '1.8.0-fixture', 'java_home': '/synthetic', 'vendor': 'synthetic',
        }))
        # Keep the public API dispatch seam but use the real Core process host.
        # Do not change global host bindings for other tests in the owner suite.
        self.native = self.stack.enter_context(patch.object(capture, 'capture_process', side_effect=tool_process.capture))
        self.stack.enter_context(patch.object(capture, 'open_process_output', side_effect=tool_process.open_output))
        self.capture_reader = self.stack.enter_context(patch.object(
            capture, 'read_captured_process', side_effect=tool_process.read_capture,
        ))

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
        CoreFixtureSelections(configuration_home=self.config).register(
            'fixture:pack', self.source, self.runtime, self.java,
        )
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

    def test_fixture_registration_command_uses_bound_core_home(self):
        output = StringIO()
        status = capture.main([
            'fixtures', 'set', '--workspace', str(self.source),
            '--pack-profile', 'fixture:pack', '--runtime', str(self.runtime),
            '--java-home', str(self.java), '--json',
        ], context=ExecutionContext(self.source, self.state,
                                    configuration_home=self.config), output=output)
        self.assertEqual(0, status)
        result = json.loads(output.getvalue())
        self.assertEqual('registered-unverified', result['state'])
        self.assertEqual(str(self.java), result['selection']['java_home'])
        self.assertTrue((self.config / 'recipe-fixtures-v1.json').is_file())

    def test_direct_shell_entry_binds_core_filesystem_and_capture_hosts(self):
        from workbench_api import host_filesystem as filesystem_port
        from workbench_core import host_filesystem as core_filesystem, host_services
        from workbench_shell import cli as shell_cli

        with (patch.object(host_services, 'install_local_host_services',
                           wraps=host_services.install_local_host_services) as install,
              patch.object(capture_workspace_port, '_host', None),
              patch.object(sys, 'stdout', StringIO())):
            with self.assertRaises(SystemExit) as stopped:
                shell_cli.main(['--help'])
            self.assertIs(core_capture_workspace_host, capture_workspace_port._host)
        self.assertEqual(0, stopped.exception.code)
        install.assert_called_once()
        self.assertIs(core_filesystem, filesystem_port._filesystem())

    def prepare(self, request=None):
        request = request or self.plan()
        return capture.prepare(self.state, request['attempt_id'], request['id'], cancelled=self.cancelled)

    def run_capture(self, prepared=None, **kwargs):
        prepared = prepared or self.prepare()
        return capture.run(self.state, prepared['attempt_id'], prepared['id'], accept_eula=True,
                           cancelled=kwargs.get('cancelled', self.cancelled))

    def attempt(self, value):
        return self.state / '.workbench/check-attempts' / value['attempt_id']

    def test_default_capture_store_is_core_selected_and_registered(self):
        request = capture.plan(None, source=self.source, runtime=self.runtime, java_home=self.java,
                               profile='fixture:pack', heap_mib=128, cancelled=self.cancelled)
        root = CoreManagedAttempts(
            workspace=self.source, configuration_home=self.config,
            state_root=self.state, locations={'evidence': self.evidence},
            owner_id='workbench-shell',
        ).default_root(capture.ATTEMPT_FAMILY, workspace=self.source)
        attempt = root / '.workbench/check-attempts' / request['attempt_id']
        self.assertTrue(attempt.is_dir())
        self.assertEqual(request, capture.show(None, request['attempt_id']))
        stores = ResourceCatalog(self.config).inventory(workspace=self.source)['record_stores']
        self.assertEqual([str(attempt.parent)], [row['path'] for row in stores])

    def test_explicit_checkout_selects_own_store_and_reopens_after_context_switch(self):
        other = self.root / 'Another source'
        shutil.copytree(self.source, other)
        original = capture.plan(None, source=self.source, runtime=self.runtime, java_home=self.java,
                                profile='fixture:pack', heap_mib=128, cancelled=self.cancelled)
        selected = capture.plan(None, source=other, runtime=self.runtime, java_home=self.java,
                                profile='fixture:pack', heap_mib=128, cancelled=self.cancelled)
        rows = ResourceCatalog(self.config).inventory()['record_stores']
        self.assertEqual({str(self.source), str(other)}, {row['workspace'] for row in rows})
        self.assertEqual(2, len({row['path'] for row in rows}))
        with managed_attempts_scope(CoreManagedAttempts(
            workspace=other, configuration_home=self.config,
            state_root=self.root / 'Changed state',
            locations={'evidence': self.root / 'Changed evidence'},
            owner_id='workbench-shell',
        )):
            self.assertEqual(original, capture.show(None, original['attempt_id']))
            self.assertEqual(selected, capture.show(None, selected['attempt_id']))

    def test_historical_default_root_reopens_from_core_lookup(self):
        previous_root = self.state / 'recipe-captures'
        request = capture.plan(previous_root, source=self.source, runtime=self.runtime,
                               java_home=self.java, profile='fixture:pack', heap_mib=128,
                               cancelled=self.cancelled)
        self.assertEqual(request, capture.show(None, request['attempt_id']))

    def test_request_v1_bytes_and_ready_witness_bind_exact_request(self):
        with patch.object(capture, 'publish_immutable_bytes',
                          wraps=capture.publish_immutable_bytes) as published:
            request = self.plan()
        attempt = self.attempt(request)
        raw = (attempt / 'request.json').read_bytes()
        self.assertEqual(check_storage.canonical(request) + b'\n', raw)
        self.assertIn(attempt / 'request.json', [call.args[0] for call in published.call_args_list])
        intent = json.loads((attempt / 'plan-intent.json').read_bytes())
        ready = json.loads((attempt / 'request-ready.json').read_bytes())
        self.assertEqual(capture.PLAN_INTENT, intent['format'])
        self.assertEqual(capture.REQUEST_READY, ready['format'])
        self.assertEqual(request['id'], ready['request_id'])
        self.assertEqual(intent['id'], ready['intent_id'])
        self.assertEqual(sha256(raw).hexdigest(), ready['request_sha256'])
        self.assertEqual(len(raw), ready['request_size'])
        self.assertEqual(request, capture.show(self.state, request['attempt_id']))

    def test_historical_request_is_readable_but_cannot_start_preparation(self):
        request = self.plan()
        attempt = self.attempt(request)
        request_path = attempt / 'request.json'
        raw = request_path.read_bytes()
        (attempt / 'plan-intent.json').unlink()
        (attempt / 'request-ready.json').unlink()
        if os.name != 'nt':
            request_path.chmod(0o644)
        with patch.object(capture, 'read_bounded_single_link_bytes',
                          wraps=capture.read_bounded_single_link_bytes) as historical_read:
            self.assertEqual(request, capture.show(self.state, request['attempt_id']))
        self.assertIn((request_path,), [call.args for call in historical_read.call_args_list])
        with self.assertRaisesRegex(ValueError, 'durable plan intent witness'):
            self.prepare(request)
        self.assertEqual(raw, request_path.read_bytes())
        self.assertFalse((attempt / 'prepare-started.json').exists())
        self.native.assert_not_called()

    def test_plan_intent_faults_retain_stage_without_a_reviewable_request(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        attempts_root = self.state / '.workbench/check-attempts'
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                before = {path.name for path in attempts_root.iterdir()} if attempts_root.exists() else set()

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target).name == 'plan-intent.json' and moment == 'before-link':
                        raise OSError('intent before-link interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if (Path(directory).name.startswith('recipe-capture-')
                            and (Path(directory) / 'plan-intent.json').exists()
                            and moment == 'after-link-before-flush'):
                        raise OSError('intent post-link flush unavailable')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(OSError):
                        self.plan()
                [name] = {path.name for path in attempts_root.iterdir()} - before
                attempt = attempts_root / name
                stages = list(attempt.glob('.plan-intent.json.*.tmp'))
                self.assertEqual(1, len(stages))
                self.assertEqual(moment == 'after-link-before-flush', (attempt / 'plan-intent.json').exists())
                self.assertFalse((attempt / 'request.json').exists())
                with self.assertRaises(FileNotFoundError):
                    capture.show(self.state, name)
                self.assertFalse((attempt / 'prepare-started.json').exists())
                self.native.assert_not_called()

    def test_request_publication_faults_leave_unready_attempt(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        attempts_root = self.state / '.workbench/check-attempts'
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                before = {path.name for path in attempts_root.iterdir()} if attempts_root.exists() else set()

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target).name == 'request.json' and moment == 'before-link':
                        raise OSError('request before-link interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if (Path(directory).name.startswith('recipe-capture-')
                            and (Path(directory) / 'request.json').exists()
                            and moment == 'after-link-before-flush'):
                        raise OSError('request post-link flush unavailable')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(OSError):
                        self.plan()
                [name] = {path.name for path in attempts_root.iterdir()} - before
                attempt = attempts_root / name
                self.assertTrue((attempt / 'plan-intent.json').exists())
                self.assertEqual(moment == 'after-link-before-flush', (attempt / 'request.json').exists())
                self.assertFalse((attempt / 'request-ready.json').exists())
                if (attempt / 'request.json').exists():
                    self.assertEqual('interrupted', capture.show(self.state, name)['state'])
                    request = json.loads((attempt / 'request.json').read_bytes())
                    with self.assertRaisesRegex(ValueError, 'durable ready witness'):
                        self.prepare(request)
                else:
                    with self.assertRaises(FileNotFoundError):
                        capture.show(self.state, name)
                self.assertFalse((attempt / 'prepare-started.json').exists())
                self.native.assert_not_called()

    def test_request_ready_faults_keep_uncertain_stage_and_refuse_preparation(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        attempts_root = self.state / '.workbench/check-attempts'
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                before = {path.name for path in attempts_root.iterdir()} if attempts_root.exists() else set()

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target).name == 'request-ready.json' and moment == 'before-link':
                        raise OSError('ready before-link interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if (Path(directory).name.startswith('recipe-capture-')
                            and (Path(directory) / 'request-ready.json').exists()
                            and moment == 'after-link-before-flush'):
                        raise OSError('ready post-link flush unavailable')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(OSError):
                        self.plan()
                [name] = {path.name for path in attempts_root.iterdir()} - before
                attempt = attempts_root / name
                request = json.loads((attempt / 'request.json').read_bytes())
                stages = list(attempt.glob('.request-ready.json.*.tmp'))
                self.assertEqual(1, len(stages))
                stage_bytes = stages[0].read_bytes()
                self.assertEqual(moment == 'after-link-before-flush', (attempt / 'request-ready.json').exists())
                self.assertEqual('interrupted', capture.show(self.state, name)['state'])
                with self.assertRaisesRegex(ValueError, 'uncertain record stage'):
                    self.prepare(request)
                self.assertEqual(stage_bytes, stages[0].read_bytes())
                self.assertFalse((attempt / 'prepare-started.json').exists())
                self.native.assert_not_called()

    def test_request_witness_change_or_missing_half_refuses_preparation(self):
        request = self.plan()
        attempt = self.attempt(request)
        intent_path = attempt / 'plan-intent.json'
        original_intent = intent_path.read_bytes()
        ready_path = attempt / 'request-ready.json'
        original = ready_path.read_bytes()
        ready_path.unlink()
        self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
        with self.assertRaisesRegex(ValueError, 'durable ready witness'):
            self.prepare(request)
        ready_path.write_bytes(original)
        if os.name != 'nt':
            ready_path.chmod(0o600)
        intent_path.unlink()
        self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
        with self.assertRaisesRegex(ValueError, 'durable plan intent witness'):
            self.prepare(request)
        intent_path.write_bytes(original_intent)
        if os.name != 'nt':
            intent_path.chmod(0o600)
        changed = json.loads(original)
        changed['request_sha256'] = '0' * 64
        changed = check_storage.seal('recipe-capture-request-ready',
                                     {key: value for key, value in changed.items() if key != 'id'})
        ready_path.write_bytes(check_storage.canonical(changed) + b'\n')
        with self.assertRaisesRegex(ValueError, 'does not bind'):
            capture.show(self.state, request['attempt_id'])
        with self.assertRaisesRegex(ValueError, 'does not bind'):
            self.prepare(request)
        self.assertFalse((attempt / 'prepare-started.json').exists())
        self.native.assert_not_called()

    def test_request_bytes_and_old_or_named_stages_refuse_preparation(self):
        request = self.plan()
        attempt = self.attempt(request)
        request_path = attempt / 'request.json'
        original = request_path.read_bytes()
        for name in ('.record-' + 'f' * 32, '.request-ready.json.unknown'):
            with self.subTest(name=name):
                stage = attempt / name
                stage.write_bytes(b'unknown retained stage')
                self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'uncertain record stage'):
                    self.prepare(request)
                self.assertEqual(b'unknown retained stage', stage.read_bytes())
                stage.unlink()
        for replacement in (b' ' + original, b'{"id":"one","id":"two"}\n'):
            with self.subTest(replacement=replacement[:16]):
                request_path.write_bytes(replacement)
                with self.assertRaises((OSError, ValueError)):
                    capture.show(self.state, request['attempt_id'])
                with self.assertRaises((OSError, ValueError)):
                    self.prepare(request)
        request_path.write_bytes(original)
        self.assertEqual(request, capture.show(self.state, request['attempt_id']))
        self.assertFalse((attempt / 'prepare-started.json').exists())
        self.native.assert_not_called()

    def test_prepare_reopens_request_after_acquiring_attempt_lock(self):
        request = self.plan()
        attempt = self.attempt(request)
        request_path = attempt / 'request.json'
        original = request_path.read_bytes()
        original_load = capture.load

        def change_after_load(root, identity):
            observed = original_load(root, identity)
            request_path.write_bytes(b' ' + original)
            return observed

        with patch.object(capture, 'load', side_effect=change_after_load):
            with self.assertRaisesRegex(ValueError, 'does not bind'):
                self.prepare(request)
        self.assertFalse((attempt / 'prepare-started.json').exists())
        self.native.assert_not_called()

    def test_existing_prepared_witness_remains_runnable_without_new_request_pair(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        (attempt / 'plan-intent.json').unlink()
        (attempt / 'request-ready.json').unlink()
        result = self.run_capture(prepared)
        self.assertEqual('complete', result['state'])
        self.assertEqual(result, capture.show(self.state, prepared['attempt_id']))

    def test_complete_reopen_verifies_present_request_witness_pair(self):
        result = self.run_capture()
        attempt = self.attempt(result)
        ready_path = attempt / 'request-ready.json'
        original = ready_path.read_bytes()
        ready_path.write_bytes(b'changed request witness')
        with self.assertRaises((OSError, ValueError)):
            capture.show(self.state, result['attempt_id'])
        ready_path.unlink()
        with self.assertRaises((OSError, ValueError)):
            capture.show(self.state, result['attempt_id'])
        ready_path.write_bytes(original)
        if os.name != 'nt':
            ready_path.chmod(0o600)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_transition_markers_keep_v1_bytes_and_historical_readback(self):
        with patch.object(capture, 'publish_commit_witness_bytes',
                          wraps=capture.publish_commit_witness_bytes) as published:
            result = self.run_capture()
        attempt = self.attempt(result)
        request = json.loads((attempt / 'request.json').read_bytes())
        prepared = json.loads((attempt / 'prepared.json').read_bytes())
        markers = {
            'prepare-started.json': {'request_id': request['id']},
            'run-started.json': {'request_id': request['id'], 'prepared_id': prepared['id'],
                                 'explicit_eula_acceptance': True},
        }
        self.assertTrue({attempt / name for name in markers} <=
                        {call.args[0] for call in published.call_args_list})
        for name, value in markers.items():
            path = attempt / name
            self.assertEqual(check_storage.canonical(value) + b'\n', path.read_bytes())
            if os.name != 'nt':
                path.chmod(0o644)
        try:
            with patch.object(capture, 'read_bounded_bytes',
                              wraps=capture.read_bounded_bytes) as historical_read:
                self.assertEqual(result, capture.show(self.state, result['attempt_id']))
            seen = [call.args[0] for call in historical_read.call_args_list]
            self.assertTrue({attempt / name for name in markers} <= set(seen))
        finally:
            if os.name != 'nt':
                for name in markers:
                    (attempt / name).chmod(0o600)

    def test_changed_transition_markers_refuse_complete_readback(self):
        result = self.run_capture()
        attempt = self.attempt(result)
        for name in ('prepare-started.json', 'run-started.json'):
            path = attempt / name
            original = path.read_bytes()
            for changed in (b'{"request_id":"first","request_id":"second"}\n',
                            b' ' + original):
                with self.subTest(name=name, changed=changed[:20]):
                    path.write_bytes(changed)
                    try:
                        with self.assertRaises((OSError, ValueError)):
                            capture.show(self.state, result['attempt_id'])
                    finally:
                        path.write_bytes(original)
            if os.name != 'nt':
                path.chmod(0o600)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_prepare_marker_interruption_retains_stage_and_refuses_retry(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                request = self.plan()
                attempt = self.attempt(request)
                marker = attempt / 'prepare-started.json'
                interrupted = False

                def interrupt_link(source, target, *args, **kwargs):
                    nonlocal interrupted
                    if Path(target) == marker and moment == 'before-link' and not interrupted:
                        interrupted = True
                        raise OSError('synthetic prepare marker interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    nonlocal interrupted
                    if Path(directory) == attempt and marker.exists() and not interrupted:
                        interrupted = True
                        raise OSError('synthetic prepare marker interruption')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaisesRegex(OSError, 'synthetic prepare marker interruption'):
                        self.prepare(request)
                self.assertTrue(interrupted)
                self.assertEqual(moment == 'after-link-before-flush', marker.exists())
                [stage] = list(attempt.glob('.prepare-started.json.*.tmp'))
                stage_raw = stage.read_bytes()
                failure = capture.show(self.state, request['attempt_id'])
                self.assertEqual(('failed', False), (failure['state'], failure['native_admitted']))
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.prepare(request)
                self.assertEqual(stage_raw, stage.read_bytes())
                self.assertFalse((attempt / 'prepared.json').exists())
                self.native.assert_not_called()

    def test_run_marker_interruption_refuses_native_execution_and_replay(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                prepared = self.prepare()
                attempt = self.attempt(prepared)
                marker = attempt / 'run-started.json'
                interrupted = False

                def interrupt_link(source, target, *args, **kwargs):
                    nonlocal interrupted
                    if Path(target) == marker and moment == 'before-link' and not interrupted:
                        interrupted = True
                        raise OSError('synthetic run marker interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    nonlocal interrupted
                    if Path(directory) == attempt and marker.exists() and not interrupted:
                        interrupted = True
                        raise OSError('synthetic run marker interruption')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaisesRegex(OSError, 'synthetic run marker interruption'):
                        self.run_capture(prepared)
                self.assertTrue(interrupted)
                self.assertEqual(moment == 'after-link-before-flush', marker.exists())
                [stage] = list(attempt.glob('.run-started.json.*.tmp'))
                stage_raw = stage.read_bytes()
                self.assertEqual('interrupted', capture.show(self.state, prepared['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'uncertain record stage|already attempted'):
                    self.run_capture(prepared)
                self.assertEqual(stage_raw, stage.read_bytes())
                self.assertFalse((attempt / 'result.json').exists())
                self.native.assert_not_called()

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

    def test_preparation_failure_v1_bytes_and_historical_core_readback(self):
        request = self.plan()
        attempt = self.attempt(request)
        failure_path = attempt / 'prepare-failed.json'
        (self.runtime / 'fixture-runtime.bin').write_bytes(b'changed runtime')
        with patch.object(capture, 'publish_immutable_bytes',
                          wraps=capture.publish_immutable_bytes) as published:
            with self.assertRaisesRegex(ValueError, 'runtime changed'):
                self.prepare(request)
        failure = capture.show(self.state, request['attempt_id'])
        raw = failure_path.read_bytes()
        self.assertEqual(check_storage.canonical(failure) + b'\n', raw)
        self.assertIn(failure_path, [call.args[0] for call in published.call_args_list])
        if os.name != 'nt':
            failure_path.chmod(0o644)
        with patch.object(capture, 'read_bounded_single_link_bytes',
                          wraps=capture.read_bounded_single_link_bytes) as historical_read:
            self.assertEqual(failure, capture.show(self.state, request['attempt_id']))
        self.assertEqual(
            [(attempt / 'request.json',), (attempt / 'request.json',), (failure_path,)],
            [call.args for call in historical_read.call_args_list],
        )
        for replacement in (b'{"id":"one","id":"two"}\n', b'changed failure'):
            with self.subTest(replacement=replacement):
                failure_path.write_bytes(replacement)
                with self.assertRaises((OSError, ValueError)):
                    capture.show(self.state, request['attempt_id'])
        failure_path.write_bytes(raw)
        self.assertEqual(failure, capture.show(self.state, request['attempt_id']))
        failure_path.unlink()
        self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
        self.native.assert_not_called()

    def test_preparation_failure_publication_faults_retain_stages_and_block_run(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                request = self.plan()
                attempt = self.attempt(request)
                failure_path = attempt / 'prepare-failed.json'
                old_stage = attempt / ('.record-' + 'd' * 32)
                new_stage = attempt / '.prepare-failed.json.01234567'
                (self.runtime / 'fixture-runtime.bin').write_bytes(f'changed runtime {moment}'.encode())

                def seed_stages():
                    old_stage.write_bytes(b'old stage remains')
                    new_stage.write_bytes(b'new stage remains')

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target) == failure_path and moment == 'before-link':
                        seed_stages()
                        raise OSError('preparation failure before link')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if (Path(directory) == attempt and failure_path.exists()
                            and moment == 'after-link-before-flush'):
                        seed_stages()
                        raise OSError('preparation failure after link')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(OSError):
                        self.prepare(request)
                self.assertEqual(moment == 'after-link-before-flush', failure_path.exists())
                self.assertEqual(b'old stage remains', old_stage.read_bytes())
                self.assertEqual(b'new stage remains', new_stage.read_bytes())
                observed = capture.show(self.state, request['attempt_id'])
                self.assertEqual('failed' if failure_path.exists() else 'interrupted', observed['state'])
                if failure_path.exists():
                    self.assertEqual(check_storage.canonical(observed) + b'\n', failure_path.read_bytes())
                with self.assertRaisesRegex(ValueError, 'preparation failed|uncertain record stage'):
                    capture.run(self.state, request['attempt_id'], 'no-prepared-id',
                                accept_eula=True, cancelled=self.cancelled)
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.prepare(request)
                self.assertFalse((attempt / 'run-started.json').exists())
                self.native.assert_not_called()

    def test_post_link_preparation_failure_cannot_authorize_native_run(self):
        request = self.plan()
        attempt = self.attempt(request)
        prepared_path = attempt / 'prepared.json'
        failure_path = attempt / 'prepare-failed.json'
        old_stage = attempt / ('.record-' + 'c' * 32)
        new_stage = attempt / '.prepared.json.01234567'
        original_fsync = durable_records.fsync_directory
        interrupted = False

        def interrupt_after_link(directory):
            nonlocal interrupted
            if Path(directory) == attempt and prepared_path.exists() and not interrupted:
                interrupted = True
                old_stage.write_bytes(b'old stage remains')
                new_stage.write_bytes(b'new stage remains')
                raise OSError('synthetic prepared post-link interruption')
            return original_fsync(directory)

        with patch.object(durable_records, 'fsync_directory', side_effect=interrupt_after_link):
            with self.assertRaisesRegex(OSError, 'synthetic prepared post-link interruption'):
                self.prepare(request)
        self.assertTrue(interrupted)
        prepared_bytes = prepared_path.read_bytes()
        failure_bytes = failure_path.read_bytes()
        prepared = json.loads(prepared_bytes)
        self.assertEqual('failed', capture.show(self.state, request['attempt_id'])['state'])
        with self.assertRaisesRegex(ValueError, 'preparation failed'):
            self.run_capture(prepared)
        self.assertEqual(prepared_bytes, prepared_path.read_bytes())
        self.assertEqual(failure_bytes, failure_path.read_bytes())
        self.assertEqual(b'old stage remains', old_stage.read_bytes())
        self.assertEqual(b'new stage remains', new_stage.read_bytes())
        self.assertFalse((attempt / 'run-started.json').exists())
        self.native.assert_not_called()

    def test_historical_preparation_failure_blocks_run_with_prepared_record(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        failure = check_storage.seal('recipe-capture-failure', {
            'format': capture.RESULT, 'request_id': prepared['request_id'],
            'attempt_id': prepared['attempt_id'], 'state': 'failed',
            'stage': 'prepare', 'error': 'historical preparation failure',
            'native_admitted': False,
        })
        failure_path = attempt / 'prepare-failed.json'
        check_storage.write_json(failure_path, failure)
        prepared_path = attempt / 'prepared.json'
        before = (prepared_path.read_bytes(), failure_path.read_bytes())
        if os.name != 'nt':
            prepared_path.chmod(0o644)
            failure_path.chmod(0o644)
        self.assertEqual(failure, capture.show(self.state, prepared['attempt_id']))
        with self.assertRaisesRegex(ValueError, 'preparation failed'):
            self.run_capture(prepared)
        self.assertEqual(before, (prepared_path.read_bytes(), failure_path.read_bytes()))
        self.assertFalse((attempt / 'run-started.json').exists())
        self.native.assert_not_called()
        if os.name != 'nt':
            failure_path.unlink()
            failure_path.symlink_to('unavailable-failure-record')
            with self.assertRaisesRegex(ValueError, 'preparation failed'):
                self.run_capture(prepared)
            self.assertFalse((attempt / 'run-started.json').exists())
            self.native.assert_not_called()

    def test_prepared_v1_bytes_and_ready_witness_bind_exact_request_and_prepared(self):
        request = self.plan()
        attempt = self.attempt(request)
        with patch.object(capture, 'publish_immutable_bytes',
                          wraps=capture.publish_immutable_bytes) as published:
            prepared = self.prepare(request)
        prepared_path = attempt / 'prepared.json'
        raw = prepared_path.read_bytes()
        self.assertEqual(check_storage.canonical(prepared) + b'\n', raw)
        self.assertIn(prepared_path, [call.args[0] for call in published.call_args_list])
        intent = json.loads((attempt / 'prepare-intent.json').read_bytes())
        ready = json.loads((attempt / 'prepare-ready.json').read_bytes())
        self.assertEqual(sha256((attempt / 'request.json').read_bytes()).hexdigest(), intent['request_sha256'])
        self.assertEqual(intent['id'], ready['intent_id'])
        self.assertEqual(prepared['id'], ready['prepared_id'])
        self.assertEqual(sha256(raw).hexdigest(), ready['prepared_sha256'])
        self.assertEqual(len(raw), ready['prepared_size'])
        self.assertEqual(prepared, capture.show(self.state, request['attempt_id']))

    def test_historical_prepared_v1_is_readable_but_cannot_start_new_native_run(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        prepared_path = attempt / 'prepared.json'
        before = prepared_path.read_bytes()
        (attempt / 'prepare-intent.json').unlink()
        (attempt / 'prepare-ready.json').unlink()
        if os.name != 'nt':
            prepared_path.chmod(0o644)
        self.assertEqual(prepared, capture.show(self.state, prepared['attempt_id']))
        with self.assertRaisesRegex(ValueError, 'durable intent witness'):
            self.run_capture(prepared)
        self.assertEqual(before, prepared_path.read_bytes())
        self.assertFalse((attempt / 'run-started.json').exists())
        self.native.assert_not_called()

    def test_prepared_final_without_failure_or_ready_refuses_native_run(self):
        request = self.plan()
        attempt = self.attempt(request)
        prepared_path = attempt / 'prepared.json'
        original_flush = durable_records.fsync_directory

        def interrupt_flush(directory):
            if Path(directory) == attempt and prepared_path.exists() and not (attempt / 'prepare-ready.json').exists():
                raise OSError('prepared post-link flush unavailable')
            return original_flush(directory)

        with (patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush),
              patch.object(capture, '_fail', side_effect=OSError('failure receipt unavailable'))):
            with self.assertRaisesRegex(OSError, 'failure receipt unavailable'):
                self.prepare(request)
        prepared = json.loads(prepared_path.read_bytes())
        self.assertFalse((attempt / 'prepare-failed.json').exists())
        self.assertFalse((attempt / 'prepare-ready.json').exists())
        self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
        with self.assertRaisesRegex(ValueError, 'durable ready witness'):
            self.run_capture(prepared)
        self.assertFalse((attempt / 'run-started.json').exists())
        self.native.assert_not_called()

    def test_ready_witness_interruption_retains_stage_and_refuses_native_run(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                request = self.plan()
                attempt = self.attempt(request)
                ready_path = attempt / 'prepare-ready.json'

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target) == ready_path and moment == 'before-link':
                        raise OSError('ready before-link interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if (Path(directory) == attempt and ready_path.exists()
                            and moment == 'after-link-before-flush'):
                        raise OSError('ready post-link flush unavailable')
                    return original_flush(directory)

                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush),
                      patch.object(capture, '_fail', side_effect=OSError('failure receipt unavailable'))):
                    with self.assertRaisesRegex(OSError, 'failure receipt unavailable'):
                        self.prepare(request)
                prepared = json.loads((attempt / 'prepared.json').read_bytes())
                stages = list(attempt.glob('.prepare-ready.json.*.tmp'))
                self.assertEqual(1, len(stages))
                stage_bytes = stages[0].read_bytes()
                self.assertEqual(moment == 'after-link-before-flush', ready_path.exists())
                self.assertFalse((attempt / 'prepare-failed.json').exists())
                self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'uncertain record stage'):
                    self.run_capture(prepared)
                self.assertEqual(stage_bytes, stages[0].read_bytes())
                self.assertFalse((attempt / 'run-started.json').exists())
                self.native.assert_not_called()

    def test_old_or_named_preparation_stage_blocks_native_run_after_ready(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        for name in ('.record-' + 'f' * 32, '.prepared.json.unknown'):
            with self.subTest(name=name):
                stage = attempt / name
                stage.write_bytes(b'unknown retained stage')
                with self.assertRaisesRegex(ValueError, 'uncertain record stage'):
                    self.run_capture(prepared)
                self.assertEqual('interrupted', capture.show(self.state, prepared['attempt_id'])['state'])
                self.assertEqual(b'unknown retained stage', stage.read_bytes())
                self.assertFalse((attempt / 'run-started.json').exists())
                self.native.assert_not_called()
                stage.unlink()

    def test_complete_reopen_verifies_present_preparation_witness_pair(self):
        result = self.run_capture()
        attempt = self.attempt(result)
        ready_path = attempt / 'prepare-ready.json'
        original = ready_path.read_bytes()
        try:
            ready_path.write_bytes(b'changed witness')
            with self.assertRaises((OSError, ValueError)):
                capture.show(self.state, result['attempt_id'])
            ready_path.unlink()
            with self.assertRaises((OSError, ValueError)):
                capture.show(self.state, result['attempt_id'])
        finally:
            ready_path.write_bytes(original)
            if os.name != 'nt':
                ready_path.chmod(0o600)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_preparation_replaces_complete_source_roots_and_preserves_original_runtime(self):
        prepared = self.prepare()
        runtime = self.attempt(prepared) / 'runtime'
        self.assertFalse((runtime / 'groovy/stale.groovy').exists())
        self.assertEqual(b'initial saved recipe\n', (runtime / 'groovy/recipes.groovy').read_bytes())
        self.assertTrue((self.runtime / 'groovy/stale.groovy').exists())
        self.assertFalse((self.runtime / 'mods/fixture-observer.jar').exists())
        self.assertTrue((runtime / 'mods/fixture-observer.jar').exists())

    def test_prepared_observer_override_and_pre_run_inventory_use_bound_core_workspace(self):
        request = self.plan()
        inventories = []
        original_inventory = _PreparedWorkspace.inventory

        def observe_inventory(workspace, *, cancelled):
            rows = original_inventory(workspace, cancelled=cancelled)
            inventories.append((workspace._root(), rows))
            return rows

        with (patch.object(capture, 'capture_prepared_workspace',
                           wraps=capture.capture_prepared_workspace) as selected,
              patch.object(_PreparedWorkspace, 'inventory', observe_inventory)):
            prepared = self.prepare(request)
            result = self.run_capture(prepared)
        attempt = self.attempt(prepared)
        self.assertEqual(2, selected.call_count)
        self.assertTrue(all(call.args[0].path == attempt for call in selected.call_args_list))
        self.assertEqual(2, len(inventories))
        self.assertEqual([(attempt / 'runtime', prepared['runtime_files'])] * 2, inventories)
        artifact = prepared['observer_build']['artifact']
        observer = attempt / 'runtime/mods/fixture-observer.jar'
        self.assertEqual((artifact['size'], artifact['sha256']),
                         (observer.stat().st_size, sha256(observer.read_bytes()).hexdigest()))
        self.assertIn({'path': 'mods/fixture-observer.jar', 'size': artifact['size'],
                       'sha256': artifact['sha256'], 'mode': 0o644}, prepared['runtime_files'])
        self.assertEqual(prepared, json.loads((attempt / 'prepared.json').read_bytes()))
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_prepared_observer_override_faults_retain_v1_disposition_and_block_native_run(self):
        original_link = core_capture_workspace.os.link
        original_flush = check_storage.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                request = self.plan()
                attempt = self.attempt(request)
                observer = attempt / 'runtime/mods/fixture-observer.jar'
                interrupted = False

                def interrupt_link(source, destination, *args, **kwargs):
                    if moment == 'before-link' and Path(destination) == observer:
                        raise OSError('synthetic prepared observer link interruption')
                    return original_link(source, destination, *args, **kwargs)

                def interrupt_flush(directory):
                    nonlocal interrupted
                    if (moment == 'after-link-before-flush' and not interrupted
                            and Path(directory) == observer.parent and observer.exists()):
                        interrupted = True
                        raise OSError('synthetic prepared observer flush interruption')
                    return original_flush(directory)

                with (patch.object(core_capture_workspace.os, 'link', side_effect=interrupt_link),
                      patch.object(check_storage, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaisesRegex(core_capture_workspace.CaptureWorkspaceError,
                                                'temporary files retained'):
                        self.prepare(request)
                stages = list(observer.parent.glob('.capture-write-*'))
                self.assertEqual(moment == 'before-link', bool(stages))
                self.assertEqual(moment == 'after-link-before-flush', observer.exists())
                self.assertTrue((attempt / 'prepare-started.json').is_file())
                self.assertFalse((attempt / 'prepared.json').exists())
                self.assertEqual('failed', capture.show(self.state, request['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'preparation failed'):
                    capture.run(self.state, request['attempt_id'], 'no-prepared-id',
                                accept_eula=True, cancelled=self.cancelled)
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.prepare(request)
                self.assertFalse((attempt / 'run-started.json').exists())
                self.native.assert_not_called()

    def test_prepared_observer_linked_without_failure_receipt_is_interrupted(self):
        request = self.plan()
        attempt = self.attempt(request)
        observer = attempt / 'runtime/mods/fixture-observer.jar'
        original_flush = check_storage.fsync_directory

        def interrupt_flush(directory):
            if Path(directory) == observer.parent and observer.exists():
                raise OSError('prepared observer flush unavailable')
            return original_flush(directory)

        with (patch.object(check_storage, 'fsync_directory', side_effect=interrupt_flush),
              patch.object(capture, '_fail', side_effect=OSError('failure receipt unavailable'))):
            with self.assertRaisesRegex(OSError, 'failure receipt unavailable'):
                self.prepare(request)
        self.assertTrue(observer.is_file())
        self.assertFalse((attempt / 'prepared.json').exists())
        self.assertFalse((attempt / 'prepare-failed.json').exists())
        self.assertEqual('interrupted', capture.show(self.state, request['attempt_id'])['state'])
        with self.assertRaises(OSError):
            capture.run(self.state, request['attempt_id'], 'no-prepared-id',
                        accept_eula=True, cancelled=self.cancelled)
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.prepare(request)
        self.assertFalse((attempt / 'run-started.json').exists())
        self.native.assert_not_called()

    def test_changed_or_external_observer_build_cannot_prepare_native_runtime(self):
        original_build = self.owner.build_observer
        for case in ('changed', 'external'):
            with self.subTest(case=case):
                request = self.plan()
                attempt = self.attempt(request)

                def invalid_build(*args, **kwargs):
                    build = original_build(*args, **kwargs)
                    artifact = Path(build['artifact']['path'])
                    if case == 'changed':
                        artifact.write_bytes(b'changed after build receipt')
                    else:
                        outside = self.root / ('external-observer-' + request['attempt_id'] + '.jar')
                        outside.write_bytes(artifact.read_bytes())
                        build['artifact'] = {**build['artifact'], 'path': str(outside)}
                    return build

                with patch.object(self.owner, 'build_observer', side_effect=invalid_build):
                    with self.assertRaisesRegex(core_capture_workspace.CaptureWorkspaceError,
                                                'changed before publication|outside this attempt'):
                        self.prepare(request)
                self.assertFalse((attempt / 'runtime/mods/fixture-observer.jar').exists())
                self.assertFalse((attempt / 'prepared.json').exists())
                self.assertEqual('failed', capture.show(self.state, request['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'preparation failed'):
                    capture.run(self.state, request['attempt_id'], 'no-prepared-id',
                                accept_eula=True, cancelled=self.cancelled)
                self.assertFalse((attempt / 'run-started.json').exists())
                self.native.assert_not_called()

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
        with patch.object(capture, 'publish_immutable_stream',
                          wraps=capture.publish_immutable_stream) as published:
            result = self.run_capture()
        self.assertEqual(('complete', 1), (result['state'], result['summary']['recipe_count']))
        attempt = self.attempt(result)
        audit_path = attempt / 'audit.json'
        audit_raw = audit_path.read_bytes()
        self.assertEqual(check_storage.canonical(json.loads(audit_raw)) + b'\n', audit_raw)
        self.assertEqual({'path': str(audit_path), 'size': len(audit_raw),
                          'sha256': sha256(audit_raw).hexdigest()}, result['audit'])
        self.assertEqual(result['audit'], result['custody']['audit.json'])
        published.assert_called_once()
        self.assertEqual(audit_path, published.call_args.args[0])
        input_path = attempt / 'input-manifest.json'
        input_raw = input_path.read_bytes()
        self.assertEqual(check_storage.canonical(json.loads(input_raw)) + b'\n', input_raw)
        self.assertEqual(sha256(input_raw).hexdigest(), result['input_manifest']['sha256'])
        result_path = attempt / 'result.json'
        self.assertEqual(check_storage.canonical(result) + b'\n', result_path.read_bytes())
        run_inputs = [attempt / name for name in ('launch.json', 'runtime-lock.json', 'protocol.json')]
        for path in run_inputs:
            raw = path.read_bytes()
            self.assertEqual(check_storage.canonical(json.loads(raw)) + b'\n', raw)
        self.assertEqual(b'eula=true\n', (attempt / 'execution/eula.txt').read_bytes())
        self.assertEqual(b'eula=false\n', (self.runtime / 'eula.txt').read_bytes())
        (self.source / 'groovy/recipes.groovy').write_bytes(b'next saved edit')
        capture.cancel(self.state, result['attempt_id'])
        with (patch.object(capture, 'require_profile_extension', side_effect=ValueError('profile removed')),
              patch.object(capture, 'read_captured_process', wraps=api_read_captured_process) as reader):
            self.assertEqual(result, capture.show(self.state, result['attempt_id']))
        reader.assert_called_once_with(
            attempt / 'process', binding=result['launch_id'],
            expected_id=result['process']['id'],
        )
        if os.name != 'nt':
            historical = [input_path, result_path, audit_path, *run_inputs]
            for path in historical:
                path.chmod(0o644)
            try:
                with (patch.object(capture, 'read_bounded_single_link_bytes',
                                   wraps=capture.read_bounded_single_link_bytes) as historical_read,
                      patch.object(capture, 'measure_ordinary_single_link_file',
                                   wraps=capture.measure_ordinary_single_link_file) as audit_read):
                    self.assertEqual(result, capture.show(self.state, result['attempt_id']))
                audit_read.assert_called_once()
                self.assertEqual((audit_path,), audit_read.call_args.args)
                self.assertEqual(result['audit']['size'], audit_read.call_args.kwargs['expected_size'])
                self.assertEqual(result['audit']['sha256'], audit_read.call_args.kwargs['expected_sha256'])
                self.assertEqual(
                    [(attempt / 'request.json',), (attempt / 'request.json',),
                     (result_path,), (attempt / 'prepared.json',),
                     (attempt / 'request.json',), (attempt / 'launch.json',),
                     (input_path,), (attempt / 'runtime-lock.json',),
                     (attempt / 'protocol.json',)],
                    [call.args for call in historical_read.call_args_list],
                )
                self.assertTrue(all(call.kwargs == {'byte_limit': 32 * 1024**2}
                                    for call in historical_read.call_args_list))
            finally:
                for path in historical:
                    path.chmod(0o600)
        self.assertEqual(1, self.native.call_count)
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.run_capture({'attempt_id': result['attempt_id'], 'id': result['prepared_id']})

    def test_streamed_audit_preserves_v1_json_bytes_with_unicode(self):
        attempt = self.root / 'Standalone audit é 資料'
        attempt.mkdir(mode=0o700)
        report = {'z': ['é', {'資料': True}], 'a': {'empty': [], 'count': 2},
                  'large': 'x' * 70000,
                  'rows': [{'number': number, 'label': 'é'} for number in range(10000)]}
        original_publish = capture.publish_immutable_stream
        chunk_sizes = []

        def observe_chunks(path, chunks, **kwargs):
            def observed():
                for chunk in chunks:
                    chunk_sizes.append(len(chunk))
                    yield chunk
            return original_publish(path, observed(), **kwargs)

        with patch.object(capture, 'publish_immutable_stream', side_effect=observe_chunks):
            receipt = capture._write_audit_record(attempt, report, check_cancelled=lambda: None)
        raw = (attempt / 'audit.json').read_bytes()
        self.assertEqual(check_storage.canonical(report) + b'\n', raw)
        self.assertGreater(len(raw), 2 * 64 * 1024)
        self.assertLess(len(chunk_sizes), 16)
        self.assertTrue(all(0 < size <= 64 * 1024 for size in chunk_sizes))
        self.assertEqual({'path': str(attempt / 'audit.json'), 'size': len(raw),
                          'sha256': sha256(raw).hexdigest()}, receipt)

    def test_execution_overrides_use_attempt_bound_core_workspace(self):
        prepared = self.prepare()
        original_inventory = _ExecutionWorkspace.inventory
        inventories = []

        def observe_inventory(workspace, *, cancelled):
            rows = original_inventory(workspace, cancelled=cancelled)
            inventories.append((workspace, rows, cancelled))
            return rows

        with patch.object(capture, 'capture_execution_workspace',
                          wraps=capture.capture_execution_workspace) as selected, \
             patch.object(check_storage, 'copy_manifest',
                          wraps=check_storage.copy_manifest) as copied, \
             patch.object(_ExecutionWorkspace, 'inventory', observe_inventory):
            result = self.run_capture(prepared)
        selected.assert_called_once()
        attempt = self.attempt(result)
        self.assertEqual(attempt, selected.call_args.args[0].path)
        self.assertEqual(capture.ATTEMPT_FAMILY, selected.call_args.args[0].family)
        execution = attempt / 'execution'
        copied.assert_called_once()
        self.assertEqual((attempt / 'runtime', execution,
                          json.loads((attempt / 'prepared.json').read_bytes())['runtime_files']),
                         copied.call_args.args)
        self.assertEqual(2, len(inventories))
        self.assertTrue(all(callable(cancelled) for _, _, cancelled in inventories))
        self.assertEqual(inventories[0][1], inventories[1][1])
        self.assertEqual(execution, inventories[1][0]._root())
        self.assertEqual(b'fixture-only=true\n', (execution / 'server.properties').read_bytes())
        self.assertEqual(b'eula=true\n', (execution / 'eula.txt').read_bytes())
        rows = json.loads((attempt / 'runtime-lock.json').read_bytes())['runtime_files']
        for name in ('server.properties', 'eula.txt'):
            path = execution / name
            self.assertIn({'path': name, 'size': path.stat().st_size,
                           'sha256': sha256(path.read_bytes()).hexdigest(), 'mode': 0o644}, rows)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_prelaunch_execution_inventory_cancellation_blocks_replay(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        original_inventory = _ExecutionWorkspace.inventory
        polls = 0

        def cancel_at_second_inventory(workspace, *, cancelled):
            nonlocal polls
            polls += 1
            if polls == 2:
                self.cancelled.set()
            return original_inventory(workspace, cancelled=cancelled)

        with patch.object(_ExecutionWorkspace, 'inventory', cancel_at_second_inventory):
            with self.assertRaisesRegex(core_capture_workspace.CaptureWorkspaceError,
                                        'cancelled'):
                self.run_capture(prepared)
        self.assertEqual(2, polls)
        self.assertTrue((attempt / 'run-started.json').is_file())
        self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
        self.cancelled.clear()
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.run_capture(prepared)
        self.native.assert_not_called()

    def test_partial_runtime_copy_retains_tree_and_blocks_native_replay(self):
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        execution = attempt / 'execution'
        original_copy = check_storage.copy_manifest

        def interrupt_after_first_file(source, destination, rows, **kwargs):
            original_copy(source, destination, rows[:1], **kwargs)
            raise check_storage.CheckStorageError('synthetic partial runtime copy')

        with patch.object(check_storage, 'copy_manifest', side_effect=interrupt_after_first_file):
            with self.assertRaisesRegex(check_storage.CheckStorageError,
                                        'synthetic partial runtime copy'):
                self.run_capture(prepared)
        first = prepared['runtime_files'][0]
        self.assertEqual((attempt / 'runtime' / first['path']).read_bytes(),
                         (execution / first['path']).read_bytes())
        self.assertEqual([first], core_capture_workspace.inventory(execution))
        self.assertTrue((attempt / 'run-started.json').is_file())
        self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.run_capture(prepared)
        self.native.assert_not_called()

    def test_execution_override_link_faults_preserve_v1_stages_and_block_replay(self):
        original_link = core_capture_workspace.os.link
        original_flush = check_storage.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                prepared = self.prepare()
                attempt = self.attempt(prepared)
                execution = attempt / 'execution'
                eula = execution / 'eula.txt'
                failed_once = False

                def interrupt_link(source, destination, *args, **kwargs):
                    if moment == 'before-link' and Path(destination) == eula:
                        raise OSError('synthetic override link interruption')
                    return original_link(source, destination, *args, **kwargs)

                def interrupt_flush(directory):
                    nonlocal failed_once
                    if (moment == 'after-link-before-flush' and not failed_once
                            and Path(directory) == execution and eula.exists()):
                        failed_once = True
                        raise OSError('synthetic override flush interruption')
                    return original_flush(directory)

                with (patch.object(core_capture_workspace.os, 'link', side_effect=interrupt_link),
                      patch.object(check_storage, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaisesRegex(core_capture_workspace.CaptureWorkspaceError,
                                                'temporary files retained'):
                        self.run_capture(prepared)
                self.assertTrue((attempt / 'run-started.json').is_file())
                self.assertEqual(moment == 'after-link-before-flush', eula.exists())
                self.assertEqual(1 if moment == 'before-link' else 0,
                                 len(list(execution.glob('.capture-write-*'))))
                self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.run_capture(prepared)
                self.native.assert_not_called()

    def test_execution_override_replacement_fault_retains_reviewed_original(self):
        (self.runtime / 'server.properties').write_bytes(b'baseline=true\n')
        prepared = self.prepare()
        attempt = self.attempt(prepared)
        execution = attempt / 'execution'
        settings = execution / 'server.properties'
        original_replace = core_capture_workspace.os.replace

        def interrupt_replace(source, destination, *args, **kwargs):
            if Path(destination) == settings:
                raise OSError('synthetic override replacement interruption')
            return original_replace(source, destination, *args, **kwargs)

        with patch.object(core_capture_workspace.os, 'replace', side_effect=interrupt_replace):
            with self.assertRaisesRegex(core_capture_workspace.CaptureWorkspaceError,
                                        'temporary files retained'):
                self.run_capture(prepared)
        self.assertEqual(b'baseline=true\n', settings.read_bytes())
        self.assertEqual(1, len(list(execution.glob('.capture-write-*'))))
        self.assertTrue((attempt / 'run-started.json').is_file())
        self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.run_capture(prepared)
        self.native.assert_not_called()

    def test_audit_interruption_retains_stage_and_blocks_native_replay(self):
        original_link = durable_records.os.link
        original_flush = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                prepared = self.prepare()
                attempt = self.attempt(prepared)
                audit_path = attempt / 'audit.json'
                failed_once = False

                def interrupt_link(source, destination, *args, **kwargs):
                    if moment == 'before-link' and Path(destination) == audit_path:
                        raise OSError('synthetic audit link interruption')
                    return original_link(source, destination, *args, **kwargs)

                def interrupt_flush(directory):
                    nonlocal failed_once
                    if (moment == 'after-link-before-flush' and not failed_once
                            and Path(directory) == attempt and audit_path.exists()):
                        failed_once = True
                        raise OSError('synthetic audit flush interruption')
                    return original_flush(directory)

                native_before = self.native.call_count
                with (patch.object(durable_records.os, 'link', side_effect=interrupt_link),
                      patch.object(durable_records, 'fsync_directory', side_effect=interrupt_flush)):
                    with self.assertRaises(filesystem_port.DurableRecordError):
                        self.run_capture(prepared)
                self.assertEqual(native_before + 1, self.native.call_count)
                self.assertEqual(moment == 'after-link-before-flush', audit_path.exists())
                stages = list(attempt.glob('.audit.json.*.tmp'))
                self.assertEqual(1, len(stages))
                self.assertTrue(stages[0].read_bytes().endswith(b'\n'))
                self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.run_capture(prepared)
                self.assertEqual(native_before + 1, self.native.call_count)

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
                         'input-manifest.json', 'audit.json',
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

    def test_input_manifest_duplicate_keys_refuse_historical_readback(self):
        result = self.run_capture()
        path = self.attempt(result) / 'input-manifest.json'
        original = path.read_bytes()
        try:
            path.write_bytes(b'{"capture_id":"first","capture_id":"second"}\n')
            with self.assertRaisesRegex(ValueError, 'duplicate keys'):
                capture._read_bounded_ordinary_json(path)
            with self.assertRaises((OSError, ValueError, ProcessError)):
                capture.show(self.state, result['attempt_id'])
        finally:
            path.write_bytes(original)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_run_input_records_publish_through_core_and_refuse_duplicate_keys(self):
        with patch.object(capture, 'publish_immutable_bytes',
                          wraps=capture.publish_immutable_bytes) as published:
            result = self.run_capture()
        attempt = self.attempt(result)
        paths = [attempt / name for name in ('runtime-lock.json', 'protocol.json', 'launch.json')]
        for path in paths:
            self.assertIn(path, [call.args[0] for call in published.call_args_list])
            raw = path.read_bytes()
            self.assertEqual(check_storage.canonical(json.loads(raw)) + b'\n', raw)
            duplicate = b'{"id":"first","id":"second"}\n'
            path.write_bytes(duplicate)
            try:
                with self.assertRaisesRegex(ValueError, 'duplicate keys'):
                    capture._read_bounded_ordinary_json(path)
                with self.assertRaises((OSError, ValueError, ProcessError)):
                    capture.show(self.state, result['attempt_id'])
            finally:
                path.write_bytes(raw)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_run_input_interruption_retains_evidence_and_refuses_native_replay(self):
        original_link = durable_records.os.link
        original_fsync = durable_records.fsync_directory
        for name in ('runtime-lock.json', 'protocol.json', 'launch.json'):
            for moment in ('before-link', 'after-link-before-flush'):
                with self.subTest(name=name, moment=moment):
                    prepared = self.prepare()
                    attempt = self.attempt(prepared)
                    target = attempt / name
                    old_stage = attempt / ('.record-' + 'c' * 32)
                    new_stage = attempt / ('.' + name + '.01234567')

                    def seed_stages():
                        old_stage.write_bytes(b'old stage remains')
                        new_stage.write_bytes(b'new stage remains')

                    def interrupt_link(source, destination, *args, **kwargs):
                        if Path(destination) == target:
                            seed_stages()
                            raise OSError('synthetic run-input before-link interruption')
                        return original_link(source, destination, *args, **kwargs)

                    def interrupt_flush(directory):
                        if Path(directory) == attempt and target.exists():
                            seed_stages()
                            raise OSError('synthetic run-input after-link interruption')
                        return original_fsync(directory)

                    component = durable_records.os if moment == 'before-link' else durable_records
                    attribute = 'link' if moment == 'before-link' else 'fsync_directory'
                    replacement = interrupt_link if moment == 'before-link' else interrupt_flush
                    with patch.object(component, attribute, side_effect=replacement):
                        with self.assertRaisesRegex(OSError, 'synthetic run-input .* interruption'):
                            self.run_capture(prepared)
                    self.assertEqual(moment == 'after-link-before-flush', target.exists())
                    self.assertEqual(b'old stage remains', old_stage.read_bytes())
                    self.assertEqual(b'new stage remains', new_stage.read_bytes())
                    self.assertTrue((attempt / 'run-started.json').is_file())
                    self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
                    with self.assertRaisesRegex(ValueError, 'already attempted'):
                        self.run_capture(prepared)
                    self.native.assert_not_called()

    def test_input_manifest_interruption_keeps_attempt_and_both_stage_shapes(self):
        original_link = durable_records.os.link
        original_fsync = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                prepared = self.prepare()
                attempt = self.attempt(prepared)
                input_path = attempt / 'input-manifest.json'
                old_stage = attempt / ('.record-' + 'a' * 32)
                new_stage = attempt / '.input-manifest.json.01234567'

                def seed_stages():
                    old_stage.write_bytes(b'old stage remains')
                    new_stage.write_bytes(b'new stage remains')

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target) == input_path:
                        seed_stages()
                        raise OSError('synthetic before-link interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if Path(directory) == attempt and input_path.exists():
                        seed_stages()
                        raise OSError('synthetic after-link interruption')
                    return original_fsync(directory)

                target = ('os.link' if moment == 'before-link' else 'fsync_directory')
                replacement = interrupt_link if moment == 'before-link' else interrupt_flush
                with patch.object(durable_records.os if target == 'os.link' else durable_records,
                                  'link' if target == 'os.link' else target, side_effect=replacement):
                    with self.assertRaisesRegex(OSError, 'synthetic .* interruption'):
                        self.run_capture(prepared)
                self.assertEqual(moment == 'after-link-before-flush', input_path.exists())
                self.assertEqual(b'old stage remains', old_stage.read_bytes())
                self.assertEqual(b'new stage remains', new_stage.read_bytes())
                self.assertTrue((attempt / 'run-started.json').is_file())
                self.assertEqual('failed', capture.show(self.state, prepared['attempt_id'])['state'])
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.run_capture(prepared)
                self.native.assert_not_called()

    def test_failed_result_reopens_through_core_as_historical_ordinary_v1(self):
        prepared = self.prepare()
        with patch.object(self.adapter, 'RECIPE_GRAPH_API_VERSION', 2):
            with self.assertRaisesRegex(ValueError, 'API'):
                self.run_capture(prepared)
        attempt = self.attempt(prepared)
        result_path = attempt / 'result.json'
        failed = capture.show(self.state, prepared['attempt_id'])
        self.assertEqual(('failed', False), (failed['state'], failed['native_admitted']))
        self.assertEqual(check_storage.canonical(failed) + b'\n', result_path.read_bytes())
        if os.name != 'nt':
            result_path.chmod(0o644)
            try:
                with patch.object(capture, 'read_bounded_single_link_bytes',
                                  wraps=capture.read_bounded_single_link_bytes) as historical_read:
                    self.assertEqual(failed, capture.show(self.state, prepared['attempt_id']))
                self.assertEqual(
                    [(attempt / 'request.json',), (attempt / 'request.json',), (result_path,)],
                    [call.args for call in historical_read.call_args_list],
                )
            finally:
                result_path.chmod(0o600)

    def test_result_readback_refuses_changed_and_duplicate_keys(self):
        result = self.run_capture()
        path = self.attempt(result) / 'result.json'
        original = path.read_bytes()
        for replacement in (b'{"id":"first","id":"second"}\n', b'changed result bytes'):
            with self.subTest(replacement=replacement):
                path.write_bytes(replacement)
                try:
                    with self.assertRaises((OSError, ValueError, ProcessError)):
                        capture.show(self.state, result['attempt_id'])
                finally:
                    path.write_bytes(original)
        path.unlink()
        try:
            self.assertEqual('interrupted', capture.show(self.state, result['attempt_id'])['state'])
        finally:
            path.write_bytes(original)
        self.assertEqual(result, capture.show(self.state, result['attempt_id']))

    def test_result_interruption_keeps_both_stage_shapes_and_blocks_replay(self):
        original_link = durable_records.os.link
        original_fsync = durable_records.fsync_directory
        for moment in ('before-link', 'after-link-before-flush'):
            with self.subTest(moment=moment):
                prepared = self.prepare()
                attempt = self.attempt(prepared)
                result_path = attempt / 'result.json'
                old_stage = attempt / ('.record-' + 'b' * 32)
                new_stage = attempt / '.result.json.01234567'

                def seed_stages():
                    old_stage.write_bytes(b'old stage remains')
                    new_stage.write_bytes(b'new stage remains')

                def interrupt_link(source, target, *args, **kwargs):
                    if Path(target) == result_path:
                        seed_stages()
                        raise OSError('synthetic result before-link interruption')
                    return original_link(source, target, *args, **kwargs)

                def interrupt_flush(directory):
                    if Path(directory) == attempt and result_path.exists():
                        seed_stages()
                        raise OSError('synthetic result after-link interruption')
                    return original_fsync(directory)

                native_before = self.native.call_count
                target = ('os.link' if moment == 'before-link' else 'fsync_directory')
                replacement = interrupt_link if moment == 'before-link' else interrupt_flush
                with patch.object(durable_records.os if target == 'os.link' else durable_records,
                                  'link' if target == 'os.link' else target, side_effect=replacement):
                    with self.assertRaisesRegex(OSError, 'synthetic result .* interruption'):
                        self.run_capture(prepared)
                self.assertEqual(native_before + 1, self.native.call_count)
                self.assertEqual(moment == 'after-link-before-flush', result_path.exists())
                self.assertEqual(b'old stage remains', old_stage.read_bytes())
                self.assertEqual(b'new stage remains', new_stage.read_bytes())
                self.assertTrue((attempt / 'run-started.json').is_file())
                observed = capture.show(self.state, prepared['attempt_id'])
                self.assertEqual('complete' if result_path.exists() else 'interrupted', observed['state'])
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.run_capture(prepared)
                self.assertEqual(native_before + 1, self.native.call_count)

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
