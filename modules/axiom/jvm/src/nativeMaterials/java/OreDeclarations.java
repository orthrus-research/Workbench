// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;
import java.util.*;
import java.util.stream.*;
import net.minecraft.block.*;
import net.minecraft.block.state.*;
import net.minecraft.item.*;
import net.minecraftforge.registries.IForgeRegistry;
import org.apache.commons.lang3.ArrayUtils;
import java.util.function.Function;
import net.minecraft.util.ResourceLocation;

final class OreDeclarations {
public static final List<BlockOre> ORES = new ArrayList<>();
public static final Map<FluidMaterial, Map<StoneType, IBlockOre>> oreBlockTable = new HashMap<>();
private static void createOreBlock(FluidMaterial material) {
        StoneType[] stoneTypeBuffer = new StoneType[16];
        int generationIndex = 0;
        for (StoneType stoneType : StoneType.STONE_TYPE_REGISTRY) {
            int id = StoneType.STONE_TYPE_REGISTRY.getIDForObject(stoneType), index = id / 16;
            if (index > generationIndex) {
                createOreBlock(material, copyNotNull(stoneTypeBuffer), generationIndex);
                Arrays.fill(stoneTypeBuffer, null);
            }
            stoneTypeBuffer[id % 16] = stoneType;
            generationIndex = index;
        }
        createOreBlock(material, copyNotNull(stoneTypeBuffer), generationIndex);
    }
private static <T> T[] copyNotNull(T[] src) {
        int nullIndex = ArrayUtils.indexOf(src, null);
        return Arrays.copyOfRange(src, 0, nullIndex == -1 ? src.length : nullIndex);
    }
private static void createOreBlock(FluidMaterial material, StoneType[] stoneTypes, int index) {
        BlockOre block = new BlockOre(material, stoneTypes);
        block.setRegistryName("ore_" + material + "_" + index);
        for (StoneType stoneType : stoneTypes) {
            OreDeclarations.oreBlockTable.computeIfAbsent(material, m -> new HashMap<>()).put(stoneType, block);
        }
        ORES.add(block);
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
static void generate() {
StoneType.init();
for (MaterialRegistry materialRegistry : FluidEnvironment.current().runtime().materials().getRegistries()) {
for (MaterialState state : materialRegistry) {
FluidMaterial material = (FluidMaterial) state;
if (material.hasProperty(PropertyKey.ORE) && !material.hasFlag(MaterialFlags.DISABLE_ORE_BLOCK)) {
                    createOreBlock(material);
                }
}}
}
static void registerBlocks(IForgeRegistry<Block> registry) { for (BlockOre block : ORES) registry.register(block); }
static void registerItems(IForgeRegistry<Item> registry) {
for (BlockOre block : ORES) {
            registry.register(createItemBlock(block, OreItemBlock::new));
        }
}
static void registerOres() {
for (BlockOre blockOre : ORES) {
            FluidMaterial material = blockOre.material;
            for (StoneType stoneType : blockOre.STONE_TYPE.func_177700_c()) {
                if (stoneType == null) continue;
                ItemStack normalStack = MaterialBlockDeclarations.toItem(blockOre.func_176223_P()
                        .func_177226_a(blockOre.STONE_TYPE, stoneType));
                OreDictUnifier.registerOre(normalStack, stoneType.processingPrefix, material);
            }
        }
}
public static Map<StoneType, IBlockState> getOreForMaterial(FluidMaterial material) {
        List<BlockOre> oreBlocks = OreDeclarations.ORES.stream()
                .filter(ore -> ore.material == material)
                .collect(Collectors.toList());
        Map<StoneType, IBlockState> stoneTypeMap = new HashMap<>();
        for (BlockOre blockOre : oreBlocks) {
            for (StoneType stoneType : blockOre.STONE_TYPE.func_177700_c()) {
                IBlockState blockState = blockOre.getOreBlock(stoneType);
                stoneTypeMap.put(stoneType, blockState);
            }
        }
        if (stoneTypeMap.isEmpty()) {
            throw new IllegalArgumentException("There is no ore generated for material " + material);
        }
        return stoneTypeMap;
    }
}
