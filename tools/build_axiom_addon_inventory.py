#!/usr/bin/env python3
"""Bind complete pack-selected artifact declarations to original Cleanroom parsing.

An existing artifact bundle is only a byte source here: every selected artifact
is rechecked against the pinned pack descriptor. This does not assert activation.
"""
import argparse
from io import BytesIO
from hashlib import sha256, new as new_hash, file_digest
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import zipfile

from axiom_material_program_sources import selected_revisions
from axiom_native_identity_conformance import ordinary, POLICY
from axiom_runtime import checked_path, verify_runtime
from build_axiom_native_materials import compile_sources, jar_bytes
from build_axiom_target import git

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / 'modules/axiom/jvm/src/materialProgramTooling/java/NativeAddonInventory.java'
RESOURCE = 'axiom-addon-inventory.json'


def selections(descriptors, side, options=None):
    if side not in ('client', 'server'):
        raise ValueError('explicit physical side required')
    if not 1 <= len(descriptors) <= 2000:
        raise ValueError('bounded complete descriptor inventory required')
    options = options or {}
    result, outputs, optional = [], set(), set()
    for path, raw in sorted(descriptors.items()):
        data = tomllib.loads(raw.decode())
        filename = data['filename']
        if (not filename or Path(filename).name != filename or any(c in filename for c in '\\:')
                or any(ord(c) < 32 or ord(c) == 127 for c in filename)
                or not filename.endswith('.jar')):
            raise ValueError('ordinary JAR filename required: ' + path)
        output = 'mods/' + filename
        if output in outputs:
            raise ValueError('duplicate artifact output: ' + output)
        outputs.add(output)
        option = data.get('option', {})
        if any(type(option.get(key, False)) is not bool for key in ('optional', 'default')):
            raise ValueError('optional/default selections must be boolean')
        if option.get('optional', False):
            optional.add(path)
        enabled = not option.get('optional', False) or options.get(path, option.get('default', False))
        selected_side = data.get('side') or 'both'
        if selected_side not in ('both', 'server', 'client'):
            raise ValueError('unknown artifact side: ' + path)
        result.append({'descriptor': path, 'descriptorSha256': sha256(raw).hexdigest(),
                       'outputPath': output, 'selected': bool(enabled and selected_side in ('both', side)),
                       'downloadHash': data['download']})
    if set(options) - optional or any(type(value) is not bool for value in options.values()):
        raise ValueError('options must name optional descriptors with boolean selections')
    return result


