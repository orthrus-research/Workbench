"""Retained source/result orchestration with a stubbed native response.

Native behavior is separately checked through the installed Java worker. This
suite exercises storage, source capture, profile binding and exact diagnostics.
"""

import copy
import contextlib
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_developer_feature import ROOT, _checkout
from workbench_api.processes import ProcessError
from workbench_axiom.material_checks import source_acknowledgement
from workbench_core import check_storage as storage
from workbench_core import setup_cli
from workbench_core.modules import InstalledModule
from workbench_profile_supersymmetry import axiom as policy
from workbench_profile_cleanroom import axiom as platform_policy
from workbench_project_intelligence.working_tree import capture_source_inputs
from workbench_shell.developer_context import DeveloperSelection, verify_developer_owner_reference
from workbench_shell.developer_checks import run_checks
from workbench_shell import developer_material_checks as saved


class SavedMaterialCheckTests(unittest.TestCase):
    def test_sandbox_backend_is_bound_to_the_prepared_native_attempt(self):
        request = self.prepare("--sandbox-backend", "docker")
        self.assertEqual("docker", request["sandbox_backend"])
        _, native = self.execute(request)
        self.assertEqual("docker", native.call_args.args[1].sandbox_backend)

    def test_old_diagnostic_revision_verifies_without_manufactured_group_counts(self):
        from workbench_shell import developer_material_delivery as delivery
        original = delivery._contents
        request = self.prepare()
        with patch.object(delivery, '_contents', side_effect=lambda *args, **kwargs: original(*args, grouped=False)):
            self.execute(request)
        value = self.client_command('diagnostics', request['attempt_id'])['result']
        self.assertNotIn('finding_counts', value)
        self.assertEqual(1, len(value['findings']))
        # Full source/evidence reconciliation still accepts the sealed old shape.
        saved.reopen(self.state / 'developer-checks', request['attempt_id'], self.selection)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.client_command('diagnostics', request['attempt_id'], '--revision', value['id'], '--group', 'error-located')

    def test_early_groups_reach_unlocated_errors_without_loading_source_warnings(self):
        from workbench_shell import developer_material_delivery as delivery
        request = self.prepare()
        native = self.response(request)
        located = native['result']['execution']['diagnostics'][0]
        native['result']['execution']['diagnostics'] = [
            {**located, 'severity': 'warning', 'message': 'located ' + str(i)} for i in range(140)
        ] + [{'channel': 'groovy-log', 'severity': 'ERROR', 'message': 'unlocated ' + str(i), 'locations': []} for i in range(150)]
        self.execute(request, native)
        first = self.client_command('diagnostics', request['attempt_id'])['result']
        self.assertEqual(290, first['findings_count'])
        self.assertEqual(128, len(first['findings']))
        self.assertTrue(all(f['location'] for f in first['findings']))
        self.assertEqual({'error-located': 0, 'error-unlocated': 150, 'warning-located': 140,
            'warning-unlocated': 0, 'information-located': 0, 'information-unlocated': 0}, first['finding_counts'])
        found, offset = [], 0
        while True:
            page = self.client_command('diagnostics', request['attempt_id'], '--revision', first['id'], '--group', 'error-unlocated', '--offset', str(offset))['result']
            self.assertEqual(150, page['group_count']); self.assertEqual(290, page['findings_count'])
            self.assertTrue(all(delivery.finding_group(row) == 'error-unlocated' for row in page['findings']))
            found.extend(page['findings'])
            if page['next_offset'] is None: break
            offset = page['next_offset']
        self.assertEqual(['diagnostic-' + str(i) for i in range(140, 290)], [f['id'] for f in found])
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            delivery.view(self.state / 'developer-checks', request['attempt_id'], self.selection, group='unknown')

    def test_diagnostics_and_exact_source_are_available_before_snapshot_publication(self):
        request = self.prepare()
        identity = request['attempt_id']
        observed = {}
        def publishing(*args, **kwargs):
            self.assertFalse((self.directory(request) / 'snapshot/publication.json').exists())
            with patch.object(saved, '_load', side_effect=AssertionError('progress must not reload all source')):
                status = self.client_command('delivery', identity)['result']
            self.assertEqual('preparing', status['detail_state'])
            early = self.client_command('diagnostics', identity, '--revision', status['diagnostic_id'])
            view = early['result']; observed.update(view)
            self.assertTrue(early['presentation']['source_current'])
            self.assertEqual('native-failed', view['native_outcome'])
            self.assertEqual(1, view['findings_count'])
            finding = view['findings'][0]
            evidence = self.client_command('diagnostic', identity, '--revision', view['id'], '--diagnostic', finding['id'])['result']
            self.assertEqual('native fixture error', evidence['native']['message'])
            source = self.client_command('source', identity, '--revision', view['id'], '--diagnostic', finding['id'])['result']
            self.assertEqual(self.source.read_bytes().decode('utf-8'), source['text'])
            self.assertEqual(view['id'], source['result_id'])
            self.assertEqual(finding, source['source'])
            raise OSError('interrupted finalizer')
        with patch.object(saved, 'publish_snapshot', side_effect=publishing):
            with self.assertRaisesRegex(OSError, 'interrupted finalizer'): self.execute(request)
        status = self.client_command('delivery', identity)['result']
        self.assertEqual('interrupted', status['detail_state'])
        self.assertEqual(observed['id'], status['diagnostic_id'])
        self.source.write_text('new saved text\n')
        changed = self.client_command('diagnostics', identity, '--revision', observed['id'])
        self.assertFalse(changed['presentation']['source_current'])
        self.assertEqual(observed['findings'], changed['result']['findings'])

    def test_delivery_pages_cannot_mix_attempts_and_join_snapshot_custody(self):
        request = self.prepare()
        self.execute(request)
        identity = request['attempt_id']
        early = self.client_command('diagnostics', identity)['result']
        self.assertEqual('ready', self.client_command('delivery', identity)['result']['detail_state'])
        with self.assertRaisesRegex(ValueError, 'another input or revision'):
            self.client_command('diagnostics', identity, '--revision', 'check-diagnostics:sha256:' + '0' * 64)
        original, _ = saved.reopen(self.state / 'developer-checks', identity, self.selection)
        self.assertEqual(original['findings'], early['findings'])
        self.assertEqual(early['id'], original['diagnostic_delivery'])
        publication = storage.read_json(self.directory(request) / 'snapshot/publication.json', byte_limit=None)
        self.assertTrue(any(row['role'].startswith('diagnostic-delivery-') for row in publication['manifest']['retained_inputs']))
        page = self.directory(request) / 'diagnostic-delivery/findings-0.json'
        page.write_bytes(page.read_bytes().replace(b'native fixture error', b'native fixture wrong'))
        with self.assertRaisesRegex(ValueError, 'content changed'):
            self.client_command('diagnostics', identity, '--revision', early['id'])

    def test_core_retention_route_reviews_exact_settings_outside_reader_lease(self):
        from workbench_core import check_retention
        before = self.client_command('retention', 'status')['result']
        self.assertEqual('disabled', before['policy']['settings']['mode'])
        settings = {**check_retention.RECOMMENDATION, 'mode': 'keep-everything'}
        encoded = json.dumps(settings)
        proposal = self.client_command('retention', 'configure', '--settings', encoded)['result']['proposal']
        configured = self.client_command('retention', 'configure', '--settings', encoded, '--confirm', proposal['id'])['result']
        self.assertEqual('configured', configured['state'])
        self.assertEqual('disabled', self.client_command('retention', 'maintain')['result']['state'])

    def test_registered_history_remains_honest_through_retirement_restore_and_expiry(self):
        from workbench_core import check_lifecycle as life
        from workbench_core.storage import manager
        request = self.prepare()
        self.execute(request)
        attempt = self.directory(request)
        root = attempt.parent.parent.parent
        self.assertEqual(request['attempt_id'], life.history(root)['checks'][0]['attempt_id'])
        life.export_bundle(root, attempt.name, self.base / 'evidence-bundle')
        item = next(row for row in manager.inventory_storage(root)['items'] if row['path'] == str(attempt))
        receipt = manager.execute_cleanup(root, manager.plan_cleanup(root, selector=item['item_id']))
        self.assertEqual('retired', self.client_command('history')['result']['attempts'][0]['state'])
        with self.assertRaisesRegex(ValueError, 'details are retired'):
            saved._load(root, attempt.name, self.selection)
        trash = next(row for row in manager.inventory_storage(root)['items'] if row['resource_id'] == receipt['result']['trash_id'])
        manager.execute_purge_trash(root, manager.plan_purge_trash(root, selector=trash['item_id'], confirmation=trash['resource_id']))
        row = self.client_command('history')['result']['attempts'][0]
        self.assertEqual('expired', row['state'])
        self.assertEqual('completed', row['original_summary']['state'])
        self.assertFalse(attempt.exists())

    def test_historical_reader_support_is_separate_from_capture_and_unknown_details_export(self):
        request = self.prepare()
        self.execute(request)
        directory = self.directory(request)
        publication_path = directory / 'snapshot/publication.json'
        publication = storage.read_json(publication_path, byte_limit=None)
        publication['summary']['overview']['format'] = 'axiom-check-overview-v1'
        publication.pop('id')
        publication_path.write_bytes(storage.canonical(storage.seal('check-snapshot-publication', publication)))
        first = self.client_command('show', request['attempt_id'])['result']
        self.assertEqual('loaded', first['overview_state'])
        self.assertEqual('complete', first['interpretation']['state'])
        before = publication_path.read_bytes()
        with patch('workbench_axiom.retained_snapshots.SUPPORTED_SCHEMAS', frozenset()):
            unknown = self.client_command('show', request['attempt_id'])['result']
            self.assertEqual(first['coverage'], unknown['coverage'])
            self.assertEqual(first['native_outcome'], unknown['native_outcome'])
            self.assertEqual('unsupported', unknown['interpretation']['state'])
            self.assertEqual('unsupported', unknown['finding_page']['state'])
            destination = self.base / 'unknown-export.json'
            self.client_command('export', request['attempt_id'], '--snapshot', unknown['snapshot_id'],
                                '--destination', str(destination))
            self.assertEqual((directory / 'result.json').read_bytes(), destination.read_bytes())
        self.assertEqual(before, publication_path.read_bytes())

    def test_retained_comparison_uses_both_snapshots_without_native_invocation(self):
        request = self.prepare()
        self.execute(request)
        with patch('workbench_axiom.cli.invoke', side_effect=AssertionError('retained comparison must not initialize')):
            result = self.client_command('compare', request['attempt_id'], request['attempt_id'])['result']
        self.assertTrue(all(row['state'] == 'unchanged' for row in result['sections']['comparable']))
        self.assertEqual(['admission-findings'], [row['section'] for row in result['sections']['incompatible']])
        self.assertFalse(result['native_support_established'])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="saved-material-tests-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.pack = _checkout(self.base)
        self.state = self.base / "state"
        self.core_environment = {"WORKBENCH_CONFIG_HOME": str(self.base / "core-configuration")}
        environment_patch = patch.dict(os.environ, self.core_environment)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        self.selection = DeveloperSelection(self.pack.as_uri(), "supersymmetry", "cleanroom", "cleanroom-provisional")
        self.program = self.pack / "authoring/groovy"
        (self.program / "material").mkdir(parents=True)
        (self.program / "runConfig.json").write_text('{"packId":"supersymmetry"}')
        self.source = self.program / "material/Developer.groovy"
        self.source.write_bytes("// 🌍 developer source\r\ninvalid source\n".encode())
        (self.pack / "material-intent.json").write_text('{"observeMaterials":["supersymmetry:example"]}')
        self.engine = self.base / "engine"
        (self.engine / "lib").mkdir(parents=True)
        (self.engine / "lib/engine.jar").write_bytes(b"test only; not executable")
        manifest = {"schema": "axiom.installation.v1", "component": "workbench-axiom-engine", "version": "0.1.0",
                    "mainClass": "research.orthrus.axiom.Main", "jars": {"engine.jar": sha256(b"test only; not executable").hexdigest()}}
        (self.engine / "engine-manifest.json").write_text(json.dumps(manifest))
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        (self.runtime / "runtime.bin").write_bytes(b"fixture runtime")
        java_row = {"path": "bin/java", "size": len(b"fixture only"), "sha256": sha256(b"fixture only").hexdigest()}
        catalog = policy.material_contexts()
        self.context_id = catalog["policy"]["contexts"][0]["id"]
        self.context = catalog["policy"]["contexts"][0]
        (self.runtime / "runtime.json").write_text(json.dumps({"schema": "axiom.material-runtime.v1", "context": self.context,
            "contextPolicySha256": catalog["sha256"], "admissionPolicySha256": policy.material_admission(self.context_id)["sha256"],
            "files": [{"path": "runtime.bin", "size": 15, "sha256": sha256(b"fixture runtime").hexdigest()}],
            "runtimeInputs": [java_row]}))
        self.java = self.base / "jdk/bin/java"
        self.java.parent.mkdir(parents=True)
        self.java.write_bytes(b"fixture only")
        self.java.chmod(0o700)
        self.jvm_policy = {"profile": "cleanroom", "sha256": "fixture-policy-only", "policy": {
            "profile": "cleanroom", "schema": "axiom.jvm-runtime.v1", "selectionStatus": "fixture-not-qualified",
            "runtimeFiles": [java_row], "compilerFiles": []}}
        self.jvm_policy_patch = patch.object(platform_policy, "jvm_policy", return_value=self.jvm_policy)
        self.jvm_policy_patch.start()
        self.addCleanup(self.jvm_policy_patch.stop)

    def client_command(self, *args):
        return run_checks(self.selection, ["materials", *args], state_root=self.state)

    def command(self, *args):
        # These existing cases exercise complete owner evidence and source
        # custody. The public CLI now returns a small view, tested separately.
        envelope = self.client_command(*args)
        if envelope['result'].get('format') == 'workbench-material-check-view-v1':
            view = envelope['result']
            record, _ = saved.reopen(self.state / 'developer-checks', view['attempt_id'], self.selection)
            self.assertEqual(view['id'], record['id'])
            self.assertEqual(view['native_exit_code'], record['native_exit_code'])
            envelope = {**envelope, 'result': record}
        return envelope

    def prepare(self, *extra, intent="material-intent.json"):
        intent_args = [] if intent is None else ["--request", intent]
        return self.command("prepare", "--engine-home", str(self.engine), "--runtime-home", str(self.runtime),
                            "--java", str(self.java), "--context", self.context_id, "--program-root", "authoring",
                            *intent_args, *extra)["result"]

    def configure(self, *extra):
        return self.command("setup", "--engine-home", str(self.engine), "--runtime-home", str(self.runtime),
                            "--java", str(self.java), "--context", self.context_id,
                            "--program-root", "authoring", *extra)["result"]

    def core_setup(self, *, managed=False):
        home = str(self.java.parent.parent)
        return setup_cli._write_setup_record(setup_cli.default_setup_record_path(environment=self.core_environment), {
            "workspace": str(self.pack), "state_root": str(self.state),
            "profile_config": None, "profile_selection_digest": None,
            "java_home": None if managed else home, "managed_java_home": home if managed else None,
            "git_executable": None,
        })

    def configure_using_core(self):
        return self.command("setup", "--engine-home", str(self.engine), "--runtime-home", str(self.runtime),
                            "--context", self.context_id, "--program-root", "authoring")["result"]

    def preparation_requirements(self):
        source = self.base / "original-server.jar"
        source.write_bytes(b"local original server fixture")
        return {
            "schema": "axiom.native-input-preparation.v1", "profile": "cleanroom", "side": "server",
            "inputStage": "raw-original-artifacts", "policySha256": {
                "native-root-class-space.json": "a" * 64, "native-identity-runtime.json": "b" * 64,
            }, "artifacts": [{"path": "net/minecraft/server/1.12.2/server.jar", "url": source.as_uri(),
                "size": source.stat().st_size, "sha256": sha256(source.read_bytes()).hexdigest()}],
        }

    def test_setup_prepare_uses_profile_inputs_without_jvm_or_native_readiness(self):
        before = capture_source_inputs(self.pack)
        requirements = self.preparation_requirements()
        with patch.object(platform_policy, "preparation_inputs", return_value=requirements), \
                patch.object(saved.setup, "selected_java", side_effect=AssertionError("raw inputs do not need Java")), \
                patch("workbench_axiom.cli.invoke", side_effect=AssertionError("raw preparation must not execute")):
            result = self.command("setup", "--prepare", "--context", self.context_id)["result"]
            self.assertEqual(result, self.command("setup", "--prepare", "--context", self.context_id)["result"])
            self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])
            with self.assertRaisesRegex(ValueError, "setup is missing"):
                self.command("run", "--context", self.context_id)
        self.assertEqual("inputs-prepared", result["state"])
        self.assertEqual("incomplete", result["setup_state"])
        self.assertIn("engine", result["pending"])
        self.assertIn("native-runtime", result["pending"])
        self.assertEqual(requirements, result["requirements"])
        self.assertEqual(self.context, result["inputs"]["context"])
        self.assertIn("platformOwner", result["inputs"])
        self.assertIn("profileOwner", result["inputs"])
        artifact_root = Path(result["paths"]["artifact_root"])
        self.assertEqual(b"local original server fixture", (artifact_root / requirements["artifacts"][0]["path"]).read_bytes())
        self.assertFalse((self.state / "developer-checks/artifacts").exists())
        self.assertEqual(before, capture_source_inputs(self.pack))

    def test_setup_prepare_keeps_existing_explicit_setup(self):
        configured = self.configure()
        with patch.object(platform_policy, "preparation_inputs", return_value=self.preparation_requirements()):
            self.command("setup", "--prepare", "--context", self.context_id)
        status = self.command("setup-status", "--context", self.context_id)["result"]
        self.assertEqual("ready", status["state"])
        self.assertEqual(configured["id"], status["setup_id"])

    def pack_preparation_requirements(self):
        source = self.base / "original-addon.jar"
        source.write_bytes(b"local original addon fixture")
        return {
            "schema": "axiom.native-input-preparation.v1", "profile": "supersymmetry", "side": "server",
            "inputStage": "raw-original-artifacts", "policySha256": {"axiom-native-inputs.json": "c" * 64},
            "artifacts": [{"path": "mods/original-addon.jar", "url": source.as_uri(),
                "size": source.stat().st_size, "sha256": sha256(source.read_bytes()).hexdigest()}],
        }

    def test_pack_preparation_stages_both_owners_reuses_and_refuses_changed_pack_bytes(self):
        context_id = "supersymmetry:material-authoring-pack"
        platform, pack = self.preparation_requirements(), self.pack_preparation_requirements()
        before = capture_source_inputs(self.pack)
        with patch.object(platform_policy, "preparation_inputs", return_value=platform), \
                patch.object(policy, "preparation_inputs", return_value=pack), \
                patch("workbench_axiom.cli.invoke", side_effect=AssertionError("input preparation must not execute")):
            result = self.command("setup", "--prepare", "--context", context_id)["result"]
            self.assertEqual(platform["artifacts"] + pack["artifacts"], result["requirements"]["artifacts"])
            self.assertEqual({"cleanroom/native-root-class-space.json", "cleanroom/native-identity-runtime.json",
                              "supersymmetry/axiom-native-inputs.json"}, set(result["requirements"]["policySha256"]))
            with patch.object(saved.setup, "fetch_verified_artifact", side_effect=AssertionError("reuse needs no download")):
                self.assertEqual(result, self.command("setup", "--prepare", "--context", context_id)["result"])
            addon = Path(result["paths"]["artifact_root"]) / pack["artifacts"][0]["path"]
            self.assertEqual(b"local original addon fixture", addon.read_bytes())
            addon.write_bytes(b"changed input")
            with self.assertRaisesRegex(ValueError, "prepared native input files differ"):
                self.command("setup", "--prepare", "--context", context_id)
            self.assertEqual("missing", self.command("setup-status", "--context", context_id)["result"]["state"])
        self.assertEqual(before, capture_source_inputs(self.pack))

    def test_pack_owner_change_during_download_leaves_setup_incomplete(self):
        context_id = "supersymmetry:material-authoring-pack"
        platform, pack = self.preparation_requirements(), self.pack_preparation_requirements()
        original = saved.setup.fetch_verified_artifact

        def fetch(**kwargs):
            value = original(**kwargs)
            pack["policySha256"]["axiom-native-inputs.json"] = "d" * 64
            return value

        with patch.object(platform_policy, "preparation_inputs", return_value=platform), \
                patch.object(policy, "preparation_inputs", return_value=pack), \
                patch.object(saved.setup, "fetch_verified_artifact", side_effect=fetch), \
                self.assertRaisesRegex(ValueError, "differs from the installed profile policies"):
            self.command("setup", "--prepare", "--context", context_id)
        self.assertEqual("missing", self.command("setup-status", "--context", context_id)["result"]["state"])
        self.assertTrue(list(self.state.rglob("failure.json")))
        self.assertFalse(list(self.state.rglob("preparation.json")))

    def test_pack_and_platform_cannot_overwrite_each_others_artifact(self):
        platform, pack = self.preparation_requirements(), self.pack_preparation_requirements()
        pack["artifacts"][0]["path"] = platform["artifacts"][0]["path"]
        with patch.object(platform_policy, "preparation_inputs", return_value=platform), \
                patch.object(policy, "preparation_inputs", return_value=pack), \
                patch.object(saved.setup, "fetch_verified_artifact", side_effect=AssertionError("ambiguous inputs must not download")), \
                self.assertRaisesRegex(ValueError, "repeats an artifact path"):
            self.command("setup", "--prepare", "--context", "supersymmetry:material-authoring-pack")

    def test_setup_prepare_requires_selected_server_context_before_acquisition(self):
        catalog = saved._domain().contexts(self.selection.pack_profile)
        wrong_side = copy.deepcopy(catalog)
        wrong_side["policy"]["contexts"][0]["side"] = "client"
        for context, selected_catalog in (("supersymmetry:unknown", catalog), (self.context_id, wrong_side)):
            with self.subTest(context=context), \
                    patch.object(platform_policy, "preparation_inputs", return_value=self.preparation_requirements()), \
                    patch.object(saved._domain(), "contexts", return_value=selected_catalog), \
                    patch.object(saved.setup, "fetch_verified_artifact", side_effect=AssertionError("invalid context must not acquire")), \
                    self.assertRaisesRegex(ValueError, "selected SERVER context"):
                self.command("setup", "--prepare", "--context", context)

    def test_setup_prepare_does_not_mix_explicit_binding_arguments(self):
        for extra in (("--java", str(self.java)),
                      ("--engine-home", str(self.engine), "--engine-archive", "engine.zip"),
                      ("--runtime-home", str(self.runtime)), ("--program-root", "authoring")):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(saved.setup, "fetch_verified_artifact", side_effect=AssertionError("argument error must not acquire")), \
                    self.assertRaises(SystemExit):
                self.command("setup", "--prepare", "--context", self.context_id, *extra)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.command("setup", "--context", self.context_id)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.command("setup", "--engine-archive", "engine.zip", "--context", self.context_id)

    def test_setup_prepare_can_assemble_with_engine_and_core_selected_java(self):
        context_id = "supersymmetry:material-authoring-pack"
        self.core_setup()
        with patch.object(platform_policy, "preparation_inputs", return_value=self.preparation_requirements()), \
                patch.object(policy, "preparation_inputs", return_value=self.pack_preparation_requirements()), \
                patch.object(saved, "_assemble_prepared_runtime", return_value={"state": "configured-not-run"}) as assemble:
            result = self.command("setup", "--prepare", "--engine-home", str(self.engine), "--context", context_id)["result"]
        self.assertEqual("configured-not-run", result["state"])
        args = assemble.call_args.args
        self.assertEqual((context_id, self.engine, self.java), (args[2], args[4], args[5]))
        self.assertEqual("inputs-prepared", args[3]["state"])

    def test_setup_prepare_installs_engine_zip_before_managed_runtime_assembly(self):
        from workbench_axiom.cli import ENGINE_NOTICES
        archive_path = self.base / "engine.zip"
        prefix = "workbench-axiom-engine-0.1.0/"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in self.engine.rglob("*"):
                if path.is_file():
                    archive.write(path, prefix + path.relative_to(self.engine).as_posix())
            for name in ENGINE_NOTICES:
                archive.writestr(prefix + name, b"original notice fixture")
        self.core_setup()
        context_id = "supersymmetry:material-authoring-pack"
        with patch.object(platform_policy, "preparation_inputs", return_value=self.preparation_requirements()), \
                patch.object(policy, "preparation_inputs", return_value=self.pack_preparation_requirements()), \
                patch.object(saved, "_assemble_prepared_runtime", return_value={"state": "configured-not-run"}) as assemble:
            self.command("setup", "--prepare", "--engine-archive", str(archive_path), "--context", context_id)
        args = assemble.call_args.args
        self.assertEqual(self.java, args[5])
        self.assertNotEqual(self.engine, args[4])
        self.assertTrue(args[4].is_relative_to(self.state))
        self.assertEqual((self.engine / "lib/engine.jar").read_bytes(), (args[4] / "lib/engine.jar").read_bytes())

    def test_setup_prepare_propagates_cancellation_without_publishing(self):
        with patch.object(platform_policy, "preparation_inputs", return_value=self.preparation_requirements()), \
                patch.object(saved.setup, "fetch_verified_artifact", side_effect=AssertionError("cancelled before acquisition")), \
                self.assertRaisesRegex(ValueError, "cancelled"):
            run_checks(self.selection, ["materials", "setup", "--prepare", "--context", self.context_id],
                       state_root=self.state, cancelled=lambda: True)
        self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])

    def test_initial_setup_reuses_core_java_without_native_execution_or_provisioning(self):
        before = capture_source_inputs(self.pack)
        for managed in (False, True):
            with self.subTest(managed=managed):
                self.core_setup(managed=managed)
                with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("setup must not execute")), \
                        patch("workbench_core.runtime_java.ensure_java_runtime", side_effect=AssertionError("setup must not provision")):
                    configured = self.configure_using_core()
                self.assertEqual(str(self.java), configured["paths"]["java"])
                self.assertEqual("configured-not-run", configured["state"])
                self.assertEqual("fixture-not-qualified", configured["inputs"]["jvmSelectionStatus"])
                prepared = self.command("prepare", "--context", self.context_id)["result"]
                self.assertEqual(configured["id"], prepared["setup_id"])
                self.assertEqual(configured["paths"], prepared["paths"])
        self.assertEqual(before, capture_source_inputs(self.pack))

    def test_initial_setup_requires_core_java_or_explicit_java(self):
        with self.assertRaisesRegex(ValueError, "Core setup has no selected Java"):
            self.configure_using_core()
        self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])
        with patch.object(saved.setup, "selected_java", side_effect=AssertionError("explicit Java takes precedence")):
            configured = self.configure()
        self.assertEqual(str(self.java), configured["paths"]["java"])

    def test_initial_setup_reused_core_java_must_match_current_profile_bytes(self):
        self.core_setup()
        self.java.write_bytes(b"wrong Core-selected JVM")
        with self.assertRaisesRegex(ValueError, "platform JVM policy"):
            self.configure_using_core()
        self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])

    def test_reused_core_java_is_revalidated_after_material_setup(self):
        self.core_setup()
        self.configure_using_core()
        self.java.unlink()
        self.assertEqual("stale", self.command("setup-status", "--context", self.context_id)["result"]["state"])
        with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("no stale run")), self.assertRaises(FileNotFoundError):
            self.command("run", "--context", self.context_id)

    def test_setup_is_context_owned_and_status_is_not_native_qualification(self):
        self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])
        before = capture_source_inputs(self.pack)
        with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("setup must not execute")):
            configured = self.configure()
            status = self.command("setup-status", "--context", self.context_id)["result"]
        self.assertEqual("configured-not-run", configured["state"])
        self.assertEqual("ready", status["state"])
        self.assertEqual(configured["id"], status["setup_id"])
        self.assertEqual("authoring", status["program_root"])
        self.assertEqual("saved-input-and-profile-bindings-only", status["readiness_scope"])
        self.assertEqual("missing", self.command("setup-status", "--context", "supersymmetry:other")["result"]["state"])
        self.selection = DeveloperSelection(self.pack.as_uri(), "supersymmetry", "cleanroom", "different-variant")
        self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])
        self.assertEqual(before, capture_source_inputs(self.pack))

    def test_saved_setup_reruns_recapture_source_and_do_not_reprompt_for_paths(self):
        configured = self.configure()
        requests = []
        def invoke(operation, args, execution, **capture):
            request = json.loads((execution.workspace / "request.json").read_bytes())
            requests.append(request)
            return self.response(request), 4
        with patch("workbench_axiom.cli.invoke", side_effect=invoke):
            first = self.command("run", "--context", self.context_id)
            self.source.write_text("// saved developer correction\n")
            second = self.command("run", "--context", self.context_id)
        self.assertNotEqual(first["result"]["attempt_id"], second["result"]["attempt_id"])
        self.assertNotEqual(requests[0]["program"]["sha256"], requests[1]["program"]["sha256"])
        self.assertEqual([configured["id"], configured["id"]], [row["setup_id"] for row in requests])
        self.assertEqual("authoring", requests[1]["program_root"])
        self.assertEqual(configured["paths"], requests[1]["paths"])
        self.assertTrue(second["presentation"]["source_current"])

    def test_saved_setup_refuses_replaced_runtime_bytes_with_unchanged_manifest(self):
        self.configure()
        (self.runtime / "runtime.bin").write_bytes(b"replaced runtime")
        status = self.command("setup-status", "--context", self.context_id)["result"]
        self.assertEqual("stale", status["state"])
        self.assertIn("runtime files differ", status["failure"]["message"])
        with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("no stale run")), self.assertRaisesRegex(ValueError, "runtime files differ"):
            self.command("run", "--context", self.context_id)

    def test_saved_setup_refuses_missing_or_replaced_jvm(self):
        self.configure()
        self.java.write_bytes(b"different JVM")
        self.assertEqual("stale", self.command("setup-status", "--context", self.context_id)["result"]["state"])
        with self.assertRaisesRegex(ValueError, "platform JVM policy"):
            self.command("prepare", "--context", self.context_id)
        self.java.unlink()
        self.assertEqual("stale", self.command("setup-status", "--context", self.context_id)["result"]["state"])

    def test_saved_setup_detects_changed_profile_identity(self):
        self.configure()
        original = saved._binding
        def replacement(selection, paths, context_id):
            return {**original(selection, paths, context_id), "profileOwner": {"changed": True}}
        with patch.object(saved, "_binding", side_effect=replacement):
            self.assertEqual("stale", self.command("setup-status", "--context", self.context_id)["result"]["state"])
            with self.assertRaisesRegex(ValueError, "stale"):
                self.command("prepare", "--context", self.context_id)

    def test_partial_override_refuses_and_complete_override_does_not_replace_setup(self):
        configured = self.configure()
        with self.assertRaisesRegex(ValueError, "together"):
            self.command("prepare", "--context", self.context_id, "--java", str(self.java))
        request = self.prepare(intent=None)
        self.assertIsNone(request["setup_id"])
        self.assertEqual(configured["id"], self.command("setup-status", "--context", self.context_id)["result"]["setup_id"])
        with self.assertRaisesRegex(ValueError, "complete material program"):
            self.command("prepare", "--context", self.context_id, "--program-root", ".")

    def test_setup_does_not_waive_prepare_execute_freshness(self):
        self.configure()
        request = self.command("prepare", "--context", self.context_id)["result"]
        self.java.write_bytes(b"replaced after prepare")
        with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("no stale run")), self.assertRaisesRegex(ValueError, "platform JVM policy"):
            self.command("execute", request["attempt_id"], "--confirm", request["id"])

    def test_setup_requires_current_platform_jvm_not_just_an_existing_executable(self):
        self.java.write_bytes(b"wrong initial JVM")
        with self.assertRaisesRegex(ValueError, "platform JVM policy"):
            self.configure()
        self.assertEqual("missing", self.command("setup-status", "--context", self.context_id)["result"]["state"])

    def test_setup_binds_independent_platform_jvm_policy_and_checks_runtime_compiler_identity(self):
        configured = self.configure()
        self.assertEqual("fixture-policy-only", configured["inputs"]["platformJvmPolicySha256"])
        self.assertEqual("fixture-not-qualified", configured["inputs"]["jvmSelectionStatus"])
        self.assertIn("platformOwner", configured["inputs"])
        manifest = json.loads((self.runtime / "runtime.json").read_bytes())
        manifest["runtimeInputs"] = []
        (self.runtime / "runtime.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "platform JVM policy"):
            self.configure()

    def test_cleanroom_exposes_selected_packaged_jvm_policy_without_installed_native_qualification(self):
        self.jvm_policy_patch.stop()
        observed = platform_policy.jvm_policy()
        raw = (ROOT / "profiles/platforms/cleanroom/jvm-runtime.json").read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), observed["sha256"])
        self.assertEqual(json.loads(raw), observed["policy"])
        self.assertEqual("selected-workbench-mvp-runtime", observed["policy"]["selectionStatus"])
        self.assertEqual("unqualified-installed-native-runtime", observed["policy"]["qualificationStatus"])

    def directory(self, request):
        return self.state / "developer-checks/.workbench/check-attempts" / request["attempt_id"]

    def response(self, request, *, line=2, path="groovy/material/Developer.groovy"):
        body = {"sourceProgram": source_acknowledgement(request["program"]),
                "context": request["inputs"]["context"], "runtimeManifestSha256": request["inputs"]["runtimeManifestSha256"],
                "contextPolicySha256": request["inputs"]["contextPolicySha256"], "admissionPolicySha256": request["inputs"]["admissionPolicySha256"],
                "execution": {"diagnostics": [{"channel": "groovy-log", "severity": "ERROR", "message": "native fixture error",
                              "locations": [{"path": path, "line": line, "column": 8, "precision": "native-compiler"}]}]}}
        return {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "material-program", "status": "source-error",
                "result": body, "invocation": {"installationSha256": request["inputs"]["engineManifestSha256"]}}

    def execute(self, request, response=None, *, effect=None):
        if effect is None:
            effect = lambda *args, **capture: (response if response is not None else self.response(request), 1)
        with patch("workbench_axiom.cli.invoke", side_effect=effect) as native:
            result = self.command("execute", request["attempt_id"], "--confirm", request["id"])
        return result, native

    def test_native_diagnostics_need_no_saved_intent_or_material_selector(self):
        (self.pack / "material-intent.json").unlink()
        before = capture_source_inputs(self.pack)
        request = self.prepare(intent=None)
        self.assertIsNone(request["intent_path"])
        self.assertEqual(len(request["candidate"]["files"]), 2)
        self.assertEqual(b"{}", (self.directory(request) / "intent.json").read_bytes())
        response, invocation = self.execute(request)
        self.assertEqual("source-error", response["result"]["native"]["status"])
        self.assertTrue(response["result"]["findings"])
        self.assertEqual(b"{}", invocation.call_args.args[1].request.read_bytes())
        self.assertEqual(response["result"], self.command("show", request["attempt_id"])["result"])
        self.assertEqual(before, capture_source_inputs(self.pack))

    def test_file_capture_is_bound_exported_and_detects_changed_raw_evidence(self):
        from workbench_core import tool_process, check_lifecycle
        import sys
        request = self.prepare()
        original = self.response(request)
        payload = json.dumps(original).encode()
        def invoke(operation, args, context, *, capture_directory, capture_binding):
            self.assertEqual(self.directory(request) / 'native-process', capture_directory)
            self.assertEqual(request['id'], capture_binding)
            captured = tool_process.capture([sys.executable, '-c',
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(b'\\xfferror'); sys.exit(1)"],
                directory=capture_directory, binding=capture_binding, cwd=context.workspace,
                stdin=payload, environment={}, cancelled=context.cancelled, timeout_seconds=None, output_limit=None)
            with tool_process.open_output(captured.stdout) as stream:
                response = json.loads(stream.read())
            response['invocation']['capture'] = captured.reference
            return response, captured.exit_code
        response, _ = self.execute(request, effect=invoke)
        self.assertEqual('completed', response['result']['state'])
        attempt = self.directory(request)
        root = self.state / 'developer-checks'
        custody = storage.read_json(attempt / check_lifecycle.MANIFEST)
        self.assertEqual(payload, (attempt / 'native-process/stdout.raw').read_bytes())
        for name in ['stdout.raw', 'stderr.raw', 'started.json', 'capture.json']:
            self.assertIn('native-process/' + name, custody['files'])
        bundle = self.base / 'capture-evidence-export'
        check_lifecycle.export_bundle(root, attempt.name, bundle)
        check_lifecycle.verify_bundle(bundle, expected=custody)
        self.assertEqual(response['result'], self.command('show', attempt.name)['result'])
        output = attempt / 'native-process/stdout.raw'
        output.write_bytes(b' ' * len(payload))
        with self.assertRaisesRegex(ProcessError, 'changed while reading'):
            saved.reopen(root, attempt.name, self.selection)
        with self.assertRaises(ValueError):
            check_lifecycle.verify_payload(attempt, custody, full=True)

    def test_partial_file_capture_joins_incomplete_check_evidence(self):
        from workbench_core import process_capture, check_lifecycle
        request = self.prepare()
        def invoke(operation, args, context, *, capture_directory, capture_binding):
            capture = process_capture.FileCapture(capture_directory, capture_binding, None)
            capture.write_raw('stdout', b'partial native response')
            capture.commit(failure={'type': 'ProcessError', 'message': 'fixture interrupted'})
            raise ProcessError('fixture interrupted')
        response, _ = self.execute(request, effect=invoke)
        self.assertEqual('incomplete', response['result']['state'])
        self.assertIsNone(response['result']['native'])
        custody = storage.read_json(self.directory(request) / check_lifecycle.MANIFEST)
        self.assertIn('native-process/stdout.raw', custody['files'])
        self.assertEqual(response['result'], self.command('show', request['attempt_id'])['result'])

    def test_run_defaults_to_live_checkout_and_recaptures_additions_edits_and_deletions(self):
        shutil.copytree(self.program, self.pack / "groovy", dirs_exist_ok=True)
        self.source = self.pack / "groovy/material/Developer.groovy"
        recipe = self.pack / "groovy/postInit/Recipe.groovy"
        recipe.parent.mkdir(exist_ok=True); recipe.write_text("// saved recipe\n")
        metadata = self.pack / "groovy/groovy.iml"
        metadata.write_bytes(b"<module />\r\n")
        requests = []
        def invoke(operation, args, execution, **capture):
            request = json.loads((execution.workspace / "request.json").read_bytes())
            requests.append(request)
            return self.response(request), 4
        def run():
            return self.command("run", "--engine-home", str(self.engine), "--runtime-home", str(self.runtime),
                                "--java", str(self.java), "--context", self.context_id)
        with patch("workbench_axiom.cli.invoke", side_effect=invoke):
            first = run()
            recipe.unlink()
            self.source.write_text("// edited material\n")
            added = self.pack / "groovy/material/NewMaterial.groovy"
            added.write_text("// new untracked material\n")
            before = capture_source_inputs(self.pack)
            second = run()
        self.assertEqual(4, second["exit_code"])
        self.assertNotEqual(first["result"]["attempt_id"], second["result"]["attempt_id"])
        self.assertEqual(".", requests[1]["program_root"])
        self.assertIsNone(requests[1]["intent_path"])
        files = {row["path"]: row["sha256"] for row in requests[1]["program"]["files"]}
        self.assertNotIn("groovy/postInit/Recipe.groovy", files)
        self.assertIn("groovy/material/NewMaterial.groovy", files)
        self.assertEqual(sha256(metadata.read_bytes()).hexdigest(), files["groovy/groovy.iml"])
        self.assertEqual(sha256(self.source.read_bytes()).hexdigest(), files["groovy/material/Developer.groovy"])
        self.assertEqual(before, capture_source_inputs(self.pack))
        self.assertTrue(second["presentation"]["source_current"])
        self.assertEqual(second["result"], self.command("show", second["result"]["attempt_id"])["result"])

    def test_diagnostics_default_cannot_be_replaced_with_an_unrecorded_intent(self):
        request = self.prepare(intent=None)
        (self.directory(request) / "intent.json").write_bytes(b'{"observeMaterials":["supersymmetry:other"]}')
        with self.assertRaisesRegex(ValueError, "intent differs"):
            self.command("show", request["attempt_id"])

    def test_fresh_comparison_uses_current_optional_intent_not_the_baselines_choice(self):
        before = self.prepare()
        after = self.prepare("--baseline", before["attempt_id"], intent=None)
        self.assertIsNone(after["intent_path"])
        self.assertEqual(b"{}", (self.directory(after) / "intent.json").read_bytes())
        self.assertEqual(before["candidate"], after["baseline"]["candidate"])
        self.assertEqual(after, self.command("show", after["attempt_id"])["result"])
        observed = self.prepare("--baseline", after["attempt_id"])
        self.assertEqual("material-intent.json", observed["intent_path"])
        self.assertEqual(observed, self.command("show", observed["attempt_id"])["result"])

    def test_prepare_execute_reopen_and_owner_link_without_game_image_or_source_mutation(self):
        before = capture_source_inputs(self.pack)
        request = self.prepare()
        self.assertEqual(request["state"], "prepared-not-run")
        self.assertEqual(len(request["candidate"]["files"]), 3)
        self.assertFalse(request["authority"]["runtime_image_required"])
        response, invocation = self.execute(request)
        result = response["result"]
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["native"]["status"], "source-error")
        self.assertFalse(result["authority"]["validity_qualified"])
        self.assertEqual(before, capture_source_inputs(self.pack))
        self.assertEqual(invocation.call_args.args[2].workspace, self.directory(request))
        with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("no rerun")), patch.object(saved, "_binding", side_effect=AssertionError("no current runtime prerequisite")):
            reopened = self.command("show", request["attempt_id"])
            self.assertEqual(reopened["result"], result)
            self.assertTrue(reopened["presentation"]["source_current"])
            self.assertEqual(verify_developer_owner_reference(response["owner_record_ref"], self.selection, suite_root=ROOT)["last_verified_state"], "completed")
        self.assertFalse((self.pack / ".workbench").exists())

    def test_complete_large_native_trace_survives_retention_reopen_and_owner_reference(self):
        request = self.prepare()
        native = self.response(request)
        trace = "original native trace\n" * (2 * 1024**2)
        native["result"]["execution"]["diagnostics"].append({"logger": "FML", "severity": "warning",
            "message": "native early warning", "trace": trace, "locationStatus": "unlocated"})
        response, _ = self.execute(request, native)
        result = response["result"]
        self.assertEqual("completed", result["state"])
        self.assertGreater((self.directory(request) / "result.json").stat().st_size, 32 * 1024**2)
        self.assertEqual(native, result["native"])
        self.assertEqual("log4j", result["findings"][-1]["channel"])
        self.assertIsNone(result["findings"][-1]["location"])
        with patch("workbench_axiom.cli.invoke", side_effect=AssertionError("no rerun")):
            self.assertEqual(result, self.command("show", request["attempt_id"])["result"])
            self.assertEqual("completed", verify_developer_owner_reference(
                response["owner_record_ref"], self.selection, suite_root=ROOT)["last_verified_state"])

    def test_small_cli_view_and_selected_large_export_never_reopen_complete_native_json(self):
        from workbench_shell import developer_material_snapshots as views
        request = self.prepare()
        native = self.response(request)
        diagnostic = native['result']['execution']['diagnostics'][0]
        diagnostic['trace'] = 'original retained trace 🌍\n' * 5000
        self.execute(request, native)
        with patch.object(saved, 'reopen', side_effect=AssertionError('small reads cannot reopen complete results')):
            envelope = self.client_command('show', request['attempt_id'])
            view = envelope['result']
            self.assertEqual(view['format'], views.VIEW)
            self.assertLess(len(storage.canonical(envelope)), 20000)
            self.assertNotIn('execution', view['native']['result'])
            self.assertEqual(1, view['findings_count'])
            self.assertEqual('completed', verify_developer_owner_reference(
                envelope['owner_record_ref'], self.selection, suite_root=ROOT)['last_verified_state'])
            query = views.request(view['snapshot_id'], view['view_id'], 'record', 'diagnostics', record_key='item:0')
            response = self.client_command('query', request['attempt_id'], '--query', json.dumps(query))['result']
            self.assertNotIn('value', response['payload'])
            content = response['payload']['content']
            target = self.base / 'complete-diagnostic.json'
            exported = self.client_command('export', request['attempt_id'], '--snapshot', view['snapshot_id'],
                '--destination', str(target), '--section', 'diagnostics', '--key', 'item:0', '--sha256', content['sha256'])['result']
            self.assertEqual(diagnostic, json.loads(target.read_bytes()))
            self.assertEqual(content['sha256'], exported['content']['sha256'])
            other = self.client_command('show', request['attempt_id'])['result']
            self.assertEqual(view['snapshot_id'], other['snapshot_id'])
            self.assertNotEqual(view['view_id'], other['view_id'])

    def test_cli_finding_pages_preserve_total_order_and_reject_cross_view_cursors(self):
        request = self.prepare()
        native = self.response(request)
        native['result']['execution']['diagnostics'] = [
            {'channel': 'groovy-log', 'severity': 'warning', 'message': str(i) + 'x' * 1000, 'locations': []}
            for i in range(160)]
        retained, _ = self.execute(request, native)
        view = self.client_command('show', request['attempt_id'])['result']
        page, query = view['finding_page'], view['finding_query']
        self.assertFalse(page['complete'])
        bad = {**query, 'view_id': 'other-view', 'cursor': page['next_cursor']}
        with self.assertRaisesRegex(ValueError, 'cursor'):
            self.client_command('query', request['attempt_id'], '--query', json.dumps(bad))
        found = []
        while True:
            self.assertEqual(160, page['payload']['total'])
            self.assertEqual(len(found), page['payload']['offset'])
            found.extend(row['value'] for row in page['payload']['records'])
            if page['complete']: break
            query = {**query, 'cursor': page['next_cursor']}
            page = self.client_command('query', request['attempt_id'], '--query', json.dumps(query))['result']
        self.assertEqual(retained['result']['findings'], found)

    def test_compiler_label_uses_original_finding_message_without_resealing_old_evidence(self):
        request = self.prepare()
        native = self.response(request)
        diagnostic = native['result']['execution']['diagnostics'][0]
        diagnostic['message'] = 'Throwing'
        diagnostic['compilerFindings'] = [{'message': 'unexpected token: fixture', 'location': diagnostic['locations'][0]}]
        retained, _ = self.execute(request, native)
        before = (self.directory(request) / 'result.json').read_bytes()
        view = self.client_command('show', request['attempt_id'])
        self.assertEqual('unexpected token: fixture', view['presentation']['finding_labels']['diagnostic-0'])
        self.assertEqual('Throwing', view['result']['finding_page']['payload']['records'][0]['value']['message'])
        self.assertEqual(before, (self.directory(request) / 'result.json').read_bytes())
        self.assertEqual('Throwing', retained['result']['findings'][0]['message'])

    def test_cancelled_execution_does_not_restart_snapshot_publication_for_presentation(self):
        request = self.prepare()
        def cancel(*args, **capture):
            storage.write_json(self.directory(request) / 'cancel.json', {'request_id': request['id']})
            raise ProcessError('fixture cancellation')
        with patch('workbench_axiom.cli.invoke', side_effect=cancel), patch.object(saved, 'publish_snapshot', side_effect=AssertionError('cancelled indexing must not restart')):
            result = self.client_command('execute', request['attempt_id'], '--confirm', request['id'])['result']
        self.assertEqual('incomplete', result['state'])
        self.assertEqual('cancelled-before-snapshot-publication', result['detail_state'])
        self.assertIsNone(result['snapshot_id'])
        self.assertTrue((self.directory(request) / 'result.json').exists())

    def test_snapshot_history_and_source_do_not_reopen_the_full_result(self):
        request = self.prepare()
        response, _ = self.execute(request)
        finding = response['result']['findings'][0]
        with patch.object(saved, 'reopen', side_effect=AssertionError('no complete result parsing')):
            history = self.command('history')['result']['attempts']
            self.assertEqual(response['result']['id'], history[0]['record_id'])
            source = self.command('source', request['attempt_id'], '--diagnostic', finding['id'])['result']
            self.assertEqual(finding, source['source'])
            self.assertEqual(self.source.read_text(), source['text'].replace('\r\n', '\n'))

    def test_cancel_during_publication_preserves_native_failure_and_does_not_restart(self):
        from workbench_core import check_snapshot_index, check_snapshots
        request = self.prepare()
        directory = self.directory(request)
        originals = {}

        def cancel_index(path, value, views, cancelled):
            self.assertTrue((directory / 'result.json').exists())
            self.assertTrue(path.is_relative_to(directory / 'snapshot-operations'))
            self.assertTrue(path.parent.is_dir())
            self.assertFalse((directory / 'snapshot').exists())
            originals['result'] = (directory / 'result.json').read_bytes()
            self.assertEqual('requested', self.client_command('cancel', request['attempt_id'])['result']['state'])
            cancelled()
            self.fail('snapshot index ignored cancellation')

        with patch('workbench_axiom.cli.invoke', return_value=(self.response(request), 1)) as native, \
                patch.object(check_snapshot_index, 'build', side_effect=cancel_index) as index:
            result = self.client_command('execute', request['attempt_id'], '--confirm', request['id'])['result']
        native.assert_called_once()
        index.assert_called_once()
        self.assertEqual('completed', result['state'])
        self.assertEqual('native-failed', result['native_outcome'])
        self.assertEqual('incomplete', result['coverage'])
        self.assertEqual('cancelled-before-snapshot-publication', result['detail_state'])
        self.assertIsNone(result['snapshot_id'])
        self.assertEqual(originals['result'], (directory / 'result.json').read_bytes())
        self.assertFalse((directory / 'snapshot/publication.json').exists())
        self.assertEqual('already-completed', self.client_command('cancel', request['attempt_id'])['result']['state'])
        with patch('workbench_axiom.cli.invoke', side_effect=AssertionError('recovery must not rerun native work')):
            check_snapshots.recover(directory)
            saved.publish_snapshot(self.state / 'developer-checks', request['attempt_id'], self.selection)
            reopened = self.client_command('show', request['attempt_id'])['result']
        self.assertEqual('native-failed', reopened['native_outcome'])
        self.assertEqual(originals['result'], (directory / 'result.json').read_bytes())

    def test_snapshot_rebuild_preserves_native_failure_without_invocation(self):
        request = self.prepare()
        response, _ = self.execute(request)
        directory = self.directory(request)
        pointer = storage.read_json(directory / 'snapshot/current.json')
        (directory / 'snapshot/indexes' / pointer['generation'] / 'query.sqlite3').unlink()
        before = (directory / 'snapshot/publication.json').read_bytes()
        root = directory.parents[2]
        with patch('workbench_axiom.cli.invoke', side_effect=AssertionError('index rebuilding cannot run native code')):
            saved.rebuild_snapshot(root, request['attempt_id'], self.selection)
        self.assertEqual(before, (directory / 'snapshot/publication.json').read_bytes())
        with saved.open_snapshot(root, request['attempt_id'], self.selection) as opened:
            self.assertEqual('native-failed', opened.manifest['native_outcome'])
            self.assertEqual(response['result']['findings'][0], opened.read_record('findings', 'item:0'))

    def test_snapshot_source_reconciles_finding_with_native_diagnostic_and_saved_bytes(self):
        request = self.prepare()
        self.execute(request)
        original = saved.snapshots.Snapshot.read_record
        def altered(opened, section, key):
            value = original(opened, section, key)
            if section == 'findings':
                value['location']['path'] = '../../unselected-source'
            return value
        with patch.object(saved.snapshots.Snapshot, 'read_record', altered), self.assertRaisesRegex(ValueError, 'source evidence changed'):
            self.command('source', request['attempt_id'], '--diagnostic', 'diagnostic-0')

    def test_snapshot_source_admission_location_keeps_the_original_message_and_anchor(self):
        request = self.prepare()
        native = self.response(request)
        native['result']['sourceAdmission'] = {'findings': [{
            'path': 'groovy/material/Developer.groovy', 'line': 2, 'column': 3,
            'code': 'fixture-source-refusal', 'message': '/result/sourceAdmission/findings/0 is literal message text'}]}
        response, _ = self.execute(request, native)
        finding = response['result']['findings'][0]
        with patch.object(saved, 'reopen', side_effect=AssertionError('no whole result parsing')):
            result = self.command('source', request['attempt_id'], '--diagnostic', finding['id'])['result']
        self.assertEqual(finding, result['source'])

    def test_snapshot_native_outcome_uses_scope_evidence_instead_of_cli_exit_code(self):
        request = self.prepare()
        native = self.response(request)
        native['status'] = 'rejected'
        native['result']['initialization'] = {'status': 'completed'}
        response, _ = self.execute(request, native)
        with saved.open_snapshot(self.directory(request).parents[2], request['attempt_id'], self.selection) as opened:
            self.assertEqual('completed', opened.manifest['native_outcome'])
            self.assertEqual(1, opened.publication['summary']['native_exit_code'])
        self.assertEqual('rejected', response['result']['native']['status'])

    def test_native_line_anchor_preserves_unicode_crlf_and_retained_source_after_edit(self):
        request = self.prepare()
        response, _ = self.execute(request)
        finding = response["result"]["findings"][0]
        self.assertEqual(finding["location"]["start"], {"line": 2, "column": 1})
        self.assertEqual(finding["location"]["byte_start"], len("// 🌍 developer source\r\n".encode()))
        self.assertEqual(finding["nativeLocation"]["column"], 8)
        self.assertEqual(finding["location_precision"], "native-line-anchor")
        old = self.source.read_text()
        self.source.write_text("// developer fixed this\n")
        self.assertFalse(self.command("show", request["attempt_id"])["presentation"]["source_current"])
        source = self.command("source", request["attempt_id"], "--diagnostic", finding["id"])["result"]
        self.assertEqual(source["text"].replace("\r\n", "\n"), old)
        self.assertTrue(source["read_only"])

    def test_unknown_path_and_out_of_range_line_never_create_guessed_links(self):
        for options in ({"path": "Developer.groovy"}, {"line": 5000}):
            request = self.prepare()
            response, _ = self.execute(request, self.response(request, **options))
            self.assertIsNone(response["result"]["findings"][0]["location"])

    def test_changed_source_or_intent_requires_new_preparation(self):
        request = self.prepare()
        (self.pack / "material-intent.json").write_text('{"observeMaterials":["supersymmetry:changed"]}')
        with self.assertRaisesRegex(ValueError, "changed"), patch("workbench_axiom.cli.invoke", side_effect=AssertionError("no stale run")):
            self.command("execute", request["attempt_id"], "--confirm", request["id"])
        self.assertFalse((self.directory(request) / "started.json").exists())

    def test_wrong_confirmation_and_repeated_execution_are_refused(self):
        request = self.prepare()
        with self.assertRaisesRegex(ValueError, "confirm"):
            self.command("execute", request["attempt_id"], "--confirm", "wrong")
        self.execute(request)
        with self.assertRaisesRegex(ValueError, "already started"):
            self.command("execute", request["attempt_id"], "--confirm", request["id"])

    def test_changed_saved_source_archive_intent_or_result_is_not_reopened(self):
        for path in ("program.zip", "intent.json", "source/authoring/groovy/material/Developer.groovy", "result.json"):
            request = self.prepare()
            self.execute(request)
            target = self.directory(request) / path
            target.write_bytes(b"{}")
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.command("show", request["attempt_id"])

    def test_ignored_files_inside_program_are_not_silently_dropped(self):
        (self.pack / ".gitignore").write_text("Hidden.groovy\n")
        (self.program / "material/Hidden.groovy").write_text("// would be omitted\n")
        with self.assertRaisesRegex(ValueError, "ignored files"):
            self.prepare()

    def test_saved_configuration_is_captured_and_recaptured_without_source_mutation(self):
        config = self.pack / "authoring/config"
        config.mkdir()
        settings = config / "supercritical.cfg"
        settings.write_bytes(b"B:disableAllMaterials=true\r\n")
        resource = config / "resource.bin"
        resource.write_bytes(b"\xff\x00native resource")
        before = capture_source_inputs(self.pack)
        first = self.prepare(intent=None)
        self.assertEqual(before, capture_source_inputs(self.pack))
        for file in (settings, resource):
            relative = file.relative_to(self.pack).as_posix()
            self.assertEqual(file.read_bytes(), (self.directory(first) / "source" / relative).read_bytes())
        settings.write_bytes(b"B:disableAllMaterials=false\n")
        resource.unlink()
        (config / "added.cfg").write_bytes(b"new saved setting")
        second = self.prepare("--baseline", first["attempt_id"], intent=None)
        self.assertNotEqual(first["program"]["sha256"], second["program"]["sha256"])
        self.assertEqual(first["program"], second["baseline"]["program"])
        self.assertIn("config/added.cfg", second["program"]["paths"])
        self.assertNotIn("config/resource.bin", second["program"]["paths"])
        self.assertEqual(second, self.command("show", second["attempt_id"])["result"])

    def test_config_change_after_prepare_requires_fresh_request_and_retained_tamper_refuses(self):
        settings = self.pack / "authoring/config/native.cfg"
        settings.parent.mkdir()
        settings.write_bytes(b"original")
        request = self.prepare(intent=None)
        settings.write_bytes(b"edited")
        with self.assertRaisesRegex(ValueError, "changed"), patch("workbench_axiom.cli.invoke") as invoke:
            self.command("execute", request["attempt_id"], "--confirm", request["id"])
        invoke.assert_not_called()
        self.assertFalse((self.directory(request) / "started.json").exists())
        self.assertFalse(self.command("show", request["attempt_id"])["presentation"]["source_current"])
        retained = self.directory(request) / "source/authoring/config/native.cfg"
        retained.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "bytes changed"):
            self.command("show", request["attempt_id"])

    def test_ignored_configuration_cannot_disappear_before_or_after_preparation(self):
        config = self.pack / "authoring/config"
        config.mkdir()
        (self.pack / ".gitignore").write_text("Hidden.cfg\n")
        request = self.prepare(intent=None)
        (config / "Hidden.cfg").write_text("actual native config not in Git capture\n")
        with self.assertRaisesRegex(ValueError, "ignored files"):
            self.prepare(intent=None)
        with self.assertRaisesRegex(ValueError, "ignored files"), patch("workbench_axiom.cli.invoke") as invoke:
            self.command("execute", request["attempt_id"], "--confirm", request["id"])
        invoke.assert_not_called()
        self.assertFalse(self.command("show", request["attempt_id"])["presentation"]["source_current"])

    def test_configuration_directory_cannot_be_a_symlink(self):
        actual = self.base / "outside-config"
        actual.mkdir()
        (actual / "native.cfg").write_bytes(b"outside selected input")
        (self.pack / "authoring/config").symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare(intent=None)

    def test_baseline_is_copied_self_contained_and_rerun_with_current_intent(self):
        before = self.prepare()
        self.source.write_text("// edited candidate\n")
        current = self.prepare("--baseline", before["attempt_id"])
        self.assertNotEqual(current["program"]["sha256"], current["baseline"]["program"]["sha256"])
        response = self.response(current)
        response["result"] = {"candidate": copy.deepcopy(response), "baseline": self.response(before)}
        response["status"] = "incomplete"
        (self.directory(before) / "program.zip").write_bytes(b"original attempt not needed to reopen the copied input")
        result, native = self.execute(current, response)
        self.assertEqual(result["result"]["state"], "completed")
        self.assertEqual(native.call_args.args[1].baseline_program, self.directory(current) / "baseline.zip")
        sides = {row["side"] for row in result["result"]["findings"]}
        self.assertEqual(sides, {"baseline", "candidate"})
        self.assertEqual(self.command("show", current["attempt_id"])["result"], result["result"])

    def test_cancellation_retains_incomplete_without_native_validity_claim(self):
        request = self.prepare()
        self.command("cancel", request["attempt_id"])
        def cancelled(operation, args, context, **capture):
            self.assertTrue(context.cancelled.is_set())
            raise ProcessError("native-tool invocation was cancelled before launch")
        result, _ = self.execute(request, effect=cancelled)
        self.assertEqual(result["result"]["state"], "incomplete")
        self.assertIsNone(result["result"]["native"])
        self.assertEqual(self.command("cancel", request["attempt_id"])["result"]["state"], "already-completed")

    def test_native_inventory_mismatch_is_retained_as_incomplete_not_source_error(self):
        request = self.prepare()
        response = self.response(request)
        response["result"]["sourceProgram"]["sha256"] = "wrong"
        result, _ = self.execute(request, response)
        self.assertEqual(result["result"]["state"], "incomplete")
        self.assertIsNone(result["result"]["native"])
        self.assertEqual(result["result"]["findings"], [])

    def test_unavailable_disabled_or_duplicate_module_cannot_start_check(self):
        for rows in ([], [InstalledModule("axiom", "workbench-axiom", "0.1.0", "disabled")],
                     [InstalledModule("axiom", "workbench-axiom", "0.1.0", "unavailable")]):
            with patch("workbench_core.modules.discover", return_value=rows), self.assertRaisesRegex(ValueError, "install and enable"):
                self.command("contexts")

    def test_started_without_result_is_not_labeled_live_or_retried(self):
        request = self.prepare()
        storage.write_json(self.directory(request) / "started.json", {"request_id": request["id"]})
        self.assertEqual(self.command("show", request["attempt_id"])["presentation"]["attempt_state"], "started-without-retained-result")
        with self.assertRaisesRegex(ValueError, "already started"):
            self.command("execute", request["attempt_id"], "--confirm", request["id"])

    def test_module_enablement_uses_core_state_not_work_session_storage(self):
        core_state = self.base / "core-setup-state"
        with patch("workbench_api.state_paths.default_runtime_state_root", return_value=core_state), \
                patch("workbench_core.module_cli.disabled_modules", return_value=("axiom",)) as disabled:
            with self.assertRaisesRegex(ValueError, "install and enable"):
                self.command("contexts")
            disabled.assert_called_once_with(core_state)

    def test_cli_links_completed_and_incomplete_results_to_existing_work_session(self):
        from workbench_shell.developer_context_cli import main
        def cli(*arguments):
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                code = main(["--state-root", str(self.state), *arguments], suite_root=ROOT)
            value = json.loads(stream.getvalue())
            self.assertEqual(code, 0, value)
            return value
        selected = cli("select", str(self.pack), "--pack-profile", "supersymmetry",
                       "--platform-profile", "cleanroom", "--variant", "cleanroom-provisional")
        for failure in (False, True):
            request = self.prepare()
            def invoke(*args, **capture):
                if failure:
                    raise ProcessError("fixture cancellation")
                return self.response(request), 1
            self.execute(request, effect=invoke)
            value = cli("run", selected["session_id"], "--", "checks", "materials", "show", request["attempt_id"])
            self.assertEqual(value["session_link"]["state"], "linked")
            self.assertEqual(value["result"]["state"], "incomplete" if failure else "completed")
            self.assertFalse(value["result"]["authority"]["validity_qualified"])

    def test_historical_read_during_check_preserves_both_session_links(self):
        from workbench_shell import developer_context_cli as cli
        from workbench_shell.work_session import WorkSessionStore

        def invoke(*arguments):
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                code = cli.main(["--state-root", str(self.state), *arguments], suite_root=ROOT)
            return code, json.loads(stream.getvalue())

        _, selected = invoke("select", str(self.pack), "--pack-profile", "supersymmetry",
                             "--platform-profile", "cleanroom", "--variant", "cleanroom-provisional")
        session = selected["session_id"]
        historical = self.prepare()
        self.execute(historical)
        current = self.prepare()
        original = cli.run_selected_action
        historical_view = None

        def concurrent_read(*arguments, **kwargs):
            nonlocal historical_view
            result = original(*arguments, **kwargs)
            historical_view = original(arguments[0], ["checks", "materials", "show", historical["attempt_id"]],
                                       **kwargs)
            store = WorkSessionStore(self.state)
            store.bind_owner_artifacts(
                session, expected_sequence=store.status(session)["latest_sequence"],
                frontend={"frontend_id": "historical-reader", "kind": "cli", "version": "test"},
                owner_record_refs=[historical_view["owner_record_ref"]],
                owner_reference_verifier=lambda row: verify_developer_owner_reference(row, self.selection, suite_root=ROOT),
            )
            return result

        with patch.object(cli, "run_selected_action", side_effect=concurrent_read) as action, \
                patch("workbench_axiom.cli.invoke", return_value=(self.response(current), 1)) as native:
            code, value = invoke("run", session, "--", "checks", "materials", "execute",
                                 current["attempt_id"], "--confirm", current["id"])
        self.assertEqual(0, code, value)
        self.assertEqual("linked", value["session_link"]["state"])
        action.assert_called_once()
        native.assert_called_once()
        refs = WorkSessionStore(self.state).status(session)["owner_record_refs"]
        self.assertEqual({historical_view["owner_record_ref"]["record_id"], value["owner_record_ref"]["record_id"]},
                         {row["record_id"] for row in refs})

    def test_artifact_retry_refuses_navigation_or_disguised_lifecycle_changes(self):
        from workbench_shell import developer_context_cli as cli
        from workbench_shell.work_session import WorkSessionStore

        request = self.prepare()
        self.execute(request)
        original = cli.run_selected_action
        for kind, changes in (
            ("reader-navigated", {}),
            ("owner-artifacts-retained", {"lifecycle": "attention"}),
            ("owner-artifacts-retained", {"closed": True}),
        ):
            with self.subTest(kind=kind, changes=changes):
                with contextlib.redirect_stdout(io.StringIO()) as stream:
                    self.assertEqual(0, cli.main(["--state-root", str(self.state), "select", str(self.pack),
                                                 "--pack-profile", "supersymmetry", "--platform-profile", "cleanroom",
                                                 "--variant", "cleanroom-provisional"], suite_root=ROOT))
                session = json.loads(stream.getvalue())["session_id"]
                store = WorkSessionStore(self.state)

                def changed(*arguments, **kwargs):
                    result = original(*arguments, **kwargs)
                    store.append(session, expected_sequence=store.status(session)["latest_sequence"],
                                 frontend={"frontend_id": "other-reader", "kind": "cli", "version": "test"},
                                 kind=kind, **changes)
                    return result

                with patch.object(cli, "run_selected_action", side_effect=changed) as action, \
                        contextlib.redirect_stdout(io.StringIO()) as stream:
                    code = cli.main(["--state-root", str(self.state), "run", session, "--", "checks", "materials",
                                     "show", request["attempt_id"]], suite_root=ROOT)
                value = json.loads(stream.getvalue())
                self.assertEqual(2, code)
                self.assertEqual("conflict", value["session_link"]["state"])
                self.assertIsNotNone(value["session_link"]["owner_record_ref"])
                self.assertEqual(1, store.status(session)["latest_sequence"])
                action.assert_called_once()

    def test_artifact_retry_is_bounded_when_readers_keep_winning(self):
        from workbench_shell import developer_context_cli as cli
        from workbench_shell.work_session import WorkSessionStore

        request = self.prepare()
        self.execute(request)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(0, cli.main(["--state-root", str(self.state), "select", str(self.pack),
                                         "--pack-profile", "supersymmetry", "--platform-profile", "cleanroom",
                                         "--variant", "cleanroom-provisional"], suite_root=ROOT))
        session = json.loads(stream.getvalue())["session_id"]
        original = WorkSessionStore.bind_owner_artifacts

        def racing(store, selector, **arguments):
            original(store, selector, **{**arguments, "expected_sequence": store.status(selector)["latest_sequence"]})
            return original(store, selector, **arguments)

        with patch.object(WorkSessionStore, "bind_owner_artifacts", autospec=True, side_effect=racing) as links, \
                contextlib.redirect_stdout(io.StringIO()) as stream:
            code = cli.main(["--state-root", str(self.state), "run", session, "--", "checks", "materials",
                             "show", request["attempt_id"]], suite_root=ROOT)
        value = json.loads(stream.getvalue())
        self.assertEqual(2, code)
        self.assertEqual("conflict", value["session_link"]["state"])
        self.assertEqual(3, links.call_count)
        self.assertEqual(3, WorkSessionStore(self.state).status(session)["latest_sequence"])


