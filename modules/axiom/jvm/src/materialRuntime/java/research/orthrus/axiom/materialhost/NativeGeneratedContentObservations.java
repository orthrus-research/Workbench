package research.orthrus.axiom.materialhost;

import java.util.*;
import static research.orthrus.axiom.materialhost.NativeObservationAccess.*;
import static research.orthrus.axiom.materialhost.NativeRegistrationEffects.*;

/** Reads original generated collections after registry events, with requested native
 * eligibility/drop getters. No initializer, registration callback or recipe handler is invoked.
 */
final class NativeGeneratedContentObservations {
    private static final String META_ITEM = "gregtech.api.items.metaitem.MetaItem";
    private static final String PREFIX_ITEM = "gregtech.api.items.materialitem.MetaPrefixItem";
    private static final String META_BLOCKS = "gregtech.common.blocks.MetaBlocks";
    private static final String ORE_BLOCK = "gregtech.common.blocks.BlockOre";
    private static final String MATERIAL = "gregtech.api.unification.material.Material";
    private static final String ORE_PREFIX = "gregtech.api.unification.ore.OrePrefix";
    private static final String UNIFIER = "gregtech.api.unification.OreDictUnifier";
    private static final String STACK = "net.minecraft.item.ItemStack";
    private static final String BLOCK = "net.minecraft.block.Block";
    private static final String STATE = "net.minecraft.block.state.IBlockState";
    private static final String MATERIAL_BLOCK = "gregtech.common.blocks.BlockMaterialBase";
    private NativeGeneratedContentObservations() {}

    static Map<String,Object> collect(List<Object> selected) throws ReflectiveOperationException {
        return collect(selected, new NativeSelectedMaterialObservations(Map.of()));
    }

    static Map<String,Object> collect(List<Object> selected, NativeSelectedMaterialObservations observations) throws ReflectiveOperationException {
        return Map.of("prefixItems", prefixes(selected, observations), "materialBlocks", materialBlocks(selected), "oreBlocks", ores(selected, observations));
    }

    private static Object registry(String name) throws ReflectiveOperationException {
        return field("net.minecraftforge.fml.common.registry.ForgeRegistries", null, name);
    }
    private static Object registered(Object registry, Object name) throws ReflectiveOperationException {
        return call(registry, "getValue", type("net.minecraft.util.ResourceLocation"), name);
    }
    private static String registeredName(Object value, String registry) throws ReflectiveOperationException {
        Object name = virtual("net.minecraftforge.registries.IForgeRegistryEntry", value, "getRegistryName",
                type("net.minecraft.util.ResourceLocation"), new Class<?>[0]);
        require(name != null && registered(registry(registry), name) == value, "Original " + registry + " membership differs: " + name);
        require(value.getClass().getClassLoader() == NativeGeneratedContentObservations.class.getClassLoader(),
                "Original generated class space differs: " + name);
        return name.toString();
    }
    static Object selectedStack(Object prefix, Object material) throws ReflectiveOperationException {
        return callStatic(UNIFIER, "get", new Class<?>[]{type(ORE_PREFIX), type(MATERIAL)}, prefix, material);
    }
    private static Map<String,Object> witnessed(Map<String,Object> catalog, List<Map<String,Object>> witnesses) {
        var result = new LinkedHashMap<>(catalog);
        result.put("witnessScope", "selected-native-materials-original-form-and-unifier-getters");
        result.put("witnesses", "observed".equals(catalog.get("status")) ? List.copyOf(witnesses) : List.of());
        return result;
    }

