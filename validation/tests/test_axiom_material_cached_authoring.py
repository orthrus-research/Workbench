"""Receipt acceptance checks; these synthetic dictionaries do not prove native parity."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import axiom_material_observation_conformance as observation


class MaterialCachedAuthoringTests(unittest.TestCase):
    def fixture(self,guarded):
        compiled={'classes.MaterialEdits':'a'*64,'preInit.Materials$_run_closure1':'b'*64}
        return {'originalCompilerWithoutHook':not guarded,'productionObserversUsed':False,'compiledCache':{
            'actualGroovyCompiledClasses':compiled,'cachedClassesLoaded':sorted(compiled),
            'nativeCacheFiles':{'_index.json':'c'*64},'guarded':guarded,'materialExecutions':1,
            'registryReplay':False,'compilerFallback':False,'savedSourceUnchanged':True,'cacheBytesUnchanged':True,
            'installedCacheReuseAvailable':False,'definitions':[{'name':name,'inputSha256':digest,'outputSha256':'d'*64,
                'route':'CompiledClass.ensureLoaded -> GroovyScriptClassLoader.defineClass(String,byte[])'}
                for name,digest in compiled.items()] if guarded else []}}

    def verify(self,reference,guarded):
        with patch.object(observation,'compare',return_value=[]) as compare, \
                patch.object(observation.authoring,'check',return_value=[]) as authoring:
            result=observation.check_cached({'name':'complete-program'},reference,{'installed':True},guarded)
            compare.assert_called_once_with(reference,{'installed':True})
            authoring.assert_called_once_with({'name':'complete-program'},reference,{'installed':True})
            return result

    def test_original_and_guarded_require_different_definition_evidence(self):
        for guarded in (False,True):
            self.assertEqual([],self.verify(self.fixture(guarded),guarded))
            self.assertTrue(self.verify(self.fixture(not guarded),guarded))

    def test_missing_closure_classes_recompile_replay_or_mutation_cannot_pass(self):
        base=self.fixture(True)
        for key,value in (('actualGroovyCompiledClasses',{}),('cachedClassesLoaded',[]),('nativeCacheFiles',{}),
                ('compilerFallback',True),('registryReplay',True),('materialExecutions',2),
                ('savedSourceUnchanged',False),('cacheBytesUnchanged',False),('installedCacheReuseAvailable',True),('definitions',[])):
            changed=deepcopy(base);changed['compiledCache'][key]=value
            self.assertTrue(self.verify(changed,True),key)

    def test_partial_duplicate_or_wrong_definition_route_cannot_pass(self):
        for key,value in (('inputSha256','0'*64),('outputSha256','a'*64),('route','fresh-compiler'),('name','different.Type')):
            changed=self.fixture(True);changed['compiledCache']['definitions'][0][key]=value
            self.assertTrue(self.verify(changed,True),key)
        changed=self.fixture(True);changed['compiledCache']['definitions'].append(deepcopy(changed['compiledCache']['definitions'][0]))
        self.assertTrue(self.verify(changed,True))

    def test_cache_mechanism_success_cannot_erase_native_behavior_difference(self):
        with patch.object(observation,'compare',return_value=['phase']), \
                patch.object(observation.authoring,'check',return_value=['native color differs']):
            self.assertEqual(['phase','native color differs'],observation.check_cached({},self.fixture(True),{},True))


if __name__=='__main__':unittest.main()
