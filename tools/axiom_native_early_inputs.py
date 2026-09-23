"""Bind saved programs and original profile artifacts for the existing early lane.

This helper prepares inputs only. Original Cleanroom discovery owns coremod
selection, configuration application and execution order.
"""
from hashlib import sha256, new as new_hash
import json
from pathlib import Path
import sys
import zipfile

from axiom_material_program_sources import selected_revisions
from axiom_native_identity_conformance import POLICY
from axiom_runtime import checked_path
from build_axiom_addon_inventory import manifest_inputs, selections
from build_axiom_target import git

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / 'api/src', ROOT / 'modules/axiom/src'):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_axiom.material_checks import archive_inventory, source_acknowledgement


def prepare_inputs(addon_inventory, pack, program_archive, context):
    """Return native-home files and expected input identities, without staging.

    Saved Groovy/configuration entries are returned as unchanged bytes; original
    artifact entries are verified Paths. ``coremodSources`` binds scanner-reported
    plugin names to native-home-relative filenames, not observed CodeSources.
    Additional profile execution policy remains the caller's responsibility.
    """
    addon_inventory, pack, program_archive = (
        Path(path).resolve(strict=True) for path in (addon_inventory, pack, program_archive))
    revision = selected_revisions()['supersymmetry']
    descriptors = context.get('artifactDescriptors')
    if (context.get('schema') != 'axiom.native-pack-early-context.v1'
            or context.get('id') != 'supersymmetry:required-early'
            or context.get('side') != 'server' or context.get('packRevision') != revision
            or not isinstance(descriptors, list) or not descriptors
            or any(not isinstance(path, str) or not path.startswith('mods/')
                   or not path.endswith('.pw.toml') for path in descriptors)
            or len(set(descriptors)) != len(descriptors)):
        raise ValueError('Explicit selected SERVER early context required')

    build_raw = (addon_inventory / 'program.json').read_bytes()
    build = json.loads(build_raw)
    policy_raw = POLICY.read_bytes()
    policy = json.loads(policy_raw)
    if (build.get('schema') != 'axiom.native-addon-inventory-build.v1'
            or build.get('candidateInputScope') != 'complete-artifacts'
            or build.get('packRevision') != revision or build.get('side') != context['side']
            or build.get('policySha256') != sha256(policy_raw).hexdigest()
            or build.get('images') != policy['images'] or build.get('libraries') != policy['libraries']):
        raise ValueError('Original complete addon inventory differs from the selected context')
    for path, digest in build['recipeInputs'].items():
        checked_path(ROOT, {'path': path, 'sha256': digest})
    inventory_jar = checked_path(addon_inventory, {
        'path': 'addon-inventory.jar', 'sha256': build['artifacts']['addon-inventory.jar']})
    with zipfile.ZipFile(inventory_jar) as archive:
        inventory = json.loads(archive.read('axiom-addon-inventory.json'))
    if (inventory.get('schema') != 'axiom.native-addon-inventory.v6'
            or inventory.get('candidateInputScope') != 'complete-artifacts'
            or inventory.get('candidateLayout') != 'original-flat-profile-mod-filenames'
            or inventory.get('candidateResourceScope') != 'original-entrypoints-metadata-manifests-native-server-language-and-api-packages'
            or inventory.get('selectedArtifactCoverage') != 'complete-declared-selection'
            or inventory.get('packRevision') != revision or inventory.get('side') != context['side']):
        raise ValueError('Native addon inventory scope differs')
    rows = {row['descriptor']: row for row in inventory['artifacts']}
    if len(rows) != len(inventory['artifacts']) or len(rows) != build['selectedArtifacts']:
        raise ValueError('Native addon inventory artifact coverage differs')

    originals = {path: git(pack, 'show', revision + ':' + path) for path in descriptors}
    options = {path: enabled for path, enabled in inventory['options'].items() if path in originals}
    selected = {row['descriptor']: row for row in selections(originals, context['side'], options)}
    files, artifacts, coremods = {}, [], {}
    for path in descriptors:
        selection = selected[path]
        row = rows.get(path)
        if (not selection['selected'] or row is None or row.get('selected') is not True
                or row.get('descriptorSha256') != selection['descriptorSha256']
                or inventory['descriptorInventory'].get(path) != selection['descriptorSha256']
                or row.get('outputPath') != selection['outputPath']):
            raise ValueError('Required artifact differs from its selected pack descriptor: ' + path)
        candidate = row['candidateInput']
        expected = {'path': 'native-addon-home/' + row['outputPath'],
                    'size': row['size'], 'sha256': row['sha256']}
        if candidate != expected or build['artifacts'].get(candidate['path']) != row['sha256']:
            raise ValueError('Required artifact is not the complete original JAR: ' + path)
        artifact = checked_path(addon_inventory, candidate)
        raw = artifact.read_bytes()
        download = selection['downloadHash']
        if (download['hash-format'] not in ('sha1', 'sha256', 'sha512')
                or new_hash(download['hash-format'], raw).hexdigest() != download['hash']):
            raise ValueError('Original JAR differs from the pinned pack download: ' + path)
        with zipfile.ZipFile(artifact) as archive:
            if manifest_inputs(archive) != row['manifestInputs']:
                raise ValueError('Original manifest inputs differ: ' + path)
        plugin = row['manifest'].get('FMLCorePlugin')
        if plugin:
            if plugin in coremods:
                raise ValueError('Coremod source binding is not unique: ' + plugin)
            coremods[plugin] = row['outputPath']
        files[row['outputPath']] = artifact
        artifacts.append({**{key: row[key] for key in (
            'descriptor', 'descriptorSha256', 'outputPath', 'size', 'sha256', 'manifestInputs', 'manifest')},
            'coremodPlugin': plugin})

    program = archive_inventory(program_archive)
    program_raw = program_archive.read_bytes()
    with zipfile.ZipFile(program_archive) as archive:
        for row in program['files']:
            raw = archive.read(row['path'])
            if len(raw) != row['size'] or sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('Saved program changed during input preparation')
            files[row['path']] = raw
    if program_archive.read_bytes() != program_raw:
        raise ValueError('Saved program archive changed during input preparation')
    if (addon_inventory / 'program.json').read_bytes() != build_raw:
        raise ValueError('Addon inventory changed during input preparation')
    return {
        'nativeHomeFiles': files,
        'artifacts': artifacts,
        'coremodSources': coremods,
        'sourceProgram': source_acknowledgement(program),
        'bindings': {
            'packRevision': revision,
            'contextSha256': sha256(json.dumps(context, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'addonInventoryBuildSha256': sha256(build_raw).hexdigest(),
            'addonInventorySha256': build['artifacts']['addon-inventory.jar'],
            'programArchive': {'size': len(program_raw), 'sha256': sha256(program_raw).hexdigest()},
            'descriptorInputs': {path: sha256(raw).hexdigest() for path, raw in originals.items()},
        },
    }
