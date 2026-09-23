#!/usr/bin/env python3
"""Complete saved material effect witnesses; current membership is not initialization validity.

Programs execute original native registration in fresh workers without material
selectors or expectations. This lane compares complete observed identity/state
maps at equal native phases. It neither implements registration nor repairs source.
"""
import argparse
from pathlib import Path
import re

import axiom_pack_configuration_conformance as configuration
from axiom_material_program_cases import cases, Edit, PRODUCER, EDITS
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git

RUN_CONFIG = 'groovy/runConfig.json'
IDENTITY = 'supersymmetry:developer_effect_probe'
REPLACEMENT = 'supersymmetry:developer_effect_replacement'
STATE_SCOPE = 'native-material-observation-selected-property-values-v1'
ANCHOR = "log.infoMC('Registering the developer material program')"
ADDITION = "named(31003, 'developer_effect_probe').dust().color(0x13579b).build()"
SETTER = 'Aluminosilicate.setMaterialRGB(0x99ccbb)'
BAD_SETTER = 'Aluminosilicate.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(0)'
NATIVE_ERROR = ('java.lang.IllegalArgumentException', 'Harvest Level must be greater than zero!')


def delta(added=(), removed=(), modified=()):
    return {'added': list(added), 'removed': list(removed), 'modified': list(modified)}


def corpus(config, run_config):
    baseline = {**cases()[0]['files'], configuration.CONFIG: config, RUN_CONFIG: run_config}
    addition = Edit(PRODUCER, ANCHOR, ANCHOR + '\n        ' + ADDITION).apply(baseline)
    changed = Edit(PRODUCER, ADDITION, ADDITION.replace('0x13579b', '0x2468ac')).apply(addition)
    replaced = Edit(PRODUCER, ADDITION, ADDITION.replace('developer_effect_probe', 'developer_effect_replacement')).apply(addition)
    error = Edit(EDITS, SETTER, BAD_SETTER).apply(addition)
    definitions = [
        ('baseline', baseline, None, delta(), ()),
        ('addition', addition, 'baseline', delta(added=(IDENTITY,)), (IDENTITY,)),
        ('same-count-property', changed, 'addition', delta(modified=(IDENTITY,)), (IDENTITY,)),
        ('same-count-identity-replacement', replaced, 'addition', delta(added=(REPLACEMENT,), removed=(IDENTITY,)), (REPLACEMENT,)),
        ('deletion', dict(baseline), 'addition', delta(removed=(IDENTITY,)), ()),
        ('native-error', error, None, None, (IDENTITY,)),
        ('manual-correction', dict(addition), 'addition', delta(), (IDENTITY,)),
        ('baseline-repeat', dict(baseline), 'baseline', delta(), ()),
    ]
    return [{'name': name, 'files': files, 'comparisonReference': reference, 'effectDelta': expected,
             'probeIdentities': list(probes), 'nativeError': name == 'native-error'}
            for name, files, reference, expected, probes in definitions]


def native_materials(execution):
    """Require an entire current native collection; never replace missing data with empty."""
    effects = execution.get('registrationEffects', {})
    if (effects.get('schema') != 'axiom.native-registration-effects.v1'
            or not isinstance(effects.get('phase'), str) or effects['phase'] != execution.get('phase')):
        raise ValueError('Native material inventory phase or schema differs')
    observed = effects.get('materials', {})
    entries = observed.get('entries')
    if (observed.get('status') != 'observed' or observed.get('inventoryComplete') is not True
            or observed.get('membership') != 'native-material-registry' or observed.get('stateScope') != STATE_SCOPE
            or not isinstance(entries, dict) or len(entries) > 8192):
        raise ValueError('Complete current native material inventory was not observed')
    if any(not isinstance(identity, str) or not identity or not isinstance(state, str)
           or re.fullmatch('[0-9a-f]{64}', state) is None for identity, state in entries.items()):
        raise ValueError('Native material identity/state inventory is malformed')
    count = execution.get('materialRegistries', {}).get('totalRegisteredMaterials')
    if type(count) is not int or count != len(entries):
        raise ValueError('Complete native material identity inventory differs from native registered count')
    return {'phase': effects['phase'], 'membership': observed['membership'], 'stateScope': observed['stateScope'],
            'entries': dict(entries)}


def identity_delta(before, after):
    for key in ('phase', 'membership', 'stateScope'):
        if not before.get(key) or before[key] != after.get(key):
            raise ValueError('Different or unavailable native material ' + key + '; no cross-checkpoint delta inferred')
    old, current = before['entries'], after['entries']
    return delta(sorted(current.keys() - old.keys()), sorted(old.keys() - current.keys()),
                 sorted(key for key in old.keys() & current.keys() if old[key] != current[key]))


def check_result(case, response, exit_code):
    failures = configuration.check_result(case, response, exit_code,
        native_failure=NATIVE_ERROR if case['nativeError'] else None)
    body = response.get('result', {})
    execution = body.get('execution', {})
    initialization = body.get('initialization', {})
    if initialization.get('status') != 'incomplete' or initialization.get('wholePackValidity') is not False:
        failures.append('Current membership observation was promoted to complete initialization validity')
    if body.get('expectations', {}).get('status') != 'not-requested':
        failures.append('Material feedback unexpectedly required developer expectations')
    try:
        observed = native_materials(execution)
        probes = sorted(set(observed['entries']) & {IDENTITY, REPLACEMENT})
        if probes != sorted(case['probeIdentities']):
            failures.append('Actual registered probe identities differ: ' + str(probes))
    except (ValueError, KeyError, TypeError) as failure:
        failures.append(str(failure))
    if case['nativeError']:
        # The setter is called only after all MaterialEvent registration. Its
        # interrupted PostMaterialEvent is real current membership, not FROZEN.
        if execution.get('phase') == 'FROZEN':
            failures.append('Native setter failure was incorrectly represented as a completed frozen phase')
        locations = [location for row in execution.get('diagnostics', []) for location in row.get('locations', [])]
        if not any(row.get('path') == EDITS and row.get('line') == 10 for row in locations):
            failures.append('Original native setter error saved-source line is missing')
    return failures


def run(java, engine, runtime, pack, report):
    revision = selected_revisions()['supersymmetry']
    run_config = git(pack, 'show', revision + ':' + RUN_CONFIG)
    observed = {}

    def select(config):
        return corpus(config, run_config)

    def check(case, response, exit_code):
        failures = check_result(case, response, exit_code)
        try:
            current = native_materials(response.get('result', {}).get('execution', {}))
            reference = case['comparisonReference']
            if reference is not None:
                if reference not in observed:
                    failures.append('No verified complete native reference inventory: ' + reference)
                else:
                    actual = identity_delta(observed[reference], current)
                    if actual != case['effectDelta']:
                        failures.append('Native material identity/state delta differs: ' + str(actual))
            if not failures:
                observed[case['name']] = current
        except (ValueError, KeyError, TypeError) as failure:
            failures.append(str(failure))
        return failures

    return configuration.run(java, engine, runtime, pack, report, case_selector=select, result_checker=check,
                             receipt_schema='axiom.pack-material-effect-observations.v1')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report)
