#!/usr/bin/env python3
"""Witness original Susy material callbacks through native event dispatch.

Complete fixture programs run in fresh workers. These observations qualify
neither full addon discovery/order nor pack-generated content or recipes.
"""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
import axiom_pack_property_conformance as properties
from axiom_material_program_cases import cases, Edit, EDITS

SUSY = 'supersymmetry.common.materials.SusyMaterials'
TITANATE = 'supersymmetry:developer_titanate'
PHOSPHATE = 'supersymmetry:developer_phosphate'
MOLY = 'susy:molybdenum_disilicide'
OBSERVED = [MOLY, 'susy:kreep_basalt', 'susy:metallized_bopet', 'susy:lubricating_oil',
            'gregtech:lead', 'gregtech:phosphorus', 'gregtech:aluminium', TITANATE, PHOSPHATE]


def corpus(config):
    definitions = [
        ('native-susy-catalog', 'Titanate.setMaterialRGB(' + SUSY + '.MolybdenumDisilicide.getMaterialRGB())'),
        ('native-susy-edit', SUSY + '.MolybdenumDisilicide.setMaterialRGB(0x123456)'),
        ('native-susy-error', SUSY + '.MolybdenumDisilicide.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(0)'),
        ('native-susy-distinct-aliases', 'Titanate.setMaterialRGB(' + SUSY + '.KreepBasalt.getId()); Phosphate.setMaterialRGB(' + SUSY + '.Leucobasalt.getId())'),
        ('native-susy-catalog-again', 'Titanate.setMaterialRGB(' + SUSY + '.MolybdenumDisilicide.getMaterialRGB())'),
    ]
    result = []
    for name, expression in definitions:
        files = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)', 'Titanate.addFlags(NO_SMELTING)\n        ' + expression).apply(cases()[0]['files'])
        files[configuration.CONFIG] = config
        result.append({'name': name, 'files': files, 'observeMaterials': OBSERVED})
    return result


def check_dispatch(execution, key, method):
    dispatch = execution.get(key, {})
    rows = dispatch.get('listeners', [])
    failures = []
    if (dispatch.get('scope') != 'native-dispatch-cache-before-post-not-completed-invocations'
            or dispatch.get('discoveryOrderQualified') is not False
            or [row.get('index') for row in rows] != list(range(len(rows)))):
        failures.append('native event dispatch snapshot absent or misrepresented')
    native = [row for row in rows if row.get('class') == 'net.minecraftforge.fml.common.eventhandler.ASMEventHandler'
              and 'supersymmetry.common.CommonProxy ' + method + '(' in row.get('handler', '')]
    scripts = [row for row in rows if row.get('class') == 'com.cleanroommc.groovyscript.event.GroovyEventManager$EventListener']
    if (len(native) != 1 or native[0].get('priority') != 'HIGH' or not scripts
            or any(native[0]['index'] >= row['index'] for row in scripts)):
        failures.append('original Susy HIGH handler must precede the fixture script listener')
    return failures


def check_result(case, response, exit_code):
    if case['name'] == 'pack':
        failures = properties.check_result(case, response, exit_code)
        return failures + check_dispatch(response.get('result', {}).get('execution', {}), 'materialEventDispatch', 'registerMaterials')
    failure = ('java.lang.IllegalArgumentException', 'Harvest Level must be greater than zero!') if case['name'] == 'native-susy-error' else None
    failures = configuration.check_result(case, response, exit_code, native_failure=failure)
    body = response.get('result', {}); execution = body.get('execution', {})
    failures += check_dispatch(execution, 'materialEventDispatch', 'registerMaterials')
    failures += check_dispatch(execution, 'postMaterialEventDispatch', 'postRegisterMaterials')
    if execution.get('registeredMaterials') != 648 or execution.get('candidateAdmissionViolations') != []:
        failures.append('complete Susy catalog and unchanged fixture producers were not observed')
    materials = {row['name']: row for row in execution.get('materials', [])}
    expected = {MOLY: (8792, 0x123456 if case['name'] == 'native-susy-edit' else 0x967BB6),
                'susy:kreep_basalt': (27209, None), 'susy:metallized_bopet': (24999, 0x7e9e8e),
                'susy:lubricating_oil': (27059, 0x858146), 'gregtech:phosphorus': (None, 0xfffed6)}
    for name, (identifier, color) in expected.items():
        material = materials.get(name, {})
        if (identifier is not None and material.get('id') != identifier or color is not None and material.get('color') != color
                or material.get('storageRegistry') != 'gregtech' or material.get('registryIdentity') is not True):
            failures.append('native producer/property state differs: ' + name)
    if case['name'] in {'native-susy-catalog', 'native-susy-catalog-again'} and materials.get(TITANATE, {}).get('color') != 0x967BB6:
        failures.append('saved native static field read did not reach its original material')
    if case['name'] == 'native-susy-distinct-aliases':
        if (materials.get(TITANATE, {}).get('color'), materials.get(PHOSPHATE, {}).get('color')) != (27208, 27209):
            failures.append('distinct native static identities were repaired or deduplicated')
    lead = materials.get('gregtech:lead', {}).get('nativePropertyState', {}).get('properties', {})
    if lead.get('fluid_pipe', {}).get('values', {}).get('maxFluidTemperature') != 1200 or 'tankless_fluid_pipe' not in lead:
        failures.append('Susy changeProperties and post-material pipe state absent')
    aluminium = materials.get('gregtech:aluminium', {}).get('nativePropertyState', {})
    if 'continuously_cast' not in aluminium.get('flags', []):
        failures.append('native post-material flag mutation absent')
    transformations = body.get('transformations', {})
    for owner in ('SusyMaterials', 'SuSyElementMaterials', 'SuSyFirstDegreeMaterials', 'SuSySecondDegreeMaterials',
                  'SuSyOrganicChemistryMaterials', 'SuSyHighDegreeMaterials', 'SuSyUnknownCompositionMaterials'):
        if not transformations.get('supersymmetry.common.materials.' + owner):
            failures.append('original producer definition observation absent: ' + owner)
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=corpus, result_checker=check_result, receipt_schema='axiom.pack-lifecycle-observations.v1')
