#!/usr/bin/env python3
"""Native candidate/configured-state observations; no claim of loaded-mod parity."""
import argparse
from hashlib import sha256
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
import axiom_pack_gtfo_configuration_conformance as gtfo
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git

CONFIG = 'config/fmlModState.properties'


def check_forge(execution):
    failures = []
    value = execution.get('forgeInitialization', {})
    expected = {'status': 'returned', 'side': 'SERVER',
                'nativeHandler': 'net.minecraftforge.fml.server.FMLServerHandler',
                'serverStarted': False, 'activeOwnerAbsent': True, 'toolInitializationComplete': True,
                'usernameCacheEntries': 0, 'modActivationQualified': False,
                'oreDictionaryNames': 155,
                'harvestLevels': {'minecraft:obsidian': 3, 'minecraft:iron_ore': 1,
                                  'minecraft:diamond_ore': 2, 'minecraft:quartz_ore': 0}}
    if value != expected:
        failures.append('original Forge initialization state differs')
    steps = {r['id']: r for r in execution.get('initialization', {}).get('steps', [])}
    prefix = steps.get('forge-platform-initialization', {})
    addon = steps.get('addon-candidate-discovery', {})
    if (prefix.get('status') != 'returned' or not isinstance(prefix.get('sequence'), int)
            or not isinstance(addon.get('sequence'), int) or prefix['sequence'] >= addon['sequence']):
        failures.append('native Forge initialization did not precede addon inspection')
    return failures


def check_side(value):
    failures = []
    side = value.get('nativeSideEligibility', {})
    expected = {'method': 'original FMLModContainer.shouldLoadInEnvironment', 'side': 'SERVER',
                'scope': 'raw-Mod-annotations-not-filtered-discovery',
                'evaluated': 194, 'eligible': 170, 'rejected': 24}
    if any(side.get(k) != v for k, v in expected.items()):
        failures.append('original native side evaluation scope/counts differ')
    restricted = side.get('restrictedDeclarations', [])
    names = [r.get('class') for r in restricted]
    if len(names) != 25 or len(set(names)) != 25:
        failures.append('native side-restricted declarations absent or duplicated')
    server_class = 'com.sonicether.soundphysics.SoundPhysicsServer'
    client_class = 'com.sonicether.soundphysics.SoundPhysics'
    if server_class not in names or client_class not in names:
        failures.append('original SoundPhysics client/server distinction absent')
    for row in restricted:
        if (row.get('nativeSideEligible') is not (row.get('class') == server_class)
                or len(row.get('classSha256', '')) != 64):
            failures.append('native restricted-side decision/identity differs')
    for candidate in [*value.get('gtfoCandidates', []), *value.get('queries', {}).get('applecore', {}).get('candidates', [])]:
        if candidate.get('nativeSideEligible') is not True:
            failures.append('original GTFO/AppleCore native side eligibility absent')
    return failures


def check_activation(case, execution):
    failures = []
    discovery = execution.get('addonDiscovery', {})
    value = discovery.get('nativeCandidateActivation', {})
    expected = {'scope': 'discovered-jar-candidates-only-not-complete-pack-activation',
                'method': 'original LoadController transition(LOADING) and FMLLoadEvent dispatch',
                'loaderState': 'LOADING', 'hostLoaderStateRestored': True,
                'modInstancesConstructed': False, 'packActivationQualified': False}
    if any(value.get(k) != v for k, v in expected.items()): failures.append('native candidate bus scope/state differs')
    candidates = discovery.get('nativeCandidateDiscovery', {}).get('candidateOrder', [])
    disabled = set(case.get('disabled_candidates', []))
    if not case.get('apple_enabled', True): disabled.add('applecore')
    states = value.get('containerStates', {})
    if len(candidates) != 162 or states != {id: 'DISABLED' if id in disabled else 'LOADED' for id in candidates}:
        failures.append('original candidate controller states absent or differ')
    if value.get('presentCandidateQueries') != {id: id not in disabled for id in candidates}:
        failures.append('candidate-only original loaded queries differ')
    if value.get('activeCandidateOrder') != [id for id in candidates if id not in disabled]:
        failures.append('original active bus order differs')
    language = value.get('language', {})
    if (language.get('method') != 'original FMLServerHandler.addModAsResource -> LanguageMap.inject'
            or not isinstance(language.get('entries'), int) or language['entries'] <= 0
            or len(language.get('sha256', '')) != 64):
        failures.append('original native language injection evidence missing')
    order = value.get('dependencyOrder', {})
    if (order.get('status') != 'deferred' or order.get('versionRequirementsChecked') is not False
            or 'forge' not in order.get('requiredIdsOutsideCandidateSet', [])):
        failures.append('uncomposed sorting inputs concealed or order fabricated')
    steps = {r['id']: r for r in execution.get('initialization', {}).get('steps', [])}
    bus = steps.get('addon-candidate-bus', {}); scan = steps.get('addon-candidate-discovery', {})
    if (bus.get('status') != 'returned' or not isinstance(bus.get('elapsedNanos'), int)
            or bus.get('sequence', 0) <= scan.get('sequence', 0)):
        failures.append('native candidate bus trace absent or out of order')
    return failures


