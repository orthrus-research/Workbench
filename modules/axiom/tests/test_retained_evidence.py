"""Axiom admission preserves source/program/intent bindings without Shell imports."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from workbench_axiom.material_checks import program_snapshot
from workbench_axiom.retained_evidence import admit_retained_snapshot, _AUTHORITY


class Inputs:
    attempt_id = 'material-check-' + 'a' * 32
    context = {'owner': 'axiom', 'workspace_uri': 'file:///saved',
               'selection_id': 'selection', 'context_id': 'supersymmetry:material-authoring-pack'}

    @staticmethod
    def seal(kind, body):
        raw = json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return {**body, 'id': kind + ':sha256:' + sha256(raw).hexdigest()}

    def scope_identity(self, scope):
        return self.seal('check-snapshot-scope', scope)['id']

    def read_input(self, role):
        return self.roles[role]

    def source_files(self, directory, files):
        assert directory in {'source', 'baseline-source'}
        assert files == self.candidate['files']
        return dict(self.sources)


class RetainedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.inputs = Inputs()
        self.inputs.sources = {'groovy/runConfig.json': b'{}', 'groovy/postInit/A.groovy': b'// saved\n',
                               'config/actual.cfg': b'B:original=true\n'}
        files = [{'path': path, 'mode': 0o100644, 'size': len(raw), 'sha256': sha256(raw).hexdigest()}
                 for path, raw in sorted(self.inputs.sources.items())]
        self.inputs.candidate = self.inputs.seal('candidate', {'format': 'workbench-saved-candidate-v1',
            'source': {'root_uri': 'file:///saved'}, 'files': files})
        archive, program = program_snapshot(self.inputs.sources, '.')
        self.inputs.roles = {'intent': b'{}', 'saved-program': archive}
        self.request = self.inputs.seal('material-check-request', {
            'format': 'workbench-material-check-request-v1', 'state': 'prepared-not-run',
            'authority': _AUTHORITY, 'attempt_id': self.inputs.attempt_id,
            'workspace_uri': 'file:///saved', 'selection_id': 'selection',
            'candidate': self.inputs.candidate, 'program': program, 'program_root': '.',
            'intent_path': None, 'baseline': None,
            'inputs': {'context': {'id': self.inputs.context['context_id'], 'initializationStage': 'recipes'},
                       'engineManifestSha256': '1' * 64, 'runtimeManifestSha256': '2' * 64,
                       'toolchainFiles': [{'sha256': '3' * 64}], 'platformJvmPolicySha256': '4' * 64}})

    def reseal(self, value):
        return self.inputs.seal('material-check-request', {k: v for k, v in value.items() if k != 'id'})

    def test_full_source_admission_and_historical_scope(self):
        admission = admit_retained_snapshot(self.request, self.inputs)
        self.assertEqual(self.request['id'], admission.expected['request_id'])
        scope = {'name': 'axiom-retained-material-check-v1', 'sections': {
            'admission-findings': False, 'crafting-recipes': True, 'crafting-values': True,
            'diagnostics': True, 'findings': True, 'furnace': True, 'gt-recipes': True, 'report': True}}
        manifest = {'producer': {'id': 'axiom'}, 'scope_id': self.inputs.scope_identity(scope)}
        self.assertEqual(scope, admission.scope(manifest))
        self.assertIsNone(admission.scope({**manifest, 'scope_id': 'unknown-future-scope'}))
        self.assertEqual(20, len(admission.supported_schemas))

    def test_wrong_request_identity_and_authority_refuse(self):
        changed = deepcopy(self.request)
        changed['selection_id'] = 'other'
        for request in (changed, self.reseal(changed)):
            with self.assertRaises(ValueError):
                admit_retained_snapshot(request, self.inputs)
        changed = deepcopy(self.request)
        changed['authority']['validity_qualified'] = True
        with self.assertRaisesRegex(ValueError, 'authority'):
            admit_retained_snapshot(self.reseal(changed), self.inputs)

    def test_changed_program_archive_intent_and_candidate_refuse(self):
        for role in ('saved-program', 'intent'):
            with self.subTest(role=role):
                original = self.inputs.roles[role]
                self.inputs.roles[role] = original + b' '
                with self.assertRaises(ValueError):
                    admit_retained_snapshot(self.request, self.inputs)
                self.inputs.roles[role] = original
        changed = deepcopy(self.request)
        changed['candidate']['source']['root_uri'] = 'file:///other'
        with self.assertRaisesRegex(ValueError, 'candidate identity'):
            admit_retained_snapshot(self.reseal(changed), self.inputs)

    def test_paired_evidence_requires_original_baseline_archive(self):
        paired = deepcopy(self.request)
        paired['baseline'] = {key: paired[key] for key in ('candidate', 'program', 'program_root')}
        paired = self.reseal(paired)
        with self.assertRaises(KeyError):
            admit_retained_snapshot(paired, self.inputs)
        self.inputs.roles['saved-baseline-program'] = self.inputs.roles['saved-program']
        admission = admit_retained_snapshot(paired, self.inputs)
        self.assertEqual(paired['id'], admission.expected['request_id'])


if __name__ == '__main__':
    unittest.main()
