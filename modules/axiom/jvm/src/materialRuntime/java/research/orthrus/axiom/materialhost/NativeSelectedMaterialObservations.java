package research.orthrus.axiom.materialhost;

import java.util.*;
import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;

/** Requested values from the original registry and generated collections after preInit. */
final class NativeSelectedMaterialObservations {
    private static final String PROPERTY = "gregtech.api.unification.material.properties.PropertyKey";
    private static final String PREFIX = "gregtech.api.unification.ore.OrePrefix";
    private static final String STONE = "gregtech.api.unification.ore.StoneType";
    private final List<String> requested, full;
    private final List<Map<String,Object>> checks;
    private final List<Object> materials = new ArrayList<>();
    private final Map<String,Object> values = new LinkedHashMap<>();

    @SuppressWarnings("unchecked")
    NativeSelectedMaterialObservations(Map<String,Object> selection) throws ReflectiveOperationException {
        requested = (List<String>)selection.getOrDefault("materials", List.of());
        full = (List<String>)selection.getOrDefault("fullMaterials", List.of());
        checks = (List<Map<String,Object>>)selection.getOrDefault("expectations", List.of());
        if (requested.isEmpty()) return;
        Object manager = materialManager();
        var rows = new ArrayList<Map<String,Object>>(); var lookups = new ArrayList<Map<String,Object>>();
        var missing = new ArrayList<String>(); var seen = Collections.newSetFromMap(new IdentityHashMap<Object,Boolean>());
        for (String name : requested) {
            Object material = call(manager, "getMaterial", String.class, name);
            var lookup = new LinkedHashMap<String,Object>(); lookup.put("requested", name);
            boolean exact = material != null && materialName(material).equals(name); lookup.put("exactIdentity", exact);
            if (material != null) {
                lookup.put("resolved", materialName(material)); lookup.put("storageRegistry", call(call(material, "getRegistry"), "getModid"));
                if (seen.add(material)) { materials.add(material); rows.add(NativeMaterialObservations.material(material)); }
            }
            if (!exact) missing.add(name); lookups.add(lookup);
        }
        values.put("materials", rows); values.put("lookups", lookups); values.put("missingMaterials", missing);
        values.put("vocabulary", NativeMaterialObservations.vocabulary(true));
    }

    List<Object> formMaterials() throws ReflectiveOperationException {
        var selected = new ArrayList<Object>();
        for (Object material : materials) {
            String name = materialName(material);
            if (full.contains(name) || checks.stream().anyMatch(row -> name.equals(row.get("material")) && "form".equals(row.get("kind"))))
                selected.add(material);
        }
        return selected;
    }

    boolean form(Object material, String family, Object prefix, Object stone, String fact) throws ReflectiveOperationException {
        String name = materialName(material);
        if (full.contains(name)) return true;
        return checks.stream().anyMatch(row -> name.equals(row.get("material")) && "form".equals(row.get("kind"))
                && family.equals(row.get("family")) && Objects.equals(prefix, row.get("prefix"))
                && (stone == null || Objects.equals(stone, row.get("stone"))) && (fact == null || fact.equals(row.get("fact"))));
    }