def verified_artifact(archive, row, record):
    if record.get('outputPath') != row['outputPath'] or record.get('metadataPath') != row['descriptor']:
        raise ValueError('artifact record differs from selected descriptor')
    digest = record.get('sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('invalid artifact digest')
    entry = archive.getinfo('blobs/' + digest)
    if not 0 < entry.file_size <= 256 << 20 or entry.file_size != record.get('size'):
        raise ValueError('artifact size differs or exceeds bound')
    raw = archive.read(entry)
    declared = row['downloadHash']
    algorithm = declared['hash-format']
    if algorithm not in ('sha1', 'sha256', 'sha512'):
        raise ValueError('unsupported artifact hash')
    if sha256(raw).hexdigest() != digest or new_hash(algorithm, raw).hexdigest() != declared['hash']:
        raise ValueError('artifact bytes differ from pinned descriptor: ' + row['descriptor'])
    return raw


def bundle_digest(path):
    if not 0 < path.stat().st_size <= 2 << 30:
        raise ValueError('artifact bundle exceeds bound')
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


import sys

_assembly_root = Path(__file__).resolve().parents[1]
for _assembly_source in (_assembly_root / 'api/src', _assembly_root / 'modules/axiom/src'):
    if str(_assembly_source) not in sys.path:
        sys.path.insert(0, str(_assembly_source))
from workbench_axiom.native_assembly import manifest_inputs


def candidate_projection(archive, declarations, api_declarations=(), api_package_witnesses=None):
    """Original entrypoints/metadata in original JAR order, never substitute bytes.

    This projects candidate discovery only, not the complete ASM subscriber table.
    Full-artifact native comparisons are required before using its observations.
    """
    selected = {row['entry'] for row in declarations} | {'mcmod.info', 'version.properties'}
    selected.update(row['entry'] for row in manifest_inputs(archive))
    witnesses = [*api_declarations, *(api_package_witnesses or {}).values()]
    for row in witnesses:
        if sha256(archive.read(row['entry'])).hexdigest() != row['sha256']:
            raise ValueError('native API class/package witness identity differs')
        selected.add(row['entry'])
    # Original FMLServerHandler.addModAsResource is part of registerBus.
    # Keep both native spellings; the native reader owns fallback and parsing.
    for row in declarations:
        modid = row.get('annotation', {}).get('modid')
        if modid:
            selected.update('assets/'+modid.lower()+'/lang/'+name for name in ('en_us.lang', 'en_US.lang'))
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as target:
        for entry in archive.infolist():
            if entry.filename not in selected or entry.is_dir():
                continue
            if entry.file_size > 4 << 20:
                raise ValueError('candidate metadata/class exceeds bound')
            info = zipfile.ZipInfo(entry.filename, (1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            target.writestr(info, archive.read(entry))
    return output.getvalue()


def build(java, images, libraries, pack, artifacts, output, side='server', options=None,
          candidate_inputs='entrypoints-and-metadata'):
    if candidate_inputs not in ('entrypoints-and-metadata', 'complete-artifacts'):
        raise ValueError('explicit native candidate input scope required')
    java, images, libraries, pack, artifacts = [ordinary(p).resolve(strict=True) for p in (java, images, libraries, pack, artifacts)]
    output = ordinary(output)
    if output.exists():
        raise ValueError('addon inventory output must be new')
    runtime = verify_runtime(java, compiler=True)
    revision = selected_revisions()['supersymmetry']
    paths = git(pack, 'ls-tree', '-r', '--name-only', revision, '--', 'mods').decode().splitlines()
    descriptors = {p: git(pack, 'show', revision + ':' + p) for p in paths if p.endswith('.pw.toml')}
    if not descriptors:
        raise ValueError('complete selected pack descriptors required')
    rows = selections(descriptors, side, options)
    policy_raw = POLICY.read_bytes(); policy = json.loads(policy_raw)
    dependencies = [checked_path(images, row) for row in policy['images']]
    dependencies += [checked_path(libraries, row) for row in policy['libraries']]
    frozen = {p: p.read_bytes() for p in (Path(__file__), SCANNER, POLICY,
        ROOT/'profiles/platforms/cleanroom/jvm-runtime.json', ROOT/'modules/axiom/sources/supersymmetry.lock.json',
        *(ROOT/'tools'/name for name in ('build_axiom_native_materials.py', 'axiom_material_program_sources.py',
          'axiom_native_identity_conformance.py', 'axiom_runtime.py', 'build_axiom_target.py')))}
    before = bundle_digest(artifacts)
    with tempfile.TemporaryDirectory(prefix='axiom-addon-inventory-') as temporary:
        work = Path(temporary); inputs = work/'inputs'; inputs.mkdir()
        sources, evidence, candidate_files, total = {}, {}, {}, 0
        projections = work/'candidate-inputs'; projections.mkdir()
        with zipfile.ZipFile(artifacts) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError('duplicate artifact bundle entries')
            manifest = json.loads(archive.read('manifest.json'))
            records = {r['metadataPath']: r for r in manifest['artifacts']}
            if len(records) != len(manifest['artifacts']):
                raise ValueError('duplicate artifact declarations')
            for index, row in enumerate(rows):
                if not row['selected']:
                    continue
                if row['descriptor'] not in records:
                    raise ValueError('missing selected artifact: ' + row['descriptor'])
                raw = verified_artifact(archive, row, records[row['descriptor']]); total += len(raw)
                if total > 2 << 30:
                    raise ValueError('complete artifact bytes exceed bound')
                name = str(index).zfill(4) + '.jar'
                (inputs/name).write_bytes(raw)
                evidence[name] = {k: v for k, v in row.items() if k != 'downloadHash'}
                evidence[name].update(sha256=sha256(raw).hexdigest(), size=len(raw))
        classes = compile_sources(java, {SCANNER.stem: frozen[SCANNER].decode()}, dependencies, work/'compile')
        jar_bytes(work/'scanner.jar', classes)
        env = {k: v for k, v in os.environ.items() if k not in {'JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'CLASSPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'}}
        subprocess.run([str(java/'bin/java'), '-cp', os.pathsep.join(map(str, [work/'scanner.jar', *dependencies])),
                        'research.orthrus.axiom.tooling.NativeAddonInventory', str(inputs), str(work/'scan.json')],
                       check=True, capture_output=True, env=env)
        scan = json.loads((work/'scan.json').read_bytes())
        scanned = [r['file'] for r in scan['artifacts']]
        if len(scanned) != len(set(scanned)) or set(scanned) != set(evidence):
            raise ValueError('native scan omitted/duplicated selected artifacts')
        # ModAPIManager looks up only its annotated API packages. Retain actual
        # original class witnesses for each such package in EVERY selected JAR,
        # including embedded copies with no package annotation of their own.
        api_packages = sorted({d['class'].split('.package-info',1)[0]
                               for row in scan['artifacts'] for d in row['apiDeclarations']
                               if '.package-info' in d['class']})
        for row in scan['artifacts']:
            filename = row.pop('file')
            row.update(evidence[filename])
            packages = row.pop('packageEntries')
            row['apiPackageWitnesses'] = {p: packages[p] for p in api_packages if p in packages}
            with zipfile.ZipFile(inputs/filename) as jar:
                row['manifestInputs'] = manifest_inputs(jar)
                # Preserve original entrypoint bytes as non-loadable resources.
                for declaration in row['declarations']:
                    raw = jar.read(declaration['entry'])
                    if sha256(raw).hexdigest() != declaration['sha256']:
                        raise ValueError('native class identity differs')
                    sources['axiom-addon-classes/' + declaration['sha256']] = raw
                if candidate_inputs == 'complete-artifacts':
                    candidate_hash = row['sha256']
                    candidate_path = 'native-addon-home/' + row['outputPath']
                    row['candidateInput'] = {'path': candidate_path, 'sha256': candidate_hash, 'size': row['size']}
                    candidate_files[candidate_path] = inputs/filename
                else:
                    candidate_raw = candidate_projection(jar, row['declarations'], row['apiDeclarations'], row['apiPackageWitnesses'])
                    candidate_hash = sha256(candidate_raw).hexdigest()
                    candidate_path = 'native-addon-home/' + row['outputPath']
                    row['candidateInput'] = {'path': candidate_path, 'sha256': candidate_hash, 'size': len(candidate_raw)}
                    projection = projections/(candidate_hash+'.jar')
                    if not projection.exists(): projection.write_bytes(candidate_raw)
                    candidate_files[candidate_path] = projection
        inventory = {**scan, 'schema': 'axiom.native-addon-inventory.v6', 'packRevision': revision, 'side': side, 'options': options or {},
                     'candidateInputScope': candidate_inputs,
                     'candidateResourceScope': 'original-entrypoints-metadata-manifests-native-server-language-and-api-packages',
                     'candidateLayout': 'original-flat-profile-mod-filenames',
                     'apiPackages': api_packages,
                     'descriptorInventory': {p: sha256(raw).hexdigest() for p, raw in descriptors.items()},
                     'excludedDescriptors': [r['descriptor'] for r in rows if not r['selected']],
                     'selectedArtifactCoverage': 'complete-declared-selection', 'activationQualified': False,
                     'scope': 'original-native-Mod-annotation-inventory; not Cleanroom discovery/activation equivalence'}
        sources[RESOURCE] = (json.dumps(inventory, sort_keys=True, separators=(',', ':'))+'\n').encode()
        jar_bytes(work/'addon-inventory.jar', sources)
        report = {'schema': 'axiom.native-addon-inventory-build.v1', 'packRevision': revision, 'side': side,
                  'candidateInputScope': candidate_inputs,
                  'artifactBundleSha256': before, 'selectedArtifacts': len(evidence), 'declaredArtifacts': len(rows),
                  'nativeModDeclarations': sum(len(r['declarations']) for r in scan['artifacts']),
                  'recipeInputs': {str(p.relative_to(ROOT)): sha256(raw).hexdigest() for p, raw in frozen.items()},
                  'images': policy['images'], 'libraries': policy['libraries'], 'policySha256': sha256(policy_raw).hexdigest(),
                  'runtimeInputs': runtime['runtimeFiles'] + runtime['compilerFiles'],
                  'artifacts': {'addon-inventory.jar': sha256((work/'addon-inventory.jar').read_bytes()).hexdigest(),
                                **{name: sha256(path.read_bytes()).hexdigest() for name, path in candidate_files.items()}},
                  'activationQualified': False, 'modCallbacksExecuted': False}
        if any(p.read_bytes() != raw for p, raw in frozen.items()) or bundle_digest(artifacts) != before:
            raise ValueError('addon inventory inputs changed during build')
        for base, key in ((images, 'images'), (libraries, 'libraries')):
            for row in policy[key]: checked_path(base, row)
        verify_runtime(java, compiler=True)
        output.mkdir(parents=True)
        shutil.copyfile(work/'addon-inventory.jar', output/'addon-inventory.jar')
        for name, path in candidate_files.items():
            (output/name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output/name)
        (output/'program.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'images', 'library-root', 'supersymmetry', 'artifacts', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--side', choices=('client', 'server'), default='server')
    parser.add_argument('--candidate-inputs', choices=('entrypoints-and-metadata', 'complete-artifacts'),
                        default='entrypoints-and-metadata', help='Complete artifacts provide the native differential reference')
    args = parser.parse_args()
    result = build(args.java_home, args.images, args.library_root, args.supersymmetry, args.artifacts, args.output,
                   args.side, candidate_inputs=args.candidate_inputs)
    print(json.dumps({k: result[k] for k in ('selectedArtifacts', 'nativeModDeclarations', 'activationQualified')}))


if __name__ == '__main__': main()
