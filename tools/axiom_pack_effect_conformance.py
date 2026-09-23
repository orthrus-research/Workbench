#!/usr/bin/env python3
"""Saved custom-item effect witnesses, not complete pack initialization qualification.

Each complete program uses the selected pack's original loader configuration and
native owners. No expectations file, material selector, automatic repair, or
registry algorithm is supplied. The native fixed-bound worker remains unchanged.
"""
import argparse
from pathlib import Path

import axiom_pack_configuration_conformance as configuration
import axiom_pack_meta_item_conformance as items
from axiom_material_program_cases import cases
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git

OWNER='gregtech:axiom_custom'
RUN_CONFIG='groovy/runConfig.json'


def corpus(config, run_config):
    baseline=dict(cases()[0]['files'])
    baseline[configuration.CONFIG]=config
    baseline[RUN_CONFIG]=run_config
    definitions=[
        ('baseline', "addItem(1, 'probe')", {'names':['probe']}, [], [], []),
        ('addition', "addItem(1, 'probe'); addItem(7, 'tail')", {'names':['probe','tail']}, [7], [], []),
        ('same-count-property', "addItem(1, 'probe').setMaxStackSize(16)", {'names':['probe'],'values':{'maxStackSize':16}}, [], [], [1]),
        ('same-count-identity-replacement', "addItem(2, 'replacement')", {'names':['replacement'],'meta':2}, [2], [1], []),
        ('deletion', '', {'names':[]}, [], [1], []),
        ('native-error', "addItem(1, 'probe').setMaxStackSize(0)",
         {'names':['probe'],'error':'java.lang.IllegalArgumentException','message':'Cannot set Max Stack Size to negative or zero value.'}, [], [], []),
        ('manual-correction', "addItem(1, 'probe')", {'names':['probe']}, [], [], []),
    ]
    return [{'name':name,'files':{**baseline,items.SCRIPT:items.program(expression)},'expected':expected,
             'effectDelta':{'added':[OWNER+'#'+str(v) for v in added], 'removed':[OWNER+'#'+str(v) for v in removed],
                            'modified':[OWNER+'#'+str(v) for v in modified]}}
            for name,expression,expected,added,removed,modified in definitions]


def native_variants(execution):
    """Read actual native membership. Missing observation is not an empty owner."""
    observed=execution.get('customMetaItems',{})
    if observed.get('status')!='observed' or not isinstance(observed.get('items'),list):
        raise ValueError('Native custom item membership was not observed')
    result={}
    for item in observed['items']:
        if item.get('registryName')!=OWNER or item.get('nativeClassSpace') is not True:
            raise ValueError('Native item owner differs')
        for variant in item.get('variants',[]):
            identity=OWNER+'#'+str(variant['meta'])
            if identity in result:
                raise ValueError('Native owner/meta identity duplicated')
            # Name lookup false can be real native state (duplicate names).
            # Preserve it rather than turning it into a forged absent variant.
            result[identity]={'owner':{key:value for key,value in item.items() if key!='variants'}, 'variant':variant}
    return result


def identity_delta(before, after):
    return {'added':sorted(after.keys()-before.keys()),'removed':sorted(before.keys()-after.keys()),
            'modified':sorted(key for key in before.keys() & after.keys() if before[key]!=after[key])}


def check_result(case,response,exit_code):
    failures=items.check_result(case,response,exit_code)
    body=response.get('result',{});execution=body.get('execution',{});effects=execution.get('registrationEffects',{})
    if body.get('initialization',{}).get('status')!='incomplete' or body.get('initialization',{}).get('wholePackValidity') is not False:
        failures.append('Bounded current initialization was promoted or scoped result contract is missing')
    if body.get('expectations',{}).get('status')!='not-requested':
        failures.append('Native feedback unexpectedly required expectations')
    if effects.get('schema')!='axiom.native-registration-effects.v1' or effects.get('phase')!=execution.get('phase'):
        failures.append('Native effect inventory is not bound to the observed phase')
    materials=effects.get('materials',{})
    registries=execution.get('materialRegistries',{})
    if (materials.get('status')!='observed' or materials.get('inventoryComplete') is not True
            or materials.get('membership')!='native-material-registry' or not isinstance(materials.get('entries'),dict)
            or len(materials.get('entries',{}))!=registries.get('totalRegisteredMaterials')):
        failures.append('Complete current native material membership catalog differs from native registry observation')
    if effects.get('customItems')!={'status':'reference','sourcePointer':'/execution/customMetaItems'}:
        failures.append('Custom effects must reference original observations without duplicating or registering them')
    fluids=effects.get('fluids',{})
    if (fluids.get('status')!='observed' or fluids.get('inventoryComplete') is not True
            or fluids.get('membership')!='native-forge-fluid-registry' or not isinstance(fluids.get('entries'),dict)):
        failures.append('Actual registered-fluid inventory is unavailable; queued builders are not a substitute')
    try:
        actual=native_variants(execution)
        expected_names=case['expected']['names']
        if sorted(row['variant']['name'] for row in actual.values())!=sorted(expected_names):
            failures.append('Exact native custom-item identities/names differ')
    except (ValueError,KeyError,TypeError) as failure:
        failures.append(str(failure))
    return failures


def run(java,engine,runtime,pack,report):
    revision=selected_revisions()['supersymmetry']
    run_config=git(pack,'show',revision+':'+RUN_CONFIG)
    observed={}
    def select(config):
        return corpus(config,run_config)
    def check(case,response,exit_code):
        failures=check_result(case,response,exit_code)
        try:
            current=native_variants(response.get('result',{}).get('execution',{}))
            if case['name']=='baseline':observed['baseline']=current
            if 'baseline' not in observed:
                failures.append('No complete native baseline membership was observed')
            elif identity_delta(observed['baseline'],current)!=case['effectDelta']:
                failures.append('Native baseline-relative identity/state delta differs: '+str(identity_delta(observed['baseline'],current)))
        except (ValueError,KeyError,TypeError) as failure:
            failures.append(str(failure))
        return failures
    return configuration.run(java,engine,runtime,pack,report,case_selector=select,result_checker=check,
                             receipt_schema='axiom.pack-registration-effect-observations.v1')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('java-home','engine-home','runtime-home','pack','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    run(args.java_home,args.engine_home,args.runtime_home,args.pack,args.report)
