package dev.workbench.cleanmixp0.mixin;

import dev.workbench.cleanmixp0.KnownGapTargets;
import dev.workbench.cleanmixp0.P0Counters;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

public final class KnownGapMixins {

    private KnownGapMixins() {
    }

    @Mixin(KnownGapTargets.LazyParent.class)
    public abstract static class LazyParentMixin {
        @Inject(method = "exercise", at = @At("HEAD"), cancellable = true)
        protected void workbench$lazyHandler(CallbackInfo callbackInfo) {
            P0Counters.lazyParent++;
        }
    }

    @Mixin(KnownGapTargets.LazyChild.class)
    public abstract static class LazyChildMixin extends LazyParentMixin {
        @Override
        protected void workbench$lazyHandler(CallbackInfo callbackInfo) {
            P0Counters.lazyChild++;
            if (callbackInfo == null) {
                P0Counters.lazyCallbackInfoNull++;
            }
        }
    }

    @Mixin(KnownGapTargets.Reentrant.class)
    public abstract static class ReentrantMixin {
        @Inject(method = "value", at = @At("RETURN"), cancellable = true)
        private void workbench$reentrant(CallbackInfoReturnable<Integer> cir) {
            P0Counters.reentrantMixin++;
            cir.setReturnValue(29);
        }
    }

    @Mixin(KnownGapTargets.ThreeBase.class)
    public abstract static class ThreeBaseMixin {
        @Inject(method = "exercise", at = @At("HEAD"))
        protected void workbench$threeHandler(CallbackInfo callbackInfo) {
            P0Counters.threeBase++;
        }
    }

    @Mixin(KnownGapTargets.ThreeMiddle.class)
    public abstract static class ThreeMiddleMixin extends ThreeBaseMixin {
        @Override
        protected void workbench$threeHandler(CallbackInfo callbackInfo) {
            P0Counters.threeMiddle++;
        }
    }

    @Mixin(KnownGapTargets.ThreeLeaf.class)
    public abstract static class ThreeLeafMixin extends ThreeMiddleMixin {
        @Override
        protected void workbench$threeHandler(CallbackInfo callbackInfo) {
            P0Counters.threeLeaf++;
        }
    }
}
