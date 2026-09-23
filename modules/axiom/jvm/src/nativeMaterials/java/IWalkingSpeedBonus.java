// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;

import net.minecraft.block.state.IBlockState;
import net.minecraft.entity.Entity;


/**
 * @deprecated use {@link gregtech.api.util.BlockUtility#setWalkingSpeedBonus(IBlockState, double)}
 */
@SuppressWarnings("DeprecatedIsStillUsed")
@Deprecated

public interface IWalkingSpeedBonus {

    default double getWalkingSpeedBonus() {
        return 1.0D;
    }

    default boolean checkApplicableBlocks(IBlockState state) {
        return false;
    }

    default boolean bonusSpeedCondition(Entity walkingEntity) { throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: default boolean bonusSpeedCondition"); }
}
