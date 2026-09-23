package research.orthrus.axiom.nativeconstruction;

import java.util.*;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.item.*;
import net.minecraftforge.fml.common.*;
import net.minecraftforge.fml.common.registry.ForgeRegistries;
import net.minecraftforge.oredict.OreDictionary;
import net.minecraftforge.registries.IForgeRegistry;

/** Checkpoint ownership only. All generation/state/drop decisions are retained source. */
final class OreContentFamily {
    private final boolean addonAdmitted;
    private boolean prepared, generated, blocksRegistered, itemsRegistered;
    private List<StoneType> stones = List.of();
    private Map<String, StoneType> stoneNames = Map.of();
    private Map<StoneType, Integer> stoneIds = Map.of();
    private List<BlockOre> ores = List.of();
    private Map<FluidMaterial, Map<StoneType, IBlockOre>> index = Map.of();
    private final Map<BlockOre, Item> itemBindings = new IdentityHashMap<>();
    private final Map<BlockOre, IBlockState> oreDefaults = new IdentityHashMap<>();
    private final Map<Block, IBlockState> hostStates = new IdentityHashMap<>();
    private StoneVariantBlock gtHost;
    private OreAddon addon;

    OreContentFamily(boolean addonAdmitted) { this.addonAdmitted = addonAdmitted; }
    List<BlockOre> ores() { return ores; }
    List<StoneType> stones() { return stones; }

    void prepare(ModContainer addonOwner, OreAddon sourceAddon) {
        if (prepared || !OreDeclarations.ORES.isEmpty() || !OreDeclarations.oreBlockTable.isEmpty() ||
                !OreHostBlocks.STONE_BLOCKS.isEmpty() ||
                StoneType.STONE_TYPE_REGISTRY.iterator().hasNext())
            throw new IllegalStateException("Ore preparation requires a fresh native universe");
        Loader loader = Loader.instance(); ModContainer previous = loader.activeModContainer();
        if (previous == null || !previous.getModId().equals("gregtech")) throw new IllegalStateException("GT owner required for host construction");
        gtHost = new StoneVariantBlock(StoneVariantBlock.StoneVariant.SMOOTH);
        OreHostBlocks.STONE_BLOCKS.put(StoneVariantBlock.StoneVariant.SMOOTH, gtHost);
        hostStates.put(gtHost, gtHost.func_176223_P());
        if (addonAdmitted) {
            if (addonOwner == null || sourceAddon == null || sourceAddon.getClass().getClassLoader() != getClass().getClassLoader())
                throw new IllegalArgumentException("Explicit owner and same-context source addon required");
            addon = sourceAddon;
            try {
                loader.setActiveModContainer(addonOwner);
                Block host = Objects.requireNonNull(addon.prepare());
                hostStates.put(host, host.func_176223_P());
            } finally { loader.setActiveModContainer(previous); }
        } else if (addonOwner != null || sourceAddon != null) throw new IllegalArgumentException("Addon was not admitted");
        prepared = true;
        captureStones();
    }

    private void captureStones() {
        var values = new ArrayList<StoneType>(); var names = new HashMap<String, StoneType>(); var ids = new IdentityHashMap<StoneType, Integer>();
        for (var stone : StoneType.STONE_TYPE_REGISTRY) {
            values.add(stone); names.put(StoneType.STONE_TYPE_REGISTRY.getNameForObject(stone), stone);
            ids.put(stone, StoneType.STONE_TYPE_REGISTRY.getIDForObject(stone));
        }
        stones = List.copyOf(values); stoneNames = Map.copyOf(StoneType.STONE_TYPE_REGISTRY.registryObjects); stoneIds = Map.copyOf(ids);
    }
    void generate() {
        if (!prepared || generated) throw new IllegalStateException("Ore generation requires the prepared checkpoint");
        verify(); OreDeclarations.generate(); generated = true;
        ores = List.copyOf(OreDeclarations.ORES);
        ores.forEach(b -> oreDefaults.put(b, b.func_176223_P()));
        var table = new IdentityHashMap<FluidMaterial, Map<StoneType, IBlockOre>>();
        OreDeclarations.oreBlockTable.forEach((material, types) -> table.put(material, Map.copyOf(types)));
        index = Map.copyOf(table); captureStones();
    }
    void registerBlocks(IForgeRegistry<Block> registry) {
        if (!generated || blocksRegistered) throw new IllegalStateException("Ore blocks require completed generation");
        OreDeclarations.registerBlocks(registry); blocksRegistered = true;
    }
    void registerItems(IForgeRegistry<Item> registry) {
        verify();
        if (!blocksRegistered || itemsRegistered) throw new IllegalStateException("Ore ItemBlocks require registered blocks");
        OreDeclarations.registerItems(registry);
        for (var ore : ores) {
            Item item = Item.func_150898_a(ore);
            if (!(item instanceof OreItemBlock form) || form.func_179223_d() != ore)
                throw new IllegalStateException("Native ore block-to-item binding differs");
            itemBindings.put(ore, item);
        }
        itemsRegistered = true; verify();
    }
    void registerOres() { verify(); if (!itemsRegistered) throw new IllegalStateException("Ore membership requires ItemBlocks"); OreDeclarations.registerOres(); }
    boolean admits(OrePrefix prefix) { return stones.stream().anyMatch(s -> s.processingPrefix == prefix); }

