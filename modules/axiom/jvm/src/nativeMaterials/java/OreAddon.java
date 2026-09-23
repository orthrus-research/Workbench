package research.orthrus.axiom.nativeconstruction;

import net.minecraft.block.Block;

/** Mandatory source-program dependency, not installed addon discovery or a legacy adapter.
 * Implementations belong to the separately supplied, digest-bound native program.
 */
interface OreAddon {
    Block prepare();
    void verify();
}