def check_apis(execution):
    failures=[]
    value=execution.get('addonDiscovery',{}).get('nativeApiProviders',{})
    expected={'scope':'discovered-jar-candidates-only-not-complete-pack-api-composition',
              'method':'original ModAPIManager.registerDataTableAndParseAPI',
              'declarationCount':79,'providerCount':79,'hostApiStateRestored':True,
              'candidateTransformerInstalled':False,'packApiCompositionQualified':False}
    if any(value.get(k)!=v for k,v in expected.items()):failures.append('native API provider scope/count/state differs')
    providers=value.get('providers',{});membership=value.get('packageMembership',{})
    if len(providers)!=79 or len(membership)!=79:failures.append('native API identities/package membership absent')
    for row in providers.values():
        packages=row.get('packages',[]);source=row.get('sourceArtifactSha256','')
        if (len(source)!=64 or not packages or not row.get('owner') or not row.get('version')
                or any(source not in membership.get(p,{}) for p in packages)):
            failures.append('native API source/package custody differs')
    for id,owner,version,package,embedded,before in (
            ('AppleCoreAPI','applecore','3.4.0','squeek.applecore.api',{'applecore'},[]),
            ('ComputerCraft|API','ComputerCraft','1.89.2','dan200.computercraft.api',{'cctweaked','computercraft','opencomputers'},['cctweaked','computercraft','opencomputers']),
            ('ctm-api','ctm','0.1.0','team.chisel.ctm.api',{'gregicprobe'},['gregicprobe'])):
        row=providers.get(id,{})
        actual={id for ids in membership.get(package,{}).values() for id in ids}
        if (row.get('owner')!=owner or row.get('version')!=version or row.get('packages')!=[package] or actual!=embedded
                or row.get('after')!=[owner] or row.get('before')!=before):
            failures.append('native API owner/version/embedded membership differs: '+id)
    ctm=providers.get('ctm-api',{})
    if membership.get('team.chisel.ctm.api',{}).get(ctm.get('sourceArtifactSha256'))!=[]:
        failures.append('native API from a JAR with no side-eligible mod was lost')
    steps={r['id']:r for r in execution.get('initialization',{}).get('steps',[])}
    api=steps.get('addon-api-providers',{});bus=steps.get('addon-candidate-bus',{})
    if (api.get('status')!='returned' or not isinstance(api.get('elapsedNanos'),int)
            or not steps.get('addon-candidate-discovery',{}).get('sequence',0)<api.get('sequence',0)<bus.get('sequence',0)):
        failures.append('native API discovery order/trace differs')
    return failures


CONTAINED_DEPENDENCY_CARRIERS = {
    'mods/ForgeMultipart-1.12.2-2.6.2.83-universal.jar', 'mods/gaspunk-1.12.2-1.4.8.jar',
    'mods/incontrol-1.12-3.9.18.jar', 'mods/Scalar Legacy-1.0.1.jar', 'mods/shetiphiancore-1.12.0-3.5.9.jar',
}


def check_libraries(execution):
    failures = []
    value = execution.get('addonDiscovery', {}).get('nativeLibraryCandidates', {})
    expected = {'method': 'original LibraryManager.getCandidates',
                'scope': 'flat-profile-artifact-directory-only-not-complete-Cleanroom-selection',
                'originalFilenames': True, 'launcherArgumentsRestored': True,
                'hostLibraryStateRestored': True, 'nativeCacheIdentityObserved': True,
                'containedDependencyExtractionExecuted': False,
                'externalModListsComposed': False, 'classpathCandidatesComposed': False,
                'coremodFilteringApplied': False, 'completeSelectionQualified': False}
    if any(value.get(k) != v for k, v in expected.items()):
        failures.append('native flat-library scope/state differs')
    manifest = value.get('manifestSelection', {})
    expected_manifest = {'reader': 'original java.util.jar.JarFile.getManifest', 'artifacts': 184,
                         'present': 184, 'rawBytesVerified': True, 'bansoukouPresent': False}
    embedded = manifest.get('containedDependencyCarriers', {})
    identity = manifest.get('identitySha256', '')
    if (any(manifest.get(k) != v for k, v in expected_manifest.items())
            or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
            or set(embedded) != CONTAINED_DEPENDENCY_CARRIERS
            or embedded.get('mods/gaspunk-1.12.2-1.4.8.jar') != 'Ladylib-2.1.jar'):
        failures.append('original raw-manifest selection evidence absent or differs')
    paths = value.get('artifactOrder', [])
    if (len(paths) != 184 or len(set(paths)) != 184
            or any(not p.startswith('mods/') or p.count('/') != 1 or not p.endswith('.jar') for p in paths)
            or paths != sorted(paths, key=lambda p: p.lower())):
        failures.append('native original-filename ordering absent or differs')
    steps = {r['id']: r for r in execution.get('initialization', {}).get('steps', [])}
    scan = steps.get('addon-candidate-discovery', {})
    libraries = steps.get('addon-library-candidates', {})
    api = steps.get('addon-api-providers', {})
    if (libraries.get('status') != 'returned' or not isinstance(libraries.get('elapsedNanos'), int)
            or not scan.get('sequence', 0) < libraries.get('sequence', 0) < api.get('sequence', 0)):
        failures.append('native library enumeration order/trace differs')
    return failures


