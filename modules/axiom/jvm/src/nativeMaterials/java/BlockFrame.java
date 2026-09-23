// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.Block;
import net.minecraft.block.SoundType;
import net.minecraft.block.material.EnumPushReaction;
import net.minecraft.block.state.BlockFaceShape;
import net.minecraft.block.state.IBlockState;
import net.minecraft.client.util.ITooltipFlag;
import net.minecraft.entity.Entity;
import net.minecraft.entity.EntityLiving.SpawnPlacementType;
import net.minecraft.entity.player.EntityPlayer;
import net.minecraft.item.Item;
import net.minecraft.item.ItemBlock;
import net.minecraft.item.ItemStack;
import net.minecraft.tileentity.TileEntity;
import net.minecraft.util.BlockRenderLayer;
import net.minecraft.util.EnumFacing;
import net.minecraft.util.EnumHand;
import net.minecraft.util.SoundCategory;
import net.minecraft.util.math.AxisAlignedBB;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.MathHelper;
import net.minecraft.world.IBlockAccess;
import net.minecraft.world.World;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.fml.relauncher.SideOnly;


import java.util.List;

public abstract class BlockFrame extends BlockMaterialBase {
public static final AxisAlignedBB COLLISION_BOX = new AxisAlignedBB(0.05, 0.0, 0.05, 0.95, 1.0, 0.95);
public static BlockFrame create(FluidMaterial[] materials) {
        PropertyMaterial property = PropertyMaterial.create("variant", materials);
        return new BlockFrame() {

            
            @Override
            public PropertyMaterial getVariantProperty() {
                return property;
            }
        };
    }
private BlockFrame() {
        super(net.minecraft.block.material.Material.field_151573_f);
        func_149663_c("frame");
        func_149711_c(3.0f);
        func_149752_b(6.0f);
        func_149647_a(MaterialItemDeclarations.TAB_GREGTECH_MATERIALS);
    }
@Override
public String getHarvestTool( IBlockState state) {
        FluidMaterial material = getGtMaterial(state);
        if (MaterialBlockDeclarations.isMaterialWood(material)) {
            return MaterialBlockDeclarations.AXE;
        }
        return MaterialBlockDeclarations.WRENCH;
    }
@Override
public SoundType getSoundType( IBlockState state,  World world,  BlockPos pos,
                                   Entity entity) {
        FluidMaterial material = getGtMaterial(state);
        if (MaterialBlockDeclarations.isMaterialWood(material)) {
            return SoundType.field_185848_a;
        }
        return SoundType.field_185852_e;
    }
public SoundType getSoundType(ItemStack stack) {
        FluidMaterial material = getGtMaterial(stack);
        if (MaterialBlockDeclarations.isMaterialWood(material)) {
            return SoundType.field_185848_a;
        }
        return SoundType.field_185852_e;
    }
@Override
public int getHarvestLevel( IBlockState state) {
        return 1;
    }
@Override
public net.minecraft.block.material.Material func_149688_o( IBlockState state) {
        FluidMaterial material = getGtMaterial(state);
        if (MaterialBlockDeclarations.isMaterialWood(material)) {
            return net.minecraft.block.material.Material.field_151575_d;
        }
        return super.func_149688_o(state);
    }
@Override
public boolean canCreatureSpawn( IBlockState state,  IBlockAccess world,  BlockPos pos,
                                     SpawnPlacementType type) {
        return false;
    }
public boolean replaceWithFramedPipe(World worldIn, BlockPos pos, IBlockState state, EntityPlayer playerIn,
                                         ItemStack stackInHand, EnumFacing facing) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: replaceWithFramedPipe"); }
public boolean removeFrame(World world, BlockPos pos, EntityPlayer player, ItemStack stack) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: removeFrame"); }
@Override
public boolean func_180639_a( World world,  BlockPos pos,  IBlockState state,
                                     EntityPlayer player,  EnumHand hand,  EnumFacing facing,
                                    float hitX, float hitY, float hitZ) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: onBlockActivated"); }
@Override
public void func_180634_a( World worldIn,  BlockPos pos,  IBlockState state,
                                  Entity entityIn) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: onEntityCollision"); }
@Override
public EnumPushReaction func_149656_h( IBlockState state) {
        return EnumPushReaction.DESTROY;
    }
@Override
public AxisAlignedBB func_180646_a( IBlockState blockState,  IBlockAccess worldIn,
                                                  BlockPos pos) {
        return COLLISION_BOX;
    }
@Override
public BlockRenderLayer func_180664_k() {
        return BlockRenderLayer.CUTOUT_MIPPED;
    }
@Override
public boolean func_149662_c( IBlockState state) {
        return false;
    }
@Override
public BlockFaceShape func_193383_a( IBlockAccess worldIn,  IBlockState state,
                                             BlockPos pos,  EnumFacing face) {
        return BlockFaceShape.UNDEFINED;
    }
@Override
public void func_190948_a( ItemStack stack,  World world,  List<String> tooltip,
                                ITooltipFlag flag) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: addInformation"); }
public void onModelRegister() { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: onModelRegister"); }
public static BlockFrame getFrameBlockFromItem(ItemStack stack) {
        Item item = stack.func_77973_b();
        if (item instanceof ItemBlock) {
            Block block = ((ItemBlock) item).func_179223_d();
            if (block instanceof BlockFrame) return (BlockFrame) block;
        }
        return null;
    }
}
