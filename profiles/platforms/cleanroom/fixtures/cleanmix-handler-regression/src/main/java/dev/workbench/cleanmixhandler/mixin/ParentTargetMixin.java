package dev.workbench.cleanmixhandler.mixin;

import dev.workbench.cleanmixhandler.HarnessCounters;
import dev.workbench.cleanmixhandler.target.ParentTarget;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ParentTarget.class)
public abstract class ParentTargetMixin {

    @Inject(method = "exercise", at = @At("HEAD"))
    protected void workbench$handler(CallbackInfo callbackInfo) {
        HarnessCounters.parentHandlerCount++;
    }
}
