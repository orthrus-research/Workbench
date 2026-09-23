package research.orthrus.axiom.materialhost;

import gregtech.api.fluids.store.FluidStorageKey;
import gregtech.api.fluids.store.FluidStorageImpl;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.properties.FluidProperty;
import gregtech.api.unification.material.properties.PropertyKey;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.core.unification.material.internal.MaterialRegistryManager;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fml.common.Loader;
import java.lang.reflect.Field;
import java.util.*;

/** Read selected native state. Never drain queues, invoke builders or replay handlers. */
public final class NativeMaterialState {
    private NativeMaterialState() {}

    public static Map<String,Object> registries() {
        try { return NativeRegistrationEffects.registries(); }
        catch(ReflectiveOperationException failure) { throw new IllegalStateException("Original registry observation failed",failure); }
    }

    public static Map<String,Object> checkpoint(String name,List<String> requested) {
        var manager=MaterialRegistryManager.getInstance();
        var owner=Loader.instance().activeModContainer();
        var materials=new ArrayList<Map<String,Object>>();
        for(String identity:requested) {
            Material material=manager.getMaterial(identity);
            if(material!=null&&material.getRegistryName().equals(identity)) {
                var value=new LinkedHashMap<String,Object>();
                value.put("material",identity);value.put("color",material.getMaterialRGB());
                value.put("formula",material.getChemicalFormula());value.put("storageRegistry",material.getRegistry().getModid());
                materials.add(Collections.unmodifiableMap(value)); // the native formula may be null
            }
        }
        return Map.of("checkpoint",name,"phase",manager.getPhase().name(),
                "activeOwner",owner==null?"":owner.getModId(),
                "defaultRegistryMaterials",manager.getDefaultRegistry().getAllMaterials().size(),"materials",materials);
    }

    private static Object field(Class<?> type,Object receiver,String name) throws ReflectiveOperationException {
        Field value=type.getDeclaredField(name);value.setAccessible(true);return value.get(receiver);
    }

    public static Map<String,Object> deferred(List<String> requested) {
        return deferred(requested,true);
    }

    public static Map<String,Object> deferred(List<String> requested,boolean prefixStateAvailable) {
        try {
            var manager=MaterialRegistryManager.getInstance();
            var fluids=new ArrayList<Map<String,Object>>();
            for(String identity:requested) {
                Material material=manager.getMaterial(identity);
                if(material==null||!material.getRegistryName().equals(identity))continue;
                FluidProperty property=material.getProperty(PropertyKey.FLUID);
                var row=new LinkedHashMap<String,Object>();row.put("material",identity);
                row.put("hasFluidProperty",property!=null);
                if(property!=null) {
                    Object storage=field(FluidProperty.class,property,"storage");
                    row.put("registrationCompleted",field(FluidStorageImpl.class,storage,"registered"));
                    row.put("primaryKey",property.getPrimaryKey()==null?"":property.getPrimaryKey().getResourceLocation().toString());
                    Object pending=field(FluidStorageImpl.class,storage,"toRegister");
                    var queued=new ArrayList<Map<String,Object>>();
                    if(pending!=null)for(var entry:((Map<?,?>)pending).entrySet()) {
                        var key=(FluidStorageKey)entry.getKey();
                        var builder=entry.getValue();
                        var attributes=((Collection<?>)field(gregtech.api.fluids.FluidBuilder.class,builder,"attributes")).stream()
                                .map(value->((gregtech.api.fluids.attribute.FluidAttribute)value).getResourceLocation().toString()).toList();
                        queued.add(Map.of("key",key.getResourceLocation().toString(),"priority",key.getRegistrationPriority(),
                                "builderClass",builder.getClass().getName(),
                                "temperature",field(gregtech.api.fluids.FluidBuilder.class,builder,"temperature"),
                                "attributes",attributes));
                    }
                    queued.sort(Comparator.comparing(value->(String)value.get("key")));
                    var stored=new ArrayList<Map<String,Object>>();
                    for(var entry:((Map<?,?>)field(FluidStorageImpl.class,storage,"map")).entrySet())
                        stored.add(Map.of("key",((FluidStorageKey)entry.getKey()).getResourceLocation().toString(),
                                "fluid",((Fluid)entry.getValue()).getName()));
                    stored.sort(Comparator.comparing(value->(String)value.get("key")));
                    row.put("queued",queued);row.put("stored",stored);
                }
                fluids.add(row);
            }
            var prefixes=new ArrayList<Map<String,Object>>();
            if(prefixStateAvailable)for(OrePrefix prefix:OrePrefix.values()) {
                // generatedMaterials is a native HashSet, not execution order.
                var pending=((Set<?>)field(OrePrefix.class,prefix,"generatedMaterials")).stream()
                        .map(value->((Material)value).getRegistryName()).sorted().toList();
                int handlers=((List<?>)field(OrePrefix.class,prefix,"oreProcessingHandlers")).size();
                prefixes.add(Map.of("prefix",prefix.name,"pendingMaterials",pending,"registeredHandlers",handlers));
            }
            prefixes.sort(Comparator.comparing(value->(String)value.get("prefix")));
            // Inspect keys already in the native map; never construct keys to fill a vocabulary.
            var keys=((Map<?,?>)field(FluidStorageKey.class,null,"keys")).values().stream()
                    .map(value->((FluidStorageKey)value).getResourceLocation().toString()).sorted().toList();
            var result=new LinkedHashMap<String,Object>(Map.of("schema","axiom.native-deferred-material-work.v1","phase",manager.getPhase().name(),
                    "fluids",fluids,"fluidStorageKeys",keys,
                    "fluidScope","exact-requested-registered-materials",
                    "collectionOrder","key-sorted-membership-not-processing-order",
                    "fluidRegistrationExecuted",false,"recipeHandlersExecuted",false));
            if(prefixStateAvailable)result.put("prefixProcessing",prefixes);
            result.put("prefixScope",prefixStateAvailable?"all-native-prefix-queues":"not-observed-before-content-construction-checkpoint");
            return result;
        } catch(ReflectiveOperationException|RuntimeException failure) {
            var gap=NativeBoundary.unsupported("material.deferred-state-observation");gap.initCause(failure);throw gap;
        }
    }
}
