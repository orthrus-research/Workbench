package dev.workbench.cleanmixhandler.mixin;

import dev.workbench.cleanmixhandler.HarnessCounters;
import dev.workbench.cleanmixhandler.target.ChildTarget;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

@Mixin(ChildTarget.class)
public abstract class ChildTargetMixin extends ParentTargetMixin {

    @Override
    protected void workbench$handler(CallbackInfo callbackInfo) {
        HarnessCounters.childHandlerCount++;
    }
}
