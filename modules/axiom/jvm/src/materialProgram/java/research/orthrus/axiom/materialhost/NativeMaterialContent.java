package research.orthrus.axiom.materialhost;

import gregtech.api.GregTechAPI;
import gregtech.api.items.materialitem.MetaPrefixItem;
import gregtech.api.items.metaitem.MetaItem;
import gregtech.api.items.metaitem.StandardMetaItem;
import gregtech.api.unification.OreDictUnifier;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.api.unification.ore.StoneType;
import gregtech.api.util.GTUtility;
import gregtech.common.blocks.BlockMaterialBase;
import gregtech.common.blocks.BlockOre;
import gregtech.common.blocks.MaterialItemBlock;
import gregtech.common.blocks.OreItemBlock;
import gregtech.common.blocks.StoneVariantBlock;
import net.minecraft.block.Block;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegistryEvent;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.oredict.OreDictionary;
import java.util.*;

/** Composed native material-content lifecycle, sharing the program's actual Materials.
 * Bounded StandardMetaItem coexistence, not full CommonProxy/addon execution.
 * A failed phase is terminal. Never clear a native registry or roll back effects.
 */
public final class NativeMaterialContent {
    private enum Phase { NEW, CONSTRUCTING, CONSTRUCTED, BLOCK_REGISTERING, BLOCKS_REGISTERED, REGISTERING, ITEMS_REGISTERED, ORE_REGISTERING, COMPLETE, FAILED }
    private Phase phase = Phase.NEW;
    private boolean eventReceived;
    private List<MetaPrefixItem> items = List.of();
    private List<MetaItem<?>> composition = List.of();
    private List<BlockMaterialBase> blocks = List.of();
    private final List<Map<String,Object>> checkpoints = new ArrayList<>();
    private String failedPhase;
    private Map<String,Object> failure;
    private Map<String,Object> interruptedState;

    /** Recorded checkpoints only: inspection must not advance a stopped native lifecycle. */
    public Map<String,Object> progress() {
        var result = new LinkedHashMap<String,Object>();
        result.put("schema", "axiom.native-material-content-progress.v1");
        result.put("phase", phase.name());
        result.put("completedCheckpoints", List.copyOf(checkpoints));
        result.put("lastCompletedPhase", checkpoints.isEmpty() ? "NONE" : checkpoints.getLast().get("phase"));
        result.put("interruptedPhaseState", "not-a-complete-inventory");
        if (failedPhase != null) { result.put("failedPhase", failedPhase); result.put("failure", failure); }
        if (interruptedState != null) result.put("interruptedState", interruptedState);
        return Collections.unmodifiableMap(result);
    }

    public boolean constructedCheckpointReached() { return !checkpoints.isEmpty(); }

