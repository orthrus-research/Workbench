"""Original artifact and complete saved-program custody; no native execution."""
from copy import deepcopy
from hashlib import sha1, sha256
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import axiom_native_early_inputs as inputs


def jar(entries):
    data = BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name, raw in entries:
            archive.writestr(name, raw)
    return data.getvalue()


class NativeEarlyInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addons = self.root / 'addons'
        self.addons.mkdir()
        self.pack = self.root / 'pack'
        self.pack.mkdir()
        self.revision = inputs.selected_revisions()['supersymmetry']
        self.originals, self.jars, rows = {}, {}, []
        for name, plugin in [('First', 'example.FirstPlugin'), ('Second', None), ('Unselected', 'other.Plugin')]:
            descriptor = 'mods/' + name.lower() + '.pw.toml'
            output = 'mods/' + name + '-1.0.jar'
            manifest = b'Manifest-Version: 1.0\r\nLong-Value: original\r\n continuation\r\n'
            if plugin:
                manifest += ('FMLCorePlugin: ' + plugin + '\r\n').encode()
            manifest += b'\r\nName: original/Entry.class\r\nSealed: true\r\n\r\n'
            raw = jar([('META-INF/MANIFEST.MF', manifest), ('original/Entry.class', b'original class'),
                       ('original/Other.class', b'complete non-entrypoint class'), ('META-INF/access.cfg', b'public original')])
            self.originals[descriptor] = (f'filename="{name}-1.0.jar"\nside="both"\n'
                '[download]\nhash-format="sha1"\nhash="' + sha1(raw).hexdigest() + '"\n').encode()
            pin = {'path': 'native-addon-home/' + output, 'size': len(raw), 'sha256': sha256(raw).hexdigest()}
            artifact = self.addons / pin['path']
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(raw)
            self.jars[descriptor] = raw
            rows.append({'descriptor': descriptor, 'descriptorSha256': sha256(self.originals[descriptor]).hexdigest(),
                         'outputPath': output, 'selected': True, 'size': len(raw), 'sha256': pin['sha256'],
                         'candidateInput': pin,
                         'manifest': {'Manifest-Version': '1.0', **({'FMLCorePlugin': plugin} if plugin else {})},
                         'manifestInputs': [{'entry': 'META-INF/MANIFEST.MF', 'size': len(manifest),
                                             'sha256': sha256(manifest).hexdigest()}]})
        self.inventory = {'schema': 'axiom.native-addon-inventory.v6', 'packRevision': self.revision,
            'side': 'server', 'candidateInputScope': 'complete-artifacts',
            'candidateLayout': 'original-flat-profile-mod-filenames',
            'candidateResourceScope': 'original-entrypoints-metadata-manifests-native-server-language-and-api-packages',
            'selectedArtifactCoverage': 'complete-declared-selection', 'options': {}, 'artifacts': rows,
            'descriptorInventory': {path: sha256(raw).hexdigest() for path, raw in self.originals.items()}}
        policy_raw = inputs.POLICY.read_bytes()
        policy = json.loads(policy_raw)
        self.build = {'schema': 'axiom.native-addon-inventory-build.v1', 'packRevision': self.revision,
            'side': 'server', 'candidateInputScope': 'complete-artifacts', 'selectedArtifacts': len(rows),
            'images': policy['images'], 'libraries': policy['libraries'],
            'policySha256': sha256(policy_raw).hexdigest(), 'recipeInputs': {},
            'artifacts': {row['candidateInput']['path']: row['sha256'] for row in rows}}
        self.write_inventory()
        self.context = {'schema': 'axiom.native-pack-early-context.v1', 'id': 'supersymmetry:required-early',
                        'side': 'server', 'packRevision': self.revision,
                        'artifactDescriptors': ['mods/second.pw.toml', 'mods/first.pw.toml']}
        self.sources = {'groovy/runConfig.json': b'{"loaders":{"postInit":["postInit/"],"preInit":["classes/","preInit/"]}}\r\n',
                        'groovy/classes/Shared.groovy': b'// complete shared source\r\n',
                        'groovy/preInit/Startup.groovy': b'// saved startup\n',
                        'groovy/postInit/Recipes.groovy': b'// retained later loader\n',
                        'config/forge_early.cfg': b'general {\n S:LOADING_PLUGIN_BLACKLIST < example.FirstPlugin >\n}\n',
                        'config/nested/resource.bin': b'\x00\xff\r\n',
                        'config/caf\u00e9.cfg': b'# exact saved bytes\r\n'}
        self.program = self.root / 'program.zip'
        self.program.write_bytes(jar(self.sources.items()))
        self.git_calls = []

    def write_inventory(self):
        raw = jar([('axiom-addon-inventory.json', json.dumps(self.inventory).encode())])
        (self.addons / 'addon-inventory.jar').write_bytes(raw)
        self.build['artifacts']['addon-inventory.jar'] = sha256(raw).hexdigest()
        (self.addons / 'program.json').write_text(json.dumps(self.build))

    def prepare(self, context=None):
        def original(pack, command, reference):
            self.git_calls.append((pack, command, reference))
            self.assertEqual(self.pack, pack)
            self.assertEqual('show', command)
            revision, path = reference.split(':', 1)
            self.assertEqual(self.revision, revision)
            return self.originals[path]
        with patch.object(inputs, 'git', side_effect=original):
            return inputs.prepare_inputs(self.addons, self.pack, self.program, context or self.context)

    def test_complete_saved_source_and_configuration_bytes_are_preserved(self):
        before = self.program.read_bytes()
        result = self.prepare()
        for path, raw in self.sources.items():
            self.assertEqual(raw, result['nativeHomeFiles'][path])
        inventory = [{'path': path, 'sha256': sha256(raw).hexdigest(), 'size': len(raw)}
                     for path, raw in sorted(self.sources.items())]
        digest = sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest, result['sourceProgram']['sha256'])
        self.assertEqual(len(self.sources), result['sourceProgram']['fileCount'])
        self.assertEqual('all-submitted-groovy-and-config-files', result['sourceProgram']['scope'])
        self.assertEqual({'size': len(before), 'sha256': sha256(before).hexdigest()}, result['bindings']['programArchive'])
        self.assertEqual(before, self.program.read_bytes())

    def test_exact_profile_artifacts_retain_original_names_complete_bytes_and_manifest(self):
        result = self.prepare()
        self.assertEqual(self.context['artifactDescriptors'], [row['descriptor'] for row in result['artifacts']])
        self.assertEqual({'example.FirstPlugin': 'mods/First-1.0.jar'}, result['coremodSources'])
        self.assertEqual(set(self.sources) | {'mods/First-1.0.jar', 'mods/Second-1.0.jar'}, set(result['nativeHomeFiles']))
        for row in result['artifacts']:
            artifact = result['nativeHomeFiles'][row['outputPath']]
            self.assertIsInstance(artifact, Path)
            self.assertEqual(self.jars[row['descriptor']], artifact.read_bytes())
            with zipfile.ZipFile(artifact) as archive:
                self.assertEqual(b'complete non-entrypoint class', archive.read('original/Other.class'))
                self.assertEqual(row['manifestInputs'], inputs.manifest_inputs(archive))
        self.assertIsNone(result['artifacts'][0]['coremodPlugin'])
        self.assertEqual(2, len(self.git_calls))

    def test_changed_saved_configuration_changes_custody_without_interpreting_blacklist(self):
        before = self.prepare()
        self.sources['config/forge_early.cfg'] = b'general {\n S:LOADING_PLUGIN_BLACKLIST < >\n}\n'
        self.program.write_bytes(jar(self.sources.items()))
        after = self.prepare()
        self.assertNotEqual(before['sourceProgram']['sha256'], after['sourceProgram']['sha256'])
        self.assertEqual(before['coremodSources'], after['coremodSources'])
        self.assertNotIn('loaded', after)

    def test_projection_and_context_revision_or_side_mismatch_are_refused(self):
        for key, value in [('side', 'client'), ('packRevision', '0' * 40), ('artifactDescriptors', ['mods/first.pw.toml'] * 2)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare({**self.context, key: value})
        self.build['candidateInputScope'] = 'entrypoints-and-metadata'
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'complete addon inventory'):
            self.prepare()

    def test_original_download_pin_still_binds_when_local_inventory_hashes_are_updated(self):
        row = self.inventory['artifacts'][0]
        raw = jar([('META-INF/MANIFEST.MF', b'Manifest-Version: 1.0\r\n\r\n'), ('changed.class', b'changed')])
        artifact = self.addons / row['candidateInput']['path']
        artifact.write_bytes(raw)
        row.update(size=len(raw), sha256=sha256(raw).hexdigest())
        row['candidateInput'].update(size=row['size'], sha256=row['sha256'])
        self.build['artifacts'][row['candidateInput']['path']] = row['sha256']
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'pinned pack download'):
            self.prepare()

    def test_missing_required_artifact_is_reported(self):
        self.inventory['artifacts'].pop(0)
        self.build['selectedArtifacts'] -= 1
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'selected pack descriptor'):
            self.prepare()

    def test_original_manifest_input_identity_is_checked(self):
        self.inventory['artifacts'][0]['manifestInputs'][0]['sha256'] = '0' * 64
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'manifest inputs differ'):
            self.prepare()

    def test_additional_profile_execution_policy_is_retained_only_as_input_identity(self):
        context = deepcopy(self.context)
        context.update(coremods=['profile callback trace'], transformers=['profile ordering'], programContext={'loader': 'preInit'})
        before, after = self.prepare(), self.prepare(context)
        self.assertEqual(before['nativeHomeFiles'], after['nativeHomeFiles'])
        self.assertEqual(before['coremodSources'], after['coremodSources'])
        self.assertNotEqual(before['bindings']['contextSha256'], after['bindings']['contextSha256'])


if __name__ == '__main__':
    unittest.main()