    @SuppressWarnings("unchecked")
    Map<String,Object> finish(Map<String,Object> generated) throws ReflectiveOperationException {
        if (requested.isEmpty()) return Map.of();
        for (var family : Map.of("prefixItems", "gt-prefix-items", "materialBlocks", "gt-material-blocks").entrySet()) {
            var catalog = (Map<String,Object>)generated.get(family.getKey()); var rows = new ArrayList<Object>();
            for (Object value : (List<?>)catalog.get("witnesses")) {
                var row = (Map<String,Object>)value;
                for (Object material : materials) if (materialName(material).equals(row.get("material"))
                        && form(material, family.getValue(), row.get("prefix"), null, null)) { rows.add(row); break; }
            }
            values.put(family.getKey(), forms(catalog, rows));
        }
        var oreCatalog = (Map<String,Object>)generated.get("oreBlocks"); var oreRows = new ArrayList<Object>();
        for (Object material : materials) for (Object stone : (Iterable<?>)field(STONE, null, "STONE_TYPE_REGISTRY")) {
            Object prefix = field(STONE, stone, "processingPrefix"); String name = materialName(material);
            Object prefixName = call(prefix, "name"), stoneName = field(STONE, stone, "name");
            if (!form(material, "gt-ore-blocks", prefixName, stoneName, null)) continue;
            var variants = new ArrayList<Object>();
            for (Object value : (List<?>)oreCatalog.get("witnesses")) {
                var witness = (Map<String,Object>)value;
                if (!name.equals(witness.get("material"))) continue;
                for (Object item : (List<?>)witness.get("generated")) {
                    var variant = (Map<String,Object>)item;
                    if (!stoneName.equals(variant.get("stone"))) continue;
                    var stack = new LinkedHashMap<>((Map<String,Object>)variant.get("stack"));
                    stack.put("block", witness.get("block")); stack.put("stateRoundTripIdentity", variant.get("stateRoundTripIdentity"));
                    stack.put("propertyRoundTripIdentity", variant.get("propertyRoundTripIdentity"));
                    if (variant.containsKey("ordinaryDrop")) stack.put("ordinaryDrop", variant.get("ordinaryDrop"));
                    variants.add(stack);
                }
            }
            oreRows.add(Map.of("material", name, "prefix", prefixName, "stone", stoneName,
                    "selected", NativeGeneratedContentObservations.stack(NativeGeneratedContentObservations.selectedStack(prefix, material), material, prefix),
                    "generated", variants));
        }
        values.put("materialOres", forms(oreCatalog, oreRows)); values.put("deferredWork", deferred());
        values.put("selectedObservations", Map.of("status", "observed", "requestedMaterials", requested,
                "fullMaterials", full, "scope", "original-selected-values-and-requested-native-facts-after-preinit",
                "meaning", "observation-selectors-do-not-select-source-or-execute-deferred-phases"));
        return values;
    }

    private Map<String,Object> forms(Map<String,Object> catalog, List<Object> rows) {
        boolean complete = "observed".equals(catalog.get("status")) && Boolean.TRUE.equals(catalog.get("inventoryComplete"));
        return Map.of("phase", complete ? "COMPLETE" : "INCOMPLETE", "forms", complete ? rows : List.of(),
                "scope", "original-complete-collection-membership-and-requested-native-getters", "recipeHandlersExecuted", false);
    }

    private Map<String,Object> deferred() throws ReflectiveOperationException {
        var fluids = new ArrayList<Object>();
        for (Object material : materials) {
            String name = materialName(material);
            if (!requested.contains(name) || !full.contains(name) && checks.stream().noneMatch(row -> name.equals(row.get("material")) && "fluid".equals(row.get("kind")))) continue;
            Object property = call(material, "getProperty", type(PROPERTY), field(PROPERTY, null, "FLUID"));
            var row = new LinkedHashMap<String,Object>(); row.put("material", name); row.put("hasFluidProperty", property != null);
            if (property != null) row.putAll(NativeFluidObservations.storage(property)); fluids.add(row);
        }
        var prefixes = new ArrayList<Object>();
        for (Object prefix : (Collection<?>)callStatic(PREFIX, "values")) {
            Object name = call(prefix, "name");
            if (full.isEmpty() && checks.stream().noneMatch(row -> "processing".equals(row.get("kind")) && name.equals(row.get("prefix")))) continue;
            var pending = (Set<?>)field(PREFIX, prefix, "generatedMaterials"); var selected = new ArrayList<String>();
            for (Object material : materials) if (requested.contains(materialName(material)) && pending.contains(material)) selected.add(materialName(material));
            Collections.sort(selected);
            prefixes.add(Map.of("prefix", name, "pendingMaterials", selected,
                    "registeredHandlers", ((List<?>)field(PREFIX, prefix, "oreProcessingHandlers")).size()));
        }
        var keys = new ArrayList<String>();
        for (Object key : ((Map<?,?>)field("gregtech.api.fluids.store.FluidStorageKey", null, "keys")).values())
            keys.add(call(key, "getResourceLocation").toString());
        Collections.sort(keys);
        return Map.of("schema", "axiom.native-deferred-material-work.v1", "phase", call(call(materialManager(), "getPhase"), "name"),
                "fluids", fluids, "fluidStorageKeys", keys, "prefixProcessing", prefixes,
                "prefixScope", "requested-prefixes-and-exact-requested-material-membership",
                "fluidScope", "requested-native-fluid-state", "fluidRegistrationExecuted", false, "recipeHandlersExecuted", false);
    }
}
