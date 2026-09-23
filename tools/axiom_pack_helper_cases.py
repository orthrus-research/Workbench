"""Whole pinned pack helpers in bounded material programs, not a pack baseline."""
from axiom_material_program_cases import cases, Edit, EDITS, identity

PATHS = (
    'groovy/classes/Battery.groovy', 'groovy/classes/QuenchingFluid.groovy',
    'groovy/globals/Carbons.groovy', 'groovy/globals/GroovyUtils.groovy',
    'groovy/globals/Globals.groovy',
)


def corpus(originals):
    if set(originals) != set(PATHS):
        raise ValueError('Complete selected pack helper sources required')
    base = {**cases()[0]['files'], **originals}
    selected = []
    for name, statement in (
        ('battery-capacity', """def battery = new classes.Battery('authoring', 1, 120)
        assert battery.capacity == 76800L
        assert battery.fetchMetaname() == 'battery.authoring'"""),
        ('quenching-registration', """def fluid = new classes.QuenchingFluid('cold', 'hot', 100, 2.5f, true)
        assert fluid.getColdFluid() == 'cold'
        assert fluid.getHotFluid() == 'hot'
        assert fluid.getDuration() == 2.5f
        assert fluid.isInert()
        assert classes.QuenchingFluid.quenching_fluids.size() == 1
        assert classes.QuenchingFluid.quenching_fluids[0] == fluid"""),
        ('carbon-traits-and-index', """assert globals.Carbons.sources.size() == 16
        assert globals.Carbons.getAt('gemCoke').carbon == 100
        assert globals.Carbons.getAt(['gemCoal', 'dustCoal']).size() == 2
        assert globals.Carbons.dusts().size() == 10
        assert globals.Carbons.combustibles().size() == 14
        assert globals.Carbons.highPurityCombustibles().size() == 4"""),
        ('global-records-and-arrays', """assert globals.Globals.voltageTiers[1] == 'lv'
        assert globals.Globals.inertGases[1].name() == 'argon'
        assert globals.Globals.inertGases[1].duration() == 2
        assert globals.Globals.dimensions['Nether'] == -1"""),
        ('compiler-short-and-loop', """short identifier = 2 as short
        int total = 0
        for (value in [1, 2, 3]) { total += value }
        switch(identifier) { case 2: total += 10; break; default: total = 0 }
        assert total == 16
        assert total >= 16 && total <= 16 && total != 0 && total > 0 && total < 17"""),
    ):
        files = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)',
                     'Titanate.addFlags(NO_SMELTING)\n        ' + statement).apply(base)
        selected.append({'name': 'pack-helper-' + name, 'files': files, 'expectedColor': 0x99ccbb})
    files = dict(base)
    files['groovy/preInit/Materials.groovy'] = files['groovy/preInit/Materials.groovy'].replace(b'eventManager.listen', b'event_manager.listen')
    selected.append({'name': 'pack-helper-native-binding-alias', 'files': files, 'expectedColor': 0x99ccbb})
    for temperature in (300, 0):
        files = Edit(EDITS, 'Titanate.addFlags(NO_SMELTING)',
                     'Titanate.addFlags(NO_SMELTING)\n        new gregtech.api.fluids.FluidBuilder().temperature('
                     + str(temperature) + ')').apply(base)
        case = {'name': 'pack-helper-fluid-temperature-' + str(temperature), 'files': files, 'expectedColor': 0x99ccbb}
        if temperature == 0:
            case.update(nativeError=True, executionCompleted=False, expectedPhase='CLOSED', expectedColor=0x88bbaa,
                        expectedNativeFailure='temperature must be > 0',
                        expectedNativeErrors=['java.lang.IllegalArgumentException: temperature must be > 0'])
        selected.append(case)
    return [{**case, 'candidateIdentity': identity(case['files'])} for case in selected]