    private static Map<String,Object> prefixes(List<Object> selected, NativeSelectedMaterialObservations observations) {
        var witnesses = new ArrayList<Map<String,Object>>();
        var viewDifferences = new TreeMap<String,Object>();
        var catalog = observe("native-meta-prefix-item-collections", "original-prefix-item-variants-and-registry-identities-v1", () -> {
            var entries = new Catalog();
            for (Object item : (Collection<?>)callStatic(META_ITEM, "getMetaItems")) {
                if (!type(PREFIX_ITEM).isInstance(item)) continue;
                String name = registeredName(item, "ITEMS");
                Object prefix = call(item, "getOrePrefix"), storage = field(PREFIX_ITEM, item, "registry");
                var registeredMaterials = Collections.newSetFromMap(new IdentityHashMap<Object,Boolean>());
                registeredMaterials.addAll((Collection<?>)call(storage, "getAllMaterials"));
                var numericMaterials = Collections.newSetFromMap(new IdentityHashMap<Object,Boolean>());
                for (Object material : (Iterable<?>)storage) numericMaterials.add(material);
                var variants = new ArrayList<Map<String,Object>>();
                var generated = new IdentityHashMap<Object,List<Map<String,Object>>>();
                for (Object material : selected) generated.put(material, new ArrayList<>());
                for (Object value : (Collection<?>)call(item, "getAllItems")) {
                    int metadata = ((Number)call(value, "getMetaValue")).intValue();
                    Object material = virtual(PREFIX_ITEM, item, "getMaterial", type(MATERIAL), new Class<?>[]{int.class}, metadata);
                    require(material != null && call(value, "getMetaItem") == item && call(material, "getRegistry") == storage,
                            "Original prefix variant material/owner differs: " + name + "#" + metadata);
                    require(numericMaterials.contains(material) && ((Number)call(material, "getId")).intValue() == metadata,
                            "Original prefix variant numeric registry differs: " + name + "#" + metadata);
                    boolean namedIdentity = registeredMaterials.contains(material);
                    if (!namedIdentity) {
                        String key = call(storage, "getModid") + "#" + metadata;
                        if (!viewDifferences.containsKey(key)) {
                            Object named = null; String identity = materialName(material);
                            for (Object candidate : registeredMaterials) if (materialName(candidate).equals(identity)) { named = candidate; break; }
                            viewDifferences.put(key, Map.of("numericMaterial", NativeMaterialObservations.material(material),
                                    "namedMaterial", named == null ? Map.of("status", "absent") : NativeMaterialObservations.material(named)));
                        }
                    }
                    variants.add(Map.of("meta", metadata, "name", field(type(META_ITEM + "$MetaValueItem"), value, "unlocalizedName"),
                            "material", materialName(material), "ownerIdentity", true,
                            "numericRegistryIdentity", true, "namedRegistryIdentity", namedIdentity));
                    if (generated.containsKey(material)) generated.get(material).add(stack(call(value, "getStackForm"), material, prefix));
                }
                variants.sort(Comparator.comparingInt(row -> (Integer)row.get("meta")));
                add(entries, name, Map.of("prefix", call(prefix, "name"), "registry", call(storage, "getModid"),
                        "registryIdentity", true, "offset", field(META_ITEM, item, "metaItemOffset"), "variants", variants));
                for (Object material : selected) if (call(material, "getRegistry") == storage) {
                    var witness = new LinkedHashMap<String,Object>();
                    witness.put("material", materialName(material)); witness.put("prefix", call(prefix, "name"));
                    witness.put("generator", name); witness.put("generated", generated.get(material));
                    witness.put("selected", stack(selectedStack(prefix, material), material, prefix));
                    if (observations.form(material, "gt-prefix-items", call(prefix, "name"), null, "eligible"))
                        witness.put("eligible", call(prefix, "doGenerateItem", type(MATERIAL), material));
                    witnesses.add(witness);
                }
            }
            return entries;
        });
        var result = new LinkedHashMap<>(witnessed(catalog, witnesses));
        result.put("materialRegistryViewDifferences", "observed".equals(catalog.get("status")) ? viewDifferences : Map.of());
        return result;
    }

    private static Map<String,Object> materialBlocks(List<Object> selected) {
        var witnesses = new ArrayList<Map<String,Object>>();
        var catalog = observe("native-material-block-collections", "original-block-item-state-property-and-unifier-identities-v1", () -> {
            var entries = new Catalog();
            for (String family : List.of("COMPRESSED", "FRAMES")) {
                Object prefix = field(ORE_PREFIX, null, family.equals("COMPRESSED") ? "block" : "frameGt");
                var mapping = (Map<?,?>)field(META_BLOCKS, null, family);
                var blocks = (List<?>)field(META_BLOCKS, null, family.equals("COMPRESSED") ? "COMPRESSED_BLOCKS" : "FRAME_BLOCKS");
                var grouped = new IdentityHashMap<Object,List<Map<String,Object>>>();
                var byMaterial = new IdentityHashMap<Object,Map<String,Object>>();
                for (Object block : blocks) grouped.put(block, new ArrayList<>());
                for (var entry : mapping.entrySet()) {
                    Object material = entry.getKey(), block = entry.getValue();
                    require(grouped.containsKey(block), "Native mapped material block is absent from its original collection");
                    var value = materialBlock(material, block, prefix);
                    grouped.get(block).add(Map.of("material", materialName(material), "form", value));
                    byMaterial.put(material, value);
                }
                for (Object block : blocks) {
                    String name = registeredName(block, "BLOCKS"); requireBlockItem(block);
                    Object property = virtual(MATERIAL_BLOCK, block, "getVariantProperty",
                            type("gregtech.common.blocks.properties.PropertyMaterial"), new Class<?>[0]);
                    var allowed = new ArrayList<String>();
                    for (Object material : (List<?>)field("gregtech.common.blocks.properties.PropertyMaterial", property, "allowedValues"))
                        allowed.add(materialName(material));
                    var variants = grouped.get(block); variants.sort(Comparator.comparing(row -> (String)row.get("material")));
                    add(entries, name, Map.of("family", family, "registryIdentity", true, "blockItemIdentity", true,
                            "allowedMaterialsInMetadataOrder", allowed, "mappedVariants", variants));
                }
                for (Object material : selected) witnesses.add(Map.of("material", materialName(material),
                        "prefix", call(prefix, "name"), "generated", byMaterial.containsKey(material) ? List.of(byMaterial.get(material)) : List.of(),
                        "selected", stack(selectedStack(prefix, material), material, prefix)));
            }
            return entries;
        });
        return witnessed(catalog, witnesses);
    }

