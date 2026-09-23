package research.orthrus.axiom.nativeconstruction;

import java.util.*;
import net.minecraft.block.Block;
import net.minecraft.item.*;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.event.RegistryEvent;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.oredict.OreDictionary;

/** Host-owned, up-front content composition, not a replacement CommonProxy.
 * Source-owned decisions live in MaterialItemDeclarations/MaterialBlockDeclarations.
 * A failed operation is terminal; native partial mutations are never rolled back.
 */
final class MaterialContentFamily {
    enum Universe { PREFIX_ITEMS, PREFIX_ITEMS_AND_MATERIAL_BLOCKS, GT_ORE_CONTENT, ADDON_ORE_CONTENT }
    enum Phase { NEW, CONSTRUCTED, ORE_HOST_PREPARING, ORE_HOSTS_PREPARED, BLOCK_REGISTERING, BLOCKS_REGISTERED, REGISTERING, ITEMS_REGISTERED, ORE_REGISTERING, COMPLETE, FAILED }
    private final Universe universe;
    private Phase phase = Phase.NEW;
    private final List<Object> checkpoints = new ArrayList<>();
    private List<MetaPrefixItem> items = List.of();
    private List<BlockMaterialBase> blocks = List.of();
    private List<BlockCompressed> compressed = List.of();
    private List<BlockFrame> frames = List.of();
    private Map<FluidMaterial, BlockCompressed> compressedIndex = Map.of();
    private Map<FluidMaterial, BlockFrame> frameIndex = Map.of();
    private final Map<BlockMaterialBase, MaterialItemBlock> blockItems = new IdentityHashMap<>();
    private boolean eventReceived;
    private boolean blocksRegistered, itemsRegistered;
    private OreContentFamily oreContent;

    MaterialContentFamily(Universe universe) { this.universe = Objects.requireNonNull(universe); }
    private boolean includesBlocks() { return universe != Universe.PREFIX_ITEMS; }
    private boolean includesOres() { return universe == Universe.GT_ORE_CONTENT || universe == Universe.ADDON_ORE_CONTENT; }

    Phase phase() { return phase; }
    List<MetaPrefixItem> items() { return items; }
    List<BlockMaterialBase> blocks() { return blocks; }
    List<Object> checkpoints() { return List.copyOf(checkpoints); }
    OreContentFamily oreContent() {
        if (oreContent == null) throw new Failure("incomplete", "ore.family", "Ore family was not admitted");
        return oreContent;
    }

    void construct() {
        require(Phase.NEW);
        if (FluidEnvironment.current().runtime().materials().getPhase() != MaterialPhase.FROZEN || !MetaItem.getMetaItems().isEmpty() ||
                !MaterialBlockDeclarations.COMPRESSED_BLOCKS.isEmpty() || !MaterialBlockDeclarations.FRAME_BLOCKS.isEmpty() ||
                !MaterialBlockDeclarations.COMPRESSED.isEmpty() || !MaterialBlockDeclarations.FRAMES.isEmpty())
            throw new IllegalStateException("Material item family requires a frozen catalog and fresh item universe");
        perform(() -> {
            // Original CoreModule order: frozen catalog -> unifier scan/listener
            // -> generated item construction. This checkpoint owns that scan.
            OreDictUnifier.init();
            if (includesBlocks()) {
                MaterialBlockDeclarations.construct();
                compressed = List.copyOf(MaterialBlockDeclarations.COMPRESSED_BLOCKS);
                frames = List.copyOf(MaterialBlockDeclarations.FRAME_BLOCKS);
                compressedIndex = Map.copyOf(MaterialBlockDeclarations.COMPRESSED);
                frameIndex = Map.copyOf(MaterialBlockDeclarations.FRAMES);
                var all = new ArrayList<BlockMaterialBase>(); all.addAll(compressed); all.addAll(frames);
                blocks = List.copyOf(all);
            }
            MaterialItemDeclarations.construct();
            items = MetaItem.getMetaItems().stream().map(i -> (MetaPrefixItem)i).toList();
            if (includesOres()) oreContent = new OreContentFamily(universe == Universe.ADDON_ORE_CONTENT);
            phase = Phase.CONSTRUCTED; capture();
        });
    }

    void prepareOreHosts(net.minecraftforge.fml.common.ModContainer addonOwner, OreAddon addon) {
        if (!includesOres()) throw new IllegalStateException("Ore family was not admitted");
        require(Phase.CONSTRUCTED);
        perform(() -> {
            requireUniverse(); phase = Phase.ORE_HOST_PREPARING;
            oreContent.prepare(addonOwner, addon);
            phase = Phase.ORE_HOSTS_PREPARED; capture();
        });
    }

