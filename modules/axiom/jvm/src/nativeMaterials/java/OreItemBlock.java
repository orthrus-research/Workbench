// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.state.IBlockState;
import net.minecraft.item.ItemBlock;
import net.minecraft.item.ItemStack;


public class OreItemBlock extends ItemBlock {

    private final BlockOre oreBlock;

    public OreItemBlock(BlockOre oreBlock) {
        super(oreBlock);
        this.oreBlock = oreBlock;
        func_77627_a(true);
    }

    @Override
    public int func_77647_b(int damage) {
        return damage;
    }

    protected IBlockState getBlockState(ItemStack stack) {
        return oreBlock.func_176203_a(func_77647_b(stack.func_77952_i()));
    }

    
    @Override
    public String func_77653_i( ItemStack stack) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: public String getItemStackDisplayName"); }
}