    public void execute() {
        if (phase != Phase.NEW) throw NativeBoundary.unsupported("material-content.repeated-execution:" + phase);
        try {
            if (!GregTechAPI.materialManager.getPhase().name().equals("FROZEN"))
                throw NativeBoundary.unsupported("material-content.requires-frozen-catalog");
            if (MetaItem.getMetaItems().stream().anyMatch(item -> item.getClass() != StandardMetaItem.class))
                throw NativeBoundary.unsupported("material-content.uncomposed-or-existing-generated-items");
            if (!NativeMaterialBlockDeclarations.COMPRESSED_BLOCKS.isEmpty() || !NativeMaterialBlockDeclarations.FRAME_BLOCKS.isEmpty()
                    || !NativeMaterialBlockDeclarations.COMPRESSED.isEmpty() || !NativeMaterialBlockDeclarations.FRAMES.isEmpty())
                throw NativeBoundary.unsupported("material-content.existing-generated-blocks");
            var owner = Loader.instance().activeModContainer();
            if (owner == null || !owner.getModId().equals("gregtech"))
                throw NativeBoundary.unsupported("material-content.requires-gt-owner");
            phase = Phase.CONSTRUCTING;
            OreDictUnifier.init();
            if (!NativeOreHostBlocks.STONE_BLOCKS.isEmpty() || !NativeOreDeclarations.ORES.isEmpty()
                    || !NativeOreDeclarations.oreBlockTable.isEmpty() || StoneType.STONE_TYPE_REGISTRY.iterator().hasNext())
                throw NativeBoundary.unsupported("material-content.existing-ore-universe");
            NativeOreHostBlocks.STONE_BLOCKS.put(StoneVariantBlock.StoneVariant.SMOOTH,
                    new StoneVariantBlock(StoneVariantBlock.StoneVariant.SMOOTH));
            NativeMaterialBlockDeclarations.construct();
            var constructedBlocks = new ArrayList<BlockMaterialBase>();
            constructedBlocks.addAll(NativeMaterialBlockDeclarations.COMPRESSED_BLOCKS);
            constructedBlocks.addAll(NativeMaterialBlockDeclarations.FRAME_BLOCKS);
            blocks = List.copyOf(constructedBlocks);
            NativePrefixItemDeclarations.construct();
            composition = List.copyOf(MetaItem.getMetaItems());
            items = composition.stream().filter(MetaPrefixItem.class::isInstance).map(MetaPrefixItem.class::cast).toList();
            phase = Phase.CONSTRUCTED;
            capture();
            phase = Phase.BLOCK_REGISTERING;
            var blockListener = new BlockRegistration();
            MinecraftForge.EVENT_BUS.register(blockListener);
            try {
                MinecraftForge.EVENT_BUS.post(new RegistryEvent.Register<Block>(new ResourceLocation("minecraft:block"), ForgeRegistries.BLOCKS));
                if (!eventReceived) throw NativeBoundary.unsupported("material-content.missing-block-event");
            } finally {
                MinecraftForge.EVENT_BUS.unregister(blockListener);
            }
            phase = Phase.BLOCKS_REGISTERED;
            capture();
            phase = Phase.REGISTERING;
            eventReceived = false;
            var listener = new Registration();
            MinecraftForge.EVENT_BUS.register(listener);
            try {
                MinecraftForge.EVENT_BUS.post(new RegistryEvent.Register<Item>(new ResourceLocation("minecraft:item"), ForgeRegistries.ITEMS));
                if (!eventReceived) throw NativeBoundary.unsupported("material-content.missing-item-event");
            } finally {
                MinecraftForge.EVENT_BUS.unregister(listener);
            }
            phase = Phase.ITEMS_REGISTERED;
            capture();
            phase = Phase.ORE_REGISTERING;
            NativePrefixItemDeclarations.registerOres();
            NativeMaterialBlockDeclarations.registerOres();
            NativeOreDeclarations.registerOres();
            phase = Phase.COMPLETE;
            capture();
        } catch (RuntimeException | Error failure) {
            failedPhase = phase.name();
            this.failure = Map.of("type", failure.getClass().getName(), "message", String.valueOf(failure.getMessage()));
            // A completed construction checkpoint already visited these native
            // registries. Observe partial later effects without replaying work.
            if (!checkpoints.isEmpty()) {
                try { interruptedState = snapshot(); }
                catch (RuntimeException | Error observationFailure) {
                    NativeBoundary.unsupported("material-content.progress-observation");
                    interruptedState = Map.of("observationFailure", observationFailure.getClass().getName());
                }
            }
            phase = Phase.FAILED;
            throw failure;
        }
    }

    public final class BlockRegistration {
        @SubscribeEvent public void register(RegistryEvent.Register<Block> event) {
            if (phase != Phase.BLOCK_REGISTERING || eventReceived || event.getRegistry() != ForgeRegistries.BLOCKS)
                throw NativeBoundary.unsupported("material-content.unexpected-block-event");
            eventReceived = true;
            NativeOreDeclarations.generate();
            NativeMaterialBlockDeclarations.registerBlocks(event.getRegistry());
            NativeOreDeclarations.registerBlocks(event.getRegistry());
        }
    }

    public final class Registration {
        @SubscribeEvent public void register(RegistryEvent.Register<Item> event) {
            if (phase != Phase.REGISTERING || eventReceived || event.getRegistry() != ForgeRegistries.ITEMS)
                throw NativeBoundary.unsupported("material-content.unexpected-item-event");
            eventReceived = true;
            NativePrefixItemDeclarations.register(event.getRegistry());
            NativeMaterialBlockDeclarations.registerItems(event.getRegistry());
            NativeOreDeclarations.registerItems(event.getRegistry());
        }
    }

    private void capture() {
        if (!MetaItem.getMetaItems().equals(composition))
            throw NativeBoundary.unsupported("material-content.changed-meta-item-composition");
        checkpoints.add(snapshot());
    }

