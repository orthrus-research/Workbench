"""Bounded original GroovyScript language source closure for material authoring.

This is an unqualified build target until native compiler, dispatch and containment
witnesses pass. Host projections are declared below and never silently emulate an
unavailable addon, object mapper, registry reload or client service.
"""
from pathlib import Path
import re

from axiom_material_api_sources import annotations,declaration,substitute,unit,HOST
from axiom_item_sources import native_symbols
from axiom_fluid_sources import member
from axiom_material_program_sources import selected_revisions,GR,GT
from build_axiom_target import git

ROOT=Path(__file__).resolve().parents[1]
MIXINS=('AsmDecompilerMixin','ClassNodeResolverMixin','ClosureMixin','CompUnitClassGenMixin','Java8Mixin',
        'MetaClassImplMixin','ModuleNodeAccessor','ModuleNodeMixin','ResolveVisitorMixin')
CLASSES=(
    *('api/'+n for n in ('GroovyBlacklist','GroovyLog','Hidden','INamed','IScriptReloadable','IObjectParser','Result',
                         'IIngredient','IResourceStack','IMarkable')),
    *('sandbox/'+n for n in ('GroovyScriptSandbox','SandboxData','RunConfig','CustomGroovyScriptEngine',
        'GroovyScriptClassLoader','Preprocessor','FileUtil','CompiledScript','CompiledClass','LoadStage',
        'ClosureHelper','JavaBeanException','GroovyLogImpl','ScriptModContainer')),
    *('sandbox/meta/'+n for n in ('BlackListedMetaClass','ClassMetaClass','ClassScriptMetaClass','GrSMetaClassCreationHandle','ScriptMetaClass','Getter','Setter')),
    *('sandbox/expand/'+n for n in ('ExpansionHelper','LambdaClosure','IDocumented')),
    *('sandbox/security/'+n for n in ('GroovySecurityManager','SandboxSecurityException')),
    *('sandbox/mapper/'+n for n in ('GroovyDeobfMapper','RemappedCachedField','RemappedCachedMethod')),
    *('sandbox/transformer/'+n for n in ('AbstractCompileCustomizer','AbstractTransformer','AsmDecompileHelper',
        'GroovyScriptEarlyCompiler','GroovyScriptCompiler','GroovyScriptTransformer','GroovyCodeFactory')),
    *('helper/'+n for n in ('Alias','JsonHelper','GroovyFile','GroovyHelper','MetaClassExpansion','ReflectionHelper','EnumHelper','ArrayUtils')),
    *('event/'+n for n in ('GroovyEventManager','EventBusExtended','EventBusType','ScriptRunEvent','GroovyReloadEvent')),
    *('core/mixin/groovy/'+n for n in MIXINS),'core/mixin/EventBusMixin',
    'core/SideOnlyConfig','core/GroovyScriptTransformer','packmode/Packmode',
    *('core/visitors/'+n for n in ('InvokerHelperVisitor','CachedClassMethodsVisitor',
        'CachedClassFieldsVisitor','CachedClassConstructorsVisitor','StaticVerifierVisitor')),
)
SUPPORT=('GroovyScript','GroovyScriptConfig','core/GroovyScriptCore','compat/mods/ModSupport',
         'mapper/ObjectMapperManager','mapper/ObjectMappers','registry/ReloadableRegistryManager')
GT_CLASSES=('integration/groovy/GroovyMaterialBuilderExpansion',)
GT_SUPPORT=()
INGREDIENT_BOUNDARIES={
    'default ItemStack applyTransform(': 'groovy.ingredient-crafting-transform',
    'default IIngredient or(': 'groovy.ingredient-disjunction',
}


def read_sources(groovy,gtceu):
    rev=selected_revisions()
    originals={'groovy:'+n:git(groovy,'show',rev['groovyscript']+':'+GR+n+'.java').decode() for n in (*CLASSES,*SUPPORT)}
    originals.update({'gtceu:'+n:git(gtceu,'show',rev['gtceu']+':'+GT+n+'.java').decode() for n in (*GT_CLASSES,*GT_SUPPORT)})
    originals['groovy:gradle.properties']=git(groovy,'show',rev['groovyscript']+':gradle.properties').decode()
    originals['groovy:buildscript.properties']=git(groovy,'show',rev['groovyscript']+':buildscript.properties').decode()
    originals['groovy:mappings.srg']=git(groovy,'show',rev['groovyscript']+':src/main/resources/assets/groovyscript/mappings.srg').decode()
    return originals


