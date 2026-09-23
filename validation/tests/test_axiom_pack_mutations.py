"""Mutation witness/custody checks; native acceptance runs independently."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import axiom_pack_mutation_conformance as mutations
import axiom_pack_configuration_conformance as configuration


class PackMutationTests(unittest.TestCase):
    def test_complete_fixture_programs_only_change_the_saved_edit(self):
        cases=mutations.corpus(configuration.ANCHOR)
        self.assertEqual(28,len(cases))
        self.assertEqual(cases[0]['files'],cases[-1]['files'])
        self.assertEqual(len(cases),len({case['name'] for case in cases}))
        for case in cases:
            self.assertEqual(mutations.OBSERVED,case['observeMaterials'])
            self.assertEqual(set(cases[0]['files']),set(case['files']))
            self.assertEqual(cases[0]['files'][configuration.CONFIG],case['files'][configuration.CONFIG])
            for path,raw in case['files'].items():
                if path!=mutations.EDITS:self.assertEqual(cases[0]['files'][path],raw)

    def test_native_logged_errors_and_unchecked_numbers_are_not_reclassified(self):
        cases={case['name']:case for case in mutations.corpus(configuration.ANCHOR)}
        self.assertEqual({'loggedError':"Can't find gas tier"},cases['blast-invalid-tier']['expected'])
        for name in ('moderator-native-unchecked-number','mill-ball-native-unchecked-number'):
            self.assertNotIn('error',cases[name]['expected'])
            self.assertNotIn('loggedError',cases[name]['expected'])

    def test_state_comparison_preserves_absence_null_and_native_double_values(self):
        self.assertFalse(mutations.subset({}, {'directSmeltResult':None}))
        self.assertTrue(mutations.subset({'directSmeltResult':None}, {'directSmeltResult':None}))
        self.assertTrue(mutations.subset({'type':'float64','value':0.0625,'rawBits':'3fb0000000000000'},0.0625))
        self.assertFalse(mutations.subset({'type':'float64','value':0.0625},0.5))
        self.assertFalse(mutations.subset(['a','b'],['b','a']))

    def test_native_blast_branch_argument_order_is_preserved(self):
        cases={case['name']:case for case in mutations.corpus(configuration.ANCHOR)}
        self.assertEqual(480,cases['blast-native']['expected']['values']['blast']['eutOverride'])
        self.assertEqual(240,cases['blast-existing-native']['expected']['values']['blast']['eutOverride'])

    def test_worker_timings_require_completed_nonnegative_explicit_stages(self):
        body={'workerStages':{'steps':[{'id':name,'status':'returned','elapsedNanos':1} for name in
            ('runtime-verification','source-intake-and-admission','native-bootstrap','material-context')]}}
        self.assertEqual([],mutations.check_worker_stages(body))
        for value in (-1,True,None):
            bad=deepcopy(body);bad['workerStages']['steps'][0]['elapsedNanos']=value
            self.assertTrue(mutations.check_worker_stages(bad))
        bad=deepcopy(body);bad['workerStages']['steps'].pop()
        self.assertTrue(mutations.check_worker_stages(bad))

    def test_catalog_requires_non_shrinking_limits_linked_native_identities_and_mixin(self):
        maps=[{'name':name,'limits':limits,'builderLinked':True,'categoryLinked':True,
               'virtualizedRegistryLinked':True,'nativeClassSpace':True} for name,limits in
              [('electric_blast_furnace',[3,3,2,1]),('pyrolyse_oven',[2,1,2,1]),('railroad_engineering_station',[16,1,4,0])]]
        maps.append({'name':'mixer','onRecipeBuildOwner':'supersymmetry.api.recipes.SuSyRecipeMaps'})
        body={'execution':{'recipeMaps':maps},'transformations':{
            'gregtech.api.GTValues':[{'inputSha256':'same','outputSha256':'same'}],
            'gregtech.common.ConfigHolder':[{'inputSha256':'same','outputSha256':'same'}],
            'supersymmetry.api.recipes.SuSyRecipeMaps':[{'inputSha256':'same','outputSha256':'same'}],
            'gregtech.api.recipes.RecipeMaps':[{'mergedMixins':['supersymmetry.mixins.gregtech.RecipeMapsMixin']}]}}
        self.assertEqual([],mutations.check_catalog(body))
        for key,value in [('limits',[12,1,3,0]),('builderLinked',False),('nativeClassSpace',False)]:
            bad=deepcopy(body);bad['execution']['recipeMaps'][2][key]=value
            self.assertTrue(mutations.check_catalog(bad))
        bad=deepcopy(body);bad['transformations']['gregtech.api.recipes.RecipeMaps']=[]
        self.assertTrue(mutations.check_catalog(bad))
        bad=deepcopy(body);bad['execution']['recipeMaps'][-1]['onRecipeBuildOwner']='host.Replacement'
        self.assertTrue(mutations.check_catalog(bad))


if __name__=='__main__':unittest.main()
