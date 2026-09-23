"""Complete ordinary-authoring programs, never a containment or resource campaign."""
from axiom_groovy_language_conformance import authoring_cases
from axiom_material_program_cases import cases, Edit, EDITS, PRODUCER, LISTENERS, identity


def corpus():
    base=cases()[0]
    selected=[{**base,'expectedColor':0x99ccbb},*authoring_cases()]
    for name,statement in (
        ('authoring-default-import',"assert IIngredient.name == 'com.cleanroommc.groovyscript.api.IIngredient'"),
        ('authoring-native-resource-mapping',"""def location = Aluminosilicate.getResourceLocation()
        assert location.getNamespace() == 'supersymmetry'
        assert location.getPath() == 'developer_aluminosilicate'"""),
    ):
        files=Edit(EDITS,'Titanate.addFlags(NO_SMELTING)','Titanate.addFlags(NO_SMELTING)\n        '+statement).apply(base['files'])
        selected.append({'name':name,'files':files,'expectedColor':0x99ccbb})
    files=Edit(EDITS,'    static void apply() {',"""    static void describe() { log.infoMC('axiom-authoring-nested-binding') }
    static void apply() {
        describe()""").apply(base['files'])
    selected.append({'name':'authoring-nested-binding','files':files,'expectedColor':0x99ccbb,'expectedLog':'axiom-authoring-nested-binding'})
    files=Edit(PRODUCER,'.flags(NO_SMELTING)',".flags('no_smelting', 'generate_plate')").apply(base['files'])
    files=Edit(EDITS,'Titanate.addFlags(NO_SMELTING)',
        'Titanate.addFlags(NO_SMELTING)\n        assert Aluminosilicate.hasFlag(GENERATE_PLATE)').apply(files)
    selected.append({'name':'authoring-string-builder-expansion','files':files,'expectedColor':0x99ccbb,'expectedPlate':True})
    return [{**case,'candidateIdentity':identity(case['files'])} for case in selected]


def check(case,reference,native):
    errors=[]
    for side,value in (('reference',reference),('installed',native)):
        if (value.get('phase')!=case.get('expectedPhase','FROZEN') or value.get('registeredMaterials')!=605
                or value.get('executionCompleted') is not case.get('executionCompleted',True) or value.get('coverageGaps')!=[]
                or (not bool(value.get('nativeErrors')) if case.get('nativeErrorsMatchReference')
                    else value.get('nativeErrors')!=case.get('expectedNativeErrors',[]))):
            errors.append(side+': incomplete ordinary program')
        materials=value.get('materials',[])
        if len(materials)!=3 or materials[0].get('color')!=case['expectedColor']:
            errors.append(side+': native authoring result differs')
        if 'expectedPhosphateFormula' in case and (len(materials)!=3 or 'formula' not in materials[1]
                or materials[1]['formula']!=case['expectedPhosphateFormula']):
            errors.append(side+': native formula argument differs')
        if case.get('expectedLog') and case['expectedLog'] not in value.get('log',''):
            errors.append(side+': nested native binding was not observed')
        if 'expectedToolValues' in case:
            if len(materials)!=3: errors.append(side+': native property material selection differs')
            else:
                for index,material in enumerate(materials):
                    expected=case['expectedToolValues'] if index==case.get('toolMaterialIndex',0) else {}
                    if material.get('propertyValues')!=expected:errors.append(side+': native tool values differ at '+str(index))
                selected=materials[case.get('toolMaterialIndex',0)]
                if ('ingot' in selected.get('properties',[])) is not case['expectedIngot']:
                    errors.append(side+': native tool dependency differs')
        if case.get('expectedNativeFailure'):
            failure=value.get('failure',{});text=failure.get('message','') if side=='reference' else value.get('nativeException','')
            if case['expectedNativeFailure'] not in text:errors.append(side+': native authoring failure absent')
    if native.get('cleanObservation') is not (not bool(case.get('expectedNativeFailure'))) or native.get('candidateAdmissionViolations'):
        errors.append('installed: ordinary authoring was not admitted cleanly')
    if case['name']=='authoring-collections' and native.get('candidateDispatchObservations',{}).get(
            'getProperty it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap#first')!=1:
        errors.append('native map implementation was not observed')
    if case.get('expectedPlate') and not any(row.get('material')=='supersymmetry:developer_aluminosilicate'
            and row.get('prefix')=='plate' and row.get('generated') for row in native.get('prefixItems',{}).get('forms',[])):
        errors.append('native string expansion did not produce the requested plate')
    return errors


