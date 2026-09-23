#!/usr/bin/env python3
"""Original GT material mapper through saved Groovy source, not a lookup oracle.

Each complete program runs in a fresh worker. Missing names and bad arguments
retain original native logging/defaults/exceptions; no source is auto-repaired.
"""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
import axiom_pack_property_conformance as properties
from axiom_material_program_cases import cases, Edit, EDITS

MATERIAL = 'supersymmetry:developer_titanate'


def corpus(config):
    definitions = [
        ('mapper-native', "Titanate.setMaterialRGB(material('water').getId())", {'color': 269}),
        ('mapper-qualified', "Titanate.setMaterialRGB(material('susy:molybdenum_disilicide').getId())", {'color': 8792}),
        ('mapper-fallback-registry', "Titanate.setMaterialRGB(material('unregistered_namespace:water').getId())", {'color': 269}),
        ('mapper-closure', "def lookup = material; Titanate.setMaterialRGB(lookup('water').getId())", {'color': 269}),
        ('mapper-edit', "material('supersymmetry:developer_titanate').setMaterialRGB(0x102030)", {'color': 0x102030}),
        ('mapper-native-error', "material('supersymmetry:developer_titanate').getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(0)",
         {'error': 'java.lang.IllegalArgumentException', 'message': 'Harvest Level must be greater than zero!'}),
        ('mapper-missing', "if (material('axiom_missing_material') == null) Titanate.setMaterialRGB(123)",
         {'color': 123, 'loggedError': 'axiom_missing_material'}),
        ('mapper-empty', "if (material('') == null) Titanate.setMaterialRGB(124)",
         {'color': 124, 'loggedError': "Can't find"}),
        ('mapper-default', "if (material() == null) Titanate.setMaterialRGB(125)", {'color': 125}),
        ('mapper-extra-argument', "if (material('water', 2) == null) Titanate.setMaterialRGB(126)",
         {'color': 126, 'loggedError': 'extra arguments are not allowed'}),
        ('mapper-null', 'material(null)', {'error': 'java.lang.NullPointerException', 'message': 'isEmpty'}),
        ('mapper-wrong-type', 'material(42)', {'error': 'groovy.lang.MissingMethodException', 'message': 'doCall'}),
        ('mapper-native-again', "Titanate.setMaterialRGB(material('water').getId())", {'color': 269}),
    ]
    result = []
    for name, expression, expected in definitions:
        files = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)',
                     'Titanate.addFlags(NO_SMELTING)\n        ' + expression).apply(cases()[0]['files'])
        files[configuration.CONFIG] = config
        result.append({'name': name, 'files': files, 'expected': expected, 'observeMaterials': [MATERIAL]})
    return result


def check_bindings(execution):
    value = execution.get('objectMapperBindings', {})
    if (set(value.get('registered', [])) != {'element', 'material', 'metaitem', 'oreprefix', 'recipemap'}
            or len(value.get('registered', [])) != 5 or value.get('admitted') != ['material']
            or value.get('materialBindingIdentity') is not True
            or value.get('registrationMethod') != 'native-GroovyScriptSandbox.registerBinding'
            or value.get('completeMapperInitialization') is not False):
        return ['original mapper binding identity/scope differs']
    return []


def check_result(case, response, exit_code):
    execution = response.get('result', {}).get('execution', {})
    if case['name'] == 'pack':
        failures = properties.check_result(case, response, exit_code) + check_bindings(execution)
        visits = execution.get('candidateDispatchObservations', {})
        if any(visits.get('invoke material.' + owner + '#material') != 2
               for owner in ('SecondDegreeMaterials', 'ThirdDegreeMaterials')):
            failures.append('unchanged pack did not visit all four original producer mapper calls')
        if 'Finished new material registration' not in execution.get('log', ''):
            failures.append('native pack listener did not observe material producer return')
        return failures
    expected = case['expected']
    failure = (expected['error'], expected['message']) if 'error' in expected else None
    failures = configuration.check_result(case, response, exit_code, native_failure=failure,
                                         native_logged_error=expected.get('loggedError')) + check_bindings(execution)
    if execution.get('candidateAdmissionViolations') != []:
        failures.append('mapper fixture did not reach original native semantics')
    materials = [row for row in execution.get('materials', []) if row.get('name') == MATERIAL]
    if 'color' in expected and (len(materials) != 1 or materials[0].get('color') != expected['color']):
        failures.append('native lookup/default/continuation witness differs')
    for owner in ('ObjectMapperManager', 'ObjectMapper', 'AbstractObjectMapper'):
        rows = response.get('result', {}).get('transformations', {}).get('com.cleanroommc.groovyscript.mapper.' + owner, [])
        if not rows or any(row['inputSha256'] != row['outputSha256'] for row in rows):
            failures.append('original unchanged mapper bytecode observation absent: ' + owner)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=corpus, result_checker=check_result, receipt_schema='axiom.pack-mapper-observations.v1')
