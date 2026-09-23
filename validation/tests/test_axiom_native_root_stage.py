"""Root-stage evidence tests; no original native classes or worker processes run."""
from copy import deepcopy
from hashlib import sha1, sha256
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import axiom_native_root_stage as lane


TARGETS = ('net/minecraft/block/Block', 'net/minecraft/item/ItemStack',
           'net/minecraft/util/EnumFacing', 'net/minecraft/util/math/Vec3d',
           'net/minecraft/enchantment/Enchantment')
ROOTS = ('net.minecraftforge.fml.relauncher.FMLCorePlugin',
         'net.minecraftforge.classloading.FMLForgePlugin')
CONTAINERS = ['net.minecraftforge.fml.common.FMLContainer', 'net.minecraftforge.common.ForgeModContainer']
TWEAKS = ['net.minecraftforge.fml.common.launcher.FMLInjectionAndSortingTweaker', 'org.spongepowered.asm.launch.MixinTweaker']


class NativeRootStageTests(unittest.TestCase):
    def policy_inputs(self):
        raw = lane.LIBRARIES.read_bytes()
        return json.loads(lane.POLICY.read_bytes()), json.loads(raw), json.loads(lane.LOCK.read_bytes()), raw

    def body(self):
        source = '/fixture/lib/000-cleanroom-original.jar'
        return {'schema': 'axiom.result.v1', 'operation': 'native-root-stage',
                'scope': 'original-server-root-stage-not-material-initialization', 'status': 'incomplete',
                'diagnostics': [], 'result': {
                    'schema': 'axiom.native-root-stage.v1', 'rootStageReady': True,
                    'side': 'SERVER', 'inputStage': 'original-obfuscated-artifacts', 'stage': 'root-stage-ready',
                    'materialInitializationComplete': False, 'fullLauncherCompositionQualified': False,
                    'minecraftLaunched': False, 'candidateCompilationStarted': False, 'targetClassesDefined': False,
                    'nativeHomeInitialized': True, 'nativeOptionsAccepted': True, 'binaryPatches': 1165,
                    'patchInventoryMatched': True, 'injectedContainers': CONTAINERS.copy(), 'queuedTweaks': TWEAKS.copy(),
                    'artifactCodeSources': {'cleanroom': source, 'minecraft': '/fixture/lib/raw-server.jar'},
                    'rootCallbacks': [{'plugin': root, 'setupClass': 'net.minecraftforge.fml.common.asm.FMLSanityChecker' if i == 0 else 'none',
                                       'wrapperInjectionReturned': True, 'codeSource': source} for i, root in enumerate(ROOTS)],
                    'witnesses': {target: {'source': f'raw{i}', 'patchedSha256': sha256(f'patched{i}'.encode()).hexdigest(),
                                            'remappedSha256': sha256(f'mapped{i}'.encode()).hexdigest(),
                                            'accessSha256': sha256(f'access{i}'.encode()).hexdigest(), 'targetDefined': False}
                                  for i, target in enumerate(TARGETS)}}}

    def descriptor(self):
        result = self.body()['result']
        return {'binaryPatches': result['binaryPatches'], 'witnesses': deepcopy(result['witnesses'])}

    def test_selected_policy_retains_original_server_universal_and_library_identity(self):
        policy, libraries, lock, raw = self.policy_inputs()
        lane.verify_policy(policy, libraries, lock, raw)
        self.assertEqual('minecraft-server', policy['inputs'][1]['role'])
        self.assertNotEqual(libraries['inputs'][1]['sha256'], policy['inputs'][1]['sha256'])

    def test_policy_side_stage_resource_or_source_drift_refuses(self):
        policy, libraries, lock, raw = self.policy_inputs()
        for key, value in [('side', 'CLIENT'), ('inputStage', 'prepared-patched-image'),
                           ('cleanroomRevision', 'f' * 40), ('requiredResources', []),
                           ('libraryPolicySha256', '0' * 64), ('qualification', {'materialInitializationComplete': True})]:
            with self.subTest(field=key):
                bad = deepcopy(policy); bad[key] = value
                with self.assertRaises(ValueError): lane.verify_policy(bad, libraries, lock, raw)
        with self.assertRaises(ValueError): lane.verify_policy(policy, libraries, lock, raw + b'\n')

    def test_raw_universal_identity_and_well_formed_pins_are_required(self):
        policy, libraries, lock, raw = self.policy_inputs()
        for index, key, value in [(0, 'sha256', '0' * 64), (0, 'url', 'https://fixture.invalid/other.jar'),
                                  (1, 'sha256', 'x' * 64), (1, 'size', True), (1, 'path', '../server.jar')]:
            with self.subTest(index=index, field=key):
                bad = deepcopy(policy); bad['inputs'][index][key] = value
                with self.assertRaises(ValueError): lane.verify_policy(bad, libraries, lock, raw)

    def source_fixture(self):
        lock = json.loads(lane.LOCK.read_bytes()); payloads = {}
        roots = {'cleanroom': Path('/fixture/cleanroom'), 'foundation': Path('/fixture/foundation')}
        for row in lock['references']:
            raw = (row['repository'] + ':' + row['path'] + '\n').encode()
            payloads[row['repository'], row['path']] = raw
            row.update(size=len(raw), sha256=sha256(raw).hexdigest(),
                       gitBlob=sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())
        def git(repo, operation, obj):
            self.assertEqual('show', operation)
            owner, = [name for name, directory in roots.items() if directory == repo]
            revision, path = obj.split(':', 1)
            self.assertEqual(lock['revisions'][owner], revision)
            return payloads[owner, path]
        return lock, roots, git

    def test_source_reader_uses_selected_git_objects_with_byte_and_blob_witnesses(self):
        lock, roots, reader = self.source_fixture()
        with patch.object(lane, 'git', side_effect=reader) as git:
            result = lane.verify_sources(roots, lock)
        self.assertEqual(len(lock['references']), len(result))
        self.assertEqual(len(lock['references']), git.call_count)
        for _, operation, obj in (call.args for call in git.call_args_list):
            self.assertEqual('show', operation); self.assertNotIn('HEAD', obj)

    def test_source_hash_count_duplicate_and_revision_drift_refuse(self):
        lock, roots, reader = self.source_fixture()
        for key, value in [('size', -1), ('sha256', '0' * 64), ('gitBlob', '0' * 40)]:
            bad = deepcopy(lock); bad['references'][0][key] = value
            with self.subTest(field=key), patch.object(lane, 'git', side_effect=reader), self.assertRaises(ValueError):
                lane.verify_sources(roots, bad)
        for change in ('missing', 'duplicate', 'mutable'):
            bad = deepcopy(lock)
            if change == 'missing': bad['references'].pop()
            elif change == 'duplicate': bad['references'].append(deepcopy(bad['references'][0]))
            else: bad['revisions']['cleanroom'] = 'HEAD'
            with self.subTest(change=change), patch.object(lane, 'git', side_effect=reader), self.assertRaises(ValueError):
                lane.verify_sources(roots, bad)

    def test_supported_root_readiness_stays_incomplete_for_materials(self):
        self.assertEqual([], lane.check_result(self.body()))
        self.assertEqual([], lane.check_result(self.body(), self.descriptor()))

    def test_envelope_root_side_stage_and_failure_cannot_be_promoted(self):
        for key, value in [('schema', 'wrong'), ('operation', 'material-program'), ('status', 'accepted'), ('scope', 'full-launcher')]:
            bad = self.body(); bad[key] = value
            with self.subTest(envelope=key): self.assertTrue(lane.check_result(bad))
        for key, value in [('schema', 'wrong'), ('side', 'CLIENT'), ('inputStage', 'prepared-image'),
                           ('stage', 'native-root-registration'), ('failure', [{'class': 'java.lang.LinkageError'}]),
                           ('rootStageReady', 'true'), ('binaryPatches', 0), ('binaryPatches', True),
                           ('nativeOptionsAccepted', False), ('nativeHomeInitialized', False)]:
            bad = self.body(); bad['result'][key] = value
            with self.subTest(result=key): self.assertTrue(lane.check_result(bad))

    def test_each_deferred_capability_requires_an_explicit_false(self):
        for key in ('materialInitializationComplete', 'fullLauncherCompositionQualified', 'minecraftLaunched',
                    'candidateCompilationStarted', 'targetClassesDefined'):
            for value in (True, 'false', 0, None):
                bad = self.body(); bad['result'][key] = value
                with self.subTest(field=key, value=value): self.assertTrue(lane.check_result(bad))

    def test_counts_do_not_replace_exact_native_class_witnesses(self):
        bad = self.body(); bad['result']['witnesses'] = {str(i): {} for i in range(5)}
        self.assertTrue(lane.check_result(bad))
        for key, value in [('source', ''), ('patchedSha256', 'x' * 64), ('remappedSha256', ''),
                           ('accessSha256', '0'), ('targetDefined', True)]:
            bad = self.body(); bad['result']['witnesses'][TARGETS[0]][key] = value
            with self.subTest(field=key): self.assertTrue(lane.check_result(bad))
        bad = self.body(); bad['result']['witnesses'][TARGETS[0]] = {}
        self.assertTrue(lane.check_result(bad))

    def test_callbacks_need_original_order_hook_return_and_codesource(self):
        for key, value in [('plugin', 'fixture.Replacement'), ('setupClass', 'none'),
                           ('wrapperInjectionReturned', False), ('codeSource', '')]:
            bad = self.body(); bad['result']['rootCallbacks'][0][key] = value
            with self.subTest(field=key): self.assertTrue(lane.check_result(bad))
        bad = self.body(); bad['result']['rootCallbacks'].reverse()
        self.assertTrue(lane.check_result(bad))
        for field in ('injectedContainers', 'queuedTweaks'):
            bad = self.body(); bad['result'][field] = []
            with self.subTest(field=field): self.assertTrue(lane.check_result(bad))

    def test_well_formed_but_wrong_bytes_fail_against_retained_descriptor(self):
        for key in ('source', 'patchedSha256', 'remappedSha256', 'accessSha256'):
            descriptor = self.descriptor(); descriptor['witnesses'][TARGETS[0]][key] = 'different' if key == 'source' else '0' * 64
            with self.subTest(field=key): self.assertTrue(lane.check_result(self.body(), descriptor))
        descriptor = self.descriptor(); descriptor['binaryPatches'] += 1
        self.assertTrue(lane.check_result(self.body(), descriptor))

    def test_missing_or_malformed_native_result_is_not_a_success(self):
        for result in (None, [], {}, {'rootStageReady': True}):
            bad = self.body(); bad['result'] = result
            with self.subTest(result=result): self.assertTrue(lane.check_result(bad))


if __name__ == '__main__': unittest.main()
