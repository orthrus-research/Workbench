#!/usr/bin/env python3
"""Saved custom-item programs observed through the original native owners.

These expectations describe selected native outcomes, not item algorithms or a
whole-pack parity oracle. Every case receives a new worker and complete sources.
"""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
import axiom_pack_mutation_conformance as mutations
from axiom_material_program_cases import cases, identity, source_acknowledgement, matches_source_acknowledgement

SCRIPT = 'groovy/preInit/CustomItems.groovy'
PACK_SCRIPT = 'groovy/preInit/RegisterMetaItems.groovy'
META = 'gregtech.api.items.metaitem.'
ELECTRIC = META + 'ElectricStats'
BAUBLE = 'gregtech.integration.baubles.BaubleBehavior'


def program(expression, offset=2, with_arguments=''):
    return ('''package preInit
import gregtech.api.items.metaitem.StandardMetaItem
import gregtech.api.items.metaitem.ElectricStats
import gregtech.api.unification.material.event.PostMaterialEvent
import gregtech.api.unification.ore.OrePrefix
import gregtech.api.unification.material.MarkerMaterials
eventManager.listen { PostMaterialEvent event ->
    new StandardMetaItem(''' + str(offset) + ''' as short).with(''' + with_arguments + ''') {
        setRegistryName('axiom_custom')
        ''' + expression + '''
    }
    log.infoMC('Returned from custom declaration closure')
}
''').encode()


def corpus(config):
    baseline = dict(cases()[0]['files']); baseline[configuration.CONFIG] = config
    item = "addItem(1, 'probe')"
    definitions = [
        ('ordinary', item + "; addItem(7, 'tail')", {'names': ['probe', 'tail']}),
        ('removed-variant', "addItem(7, 'tail')", {'names': ['tail']}),
        ('duplicate-id', item + "; addItem(1, 'duplicate'); addItem(7, 'tail')",
         {'names': ['probe'], 'error': 'java.lang.IllegalArgumentException', 'message': 'MetaId 1 is already occupied'}),
        ('duplicate-name', item + "; addItem(2, 'probe')", {'names': ['probe', 'probe'], 'lookups': [False, True]}),
        ('negative-offset', "addItem(2, 'zero')", {'names': ['zero'], 'offset': -2, 'meta': 2}),
        ('range-low', "addItem(-3, 'bad')", {'names': [], 'error': 'java.lang.IllegalArgumentException', 'message': 'MetaItem ID should be in range'}),
        ('range-high', "addItem(32765, 'bad')", {'names': [], 'error': 'java.lang.IllegalArgumentException', 'message': 'MetaItem ID should be in range'}),
        ('range-upper-valid', "addItem(32764, 'upper')", {'names': ['upper'], 'meta': 32764}),
        ('stack-zero', item + '.setMaxStackSize(0)', {'names': ['probe'], 'error': 'java.lang.IllegalArgumentException', 'message': 'Cannot set Max Stack Size to negative or zero value.'}),
        ('stack-negative', item + '.setMaxStackSize(-1)', {'names': ['probe'], 'error': 'java.lang.IllegalArgumentException', 'message': 'Cannot set Max Stack Size to negative or zero value.'}),
        ('stack-native-unchecked-upper', item + '.setMaxStackSize(128)', {'names': ['probe'], 'values': {'maxStackSize': 128}}),
        ('model-zero', item + '.setModelAmount(0)', {'names': ['probe'], 'error': 'java.lang.IllegalArgumentException', 'message': 'Cannot set amount of models to negative or zero number.'}),
        ('model-corrected', item + '.setModelAmount(8)', {'names': ['probe'], 'values': {'modelAmount': 8}}),
        ('battery', item + '.addComponents(ElectricStats.createRechargeableBattery(80000L, 1), new gregtech.integration.baubles.BaubleBehavior(baubles.api.BaubleType.TRINKET))'
         '.setUnificationData(OrePrefix.battery, MarkerMaterials.Tier.LV).setModelAmount(8)',
         {'names': ['probe'], 'values': {'modelAmount': 8}, 'components': [
             {'class': ELECTRIC, 'maxCharge': 80000, 'tier': 1, 'chargeable': True, 'dischargeable': True},
             {'class': BAUBLE, 'baubleType': 'TRINKET'}]}),
        ('electric-native-unchecked-values', item + '.addComponents(ElectricStats.createRechargeableBattery(-5L, -1))',
         {'names': ['probe'], 'components': [{'class': ELECTRIC, 'maxCharge': -5, 'tier': -1, 'chargeable': True, 'dischargeable': True}]}),
        ('native-null-components', item + '.addComponents(null, null)',
         {'names': ['probe'], 'components': [{'class': None}, {'class': None}]}),
        ('native-null-bauble-type', item + '.addComponents(new gregtech.integration.baubles.BaubleBehavior(null))',
         {'names': ['probe'], 'components': [{'class': BAUBLE, 'baubleType': None}]}),
        ('null-prefix', item + '.setUnificationData(null, null)',
         {'names': ['probe'], 'error': 'java.lang.IllegalArgumentException', 'message': 'Cannot add null OrePrefix.'}),
        ('registry-name-reassignment', "setRegistryName('other')", {'names': [], 'error': 'java.lang.IllegalStateException', 'message': 'Attempted to set registry name with existing registry name!'}),
        ('with-return-self', item, {'names': ['probe'], 'with': 'true'}),
        ('with-return-value', item, {'names': ['probe'], 'with': 'false'}),
        ('unadmitted-delegate', 'getTranslationKey()', {'names': [], 'admission': True}),
        ('ordinary-again', item + "; addItem(7, 'tail')", {'names': ['probe', 'tail']}),
    ]
    result = [{'name': 'no-custom-items', 'files': {**baseline, SCRIPT: b'package preInit\n'}, 'expected': {'notObserved': True}}]
    for name, expression, expected in definitions:
        result.append({'name': name, 'files': {**baseline, SCRIPT: program(expression, expected.get('offset', 2), expected.get('with', ''))},
                       'expected': expected})
    return result


