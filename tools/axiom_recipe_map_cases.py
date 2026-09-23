"""Complete saved-source RecipeMap extension programs, not a replacement model."""
from axiom_material_program_cases import cases, Edit, EDITS, identity

EXTENSION = 'groovy/preInit/MetaClassExpansions.groovy'


def corpus(extension):
    base = cases()[0]['files']
    imports = '''import gregtech.api.recipes.RecipeMap
import gregtech.api.recipes.builders.SimpleRecipeBuilder
'''
    ordinary = "def map = new RecipeMap('axiom_map', 4, 5, 2, 3, new SimpleRecipeBuilder(), false)\n"
    locked = "def map = new RecipeMap('axiom_map', 4, false, 5, false, 2, false, 3, false, new SimpleRecipeBuilder(), true)\n"
    rows = [
        ('four-limits-receiver-capture', ordinary + '''
        assert map.modifyMaxOutputs(9).modifyMaxInputs(8).modifyMaxFluidOutputs(7).modifyMaxFluidInputs(6).is(map)
        assert map.getMaxInputs() == 8
        assert map.getMaxOutputs() == 9
        assert map.getMaxFluidInputs() == 6
        assert map.getMaxFluidOutputs() == 7
        assert RecipeMap.getByName('axiom_map').is(map)
''', [8, 9, 6, 7], {}),
        ('decrease-vs-native-setters', ordinary + '''
        map.setMaxInputs(1); map.setMaxOutputs(1); map.setMaxFluidInputs(1); map.setMaxFluidOutputs(1)
        assert map.getMaxInputs() == 4
        assert map.getMaxOutputs() == 5
        assert map.getMaxFluidInputs() == 2
        assert map.getMaxFluidOutputs() == 3
        map.modifyMaxInputs(1).modifyMaxOutputs(2).modifyMaxFluidInputs(0).modifyMaxFluidOutputs(1)
''', [1, 2, 0, 1], {}),
        ('locked-flags-direct-fields', locked + '''
        map.modifyMaxInputs(1).modifyMaxOutputs(2).modifyMaxFluidInputs(0).modifyMaxFluidOutputs(1)
''', [1, 2, 0, 1], {'locked': True}),
        ('multiple-native-maps', ordinary + '''
        def second = new RecipeMap('axiom_second', 10, 11, 12, 13, new SimpleRecipeBuilder(), false)
        assert !second.is(map)
        assert second.modifyMaxOutputs(2).is(second)
        assert second.getMaxInputs() == 10
        assert map.getMaxOutputs() == 5
        map.modifyMaxInputs(1)
''', [1, 5, 2, 3], {'second': [10, 2, 12, 13]}),
        ('native-numeric-coercion', ordinary + '''
        map.modifyMaxInputs(2L).modifyMaxOutputs((short)3).modifyMaxFluidInputs(1.75d).modifyMaxFluidOutputs(2G)
''', [2, 3, 1, 2], {}),
        ('native-setter-increase', ordinary + '''
        map.setMaxInputs(8); map.setMaxOutputs(9); map.setMaxFluidInputs(6); map.setMaxFluidOutputs(7)
''', [8, 9, 6, 7], {}),
    ]
    for method, noun in (('Inputs','item input'), ('Outputs','item output'), ('FluidInputs','fluid input'), ('FluidOutputs','fluid output')):
        message = 'Cannot change max ' + noun + ' amount for axiom_map'
        rows.append(('native-locked-' + method, locked + 'map.setMax' + method + '(1)', [4,5,2,3],
                     {'locked': True, 'nativeError': True, 'expectedNativeFailure': message,
                      'expectedNativeErrors': ['java.lang.UnsupportedOperationException: ' + message]}))
    rows.append(('extension-removed-fresh-worker', ordinary + 'map.modifyMaxInputs(1)', [4,5,2,3],
                 {'removeExtension': True, 'nativeError': True,
                  'nativeErrorsMatchReference': True,
                  'expectedNativeFailure': 'No signature of method: gregtech.api.recipes.RecipeMap.modifyMaxInputs()'}))
    selected = []
    for name, statements, limits, extra in rows:
        files = Edit(EDITS, 'class MaterialEdits {', imports + '\nclass MaterialEdits {').apply(base)
        files = Edit(EDITS, 'Aluminosilicate.setMaterialRGB(0x99ccbb)', 'Aluminosilicate.setMaterialRGB(0x99ccbb)\n' + statements).apply(files)
        if not extra.get('removeExtension'):
            files[EXTENSION] = extension
        selected.append({'name': 'recipe-map-' + name, 'files': files, 'candidateIdentity': identity(files),
                         'expectedColor': 0x99ccbb, 'expectedLimits': limits,
                         'executionCompleted': not extra.get('nativeError', False),
                         'expectedPhase': 'CLOSED' if extra.get('nativeError') else 'FROZEN', **extra})
    return selected


def check(case, before, after):
    errors = []
    if before.get('recipeMaps') != after.get('recipeMaps'):
        errors.append('native RecipeMap observations differ')
    maps = after.get('recipeMaps', [])
    expected = {'axiom_map': case['expectedLimits']}
    if 'second' in case:
        expected['axiom_second'] = case['second']
    if {row['name']: row['limits'] for row in maps} != expected:
        errors.append('expected native limits not observed')
    for row in maps:
        if not all(row.get(key) is True for key in ('builderLinked', 'categoryLinked', 'virtualizedRegistryLinked', 'nativeClassSpace')):
            errors.append('native constructor side effects absent')
        locked = bool(case.get('locked')) and row['name'] == 'axiom_map'
        if row.get('modifiable') != [not locked] * 4 or row.get('hidden') is not locked:
            errors.append('native constructor flags differ')
    if not case.get('removeExtension'):
        dispatch = after.get('candidateDispatchObservations', {})
        if sum(value for key, value in dispatch.items() if key.startswith('setProperty ') and '#modifyMax' in key) != 4:
            errors.append('complete native pack extension was not installed')
    return errors
