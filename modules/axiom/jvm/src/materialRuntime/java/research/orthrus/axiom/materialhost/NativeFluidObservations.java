package research.orthrus.axiom.materialhost;

import java.util.*;
import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;

/** Reads existing storage/registry bindings; never creates or registers a fluid. */
final class NativeFluidObservations {
    private NativeFluidObservations() {}

    static Map<String,Object> storage(Object property) throws ReflectiveOperationException {
        Object storage = field("gregtech.api.unification.material.properties.FluidProperty", property, "storage");
        String owner = "gregtech.api.fluids.store.FluidStorageImpl";
        Object primary = call(property, "getPrimaryKey"), pending = field(owner, storage, "toRegister");
        var queued = new ArrayList<Map<String,Object>>();
        if (pending != null) for (var entry : ((Map<?,?>)pending).entrySet()) {
            Object key = entry.getKey(), builder = entry.getValue();
            var attributes = new ArrayList<String>();
            for (Object attribute : (Collection<?>)field("gregtech.api.fluids.FluidBuilder", builder, "attributes"))
                attributes.add(call(attribute, "getResourceLocation").toString());
            queued.add(Map.of("key", call(key, "getResourceLocation").toString(),
                    "priority", call(key, "getRegistrationPriority"), "builderClass", builder.getClass().getName(),
                    "temperature", field("gregtech.api.fluids.FluidBuilder", builder, "temperature"), "attributes", attributes));
        }
        queued.sort(Comparator.comparing(row -> (String)row.get("key")));
        var stored = new ArrayList<Map<String,Object>>();
        for (var entry : ((Map<?,?>)field(owner, storage, "map")).entrySet()) {
            Object key = entry.getKey(), fluid = entry.getValue(); String name = (String)call(fluid, "getName");
            stored.add(Map.of("key", call(key, "getResourceLocation").toString(), "fluid", name,
                    "fluidClass", fluid.getClass().getName(),
                    "storageLookupIdentity", call(property, "get", type("gregtech.api.fluids.store.FluidStorageKey"), key) == fluid,
                    "fluidRegistryIdentity", callStatic("net.minecraftforge.fluids.FluidRegistry", "getFluid", String.class, name) == fluid));
        }
        stored.sort(Comparator.comparing(row -> (String)row.get("key")));
        return Map.of("registrationCompleted", field(owner, storage, "registered"),
                "primaryKey", primary == null ? "" : call(primary, "getResourceLocation").toString(),
                "pendingQueuePresent", pending != null, "queued", queued, "stored", stored);
    }

    static boolean completed(Map<String,Object> values) {
        if (!Boolean.TRUE.equals(values.get("registrationCompleted"))
                || !Boolean.FALSE.equals(values.get("pendingQueuePresent"))) return false;
        var stored = (List<?>)values.get("stored");
        return !stored.isEmpty() && stored.stream().anyMatch(value -> ((Map<?,?>)value).get("key").equals(values.get("primaryKey")))
                && stored.stream().allMatch(value -> Boolean.TRUE.equals(((Map<?,?>)value).get("storageLookupIdentity"))
                        && Boolean.TRUE.equals(((Map<?,?>)value).get("fluidRegistryIdentity")));
    }
}
