#!/usr/bin/env python3
"""Fresh-worker Supercritical configuration/material witnesses, not pack parity."""
import argparse
from hashlib import sha256
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
from axiom_material_program_cases import cases, Edit, EDITS, PRODUCER, identity, source_acknowledgement, matches_source_acknowledgement
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git

CONFIG = 'config/supercritical.cfg'
CORIUM = 'supercritical:corium'
TARGET = 'supercritical:developer_material'
GAP = 'material-context.supercritical-construction-override-incomplete'
PROXY = 'supercritical.common.CommonProxy'
EVENTS = 'supercritical.common.SCEventHandlers'
MIXIN = 'supercritical.mixins.gregtech.MixinOrePrefix'
PREFIXES = ('fuelRod', 'fuelRodDepleted', 'fuelRodHotDepleted', 'fuelPelletRaw',
            'fuelPellet', 'fuelPelletDepleted', 'dustSpentFuel', 'dustBredFuel', 'dustFissionByproduct')
MODIFICATIONS = b'B:enableMaterialModifications=false'
MATERIALS = b'B:disableAllMaterials=true'
DIVISOR = b'D:fissionCoolantDivisor=35.96625'
ANCHOR = "log.infoMC('Registering the developer material program')"
DECLARATION = "new Material.Builder(31003, new ResourceLocation('supercritical', 'developer_material')).dust().color(0x123456).build()"


def corpus(config, supercritical):
    for anchor in (MODIFICATIONS, MATERIALS, DIVISOR):
        if supercritical.count(anchor) != 1:
            raise ValueError('Expected one exact selected Supercritical setting: ' + anchor.decode())
    result = []
    for name, raw, expected in (
            ('corium-only', supercritical, {}),
            ('saved-divisor', supercritical.replace(DIVISOR, b'D:fissionCoolantDivisor=42.5'), {'divisor': 42.5}),
            ('invalid-divisor', supercritical.replace(DIVISOR, b'D:fissionCoolantDivisor=bad'), {'divisor': 14.0}),
            ('nonfinite-divisor', supercritical.replace(DIVISOR, b'D:fissionCoolantDivisor=NaN'), {'divisor': 'NaN'}),
            ('optional-catalog', supercritical.replace(MATERIALS, b'B:disableAllMaterials=false'), {'catalog': True}),
            ('construction-required', supercritical.replace(MODIFICATIONS, b'B:enableMaterialModifications=true'), {'deferred': True}),
            ('native-default-setting', supercritical.replace(MODIFICATIONS, b''), {'deferred': True}),
            ('native-default-file', None, {'deferred': True, 'catalog': True, 'divisor': 14.0}),
            ('saved-corium-edit', supercritical, {'color': 0x123456}),
            ('saved-addition', supercritical, {'added': True}),
            ('native-setter-error', supercritical, {'error': True}),
            ('developer-correction', supercritical, {})):
        files = dict(cases()[0]['files'])
        if 'color' in expected:
            files = Edit(EDITS, 'Aluminosilicate.setMaterialRGB(0x99ccbb)',
                         "material('supercritical:corium').setMaterialRGB(0x123456)").apply(files)
        if expected.get('added'):
            files = Edit(PRODUCER, ANCHOR, ANCHOR + '\n        ' + DECLARATION).apply(files)
        if expected.get('error'):
            files = dict(cases()[2]['files'])
        files[configuration.CONFIG] = config
        if raw is not None: files[CONFIG] = raw
        result.append({'name': name, 'files': files, 'observeMaterials': [CORIUM, TARGET], **expected})
    return result


