#!/usr/bin/env python3
"""Bounded native addon-property witnesses, not a whole-pack parity oracle.

Execute original constructors and Material.setProperty on saved Groovy edits.
The native property graph, flags, exceptions and source locations are observed;
no implementation of the upstream verification algorithms lives in this tool.
"""
import argparse
from pathlib import Path
import struct

import axiom_pack_configuration_conformance as configuration
from axiom_material_program_cases import cases, Edit, EDITS, PRODUCER

MATERIAL = 'supersymmetry:developer_addon_property'
SUSY = 'supersymmetry.api.unification.material.properties.'
GT = 'gregtech.api.unification.material.properties.'
COOLANT = 'supercritical.api.unification.material.properties.CoolantProperty'
FIBER = SUSY + 'FiberProperty'
DUMMY = SUSY + 'DummyABSProperty'
MILL = SUSY + 'MillBallProperty'
FUEL = 'supercritical.api.unification.material.properties.FissionFuelProperty'


def corpus(config):
    base = Edit(PRODUCER, 'static Material Titanate', 'static Material Target\n    static Material Titanate').apply(cases()[0]['files'])
    base = Edit(PRODUCER, "log.infoMC('Registering the developer material program')",
                "Target = named(31003, 'developer_addon_property').build()").apply(base)
    base[configuration.CONFIG] = config
    fiber_base = GT + 'MaterialProperties.addBaseType(' + SUSY + 'SuSyPropertyKey.FIBER); '
    fluid = ('Target.getProperties().ensureSet(' + GT + 'PropertyKey.FLUID); '
             'Target.getProperty(' + GT + 'PropertyKey.FLUID).enqueueRegistration('
             'gregtech.api.fluids.store.FluidStorageKeys.LIQUID, new gregtech.api.fluids.FluidBuilder()); ')

    def fiber(arguments):
        return 'Target.setProperty(' + SUSY + 'SuSyPropertyKey.FIBER, new ' + FIBER + '(' + arguments + '))'

    def dummy(arguments):
        return 'Target.setProperty(gregicality.multiblocks.api.unification.properties.GCYMPropertyKey.ALLOY_BLAST, new ' + DUMMY + '(' + arguments + '))'

    coolant = ('Target.setProperty(supercritical.api.unification.material.properties.SCPropertyKey.COOLANT, new ' + COOLANT +
               '(Target, Phosphate, gregtech.api.fluids.store.FluidStorageKeys.LIQUID, 2, 1000, 588, 2260000, 4184)')
    common = {'generate_fiber', 'generate_thread', 'disable_decomposition'}
    queue = ('Target.getProperty(' + GT + 'PropertyKey.FLUID).getStorage().getQueuedBuilder('
             'gregtech.api.fluids.store.FluidStorageKeys.LIQUID)')
    pipe = fiber_base + fiber('') + '; Target.getProperties().ensureSet(' + GT + 'PropertyKey.FLUID_PIPE, true); '
    fuel = FUEL + '.builder(Target.getRegistryName(), 1500, 22000000, 3.5)'
    fuel_key = 'supercritical.api.unification.material.properties.SCPropertyKey.FISSION_FUEL'
    def attach_fuel(builder):
        return 'Target.setProperty(' + fuel_key + ', ' + builder + '.build())'
    definitions = [
        ('fiber-default', fiber_base + fiber(''), {'property': 'fiber', 'class': FIBER,
            'values': {'solutionSpun': False, 'meltSpun': True, 'weaving': False}, 'flags': common,
            'absentFlags': {'generate_wet_fiber', 'generate_plate'}, 'absentProperties': {'empty', 'fluid', 'dust'}}),
        ('fiber-solution-weaving', fiber_base + fiber('true, false, true'), {'property': 'fiber', 'class': FIBER,
            'values': {'solutionSpun': True, 'meltSpun': False, 'weaving': True},
            'flags': common | {'generate_wet_fiber', 'generate_plate'}}),
        ('fiber-fluid-default', fiber_base + fluid + fiber(''), {'property': 'fiber', 'class': FIBER,
            'values': {'solutionSpun': False, 'meltSpun': True, 'weaving': False}, 'flags': common,
            'properties': {'fluid'}}),
        ('fiber-fluid-error', fiber_base + fluid + fiber('true, false, true'), {'property': 'fiber', 'class': FIBER,
            'values': {'solutionSpun': True, 'meltSpun': False, 'weaving': True},
            'error': 'java.lang.IllegalStateException', 'message': 'is not a melt spun fiber', 'absentFlags': common}),
        ('fiber-base-type-missing', fiber(''), {'property': 'fiber', 'class': FIBER,
            'error': 'java.lang.IllegalArgumentException', 'message': 'Material must have at least one of:',
            'flags': common}),
        ('fiber-duplicate', fiber_base + fiber('') + '; ' + fiber('true, false, true'), {'property': 'fiber', 'class': FIBER,
            'values': {'solutionSpun': False, 'meltSpun': True, 'weaving': False},
            'error': 'java.lang.IllegalArgumentException', 'message': 'already registered!'}),
        ('mill-ball', 'Target.setProperty(' + SUSY + 'SuSyPropertyKey.MILL_BALL, new ' + MILL + '(1200))',
            {'property': 'mill_ball', 'class': MILL, 'values': {'durability': 1200}, 'properties': {'dust'}}),
        # The original record has no positivity check. Do not label this invalid.
        ('mill-ball-negative-native', 'Target.setProperty(' + SUSY + 'SuSyPropertyKey.MILL_BALL, new ' + MILL + '(-1))',
            {'property': 'mill_ball', 'class': MILL, 'values': {'durability': -1}, 'properties': {'dust'}}),
        ('dummy-abs-default', fluid + dummy(''), {'property': 'blast_alloy', 'class': DUMMY,
            'values': {'temperature': 0, 'canGenerateMolten': True, 'forceGenerateMolten': False,
                       'recipeProducerClass': DUMMY + '$1'}, 'properties': {'blast', 'fluid', 'ingot', 'dust'}}),
        ('dummy-abs-explicit', fluid + dummy('1400'), {'property': 'blast_alloy', 'class': DUMMY,
            'values': {'temperature': 0, 'recipeProducerClass': DUMMY + '$1'}}),
        ('dummy-abs-setter-error', fluid + dummy('') + '; Target.getProperty(gregicality.multiblocks.api.unification.properties.GCYMPropertyKey.ALLOY_BLAST).setTemperature(0)',
            {'property': 'blast_alloy', 'class': DUMMY, 'error': 'java.lang.IllegalArgumentException', 'message': 'Invalid temperature'}),
        ('coolant-default', fluid + coolant + ')', {'property': 'coolant', 'class': COOLANT,
            'values': {'hotMaterial': 'supersymmetry:developer_phosphate', 'storageKey': 'gregtech:liquid',
                       'accumulatesHydrogen': False}, 'numbers': {'moderatorFactor': 2, 'coolingFactor': 1000,
                       'boilingPoint': 588, 'heatOfVaporization': 2260000, 'specificHeatCapacity': 4184,
                       'slowAbsorptionFactor': 0, 'fastAbsorptionFactor': 0}, 'properties': {'fluid'}}),
        ('coolant-edited', fluid + coolant + '.setAccumulatesHydrogen(true).setSlowAbsorptionFactor(0.1875).setFastAbsorptionFactor(0.0625))',
            {'property': 'coolant', 'class': COOLANT, 'values': {'accumulatesHydrogen': True},
             'numbers': {'slowAbsorptionFactor': 0.1875, 'fastAbsorptionFactor': 0.0625}}),
        ('coolant-empty-fluid-error', coolant + ')', {'property': 'coolant', 'class': COOLANT,
            'properties': {'fluid'}, 'error': 'java.lang.IllegalStateException', 'message': 'FluidProperty cannot be empty'}),
        ('mill-ball-missing-default', 'Target.getProperties().ensureSet(' + SUSY + 'SuSyPropertyKey.MILL_BALL, true)',
            {'property': 'mill_ball', 'class': None, 'valuesObserved': False,
             'error': 'java.lang.NullPointerException', 'message': 'verifyProperty'}),
        ('native-bean-read-write', fiber_base + fiber('') + '; Target.materialRGB = 0x123456; assert Target.materialRGB == Target.getMaterialRGB(); assert !Target.solid',
            {'property':'fiber','class':FIBER,'materialValues':{'color':0x123456}}),
        ('native-bean-setter-error', fiber_base + fiber('') + '; Target.getProperties().ensureSet(' + GT + 'PropertyKey.DUST); Target.getProperty(' + GT + 'PropertyKey.DUST).harvestLevel = 0',
            {'property':'fiber','class':FIBER,'error':'java.lang.IllegalArgumentException','message':'Harvest Level must be greater than zero!'}),
        ('native-string-concatenation', fiber_base + fiber('') +
            "; assert 'high_purity_' + Target.toString() == 'high_purity_developer_addon_property'; assert 'temperature=' + 777 == 'temperature=777'; assert 'enabled=' + false == 'enabled=false'; assert 'missing=' + null == 'missing=null'",
            {'property': 'fiber', 'class': FIBER}),
        ('native-queued-temperature', fiber_base + fluid + fiber('') + '; assert ' + queue +
            '.temperature == -1; ' + queue + '.temperature(777); assert ' + queue + '.temperature == 777',
            {'property': 'fiber', 'class': FIBER, 'properties': {'fluid'}}),
        ('native-queued-temperature-error', fiber_base + fluid + fiber('') + '; ' + queue + '.temperature(0)',
            {'property': 'fiber', 'class': FIBER, 'error': 'java.lang.IllegalArgumentException',
             'message': 'temperature must be > 0'}),
        ('native-blast-bean-read', fluid + dummy('1400') + '; assert Target.getProperty(' + GT +
            'PropertyKey.BLAST).blastTemperature == Target.getProperty(' + GT + 'PropertyKey.BLAST).getBlastTemperature()',
            {'property': 'blast_alloy', 'class': DUMMY, 'values': {'temperature': 0}}),
        ('native-add-ingot', fiber_base + fiber('') + '; assert Target.addIngot() == null; def ingot = Target.getProperty(' + GT +
            'PropertyKey.INGOT); assert Target.addIngot() == null; assert Target.getProperty(' + GT + 'PropertyKey.INGOT) == ingot',
            {'property': 'fiber', 'class': FIBER, 'properties': {'ingot', 'dust'}}),
        ('native-add-ingot-argument-error', fiber_base + fiber('') + '; Target.addIngot(1)',
            {'property': 'fiber', 'class': FIBER, 'error': 'groovy.lang.MissingMethodException',
             'message': 'addIngot', 'absentProperties': {'ingot'}}),
        ('native-int-array-index', fiber_base + fiber('') + '; assert gregtech.api.GTValues.VA[gregtech.api.GTValues.MV] == 120; assert gregtech.api.GTValues.VA[-1] == gregtech.api.GTValues.VA[14]',
            {'property': 'fiber', 'class': FIBER}),
        ('native-int-array-positive-bounds', fiber_base + fiber('') + '; def value = gregtech.api.GTValues.VA[999]',
            {'property': 'fiber', 'class': FIBER, 'error': 'java.lang.ArrayIndexOutOfBoundsException',
             'message': 'Index 999 out of bounds for length 15'}),
        ('native-int-array-negative-bounds', fiber_base + fiber('') + '; def value = gregtech.api.GTValues.VA[-999]',
            {'property': 'fiber', 'class': FIBER, 'error': 'java.lang.ArrayIndexOutOfBoundsException',
             'message': 'Negative array index [-999] too large for array size 15'}),
        ('native-base-proof-default', pipe + 'assert Target != null',
            {'property': 'fluid_pipe', 'class': GT + 'FluidPipeProperties',
             'values': {'containmentPredicate': {}, 'throughput': 1, 'tanks': 1, 'maxFluidTemperature': 300},
             'properties': {'ingot', 'dust'}}),
        ('native-base-proof-enabled', pipe + 'Target.setBaseProof(true)',
            {'property': 'fluid_pipe', 'class': GT + 'FluidPipeProperties',
             'values': {'containmentPredicate': {'susy:base': True}}}),
        ('native-base-proof-cleared', pipe + 'Target.setBaseProof(true); Target.setBaseProof(false)',
            {'property': 'fluid_pipe', 'class': GT + 'FluidPipeProperties',
             'values': {'containmentPredicate': {'susy:base': False}}}),
        ('native-base-proof-missing-pipe', fiber_base + fiber('') + '; Target.setBaseProof(true)',
            {'property': 'fiber', 'class': FIBER, 'loggedError': 'does not have a FluidPipeProperty!',
             'absentProperties': {'fluid_pipe'}}),
        ('native-base-proof-disabled', pipe + 'Target.setBaseProof(true)',
            {'property': 'fluid_pipe', 'class': GT + 'FluidPipeProperties', 'configurationDisabled': True,
             'values': {'containmentPredicate': {}}, 'error': 'groovy.lang.MissingMethodException', 'message': 'setBaseProof'}),
        ('native-fission-defaults', attach_fuel(fuel),
            {'property': 'fission_fuel', 'class': FUEL, 'properties': {'dust'},
             'values': {'id': MATERIAL, 'maxTemperature': 1500, 'duration': 22000000,
                        'depletedFuelSupplierPresent': False, 'allDepletedFuelsPresent': False},
             'numbers': {'requiredNeutrons': 1, 'neutronGenerationTime': 3.5, 'releasedNeutrons': 0,
                         'slowNeutronCaptureCrossSection': 0, 'fastNeutronCaptureCrossSection': 0,
                         'slowNeutronFissionCrossSection': 0, 'fastNeutronFissionCrossSection': 0,
                         'releasedHeatEnergy': 0, 'decayRate': 0}}),
        ('native-fission-edited', attach_fuel(fuel + '.id("saved-edit").maxTemperature(1600).duration(1000).neutronGenerationTime(2.5)'
            '.fastNeutronCaptureCrossSection(0.4).fastNeutronFissionCrossSection(0.2).slowNeutronCaptureCrossSection(1.8)'
            '.slowNeutronFissionCrossSection(1.8).requiredNeutrons(0).releasedNeutrons(2.5).releasedHeatEnergy(0.025).decayRate(0.05)'),
            {'property': 'fission_fuel', 'class': FUEL, 'values': {'id': 'saved-edit', 'maxTemperature': 1600, 'duration': 1000},
             'numbers': {'requiredNeutrons': 0, 'neutronGenerationTime': 2.5, 'releasedNeutrons': 2.5,
                         'slowNeutronCaptureCrossSection': 1.8, 'fastNeutronCaptureCrossSection': 0.4,
                         'slowNeutronFissionCrossSection': 1.8, 'fastNeutronFissionCrossSection': 0.2,
                         'releasedHeatEnergy': 0.025, 'decayRate': 0.05}}),
        # The original builder does not reject negative numeric fields.
        ('native-fission-unchecked-scalars', attach_fuel(fuel + '.maxTemperature(-1).duration(-2).requiredNeutrons(-0.5)'),
            {'property': 'fission_fuel', 'class': FUEL, 'values': {'maxTemperature': -1, 'duration': -2},
             'numbers': {'requiredNeutrons': -0.5}}),
        ('native-fission-duplicate', attach_fuel(fuel) + '; ' + attach_fuel(fuel + '.maxTemperature(1700)'),
            {'property': 'fission_fuel', 'class': FUEL, 'values': {'maxTemperature': 1500},
             'error': 'java.lang.IllegalArgumentException', 'message': 'already registered!'}),
        ('native-fission-builder-argument-error', fiber_base + fiber('') + '; ' + fuel + '.duration(1, 2)',
            {'property': 'fiber', 'class': FIBER, 'error': 'groovy.lang.MissingMethodException', 'message': 'java.lang.Integer.call()'}),
    ]
    result = []
    for name, expression, expected in definitions:
        files = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)',
                     'Titanate.addFlags(NO_SMELTING)\n        ' + expression).apply(base)
        disabled = expected.get('configurationDisabled', False)
        if disabled:
            if config.count(configuration.ANCHOR) != 1:
                raise ValueError('Expected one exact selected recipeInfo setting')
            files[configuration.CONFIG] = config.replace(configuration.ANCHOR, configuration.ANCHOR.replace(b'true', b'false'))
        result.append({'name': name, 'files': files, 'observeMaterials': [MATERIAL], 'expected': expected,
                       'configurationDisabled': disabled})
    # A new process must not retain addBaseType or property instances from before.
    result.append({**result[4], 'name': 'fiber-base-type-missing-again'})
    return result


