// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.Block;
import net.minecraft.block.SoundType;
import net.minecraft.block.state.BlockStateContainer;
import net.minecraft.block.state.IBlockState;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.entity.Entity;
import net.minecraft.entity.player.EntityPlayer;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.BlockRenderLayer;
import net.minecraft.util.EnumFacing;
import net.minecraft.util.NonNullList;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.RayTraceResult;
import net.minecraft.world.World;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.fml.relauncher.SideOnly;


import java.util.Objects;
import java.util.Random;
import java.util.stream.Collectors;

public class BlockOre extends Block implements IBlockOre {

    public final PropertyStoneType STONE_TYPE;
    public final FluidMaterial material;

    public BlockOre(FluidMaterial material, StoneType[] allowedValues) {
        super(net.minecraft.block.material.Material.field_151576_e);
        func_149663_c("ore_block");
        func_149672_a(SoundType.field_185851_d);
        func_149711_c(3.0f);
        func_149752_b(5.0f);
        this.material = Objects.requireNonNull(material, "Material in BlockOre can not be null!");
        STONE_TYPE = PropertyStoneType.create("stone_type", allowedValues);
        initBlockState();
        func_149647_a(OreHostBlocks.TAB_GREGTECH_ORES);
    }

    
    @SuppressWarnings("deprecation")
    @Override
    public net.minecraft.block.material.Material func_149688_o( IBlockState state) {
        String harvestTool = getHarvestTool(state);
        if (harvestTool != null && harvestTool.equals(MaterialBlockDeclarations.SHOVEL)) {
            return net.minecraft.block.material.Material.field_151578_c;
        }
        return net.minecraft.block.material.Material.field_151576_e;
    }

    
    @Override
    protected final BlockStateContainer func_180661_e() {
        return new BlockStateContainer(this);
    }

    protected void initBlockState() {
        BlockStateContainer stateContainer = createStateContainer();
        this.field_176227_L = stateContainer;
        func_180632_j(stateContainer.func_177621_b());
    }

    
    @Override
    public Item func_180660_a( IBlockState state,  Random rand, int fortune) {
        StoneType stoneType = state.func_177229_b(STONE_TYPE);
        // if the stone type should be dropped as an item, or if it is within the first 16 block states
        // don't do any special handling
        if (stoneType.shouldBeDroppedAsItem || StoneType.STONE_TYPE_REGISTRY.getIDForObject(stoneType) < 16) {
            return super.func_180660_a(state, rand, fortune);
        }

        // always drop StoneTypes.STONE as the default
        // this prevents stone types of id>15 from dropping the meta=0 variant of the block,
        // which might not be the block with the vanilla stone type
        IBlockState stoneOre = OreDeclarations.getOreForMaterial(this.material).get(StoneTypes.STONE);
        return Item.func_150898_a(stoneOre.func_177230_c());
    }

    @Override
    public int func_180651_a( IBlockState state) {
        StoneType stoneType = state.func_177229_b(STONE_TYPE);
        if (stoneType.shouldBeDroppedAsItem) {
            return func_176201_c(state);
        } else {
            return 0;
        }
    }

    
    @Override
    public SoundType getSoundType(IBlockState state,  World world,  BlockPos pos,
                                   Entity entity) {
        StoneType stoneType = state.func_177229_b(STONE_TYPE);
        return stoneType.soundType;
    }

    @Override
    public String getHarvestTool(IBlockState state) {
        StoneType stoneType = state.func_177229_b(STONE_TYPE);
        IBlockState stoneState = stoneType.stone.get();
        return stoneState.func_177230_c().getHarvestTool(stoneState);
    }

    @Override
    public int getHarvestLevel(IBlockState state) {
        // this is save because ore blocks and stone types only generate for materials with dust property
        return Math.max(state.func_177229_b(STONE_TYPE).stoneMaterial.getBlockHarvestLevel(),
                material.getBlockHarvestLevel());
    }

    
    @Override
    protected ItemStack func_180643_i(IBlockState state) {
        StoneType stoneType = state.func_177229_b(STONE_TYPE);
        if (stoneType.shouldBeDroppedAsItem) {
            return super.func_180643_i(state);
        }
        return super.func_180643_i(this.func_176223_P());
    }

    
    @Override
    @SuppressWarnings("deprecation")
    public IBlockState func_176203_a(int meta) {
        if (meta >= STONE_TYPE.func_177700_c().size()) {
            meta = 0;
        }
        return func_176223_P().func_177226_a(STONE_TYPE, STONE_TYPE.func_177700_c().get(meta));
    }

    @Override
    public int func_176201_c(IBlockState state) {
        return STONE_TYPE.func_177700_c().indexOf(state.func_177229_b(STONE_TYPE));
    }

    
    @Override
    public ItemStack getPickBlock( IBlockState state,  RayTraceResult target,  World world,
                                   BlockPos pos,  EntityPlayer player) {
        // Still get correct block even if shouldBeDroppedAsItem is false
        return MaterialBlockDeclarations.toItem(state);
    }

    @Override
    public boolean isFireSource( World world,  BlockPos pos,  EnumFacing side) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public boolean isFireSource"); }

    @Override
    public void func_149666_a( CreativeTabs tab,  NonNullList<ItemStack> list) {
        if (tab == CreativeTabs.field_78027_g || tab == OreHostBlocks.TAB_GREGTECH_ORES) {
            field_176227_L.func_177619_a().stream()
                    .filter(state -> state.func_177229_b(STONE_TYPE).shouldBeDroppedAsItem)
                    .forEach(field_176227_L -> list.add(MaterialBlockDeclarations.toItem(field_176227_L)));
        }
    }

    @Override
    public boolean canRenderInLayer( IBlockState state,  BlockRenderLayer layer) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public boolean canRenderInLayer"); }

    private BlockStateContainer createStateContainer() {
        return new BlockStateContainer(this, STONE_TYPE);
    }

    @Override
    public IBlockState getOreBlock(StoneType stoneType) {
        return this.func_176223_P().func_177226_a(this.STONE_TYPE, stoneType);
    }

    @SideOnly(Side.CLIENT)
    public void onModelRegister() { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public void onModelRegister"); }
}