    void registerBlocks() {
        if (!includesBlocks()) throw new IllegalStateException("Block family was not admitted");
        require(includesOres() ? Phase.ORE_HOSTS_PREPARED : Phase.CONSTRUCTED);
        perform(() -> {
            requireUniverse(); phase = Phase.BLOCK_REGISTERING; eventReceived = false;
            var listener = new BlockRegistration(); MinecraftForge.EVENT_BUS.register(listener);
            try {
                MinecraftForge.EVENT_BUS.post(new RegistryEvent.Register<Block>(new net.minecraft.util.ResourceLocation("minecraft:block"), ForgeRegistries.BLOCKS));
                if (!eventReceived) throw new IllegalStateException("Native block registry event did not reach family callback");
            } finally { MinecraftForge.EVENT_BUS.unregister(listener); }
            blocksRegistered = true; requireUniverse(); phase = Phase.BLOCKS_REGISTERED; capture();
        });
    }

    public final class BlockRegistration {
        @SubscribeEvent public void register(RegistryEvent.Register<Block> event) {
            require(Phase.BLOCK_REGISTERING);
            if (eventReceived || event.getRegistry() != ForgeRegistries.BLOCKS)
                throw new IllegalStateException("Unexpected or repeated material block registry event");
            eventReceived = true;
            if (includesOres()) oreContent.generate();
            MaterialBlockDeclarations.registerBlocks(event.getRegistry());
            if (includesOres()) oreContent.registerBlocks(event.getRegistry());
        }
    }

    void registerItems() {
        require(includesBlocks() ? Phase.BLOCKS_REGISTERED : Phase.CONSTRUCTED);
        perform(() -> {
            requireUniverse();
            phase = Phase.REGISTERING; eventReceived = false;
            var listener = new Registration();
            MinecraftForge.EVENT_BUS.register(listener);
            try {
                MinecraftForge.EVENT_BUS.post(new RegistryEvent.Register<Item>(new net.minecraft.util.ResourceLocation("minecraft:item"), ForgeRegistries.ITEMS));
                if (!eventReceived) throw new IllegalStateException("Native item registry event did not reach family callback");
            } finally { MinecraftForge.EVENT_BUS.unregister(listener); }
            itemsRegistered = true;
            for (var block : blocks) {
                Item item = Item.func_150898_a(block);
                if (!(item instanceof MaterialItemBlock form) || form.func_179223_d() != block)
                    throw new IllegalStateException("Native block-to-item callback did not bind the generated ItemBlock");
                blockItems.put(block, form);
            }
            requireUniverse(); phase = Phase.ITEMS_REGISTERED; capture();
        });
    }

    public final class Registration {
        @SubscribeEvent public void register(RegistryEvent.Register<Item> event) {
            require(Phase.REGISTERING);
            if (eventReceived || event.getRegistry() != ForgeRegistries.ITEMS)
                throw new IllegalStateException("Unexpected or repeated material item registry event");
            eventReceived = true;
            MaterialItemDeclarations.register(event.getRegistry());
            if (includesBlocks()) MaterialBlockDeclarations.registerItems(event.getRegistry());
            if (includesOres()) oreContent.registerItems(event.getRegistry());
        }
    }

    void registerOres() {
        require(Phase.ITEMS_REGISTERED);
        perform(() -> {
            requireUniverse();
            phase = Phase.ORE_REGISTERING;
            // Intentionally not the full recipe-registry event: it also runs
            // recipe loaders, addon callbacks and material processing handlers.
            MaterialItemDeclarations.registerOres();
            if (includesBlocks()) MaterialBlockDeclarations.registerOres();
            if (includesOres()) oreContent.registerOres();
            requireUniverse(); phase = Phase.COMPLETE; capture();
        });
    }

    ItemStack resolve(OrePrefix prefix, FluidMaterial material, int count) {
        if (phase != Phase.COMPLETE || (items.stream().noneMatch(i -> i.getOrePrefix() == prefix) &&
                !(includesBlocks() && (prefix == OrePrefix.block || prefix == OrePrefix.frameGt)) &&
                !(includesOres() && oreContent.admits(prefix))))
            throw new Failure("incomplete", "item.family", "Material-prefix family or requested prefix is not qualified at this checkpoint");
        requireUniverse();
        if (!FluidEnvironment.current().runtime().materials().getRegisteredMaterials().contains(material))
            throw new Failure("incomplete", "item.catalog", "Material is outside this qualified catalog");
        return OreDictUnifier.get(prefix, material, count);
    }

    ItemStack generatedOre(FluidMaterial material, StoneType stone) {
        if (phase != Phase.COMPLETE || !includesOres()) throw new Failure("incomplete", "ore.family", "Ore content is not complete");
        requireUniverse();
        if (!FluidEnvironment.current().runtime().materials().getRegisteredMaterials().contains(material))
            throw new Failure("incomplete", "ore.catalog", "Material is outside this qualified catalog");
        return oreContent.generatedForm(material, stone);
    }

