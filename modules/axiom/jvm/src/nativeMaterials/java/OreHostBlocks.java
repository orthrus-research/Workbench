// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.
package research.orthrus.axiom.nativeconstruction;
import java.util.*;
import java.util.stream.*;
import net.minecraft.block.*;
import net.minecraft.block.state.*;
import net.minecraft.item.*;
import net.minecraftforge.registries.IForgeRegistry;

final class OreHostBlocks {
public static final EnumMap<StoneVariantBlock.StoneVariant, StoneVariantBlock> STONE_BLOCKS = new EnumMap<>(
            StoneVariantBlock.StoneVariant.class);
public static final BaseCreativeTab TAB_GREGTECH = new BaseCreativeTab("gregtech" + ".main",
            () -> { throw new Failure("incomplete", "ore.tab-icon", "Unqualified creative icon"); }, true);
public static final BaseCreativeTab TAB_GREGTECH_DECORATIONS = new BaseCreativeTab("gregtech" + ".decorations",
            () -> { throw new Failure("incomplete", "ore.tab-icon", "Unqualified creative icon"); }, true);
public static final BaseCreativeTab TAB_GREGTECH_ORES = new BaseCreativeTab("gregtech" + ".ores",
            () -> OreDictUnifier.get(OrePrefix.ore, PrefixDependencies.material("Aluminium")), true);

}
