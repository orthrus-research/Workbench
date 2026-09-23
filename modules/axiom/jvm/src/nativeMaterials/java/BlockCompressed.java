// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.SoundType;
import net.minecraft.block.state.IBlockState;
import net.minecraft.client.util.ITooltipFlag;
import net.minecraft.entity.Entity;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraftforge.fml.relauncher.Side;
import net.minecraftforge.fml.relauncher.SideOnly;


import java.util.List;

public abstract class BlockCompressed extends BlockMaterialBase {
public static BlockCompressed create(FluidMaterial[] materials) {
        PropertyMaterial property = PropertyMaterial.create("variant", materials);
        return new BlockCompressed() {

            
            @Override
            public PropertyMaterial getVariantProperty() {
                return property;
            }
        };
    }
private BlockCompressed() {
        super(net.minecraft.block.material.Material.field_151573_f);
        func_149663_c("compressed");
        func_149711_c(5.0f);
        func_149752_b(10.0f);
        func_149647_a(MaterialItemDeclarations.TAB_GREGTECH_MATERIALS);
    }
@Override
public net.minecraft.block.material.Material func_149688_o( IBlockState state) {
        FluidMaterial material = getGtMaterial(state);
        if (material.hasProperty(PropertyKey.GEM)) {
            return net.minecraft.block.material.Material.field_151576_e;
        } else if (material.hasProperty(PropertyKey.INGOT)) {
            return net.minecraft.block.material.Material.field_151573_f;
        } else if (material.hasProperty(PropertyKey.DUST)) {
            return net.minecraft.block.material.Material.field_151595_p;
        }
        return net.minecraft.block.material.Material.field_151576_e;
    }
@Override
public SoundType getSoundType( IBlockState state,  World world,  BlockPos pos,
                                   Entity entity) {
        FluidMaterial material = getGtMaterial(state);
        if (material.hasProperty(PropertyKey.GEM)) {
            return SoundType.field_185851_d;
        } else if (material.hasProperty(PropertyKey.INGOT)) {
            return SoundType.field_185852_e;
        } else if (material.hasProperty(PropertyKey.DUST)) {
            return SoundType.field_185855_h;
        }
        return SoundType.field_185851_d;
    }
@Override
public String getHarvestTool( IBlockState state) {
        FluidMaterial material = getGtMaterial(state);
        if (material.isSolid()) {
            return MaterialBlockDeclarations.PICKAXE;
        } else if (material.hasProperty(PropertyKey.DUST)) {
            return MaterialBlockDeclarations.SHOVEL;
        }
        return MaterialBlockDeclarations.PICKAXE;
    }
@Override
public int getHarvestLevel( IBlockState state) {
        FluidMaterial material = getGtMaterial(state);
        if (material.hasProperty(PropertyKey.DUST)) {
            return material.getBlockHarvestLevel();
        }
        return 0;
    }
@Override
public void func_190948_a( ItemStack stack,  World worldIn,  List<String> tooltip,
                                ITooltipFlag flagIn) { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: addInformation"); }
public void onModelRegister() { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: onModelRegister"); }
}
