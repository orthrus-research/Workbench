package dev.workbench.cleanmixp0;

import java.util.List;
import java.util.Set;
import org.objectweb.asm.tree.ClassNode;
import org.spongepowered.asm.mixin.extensibility.IMixinConfigPlugin;
import org.spongepowered.asm.mixin.extensibility.IMixinInfo;

public final class ReentrantConfigPlugin implements IMixinConfigPlugin {

    public static final String ACTION_PROPERTY =
        "workbench.cleanmix.p0.reentrant_action";
    private static final String TARGET =
        "dev.workbench.cleanmixp0.KnownGapTargets$Reentrant";

    @Override
    public void onLoad(String mixinPackage) {
    }

    @Override
    public String getRefMapperConfig() {
        return null;
    }

    @Override
    public boolean shouldApplyMixin(String targetClassName, String mixinClassName) {
        return true;
    }

    @Override
    public void acceptTargets(Set<String> myTargets, Set<String> otherTargets) {
    }

    @Override
    public List<String> getMixins() {
        try {
            System.setProperty(ACTION_PROPERTY, "getMixins_enter");
            System.out.println(
                "WORKBENCH_CLEANMIX_P0_REENTRANT_V1 getMixins_enter"
            );
            Class.forName(
                TARGET, true, Thread.currentThread().getContextClassLoader()
            );
            System.setProperty(ACTION_PROPERTY, "target_defined_during_getMixins");
            System.out.println(
                "WORKBENCH_CLEANMIX_P0_REENTRANT_V1 target_defined"
            );
            return null;
        } catch (Throwable failure) {
            System.setProperty(
                ACTION_PROPERTY,
                "target_failed_during_getMixins:" + failure.getClass().getName()
            );
            throw new IllegalStateException("reentrant target request failed", failure);
        }
    }

    @Override
    public void preApply(
        String targetClassName, ClassNode targetClass,
        String mixinClassName, IMixinInfo mixinInfo
    ) {
    }

    @Override
    public void postApply(
        String targetClassName, ClassNode targetClass,
        String mixinClassName, IMixinInfo mixinInfo
    ) {
    }
}
