package dev.workbench.crucible.runtimegraph.mixin;

import com.cleanroommc.groovyscript.event.EventBusType;

import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import net.minecraftforge.fml.common.eventhandler.EventPriority;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.function.Consumer;

/** Carries source custody from script registration into deferred event closures. */
@Mixin(
    targets = "com.cleanroommc.groovyscript.event.GroovyEventManager$EventListener",
    remap = false
)
public abstract class MixinGroovyEventListener {
    @Inject(
        method = "<init>(Lcom/cleanroommc/groovyscript/event/EventBusType;Lnet/minecraftforge/fml/common/eventhandler/EventPriority;Ljava/lang/Class;Ljava/util/function/Consumer;)V",
        at = @At("RETURN")
    )
    private void workbench$registerDeferredSource(
        EventBusType eventBus,
        EventPriority priority,
        Class<?> declaredEventClass,
        Consumer<?> listener,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.registerDeferredListener(
            this,
            eventBus.name(),
            priority.name(),
            declaredEventClass.getName()
        );
    }

    @Redirect(
        method = "invoke",
        at = @At(
            value = "INVOKE",
            target = "Ljava/util/function/Consumer;accept(Ljava/lang/Object;)V"
        )
    )
    private void workbench$invokeWithDeferredSource(
        Consumer<Object> listener,
        Object event
    ) {
        ProgramProvenanceTrace.DeferredCallback callback =
            ProgramProvenanceTrace.beginDeferredCallback(
                this,
                event.getClass().getName()
            );
        try {
            listener.accept(event);
            ProgramProvenanceTrace.endDeferredCallback(callback, null);
        } catch (RuntimeException failure) {
            ProgramProvenanceTrace.endDeferredCallback(callback, failure);
            throw failure;
        } catch (Error failure) {
            ProgramProvenanceTrace.endDeferredCallback(callback, failure);
            throw failure;
        }
    }
}