def language_corpus():
    """Complete programs using native pack authoring transforms, not lowered source."""
    selected=[];base=cases()[0]['files']
    declaration='''
    @groovy.transform.TupleConstructor
    static class Fuel {
        String name; int amountRequired; int duration
        String byproduct; int byproductAmount; int tier
    }
    @groovy.transform.TupleConstructor
    static class Defaults { String name = 'default'; int amount = 10 }
'''
    for name,statement,color in (
        ('tuple-pack-omitted-tail', '''def fuel = new Fuel('methane', 10, 50, 'carbon_dioxide', 5)
        assert fuel.name == 'methane'
        assert fuel.byproduct == 'carbon_dioxide'
        assert fuel.amountRequired == 10
        assert fuel.duration == 50
        assert fuel.byproductAmount == 5
        assert fuel.tier == 0
        Aluminosilicate.setMaterialRGB(fuel.amountRequired + fuel.duration + fuel.byproductAmount + fuel.tier)''',65),
        ('tuple-complete', '''def fuel = new Fuel('methane', 10, 50, 'carbon_dioxide', 5, 3)
        assert fuel.tier == 3
        fuel.tier = 7
        Aluminosilicate.setMaterialRGB(fuel.tier)''',7),
        ('tuple-default-initializers', '''assert new Fuel().name == null
        assert new Fuel().amountRequired == 0
        assert new Defaults().name == 'default'
        assert new Defaults('edited').amount == 10
        assert new Defaults('edited', 12).amount == 12
        Aluminosilicate.setMaterialRGB(new Defaults().amount)''',10),
    ):
        files=Edit(EDITS,'class MaterialEdits {','class MaterialEdits {'+declaration).apply(base)
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',statement).apply(files)
        selected.append({'name':'language-'+name,'files':files,'candidateIdentity':identity(files),'expectedColor':color})
    files=Edit(EDITS,'class MaterialEdits {','class MaterialEdits {'+declaration).apply(base)
    files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',
        "Aluminosilicate.setMaterialRGB(0x99ccbb)\n        new Fuel('methane', 10, 50, 'carbon_dioxide', 5, 3, 99)").apply(files)
    selected.append({'name':'language-tuple-invalid-arity','files':files,'candidateIdentity':identity(files),
        'expectedColor':0x99ccbb,'nativeError':True,'executionCompleted':False,'expectedPhase':'CLOSED',
        'expectedNativeFailure':'Could not find matching constructor for:',
        'expectedNativeErrors':['groovy.lang.GroovyRuntimeException: Could not find matching constructor for: '
            'classes.MaterialEdits$Fuel(String, Integer, Integer, String, Integer, Integer, Integer)']})
    return selected


