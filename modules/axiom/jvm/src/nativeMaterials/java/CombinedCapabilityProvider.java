// Source-qualified GTCEu members, LGPL-3.0; see sources/material-items.lock.json and spec/material-items.md.
package research.orthrus.axiom.nativeconstruction;

import net.minecraft.util.EnumFacing;
import net.minecraftforge.common.capabilities.Capability;
import net.minecraftforge.common.capabilities.ICapabilityProvider;


import java.util.List;

public class CombinedCapabilityProvider implements ICapabilityProvider {

    private final ICapabilityProvider[] providers;

    public CombinedCapabilityProvider(ICapabilityProvider... providers) {
        this.providers = providers;
    }

    public CombinedCapabilityProvider(List<ICapabilityProvider> providers) {
        this.providers = providers.toArray(new ICapabilityProvider[0]);
    }

    @Override
    public boolean hasCapability( Capability<?> capability,  EnumFacing facing) {
        for (ICapabilityProvider provider : providers) {
            if (provider.hasCapability(capability, facing)) {
                return true;
            }
        }
        return false;
    }

    
    @Override
    public <T> T getCapability( Capability<T> capability,  EnumFacing facing) {
        for (ICapabilityProvider provider : providers) {
            T cap = provider.getCapability(capability, facing);
            if (cap != null) {
                return cap;
            }
        }
        return null;
    }
}
