package research.orthrus.axiom.materialhost;

import java.util.*;
import java.nio.file.*;
import java.security.MessageDigest;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.common.config.ConfigManager;
import net.minecraftforge.common.config.Configuration;
import net.minecraftforge.common.config.Config;
import net.minecraftforge.fml.common.discovery.ASMDataTable;
import net.minecraftforge.fml.common.discovery.asm.ASMModParser;
import com.cleanroommc.groovyscript.api.GroovyPlugin;
import com.cleanroommc.groovyscript.compat.mods.*;
import supersymmetry.integration.groovyscript.GrSModule;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.eventhandler.*;
import java.lang.reflect.*;

/** Pack-context dependencies. This is not a replacement addon lifecycle. */
public final class NativePackMaterialContext {
    private NativePackMaterialContext() {}

    public static boolean selected(String context) {
        return switch(context) {
            case "supersymmetry:material-authoring-gt-base" -> false;
            case "supersymmetry:material-authoring-pack" -> true;
            default -> throw new IllegalArgumentException("Unknown native material context: "+context);
        };
    }

    public static Map<String,Object> initializeGregTech(Path home,NativeInitializationTrace trace) throws Exception {
        trace.begin("gregtech-configuration");
        var result=loadConfiguration(home,"gregtech","gregtech.common.ConfigHolder","config/gregtech/gregtech.cfg");
        result.put("specialEvents",gregtech.common.ConfigHolder.misc.specialEvents);
        result.put("generateLowQualityGems",gregtech.common.ConfigHolder.recipes.generateLowQualityGems);
        result.put("allUniqueStoneTypes",gregtech.common.ConfigHolder.worldgen.allUniqueStoneTypes);
        trace.returned("gregtech-configuration");
        return result;
    }

    private static Map<String,Object> loadConfiguration(Path home,String modId,String owner,String relativePath) throws Exception {
        Path file=home.resolve(relativePath);
        byte[] before=Files.isRegularFile(file)?Files.readAllBytes(file):null;
        var table=new ASMDataTable();
        // FML's original annotation parser and load/sync sequence. The complete
        // original class owns defaults, validation, nested categories and writes
        // to the disposable worker copy. No per-setting host assignments.
        try(var bytes=Launch.classLoader.getResourceAsStream(owner.replace('.','/')+".class")) {
            if(bytes==null)throw new IllegalStateException("Original configuration absent: "+owner);
            new ASMModParser(bytes).sendToTable(table,null);
        }
        ConfigManager.loadData(table);
        ConfigManager.sync(modId,Config.Type.INSTANCE);
        Class<?> type=Class.forName(owner,true,Launch.classLoader);
        if(!Arrays.asList(ConfigManager.getModConfigClasses(modId)).contains(type))
            throw new IllegalStateException("Native configuration registration did not complete: "+owner);
        var configs=ConfigManager.class.getDeclaredField("CONFIGS");configs.setAccessible(true);
        if(!(((Map<?,?>)configs.get(null)).get(file.toAbsolutePath().toString()) instanceof Configuration))
            throw new IllegalStateException("Native configuration was not loaded: "+relativePath);
        var result=new LinkedHashMap<String,Object>();
        result.put("path",relativePath);result.put("inputPresent",before!=null);
        if(before!=null)result.put("inputSha256",digest(before));
        result.put("workerFileSha256",digest(Files.readAllBytes(file)));
        result.put("registrationMethod","native-ASMModParser-ConfigManager.loadData-sync");
        result.put("defaultSource",before==null?"original-native-defaults":"saved-configuration-with-native-defaults");
        result.put("wholePackConfigurationQualified",false);
        return result;
    }

    public static Map<String,Object> initializeSupercritical(Path home,NativeInitializationTrace trace) throws Exception {
        trace.begin("supercritical-configuration");
        var result=loadConfiguration(home,"supercritical","supercritical.common.SCConfigHolder","config/supercritical.cfg");
        var misc=supercritical.common.SCConfigHolder.misc;
        var nuclear=supercritical.common.SCConfigHolder.nuclear;
        result.put("disableAllMaterials",misc.disableAllMaterials);
        result.put("enableMaterialModifications",misc.enableMaterialModifications);
        result.put("disableAllRecipes",misc.disableAllRecipes);
        double divisor=nuclear.fissionCoolantDivisor;
        // Report non-finite native values without losing the entire JSON result.
        // This is serialization only; never normalize or write back the setting.
        result.put("fissionCoolantDivisor",Double.isFinite(divisor)?divisor:Double.toString(divisor));
        result.put("fissionCoolantDivisorRawBits",HexFormat.of().toHexDigits(Double.doubleToRawLongBits(divisor)));
        result.put("susyConstructionExecuted",false);
        result.put("susyConstructionOverrideApplied",false);
        trace.returned("supercritical-configuration");
        return result;
    }

