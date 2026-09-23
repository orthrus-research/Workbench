package dev.workbench.dailyloop.mixin;

import dev.workbench.dailyloop.DailyLoopProbe;
import net.minecraft.block.Block;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(Block.class)
abstract class MixinBlock {
    @Inject(
        method = "getTranslationKey()Ljava/lang/String;",
        at = @At("RETURN"),
        require = 1,
        expect = 1,
        allow = 1
    )
    private void workbench$observeTranslationKey(
        CallbackInfoReturnable<String> callback
    ) {
        DailyLoopProbe.observeTranslationKey(callback.getReturnValue());
    }
}
