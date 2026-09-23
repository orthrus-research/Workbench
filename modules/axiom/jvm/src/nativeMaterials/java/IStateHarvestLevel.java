// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;


import net.minecraft.block.state.IBlockState;

public interface IStateHarvestLevel {

    int getHarvestLevel(IBlockState state);

    default String getHarvestTool(IBlockState state) {
        return MaterialBlockDeclarations.PICKAXE;
    }
}
