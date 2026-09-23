package research.orthrus.axiom.nativeconstruction;

import net.minecraft.block.Block;

/** Test-owned dependency wiring. Original addon implementation is supplied separately. */
final class NativeOreAddonProbe implements OreAddon {
    private SusyStoneVariantBlock host;
    public Block prepare() {
        if (host != null || !SusyOreHostBlocks.SUSY_STONE_BLOCKS.isEmpty()) throw new IllegalStateException("fresh addon context required");
        // Pinned Susy preLoad precedes SuSyBlocks.init; suppliers remain lazy.
        SusyStoneTypes.init();
        host = new SusyStoneVariantBlock(SusyStoneVariantBlock.StoneVariant.SMOOTH);
        SusyOreHostBlocks.SUSY_STONE_BLOCKS.put(SusyStoneVariantBlock.StoneVariant.SMOOTH, host);
        return host;
    }
    public void verify() {
        if (host == null || SusyOreHostBlocks.SUSY_STONE_BLOCKS.size() != 1 ||
                SusyOreHostBlocks.SUSY_STONE_BLOCKS.get(SusyStoneVariantBlock.StoneVariant.SMOOTH) != host)
            throw new IllegalStateException("Source addon host identity changed");
    }
}