    private static Map<String,Object> materialBlock(Object material, Object block, Object prefix) throws ReflectiveOperationException {
        String name = registeredName(block, "BLOCKS"); requireBlockItem(block);
        Object state = virtual(MATERIAL_BLOCK, block, "getBlock", type(STATE), new Class<?>[]{type(MATERIAL)}, material);
        Object property = virtual(MATERIAL_BLOCK, block, "getVariantProperty",
                type("gregtech.common.blocks.properties.PropertyMaterial"), new Class<?>[0]);
        int meta = ((Number)virtual(BLOCK, block, "func_176201_c", int.class, new Class<?>[]{type(STATE)}, state)).intValue();
        Object roundTrip = virtual(BLOCK, block, "func_176203_a", type(STATE), new Class<?>[]{int.class}, meta);
        String propertyName = (String)call(property, "getName", type(MATERIAL), material);
        require(virtual(MATERIAL_BLOCK, block, "getGtMaterial", type(MATERIAL), new Class<?>[]{type(STATE)}, state) == material
                && virtual(MATERIAL_BLOCK, block, "getGtMaterial", type(MATERIAL), new Class<?>[]{type(STATE)}, roundTrip) == material
                && call(call(property, "func_185929_b", String.class, propertyName), "orNull") == material,
                "Original material block state/property round trip differs: " + name + "#" + meta);
        var value = new LinkedHashMap<>(stack(virtual(MATERIAL_BLOCK, block, "getItem", type(STACK), new Class<?>[]{type(MATERIAL)}, material), material, prefix));
        value.put("block", name); value.put("blockRegistryIdentity", true); value.put("blockItemIdentity", true);
        value.put("stateMaterialIdentity", true); value.put("stateRoundTripIdentity", true);
        value.put("propertyName", propertyName); value.put("propertyRoundTripIdentity", true);
        return value;
    }

    private static Map<String,Object> ores(List<Object> selected, NativeSelectedMaterialObservations observations) {
        var witnesses = new ArrayList<Map<String,Object>>();
        var catalog = observe("native-ore-block-collection", "original-ore-stone-variants-and-block-item-registry-identities-v1", () -> {
            var entries = new Catalog();
            for (Object block : (List<?>)field(META_BLOCKS, null, "ORES")) {
                String name = registeredName(block, "BLOCKS"); Object item = requireBlockItem(block);
                // The original published artifact maps this Material field to the inherited SRG name.
                Object material = field(ORE_BLOCK, block, "field_149764_J"), property = field(ORE_BLOCK, block, "STONE_TYPE");
                var variants = new ArrayList<Map<String,Object>>(); var forms = new ArrayList<Map<String,Object>>();
                for (Object stone : (List<?>)field("gregtech.common.blocks.properties.PropertyStoneType", property, "allowedValues")) {
                    String stoneName = (String)field("gregtech.api.unification.ore.StoneType", stone, "name");
                    Object prefix = field("gregtech.api.unification.ore.StoneType", stone, "processingPrefix");
                    Object state = virtual(ORE_BLOCK, block, "getOreBlock", type(STATE), new Class<?>[]{type("gregtech.api.unification.ore.StoneType")}, stone);
                    int meta = ((Number)virtual(BLOCK, block, "func_176201_c", int.class, new Class<?>[]{type(STATE)}, state)).intValue();
                    Object restored = virtual(BLOCK, block, "func_176203_a", type(STATE), new Class<?>[]{int.class}, meta);
                    require(virtual(STATE, restored, "func_177229_b", Comparable.class, new Class<?>[]{type("net.minecraft.block.properties.IProperty")}, property) == stone
                            && call(call(property, "func_185929_b", String.class, stoneName), "orNull") == stone,
                            "Original ore state/property round trip differs: " + name + "#" + meta);
                    var value = new LinkedHashMap<String,Object>(); value.put("stone", stoneName); value.put("meta", meta);
                    value.put("prefix", call(prefix, "name")); value.put("stateRoundTripIdentity", true);
                    value.put("propertyRoundTripIdentity", true); value.put("droppedAsItem", field("gregtech.api.unification.ore.StoneType", stone, "shouldBeDroppedAsItem"));
                    variants.add(value);
                    if (selected.stream().anyMatch(candidate -> candidate == material)) {
                        var form = new LinkedHashMap<>(value);
                        Object stack = type(STACK).getConstructor(type("net.minecraft.item.Item"), int.class, int.class).newInstance(item, 1, meta);
                        form.put("stack", stack(stack, material, prefix));
                        if (observations.form(material, "gt-ore-blocks", call(prefix, "name"), stoneName, "ordinaryDrop")) {
                            Object drop = virtual(BLOCK, block, "func_180660_a", type("net.minecraft.item.Item"),
                                    new Class<?>[]{type(STATE), Random.class, int.class}, state, new Random(0), 0);
                            int damage = ((Number)virtual(BLOCK, block, "func_180651_a", int.class,
                                    new Class<?>[]{type(STATE)}, state)).intValue();
                            Object dropStack = type(STACK).getConstructor(type("net.minecraft.item.Item"), int.class, int.class).newInstance(drop, 1, damage);
                            form.put("ordinaryDrop", stack(dropStack, material, prefix));
                        }
                        forms.add(form);
                    }
                }
                add(entries, name, Map.of("material", materialName(material), "registryIdentity", true, "blockItemIdentity", true, "variants", variants));
                if (!forms.isEmpty()) witnesses.add(Map.of("material", materialName(material), "block", name, "generated", forms));
            }
            return entries;
        });
        return witnessed(catalog, witnesses);
    }