def corpus(sussy, gtfo_config):
    base = gtfo.corpus(sussy, gtfo_config)[0]['files']
    result = []
    for name, states, apple_enabled, disabled in (
            ('no-state-file', None, True, []),
            ('applecore-disabled', b'applecore=false\n', False, []),
            ('applecore-enabled', b'applecore=TrUe\n', True, []),
            ('unknown-query-is-not-injected', b'gcys=true\nactuallyadditions=true\nnuclearcraft=true\n', True, []),
            ('native-invalid-boolean', b'applecore=not-a-boolean\n', False, []),
            ('native-properties-last-key', b'applecore=false\napplecore=true\n', True, []),
            ('gtfo-disabled', b'gregtechfoodoption=false\n', True, ['gregtechfoodoption']),
            ('both-candidates-disabled', b'applecore=false\ngregtechfoodoption=false\n', False, ['gregtechfoodoption']),
            ('restored-no-state-file', None, True, [])):
        files = dict(base)
        if states is not None: files[CONFIG] = states
        result.append({'name': name, 'files': files, 'apple_enabled': apple_enabled, 'disabled_candidates': disabled})
    return result


def check_discovery(case, execution):
    failures = check_forge(execution)
    value = execution.get('addonDiscovery', {})
    failures.extend(check_side(value))
    failures.extend(check_activation(case, execution))
    failures.extend(check_apis(execution))
    failures.extend(check_libraries(execution))
    if any(value.get(k) != expected for k, expected in {
            'status': 'native-candidates-observed', 'selectedArtifacts': 184, 'classFiles': 66104,
            'modDeclarations': 194, 'side': 'server', 'activationQualified': False,
            'loadedQueriesResolved': False, 'modClassesDefined': False, 'modCallbacksExecuted': False,
            'packRevision': selected_revisions()['supersymmetry']}.items()):
        failures.append('native candidate inventory identity/scope differs')
    queries = value.get('queries', {})
    for id in ('nuclearcraft', 'actuallyadditions', 'gcys'):
        if queries.get(id) != {'declarationStatus': 'no-Mod-annotation-in-selected-artifacts', 'candidates': [], 'loadedState': 'unresolved'}:
            failures.append('undeclared query was promoted to an absence/activation answer: ' + id)
    apple = queries.get('applecore', {})
    if (apple.get('declarationStatus') != 'declared' or apple.get('loadedState') != 'unresolved'
            or apple.get('configuredEnabled') is not case.get('apple_enabled', True)):
        failures.append('native AppleCore declaration/configured state differs')
    candidates = apple.get('candidates', [])
    if len(candidates) != 1 or candidates[0].get('class') != 'squeek.applecore.AppleCore':
        failures.append('original AppleCore native identity absent')
    gtfo_candidates = value.get('gtfoCandidates', [])
    if len(gtfo_candidates) != 1 or gtfo_candidates[0].get('annotation', {}).get('dependencies') != 'required-after:gregtech@[2.8.0-beta,);after:gcy_science;after:nutrition':
        failures.append('original GTFO sorting declaration differs')
    raw = case['files'].get(CONFIG)
    if value.get('modStateFilePresent') is not (raw is not None): failures.append('native mod state file presence differs')
    if raw is not None and value.get('modStateFileSha256') != sha256(raw).hexdigest(): failures.append('native mod state input hash differs')
    if raw is None and 'modStateFileSha256' in value: failures.append('missing state file hash invented')
    step = next((r for r in execution.get('initialization', {}).get('steps', []) if r['id'] == 'addon-candidate-discovery'), {})
    if step.get('status') != 'returned': failures.append('native candidate discovery step did not return')
    return failures


def check_result(case, response, exit_code):
    execution = response.get('result', {}).get('execution', {})
    return gtfo.check_result(case, response, exit_code) + check_discovery(case, execution)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    parser.add_argument('--reference-runtime-home', type=Path)
    args = parser.parse_args()
    saved = git(args.pack, 'show', selected_revisions()['supersymmetry']+':'+gtfo.CONFIG)
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=lambda sussy: corpus(sussy, saved), result_checker=check_result,
                      receipt_schema='axiom.pack-addon-candidate-observations.v1')
    if args.reference_runtime_home:
        from axiom_pack_candidate_metadata_conformance import compare
        reference = args.report.with_name(args.report.stem+'-full-reference.json')
        configuration.run(args.java_home, args.engine_home, args.reference_runtime_home, args.pack, reference, args.whole_pack,
                          case_selector=lambda sussy: corpus(sussy, saved), result_checker=check_result,
                          receipt_schema='axiom.pack-addon-candidate-observations.v1')
        compare(args.report, reference, args.report.with_name(args.report.stem+'-comparison.json'))


if __name__ == '__main__': main()