def read_resources(groovy):
    revision=selected_revisions()['groovyscript']
    return {name:git(groovy,'show',revision+':src/main/resources/'+name) for name in (
        'assets/groovyscript/mappings.srg','mixin.groovyscript.json')}


def strip(text):
    text=annotations(text)
    text=re.sub(r'^import org\.intellij\.lang\.annotations\.[^;]+;\n','',text,flags=re.M)
    return re.sub(r'@(?:Contract|Flow)\b(?:\([^\n]*?\))?','',text).replace('public static  ','public static ')


def methods(text,*markers): return '\n'.join(strip(member(text,m)) for m in markers)
def port(signature,key): return signature+' { throw '+HOST+'.unsupported("'+key+'"); }'


def ingredient_source(text,mappings):
    """Original imported interface; crafting/disjunction are explicit coverage ports.

    Preserve its inheritance, public API and native EMPTY/ANY implementations.
    These imports are needed by the original compiler, not permission to execute
    recipes or fabricate ingredient objects for the material context.
    """
    text=strip(text)
    for marker,key in INGREDIENT_BOUNDARIES.items():
        original=member(text,marker)
        text=substitute(text,original,port(original[:original.index('{')].rstrip(),key))
    text=substitute(text,'import com.cleanroommc.groovyscript.helper.ingredient.OrIngredient;\n','')
    text=substitute(text,'import net.minecraftforge.common.ForgeHooks;\n','')
    fields,native_methods=native_symbols(mappings)
    for owner in ('net/minecraft/item/ItemStack','net/minecraft/item/crafting/Ingredient'):
        simple=owner.rsplit('/',1)[1]
        text=substitute(text,simple+'.EMPTY',simple+'.'+fields[owner,'EMPTY'])
    for expression,name in (('ingredient.getCount(', 'getCount'),('stack.isEmpty(', 'isEmpty')):
        choices=native_methods['net/minecraft/item/ItemStack',name]
        if len(choices)!=1: raise ValueError('Ingredient native method mapping differs: '+name)
        text=substitute(text,expression,expression.split('.')[0]+'.'+next(iter(choices))+'(')
    return text