    ItemStack generatedForm(FluidMaterial material, StoneType stone) {
        if (!itemsRegistered || !stoneIds.containsKey(stone)) throw new Failure("incomplete", "ore.stone", "Stone type was not qualified");
        var byStone = OreDeclarations.oreBlockTable.get(material);
        var block = byStone == null ? null : byStone.get(stone);
        return block == null ? ItemStack.field_190927_a : MaterialBlockDeclarations.toItem(block.getOreBlock(stone));
    }

    void verify() {
        var current = new ArrayList<StoneType>(); StoneType.STONE_TYPE_REGISTRY.forEach(current::add);
        if (prepared && addon != null) addon.verify();
        if (!current.equals(stones) || !StoneType.STONE_TYPE_REGISTRY.registryObjects.equals(stoneNames) ||
                stoneIds.entrySet().stream().anyMatch(e -> StoneType.STONE_TYPE_REGISTRY.getIDForObject(e.getKey()) != e.getValue()) ||
                !OreDeclarations.ORES.equals(ores) || !OreDeclarations.oreBlockTable.equals(index) ||
                (prepared && (OreHostBlocks.STONE_BLOCKS.size() != 1 || OreHostBlocks.STONE_BLOCKS.get(StoneVariantBlock.StoneVariant.SMOOTH) != gtHost)) ||
                hostStates.entrySet().stream().anyMatch(e -> e.getKey().func_176223_P() != e.getValue()) ||
                oreDefaults.entrySet().stream().anyMatch(e -> e.getKey().func_176223_P() != e.getValue()) ||
                (blocksRegistered && ores.stream().anyMatch(b -> ForgeRegistries.BLOCKS.getValue(b.getRegistryName()) != b)) ||
                (itemsRegistered && ores.stream().anyMatch(b -> Item.func_150898_a(b) != itemBindings.get(b) || ForgeRegistries.ITEMS.getValue(b.getRegistryName()) != itemBindings.get(b))))
            throw new Failure("incomplete", "ore.universe", "Ore/host-stone identity changed outside the qualified checkpoint");
    }

    static List<Object> stack(ItemStack s) {
        return s.func_190926_b() ? List.of() : List.of(s.func_77973_b().getRegistryName().toString(), s.func_77952_i(), s.func_190916_E());
    }
    Map<String, Object> inventory() {
        var types = new ArrayList<Object>();
        for (var stone : stones) types.add(Map.of("name", stone.name, "id", stoneIds.get(stone),
                "prefix", stone.processingPrefix.name(), "material", stone.stoneMaterial.getRegistryName(), "uniqueItem", stone.shouldBeDroppedAsItem));
        var entries = new ArrayList<Object>();
        for (var block : OreDeclarations.ORES) {
            var variants = new ArrayList<Object>();
            for (var stone : block.STONE_TYPE.func_177700_c()) {
                var state = block.getOreBlock(stone);
                var row = new LinkedHashMap<String, Object>();
                row.put("stoneType", stone.name); row.put("metadata", block.func_176201_c(state));
                if (Item.func_150898_a(block) instanceof OreItemBlock) {
                    var form = MaterialBlockDeclarations.toItem(state);
                    row.put("generatedForm", stack(form));
                    row.put("oreNames", Arrays.stream(OreDictionary.getOreIDs(form)).mapToObj(OreDictionary::getOreName).toList());
                    var entry = OreDictUnifier.getUnificationEntry(form);
                    row.put("unificationEntry", entry == null ? Map.of() : Map.of("prefix", entry.orePrefix.name(), "material", entry.material.getRegistryName()));
                    row.put("selectedByUnifier", stack(OreDictUnifier.get(stone.processingPrefix, block.material, 1)));
                    if (itemsRegistered) {
                        row.put("ordinaryDrop", stack(new ItemStack(block.func_180660_a(state, new Random(0), 0), 1, block.func_180651_a(state))));
                        row.put("silkSelection", stack(block.func_180643_i(state)));
                    }
                }
                variants.add(row);
            }
            entries.add(Map.of("registryName", block.getRegistryName().toString(), "material", block.material.getRegistryName(),
                    "blockRegistered", ForgeRegistries.BLOCKS.getValue(block.getRegistryName()) == block,
                    "itemRegistered", Item.func_150898_a(block) instanceof OreItemBlock, "variants", variants));
        }
        return Map.of("stoneTypes", types, "oreBlocks", entries, "addonStoneDeclarations", addonAdmitted,
                "worldGenerationQualified", false, "harvestingEventsQualified", false);
    }
}