def check_result(case, response, exit_code):
    if case['name'] == 'pack':
        failures = configuration.check_result(case, response, exit_code)
        execution = response.get('result', {}).get('execution', {})
        for owner in (FIBER, DUMMY, MILL, COOLANT, FUEL):
            if any(owner in row for row in execution.get('candidateAdmissionViolations', [])):
                failures.append('pack property family still rejected: ' + owner)
        if (execution.get('stage') != 'frozen-materials' or execution.get('phase') != 'FROZEN'
                or execution.get('registeredMaterials') != case.get('expectedRegisteredMaterials', 3441)
                or execution.get('candidateAdmissionViolations') != [] or execution.get('nativeErrors') != []
                or execution.get('nativeException')
                or 'material-context.pack-generated-content-incomplete' not in execution.get('coverageGaps', [])):
            failures.append('unchanged selected pack did not reach the recorded next authoring boundary')
        steps = {row['id']: row for row in execution.get('initialization', {}).get('steps', [])}
        if (steps.get('material-event', {}).get('status') != 'returned'
                or steps.get('post-material-event', {}).get('status') != 'returned'
                or steps.get('material-freeze', {}).get('status') != 'returned'
                or 'Finished modifying material flags' not in execution.get('log', '')):
            failures.append('complete original mutation callback did not return before post-material dispatch')
        return failures
    expected = case['expected']
    # Reuse configuration/custody/trace checks with the expected original error.
    failure = (expected['error'], expected['message']) if 'error' in expected else None
    failures = configuration.check_result(case, response, exit_code, native_failure=failure,
                                         native_logged_error=expected.get('loggedError'))
    execution = response.get('result', {}).get('execution', {})
    if execution.get('candidateAdmissionViolations') != []:
        failures.append('property witness hit admission instead of native semantics')
    if 'error' in expected:
        exception = execution.get('nativeException', '')
        if expected['error'] not in exception or expected['message'] not in exception:
            failures.append('original property error absent')
    materials = [row for row in execution.get('materials', []) if row.get('name') == MATERIAL]
    if len(materials) != 1:
        return failures + ['native material state absent']
    for name,value in expected.get('materialValues',{}).items():
        if materials[0].get(name) != value:
            failures.append('native material value differs: ' + name)
    state = materials[0].get('nativePropertyState', {})
    if (state.get('schema') != 'axiom.native-material-property-state.v1'
            or state.get('verificationInvokedByObserver') is not False
            or state.get('recipeProducerInvokedByObserver') is not False):
        failures.append('passive property observation boundary differs')
    properties = state.get('properties', {}); flags = set(state.get('flags', []))
    prop = properties.get(expected['property'], {})
    if 'class' not in prop or prop.get('class') != expected['class'] or prop.get('valuesObserved') is not expected.get('valuesObserved', True):
        failures.append('original native property class or observed values absent')
    values = prop.get('values', {})
    for name, value in expected.get('values', {}).items():
        if values.get(name) != value:
            failures.append('native property field differs: ' + name)
    for name, value in expected.get('numbers', {}).items():
        if values.get(name) != {'type': 'float64', 'value': value, 'rawBits': struct.pack('>d', value).hex()}:
            failures.append('native double field differs: ' + name)
    for name, actual in [('flags', flags), ('properties', set(properties))]:
        if not expected.get(name, set()) <= actual or expected.get('absent' + name.title(), set()) & actual:
            failures.append('native ' + name + ' membership differs')
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=corpus, result_checker=check_result, receipt_schema='axiom.pack-property-observations.v1')
