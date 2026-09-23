package dev.workbench.crucible.runtimegraph.client;

import mezz.jei.api.IJeiRuntime;
import mezz.jei.api.IModPlugin;
import mezz.jei.api.IModRegistry;
import mezz.jei.api.JEIPlugin;

/** Read-only bridge to the naturally completed HEI lifecycle. */
@JEIPlugin
public final class RuntimeGraphJeiPlugin implements IModPlugin {
    @Override
    public void register(IModRegistry registry) {
        ClientJeiCapture.register(registry);
    }

    @Override
    public void onRuntimeAvailable(IJeiRuntime runtime) {
        ClientJeiCapture.runtimeAvailable(runtime);
    }
}
