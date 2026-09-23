package dev.workbench.worldgenobservatory.probe.mixin;

import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import com.llamalad7.mixinextras.sugar.Local;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import net.minecraftforge.common.MinecraftForge;
import net.minecraftforge.fml.common.eventhandler.Event;
import net.minecraftforge.fml.common.eventhandler.EventBus;
import net.minecraftforge.fml.common.eventhandler.IEventListener;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;

@Mixin(value = EventBus.class, remap = false)
public abstract class MixinEventBus {

    @WrapMethod(
        method = "post(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
        remap = false,
        require = 1,
        expect = 1,
        allow = 1
    )
    private boolean workbench$observePost(Event event, Operation<Boolean> original) {
        if (!ProbeRuntime.hasActiveWorldgenSpan() || !workbench$isObservedBus()) {
            return original.call(event);
        }
        String busId = workbench$busId();
        ProbeRuntime.EventState before = ProbeRuntime.eventState(event);
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:event_bus.post",
            "generation_phase",
            ProbeRuntime.Actor.target(
                EventBus.class.getName(),
                "post",
                "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z"
            ),
            ProbeRuntime.currentScope(),
            busId,
            ProbeRuntime.className(event)
        );
        ProbeRuntime.eventBoundary(
            "cleanroom-worldgen:event_bus.post",
            busId,
            event,
            "post_enter",
            null,
            null,
            before,
            before,
            null
        );
        try {
            boolean cancelled = original.call(event);
            ProbeRuntime.EventState after = ProbeRuntime.eventState(event);
            ProbeRuntime.eventBoundary(
                "cleanroom-worldgen:event_bus.post",
                busId,
                event,
                "post_return",
                null,
                null,
                before,
                after,
                null
            );
            ProbeRuntime.returned(span, cancelled);
            return cancelled;
        } catch (Throwable originalFailure) {
            ProbeRuntime.EventState after = ProbeRuntime.eventState(event);
            ProbeRuntime.eventBoundary(
                "cleanroom-worldgen:event_bus.post",
                busId,
                event,
                "post_return",
                null,
                null,
                before,
                after,
                originalFailure
            );
            ProbeRuntime.threw(span, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    @WrapOperation(
        method = "post(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z",
        at = @At(
            value = "INVOKE",
            target = "Lnet/minecraftforge/fml/common/eventhandler/IEventListener;invoke(Lnet/minecraftforge/fml/common/eventhandler/Event;)V",
            remap = false
        ),
        remap = false,
        require = 1,
        expect = 1,
        allow = 1
    )
    private void workbench$observeListenerInvocation(
        IEventListener listener,
        Event event,
        Operation<Void> original,
        @Local(index = 3) int listenerOrdinal
    ) {
        if (!ProbeRuntime.hasActiveWorldgenSpan() || !workbench$isObservedBus()) {
            original.call(listener, event);
            return;
        }
        String busId = workbench$busId();
        ProbeRuntime.EventState before = ProbeRuntime.eventState(event);
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:event_bus.listener_invoke",
            "event_listener",
            ProbeRuntime.Actor.listener(listener),
            ProbeRuntime.currentScope(),
            busId,
            ProbeRuntime.className(event),
            listenerOrdinal,
            ProbeRuntime.className(listener)
        );
        ProbeRuntime.eventBoundary(
            "cleanroom-worldgen:event_bus.listener_invoke",
            busId,
            event,
            "listener_enter",
            listener,
            listenerOrdinal,
            before,
            before,
            null
        );
        try {
            original.call(listener, event);
            ProbeRuntime.EventState after = ProbeRuntime.eventState(event);
            ProbeRuntime.eventBoundary(
                "cleanroom-worldgen:event_bus.listener_invoke",
                busId,
                event,
                "listener_return",
                listener,
                listenerOrdinal,
                before,
                after,
                null
            );
            ProbeRuntime.returned(span, after);
        } catch (Throwable originalFailure) {
            ProbeRuntime.EventState after = ProbeRuntime.eventState(event);
            ProbeRuntime.eventBoundary(
                "cleanroom-worldgen:event_bus.listener_invoke",
                busId,
                event,
                "listener_throw",
                listener,
                listenerOrdinal,
                before,
                after,
                originalFailure
            );
            ProbeRuntime.threw(span, originalFailure);
            ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    private boolean workbench$isObservedBus() {
        EventBus self = (EventBus) (Object) this;
        return self == MinecraftForge.EVENT_BUS
            || self == MinecraftForge.TERRAIN_GEN_BUS
            || self == MinecraftForge.ORE_GEN_BUS;
    }

    private String workbench$busId() {
        EventBus self = (EventBus) (Object) this;
        if (self == MinecraftForge.EVENT_BUS) {
            return "forge-bus:event_bus";
        }
        if (self == MinecraftForge.TERRAIN_GEN_BUS) {
            return "forge-bus:terrain_gen_bus";
        }
        if (self == MinecraftForge.ORE_GEN_BUS) {
            return "forge-bus:ore_gen_bus";
        }
        return "forge-bus:unbound";
    }
}
