package research.orthrus.axiom.materialtest;

import com.cleanroommc.groovyscript.GroovyScript;
import com.cleanroommc.groovyscript.sandbox.*;
import com.cleanroommc.groovyscript.sandbox.meta.GrSMetaClassCreationHandle;
import com.cleanroommc.groovyscript.sandbox.mapper.GroovyDeobfMapper;
import com.cleanroommc.groovyscript.sandbox.expand.ExpansionHelper;
import gregtech.api.GregTechAPI;
import gregtech.api.unification.material.*;
import gregtech.api.unification.material.event.*;
import gregtech.api.unification.material.properties.*;
import gregtech.api.unification.material.registry.MarkerMaterialRegistry;
import gregtech.core.unification.material.internal.MaterialRegistryManager;
import gregtech.integration.groovy.GroovyMaterialBuilderExpansion;
import research.orthrus.axiom.materialhost.NativeBoundary;
import research.orthrus.axiom.materialhost.NativeMaterialContent;
import research.orthrus.axiom.materialhost.MaterialCallGate;
import research.orthrus.axiom.materialhost.MaterialBytecodeGate;
import groovy.lang.*;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.thread.SidedThreadGroups;
import net.minecraftforge.fml.relauncher.FMLInjectionData;
import java.io.*;
import java.nio.file.*;
import java.lang.reflect.*;
import java.util.*;