def record_corpus():
    """Native record keyword in an unchanged complete material lifecycle program."""
    base=cases()[0]['files'];selected=[]
    declaration='''class MaterialEdits {
    record Reagent(String name, int amount, boolean liquid, double duration) {}
'''
    rows=[('pack-components','''def reagent = new Reagent('argon', 400, true, 0.25)
        assert reagent.name == 'argon'
        assert reagent.name() == 'argon'
        assert reagent.amount == 400
        assert reagent.liquid == true
        assert reagent.duration == 0.25d
        Aluminosilicate.setMaterialRGB(reagent.amount)''',400,{}),
        ('value-methods','''def a = new Reagent('argon', 400, true, 0.25)
        def b = new Reagent('argon', 400, true, 0.25)
        assert a.equals(b)
        assert a == b
        assert a.hashCode() == b.hashCode()
        assert !a.equals(new Reagent('argon', 401, true, 0.25))
        assert a.toString() == 'Reagent[name=argon, amount=400, liquid=true, duration=0.25]'
        Aluminosilicate.setMaterialRGB(a.amount)''',400,{}),
        ('named-constructor', '''def reagent = new Reagent(name: 'argon', amount: 400, liquid: true, duration: 0.25)
        assert reagent.duration() == 0.25d
        assert reagent.liquid()
        Aluminosilicate.setMaterialRGB(reagent.amount())''',400,{}),
        ('collection-methods', '''def reagent = new Reagent('argon', 400, true, 0.25)
        assert reagent.size() == 4
        assert reagent[0] == 'argon'
        assert reagent.getAt(1) == 400
        assert reagent.toList() == ['argon', 400, true, 0.25d]
        assert reagent.toMap().name == 'argon'
        assert reagent.toMap().duration == 0.25d
        Aluminosilicate.setMaterialRGB(reagent.toMap().amount)''',400,{}),
        ('top-level', '''def carrier = new Carrier('nitrogen', 8000, 4, 1)
        assert carrier.name == 'nitrogen'
        assert carrier.amount_required == 8000
        Aluminosilicate.setMaterialRGB(carrier.duration * carrier.tier)''',4,{}),
        ('default-initializers', '''assert new DefaultReagent().name == 'argon'
        assert new DefaultReagent('edited').amount == 400
        assert new DefaultReagent(name: 'edited').amount == 400
        assert new DefaultReagent('edited', 401).amount == 401
        Aluminosilicate.setMaterialRGB(new DefaultReagent().amount)''',400,{}),
        ('single-component', '''def formula = new Formula('SiO2')
        assert formula.formula() == 'SiO2'
        assert new Formula(formula: 'SiO2').formula == 'SiO2'
        Phosphate.setFormula(formula.formula(), false)
        Aluminosilicate.setMaterialRGB(0x99ccbb)''',0x99ccbb,{'expectedPhosphateFormula':'SiO2'}),
        ('invalid-arity', "new Reagent('argon', 400, true, 0.25, 9)",0x99ccbb,
            {'nativeError':True,'executionCompleted':False,'expectedPhase':'CLOSED',
             'expectedNativeFailure':'Could not find matching constructor for:',
             'expectedNativeErrors':['groovy.lang.GroovyRuntimeException: Could not find matching constructor for: '
                 'classes.MaterialEdits$Reagent(String, Integer, Boolean, BigDecimal, Integer)']}),
        ('immutable-component', "new Reagent('argon', 400, true, 0.25).amount = 5",0x99ccbb,
            {'nativeError':True,'executionCompleted':False,'expectedPhase':'CLOSED',
             'nativeLinkageFailure':True,
             'expectedNativeFailure':'does not implement the requested interface groovy.lang.GroovyObject',
             'expectedNativeErrors':['java.lang.IncompatibleClassChangeError: Class classes.MaterialEdits$Reagent '
                 'does not implement the requested interface groovy.lang.GroovyObject']}),
        ('immutable-variable', "def reagent = new Reagent('argon', 400, true, 0.25); reagent.amount = 5",0x99ccbb,
            {'nativeError':True,'executionCompleted':False,'expectedPhase':'CLOSED',
             'expectedNativeFailure':'Cannot set readonly property:',
             'expectedNativeErrors':['groovy.lang.ReadOnlyPropertyException: Cannot set readonly property: amount '
                 'for class: classes.MaterialEdits$Reagent']}),
        ('invalid-index', "new Reagent('argon', 400, true, 0.25).getAt(4)",0x99ccbb,
            {'nativeError':True,'executionCompleted':False,'expectedPhase':'CLOSED',
             'expectedNativeFailure':'No record component with index: 4',
             'expectedNativeErrors':['java.lang.IllegalArgumentException: No record component with index: 4']}),
    ]
    for name,statement,color,extra in rows:
        files=Edit(EDITS,'class MaterialEdits {',declaration).apply(base)
        if name=='top-level':files[EDITS]+=b'\nrecord Carrier(String name, int amount_required, int duration, int tier) {}\n'
        if name=='default-initializers':files[EDITS]+=b"\nrecord DefaultReagent(String name = 'argon', int amount = 400) {}\n"
        if name=='single-component':files[EDITS]+=b'\nrecord Formula(String formula) {}\n'
        if extra.get('nativeError'):statement='Aluminosilicate.setMaterialRGB(0x99ccbb)\n        '+statement
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',statement).apply(files)
        selected.append({'name':'record-'+name,'files':files,'candidateIdentity':identity(files),'expectedColor':color,**extra})
    return selected


