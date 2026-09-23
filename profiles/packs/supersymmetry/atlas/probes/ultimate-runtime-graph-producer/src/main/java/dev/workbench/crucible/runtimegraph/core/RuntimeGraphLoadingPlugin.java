package dev.workbench.crucible.runtimegraph.core;

import net.minecraftforge.common.ForgeVersion;
import net.minecraftforge.fml.relauncher.IFMLLoadingPlugin;

import zone.rong.mixinbooter.IEarlyMixinLoader;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/** Registers the initializer-retention mixins before GT worldgen classes load. */
@IFMLLoadingPlugin.Name("WorkbenchRuntimeGraphLoadingPlugin")
@IFMLLoadingPlugin.MCVersion(ForgeVersion.mcVersion)
@IFMLLoadingPlugin.TransformerExclusions({"dev.workbench.crucible.runtimegraph.core"})
public final class RuntimeGraphLoadingPlugin implements IFMLLoadingPlugin, IEarlyMixinLoader {
    @Override public List<String> getMixinConfigs() {
        return Collections.singletonList("mixins.workbench_runtime_graph.early.json");
    }
    @Override public String[] getASMTransformerClass() { return new String[0]; }
    @Override public String getModContainerClass() { return null; }
    @Override public String getSetupClass() { return null; }
    @Override public void injectData(Map<String, Object> data) {}
    @Override public String getAccessTransformerClass() { return null; }
}