    public static Map<String,Object> initializeGTFO(Path home,NativeInitializationTrace trace) throws Exception {
        trace.begin("gtfo-configuration");
        var result=loadConfiguration(home,"gregtechfoodoption","gregtechfoodoption.GTFOConfig","config/gregtechfoodoption.cfg");
        var food=gregtechfoodoption.GTFOConfig.gtfoOtherFoodModConfig;
        var misc=gregtechfoodoption.GTFOConfig.gtfoMiscConfig;
        result.put("appleCoreCompat",food.appleCoreCompat);
        result.put("constantFoodStatsDivisor",food.constantFoodStatsDivisor);
        result.put("reduceForeignFoodStats",food.reduceForeignFoodStats);
        result.put("nuclearCompat",gregtechfoodoption.GTFOConfig.gtfoncConfig.nuclearCompat);
        result.put("actuallyCompat",gregtechfoodoption.GTFOConfig.gtfoaaConfig.actuallyCompat);
        result.put("unknownSeedsWeight",misc.unknownSeedsWeight);
        result.put("greenhouseDirts",List.copyOf(Arrays.asList(misc.greenhouseDirts)));
        result.put("valueStage","native-config-sync-before-uncomposed-construction-overrides");
        result.put("constructionOverridesExecuted",false);
        trace.returned("gtfo-configuration");
        // GTFOMetaItems creates SHAPED_ITEM in its original <clinit>. Calling
        // its later init() early would also create food/tool items out of phase.
        // The full material callback additionally queries gcys; never turn the
        // standalone Loader's incomplete namedMods map into an absence oracle.
        String gap="material-context.gtfo-mod-discovery-incomplete";
        NativeBoundary.unsupported(gap);
        trace.defer("gtfo-native-construction","gregtechfoodoption.GregTechFoodOption#onStartup",gap);
        trace.defer("gtfo-native-subscriber-registration","gregtechfoodoption.GTFOEventHandler",gap);
        trace.defer("gtfo-native-item-initialization","gregtechfoodoption.item.GTFOMetaItems#init",
                "Later native preInit item/tool initialization; do not move before material callbacks");
        result.put("prerequisites",Map.of("nativeDiscoveryQualified",false,
                "constructionModQueries",List.of("nuclearcraft","actuallyadditions","applecore"),
                "materialCallbackModQueries",List.of("gcys"),"materialSubscriberRegisteredByHost",false,
                "itemInitInvokedByHost",false,"materialCallbackStatus","deferred",
                "reason",gap));
        return result;
    }

    public static Map<String,Object> registerSupercriticalSubscribers(NativeInitializationTrace trace) throws Exception {
        String common="supercritical.common.CommonProxy",events="supercritical.common.SCEventHandlers";
        // Susy's complete construction method is not composed. It forces this
        // field false after its IR calls. Never assign that value ourselves or
        // run SC's true branch as if it were the pack's effective configuration.
        if(supercritical.common.SCConfigHolder.misc.enableMaterialModifications) {
            String gap="material-context.supercritical-construction-override-incomplete";
            NativeBoundary.unsupported(gap);
            trace.defer("supercritical-native-proxy-registration",common,gap);
            trace.defer("supercritical-native-events-registration",events,gap);
            return Map.of("status","deferred","reason",gap,"subscribers",List.of(),
                    "constructionOverrideApplied",false,"discoveryOrderQualified",false);
        }
        trace.declare("supercritical-native-proxy-registration","hook",common);
        trace.declare("supercritical-native-events-registration","hook",events);
        var proxy=registerSubscriber(common,"supercritical","Supercritical",
                "supercritical-native-proxy-registration",null,trace);
        var callbacks=registerSubscriber(events,"supercritical","Supercritical",
                "supercritical-native-events-registration",null,trace);
        return Map.of("status","registered","subscribers",List.of(proxy,callbacks),
                "constructionOverrideApplied",false,"effectiveFalseConditionObserved",true,
                "compositionOrderBasis","explicit GCYM then Supercritical then Susy; peer discovery order unqualified",
                "discoveryOrderQualified",false);
    }