def check_prefixes(state):
    failures = []
    if (state.get('status') != 'observed' or state.get('coriumStaticIdentity') is not True
            or state.get('coriumStorageRegistry') != 'supercritical'
            or any(state.get(key) is not False for key in ('damageFunctionsInvoked', 'generatedItemsRegistered', 'fluidsRegistered'))):
        failures.append('original Corium/prefix state absent or unexecuted work claimed')
    rows = state.get('prefixes', [])
    if len(rows) != 9 or {r['name'] for r in rows} != set(PREFIXES):
        return failures + ['original prefix inventory differs']
    for row in rows:
        if (row.get('nativePrefixIdentity') is not True or row.get('metaItemDeclarationCount') != 1
                or row.get('radiationFunctionPresent') is not (row['name'] in PREFIXES[:6])
                or row.get('heatFunctionPresent') is not (row['name'] == 'fuelRodHotDepleted')):
            failures.append('native prefix declaration/function identity differs: ' + row['name'])
    return failures


def check_composition(body, deferred=False):
    execution = body.get('execution', {}); failures = []
    composition = execution.get('supercriticalSubscribers', {})
    steps = {row['id']: row for row in execution.get('initialization', {}).get('steps', [])}
    status = 'deferred' if deferred else 'returned'
    for name in ('supercritical-native-proxy-registration', 'supercritical-native-events-registration'):
        step = steps.get(name, {})
        if step.get('status') != status or deferred and ('elapsedNanos' in step or 'sequence' in step):
            failures.append('native registration visit state differs: ' + name)
    if steps.get('supercritical-configuration', {}).get('status') != 'returned':
        failures.append('native configuration stage missing')
    if composition.get('constructionOverrideApplied') is not False or composition.get('discoveryOrderQualified') is not False:
        failures.append('uncomposed construction/discovery was claimed')
    if deferred:
        if (composition.get('status') != 'deferred' or composition.get('reason') != GAP
                or composition.get('subscribers') != [] or GAP not in execution.get('coverageGaps', [])
                or execution.get('supercriticalState', {}).get('status') != 'not-observed-before-material-callback-completion'):
            failures.append('construction dependency was hidden or unvisited native state inspected')
        return failures
    subscribers = {r['subscriber']: r for r in composition.get('subscribers', [])}
    if composition.get('status') != 'registered' or set(subscribers) != {PROXY, EVENTS}:
        return failures + ['complete Supercritical subscribers absent']
    expected = {PROXY: {'createMaterialRegistry', 'postRegisterMaterials', 'registerBlocks', 'registerItems',
                        'registerRecipes', 'registerRecipesLowest', 'registerModuleContainer', 'syncConfigValues'},
                EVENTS: {'registerMaterials', 'registerMaterialsPost'}}
    for owner, row in subscribers.items():
        if (row.get('registrantOwner') != 'supercritical' or row.get('activeOwnerRestored') is not True
                or row.get('registrationMethod') != 'native-EventBus.register-complete-class'
                or {r['method'] for r in row.get('handlers', [])} != expected[owner]):
            failures.append('native full subscriber/owner differs: ' + owner)
    transforms = body.get('transformations', {}).get('gregtech.api.unification.ore.OrePrefix', [])
    if not transforms or MIXIN not in transforms[-1].get('mergedMixins', []):
        failures.append('required original ore-prefix mixin absent')
    for key, owner, method, priority in (
            ('materialRegistryEventDispatch', PROXY, 'createMaterialRegistry', 'NORMAL'),
            ('materialEventDispatch', EVENTS, 'registerMaterials', 'HIGH'),
            ('postMaterialEventDispatch', PROXY, 'postRegisterMaterials', 'NORMAL'),
            ('postMaterialEventDispatch', EVENTS, 'registerMaterialsPost', 'NORMAL')):
        dispatch = execution.get(key, {})
        matches = [r for r in dispatch.get('listeners', []) if owner + ' ' + method + '(' in r.get('handler', '')]
        if len(matches) != 1 or matches[0].get('priority') != priority or dispatch.get('discoveryOrderQualified') is not False:
            failures.append('original event dispatch entry differs: ' + method)
    return failures


