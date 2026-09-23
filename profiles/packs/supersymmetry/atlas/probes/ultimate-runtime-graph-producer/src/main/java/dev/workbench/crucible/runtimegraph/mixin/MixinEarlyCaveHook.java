package dev.workbench.crucible.runtimegraph.mixin;

import com.personthecat.cavegenerator.Main;
import com.personthecat.cavegenerator.world.generator.EarlyCaveHook;

import dev.workbench.crucible.runtimegraph.worldgen.RealizedWorldObservationTrace;

import net.minecraft.world.World;
import net.minecraft.world.chunk.ChunkPrimer;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.ArrayList;

/** Observes completion of Cave Generator's primer stages without invoking them. */
@Mixin(value = EarlyCaveHook.class, remap = false)
public abstract class MixinEarlyCaveHook {
    @Inject(
        method = "func_186125_a(Lnet/minecraft/world/World;IILnet/minecraft/world/chunk/ChunkPrimer;)V",
        at = @At("RETURN"),
        require = 1,
        remap = false
    )
    private void workbench$cavePrimerCompleted(
        World world,
        int chunkX,
        int chunkZ,
        ChunkPrimer primer,
        CallbackInfo callback
    ) {
        RealizedWorldObservationTrace.global().caveHookCompleted(
            "early-map",
            world,
            chunkX,
            chunkZ,
            new ArrayList<String>(Main.instance.generators.keySet())
        );
    }
}
