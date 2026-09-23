package research.orthrus.axiom.materialhost;

import com.cleanroommc.groovyscript.GroovyScript;
import com.cleanroommc.groovyscript.sandbox.*;
import com.cleanroommc.groovyscript.sandbox.meta.GrSMetaClassCreationHandle;
import com.cleanroommc.groovyscript.sandbox.mapper.GroovyDeobfMapper;
import com.cleanroommc.groovyscript.sandbox.expand.ExpansionHelper;
import gregtech.api.GregTechAPI;
import gregtech.api.items.metaitem.MetaItem;
import gregtech.api.fluids.store.FluidStorageImpl;
import gregtech.api.fluids.store.FluidStorageKey;
import gregtech.api.unification.OreDictUnifier;
import gregtech.api.unification.material.*;
import gregtech.api.unification.material.properties.*;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.common.blocks.*;
import gregtech.core.unification.material.internal.MaterialRegistryManager;
import gregtech.integration.groovy.GroovyMaterialBuilderExpansion;
import groovy.lang.GroovySystem;
import net.minecraft.block.Block;
import net.minecraft.item.Item;
import net.minecraft.launchwrapper.Launch;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegistryEvent;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.fml.common.thread.SidedThreadGroups;
import net.minecraftforge.fml.relauncher.FMLInjectionData;
import java.io.*;
import java.nio.file.*;
import java.lang.reflect.*;
import java.util.*;

/** Trusted fixed-corpus reference. No candidate bytecode gate or production observers.
 * The runner supplies the verbatim upstream compiler class and GT registration section.
 * Native API, content declarations and platform dependencies remain shared, not independent.
 */
