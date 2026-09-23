package dev.workbench.worldgenobservatory.probe;

import net.minecraftforge.common.ForgeVersion;
import net.minecraftforge.fml.relauncher.IFMLLoadingPlugin;
import zone.rong.mixinbooter.IEarlyMixinLoader;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Exact-candidate early loader.  Cleanroom 0.6.8-alpha embeds MixinBooter and
 * calls {@link IEarlyMixinLoader} before Minecraft and Forge target classes
 * are defined.
 */
@IFMLLoadingPlugin.Name("WorkbenchWorldgenObservatoryLoadingPlugin")
@IFMLLoadingPlugin.MCVersion(ForgeVersion.mcVersion)
@IFMLLoadingPlugin.TransformerExclusions({
    "dev.workbench.worldgenobservatory.probe.ObservatoryLoadingPlugin"
})
public final class ObservatoryLoadingPlugin implements IFMLLoadingPlugin, IEarlyMixinLoader {

    @Override
    public List<String> getMixinConfigs() {
        return Collections.singletonList("mixins.workbench_worldgen_observatory.early.json");
    }

    @Override
    public String[] getASMTransformerClass() {
        return new String[0];
    }

    @Override
    public String getModContainerClass() {
        return null;
    }

    @Override
    public String getSetupClass() {
        return null;
    }

    @Override
    public void injectData(Map<String, Object> data) {
        // Cleanroom's built-in MixinBooter owns transformer setup.
    }

    @Override
    public String getAccessTransformerClass() {
        return null;
    }
}