    Map<String,Object> inventory() {
        var entries = new ArrayList<Object>();
        for (MetaPrefixItem item : items) {
            boolean registered = ForgeRegistries.ITEMS.getValue(item.getRegistryName()) == item;
            var variants = new ArrayList<Object>();
            for (var value : item.getAllItems()) {
                var row = new LinkedHashMap<String,Object>();
                row.put("metadata", value.metaValue); row.put("name", value.unlocalizedName);
                if (registered) {
                    var stack = value.getStackForm();
                    row.put("material", item.getMaterial(stack).getRegistryName());
                    row.put("oreNames", Arrays.stream(OreDictionary.getOreIDs(stack)).mapToObj(OreDictionary::getOreName).toList());
                    row.put("stackLimit", item.getItemStackLimit(stack));
                }
                variants.add(row);
            }
            entries.add(Map.of("registryName", item.getRegistryName().toString(), "registered", registered,
                    "prefix", item.getOrePrefix().name(), "variants", variants));
        }
        var result = new LinkedHashMap<String,Object>(Map.of("family", "gt-material-prefix-items", "phase", phase.name(), "items", entries,
                "wholePackParity", false, "fullRecipeRegistration", false, "allItemBehaviorQualified", false));
        if (includesBlocks()) {
            result.put("family", "gt-material-items-and-blocks");
            result.put("blocks", blockInventory()); result.put("allBlockBehaviorQualified", false);
        }
        if (includesOres() && oreContent != null) result.put("ores", oreContent.inventory());
        return result;
    }
    private List<Object> blockInventory() {
        var entries = new ArrayList<Object>();
        for (var block : blocks) {
            var variants = new ArrayList<Object>();
            var allowed = block.getVariantProperty().func_177700_c();
            for (int metadata = 0; metadata < allowed.size(); metadata++) {
                var material = allowed.get(metadata);
                var state = block.func_176203_a(metadata);
                var row = new LinkedHashMap<String,Object>();
                row.put("metadata", metadata); row.put("material", material.getRegistryName());
                row.put("stateMetadata", block.func_176201_c(state));
                if (Item.func_150898_a(block) instanceof MaterialItemBlock item) {
                    var stack = block.getItem(material);
                    row.put("item", item.getRegistryName().toString()); row.put("itemMetadata", stack.func_77952_i());
                    row.put("oreNames", Arrays.stream(OreDictionary.getOreIDs(stack)).mapToObj(OreDictionary::getOreName).toList());
                }
                variants.add(row);
            }
            entries.add(Map.of("registryName", block.getRegistryName().toString(),
                    "prefix", block instanceof BlockCompressed ? "block" : "frameGt",
                    "blockRegistered", ForgeRegistries.BLOCKS.getValue(block.getRegistryName()) == block,
                    "itemRegistered", Item.func_150898_a(block) instanceof MaterialItemBlock,
                    "variants", variants));
        }
        return entries;
    }
    private void capture() { checkpoints.add(inventory()); }
    private void requireUniverse() {
        if (oreContent != null) {
            try { oreContent.verify(); }
            catch (RuntimeException | Error failure) { phase = Phase.FAILED; throw failure; }
        }
        if (!MetaItem.getMetaItems().equals(items) || !MaterialBlockDeclarations.COMPRESSED_BLOCKS.equals(compressed) ||
                !MaterialBlockDeclarations.FRAME_BLOCKS.equals(frames) || !MaterialBlockDeclarations.COMPRESSED.equals(compressedIndex) ||
                !MaterialBlockDeclarations.FRAMES.equals(frameIndex) ||
                (blocksRegistered && blocks.stream().anyMatch(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) != b)) ||
                (itemsRegistered && (items.stream().anyMatch(i -> ForgeRegistries.ITEMS.getValue(i.getRegistryName()) != i) ||
                    blocks.stream().anyMatch(b -> Item.func_150898_a(b) != blockItems.get(b) ||
                        ForgeRegistries.ITEMS.getValue(b.getRegistryName()) != blockItems.get(b))))) {
            phase = Phase.FAILED;
            throw new Failure("incomplete", "item.family", "Material content universe changed outside this qualified composition");
        }
    }
    private void require(Phase expected) {
        if (phase != expected) throw new IllegalStateException("Expected material item phase " + expected + ", got " + phase);
    }
    private void perform(Runnable operation) {
        try { operation.run(); }
        catch (RuntimeException | Error failure) { phase = Phase.FAILED; throw failure; }
    }
}
