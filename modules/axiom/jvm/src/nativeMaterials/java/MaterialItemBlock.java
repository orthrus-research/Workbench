// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.item.ItemBlock;
import net.minecraft.item.ItemStack;


public class MaterialItemBlock extends ItemBlock {

    private final BlockMaterialBase block;
    private final OrePrefix prefix;

    public MaterialItemBlock(BlockMaterialBase block, OrePrefix prefix) {
        super(block);
        this.block = block;
        this.prefix = prefix;
        func_77627_a(true);
    }

    
    @Override
    public BlockMaterialBase func_179223_d() {
        return block;
    }

    @Override
    public int func_77647_b(int damage) {
        return damage;
    }

    
    @Override
    public String func_77653_i(ItemStack stack) { throw new Failure("incomplete", "block.behavior", "Material ItemBlock localization is not qualified"); }
}
