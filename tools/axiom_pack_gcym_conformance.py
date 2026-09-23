#!/usr/bin/env python3
"""Fresh-worker GCYM material-event witnesses, not whole-pack qualification."""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
from axiom_material_program_cases import cases, Edit, PRODUCER, identity, source_acknowledgement, matches_source_acknowledgement

OWNER = 'gregicality.multiblocks.common.GCYMEventHandlers'
MIXIN = 'supersymmetry.mixins.gcym.GCYMEventHandlersMixin'
ALLOY = 'gregicality.multiblocks.api.unification.properties.AlloyBlastProperty'
TARGET = 'supersymmetry:developer_alloy'
FORCED = ['susy:' + name for name in ('monel_500', 'hsla_980_x', 'food_grade_stainless_steel',
                                      'zircaloy_4', 'reactor_steel', 'alnico')]
ANCHOR = "log.infoMC('Registering the developer material program')"


def corpus(config):
    declaration = "named(31003, 'developer_alloy').ingot().fluid().blastTemp(1800).components(Iron, Nickel).build()"
    forced = declaration.replace("named(31003, 'developer_alloy')",
                                 "new Material.Builder(31003, new ResourceLocation('susy', 'monel_500'))").replace('1800', '1000')
    result = []
    for name, expression, target, temperature, molten in (
            ('alloy-added', declaration, TARGET, 1800, True),
            ('alloy-temperature-edit', declaration.replace('1800', '2300'), TARGET, 2300, True),
            ('alloy-below-hot-threshold', declaration.replace('1800', '1000'), TARGET, 1000, False),
            ('alloy-single-component', declaration.replace('Iron, Nickel', 'Iron'), TARGET, None, False),
            ('alloy-removed', '', TARGET, None, False),
            ('susy-force-low-temperature', forced, FORCED[0], 1000, True),
            ('susy-force-missing-property', forced.replace('Iron, Nickel', 'Iron'), FORCED[0], None, False),
            ('susy-force-corrected', forced, FORCED[0], 1000, True),
            ('alloy-fresh-baseline', declaration, TARGET, 1800, True)):
        files = Edit(PRODUCER, ANCHOR, ANCHOR + '\n        ' + expression).apply(cases()[0]['files'])
        files[configuration.CONFIG] = config
        result.append({'name': name, 'files': files, 'observeMaterials': [target],
                       'target': target, 'temperature': temperature, 'molten': molten})
    return result


def check_composition(body):
    execution = body.get('execution', {}); failures = []
    subscriber = execution.get('gcymSubscriber', {})
    if (subscriber.get('subscriber') != OWNER or subscriber.get('registrantOwner') != 'gcym'
            or subscriber.get('activeOwnerRestored') is not True
            or subscriber.get('registrationMethod') != 'native-EventBus.register-complete-class'
            or subscriber.get('eventMixinObserved') is not True or subscriber.get('requiredMixin') != MIXIN
            or subscriber.get('discoveryOrderQualified') is not False
            or {(r['method'], r['priority']) for r in subscriber.get('handlers', [])}
            != {('registerMaterials', 'HIGH'), ('registerMaterialsPost', 'NORMAL')}):
        failures.append('complete transformed native GCYM registration absent')
    transforms = body.get('transformations', {}).get(OWNER, [])
    if not transforms or MIXIN not in transforms[-1].get('mergedMixins', []):
        failures.append('original Susy GCYM transformation not observed')
    for key, method, susy_method, gcym_first in (
            ('materialEventDispatch', 'registerMaterials', 'registerMaterials', True),
            ('postMaterialEventDispatch', 'registerMaterialsPost', 'postRegisterMaterials', False)):
        dispatch = execution.get(key, {}); rows = dispatch.get('listeners', [])
        native = [r for r in rows if OWNER + ' ' + method + '(' in r.get('handler', '')]
        susy = [r for r in rows if 'supersymmetry.common.CommonProxy ' + susy_method + '(' in r.get('handler', '')]
        scripts = [r for r in rows if r.get('class', '').endswith('GroovyEventManager$EventListener')]
        if (dispatch.get('discoveryOrderQualified') is not False
                or len(native) != 1 or len(susy) != 1
                or (native[0]['index'] < susy[0]['index']) is not gcym_first
                or any(native[0]['index'] >= r['index'] for r in scripts)):
            failures.append('bounded native callback order differs: ' + key)
    return failures


