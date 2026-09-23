package dev.workbench.cleanmixp0.mixin;

import dev.workbench.cleanmixp0.DetachedTargets;
import dev.workbench.cleanmixp0.P0Counters;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

public final class DetachedHandlerMixins {

    private DetachedHandlerMixins() {
    }

    @Mixin(DetachedTargets.TwoParent.class)
    public abstract static class TwoParentMixin {
        @Inject(method = "exercise", at = @At("HEAD"))
        protected void workbench$detachedTwoHandler(CallbackInfo callbackInfo) {
            P0Counters.detachedTwoParent++;
        }
    }

    @Mixin(DetachedTargets.TwoChild.class)
    public abstract static class TwoChildMixin extends TwoParentMixin {
        @Override
        protected void workbench$detachedTwoHandler(CallbackInfo callbackInfo) {
            P0Counters.detachedTwoChild++;
        }
    }

    @Mixin(DetachedTargets.ThreeBase.class)
    public abstract static class ThreeBaseMixin {
        @Inject(method = "exercise", at = @At("HEAD"))
        protected void workbench$detachedThreeHandler(CallbackInfo callbackInfo) {
            P0Counters.detachedThreeBase++;
        }
    }

    @Mixin(DetachedTargets.ThreeMiddle.class)
    public abstract static class ThreeMiddleMixin extends ThreeBaseMixin {
        @Override
        protected void workbench$detachedThreeHandler(CallbackInfo callbackInfo) {
            P0Counters.detachedThreeMiddle++;
        }
    }

    @Mixin(DetachedTargets.ThreeLeaf.class)
    public abstract static class ThreeLeafMixin extends ThreeMiddleMixin {
        @Override
        protected void workbench$detachedThreeHandler(CallbackInfo callbackInfo) {
            P0Counters.detachedThreeLeaf++;
        }
    }
}
