#!/usr/bin/env python3
"""Original GTFO config witnesses; construction and material callback are deferred."""
import argparse
from hashlib import sha256
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
from axiom_material_program_cases import cases, identity, source_acknowledgement, matches_source_acknowledgement
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git

CONFIG = 'config/gregtechfoodoption.cfg'
SECTION = b'\n    gtfootherfoodmodconfig {'
APPLE = b'B:appleCoreCompat=false'
DIVISOR = b'I:constantFoodStatsDivisor=1'
DIRTS = b'S:greenhouseDirts <'
GAP = 'material-context.gtfo-mod-discovery-incomplete'


def edit_current(raw, before, after):
    if raw.count(SECTION) != 1: raise ValueError('Expected one native GTFO food compatibility category')
    prefix, suffix = raw.split(SECTION)
    if suffix.count(before) != 1: raise ValueError('Expected one current GTFO setting')
    return prefix + SECTION + suffix.replace(before, after)


def corpus(sussy, saved):
    current = edit_current(saved, APPLE, APPLE.replace(b'false', b'true'))
    result = []
    for name, raw, expected in (
            ('saved', saved, {}),
            ('current-compat-enabled', current, {'apple': True}),
            ('old-category-not-an-adapter', edit_current(saved, APPLE, b''), {}),
            ('saved-divisor', edit_current(saved, DIVISOR, b'I:constantFoodStatsDivisor=3'), {'divisor': 3}),
            ('invalid-divisor', edit_current(saved, DIVISOR, b'I:constantFoodStatsDivisor=bad'), {}),
            ('saved-list', saved.replace(DIRTS, DIRTS + b'\n            minecraft:dirt\n            minecraft:grass'),
             {'dirts': ['minecraft:dirt', 'minecraft:grass']}),
            ('absent-file', None, {}),
            ('saved-again', saved, {})):
        files = dict(cases()[0]['files']); files[configuration.CONFIG] = sussy
        if raw is not None: files[CONFIG] = raw
        result.append({'name': name, 'files': files, **expected})
    return result


def check_configuration(case, execution):
    failures = []; observed = execution.get('gtfoConfiguration', {}); raw = case['files'].get(CONFIG)
    expected = {'appleCoreCompat': case.get('apple', False), 'constantFoodStatsDivisor': case.get('divisor', 1),
                'reduceForeignFoodStats': False, 'nuclearCompat': True, 'actuallyCompat': True,
                'unknownSeedsWeight': 5, 'greenhouseDirts': case.get('dirts', []),
                'constructionOverridesExecuted': False, 'wholePackConfigurationQualified': False,
                'valueStage': 'native-config-sync-before-uncomposed-construction-overrides',
                'registrationMethod': 'native-ASMModParser-ConfigManager.loadData-sync',
                'inputPresent': raw is not None, 'path': CONFIG,
                'defaultSource': 'original-native-defaults' if raw is None else 'saved-configuration-with-native-defaults'}
    if any(observed.get(key) != value for key, value in expected.items()):
        failures.append('native GTFO config stage/value/authority differs')
    if raw is not None and observed.get('inputSha256') != sha256(raw).hexdigest():
        failures.append('native GTFO saved input hash differs')
    if raw is None and 'inputSha256' in observed: failures.append('missing input hash fabricated')
    if len(observed.get('workerFileSha256', '')) != 64: failures.append('worker config custody absent')
    prerequisites = observed.get('prerequisites', {})
    if prerequisites != {'nativeDiscoveryQualified': False,
                         'constructionModQueries': ['nuclearcraft', 'actuallyadditions', 'applecore'],
                         'materialCallbackModQueries': ['gcys'], 'materialSubscriberRegisteredByHost': False,
                         'itemInitInvokedByHost': False, 'materialCallbackStatus': 'deferred', 'reason': GAP}:
        failures.append('uncomposed GTFO prerequisites concealed or execution claimed')
    steps = {row['id']: row for row in execution.get('initialization', {}).get('steps', [])}
    if steps.get('gtfo-configuration', {}).get('status') != 'returned': failures.append('native config trace absent')
    for name in ('gtfo-native-construction', 'gtfo-native-subscriber-registration', 'gtfo-native-item-initialization'):
        step = steps.get(name, {})
        if step.get('status') != 'deferred' or 'elapsedNanos' in step or 'sequence' in step:
            failures.append('unexecuted GTFO stage has fabricated visit evidence: ' + name)
    if GAP not in execution.get('coverageGaps', []): failures.append('GTFO discovery gap absent')
    return failures


def check_result(case, response, exit_code):
    body = response.get('result', {}); execution = body.get('execution', {})
    failures = check_configuration(case, execution)
    if (exit_code != 4 or response.get('status') != 'incomplete' or body.get('nativeOutcome') != 'incomplete'
            or body.get('wholePackParity') is not False or not matches_source_acknowledgement(body.get('sourceProgram'), case['files'])
            or execution.get('candidateAdmissionViolations') != [] or execution.get('phase') != 'FROZEN'
            or execution.get('nativeException') or execution.get('nativeErrors') != []
            or execution.get('registeredMaterials') != (3441 if case['name'] == 'pack' else 648)):
        failures.append('previous bounded material execution/custody changed')
    if not body.get('transformations', {}).get('gregtechfoodoption.GTFOConfig'):
        failures.append('original config class transformation audit absent')
    for key in ('materialEventDispatch', 'postMaterialEventDispatch'):
        if any('gregtechfoodoption.GTFOEventHandler' in row.get('handler', '')
               for row in execution.get(key, {}).get('listeners', [])):
            failures.append('unqualified GTFO callback registered')
    return failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('java-home', 'engine-home', 'runtime-home', 'pack', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--whole-pack', action='store_true')
    args = parser.parse_args()
    saved = git(args.pack, 'show', selected_revisions()['supersymmetry'] + ':' + CONFIG)
    def selected_cases(config): return corpus(config, saved)
    configuration.run(args.java_home, args.engine_home, args.runtime_home, args.pack, args.report, args.whole_pack,
                      case_selector=selected_cases, result_checker=check_result,
                      receipt_schema='axiom.pack-gtfo-configuration-observations.v1')