def check_owners(body):
    failures = []; transformed = body.get('transformations', {})
    for owner in (META + 'StandardMetaItem', META + 'MetaItem$MetaValueItem'):
        rows = transformed.get(owner, [])
        if not rows or any(row['inputSha256'] != row['outputSha256'] for row in rows):
            failures.append('original unchanged native item owner absent: ' + owner)
    rows = transformed.get(META + 'MetaItem', [])
    if (not rows or 'com/enderio/core/common/interfaces/IOverlayRenderAware' in rows[-1].get('interfaces', [])
            or not any(method.startswith('initCapabilities(') for method in rows[-1].get('declaredMethods', []))):
        failures.append('complete native MetaItem with native optional-interface removal absent')
    return failures


def check_pack_items(execution, count=409):
    observed = execution.get('customMetaItems', {}); rows = observed.get('items', [])
    if len(rows) != 1:
        return ['selected pack custom item owner absent']
    item = rows[0]; variants = item.get('variants', []); by_name = {row['name']: row for row in variants}
    failures = []
    if (len(variants) != count or item.get('registryName') != 'gregtech:meta_item_2' or item.get('offset') != 2
            or item.get('forgeRegistered') is not False or item.get('nativeClassSpace') is not True):
        failures.append('selected pack declared-item count/owner/registration boundary differs')
    if not mutations.subset(by_name.get('fused_quartz_tube'), {'meta': 10412, 'ownerIdentity': True, 'modelAmount': 1}):
        failures.append('complete item declaration tail missing')
    batteries = [row for row in variants if any(c['class'] == BAUBLE for c in row['components'])]
    expected = {4000: (80000, 1), 4005: (320000, 2), 4008: (112000, 1), 4009: (448000, 2), 4010: (1280000, 3),
                4012: (640000, 2), 4013: (1792000, 3), 4014: (5120000, 4), 4016: (2560000, 3), 4017: (7168000, 4), 4018: (20480000, 5)}
    if len(batteries) != 11 or {row['meta'] for row in batteries} != set(expected):
        failures.append('complete native pack battery declarations missing')
    for row in batteries:
        charge, tier = expected.get(row['meta'], (None, None))
        if row['components'] != [{'class': ELECTRIC, 'maxCharge': charge, 'tier': tier, 'chargeable': True, 'dischargeable': True},
                                  {'class': BAUBLE, 'baubleType': 'TRINKET'}] or row['modelAmount'] != 8:
            failures.append('original ordered battery component values differ')
    drone = by_name.get('drone.lv', {})
    if (drone.get('maxStackSize') != 1 or drone.get('modelAmount') != 8 or drone.get('components') != [
            {'class': ELECTRIC, 'maxCharge': 10000, 'tier': 1, 'chargeable': True, 'dischargeable': True}]):
        failures.append('original drone component state differs')
    return failures


