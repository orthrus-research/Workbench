// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.state.IBlockState;

public interface IBlockOre {

    IBlockState getOreBlock(StoneType stoneType);
}
