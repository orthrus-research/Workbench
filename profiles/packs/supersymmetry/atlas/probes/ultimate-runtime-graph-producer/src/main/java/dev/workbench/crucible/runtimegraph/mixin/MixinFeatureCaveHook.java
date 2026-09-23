package dev.workbench.crucible.runtimegraph.mixin;

import com.personthecat.cavegenerator.Main;
import com.personthecat.cavegenerator.world.feature.FeatureCaveHook;

import dev.workbench.crucible.runtimegraph.worldgen.RealizedWorldObservationTrace;

import net.minecraft.world.World;
import net.minecraft.world.chunk.IChunkProvider;
import net.minecraft.world.gen.IChunkGenerator;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.ArrayList;
import java.util.Random;

/** Observes completion of Cave Generator's feature stage without invoking it. */
@Mixin(value = FeatureCaveHook.class, remap = false)
public abstract class MixinFeatureCaveHook {
    @Inject(
        method = "generate(Ljava/util/Random;IILnet/minecraft/world/World;Lnet/minecraft/world/gen/IChunkGenerator;Lnet/minecraft/world/chunk/IChunkProvider;)V",
        at = @At("RETURN"),
        require = 1,
        remap = false
    )
    private void workbench$caveFeatureCompleted(
        Random random,
        int chunkX,
        int chunkZ,
        World world,
        IChunkGenerator generator,
        IChunkProvider provider,
        CallbackInfo callback
    ) {
        RealizedWorldObservationTrace.global().caveHookCompleted(
            "feature",
            world,
            chunkX,
            chunkZ,
            new ArrayList<String>(Main.instance.generators.keySet())
        );
    }
}