def check_result(case, response, exit_code):
    body = response.get('result', {}); execution = body.get('execution', {})
    if case['name'] == 'pack':
        return mutations.check_result(case, response, exit_code) + check_pack_items(execution) + check_owners(body)
    expected = case['expected']; failures = mutations.check_worker_stages(body)
    if (exit_code != 4 or response.get('status') != 'incomplete' or body.get('wholePackParity') is not False
            or not matches_source_acknowledgement(body.get('sourceProgram'), case['files'])):
        failures.append('incomplete context or complete source custody differs')
    if execution.get('phase') != 'FROZEN' or execution.get('nativeException'):
        failures.append('original with-closure log/continuation behavior changed')
    failures += configuration.check_initialization(case, execution)
    admission = execution.get('candidateAdmissionViolations', [])
    if bool(admission) != bool(expected.get('admission')):
        failures.append('admission outcome differs from original native error')
    errors = execution.get('nativeErrors', [])
    if expected.get('error'):
        if not any(expected['error'] in row and expected['message'] in row for row in errors):
            failures.append('original native custom-item error missing')
        locations = [location for diagnostic in execution.get('diagnostics', []) if diagnostic.get('severity') == 'error'
                     for location in diagnostic.get('locations', [])]
        if not any(row.get('path') == SCRIPT and row.get('line') == 10 for row in locations):
            failures.append('native error saved-source line missing')
    elif not expected.get('admission') and errors:
        failures.append('unexpected native error')
    observed = execution.get('customMetaItems', {})
    if expected.get('notObserved'):
        if observed.get('status') != 'not-observed' or 'items' in observed:
            failures.append('observer initialized an unused native item owner')
        return failures
    failures += check_owners(body)
    rows = observed.get('items', [])
    if (observed.get('stackCreationInvoked') is not False or observed.get('registrationInvoked') is not False
            or len(rows) != 1):
        return failures + ['passive original item state unavailable']
    item = rows[0]; variants = item['variants']
    if (item['registryName'] != 'gregtech:axiom_custom' or item['offset'] != expected.get('offset', 2)
            or item['forgeRegistered'] is not False or item['nativeClassSpace'] is not True
            or [v['name'] for v in variants] != expected['names'] or not all(v['ownerIdentity'] for v in variants)):
        failures.append('original item membership or owner differs')
    if expected.get('lookups') is not None and [v['nameLookupIdentity'] for v in variants] != expected['lookups']:
        failures.append('native duplicate-name lookup/ordered-membership behavior differs')
    if variants:
        first = variants[0]
        if (not mutations.subset(first, expected.get('values', {})) or first['components'] != expected.get('components', [])
                or 'meta' in expected and first['meta'] != expected['meta']):
            failures.append('original custom-item field/component state differs')
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true'); args = parser.parse_args()
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=corpus, result_checker=check_result, whole_pack_observations=mutations.PACK_OBSERVED,
                      receipt_schema='axiom.pack-meta-item-observations.v1')
