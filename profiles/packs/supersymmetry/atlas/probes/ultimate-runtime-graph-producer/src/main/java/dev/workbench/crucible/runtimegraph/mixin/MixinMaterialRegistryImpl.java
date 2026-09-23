package dev.workbench.crucible.runtimegraph.mixin;

import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import gregtech.api.unification.material.Material;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Binds successful GT material construction to the active Groovy call chain. */
@Mixin(targets = "gregtech.core.unification.material.internal.MaterialRegistryImpl", remap = false)
public abstract class MixinMaterialRegistryImpl {
    @Inject(
        method = "register(ILjava/lang/String;Lgregtech/api/unification/material/Material;)V",
        at = @At("RETURN")
    )
    private void workbench$registerMaterial(
        int numericId,
        String key,
        Material material,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "gt-material-register",
            this,
            Integer.valueOf(numericId),
            key,
            material
        );
    }
}