    private static Object requireBlockItem(Object block) throws ReflectiveOperationException {
        Object item = staticMethod("net.minecraft.item.Item", "func_150898_a", type("net.minecraft.item.Item"), new Class<?>[]{type(BLOCK)}, block);
        require(type("net.minecraft.item.ItemBlock").isInstance(item)
                && virtual("net.minecraft.item.ItemBlock", item, "func_179223_d", type(BLOCK), new Class<?>[0]) == block,
                "Original generated block/item identity differs");
        registeredName(item, "ITEMS"); return item;
    }

    static Map<String,Object> stack(Object stack, Object material, Object prefix) throws ReflectiveOperationException {
        if (Boolean.TRUE.equals(virtual(STACK, stack, "func_190926_b", boolean.class, new Class<?>[0]))) return Map.of("empty", true);
        Object item = virtual(STACK, stack, "func_77973_b", type("net.minecraft.item.Item"), new Class<?>[0]);
        var value = new LinkedHashMap<String,Object>(); value.put("empty", false);
        value.put("item", registeredName(item, "ITEMS")); value.put("registryIdentity", true);
        value.put("metadata", virtual(STACK, stack, "func_77960_j", int.class, new Class<?>[0]));
        value.put("count", virtual(STACK, stack, "func_190916_E", int.class, new Class<?>[0]));
        value.put("stackLimit", virtual("net.minecraft.item.Item", item, "getItemStackLimit", int.class, new Class<?>[]{type(STACK)}, stack));
        boolean materialIdentity = false;
        if (type(PREFIX_ITEM).isInstance(item)) materialIdentity = call(item, "getMaterial", type(STACK), stack) == material;
        else if (type("net.minecraft.item.ItemBlock").isInstance(item)) {
            Object block = virtual("net.minecraft.item.ItemBlock", item, "func_179223_d", type(BLOCK), new Class<?>[0]);
            if (type(MATERIAL_BLOCK).isInstance(block))
                materialIdentity = virtual(MATERIAL_BLOCK, block, "getGtMaterial", type(MATERIAL), new Class<?>[]{type(STACK)}, stack) == material;
            else if (type(ORE_BLOCK).isInstance(block)) materialIdentity = field(ORE_BLOCK, block, "field_149764_J") == material;
        }
        value.put("materialIdentity", materialIdentity);
        Object entry = callStatic(UNIFIER, "getUnificationEntry", type(STACK), stack);
        value.put("unifierIdentity", entry != null && field("gregtech.api.unification.stack.UnificationEntry", entry, "material") == material
                && field("gregtech.api.unification.stack.UnificationEntry", entry, "orePrefix") == prefix);
        var ores = new ArrayList<String>();
        for (int id : (int[])callStatic("net.minecraftforge.oredict.OreDictionary", "getOreIDs", type(STACK), stack))
            ores.add((String)callStatic("net.minecraftforge.oredict.OreDictionary", "getOreName", int.class, id));
        Collections.sort(ores); value.put("oreNames", ores); return value;
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