public final class MaterialObservationReference {
    private static Object field(Class<?> type,Object receiver,String name) throws Exception {
        Field field=type.getDeclaredField(name);field.setAccessible(true);return field.get(receiver);
    }
    private static void set(Class<?> type,Object receiver,String name,Object value) throws Exception {
        Field field=type.getDeclaredField(name);field.setAccessible(true);field.set(receiver,value);
    }
    private static void check(boolean value,String message) { if(!value)throw new AssertionError(message); }
    public static Object run(String home,String mode,Map<String,String> sources,Map<String,Object> policy) throws Exception {
        check(Set.of("no-audit","cache-original","cache-guarded","argument-reference","trait-reference","recipe-map-reference","pack-helper-reference").contains(mode),"unknown reference mode");
        if(mode.equals("trait-reference"))top.outlands.foundation.TransformerDelegate.registerTransformer(new MaterialTraitReference());
        var task=new java.util.concurrent.FutureTask<Object>(() -> execute(home,mode,sources,policy));
        new Thread(SidedThreadGroups.SERVER,task,"axiom-material-reference").start();
        return task.get();
    }
    private static Object execute(String home,String mode,Map<String,String> sources,Map<String,Object> policy) throws Exception {
        File root=new File(home);Files.createDirectories(root.toPath().resolve("logs"));
        Class.forName("net.minecraft.init.Bootstrap",true,Launch.classLoader).getMethod("axiom$materialIdentities").invoke(null);
        Class.forName("net.minecraft.init.Enchantments",true,Launch.classLoader);
        Class.forName("net.minecraftforge.fluids.FluidRegistry",true,Launch.classLoader);
        set(FMLInjectionData.class,null,"minecraftHome",root);
        SandboxData.initialize(root,GroovyScript.LOGGER);
        Method side=Class.forName("com.cleanroommc.groovyscript.core.SideOnlyConfig").getDeclaredMethod("init");side.setAccessible(true);side.invoke(null);
        var loader=Loader.instance();check(field(Loader.class,loader,"modController")==null,"no game startup");
        set(Loader.class,loader,"modController",new LoadController(loader));
        var metadata=new ModMetadata();metadata.modId="gregtech";metadata.name="GregTech";
        var owner=new DummyModContainer(metadata);var script=new ScriptModContainer();
        var languageMetadata=new ModMetadata();languageMetadata.modId="groovyscript";languageMetadata.name="GroovyScript";
        set(Loader.class,loader,"namedMods",Map.of("gregtech",owner,"groovyscript",new DummyModContainer(languageMetadata),script.getModId(),script));loader.setActiveModContainer(owner);
        GroovySystem.getMetaClassRegistry().setMetaClassCreationHandle(GrSMetaClassCreationHandle.INSTANCE);
        GroovySystem.getMetaClassRegistry().getMetaClassCreationHandler().setDisableCustomMetaClassLookup(true);
        GroovyDeobfMapper.init();set(GroovyScript.class,null,"sandbox",new GroovyScriptSandbox());
        NativeRecipeContext.prepare();
        check(GroovyScript.getSandbox().getEngine().getConfig().getBytecodePostprocessor()==null,"no reference bytecode processor");
        boolean guarded=mode.equals("cache-guarded");
        if(!guarded)try { GroovyScriptClassLoader.class.getDeclaredMethod("defineClass",String.class,byte[].class);
              throw new AssertionError("Axiom cache hook entered original classloader reference"); }
        catch(NoSuchMethodException original) { }
        MaterialArgumentBytecode argumentBytecode=Set.of("argument-reference","trait-reference","recipe-map-reference","pack-helper-reference").contains(mode)?new MaterialArgumentBytecode():null;
        if(argumentBytecode!=null)GroovyScript.getSandbox().getEngine().getConfig().setBytecodePostprocessor(argumentBytecode);
        ExpansionHelper.mixinClass(Material.Builder.class,GroovyMaterialBuilderExpansion.class);
        GregTechAPI.materialManager=MaterialRegistryManager.getInstance();
        var manager=MaterialRegistryManager.getInstance();
        check(manager.getDefaultRegistry().getAllMaterials().isEmpty()&&GregTechAPI.markerMaterialRegistry==null,"no eager catalog");
        // A standard native file sink retains the original Log4j errors. It has
        // no Axiom callback, stack sampling, exception interception or writer wrapper.
        Path log4jFile=root.toPath().resolve("logs/reference-log4j.txt");
        var appender=org.apache.logging.log4j.core.appender.FileAppender.newBuilder().setName("reference-native-file")
                .withFileName(log4jFile.toString()).setLayout(org.apache.logging.log4j.core.layout.PatternLayout.newBuilder()
                        .withPattern("%level %logger %msg%n").build()).build();
        appender.start();
        var logRoot=(org.apache.logging.log4j.core.Logger)org.apache.logging.log4j.LogManager.getRootLogger();
        logRoot.addAppender(appender);
        var result=new LinkedHashMap<String,Object>();
        var content=new Content();boolean completed=false;
        NativeCompiledCacheProbe cache=null;
        try {
            GroovyScript.getRunConfig().initPackmode();
            if(mode.startsWith("cache-"))cache=NativeCompiledCacheProbe.prepare(guarded,sources,policy);
            check(manager.getDefaultRegistry().getAllMaterials().isEmpty()&&GregTechAPI.markerMaterialRegistry==null,"no material execution during cache preparation");
            GroovyScript.runGroovyScriptsInLoader(LoadStage.PRE_INIT);
            check(manager.getDefaultRegistry().getAllMaterials().isEmpty(),"scripts did not populate base catalog");
            OriginalMaterialRegistration.run();
            content.run();completed=true;
        } catch(Throwable failure) {
            var frames=new ArrayList<Map<String,Object>>();
            for(var frame:failure.getStackTrace()) {
                String name=frame.getClassName();String source=sources.get(name);
                while(source==null&&name.contains("$")) {name=name.substring(0,name.lastIndexOf('$'));source=sources.get(name);}
                if(source!=null)frames.add(Map.of("path",source,"line",frame.getLineNumber(),"class",frame.getClassName(),"method",frame.getMethodName()));
            }
            result.put("failure",Map.of("type",failure.getClass().getName(),"message",String.valueOf(failure.getMessage()),"frames",frames,
                "causality",originalExceptionGraph(failure,sources)));
        }
        // All observations happen AFTER native execution has stopped, never inside callbacks.
        result.put("executionCompleted",completed);result.put("phase",manager.getPhase().name());
        if(mode.equals("recipe-map-reference"))result.put("recipeMaps",RecipeMapReference.observe());
        result.put("activeOwner",loader.activeModContainer().getModId());
        result.put("registeredMaterials",manager.getDefaultRegistry().getAllMaterials().size());
        var materials=new ArrayList<Map<String,Object>>();var fluids=new ArrayList<Map<String,Object>>();
        for(String name:List.of("developer_aluminosilicate","developer_phosphate","developer_titanate")) {
            Material material=manager.getMaterial(name);if(material==null)continue;
            var nativeMaterial=new LinkedHashMap<String,Object>();
            nativeMaterial.put("name",material.getRegistryName());nativeMaterial.put("formula",material.getChemicalFormula());
            nativeMaterial.put("color",material.getMaterialRGB());
            nativeMaterial.put("components",material.getMaterialComponents().stream().map(c->Map.of("name",c.material.getRegistryName(),"amount",c.amount)).toList());
            var propertyNames=new TreeSet<String>();
            for(Field declared:PropertyKey.class.getFields())if(Modifier.isStatic(declared.getModifiers())&&declared.getType()==PropertyKey.class
                    &&material.hasProperty((PropertyKey<?>)declared.get(null)))propertyNames.add(declared.get(null).toString());
            nativeMaterial.put("properties",List.copyOf(propertyNames));
            ToolProperty tool=material.getProperty(PropertyKey.TOOL);var toolValues=new TreeMap<String,Object>();
            if(tool!=null)for(String getter:List.of("getToolSpeed","getToolAttackDamage","getToolAttackSpeed","getToolDurability",
                    "getToolHarvestLevel","getToolEnchantability","getShouldIgnoreCraftingTools","getUnbreakable","isMagnetic","getDurabilityMultiplier")) {
                Method method=ToolProperty.class.getMethod(getter);Object actual=method.invoke(tool);
                String fieldName=getter.substring(getter.startsWith("is")?2:3);fieldName=Character.toLowerCase(fieldName.charAt(0))+fieldName.substring(1);
                var scalar=new LinkedHashMap<String,Object>();scalar.put("value",actual);
                if(method.getReturnType()==float.class) {
                    float value=((Number)actual).floatValue();scalar.put("type","float32");
                    scalar.put("rawBits",String.format(java.util.Locale.ROOT,"%08x",Float.floatToRawIntBits(value)));
                    if(!Float.isFinite(value))scalar.put("value",String.valueOf(value));
                } else scalar.put("type",method.getReturnType()==int.class?"int32":"boolean");
                toolValues.put(fieldName,scalar);
            }
            nativeMaterial.put("propertyValues",tool==null?Map.of():Map.of("tool",toolValues));
            materials.add(nativeMaterial); // native setFormula accepts null without formatting
            var property=material.getProperty(PropertyKey.FLUID);var fluid=new LinkedHashMap<String,Object>();
            fluid.put("material",material.getRegistryName());fluid.put("hasFluidProperty",property!=null);
            if(property!=null) {
                Object storage=field(FluidProperty.class,property,"storage");
                Object queued=field(FluidStorageImpl.class,storage,"toRegister");
                fluid.put("queuedKeys",queued==null?List.of():((Map<?,?>)queued).keySet().stream()
                        .map(k->((FluidStorageKey)k).getResourceLocation().toString()).sorted().toList());
                fluid.put("storedCount",((Map<?,?>)field(FluidStorageImpl.class,storage,"map")).size());
                fluid.put("registrationCompleted",field(FluidStorageImpl.class,storage,"registered"));
            }
            fluids.add(fluid);
        }
        result.put("materials",materials);result.put("fluids",fluids);
        result.put("contentPhase",content.phase);
        if(content.constructed) {
            result.put("counts",Map.of("variants",MetaItem.getMetaItems().stream().mapToInt(i->i.getAllItems().size()).sum(),
                "registeredBlocks",content.blocks.stream().filter(b->ForgeRegistries.BLOCKS.getValue(b.getRegistryName())==b).count(),
                "registeredBlockItems",content.blocks.stream().filter(b->Item.func_150898_a(b) instanceof MaterialItemBlock).count(),
                "registeredOreBlocks",NativeOreDeclarations.ORES.stream().filter(b->ForgeRegistries.BLOCKS.getValue(b.getRegistryName())==b).count(),
                "registeredOreItems",NativeOreDeclarations.ORES.stream().filter(b->Item.func_150898_a(b) instanceof OreItemBlock).count()));
            var queues=new TreeMap<String,List<String>>();
            for(OrePrefix prefix:OrePrefix.values()) queues.put(prefix.name(),((Set<?>)field(OrePrefix.class,prefix,"generatedMaterials")).stream()
                    .map(value->((Material)value).getRegistryName()).sorted().toList());
            result.put("prefixQueues",queues);
        }
        result.put("nativeErrors",GroovyLogImpl.LOG.collectErrors());
        result.put("lateRegistered",manager.getMaterial("developer_too_late")!=null);
        result.put("coverageGaps",NativeBoundary.gaps());
        result.put("log",Files.readString(GroovyLogImpl.LOG.getLogFilePath()));
        logRoot.removeAppender(appender);appender.stop();result.put("log4j",Files.readString(log4jFile));
        result.put("originalCompilerWithoutHook",!guarded);result.put("productionObserversUsed",false);
        if(cache!=null)result.put("compiledCache",cache.observe());
        if(argumentBytecode!=null) {
            result.put("compilerBytecode",argumentBytecode.observations());
            result.put("compilerTargetBytecode",GroovyScript.getSandbox().getEngine().getConfig().getTargetBytecode());
        }
        if(mode.equals("trait-reference"))result.put("traitDefinitions",MaterialTraitReference.observations());
        return result;
    }
    /** Direct post-failure native observations; does not invoke the production
     * graph collector, diagnostic appender or source-navigation projection. */
    private static Map<String,Object> originalExceptionGraph(Throwable root,Map<String,String> sources) {
        var values=new ArrayList<Throwable>();values.add(root);
        var ids=new IdentityHashMap<Throwable,Integer>();ids.put(root,0);
        var nodes=new ArrayList<Map<String,Object>>();
        for(int index=0;index<values.size();index++) {
            Throwable value=values.get(index);var node=new LinkedHashMap<String,Object>();
            node.put("type",value.getClass().getName());node.put("message",value.getMessage());
            var frames=new ArrayList<Map<String,Object>>();
            for(StackTraceElement frame:value.getStackTrace()) {
                String name=frame.getClassName(),path=sources.get(name);
                while(path==null&&name.contains("$")) {name=name.substring(0,name.lastIndexOf('$'));path=sources.get(name);}
                if(path!=null&&frame.getLineNumber()>0)frames.add(Map.of("path",path,"line",frame.getLineNumber(),
                    "class",frame.getClassName(),"method",frame.getMethodName(),"precision","native-stack"));
            }
            node.put("locations",frames);
            var children=new ArrayList<Throwable>();if(value.getCause()!=null)children.add(value.getCause());
            children.addAll(Arrays.asList(value.getSuppressed()));
            for(Throwable child:children)if(!ids.containsKey(child)) {ids.put(child,values.size());values.add(child);}
            node.put("cause",value.getCause()==null?null:ids.get(value.getCause()));
            node.put("suppressed",Arrays.stream(value.getSuppressed()).map(ids::get).toList());nodes.add(node);
        }
        return Map.of("schema","axiom.native-diagnostic-causes.v1","root",0,"exceptions",nodes);
    }
    /** Same declared bounded content composition, independently orchestrated without checkpoints.
     * Shared source-built declarations are explicitly NOT an independent content oracle.
     */
    public static final class Content {
        String phase="NOT_STARTED";boolean constructed;List<BlockMaterialBase> blocks=new ArrayList<>();
        void run() {
            phase="CONSTRUCTING";OreDictUnifier.init();
            NativeOreHostBlocks.STONE_BLOCKS.put(StoneVariantBlock.StoneVariant.SMOOTH,new StoneVariantBlock(StoneVariantBlock.StoneVariant.SMOOTH));
            NativeMaterialBlockDeclarations.construct();
            blocks.addAll(NativeMaterialBlockDeclarations.COMPRESSED_BLOCKS);blocks.addAll(NativeMaterialBlockDeclarations.FRAME_BLOCKS);
            NativePrefixItemDeclarations.construct();constructed=true;
            MinecraftForge.EVENT_BUS.register(this);
            try {
                phase="BLOCK_REGISTERING";MinecraftForge.EVENT_BUS.post(new RegistryEvent.Register<Block>(new ResourceLocation("minecraft:block"),ForgeRegistries.BLOCKS));
                phase="REGISTERING";MinecraftForge.EVENT_BUS.post(new RegistryEvent.Register<Item>(new ResourceLocation("minecraft:item"),ForgeRegistries.ITEMS));
                phase="ORE_REGISTERING";NativePrefixItemDeclarations.registerOres();NativeMaterialBlockDeclarations.registerOres();NativeOreDeclarations.registerOres();
                phase="COMPLETE";
            } finally { MinecraftForge.EVENT_BUS.unregister(this); }
        }
        @SubscribeEvent public void blocks(RegistryEvent.Register<Block> event) {
            NativeOreDeclarations.generate();NativeMaterialBlockDeclarations.registerBlocks(event.getRegistry());NativeOreDeclarations.registerBlocks(event.getRegistry());
        }
        @SubscribeEvent public void items(RegistryEvent.Register<Item> event) {
            NativePrefixItemDeclarations.register(event.getRegistry());NativeMaterialBlockDeclarations.registerItems(event.getRegistry());NativeOreDeclarations.registerItems(event.getRegistry());
        }
    }
}
