// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.BlockRedSandstone;
import net.minecraft.block.BlockSandStone;
import net.minecraft.block.BlockStone;
import net.minecraft.block.BlockStone.EnumType;
import net.minecraft.block.SoundType;
import net.minecraft.block.state.IBlockState;
import net.minecraft.init.Blocks;

public class StoneTypes {

    // Real Types that drop custom Ores

    public static final StoneType STONE = new StoneType(0, "stone", SoundType.field_185851_d, OrePrefix.ore, PrefixDependencies.material("Stone"),
            () -> Blocks.field_150348_b.func_176223_P().func_177226_a(BlockStone.field_176247_a, EnumType.STONE),
            state -> state.func_177230_c() instanceof BlockStone &&
                    state.func_177229_b(BlockStone.field_176247_a) == BlockStone.EnumType.STONE,
            true);

    public static StoneType NETHERRACK = new StoneType(1, "netherrack", SoundType.field_185851_d, OrePrefix.oreNetherrack,
            PrefixDependencies.material("Netherrack"),
            Blocks.field_150424_aL::func_176223_P,
            state -> state.func_177230_c() == Blocks.field_150424_aL, true);

    public static StoneType ENDSTONE = new StoneType(2, "endstone", SoundType.field_185851_d, OrePrefix.oreEndstone,
            PrefixDependencies.material("Endstone"),
            Blocks.field_150377_bs::func_176223_P,
            state -> state.func_177230_c() == Blocks.field_150377_bs, true);

    // Dummy Types used for better world generation

    public static StoneType SANDSTONE = new StoneType(3, "sandstone", SoundType.field_185851_d, OrePrefix.oreSand,
            PrefixDependencies.material("SiliconDioxide"),
            () -> Blocks.field_150322_A.func_176223_P().func_177226_a(BlockSandStone.field_176297_a, BlockSandStone.EnumType.DEFAULT),
            state -> state.func_177230_c() instanceof BlockSandStone &&
                    state.func_177229_b(BlockSandStone.field_176297_a) == BlockSandStone.EnumType.DEFAULT,
            false);

    public static StoneType RED_SANDSTONE = new StoneType(4, "red_sandstone", SoundType.field_185851_d, OrePrefix.oreRedSand,
            PrefixDependencies.material("SiliconDioxide"),
            () -> Blocks.field_180395_cM.func_176223_P().func_177226_a(BlockRedSandstone.field_176336_a,
                    BlockRedSandstone.EnumType.DEFAULT),
            state -> state.func_177230_c() instanceof BlockRedSandstone &&
                    state.func_177229_b(BlockRedSandstone.field_176336_a) == BlockRedSandstone.EnumType.DEFAULT,
            false);

    public static StoneType GRANITE = new StoneType(5, "granite", SoundType.field_185851_d, OrePrefix.oreGranite,
            PrefixDependencies.material("Granite"),
            () -> Blocks.field_150348_b.func_176223_P().func_177226_a(BlockStone.field_176247_a, EnumType.GRANITE),
            state -> state.func_177230_c() instanceof BlockStone && state.func_177229_b(BlockStone.field_176247_a) == EnumType.GRANITE,
            false);

    public static StoneType DIORITE = new StoneType(6, "diorite", SoundType.field_185851_d, OrePrefix.oreDiorite,
            PrefixDependencies.material("Diorite"),
            () -> Blocks.field_150348_b.func_176223_P().func_177226_a(BlockStone.field_176247_a, EnumType.DIORITE),
            state -> state.func_177230_c() instanceof BlockStone && state.func_177229_b(BlockStone.field_176247_a) == EnumType.DIORITE,
            false);

    public static StoneType ANDESITE = new StoneType(7, "andesite", SoundType.field_185851_d, OrePrefix.oreAndesite,
            PrefixDependencies.material("Andesite"),
            () -> Blocks.field_150348_b.func_176223_P().func_177226_a(BlockStone.field_176247_a, BlockStone.EnumType.ANDESITE),
            state -> state.func_177230_c() instanceof BlockStone && state.func_177229_b(BlockStone.field_176247_a) == EnumType.ANDESITE,
            false);

    public static StoneType BLACK_GRANITE = new StoneType(8, "black_granite", SoundType.field_185851_d,
            OrePrefix.oreBlackgranite, PrefixDependencies.material("GraniteBlack"),
            () -> gtStoneState(StoneVariantBlock.StoneType.BLACK_GRANITE),
            state -> gtStonePredicate(state, StoneVariantBlock.StoneType.BLACK_GRANITE), false);

    public static StoneType RED_GRANITE = new StoneType(9, "red_granite", SoundType.field_185851_d, OrePrefix.oreRedgranite,
            PrefixDependencies.material("GraniteRed"),
            () -> gtStoneState(StoneVariantBlock.StoneType.RED_GRANITE),
            state -> gtStonePredicate(state, StoneVariantBlock.StoneType.RED_GRANITE), false);

    public static StoneType MARBLE = new StoneType(10, "marble", SoundType.field_185851_d, OrePrefix.oreMarble, PrefixDependencies.material("Marble"),
            () -> gtStoneState(StoneVariantBlock.StoneType.MARBLE),
            state -> gtStonePredicate(state, StoneVariantBlock.StoneType.MARBLE), false);

    public static StoneType BASALT = new StoneType(11, "basalt", SoundType.field_185851_d, OrePrefix.oreBasalt, PrefixDependencies.material("Basalt"),
            () -> gtStoneState(StoneVariantBlock.StoneType.BASALT),
            state -> gtStonePredicate(state, StoneVariantBlock.StoneType.BASALT), false);

    private static IBlockState gtStoneState(StoneVariantBlock.StoneType stoneType) {
        return OreHostBlocks.STONE_BLOCKS.get(StoneVariantBlock.StoneVariant.SMOOTH).getState(stoneType);
    }

    private static boolean gtStonePredicate(IBlockState state, StoneVariantBlock.StoneType stoneType) {
        StoneVariantBlock block = OreHostBlocks.STONE_BLOCKS.get(StoneVariantBlock.StoneVariant.SMOOTH);
        return state.func_177230_c() == block && block.getState(state) == stoneType;
    }
}
