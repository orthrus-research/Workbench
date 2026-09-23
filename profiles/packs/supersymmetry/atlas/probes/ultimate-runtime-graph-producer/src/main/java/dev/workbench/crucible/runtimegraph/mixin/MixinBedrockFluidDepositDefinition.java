package dev.workbench.crucible.runtimegraph.mixin;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.worldgen.WorldgenInitializerTrace;

import gregtech.api.worldgen.config.BedrockFluidDepositDefinition;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(value = BedrockFluidDepositDefinition.class, remap = false)
public abstract class MixinBedrockFluidDepositDefinition {
    @Inject(
        method = "initializeFromConfig(Lcom/google/gson/JsonObject;)Z",
        at = @At("HEAD"),
        require = 1,
        remap = false
    )
    private void workbench$retainInitializerEntry(
        JsonObject initializer,
        CallbackInfoReturnable<Boolean> callback
    ) {
        WorldgenInitializerTrace.global().initializerEntered(
            WorldgenInitializerTrace.Scope.BEDROCK_FLUID,
            this,
            initializer
        );
    }

    @Inject(
        method = "initializeFromConfig(Lcom/google/gson/JsonObject;)Z",
        at = @At("RETURN"),
        require = 1,
        remap = false
    )
    private void workbench$retainInitializerReturn(
        JsonObject initializer,
        CallbackInfoReturnable<Boolean> callback
    ) {
        WorldgenInitializerTrace.global().initializerReturned(
            WorldgenInitializerTrace.Scope.BEDROCK_FLUID,
            this,
            callback.getReturnValue().booleanValue()
        );
    }
}
