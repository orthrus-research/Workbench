#!/usr/bin/env python3
"""Complete native mutation programs and unchanged-pack return witnesses.

Expectations assess observed original state; they never implement material rules.
Every program executes in a fresh isolated worker without changing the checkout.
"""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
import axiom_pack_mapper_conformance as mappers
from axiom_material_program_cases import cases, Edit, EDITS

MATERIAL = 'supersymmetry:developer_aluminosilicate'
OBSERVED = [MATERIAL, 'gregtech:copper']
PACK_OBSERVED = ['gregtech:' + name for name in ('bismuth', 'graphite', 'beryllium', 'steel', 'stainless_steel',
                                              'manganese_phosphide', 'malachite', 'naquadah', 'polyethylene', 'tantalum')]
GT_CONFIG = 'config/gregtech/gregtech.cfg'
PROPERTY = 'gregtech.api.unification.material.properties.PropertyKey.'
STORAGE = 'gregtech.api.fluids.store.FluidStorageKeys.'
MODERATOR = 'supercritical.api.unification.material.properties.ModeratorProperty'
SC_KEY = 'supercritical.api.unification.material.properties.SCPropertyKey.MODERATOR'
MAPS = 'gregtech.api.recipes.RecipeMaps.'
SUSY_MAPS = 'supersymmetry.api.recipes.SuSyRecipeMaps.'


