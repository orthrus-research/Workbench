// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.Block;

import net.minecraft.block.properties.PropertyEnum;
import net.minecraft.block.state.BlockStateContainer;
import net.minecraft.block.state.IBlockState;
import net.minecraft.client.resources.I18n;
import net.minecraft.client.util.ITooltipFlag;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.item.ItemStack;
import net.minecraft.util.IStringSerializable;
import net.minecraft.util.NonNullList;
import net.minecraft.world.World;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.fml.relauncher.SideOnly;


import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.util.Collections;
import java.util.List;

@SuppressWarnings("deprecation")
public class VariantBlock<T extends Enum<T> & IStringSerializable> extends Block implements IWalkingSpeedBonus {

    protected PropertyEnum<T> VARIANT;
    protected T[] VALUES;

    @SuppressWarnings("DataFlowIssue")
    public VariantBlock(net.minecraft.block.material.Material materialIn) {
        super(materialIn);
        if (VALUES.length > 0 && VALUES[0] instanceof IStateHarvestLevel) {
            for (T t : VALUES) {
                IStateHarvestLevel stateHarvestLevel = (IStateHarvestLevel) t;
                IBlockState state = getState(t);
                setHarvestLevel(stateHarvestLevel.getHarvestTool(state), stateHarvestLevel.getHarvestLevel(state),
                        state);
            }
        }
        func_149647_a(OreHostBlocks.TAB_GREGTECH);
        func_180632_j(this.field_176227_L.func_177621_b().func_177226_a(VARIANT, VALUES[0]));
    }

    @Override
    public void func_149666_a( CreativeTabs tab,  NonNullList<ItemStack> list) {
        for (T variant : VALUES) {
            list.add(getItemVariant(variant));
        }
    }

    public IBlockState getState(T variant) {
        return func_176223_P().func_177226_a(VARIANT, variant);
    }

    public T getState(IBlockState blockState) {
        return blockState.func_177229_b(VARIANT);
    }

    public T getState(ItemStack stack) {
        return getState(func_176203_a(stack.func_77952_i()));
    }

    public ItemStack getItemVariant(T variant) {
        return getItemVariant(variant, 1);
    }

    public ItemStack getItemVariant(T variant, int amount) {
        return new ItemStack(this, amount, variant.ordinal());
    }

    
    @Override
    protected BlockStateContainer func_180661_e() {
        Class<T> enumClass = getActualTypeParameter(getClass(), VariantBlock.class);
        this.VARIANT = PropertyEnum.func_177709_a("variant", enumClass);
        this.VALUES = enumClass.getEnumConstants();
        return new BlockStateContainer(this, VARIANT);
    }

    @Override
    @SideOnly(Side.CLIENT)
    public void func_190948_a( ItemStack stack,  World player,  List<String> tooltip,
                                ITooltipFlag advanced) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public void addInformation"); }

    @Override
    public int func_180651_a( IBlockState state) {
        return func_176201_c(state);
    }

    
    @Override
    @SuppressWarnings("deprecation")
    public IBlockState func_176203_a(int meta) {
        return func_176223_P().func_177226_a(VARIANT, VALUES[meta % VALUES.length]);
    }

    @Override
    public int func_176201_c(IBlockState state) {
        return state.func_177229_b(VARIANT).ordinal();
    }

    // magic is here
    @SuppressWarnings("unchecked")
    protected static <T, R> Class<T> getActualTypeParameter(Class<? extends R> thisClass, Class<R> declaringClass) {
        Type type = thisClass.getGenericSuperclass();

        while (!(type instanceof ParameterizedType) || ((ParameterizedType) type).getRawType() != declaringClass) {
            if (type instanceof ParameterizedType) {
                type = ((Class<?>) ((ParameterizedType) type).getRawType()).getGenericSuperclass();
            } else {
                type = ((Class<?>) type).getGenericSuperclass();
            }
        }
        return (Class<T>) ((ParameterizedType) type).getActualTypeArguments()[0];
    }
}