class PackNativePreparationPolicyTests(unittest.TestCase):
    def test_context_selection_has_no_implicit_pack_expansion(self):
        self.assertIsNone(policy.preparation_inputs("supersymmetry:material-authoring-gt-base"))
        with self.assertRaisesRegex(ValueError, "Unknown SERVER"):
            policy.preparation_inputs("unknown")
        selected = policy.preparation_inputs("supersymmetry:material-authoring-pack")
        self.assertEqual(87, len(selected["artifacts"]))
        self.assertEqual(370471057, sum(row["size"] for row in selected["artifacts"]))
        self.assertEqual(87, len({row["path"] for row in selected["artifacts"]}))
        universal_tweaks = next(row for row in selected["artifacts"]
                                if row["path"] == "mods/UniversalTweaks-1.12.2-1.20.1.jar")
        self.assertEqual("fcc64063edb1fa248f0ad691d407a15409eccd9243fe91be78d66d36eb2e7030",
                         universal_tweaks["sha256"])
        self.assertTrue(all(row["path"].startswith("mods/") for row in selected["artifacts"]))

    def test_stale_context_or_omitted_artifact_cannot_prepare(self):
        original = policy.files(policy.__package__)
        raw = original.joinpath("axiom-native-inputs.json").read_bytes()
        native_raw = original.joinpath("axiom-native-early-context.json").read_bytes()
        catalog = policy.material_contexts()
        changes = [lambda value: value.update(nativeContextSha256="0" * 64),
                   lambda value: value["artifacts"].pop(),
                   lambda value: value["packSource"].update(revision="0" * 40)]
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            (resources / "axiom-native-early-context.json").write_bytes(native_raw)
            for change in changes:
                value = json.loads(raw); change(value)
                (resources / "axiom-native-inputs.json").write_text(json.dumps(value))
                with self.subTest(value=value), patch.object(policy, "files", return_value=resources), \
                        patch.object(policy, "material_contexts", return_value=catalog), \
                        self.assertRaisesRegex(ValueError, "differs from the selected original context"):
                    policy.preparation_inputs("supersymmetry:material-authoring-pack")
