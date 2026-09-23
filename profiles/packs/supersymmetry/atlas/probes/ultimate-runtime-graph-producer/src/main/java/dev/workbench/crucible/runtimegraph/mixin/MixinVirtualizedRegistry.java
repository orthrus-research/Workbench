package dev.workbench.crucible.runtimegraph.mixin;

import com.cleanroommc.groovyscript.registry.VirtualizedRegistry;

import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Records successful generic GroovyScript virtual-registry mutations. */
@Mixin(value = VirtualizedRegistry.class, remap = false)
public abstract class MixinVirtualizedRegistry {
    @Inject(method = "doAddScripted", at = @At("RETURN"))
    private void workbench$scripted(
        Object subject,
        CallbackInfoReturnable<Boolean> callback
    ) {
        if (callback.getReturnValueZ()) {
            ProgramProvenanceTrace.recordMutation(
                "virtualized-add-scripted",
                this,
                subject
            );
        }
    }

    @Inject(method = "doAddBackup", at = @At("RETURN"))
    private void workbench$backup(
        Object subject,
        CallbackInfoReturnable<Boolean> callback
    ) {
        if (callback.getReturnValueZ()) {
            ProgramProvenanceTrace.recordMutation(
                "virtualized-backup-before-remove",
                this,
                subject
            );
        }
    }
}
