// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.
package research.orthrus.axiom.nativeconstruction;
import java.util.*;
import java.util.Map.Entry;
import java.util.function.*;
import it.unimi.dsi.fastutil.ints.*;
import it.unimi.dsi.fastutil.objects.*;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.item.*;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.IForgeRegistry;
final class MaterialBlockDeclarations {
public static final Map<FluidMaterial, BlockCompressed> COMPRESSED = new Object2ObjectOpenHashMap<>();
public static final Map<FluidMaterial, BlockFrame> FRAMES = new Object2ObjectOpenHashMap<>();
public static final List<BlockCompressed> COMPRESSED_BLOCKS = new ArrayList<>();
public static final List<BlockFrame> FRAME_BLOCKS = new ArrayList<>();
protected static void createGeneratedBlock(Predicate<FluidMaterial> materialPredicate,
                                               TriConsumer<String, FluidMaterial[], Integer> blockGenerator) {
        for (MaterialRegistry registry : FluidEnvironment.current().runtime().materials().getRegistries()) {
            Int2ObjectMap<FluidMaterial[]> blocksToGenerate = new Int2ObjectAVLTreeMap<>();
            for (MaterialState state : registry) {
FluidMaterial material = (FluidMaterial) state;
                if (materialPredicate.test(material)) {
                    int id = material.getId();
                    int metaBlockID = id / 16;
                    int subBlockID = id % 16;

                    if (!blocksToGenerate.containsKey(metaBlockID)) {
                        FluidMaterial[] materials = new FluidMaterial[16];
                        Arrays.fill(materials, PrefixDependencies.material("NULL"));
                        blocksToGenerate.put(metaBlockID, materials);
                    }

                    blocksToGenerate.get(metaBlockID)[subBlockID] = material;
                }
            }
            blocksToGenerate.forEach((key, value) -> blockGenerator.accept(registry.getModid(), value, key));
        }
    }
private static void createCompressedBlock(String modid, FluidMaterial[] materials, int index) {
        BlockCompressed block = BlockCompressed.create(materials);
        block.setRegistryName(modid, "meta_block_compressed_" + index);
        for (FluidMaterial m : materials) {
            COMPRESSED.put(m, block);
        }
        COMPRESSED_BLOCKS.add(block);
    }
private static void createFrameBlock(String modid, FluidMaterial[] materials, int index) {
        BlockFrame block = BlockFrame.create(materials);
        block.setRegistryName(modid, "meta_block_frame_" + index);
        for (FluidMaterial m : materials) {
            FRAMES.put(m, block);
        }
        FRAME_BLOCKS.add(block);
    }
static void construct() {
createGeneratedBlock(m -> m.hasProperty(PropertyKey.DUST) && m.hasFlag(MaterialFlags.GENERATE_FRAME),
                MaterialBlockDeclarations::createFrameBlock);
createGeneratedBlock(
                material -> (material.hasProperty(PropertyKey.INGOT) || material.hasProperty(PropertyKey.GEM) ||
                        material.hasFlag(MaterialFlags.FORCE_GENERATE_BLOCK)) && !OrePrefix.block.isIgnored(material),
                MaterialBlockDeclarations::createCompressedBlock);
}
static void registerBlocks(IForgeRegistry<Block> registry) {
for (BlockCompressed block : COMPRESSED_BLOCKS) registry.register(block);
for (BlockFrame block : FRAME_BLOCKS) registry.register(block);
}
static void registerItems(IForgeRegistry<Item> registry) {
for (BlockCompressed block : COMPRESSED_BLOCKS) {
            registry.register(createItemBlock(block, b -> new MaterialItemBlock(b, OrePrefix.block)));
        }
for (BlockFrame block : FRAME_BLOCKS) {
            registry.register(createItemBlock(block, b -> new MaterialItemBlock(b, OrePrefix.frameGt)));
        }
}
private static <T extends Block> ItemBlock createItemBlock(T block, Function<T, ItemBlock> producer) {
        ItemBlock itemBlock = producer.apply(block);
        ResourceLocation registryName = block.getRegistryName();
        if (registryName == null) {
            throw new IllegalArgumentException("Block " + block.func_149739_a() + " has no registry name.");
        }
        itemBlock.setRegistryName(registryName);
        return itemBlock;
    }
static void registerOres() {
for (Entry<FluidMaterial, BlockCompressed> entry : COMPRESSED.entrySet()) {
            FluidMaterial material = entry.getKey();
            BlockCompressed block = entry.getValue();
            ItemStack itemStack = block.getItem(material);
            OreDictUnifier.registerOre(itemStack, OrePrefix.block, material);
        }
for (Entry<FluidMaterial, BlockFrame> entry : FRAMES.entrySet()) {
            FluidMaterial material = entry.getKey();
            BlockFrame block = entry.getValue();
            ItemStack itemStack = block.getItem(material);
            OreDictUnifier.registerOre(itemStack, OrePrefix.frameGt, material);
        }
}
public static ItemStack toItem(IBlockState state) {
        return toItem(state, 1);
    }
public static ItemStack toItem(IBlockState state, int amount) {
        return new ItemStack(state.func_177230_c(), amount, state.func_177230_c().func_176201_c(state));
    }
public static boolean isMaterialWood( FluidMaterial material) {
        return material != null && material.hasProperty(PropertyKey.WOOD);
    }
public static final String AXE = "axe";
public static final String PICKAXE = "pickaxe";
public static final String SHOVEL = "shovel";
public static final String WRENCH = "wrench";
}