def assemble(originals):
    result={}
    for name in CLASSES:
        text=strip(originals['groovy:'+name])
        if name=='api/IIngredient':
            text=ingredient_source(originals['groovy:'+name],originals['groovy:mappings.srg'])
        if name=='sandbox/GroovyScriptClassLoader':
            # The original collector applies BytecodeProcessor before definition,
            # but CompiledClass.ensureLoaded calls the inherited two-argument
            # defineClass directly. Route that OTHER native definition path through
            # the same configured admission hook. A null hook retains the original
            # parent implementation. Candidate source and native caches are not
            # rewritten; both guarded and no-hook cache witnesses are mandatory.
            marker='    protected ClassCollector createCustomCollector(CompilationUnit unit, SourceUnit su) {'
            text=substitute(text,marker,
                '    @Override\n    public Class<?> defineClass(String name, byte[] code) {\n'
                '        BytecodeProcessor processor = this.config.getBytecodePostprocessor();\n'
                '        return super.defineClass(name, processor == null ? code : processor.processBytecode(name, code));\n'
                '    }\n\n'+marker)
        result['com.cleanroommc.groovyscript.'+name.replace('/','.')]=text
    for name in GT_CLASSES:
        result['gregtech.'+name.replace('/','.')]=strip(originals['gtceu:'+name])

    text=strip(originals['groovy:GroovyScript'])
    body='\n'.join(declaration(text,m) for m in ('public static final String NAME =','public static final String MC_VERSION =',
        'public static final Logger LOGGER =','private static GroovyScriptSandbox sandbox;','private static RunConfig runConfig;',
        'private static ModContainer scriptMod;'))
    properties={k.strip():v.strip() for line in originals['groovy:buildscript.properties'].splitlines()
                if '=' in line and not line.lstrip().startswith('#') for k,v in [line.split('=',1)]}
    # Exact build substitution tokens are read from the selected original build configuration.
    body='public static final String ID = "'+properties['modId']+'";\npublic static final String VERSION = "'+properties['modVersion']+'";\n'+body+'\n'
    groovy_versions=re.findall(r'^groovy_version\s*=\s*(\S+)\s*$',originals['groovy:gradle.properties'],re.M)
    if groovy_versions!=['4.0.30']: raise ValueError('Original Groovy runtime version differs')
    body+='public static final String GROOVY_VERSION = "'+groovy_versions[0]+'";\n'
    body+=methods(text,'public static void initializeRunConfig(', 'public static long runGroovyScriptsInLoader(',
        'public static String getScriptPath(', 'public static File getMinecraftHome(', 'public static File getScriptFile(',
        'public static File getResourcesFile(', 'public static File getRunConfigFile(', 'public static GroovyScriptSandbox getSandbox(',
        'public static boolean isSandboxLoaded(', 'public static RunConfig getRunConfig(', 'public static void reloadRunConfig(',
        'private static RunConfig createRunConfig(')
    result['com.cleanroommc.groovyscript.GroovyScript']=unit('com.cleanroommc.groovyscript.GroovyScript',
        'import java.io.*; import java.nio.file.*; import java.nio.charset.StandardCharsets; import org.apache.logging.log4j.*;\n'
        'import com.google.gson.*; import net.minecraftforge.fml.common.*; import com.cleanroommc.groovyscript.sandbox.*;\n'
        'import com.cleanroommc.groovyscript.helper.JsonHelper; import com.cleanroommc.groovyscript.api.GroovyLog;',body)
    for name,original,field,imports in (
        ('GroovyScriptConfig','GroovyScriptConfig','public static String packmode =',''),
        ('core.GroovyScriptCore','core/GroovyScriptCore','public static final Logger LOG =','import org.apache.logging.log4j.*;')):
        qualified='com.cleanroommc.groovyscript.'+name
        result[qualified]=unit(qualified,imports,declaration(strip(originals['groovy:'+original]),field))
    result['com.cleanroommc.groovyscript.core.GroovyScriptCore']=unit('com.cleanroommc.groovyscript.core.GroovyScriptCore',
        'import org.apache.logging.log4j.*; import java.io.File;',
        declaration(strip(originals['groovy:core/GroovyScriptCore']),'public static final Logger LOG =')+'\n'+
        declaration(originals['groovy:core/GroovyScriptCore'],'public static File source;'))
    package='com.cleanroommc.groovyscript.'
    text=originals['groovy:registry/ReloadableRegistryManager']
    result[package+'registry.ReloadableRegistryManager']=unit(package+'registry.ReloadableRegistryManager',
        'import java.util.concurrent.atomic.AtomicBoolean;',declaration(text,'private static final AtomicBoolean firstLoad =')+'\n'+
        methods(text,'public static boolean isFirstLoad(', 'public static void setLoaded(')+'\n'+
        port('public static void onReload()','groovy.registry-reload')+'\n'+port('public static void afterScriptRun()','groovy.registry-postinit'))
    # Native recipe construction uses the selected unchanged binary container
    # and registry owners. Full container discovery remains an explicit port.
    text=originals['groovy:compat/mods/ModSupport']
    result[package+'compat.mods.ModSupport']=unit(package+'compat.mods.ModSupport','',
        declaration(text,'public static final ModSupport INSTANCE =')+'\n'+methods(text,'private ModSupport()')+'\n'+
        port('public boolean hasCompatFor(String name)','groovy.mod-container')+'\n'+
        port('public GroovyContainer<?> getContainer(String name)','groovy.mod-container'))
    # ObjectMapperManager now comes unchanged from the pack-pinned native JAR.
    # Its former conflict-only projection cannot execute original registration
    # callbacks and must not shadow the actual mapper registries/methods.
    text=strip(originals['groovy:mapper/ObjectMappers'])
    result[package+'mapper.ObjectMappers']=unit(package+'mapper.ObjectMappers',
        'import net.minecraft.util.ResourceLocation; import com.cleanroommc.groovyscript.GroovyScript;\n'
        'import com.cleanroommc.groovyscript.api.Result; import static com.cleanroommc.groovyscript.mapper.ObjectMapperManager.SPLITTER;',
        methods(text,'public static Result<ResourceLocation> parseResourceLocation('))
    result['io.sommers.packmode.api.PackModeAPI']=unit('io.sommers.packmode.api.PackModeAPI','import java.util.*;',
        '\n'.join(port(signature,'groovy.external-packmode') for signature in (
            'public static PackModeAPI getInstance()','public String getCurrentPackMode()',
            'public void setNextRestartPackMode(String value)','public boolean isValidPackMode(String value)',
            'public List<String> getPackModes()')))
    # GroovyScriptModule is likewise the original pinned GT binary owner, with
    # its actual module selection, native container and validation methods.
    return result
