package dev.workbench.cleanmixp0.mixin;

import dev.workbench.cleanmixp0.P0Counters;
import dev.workbench.cleanmixp0.PhaseTargets;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

public final class PhaseMixins {

    private PhaseMixins() {
    }

    @Mixin(PhaseTargets.Preinit.class)
    public abstract static class PreinitMixin {
        @Inject(method = "value", at = @At("RETURN"), cancellable = true)
        private void workbench$phasePreinit(CallbackInfoReturnable<Integer> cir) {
            P0Counters.phasePreinit++;
            cir.setReturnValue(101);
        }
    }

    @Mixin(PhaseTargets.Init.class)
    public abstract static class InitMixin {
        @Inject(method = "value", at = @At("RETURN"), cancellable = true)
        private void workbench$phaseInit(CallbackInfoReturnable<Integer> cir) {
            P0Counters.phaseInit++;
            cir.setReturnValue(102);
        }
    }

    @Mixin(PhaseTargets.Default.class)
    public abstract static class DefaultMixin {
        @Inject(method = "value", at = @At("RETURN"), cancellable = true)
        private void workbench$phaseDefault(CallbackInfoReturnable<Integer> cir) {
            P0Counters.phaseDefault++;
            cir.setReturnValue(103);
        }
    }
}
