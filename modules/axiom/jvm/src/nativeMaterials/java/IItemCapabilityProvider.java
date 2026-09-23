// Source-qualified GTCEu members, LGPL-3.0; see sources/material-items.lock.json and spec/material-items.md.
package research.orthrus.axiom.nativeconstruction;

import net.minecraft.item.ItemStack;
import net.minecraftforge.common.capabilities.ICapabilityProvider;

@FunctionalInterface
public interface IItemCapabilityProvider extends IItemComponent {

    ICapabilityProvider createProvider(ItemStack itemStack);
}