    public static Map<String,Object> inspectElement() throws Exception {
        // Define, but do not initialize the catalog or make synthetic elements.
        Class<?> element=Class.forName("gregtech.api.unification.Element",false,Launch.classLoader);
        Class<?> extension=Class.forName("supercritical.api.unification.ElementExtension",false,Launch.classLoader);
        var rows=NativeTransformAudit.observations().getOrDefault(element.getName(),List.of());
        String required="supercritical.mixins.gregtech.MixinElement";
        if(rows.isEmpty()||!extension.isAssignableFrom(element)
                ||!((List<?>)rows.getLast().get("mergedMixins")).contains(required))
            throw new IllegalStateException("Required original Supercritical element mixin was not applied");
        Class<?> prefix=Class.forName("gregtech.api.unification.ore.OrePrefix",false,Launch.classLoader);
        Class<?> prefixExtension=Class.forName("supercritical.api.unification.ore.OrePrefixExtension",false,Launch.classLoader);
        String prefixMixin="supercritical.mixins.gregtech.MixinOrePrefix";
        var prefixRows=NativeTransformAudit.observations().getOrDefault(prefix.getName(),List.of());
        if(prefixRows.isEmpty()||!prefixExtension.isAssignableFrom(prefix)
                ||!((List<?>)prefixRows.getLast().get("mergedMixins")).contains(prefixMixin))
            throw new IllegalStateException("Required original Supercritical ore-prefix mixin was not applied");
        return Map.of("selected",true,"elementMixinObserved",true,"requiredMixin",required,
                "orePrefixMixinObserved",true,"requiredOrePrefixMixin",prefixMixin,
                "wholeInstalledMixinSet",false,"materialCallbacksExecuted",false);
    }

    public static Map<String,Object> registerSusySubscriber(NativeInitializationTrace trace) throws Exception {
        return registerSubscriber("supersymmetry.common.CommonProxy","susy","Supersymmetry",
                "susy-native-subscriber-registration",null,trace);
    }

    public static Map<String,Object> registerGCYMSubscriber(NativeInitializationTrace trace) throws Exception {
        String name="gregicality.multiblocks.common.GCYMEventHandlers";
        String required="supersymmetry.mixins.gcym.GCYMEventHandlersMixin";
        var result=new LinkedHashMap<String,Object>(registerSubscriber(name,"gcym","Gregicality Multiblocks",
                "gcym-native-subscriber-registration",required,trace));
        result.put("requiredMixin",required);result.put("eventMixinObserved",true);
        result.put("compositionOrderBasis","Susy @Mod required-after:gcym; explicit bounded registration");
        return result;
    }

    private static Map<String,Object> registerSubscriber(String name,String modId,String modName,
            String step,String requiredMixin,NativeInitializationTrace trace) throws Exception {
        trace.begin(step);
        // Native EventBus scans the COMPLETE original class, including linkage
        // of declared parameter types. Do not extract/replace two callbacks.
        Class<?> subscriber=Class.forName(name,false,Launch.classLoader);
        if(requiredMixin!=null) {
            var rows=NativeTransformAudit.observations().getOrDefault(name,List.of());
            if(rows.isEmpty()||!((List<?>)rows.getLast().get("mergedMixins")).contains(requiredMixin))
                throw new IllegalStateException("Required original event mixin was not applied: "+requiredMixin);
        }
        var loader=Loader.instance();
        var previous=loader.activeModContainer();
        var metadata=new ModMetadata();metadata.modId=modId;metadata.name=modName;
        var owner=new DummyModContainer(metadata);
        try {
            loader.setActiveModContainer(owner);
            MinecraftForge.EVENT_BUS.register(subscriber);
        } finally {loader.setActiveModContainer(previous);}
        // Registration can log and swallow an individual failure. Observe the
        // actual native listener list, not merely return from register().
        Field listeners=EventBus.class.getDeclaredField("listeners");listeners.setAccessible(true);
        Object registered=((Map<?,?>)listeners.get(MinecraftForge.EVENT_BUS)).get(subscriber);
        if(!(registered instanceof List<?> actual))throw new IllegalStateException("Original subscriber has no native listeners: "+name);
        var declarations=new ArrayList<Map<String,Object>>();
        for(Method method:subscriber.getMethods()) {
            SubscribeEvent annotation=method.getAnnotation(SubscribeEvent.class);
            if(annotation==null||!Modifier.isStatic(method.getModifiers()))continue;
            String descriptor=org.objectweb.asm.Type.getMethodDescriptor(method);
            String suffix=" "+method.getName()+descriptor;
            if(actual.stream().filter(value->value.toString().contains(suffix)).count()!=1)
                throw new IllegalStateException("Native handler registration differs: "+name+"#"+method.getName());
            declarations.add(Map.of("method",method.getName(),"descriptor",descriptor,
                    "event",method.getParameterTypes()[0].getName(),"priority",annotation.priority().name()));
        }
        if(actual.size()!=declarations.size())throw new IllegalStateException("Native listener count differs: "+name);
        // Sorting is presentation only. EventBus/ListenerList keep actual order.
        declarations.sort(Comparator.comparing(row->(String)row.get("method")));
        trace.returned(step);
        return Map.of("subscriber",subscriber.getName(),"registrantOwner",owner.getModId(),
                "activeOwnerRestored",loader.activeModContainer()==previous,
                "handlers",List.copyOf(declarations),"registrationMethod","native-EventBus.register-complete-class",
                "discoveryOrderQualified",false,"otherLifecycleMethodsInvoked",false);
    }

