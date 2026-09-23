// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.Block;
import net.minecraft.block.material.MapColor;
import net.minecraft.block.state.BlockStateContainer;
import net.minecraft.block.state.IBlockState;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.item.ItemStack;
import net.minecraft.util.EnumFacing;
import net.minecraft.util.NonNullList;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.IBlockAccess;


public abstract class BlockMaterialBase extends Block {
public BlockMaterialBase(net.minecraft.block.material.Material material) {
        super(material);
    }
public ItemStack getItem( FluidMaterial material) {
        return MaterialBlockDeclarations.toItem(func_176223_P().func_177226_a(getVariantProperty(), material));
    }
public FluidMaterial getGtMaterial(int meta) {
        if (meta >= getVariantProperty().func_177700_c().size()) {
            meta = 0;
        }
        return getVariantProperty().func_177700_c().get(meta);
    }
public FluidMaterial getGtMaterial( ItemStack stack) {
        return getGtMaterial(stack.func_77960_j());
    }
public FluidMaterial getGtMaterial( IBlockState state) {
        return state.func_177229_b(getVariantProperty());
    }
public IBlockState getBlock( FluidMaterial material) {
        return func_176223_P().func_177226_a(getVariantProperty(), material);
    }
@Override
protected BlockStateContainer func_180661_e() {
        return new BlockStateContainer(this, getVariantProperty());
    }
@Override
public IBlockState func_176203_a(int meta) {
        return func_176223_P().func_177226_a(getVariantProperty(), getGtMaterial(meta));
    }
@Override
public int func_176201_c( IBlockState state) {
        return getVariantProperty().func_177700_c().indexOf(state.func_177229_b(getVariantProperty()));
    }
@Override
public int func_180651_a( IBlockState state) {
        return func_176201_c(state);
    }
@Override
public void func_149666_a( CreativeTabs tab,  NonNullList<ItemStack> list) {
        for (IBlockState state : field_176227_L.func_177619_a()) {
            if (getGtMaterial(state) != PrefixDependencies.material("NULL")) {
                list.add(MaterialBlockDeclarations.toItem(state));
            }
        }
    }
@Override
public MapColor func_180659_g( IBlockState state,  IBlockAccess worldIn,  BlockPos pos) {
        return func_149688_o(state).func_151565_r();
    }
@Override
public int getFlammability( IBlockAccess world,  BlockPos pos,  EnumFacing face) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: getFlammability"); }
@Override
public int getFireSpreadSpeed( IBlockAccess world,  BlockPos pos,  EnumFacing face) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: getFireSpreadSpeed"); }
public abstract PropertyMaterial getVariantProperty();
}
