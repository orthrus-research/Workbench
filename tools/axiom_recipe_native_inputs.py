"""Pack-pinned original class inputs for native recipe construction.

These are local input artifacts, not redistributed mod jars or admission to execute
all their APIs. Keep class bytes unchanged and retain exact per-class provenance.
Compiled, source-bounded material/language owners retain explicit precedence.
"""
from hashlib import sha1, sha256
from io import BytesIO
import json
from pathlib import Path
import tomllib
import zipfile

from axiom_material_program_sources import selected_revisions
from build_axiom_target import git

ARTIFACTS = {
    'gregtech-ce-unofficial': ('gregtech/',),
    # Original bundled mapper/completion data types are linked by GT's full
    # onCompatLoaded callback. Supplying them does not start a language server.
    'groovyscript': ('com/cleanroommc/groovyscript/', 'org/eclipse/lsp4j/',
                    'org/eclipse/xtend/', 'org/eclipse/xtend2/', 'org/eclipse/xtext/'),
    'codechicken-lib-1-8': ('codechicken/',),
    'modularui': ('com/cleanroommc/modularui/',),
    'susycore': ('supersymmetry/',),
    'sussypatches': ('dev/tianmi/sussypatches/',),
    'configanytime': ('com/cleanroommc/configanytime/',),
    'ivtoolkit': ('ivorius/ivtoolkit/',),
    'supercritical': ('supercritical/',),
    'gregicality-multiblocks': ('gregicality/',),
    'bubbles-a-baubles-fork': ('baubles/',),
    'gregtech-food-option': ('gregtechfoodoption/',),
}
RESOURCES = {'supercritical': ('mixins.supercritical.gregtech.json', 'mixins.supercritical.refmap.json'),
             'susycore': ('mixins.susy.gregtech.json', 'mixins.susy.gcym.json', 'mixins.susy.refmap.json')}


def pack_gcym_mixins(read):
    """Original Susy material-event transforms for the explicitly selected GCYM input.

    This bounded selection is not execution of SuSyLateMixinLoader or discovery
    of the whole installed mixin set. Neither callback body is rewritten.
    """
    path = 'mixins.susy.gcym.json'
    raw = read(path)
    config = json.loads(raw)
    if (config.get('package') != 'supersymmetry.mixins.gcym'
            or config.get('refmap') != 'mixins.susy.refmap.json'
            or config.get('mixins', []).count('GCYMEventHandlersMixin') != 1
            or any(key in config for key in ('plugin', 'client', 'server'))):
        raise ValueError('Unexpected selected Susy GCYM mixin configuration')
    selected = {**config, 'required': True, 'mixins': ['GCYMEventHandlersMixin']}
    mixin = 'supersymmetry/mixins/gcym/GCYMEventHandlersMixin.class'
    return (json.dumps(selected, indent=2) + '\n').encode(), {
        'source': path, 'sourceSha256': sha256(raw).hexdigest(),
        'refmap': config['refmap'], 'refmapSha256': sha256(read(config['refmap'])).hexdigest(),
        'selected': {mixin: sha256(read(mixin)).hexdigest()},
        'excluded': [name for name in config['mixins'] if name != 'GCYMEventHandlersMixin'],
        'selectionCondition': 'explicit-pack-hashed-gcym-input',
        'nativePluginDiscoveryExecuted': False, 'wholeInstalledMixinSet': False}


def pack_recipe_mixins(read):
    """Preserve the selected catalog's original recipe-build callback transform."""
    path = 'mixins.susy.gregtech.json'
    raw = read(path)
    config = json.loads(raw)
    if (config.get('package') != 'supersymmetry.mixins.gregtech'
            or config.get('refmap') != 'mixins.susy.refmap.json'
            or config.get('mixins', []).count('RecipeMapsMixin') != 1
            or 'plugin' in config or 'server' in config):
        raise ValueError('Unexpected selected Susy recipe catalog mixin configuration')
    selected = {**config, 'required': True, 'mixins': ['RecipeMapsMixin'], 'client': []}
    mixin = 'supersymmetry/mixins/gregtech/RecipeMapsMixin.class'
    return (json.dumps(selected, indent=2) + '\n').encode(), {
        'source': path, 'sourceSha256': sha256(raw).hexdigest(),
        'refmap': config['refmap'], 'refmapSha256': sha256(read(config['refmap'])).hexdigest(),
        'selected': {mixin: sha256(read(mixin)).hexdigest()},
        'excluded': [name for name in config['mixins'] if name != 'RecipeMapsMixin'],
        'excludedClient': config.get('client', []), 'wholeInstalledMixinSet': False}