    public static Map<String,Object> eventDispatch(Event event) throws ReflectiveOperationException {
        Field id=EventBus.class.getDeclaredField("busID");id.setAccessible(true);
        var sequence=new ArrayList<Map<String,Object>>();
        for(IEventListener listener:event.getListenerList().getListeners(id.getInt(MinecraftForge.EVENT_BUS))) {
            var row=new LinkedHashMap<String,Object>();
            row.put("index",sequence.size());row.put("class",listener.getClass().getName());
            if(listener instanceof EventPriority priority)row.put("priority",priority.name());
            if(listener instanceof ASMEventHandler handler) {
                row.put("handler",handler.toString());row.put("priority",handler.getPriority().name());
            }
            sequence.add(row);
        }
        return Map.of("event",event.getClass().getName(),"listeners",List.copyOf(sequence),
                "scope","native-dispatch-cache-before-post-not-completed-invocations", "discoveryOrderQualified",false);
    }

    public static Map<String,Object> initializeGroovy(Path home,NativeInitializationTrace trace) throws Exception {
        trace.begin("sussypatches-configuration");
        Path file=home.resolve("config/sussypatches.cfg");
        if(!Files.isRegularFile(file))throw new IllegalStateException("Pack material context requires saved config/sussypatches.cfg");
        byte[] before=Files.readAllBytes(file);
        // Original SusConfig initializes its complete nested configuration and
        // invokes the pinned ConfigAnytime registrar, which calls Cleanroom's
        // original parser and ConfigManager.sync. Do not assign recipeInfo here.
        trace.begin("sussypatches-native-registration");
        Class<?> type=Class.forName("dev.tianmi.sussypatches.common.SusConfig",true,Launch.classLoader);
        trace.returned("sussypatches-native-registration");
        if(!Arrays.asList(ConfigManager.getModConfigClasses("sussypatches")).contains(type))
            throw new IllegalStateException("Original SussyPatches configuration registration did not complete");
        var configs=ConfigManager.class.getDeclaredField("CONFIGS");configs.setAccessible(true);
        var loaded=((Map<?,?>)configs.get(null)).get(file.toAbsolutePath().toString());
        if(!(loaded instanceof Configuration cfg)||!cfg.hasKey("general.apis","Enable Recipe Info"))
            throw new IllegalStateException("Native SussyPatches configuration was not applied");
        Object api=type.getField("API").get(null);
        boolean enabled=api.getClass().getField("recipeInfo").getBoolean(api);
        var property=cfg.getCategory("general.apis").get("Enable Recipe Info");
        if(enabled!=property.getBoolean())
            throw new IllegalStateException("Native recipeInfo field differs from synchronized configuration");
        trace.returned("sussypatches-configuration");
        var gtMappers=NativeRecipeContext.initializeGroovy(trace);
        GroovyPlugin plugin=new GrSModule();
        // Same default as ModSupport.registerContainer(GroovyPlugin). The
        // original SuSy plugin inherits the nullable factory implementation.
        GroovyPropertyContainer properties=plugin.createGroovyPropertyContainer();
        if(properties==null)properties=new GroovyPropertyContainer();
        var constructor=ExternalModContainer.class.getDeclaredConstructor(GroovyPlugin.class,GroovyPropertyContainer.class);
        constructor.setAccessible(true);
        var container=(GroovyContainer<?>)constructor.newInstance(plugin,properties);
        // The ORIGINAL callback owns both the condition and the expansions.
        trace.begin("susy-groovy-compatibility");
        plugin.onCompatLoaded(container);
        trace.returned("susy-groovy-compatibility");
        return Map.of("configurationClass",type.getName(),"path","config/sussypatches.cfg",
                "inputSha256",digest(before),"workerFileSha256",digest(Files.readAllBytes(file)),
                "recipeInfo",enabled,"callback",GrSModule.class.getName()+"#onCompatLoaded",
                "recipeInfoProperty",Map.of("value",property.getString(),"isBooleanValue",property.isBooleanValue()),
                "gtObjectMappers",gtMappers,
                "application","observed-sussypatches-only","wholePackConfigurationQualified",false);
    }
    private static String digest(byte[] raw) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(raw));
    }
}