def corpus(config):
    target = 'Aluminosilicate'
    ore = target + '.getProperty(' + PROPERTY + 'ORE)'
    definitions = [
        ('dust-default', target + '.addDust()', {'values': {'dust': {'harvestLevel': 2, 'burnTime': 0}}}),
        ('dust-level', target + '.addDust(4)', {'values': {'dust': {'harvestLevel': 4}}}),
        ('dust-values', target + '.addDust(3, 75)', {'values': {'dust': {'harvestLevel': 3, 'burnTime': 75}}}),
        ('dust-native-error', target + '.addDust(-1)', {'error': 'java.lang.IllegalArgumentException', 'message': 'Harvest Level must be greater than zero!'}),
        ('ingot-native', target + '.addIngot()', {'properties': ['ingot']}),
        ('ore-values', ore + '.setOreMultiplier(3); ' + target + ".setOreByProducts(material('iron'), material('copper')); " + ore + '.setDirectSmeltResult(null)',
         {'values': {'ore': {'oreMultiplier': 3, 'oreByProducts': ['gregtech:iron', 'gregtech:copper'], 'directSmeltResult': None}}}),
        ('ore-clear', ore + '.getOreByProducts().clear()', {'values': {'ore': {'oreByProducts': []}}}),
        ('ore-missing-property', "Phosphate.setOreByProducts(material('iron'))", {'loggedError': 'does not have an OreProperty'}),
        ('fluid-default', target + '.setupFluidTypes(' + STORAGE + 'LIQUID)', {'queues': {'gregtech:liquid': {'temperature': 293}}}),
        ('fluid-multiple', target + '.setupFluidTypes(480, ' + STORAGE + 'LIQUID, ' + STORAGE + 'GAS)',
         {'queues': {'gregtech:liquid': {'temperature': 480}, 'gregtech:gas': {'temperature': 480}}}),
        ('fluid-requeue', target + '.setupFluidTypes(400, ' + STORAGE + 'LIQUID); ' + target + '.setupFluidTypes(500, ' + STORAGE + 'LIQUID)',
         {'queues': {'gregtech:liquid': {'temperature': 500}}}),
        ('fluid-native-error', target + '.setupFluidTypes(0, ' + STORAGE + 'LIQUID)',
         {'error': 'java.lang.IllegalArgumentException', 'message': 'temperature must be > 0'}),
        ('fluid-slurries', target + '.setupSlurries()', {'queues': {'susy:slurry': {'temperature': 293}, 'susy:impure_slurry': {'temperature': 293}}}),
        ('fluid-attributes', target + '.setupFluidTypes(400, ' + STORAGE + 'LIQUID); ' + target + '.setAcidic(' + STORAGE + 'LIQUID); ' + target + '.setBasic(' + STORAGE + 'LIQUID)',
         {'queues': {'gregtech:liquid': {'temperature': 400, 'attributes': ['gregtech:acid', 'susy:base']}}}),
        ('fluid-missing-property', target + '.setAcidic(' + STORAGE + 'LIQUID)', {'loggedError': 'does not have a FluidProperty'}),
        ('fluid-missing-key', target + '.setupFluidTypes(' + STORAGE + 'LIQUID); ' + target + '.setAcidic(' + STORAGE + 'GAS)', {'loggedError': 'does not register a FluidStorageKey'}),
        ('fluid-pipes', target + '.addFluidPipes(1200, 50, true, false, true, false, true)',
         {'values': {'fluid_pipe': {'throughput': 50, 'maxFluidTemperature': 1200, 'cryoProof': True, 'containmentPredicate': {'susy:base': True}}}}),
        ('wire-amperage', "material('copper').getProperty(" + PROPERTY + 'WIRE).setAmperage(12)',
         {'otherValues': {'gregtech:copper': {'wire': {'amperage': 12}}}}),
        ('blast-native', target + ".addBlastProperty(1800, 'MID', 480, 240, -1, -1)",
         {'values': {'blast': {'blastTemperature': 1800, 'gasTier': 'MID', 'eutOverride': 480, 'durationOverride': 240}}}),
        # The original new-property branch passes these arguments to the builder
        # in a different order from the existing-property setters. Preserve both.
        ('blast-existing-native', target + ".addBlastProperty(1000, 'LOW'); " + target + ".addBlastProperty(1800, 'MID', 480, 240, -1, -1)",
         {'values': {'blast': {'blastTemperature': 1800, 'gasTier': 'MID', 'eutOverride': 240, 'durationOverride': 480}}}),
        ('blast-native-error', target + ".addBlastProperty(1800, 'MID'); " + target + '.addBlastProperty(-1)',
         {'error': 'java.lang.IllegalArgumentException', 'message': 'Blast Temperature must be greater than zero!'}),
        ('blast-invalid-tier', target + ".addBlastProperty(1800, 'axiom_missing_tier')", {'loggedError': "Can't find gas tier"}),
        ('moderator-native', target + '.setProperty(' + SC_KEY + ', ' + MODERATOR + '.builder().maxTemperature(3650).moderationFactor(3).absorptionFactor(0.0625).build())',
         {'values': {'moderator': {'maxTemperature': 3650, 'moderationFactor': 3.0, 'absorptionFactor': 0.0625}}}),
        # Original record/builder constructors do not reject these numbers.
        ('moderator-native-unchecked-number', target + '.setProperty(' + SC_KEY + ', ' + MODERATOR + '.builder().maxTemperature(-1).build())',
         {'values': {'moderator': {'maxTemperature': -1}}}),
        ('mill-ball-native', target + '.addMillBall(7680)', {'values': {'mill_ball': {'durability': 7680}}}),
        ('mill-ball-native-unchecked-number', target + '.addMillBall(-1)', {'values': {'mill_ball': {'durability': -1}}}),
        ('catalog-limits', MAPS + 'BLAST_RECIPES.setMaxFluidInputs(2); ' + MAPS + 'PYROLYSE_RECIPES.setMaxFluidInputs(2); '
         + SUSY_MAPS + 'RAILROAD_ENGINEERING_STATION_RECIPES.setMaxFluidInputs(3); ' + SUSY_MAPS + 'RAILROAD_ENGINEERING_STATION_RECIPES.setMaxInputs(12)', {'catalog': True}),
        ('dust-default-again', target + '.addDust()', {'values': {'dust': {'harvestLevel': 2, 'burnTime': 0}}}),
    ]
    result = []
    for name, expression, expected in definitions:
        files = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)', 'Titanate.addFlags(NO_SMELTING)\n        ' + expression).apply(cases()[0]['files'])
        files[configuration.CONFIG] = config
        result.append({'name': name, 'files': files, 'expected': expected, 'observeMaterials': OBSERVED})
    return result


def subset(actual, expected):
    if isinstance(actual, dict) and actual.get('type') == 'float64' and not isinstance(expected, dict):
        return actual.get('value') == expected
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and subset(actual[key], value) for key, value in expected.items())
    return actual == expected


def property_values(execution, name):
    rows = [row for row in execution.get('materials', []) if row.get('name') == name]
    if len(rows) != 1:
        return {}
    return {key: row.get('values', {}) for key, row in rows[0].get('nativePropertyState', {}).get('properties', {}).items()}