/** Complete fixed-corpus execution discovery. Not an installed validity service. */
public final class GroovyLanguageProbe {
    private static void check(boolean value,String reason) { if (!value) throw new AssertionError(reason); }
    private static void field(Class<?> type,Object receiver,String name,Object value) throws Exception {
        Field field=type.getDeclaredField(name); field.setAccessible(true); field.set(receiver,value);
    }
    public static Object run(String home,String mode,Map<String,String> sourceClasses,Map<String,Object> admissionPolicy) throws Exception {
        var task=new java.util.concurrent.FutureTask<Object>(() -> executeContext(home,mode,sourceClasses,admissionPolicy));
        Thread context=new Thread(SidedThreadGroups.SERVER,task,"axiom-material-context");
        context.start();
        return task.get();
    }
    private static Object executeContext(String home,String mode,Map<String,String> sourceClasses,Map<String,Object> admissionPolicy) throws Exception {
        File root=new File(home);
        Files.createDirectories(root.toPath().resolve("logs"));
        // Previously source-qualified bounded vanilla identity prefix, not the
        // game bootstrap entry point. The same objects feed catalog and Groovy.
        Class.forName("net.minecraft.init.Bootstrap",true,Launch.classLoader)
                .getMethod("axiom$materialIdentities").invoke(null);
        Class.forName("net.minecraft.init.Enchantments",true,Launch.classLoader);
        Class.forName("net.minecraftforge.fluids.FluidRegistry",true,Launch.classLoader);
        // Explicit standalone paths for original cache/log/script code. Do not
        // invoke FMLInjectionData.build, which also starts unrelated mod configs.
        field(FMLInjectionData.class,null,"minecraftHome",root);
        // GroovyScriptCore injects SandboxData first; the original script mod
        // container below initializes RunConfig once from those paths.
        SandboxData.initialize(root,GroovyScript.LOGGER);
        Method sideConfig=Class.forName("com.cleanroommc.groovyscript.core.SideOnlyConfig").getDeclaredMethod("init");
        sideConfig.setAccessible(true);sideConfig.invoke(null);
        var loader=Loader.instance();
        Field controller=Loader.class.getDeclaredField("modController");controller.setAccessible(true);
        check(controller.get(loader)==null,"No FML/game startup");
        controller.set(loader,new LoadController(loader));
        var metadata=new ModMetadata();metadata.modId="gregtech";metadata.name="GregTech";
        var gtOwner=new DummyModContainer(metadata);
        var scriptOwner=new ScriptModContainer();
        field(Loader.class,loader,"namedMods",Map.of("gregtech",gtOwner,scriptOwner.getModId(),scriptOwner));
        loader.setActiveModContainer(gtOwner);
        GroovySystem.getMetaClassRegistry().setMetaClassCreationHandle(GrSMetaClassCreationHandle.INSTANCE);
        GroovySystem.getMetaClassRegistry().getMetaClassCreationHandler().setDisableCustomMetaClassLookup(true);
        GroovyDeobfMapper.init();
        check("field_110626_a".equals(GroovyDeobfMapper.getObfuscatedFieldName(net.minecraft.util.ResourceLocation.class,"namespace")),
                "Exact selected namespace field mapping loaded");
        field(GroovyScript.class,null,"sandbox",new GroovyScriptSandbox());
        var candidateBytecode=new GroovyCandidateBytecode();
        if(mode.equals("bytecode-audit"))GroovyScript.getSandbox().getEngine().getConfig().setBytecodePostprocessor(candidateBytecode);
        if(mode.equals("guarded")||mode.equals("cache-guarded")) {
            MaterialCallGate.bind(sourceClasses.keySet(),admissionPolicy);
            GroovyScript.getSandbox().getEngine().getConfig().setBytecodePostprocessor(new MaterialBytecodeGate());
        }
        if(mode.startsWith("cache-"))return GroovyCandidateBytecode.cacheWitness(GroovyScript.getSandbox().getEngine(),mode.equals("cache-guarded"));
        if(!mode.equals("missing-expansion")) ExpansionHelper.mixinClass(Material.Builder.class,GroovyMaterialBuilderExpansion.class);
        // CoreModule's constructor supplies the manager before Groovy preInit.
        // Its preInit creates the marker registry and registers GT materials only
        // AFTER scripts have loaded their classes and registered their listeners.
        GregTechAPI.materialManager=MaterialRegistryManager.getInstance();
        var manager=MaterialRegistryManager.getInstance();
        check(manager.getDefaultRegistry().getAllMaterials().isEmpty()&&GregTechAPI.markerMaterialRegistry==null,
                "No eager material population before script initialization");
        var observation=new LinkedHashMap<String,Object>();
        boolean executionCompleted=false;
        try(var logs=mode.equals("no-audit")?null:new GroovyNativeObservations()) {
            try {
                observation.put("stage","script-load");
                GroovyScript.getRunConfig().initPackmode();
                GroovyScript.runGroovyScriptsInLoader(LoadStage.PRE_INIT);
                check(manager.getDefaultRegistry().getAllMaterials().isEmpty()&&GregTechAPI.markerMaterialRegistry==null,
                        "Script initialization did not prematurely run GT producers");
                observation.put("scriptInitializationBeforeCatalog",true);
                // Native GroovyScript logs do not automatically stop FML preInit.
                // Retain that continuation, while separately disqualifying errors.
                observation.put("stage","registry-event");
                GregTechAPI.markerMaterialRegistry=MarkerMaterialRegistry.getInstance();
                MinecraftForge.EVENT_BUS.post(new MaterialRegistryEvent());
                manager.unfreezeRegistries();
                var materialEvent=new MaterialEvent();
                observation.put("stage","native-gt-catalog");
                Materials.register();
                check(manager.getDefaultRegistry().getAllMaterials().size()==602,"Full native GT catalog");
                manager.getDefaultRegistry().setFallbackMaterial(Materials.Aluminium);
                observation.put("stage","material-event");
                MinecraftForge.EVENT_BUS.post(materialEvent);
                manager.closeRegistries();
                observation.put("stage","post-material-event");
                MinecraftForge.EVENT_BUS.post(new PostMaterialEvent());
                manager.freezeRegistries();
                observation.put("stage","frozen-materials");
                var content=new NativeMaterialContent();
                observation.put("stage","native-material-content");
                content.execute();
                var developerMaterials=new ArrayList<Material>();
                for(String name:List.of("developer_aluminosilicate","developer_phosphate","developer_titanate")) {
                    Material material=manager.getMaterial(name);
                    if(material!=null)developerMaterials.add(material);
                }
                observation.put("prefixItems",content.observe(developerMaterials));
                observation.put("nativeBaseForms",content.observe(List.of(Materials.Iron,Materials.Diamond)));
                observation.put("materialBlocks",content.observeBlocks(developerMaterials));
                observation.put("nativeBaseBlocks",content.observeBlocks(List.of(Materials.Iron,Materials.Diamond)));
                observation.put("materialOres",content.observeOres(developerMaterials));
                executionCompleted=true;
            } catch(Throwable nativeFailure) {
                observation.put("nativeException",GroovyNativeObservations.trace(nativeFailure));
            }
            observation.put("nativeMessages",logs==null?List.of():logs.messages());
        }
        List<String> errors=GroovyLogImpl.LOG.collectErrors();
        var materials=new ArrayList<Map<String,Object>>();
        var missing=new ArrayList<String>();
        for(String name:List.of("developer_aluminosilicate","developer_phosphate","developer_titanate")) {
            Material value=manager.getMaterial(name);
            if(value==null) {missing.add(name);continue;}
            materials.add(Map.of("name",value.getRegistryName(),"formula",value.getChemicalFormula(),"color",value.getMaterialRGB(),
                    "components",value.getMaterialComponents().stream().map(s->Map.of("name",s.material.getRegistryName(),"amount",s.amount)).toList()));
        }
        var index=GroovyNativeObservations.scriptIndex(GroovyScript.getSandbox().getEngine());
        boolean skipped=index.stream().anyMatch(row->row.get("preprocessorCheckFailed").equals(true));
        boolean loggedError=((List<Map<String,Object>>)observation.get("nativeMessages")).stream()
                .anyMatch(row->Set.of("ERROR","FATAL").contains(row.get("level")));
        observation.put("executionCompleted",executionCompleted);
        observation.put("cleanObservation",executionCompleted&&errors.isEmpty()&&!loggedError&&!skipped&&missing.isEmpty()
                &&NativeBoundary.gaps().isEmpty()&&MaterialCallGate.violations().isEmpty()
                &&!MaterialCallGate.resourceFailure()&&!MaterialCallGate.linkageFailure());
        observation.put("scriptIndex",index);observation.put("nativeErrors",errors);
        observation.put("candidateBytecode",candidateBytecode.observations());
        observation.put("candidateAdmissionViolations",MaterialCallGate.violations());
        observation.put("candidateResourceFailure",MaterialCallGate.resourceFailure());
        observation.put("candidateLinkageFailure",MaterialCallGate.linkageFailure());
        observation.put("candidateResourceCause",MaterialCallGate.resourceCause());
        observation.put("candidateCaughtCause",MaterialCallGate.caughtCause());
        observation.put("candidateDispatchObservations",MaterialCallGate.observations());
        observation.put("coverageGaps",NativeBoundary.gaps());observation.put("materials",materials);observation.put("missingMaterials",missing);
        observation.put("registeredMaterials",manager.getDefaultRegistry().getAllMaterials().size());observation.put("phase",manager.getPhase().name());
        observation.put("lateRegistered",manager.getMaterial("developer_too_late")!=null);
        observation.put("effectiveSide",FMLCommonHandler.instance().getEffectiveSide().name());
        observation.put("physicalSide",net.minecraftforge.fml.relauncher.FMLLaunchHandler.side().name());
        observation.put("singleNativeIdentity",Material.class.getClassLoader()==Launch.classLoader);
        observation.put("log",Files.readString(GroovyLogImpl.LOG.getLogFilePath()));
        observation.put("generatedFormsQualified",false);observation.put("validityQualified",false);
        return observation;
    }
}
