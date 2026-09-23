package research.orthrus.axiom.materialhost;

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

/** Native GT-base material program lifecycle. Qualification is reported separately. */
public final class NativeMaterialProgram {
    private static void check(boolean value,String reason) { if (!value) throw new AssertionError(reason); }
    private static void field(Class<?> type,Object receiver,String name,Object value) throws Exception {
        Field field=type.getDeclaredField(name); field.setAccessible(true); field.set(receiver,value);
    }
    public static Object run(String home,List<String> requestedMaterials,Map<String,String> sourceClasses,Set<String> sourceTraits,Map<String,Object> admissionPolicy) throws Exception {
        var trace=initializationTrace(NativePackMaterialContext.selected((String)admissionPolicy.get("context")));
        var task=new java.util.concurrent.FutureTask<Object>(() -> executeContext(home,requestedMaterials,sourceClasses,sourceTraits,admissionPolicy,trace));
        Thread context=new Thread(SidedThreadGroups.SERVER,task,"axiom-material-context");
        context.start();
        try { return task.get(); }
        catch(java.util.concurrent.ExecutionException wrapper) {
            Throwable failure=wrapper.getCause();
            trace.failedRunning(failure);
            MaterialCallGate.caught(failure);
            var stopped=new LinkedHashMap<String,Object>();
            stopped.put("executionCompleted",false);stopped.put("cleanObservation",false);
            stopped.put("initialization",trace.snapshot());
            stopped.put("coverageGaps",List.of("material-context.host-or-observation-failure"));
            stopped.put("diagnostics",List.of(NativeProgramObservations.detachedFailure(failure,sourceClasses)));
            stopped.put("nativeErrors",List.of(failure.getClass().getName()+": "+failure.getMessage()));
            stopped.put("nativeException",NativeProgramObservations.trace(failure));
            stopped.put("candidateAdmissionViolations",MaterialCallGate.violations());
            stopped.put("candidateResourceFailure",MaterialCallGate.resourceFailure());
            stopped.put("candidateLinkageFailure",MaterialCallGate.linkageFailure());
            stopped.put("candidateCaughtCause",MaterialCallGate.caughtCause());
            stopped.put("scriptIndex",List.of());
            stopped.put("scriptIndexObservation","unavailable-after-host-failure");
            stopped.put("validityQualified",false);stopped.put("generatedFormsQualified",false);
            return stopped;
        }
    }
    private static NativeInitializationTrace initializationTrace(boolean pack) {
        var trace=new NativeInitializationTrace();
        trace.declare("native-context-setup","stage",NativeMaterialProgram.class.getName()+"#executeContext");
        if(pack) {
            trace.declare("forge-platform-initialization","stage","net.minecraftforge.fml.server.FMLServerHandler#<init> -> FMLCommonHandler.beginLoading -> MinecraftForge.initialize");
            trace.declare("addon-candidate-discovery","stage",NativeAddonDiscovery.class.getName()+"#inspect");
            trace.declare("gregtech-configuration","stage","net.minecraftforge.common.config.ConfigManager#sync(gregtech)");
            trace.declare("supercritical-configuration","stage","net.minecraftforge.common.config.ConfigManager#sync(supercritical)");
            trace.declare("gtfo-configuration","stage","net.minecraftforge.common.config.ConfigManager#sync(gregtechfoodoption)");
            trace.declare("sussypatches-configuration","stage",NativePackMaterialContext.class.getName()+"#initializeGroovy");
            trace.declare("sussypatches-native-registration","hook","dev.tianmi.sussypatches.common.SusConfig#<clinit>");
            trace.declare("gt-groovy-compatibility","hook","gregtech.integration.groovy.GroovyScriptModule#onCompatLoaded");
            trace.declare("susy-groovy-compatibility","hook","supersymmetry.integration.groovyscript.GrSModule#onCompatLoaded");
            trace.declare("susy-native-subscriber-registration","hook","net.minecraftforge.fml.common.eventhandler.EventBus#register(supersymmetry.common.CommonProxy)");
            trace.declare("gcym-native-subscriber-registration","hook","net.minecraftforge.fml.common.eventhandler.EventBus#register(gregicality.multiblocks.common.GCYMEventHandlers)");
            trace.declare("native-object-mapper-bindings","hook","com.cleanroommc.groovyscript.sandbox.GroovyScriptSandbox#registerBinding");
            trace.defer("addon-material-hooks","native-addon-event-subscribers","Configuration, original mixins, registration order and ownership not yet composed");
        }
        trace.declare("pre-init-scripts","stage","com.cleanroommc.groovyscript.GroovyScript#runGroovyScriptsInLoader(PRE_INIT)");
        trace.declare("material-registry-event","stage","net.minecraftforge.fml.common.eventhandler.EventBus#post(MaterialRegistryEvent)");
        trace.declare("gt-material-catalog","hook","gregtech.api.unification.material.Materials#register");
        trace.declare("material-event","stage","net.minecraftforge.fml.common.eventhandler.EventBus#post(MaterialEvent)");
        trace.declare("post-material-event","stage","net.minecraftforge.fml.common.eventhandler.EventBus#post(PostMaterialEvent)");
        trace.declare("material-freeze","hook","gregtech.core.unification.material.internal.MaterialRegistryManager#freezeRegistries");
        if(pack)trace.defer("generated-material-content","native-pack-block-and-item-registry-handlers","Required addon block/item initialization and generated-content coexistence are not composed");
        else trace.declare("generated-material-content","stage","research.orthrus.axiom.materialhost.NativeMaterialContent#execute");
        trace.defer("fluid-registration-and-prefix-processing","native-fluid-and-recipe-producers","Queues may exist, but native registration and processing are not executed");
        trace.defer("post-init-recipes","com.cleanroommc.groovyscript.GroovyScript#runGroovyScriptsInLoader(POST_INIT)","Recipe initialization dependency closure pending");
        return trace;
    }
    private static Object executeContext(String home,List<String> requestedMaterials,Map<String,String> sourceClasses,Set<String> sourceTraits,Map<String,Object> admissionPolicy,NativeInitializationTrace trace) throws Exception {
        trace.begin("native-context-setup");
        boolean packContext=NativePackMaterialContext.selected((String)admissionPolicy.get("context"));
        if(packContext) NativeBoundary.unsupported("material-context.pack-addon-lifecycle-incomplete");
        File root=new File(home);
        Files.createDirectories(root.toPath().resolve("logs"));
        // Previously source-qualified bounded vanilla identity prefix, not the
        // game bootstrap entry point. The same objects feed catalog and Groovy.
        Class.forName("net.minecraft.init.Bootstrap",true,Launch.classLoader)
                .getMethod("axiom$materialIdentities").invoke(null);
        Class.forName("net.minecraft.init.Enchantments",true,Launch.classLoader);
        Class.forName("net.minecraftforge.fluids.FluidRegistry",true,Launch.classLoader);
        // Pack platform bootstrap already called the original build method,
        // including native early config/version setup. GT-base remains bounded.
        if (packContext) NativePlatformBootstrap.verifyHome(root.toPath());
        else field(FMLInjectionData.class,null,"minecraftHome",root);
        // GroovyScriptCore injects SandboxData first; the original script mod
        // container below initializes RunConfig once from those paths.
        SandboxData.initialize(root,GroovyScript.LOGGER);
        Method sideConfig=Class.forName("com.cleanroommc.groovyscript.core.SideOnlyConfig").getDeclaredMethod("init");
        sideConfig.setAccessible(true);sideConfig.invoke(null);
        var loader=Loader.instance();
        // Host-owned standalone path corresponding to Loader.initializeLoader;
        // do not invoke mod discovery/game startup to establish a directory.
        field(Loader.class,loader,"canonicalConfigDir",new File(root,"config").getCanonicalFile());
        Field controller=Loader.class.getDeclaredField("modController");controller.setAccessible(true);
        check(controller.get(loader)==null,"No FML/game startup");
        controller.set(loader,new LoadController(loader));
        var forgeInitialization = packContext ? NativeForgeInitialization.initialize(root.toPath(),trace) : Map.<String,Object>of();
        var metadata=new ModMetadata();metadata.modId="gregtech";metadata.name="GregTech";
        var gtOwner=new DummyModContainer(metadata);
        var scriptOwner=new ScriptModContainer();
        var languageMetadata=new ModMetadata();languageMetadata.modId="groovyscript";languageMetadata.name="GroovyScript";
        field(Loader.class,loader,"namedMods",Map.of("gregtech",gtOwner,"groovyscript",new DummyModContainer(languageMetadata),scriptOwner.getModId(),scriptOwner));
        loader.setActiveModContainer(gtOwner);
        GroovySystem.getMetaClassRegistry().setMetaClassCreationHandle(GrSMetaClassCreationHandle.INSTANCE);
        GroovySystem.getMetaClassRegistry().getMetaClassCreationHandler().setDisableCustomMetaClassLookup(true);
        GroovyDeobfMapper.init();
        check("field_110626_a".equals(GroovyDeobfMapper.getObfuscatedFieldName(net.minecraft.util.ResourceLocation.class,"namespace")),
                "Exact selected namespace field mapping loaded");
        field(GroovyScript.class,null,"sandbox",new GroovyScriptSandbox());
        NativeRecipeContext.prepare();
        MaterialCallGate.bind(sourceClasses.keySet(),admissionPolicy);
        MaterialTraitClasses.bind(sourceTraits);
        GroovyScript.getSandbox().getEngine().getConfig().setBytecodePostprocessor(new MaterialBytecodeGate());
        ExpansionHelper.mixinClass(Material.Builder.class,GroovyMaterialBuilderExpansion.class);
        // CoreModule's constructor supplies the manager before Groovy preInit.
        // Its preInit creates the marker registry and registers GT materials only
        // AFTER scripts have loaded their classes and registered their listeners.
        GregTechAPI.materialManager=MaterialRegistryManager.getInstance();
        var manager=MaterialRegistryManager.getInstance();
        check(manager.getDefaultRegistry().getAllMaterials().isEmpty()&&GregTechAPI.markerMaterialRegistry==null,
                "No eager material population before script initialization");
        var observation=new LinkedHashMap<String,Object>();
        if(packContext)observation.put("forgeInitialization",forgeInitialization);
        var checkpoints=new ArrayList<Map<String,Object>>();
        boolean executionCompleted=false;
        NativeMaterialContent content=null;
        var logs=new NativeProgramObservations(sourceClasses);
        trace.returned("native-context-setup");
        try(logs) {
            try {
                if(packContext) {
                    observation.put("stage","addon-candidate-discovery");
                    observation.put("addonDiscovery",NativeAddonDiscovery.inspect(root.toPath(),trace));
                    observation.put("stage","gregtech-configuration");
                    observation.put("gregtechConfiguration",NativePackMaterialContext.initializeGregTech(root.toPath(),trace));
                    observation.put("stage","supercritical-configuration");
                    observation.put("supercriticalConfiguration",NativePackMaterialContext.initializeSupercritical(root.toPath(),trace));
                    observation.put("stage","gtfo-configuration");
                    observation.put("gtfoConfiguration",NativePackMaterialContext.initializeGTFO(root.toPath(),trace));
                    observation.put("stage","pack-configuration-and-groovy-integration");
                    observation.put("packConfiguration",NativePackMaterialContext.initializeGroovy(root.toPath(),trace));
                    observation.put("stage","native-addon-subscriber-registration");
                    observation.put("gcymSubscriber",NativePackMaterialContext.registerGCYMSubscriber(trace));
                    observation.put("supercriticalSubscribers",NativePackMaterialContext.registerSupercriticalSubscribers(trace));
                    observation.put("susySubscriber",NativePackMaterialContext.registerSusySubscriber(trace));
                    observation.put("objectMapperBindings",NativeRecipeContext.bindGroovyMappers(trace));
                }
                observation.put("stage","script-load");
                trace.begin("pre-init-scripts");
                checkpoints.add(NativeMaterialState.checkpoint("before-script-load",requestedMaterials));
                GroovyScript.getRunConfig().initPackmode();
                GroovyScript.runGroovyScriptsInLoader(LoadStage.PRE_INIT);
                trace.returned("pre-init-scripts");
                check(manager.getDefaultRegistry().getAllMaterials().isEmpty()&&GregTechAPI.markerMaterialRegistry==null,
                        "Script initialization did not prematurely run GT producers");
                observation.put("scriptInitializationBeforeCatalog",true);
                checkpoints.add(NativeMaterialState.checkpoint("after-script-load",requestedMaterials));
                // Native GroovyScript logs do not automatically stop FML preInit.
                // Retain that continuation, while separately disqualifying errors.
                observation.put("stage","registry-event");
                trace.begin("material-registry-event");
                GregTechAPI.markerMaterialRegistry=MarkerMaterialRegistry.getInstance();
                var registryEvent=new MaterialRegistryEvent();
                if(packContext)observation.put("materialRegistryEventDispatch",NativePackMaterialContext.eventDispatch(registryEvent));
                MinecraftForge.EVENT_BUS.post(registryEvent);
                trace.returned("material-registry-event");
                checkpoints.add(NativeMaterialState.checkpoint("after-registry-event",requestedMaterials));
                manager.unfreezeRegistries();
                checkpoints.add(NativeMaterialState.checkpoint("registration-open",requestedMaterials));
                var materialEvent=new MaterialEvent();
                observation.put("stage","native-gt-catalog");
                trace.begin("gt-material-catalog");
                Materials.register();
                trace.returned("gt-material-catalog");
                check(manager.getDefaultRegistry().getAllMaterials().size()==602,"Full native GT catalog");
                manager.getDefaultRegistry().setFallbackMaterial(Materials.Aluminium);
                checkpoints.add(NativeMaterialState.checkpoint("after-gt-producers",requestedMaterials));
                observation.put("stage","material-event");
                trace.begin("material-event");
                if(packContext)observation.put("materialEventDispatch",NativePackMaterialContext.eventDispatch(materialEvent));
                MinecraftForge.EVENT_BUS.post(materialEvent);
                trace.returned("material-event");
                checkpoints.add(NativeMaterialState.checkpoint("after-material-event",requestedMaterials));
                manager.closeRegistries();
                checkpoints.add(NativeMaterialState.checkpoint("registration-closed",requestedMaterials));
                observation.put("stage","post-material-event");
                trace.begin("post-material-event");
                var postMaterialEvent=new PostMaterialEvent();
                if(packContext)observation.put("postMaterialEventDispatch",NativePackMaterialContext.eventDispatch(postMaterialEvent));
                MinecraftForge.EVENT_BUS.post(postMaterialEvent);
                trace.returned("post-material-event");
                checkpoints.add(NativeMaterialState.checkpoint("after-post-material-event",requestedMaterials));
                trace.begin("material-freeze");manager.freezeRegistries();trace.returned("material-freeze");
                checkpoints.add(NativeMaterialState.checkpoint("registration-frozen",requestedMaterials));
                observation.put("stage","frozen-materials");
                if(packContext) {
                    // The complete native subscriber also owns block/item
                    // handlers. Their prerequisites are separate from materials;
                    // do not post registry events against fabricated addon state.
                    NativeBoundary.unsupported("material-context.pack-generated-content-incomplete");
                } else {
                    content=new NativeMaterialContent();
                    observation.put("stage","native-material-content");
                    trace.begin("generated-material-content");
                    content.execute();
                    trace.returned("generated-material-content");
                    checkpoints.add(NativeMaterialState.checkpoint("after-generated-content",requestedMaterials));
                    var developerMaterials=new LinkedHashSet<Material>();
                    for(String name:requestedMaterials) {
                        Material material=manager.getMaterial(name);
                        if(material!=null)developerMaterials.add(material);
                    }
                    observation.put("prefixItems",content.observe(developerMaterials));
                    observation.put("nativeBaseForms",content.observe(List.of(Materials.Iron,Materials.Diamond)));
                    observation.put("materialBlocks",content.observeBlocks(developerMaterials));
                    observation.put("nativeBaseBlocks",content.observeBlocks(List.of(Materials.Iron,Materials.Diamond)));
                    observation.put("materialOres",content.observeOres(developerMaterials));
                }
                executionCompleted=true;
            } catch(Throwable nativeFailure) {
                trace.failedRunning(nativeFailure);
                MaterialCallGate.caught(nativeFailure);
                logs.exception(nativeFailure);
                observation.put("nativeException",NativeProgramObservations.trace(nativeFailure));
                observation.put("nativeSourceFrames",MaterialDiagnosticCauses.sourceFrames(nativeFailure.getStackTrace(),sourceClasses));
            } finally {
                observation.put("contentProgress",content==null
                        ?Map.of("schema","axiom.native-material-content-progress.v1","phase","NOT_STARTED",
                                "completedCheckpoints",List.of(),"lastCompletedPhase","NONE")
                        :content.progress());
                // Preserve stopped material/queue state only after registration
                // closes. Never initialize unvisited prefix content to fill a report.
                try {
                    if(!executionCompleted)checkpoints.add(NativeMaterialState.checkpoint("at-program-stop",requestedMaterials));
                    if(Set.of("CLOSED","FROZEN").contains(manager.getPhase().name()))
                        observation.put("deferredWork",NativeMaterialState.deferred(requestedMaterials,
                                content!=null&&content.constructedCheckpointReached()));
                } catch(Throwable observationFailure) {
                    executionCompleted=false;
                    NativeBoundary.unsupported("material.stopped-state-observation");
                    logs.exception(observationFailure);
                    observation.put("stateObservationFailure",NativeProgramObservations.trace(observationFailure));
                }
            }
            observation.put("diagnostics",logs.diagnostics());
        }
        observation.put("lifecycle",Map.of("scope","native-host-checkpoints-not-per-listener-trace","checkpoints",List.copyOf(checkpoints)));
        observation.put("initialization",trace.snapshot());
        List<String> errors=GroovyLogImpl.LOG.collectErrors();
        var materials=new ArrayList<Map<String,Object>>();
        var missing=new ArrayList<String>();
        var lookups=new ArrayList<Map<String,Object>>();
        var observed=new HashSet<Material>();
        for(String name:requestedMaterials) {
            Material value=manager.getMaterial(name);
            var lookup=new LinkedHashMap<String,Object>();lookup.put("requested",name);
            lookup.put("exactIdentity",value!=null&&value.getRegistryName().equals(name));
            if(value!=null) {lookup.put("resolved",value.getRegistryName());lookup.put("storageRegistry",value.getRegistry().getModid());}
            lookups.add(lookup);
            if(value==null||!value.getRegistryName().equals(name))missing.add(name);
            if(value!=null&&observed.add(value))materials.add(NativeMaterialObservations.material(value));
        }
        var index=NativeProgramObservations.scriptIndex(GroovyScript.getSandbox().getEngine());
        boolean skipped=index.stream().anyMatch(row->row.get("preprocessorCheckFailed").equals(true));
        boolean loggedError=logs.hasErrors();
        observation.put("executionCompleted",executionCompleted);
        observation.put("cleanObservation",executionCompleted&&errors.isEmpty()&&!loggedError&&!skipped
                &&NativeBoundary.gaps().isEmpty()&&MaterialCallGate.violations().isEmpty()
                &&!MaterialCallGate.resourceFailure()&&!MaterialCallGate.linkageFailure());
        observation.put("scriptIndex",index);observation.put("nativeErrors",errors);
        observation.put("lookups",lookups);observation.put("vocabulary",NativeMaterialObservations.vocabulary(executionCompleted&&content!=null));
        observation.put("nativeCompilationFailure",logs.compilationFailure());
        observation.put("candidateAdmissionViolations",MaterialCallGate.violations());
        observation.put("candidateResourceFailure",MaterialCallGate.resourceFailure());
        observation.put("candidateLinkageFailure",MaterialCallGate.linkageFailure());
        observation.put("candidateResourceCause",MaterialCallGate.resourceCause());
        observation.put("candidateCaughtCause",MaterialCallGate.caughtCause());
        observation.put("candidateDispatchObservations",MaterialCallGate.observations());
        observation.put("candidateDispatchObservationScope","native-members-and-guest-calls; guest-fields-counted-by-owner");
        observation.put("recordCompilations",MaterialCallGate.recordCompilations());
        observation.put("traitDefinitions",MaterialTraitClasses.observations());
        observation.put("traitCompilations",MaterialTraitClasses.compilations());
        observation.put("recipeMaps",NativeRecipeMapObservations.collect());
        observation.put("customMetaItems",NativeMetaItemObservations.collect());
        observation.put("coverageGaps",NativeBoundary.gaps());observation.put("materials",materials);observation.put("missingMaterials",missing);
        observation.put("registeredMaterials",manager.getDefaultRegistry().getAllMaterials().size());observation.put("phase",manager.getPhase().name());
        observation.put("materialRegistries",NativeMaterialState.registries());
        observation.put("registrationEffects",NativeRegistrationEffects.collect());
        if(packContext)observation.put("supercriticalState",NativeSupercriticalObservations.collect(observation.get("supercriticalSubscribers")));
        observation.put("effectiveSide",FMLCommonHandler.instance().getEffectiveSide().name());
        observation.put("physicalSide",net.minecraftforge.fml.relauncher.FMLLaunchHandler.side().name());
        observation.put("singleNativeIdentity",Material.class.getClassLoader()==Launch.classLoader);
        observation.put("log",Files.readString(GroovyLogImpl.LOG.getLogFilePath()));
        observation.put("generatedFormsQualified",false);observation.put("validityQualified",false);
        return observation;
    }
}
