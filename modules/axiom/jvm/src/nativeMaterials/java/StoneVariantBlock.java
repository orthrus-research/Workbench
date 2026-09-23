// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.SoundType;
import net.minecraft.block.material.MapColor;
import net.minecraft.block.properties.PropertyEnum;
import net.minecraft.block.state.BlockStateContainer;
import net.minecraft.block.state.IBlockState;
import net.minecraft.entity.EntityLiving;
import net.minecraft.item.Item;
import net.minecraft.util.IStringSerializable;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.IBlockAccess;


import java.util.Random;

@SuppressWarnings("deprecation")
public class StoneVariantBlock extends VariantBlock<StoneVariantBlock.StoneType> {

    // shared property instance
    private static final PropertyEnum<StoneType> PROPERTY = PropertyEnum.func_177709_a("variant", StoneType.class);

    private final StoneVariant stoneVariant;

    public StoneVariantBlock( StoneVariant stoneVariant) {
        super(net.minecraft.block.material.Material.field_151576_e);
        this.stoneVariant = stoneVariant;
        setRegistryName(stoneVariant.id);
        func_149663_c(stoneVariant.translationKey);
        func_149711_c(stoneVariant.hardness);
        func_149752_b(stoneVariant.resistance);
        func_149672_a(SoundType.field_185851_d);
        setHarvestLevel(MaterialBlockDeclarations.PICKAXE, 0);
        func_180632_j(getState(StoneType.BLACK_GRANITE));
        func_149647_a(OreHostBlocks.TAB_GREGTECH_DECORATIONS);
    }

    
    @Override
    protected BlockStateContainer func_180661_e() {
        this.VARIANT = PROPERTY;
        this.VALUES = StoneType.values();
        return new BlockStateContainer(this, VARIANT);
    }

    @Override
    public boolean canCreatureSpawn( IBlockState state,  IBlockAccess world,  BlockPos pos,
                                     EntityLiving.SpawnPlacementType type) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public boolean canCreatureSpawn"); }

    @Override
    protected boolean func_149700_E() {
        return this.stoneVariant == StoneVariant.SMOOTH;
    }

    
    @Override
    public Item func_180660_a( IBlockState state,  Random rand, int fortune) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public Item getItemDropped"); }

    public enum StoneType implements IStringSerializable {

        BLACK_GRANITE("black_granite", MapColor.field_151646_E),
        RED_GRANITE("red_granite", MapColor.field_151645_D),
        MARBLE("marble", MapColor.field_151677_p),
        BASALT("basalt", MapColor.field_193560_ab),
        CONCRETE_LIGHT("concrete_light", MapColor.field_151665_m),
        CONCRETE_DARK("concrete_dark", MapColor.field_151665_m);

        private final String name;
        public final MapColor mapColor;

        StoneType( String name,  MapColor mapColor) {
            this.name = name;
            this.mapColor = mapColor;
        }

        
        @Override
        public String func_176610_l() {
            return this.name;
        }

        public OrePrefix getOrePrefix() {
            return switch (this) {
                case BLACK_GRANITE, RED_GRANITE, MARBLE, BASALT -> OrePrefix.stone;
                case CONCRETE_LIGHT, CONCRETE_DARK -> OrePrefix.block;
            };
        }

        public FluidMaterial getMaterial() {
            return switch (this) {
                case BLACK_GRANITE -> PrefixDependencies.material("GraniteBlack");
                case RED_GRANITE -> PrefixDependencies.material("GraniteRed");
                case MARBLE -> PrefixDependencies.material("Marble");
                case BASALT -> PrefixDependencies.material("Basalt");
                case CONCRETE_LIGHT, CONCRETE_DARK -> PrefixDependencies.material("Concrete");
            };
        }
    }

    public enum StoneVariant {

        SMOOTH("stone_smooth"),
        COBBLE("stone_cobble", 2.0f, 10.0f),
        COBBLE_MOSSY("stone_cobble_mossy", 2.0f, 10.0f),
        POLISHED("stone_polished"),
        BRICKS("stone_bricks"),
        BRICKS_CRACKED("stone_bricks_cracked"),
        BRICKS_MOSSY("stone_bricks_mossy"),
        CHISELED("stone_chiseled"),
        TILED("stone_tiled"),
        TILED_SMALL("stone_tiled_small"),
        BRICKS_SMALL("stone_bricks_small"),
        WINDMILL_A("stone_windmill_a", "stone_bricks_windmill_a"),
        WINDMILL_B("stone_windmill_b", "stone_bricks_windmill_b"),
        BRICKS_SQUARE("stone_bricks_square");

        public final String id;
        public final String translationKey;
        public final float hardness;
        public final float resistance;

        StoneVariant( String id) {
            this(id, id);
        }

        StoneVariant( String id,  String translationKey) {
            this(id, translationKey, 1.5f, 10.0f); // vanilla stone stats
        }

        StoneVariant( String id, float hardness, float resistance) {
            this(id, id, hardness, resistance);
        }

        StoneVariant( String id,  String translationKey, float hardness, float resistance) {
            this.id = id;
            this.translationKey = translationKey;
            this.hardness = hardness;
            this.resistance = resistance;
        }
    }
}
