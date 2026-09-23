package dev.workbench.cleanmixp0.mixin;

import dev.workbench.cleanmixp0.LateTargets;
import dev.workbench.cleanmixp0.P0Counters;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(LateTargets.Target.class)
public abstract class LateMixin {

    @Inject(method = "value", at = @At("RETURN"), cancellable = true)
    private void workbench$late(CallbackInfoReturnable<Integer> cir) {
        P0Counters.lateMixin++;
        cir.setReturnValue(19);
    }
}