    private Map<String,Object> snapshot() {
        return Map.of("phase", phase.name(), "items", items.size(),
                "variants", items.stream().mapToInt(i -> i.getAllItems().size()).sum(),
                "compressedBlocks", NativeMaterialBlockDeclarations.COMPRESSED_BLOCKS.size(),
                "frameBlocks", NativeMaterialBlockDeclarations.FRAME_BLOCKS.size(),
                "registeredBlocks", blocks.stream().filter(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) == b).count(),
                "registeredBlockItems", blocks.stream().filter(b -> Item.func_150898_a(b) instanceof MaterialItemBlock).count(),
                "oreBlocks", NativeOreDeclarations.ORES.size(),
                "registeredOreBlocks", NativeOreDeclarations.ORES.stream().filter(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) == b).count(),
                "registeredOreItems", NativeOreDeclarations.ORES.stream().filter(b -> Item.func_150898_a(b) instanceof OreItemBlock).count());
    }

    /** Generated ore states, unifier selection and ordinary drops remain distinct. */
    public Map<String,Object> observeOres(Collection<Material> materials) {
        if (phase != Phase.COMPLETE) throw new IllegalStateException("Ore results are incomplete: " + phase);
        var rows = new ArrayList<Map<String,Object>>();
        for (Material material : materials) {
            if (!GregTechAPI.materialManager.getRegisteredMaterials().contains(material))
                throw new IllegalArgumentException("Material is not in the frozen native catalog");
            var generated = NativeOreDeclarations.oreBlockTable.get(material);
            for (StoneType stone : StoneType.STONE_TYPE_REGISTRY) {
                var row = new LinkedHashMap<String,Object>();
                row.put("material", material.getRegistryName());
                row.put("stone", stone.name);
                row.put("stoneId", StoneType.STONE_TYPE_REGISTRY.func_148757_b(stone));
                row.put("prefix", stone.processingPrefix.name());
                row.put("uniqueDrop", stone.shouldBeDroppedAsItem);
                row.put("selected", stack(OreDictUnifier.get(stone.processingPrefix, material), material, stone.processingPrefix));
                var variants = new ArrayList<Map<String,Object>>();
                if (generated != null && generated.get(stone) instanceof BlockOre block) {
                    var state = block.getOreBlock(stone);
                    var value = new LinkedHashMap<>(stack(GTUtility.toItem(state), material, stone.processingPrefix));
                    value.put("block", block.getRegistryName().toString());
                    value.put("blockRegistryIdentity", ForgeRegistries.BLOCKS.getValue(block.getRegistryName()) == block);
                    value.put("stoneIdentity", state.func_177229_b(block.STONE_TYPE) == stone);
                    value.put("stateRoundTripIdentity", block.func_176203_a(block.func_176201_c(state)) == state);
                    value.put("propertyRoundTripIdentity", block.STONE_TYPE.func_185929_b(stone.name).orNull() == stone);
                    var dropped = new ItemStack(block.func_180660_a(state, new Random(0), 0), 1, block.func_180651_a(state));
                    value.put("ordinaryDrop", stack(dropped, material, stone.processingPrefix));
                    variants.add(value);
                }
                row.put("generated", variants);
                rows.add(row);
            }
        }
        return Map.of("family", "gt-ore-blocks", "phase", phase.name(), "forms", rows,
                "checkpoints", List.copyOf(checkpoints), "recipeHandlersExecuted", false,
                "worldGenerationQualified", false, "harvestingEventsQualified", false, "wholePackParity", false);
    }

    public Map<String,Object> observeBlocks(Collection<Material> materials) {
        if (phase != Phase.COMPLETE) throw new IllegalStateException("Material-block results are incomplete: " + phase);
        var rows = new ArrayList<Map<String,Object>>();
        for (Material material : materials) {
            if (!GregTechAPI.materialManager.getRegisteredMaterials().contains(material))
                throw new IllegalArgumentException("Material is not in the frozen native catalog");
            for (OrePrefix prefix : List.of(OrePrefix.block, OrePrefix.frameGt)) {
                BlockMaterialBase block = prefix == OrePrefix.block ? NativeMaterialBlockDeclarations.COMPRESSED.get(material)
                        : NativeMaterialBlockDeclarations.FRAMES.get(material);
                var generated = new ArrayList<Map<String,Object>>();
                if (block != null) {
                    ItemStack stack = block.getItem(material);
                    var value = new LinkedHashMap<>(stack(stack, material, prefix));
                    var state = block.getBlock(material);
                    var property = block.getVariantProperty();
                    String propertyName = property.func_177702_a(material);
                    value.put("block", block.getRegistryName().toString());
                    value.put("blockRegistryIdentity", ForgeRegistries.BLOCKS.getValue(block.getRegistryName()) == block);
                    value.put("blockItemIdentity", stack.func_77973_b() instanceof MaterialItemBlock item
                            && item.func_179223_d() == block && Item.func_150898_a(block) == item);
                    value.put("stateRoundTripIdentity", block.getGtMaterial(block.func_176203_a(block.func_176201_c(state))) == material);
                    value.put("stateMaterialIdentity", block.getGtMaterial(state) == material);
                    value.put("propertyName", propertyName);
                    value.put("propertyRoundTripIdentity", property.func_185929_b(propertyName).orNull() == material);
                    generated.add(value);
                }
                rows.add(Map.of("material", material.getRegistryName(), "prefix", prefix.name(), "generated", generated,
                        "selected", stack(OreDictUnifier.get(prefix, material), material, prefix)));
            }
        }
        return Map.of("family", "gt-material-blocks", "phase", phase.name(), "checkpoints", List.copyOf(checkpoints),
                "forms", rows, "recipeHandlersExecuted", false, "wholePackParity", false);
    }

    /** Describe every admitted prefix, including negative generation decisions.
     * The unifier result is distinct from this family's generated variant: an
     * ignored prefix can select an existing vanilla item instead.
     */
    public Map<String,Object> observe(Collection<Material> materials) {
        if (phase != Phase.COMPLETE) throw new IllegalStateException("Prefix-item results are incomplete: " + phase);
        var rows = new ArrayList<Map<String,Object>>();
        for (Material material : materials) {
            if (!GregTechAPI.materialManager.getRegisteredMaterials().contains(material))
                throw new IllegalArgumentException("Material is not in the frozen native catalog");
            for (MetaPrefixItem item : items) {
                OrePrefix prefix = item.getOrePrefix();
                var row = new LinkedHashMap<String,Object>();
                row.put("material", material.getRegistryName());
                row.put("prefix", prefix.name());
                row.put("eligible", prefix.doGenerateItem(material));
                var generated = new ArrayList<Map<String,Object>>();
                for (var value : item.getAllItems()) {
                    ItemStack stack = value.getStackForm();
                    if (item.getMaterial(stack) == material) generated.add(stack(stack, material, prefix));
                }
                row.put("generated", generated);
                row.put("selected", stack(OreDictUnifier.get(prefix, material), material, prefix));
                rows.add(row);
            }
        }
        return Map.of("family", "gt-prefix-items", "phase", phase.name(), "checkpoints", List.copyOf(checkpoints),
                "forms", rows, "recipeHandlersExecuted", false, "wholePackParity", false);
    }

    private static Map<String,Object> stack(ItemStack stack, Material material, OrePrefix prefix) {
        if (stack.func_190926_b()) return Map.of("empty", true);
        Item item = stack.func_77973_b();
        var entry = OreDictUnifier.getUnificationEntry(stack);
        var result = new LinkedHashMap<String,Object>();
        result.put("empty", false);
        result.put("item", item.getRegistryName().toString());
        result.put("metadata", stack.func_77960_j());
        result.put("count", stack.func_190916_E());
        result.put("stackLimit", item.getItemStackLimit(stack));
        result.put("registryIdentity", ForgeRegistries.ITEMS.getValue(item.getRegistryName()) == item);
        result.put("materialIdentity", item instanceof MetaPrefixItem generated && generated.getMaterial(stack) == material
                || item instanceof MaterialItemBlock generatedBlock && generatedBlock.func_179223_d().getGtMaterial(stack) == material
                || item instanceof OreItemBlock ore && ore.func_179223_d() instanceof BlockOre block && block.material == material);
        result.put("unifierIdentity", entry != null && entry.material == material && entry.orePrefix == prefix);
        result.put("oreNames", Arrays.stream(OreDictionary.getOreIDs(stack)).mapToObj(OreDictionary::getOreName).sorted().toList());
        return result;
    }
}