def check_catalog(body):
    execution = body.get('execution', {})
    maps = {row['name']: row for row in execution.get('recipeMaps', [])}
    failures = []
    for name, limits in {'electric_blast_furnace': [3, 3, 2, 1], 'pyrolyse_oven': [2, 1, 2, 1],
                         'railroad_engineering_station': [16, 1, 4, 0]}.items():
        row = maps.get(name, {})
        if row.get('limits') != limits or not all(row.get(key) is True for key in ('builderLinked', 'categoryLinked', 'virtualizedRegistryLinked', 'nativeClassSpace')):
            failures.append('native catalog limit/identity differs: ' + name)
    if maps.get('mixer', {}).get('onRecipeBuildOwner') != 'supersymmetry.api.recipes.SuSyRecipeMaps':
        failures.append('original Susy catalog did not register the GT mixer callback')
    rows = body.get('transformations', {}).get('gregtech.api.recipes.RecipeMaps', [])
    if not rows or 'supersymmetry.mixins.gregtech.RecipeMapsMixin' not in rows[-1].get('mergedMixins', []):
        failures.append('original Susy recipe catalog mixin absent')
    for owner in ('gregtech.api.GTValues', 'gregtech.common.ConfigHolder', 'supersymmetry.api.recipes.SuSyRecipeMaps'):
        rows = body.get('transformations', {}).get(owner, [])
        if not rows or any(row['inputSha256'] != row['outputSha256'] for row in rows):
            failures.append('unchanged native owner observation absent: ' + owner)
    return failures


def check_result(case, response, exit_code):
    body = response.get('result', {}); execution = body.get('execution', {})
    if case['name'] == 'pack':
        failures = mappers.check_result(case, response, exit_code) + check_catalog(body) + check_worker_stages(body)
        required = {'gregtech:steel': {'mill_ball': {'durability': 7680}},
                    'gregtech:stainless_steel': {'mill_ball': {'durability': 17280}},
                    'gregtech:graphite': {'moderator': {'maxTemperature': 3650, 'moderationFactor': 3.0}},
                    'gregtech:beryllium': {'moderator': {'maxTemperature': 1500, 'moderationFactor': 5.0}},
                    'gregtech:bismuth': {'fission_fuel': {'maxTemperature': 560, 'duration': 5000}},
                    'gregtech:manganese_phosphide': {'wire': {'amperage': 8}},
                    'gregtech:malachite': {'ore': {'oreMultiplier': 2}},
                    'gregtech:naquadah': {'ore': {'oreByProducts': []}}}
        for name, expected in required.items():
            if not subset(property_values(execution, name), expected):
                failures.append('unchanged pack mutation witness differs: ' + name)
        config = execution.get('gregtechConfiguration', {})
        if not subset(config, {'inputPresent': True, 'inputSha256': configuration.sha256(case['files'][GT_CONFIG]).hexdigest(),
                               'generateLowQualityGems': False, 'allUniqueStoneTypes': False}):
            failures.append('saved GT configuration was not applied')
        return failures
    expected = case['expected']
    failure = (expected['error'], expected['message']) if 'error' in expected else None
    failures = configuration.check_result(case, response, exit_code, native_failure=failure,
                                         native_logged_error=expected.get('loggedError'))
    failures += mappers.check_bindings(execution)
    failures += check_worker_stages(body)
    if execution.get('candidateAdmissionViolations') != []:
        failures.append('mutation did not reach native semantics')
    values = property_values(execution, MATERIAL)
    if not subset(values, expected.get('values', {})) or not set(expected.get('properties', [])).issubset(values):
        failures.append('native material mutation state differs')
    for name, required in expected.get('otherValues', {}).items():
        if not subset(property_values(execution, name), required):
            failures.append('native linked material state differs: ' + name)
    if 'queues' in expected:
        deferred = execution.get('deferredWork', {})
        fluid = next((row for row in deferred.get('fluids', []) if row.get('material') == MATERIAL), {})
        queues = {row['key']: row for row in fluid.get('queued', [])}
        if (set(queues) != set(expected['queues']) or not subset(queues, expected['queues'])
                or fluid.get('registrationCompleted') is not False or deferred.get('fluidRegistrationExecuted') is not False):
            failures.append('original queued fluid state differs or registration was fabricated')
    if expected.get('catalog'):
        failures += check_catalog(body)
    return failures


def check_worker_stages(body):
    trace = body.get('workerStages', {})
    steps = trace.get('steps', [])
    expected = ['runtime-verification', 'source-intake-and-admission', 'native-bootstrap', 'material-context']
    if ([step.get('id') for step in steps] != expected or
            any(step.get('status') != 'returned' or type(step.get('elapsedNanos')) is not int
                or step['elapsedNanos'] < 0 for step in steps)):
        return ['worker timing stages missing, malformed or unfinished']
    return []


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=corpus, result_checker=check_result, whole_pack_observations=PACK_OBSERVED,
                      receipt_schema='axiom.pack-mutation-observations.v1')