def check_result(case, response, exit_code):
    body = response.get('result', {}); e = body.get('execution', {}); failures = []
    deferred = case.get('deferred', False); error = case.get('error', False)
    if (exit_code != 4 or response.get('status') != body.get('nativeOutcome') or response.get('status') != 'incomplete'
            or body.get('wholePackParity') is not False or not matches_source_acknowledgement(body.get('sourceProgram'), case['files'])
            or e.get('candidateAdmissionViolations') != [] or e.get('contentProgress', {}).get('phase') != 'NOT_STARTED'):
        failures.append('bounded context/custody/admission differs')
    failures += check_composition(body, deferred)
    config = e.get('supercriticalConfiguration', {}); raw = case['files'].get(CONFIG)
    if (config.get('registrationMethod') != 'native-ASMModParser-ConfigManager.loadData-sync'
            or config.get('inputPresent') is not (raw is not None)
            or raw is not None and config.get('inputSha256') != sha256(raw).hexdigest()
            or config.get('enableMaterialModifications') is not deferred
            or config.get('disableAllMaterials') is not (not case.get('catalog', False))
            or config.get('fissionCoolantDivisor') != case.get('divisor', 35.96625)
            or any(config.get(key) is not False for key in ('wholePackConfigurationQualified', 'susyConstructionExecuted', 'susyConstructionOverrideApplied'))):
        failures.append('native configuration identity/value/authority differs')
    if error:
        if 'Harvest Level must be greater than zero!' not in e.get('nativeException', '') or e.get('phase') != 'CLOSED':
            failures.append('original native setter failure/stopped phase absent')
        locations = [where for row in e.get('diagnostics', []) for where in row.get('locations', [])]
        if not any(row.get('path') == EDITS and row.get('line') == 10 for row in locations):
            failures.append('native saved-source error location absent')
    elif e.get('phase') != 'FROZEN' or e.get('nativeException') or e.get('nativeErrors') != []:
        failures.append('native material execution failed')
    registries = e.get('materialRegistries', {})
    counts = {r['modId']: r['registeredMaterials'] for r in registries.get('registries', [])}
    expected = {'gregtech': 3441 if case['name'] == 'pack' else 648}
    if not deferred: expected['supercritical'] = (35 if case.get('catalog') else 1) + int(case.get('added', False))
    if counts != expected or registries.get('totalRegisteredMaterials') != sum(expected.values()):
        failures.append('native registry ownership/count differs')
    materials = {r['name']: r for r in e.get('materials', [])}
    if deferred:
        if CORIUM not in e.get('missingMaterials', []) or CORIUM in materials:
            failures.append('unexecuted Corium was fabricated')
    else:
        failures += check_prefixes(e.get('supercriticalState', {}))
        corium = materials.get(CORIUM, {})
        if (corium.get('id') != 0 or corium.get('color') != case.get('color', 8022864)
                or corium.get('storageRegistry') != 'supercritical' or corium.get('registryIdentity') is not True):
            failures.append('native Corium identity or developer color differs')
        queued = next((r for r in e.get('deferredWork', {}).get('fluids', []) if r['material'] == CORIUM), {})
        if (queued.get('stored') != [] or queued.get('registrationCompleted') is not False
                or not any(r['temperature'] == 2500 for r in queued.get('queued', []))):
            failures.append('original Corium pending fluid absent or prematurely registered')
    if case.get('added'):
        material = materials.get(TARGET, {})
        if material.get('storageRegistry') != 'supercritical' or material.get('id') != 31003 or material.get('color') != 0x123456:
            failures.append('developer material not registered in native SC registry')
    elif TARGET not in e.get('missingMaterials', []) or TARGET in materials:
        failures.append('absent developer material retained')
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    selected = git(args.pack, 'show', selected_revisions()['supersymmetry'] + ':' + CONFIG)
    def selected_cases(config): return corpus(config, selected)
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=selected_cases, result_checker=check_result, whole_pack_observations=(CORIUM, TARGET),
                      receipt_schema='axiom.pack-supercritical-observations.v1')