def check_material(execution, target, temperature, molten, *, removed=False, forced=False):
    failures = []
    material = next((r for r in execution.get('materials', []) if r['name'] == target), None)
    if removed:
        return [] if material is None and target in execution.get('missingMaterials', []) else ['removed alloy retained']
    if material is None:
        return ['native material absent: ' + target]
    prop = material.get('nativePropertyState', {}).get('properties', {}).get('blast_alloy')
    if temperature is None:
        if prop is not None: failures.append('ineligible material received an alloy property')
    elif (not prop or prop.get('class') != ALLOY or prop.get('valuesObserved') is not True
          or prop.get('values', {}).get('temperature') != temperature
          or prop.get('values', {}).get('forceGenerateMolten') is not forced):
        failures.append('native alloy property differs: ' + target)
    fluid = next((r for r in execution.get('deferredWork', {}).get('fluids', []) if r['material'] == target), {})
    queued = [r for r in fluid.get('queued', []) if r['key'] == 'gcym:molten']
    if (bool(queued) is not molten or len(queued) > 1 or molten and queued[0]['temperature'] != temperature
            or fluid.get('registrationCompleted') is not False or fluid.get('stored') != []):
        failures.append('native pending molten state differs or was prematurely registered: ' + target)
    return failures


def check_result(case, response, exit_code):
    body = response.get('result', {}); execution = body.get('execution', {})
    failures = check_composition(body)
    if (exit_code != 4 or response.get('status') != 'incomplete' or body.get('nativeOutcome') != 'incomplete'
            or body.get('wholePackParity') is not False or not matches_source_acknowledgement(body.get('sourceProgram'), case['files'])
            or execution.get('candidateAdmissionViolations') != []
            or execution.get('contentProgress', {}).get('phase') != 'NOT_STARTED'):
        failures.append('bounded context/input/admission contract differs')
    failed = case['name'] == 'susy-force-missing-property'
    if failed:
        if ('SuSy material has no GCYM AlloyBlastProperty: monel_500' not in execution.get('nativeException', '')
                or execution.get('phase') != 'CLOSED'):
            failures.append('original mixin failure or stopped phase missing')
    elif (execution.get('phase') != 'FROZEN' or execution.get('nativeException') or execution.get('nativeErrors') != []):
        failures.append('original material/post-material execution failed')
    steps = {r['id']: r['status'] for r in execution.get('initialization', {}).get('steps', [])}
    for name, status in {'gcym-native-subscriber-registration': 'returned',
                         'post-material-event': 'threw' if failed else 'returned',
                         'material-freeze': 'not-reached' if failed else 'returned',
                         'addon-material-hooks': 'deferred', 'generated-material-content': 'deferred',
                         'fluid-registration-and-prefix-processing': 'deferred', 'post-init-recipes': 'deferred'}.items():
        if steps.get(name) != status: failures.append('native initialization step differs: ' + name)
    if case['name'] == 'pack':
        if execution.get('registeredMaterials') != 3441: failures.append('original GCYM producer was not cancelled')
        for target in FORCED:
            material = next((r for r in execution.get('materials', []) if r['name'] == target), {})
            temperature = material.get('nativePropertyState', {}).get('properties', {}).get('blast_alloy', {}).get('values', {}).get('temperature')
            if not isinstance(temperature, int): failures.append('forced pack alloy temperature absent: ' + target)
            failures += check_material(execution, target, temperature, True, forced=True)
    else:
        expected_count = 648 if case['name'] == 'alloy-removed' else 649
        if execution.get('registeredMaterials') != expected_count: failures.append('cancelled GCYM catalog or saved declaration count differs')
        failures += check_material(execution, case['target'], case['temperature'], case['molten'],
                                   removed=case['name'] == 'alloy-removed', forced=case['target'].startswith('susy:'))
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=corpus, result_checker=check_result, whole_pack_observations=FORCED,
                      receipt_schema='axiom.pack-gcym-observations.v1')