def trait_corpus(sintering=None):
    """Complete native trait programs modeled on the selected pack's data classes."""
    base=cases()[0]['files'];selected=[]
    declaration='''class MaterialEdits {
    @groovy.transform.TupleConstructor
    static class Feed { String name; boolean active = false; int amount = 10; String describe() { 'delegate' } }
    trait Marker {}
    trait Combustible { int duration }
    trait Pure extends Combustible {}
    trait Active { boolean active = true; String describe() { 'trait' }; String named() { this.name } }
    trait First { int rank() { 1 } }
    trait Last { int rank() { 2 } }
    trait Pyrolyzable { String product }
    trait MaterialEdit {
        void harvest(int level) {
            material.DeveloperMaterials.Aluminosilicate.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).setHarvestLevel(level)
        }
    }
'''
    rows=[
        ('marker', '''def base = new Feed('argon')
        def wrapped = base.withTraits(Marker)
        assert wrapped instanceof Marker
        assert !(base instanceof Marker)
        assert wrapped.name == 'argon'
        assert wrapped.describe() == 'delegate'
        Aluminosilicate.setMaterialRGB(wrapped.amount)''',10),
        ('inherited-fields', '''def wrapped = new Feed('carbon').withTraits(Pure).tap { duration = 4 }
        assert wrapped instanceof Combustible
        assert wrapped instanceof Pure
        assert wrapped.name == 'carbon'
        Aluminosilicate.setMaterialRGB(wrapped.duration)''',4),
        ('overlapping-properties', '''def base = new Feed('nitrogen')
        def wrapped = base.withTraits(Active)
        assert !base.active
        assert wrapped.active
        assert wrapped.named() == 'nitrogen'
        assert wrapped.describe() == 'trait'
        wrapped.active = false
        assert !wrapped.active
        assert !base.active
        Aluminosilicate.setMaterialRGB(wrapped.amount)''',10),
        ('composition-order', '''def first = new Feed('argon').withTraits(First, Last)
        def last = new Feed('argon').withTraits(Last, First)
        assert first.rank() == 2
        assert last.rank() == 1
        Aluminosilicate.setMaterialRGB(first.rank() + last.rank())''',3),
        ('filtering', '''def marked = new Feed('argon').withTraits(Marker)
        def plain = new Feed('air')
        def feeds = [marked, plain]
        assert feeds.grep(Marker).size() == 1
        assert feeds.grep { it !instanceof Marker }.size() == 1
        Aluminosilicate.setMaterialRGB(feeds.grep(Marker)[0].amount)''',10),
        ('independent-state', '''def a = new Feed('argon').withTraits(Pure)
        def b = new Feed('air').withTraits(Pure)
        assert a.duration == 0
        assert b.duration == 0
        a.duration = 9
        assert b.duration == 0
        Aluminosilicate.setMaterialRGB(a.duration)''',9),
        ('loader-distinct-compositions', '''def a = new Feed('carbon').withTraits(First, Last)
        def b = new Feed('carbon').withTraits(First, Pure, Pyrolyzable)
        assert a.rank() == 2
        assert b.rank() == 1
        b.duration = 4
        b.product = 'coal_gas'
        assert b.product == 'coal_gas'
        assert b instanceof Combustible
        assert !(a instanceof Combustible)
        Aluminosilicate.setMaterialRGB(a.rank() + b.rank() + b.duration)''',7),
        ('native-material-error', '''Aluminosilicate.setMaterialRGB(0x99ccbb)
        new Feed('carbon').withTraits(MaterialEdit).harvest(0)''',0x99ccbb),
        ('native-material-correction', '''new Feed('carbon').withTraits(MaterialEdit).harvest(2)
        assert Aluminosilicate.getProperty(gregtech.api.unification.material.properties.PropertyKey.DUST).getHarvestLevel() == 2
        Aluminosilicate.setMaterialRGB(0x99ccbb)''',0x99ccbb),
    ]
    for name,statement,color in rows:
        files=Edit(EDITS,'class MaterialEdits {',declaration).apply(base)
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',statement).apply(files)
        extra={} if name!='native-material-error' else {
            'nativeError':True,'executionCompleted':False,'expectedPhase':'CLOSED',
            'expectedNativeFailure':'Harvest Level must be greater than zero!',
            'expectedNativeErrors':['java.lang.IllegalArgumentException: Harvest Level must be greater than zero!']}
        selected.append({'name':'trait-'+name,'files':files,'candidateIdentity':identity(files),'expectedColor':color,**extra})
    files=Edit(EDITS,'class MaterialEdits {',declaration).apply(base)
    files=Edit(LISTENERS,'MaterialEdits.apply()', '''MaterialEdits.apply()
    def draft = new MaterialEdits.Feed('carbon').withTraits(MaterialEdits.Pure).tap { duration = 7 }
    assert draft.duration == 7
    material.DeveloperMaterials.Aluminosilicate.setMaterialRGB(draft.duration)''').apply(files)
    selected.append({'name':'trait-nested-listener-tap','files':files,'candidateIdentity':identity(files),'expectedColor':7})
    if sintering is not None:
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)', '''assert globals.Sintering.fuels.size() == 5
        def plasma = globals.Sintering.plasmaFuels()
        assert plasma.size() == 1
        assert plasma[0] instanceof globals.Sintering.Plasma
        assert plasma[0].name == 'plasma.helium'
        assert plasma[0].byproduct == 'helium'
        assert globals.Sintering.nonPlasmaFuels().size() == 4
        assert globals.Sintering.comburents[1].name == 'oxygen'
        assert globals.Sintering.blankets[1].duration == 300
        assert globals.Sintering.RotaryKiln.fuels[0].byproductAmount == 25
        Aluminosilicate.setMaterialRGB(plasma[0].duration)''').apply(base)
        files['groovy/globals/Sintering.groovy']=sintering
        selected.append({'name':'trait-pack-sintering','files':files,'candidateIdentity':identity(files),'expectedColor':5})
    return selected


def argument_corpus():
    """Complete source programs exercising native argument construction/dispatch."""
    base=cases()[0]['files'];selected=[]
    for name,statement,color in (
        ('byte','byte value = (byte) 7; Aluminosilicate.setMaterialRGB((int)value)',7),
        ('short','short value = (short) 71; Aluminosilicate.setMaterialRGB((int)value)',71),
        ('long','long value = 123L; Aluminosilicate.setMaterialRGB((int)value)',123),
        ('float','float value = 123.75f; Aluminosilicate.setMaterialRGB((int)value)',123),
        ('double','double value = 123.75d; Aluminosilicate.setMaterialRGB((int)value)',123),
        ('big-integer','def value = 7G; Aluminosilicate.setMaterialRGB((int)(value * 3G))',21),
        ('big-decimal','def value = 3.5; Aluminosilicate.setMaterialRGB((int)(value * 2))',7),
    ):
        selected.append({'name':'argument-'+name,'files':Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)',statement).apply(base),'expectedColor':color})
    for name,statement,formula in (
        ('formula-one-argument',"Phosphate.setFormula('SiO2')",'SiO2'),
        ('formula-formatted',"Phosphate.setFormula('SiO2', true)",'SiO₂'),
        ('formula-null',"Phosphate.setFormula(null, false)\n        assert Phosphate.getChemicalFormula() == null",None),
        ('gstring-argument',"def element = 'Si'; Phosphate.setFormula(\"${element}O2\", false)",'SiO2'),
        ('gstring-assignment',"def element = 'Si'; String formula = \"${element}O2\"; Phosphate.setFormula(formula)",'SiO2'),
    ):
        selected.append({'name':'argument-'+name,'files':Edit(EDITS,"Phosphate.setFormula('(Li,Na)AlPO4(F,OH)', true)",statement).apply(base),
            'expectedColor':0x99ccbb,'expectedPhosphateFormula':formula})
    for name,statement in (
        ('string-flags',"Aluminosilicate.addFlags('generate_plate', 'no_smelting')"),
        ('typed-array-flags','def flags = new gregtech.api.unification.material.info.MaterialFlag[]{GENERATE_PLATE, NO_SMELTING}; Aluminosilicate.addFlags(flags)'),
    ):
        selected.append({'name':'argument-'+name,'files':Edit(EDITS,'Titanate.addFlags(NO_SMELTING)',
            'Titanate.addFlags(NO_SMELTING)\n        '+statement+'\n        assert Aluminosilicate.hasFlag(GENERATE_PLATE)').apply(base),
            'expectedColor':0x99ccbb,'expectedPlate':True})
    return [{**case,'candidateIdentity':identity(case['files'])} for case in selected]


def property_corpus():
    """Native getter values asserted from complete author programs, not replacement rules."""
    import struct
    fields=('toolSpeed','toolAttackDamage','toolAttackSpeed','toolDurability','toolHarvestLevel','toolEnchantability',
            'shouldIgnoreCraftingTools','unbreakable','magnetic','durabilityMultiplier')
    def observed(values):
        result={}
        for index,(field,value) in enumerate(zip(fields,values,strict=True)):
            if index<3:
                bits={'NaN':'7fc00000','Infinity':'7f800000','-Infinity':'ff800000'}.get(value) if isinstance(value,str) else struct.pack('>f',value).hex()
                result[field]={'type':'float32','value':value,'rawBits':bits}
            else: result[field]={'type':'boolean' if isinstance(value,bool) else 'int32','value':value}
        return {'tool':result}
    default=(1.0,1.0,0.0,100,2,10,False,False,False,1)
    pack=(4.0,1.0,0.0,131,1,10,False,False,False,1)
    start='Aluminosilicate.setProperty(PropertyKey.TOOL, new ToolProperty())'
    tool='def tool = Aluminosilicate.getProperty(PropertyKey.TOOL)'
    rows=[
        ('absent','',None,{}),
        ('default',start,default,{}),
        ('pack-constructor','Aluminosilicate.setProperty(PropertyKey.TOOL, new ToolProperty(4.0F, 1.0F, 131, 1))',pack,{}),
        ('mutations',start+'\n        '+tool+'''\n        tool.setToolSpeed(5.5F)
        tool.setToolAttackDamage(2.25F)
        tool.setToolAttackSpeed(-1.25F)
        tool.setToolDurability(2048)
        tool.setToolHarvestLevel(3)
        tool.setToolEnchantability(17)
        tool.setShouldIgnoreCraftingTools(true)
        tool.setUnbreakable(true)
        tool.setMagnetic(true)
        tool.setDurabilityMultiplier(2)''',(5.5,2.25,-1.25,2048,3,17,True,True,True,2),{}),
        ('gem-constructor','Titanate.setProperty(PropertyKey.TOOL, new ToolProperty(4.0F, 1.0F, 131, 1))',pack,{'toolMaterialIndex':2,'expectedIngot':False}),
        ('negative-values','Aluminosilicate.setProperty(PropertyKey.TOOL, new ToolProperty(-4.0F, -1.0F, -131, -1))',(-4.0,-1.0,0.0,-131,-1,10,False,False,False,1),{}),
        ('signed-zero',start+'\n        '+tool+'\n        tool.setToolSpeed(-0.0F)\n        tool.setToolAttackSpeed(0.0F)',(-0.0,1.0,0.0,100,2,10,False,False,False,1),{}),
        ('nonfinite',start+'\n        '+tool+'\n        tool.setToolSpeed(Float.NaN)\n        tool.setToolAttackDamage(Float.POSITIVE_INFINITY)\n        tool.setToolAttackSpeed(Float.NEGATIVE_INFINITY)',('NaN','Infinity','-Infinity',100,2,10,False,False,False,1),{}),
        ('decimal-coercion','Aluminosilicate.setProperty(PropertyKey.TOOL, new ToolProperty(4.25, 1.5, 131, 1))',(4.25,1.5,0.0,131,1,10,False,False,False,1),{}),
        ('duplicate',start+'\n        '+start,default,{'executionCompleted':False,'expectedPhase':'CLOSED','nativeError':True,
            'expectedNativeFailure':'Material Property tool already registered!',
            'expectedNativeErrors':['java.lang.IllegalArgumentException: Material Property tool already registered!']}),
    ]
    selected=[];base=cases()[0]['files']
    for name,statement,values,extra in rows:
        files=Edit(EDITS,'class MaterialEdits {','import gregtech.api.unification.material.properties.ToolProperty\nimport gregtech.api.unification.material.properties.PropertyKey\n\nclass MaterialEdits {').apply(base)
        files=Edit(EDITS,'Aluminosilicate.setMaterialRGB(0x99ccbb)','Aluminosilicate.setMaterialRGB(0x99ccbb)\n        '+statement).apply(files)
        selected.append({'name':'property-tool-'+name,'files':files,'candidateIdentity':identity(files),'expectedColor':0x99ccbb,
            'expectedToolValues':{} if values is None else observed(values),'expectedIngot':values is not None,**extra})
    return selected