def pack_material_mixins(read):
    """Select original material mixins, never rewrite their bytecode or bodies."""
    path = 'mixins.supercritical.gregtech.json'
    raw = read(path)
    config = json.loads(raw)
    if (config.get('package') != 'supercritical.mixins.gregtech'
            or config.get('refmap') != 'mixins.supercritical.refmap.json'
            or config.get('mixins', []).count('MixinElement') != 1
            or config.get('mixins', []).count('MixinOrePrefix') != 1
            or 'plugin' in config or 'client' in config or 'server' in config):
        raise ValueError('Unexpected selected Supercritical material mixin configuration')
    selected_names = ['MixinElement', 'MixinOrePrefix']
    selected = {**config, 'required': True, 'mixins': selected_names}
    mixins = ['supercritical/mixins/gregtech/' + name + '.class' for name in selected_names]
    return (json.dumps(selected, indent=2) + '\n').encode(), {
        'source': path, 'sourceSha256': sha256(raw).hexdigest(),
        'refmap': config['refmap'], 'refmapSha256': sha256(read(config['refmap'])).hexdigest(),
        'selected': {mixin: sha256(read(mixin)).hexdigest() for mixin in mixins},
        'excluded': [name for name in config['mixins'] if name not in selected_names],
        'wholeInstalledMixinSet': False}


def read_inputs(pack: Path, mods: Path):
    revision = selected_revisions()['supersymmetry']
    classes, origins, artifacts, optional, resources = {}, {}, {}, [], {}
    for key, prefixes in ARTIFACTS.items():
        descriptor = 'mods/' + key + '.pw.toml'
        raw = git(pack, 'show', revision + ':' + descriptor)
        metadata = tomllib.loads(raw.decode())
        filename = metadata['filename']
        if Path(filename).name != filename or metadata['download']['hash-format'] != 'sha1':
            raise ValueError('Unexpected selected pack artifact descriptor: ' + descriptor)
        path = mods / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError('Explicit regular pack artifact required: ' + str(path))
        data = path.read_bytes()
        if sha1(data).hexdigest() != metadata['download']['hash']:
            raise ValueError('Pack artifact hash differs: ' + filename)
        artifacts[filename] = {'sha256': sha256(data).hexdigest(), 'sha1': sha1(data).hexdigest(),
                               'descriptor': descriptor, 'descriptorSha256': sha256(raw).hexdigest()}
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for name in RESOURCES.get(key, ()):
                body = archive.read(name)
                classes[name] = body
                resources[name] = {'artifact': filename, 'sha256': sha256(body).hexdigest()}
            for name in sorted(archive.namelist()):
                if not name.endswith('.class') or not name.startswith(prefixes):
                    continue
                if name in classes:
                    raise ValueError('Native recipe input class collision: ' + name)
                body = archive.read(name)
                classes[name] = body
                origins[name] = {'artifact': filename, 'sha256': sha256(body).hexdigest()}
                if b'net/minecraftforge/fml/common/Optional$' in body:
                    optional.append(name)
    if 'gregtech/api/recipes/RecipeMap.class' not in classes:
        raise ValueError('Native RecipeMap absent')
    classes['axiom-native-recipe-optionals.txt'] = ('\n'.join(sorted(optional)) + '\n').encode()
    return classes, {'packRevision': revision, 'artifacts': artifacts, 'classes': origins,
                     'resources': resources,
                     'distribution': 'local-inputs-only', 'apiAdmission': False}
