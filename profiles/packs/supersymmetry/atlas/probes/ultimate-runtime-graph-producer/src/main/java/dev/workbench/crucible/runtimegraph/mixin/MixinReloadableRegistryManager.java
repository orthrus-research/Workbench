package dev.workbench.crucible.runtimegraph.mixin;

import dev.workbench.crucible.runtimegraph.provenance.ProgramProvenanceTrace;

import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.IForgeRegistry;
import net.minecraftforge.registries.IForgeRegistryEntry;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Captures generic reloadable and Forge-registry mutation operations. */
@Mixin(targets = "com.cleanroommc.groovyscript.registry.ReloadableRegistryManager", remap = false)
public abstract class MixinReloadableRegistryManager {
    @Inject(
        method = "markScripted(Ljava/lang/Class;Ljava/lang/Object;)V",
        at = @At("RETURN")
    )
    private static void workbench$markScripted(
        Class<?> registryClass,
        Object subject,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "reloadable-mark-scripted",
            registryClass,
            subject
        );
    }

    @Inject(
        method = "backup(Ljava/lang/Class;Ljava/lang/Object;)V",
        at = @At("RETURN")
    )
    private static void workbench$backup(
        Class<?> registryClass,
        Object subject,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "reloadable-backup-before-remove",
            registryClass,
            subject
        );
    }

    @Inject(
        method = "addRegistryEntry(Lnet/minecraftforge/registries/IForgeRegistry;Lnet/minecraft/util/ResourceLocation;Lnet/minecraftforge/registries/IForgeRegistryEntry;)V",
        at = @At("RETURN")
    )
    private static void workbench$addNamedEntry(
        IForgeRegistry<?> registry,
        ResourceLocation name,
        IForgeRegistryEntry<?> entry,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "forge-register-entry",
            registry,
            name,
            entry
        );
    }

    @Inject(
        method = "addRegistryEntry(Lnet/minecraftforge/registries/IForgeRegistry;Lnet/minecraftforge/registries/IForgeRegistryEntry;)V",
        at = @At("RETURN")
    )
    private static void workbench$addExistingEntry(
        IForgeRegistry<?> registry,
        IForgeRegistryEntry<?> entry,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "forge-register-entry",
            registry,
            entry == null ? null : entry.getRegistryName(),
            entry
        );
    }

    @Inject(
        method = "removeRegistryEntry(Lnet/minecraftforge/registries/IForgeRegistry;Lnet/minecraft/util/ResourceLocation;)V",
        at = @At("RETURN")
    )
    private static void workbench$removeEntry(
        IForgeRegistry<?> registry,
        ResourceLocation name,
        CallbackInfo callback
    ) {
        ProgramProvenanceTrace.recordMutation(
            "forge-remove-entry",
            registry,
            name
        );
    }
}
