package research.orthrus.axiom.nativeconstruction;

import net.minecraftforge.fml.common.*;

/** The material manager reads the same native Loader owner as FluidRegistry and EventBus. */
final class RegistryRuntime {
    private int networkId;
    private MaterialRegistryManager materials;
    ModContainer activeModContainer() { return Loader.instance().activeModContainer(); }
    int nextNetworkId() { return networkId++; }
    MaterialRegistryManager materials() {
        if (materials == null) materials = new MaterialRegistryManager(this);
        return materials;
    }
    void error(String template, Object... arguments) { FMLLog.log.error(template, arguments); }
}
