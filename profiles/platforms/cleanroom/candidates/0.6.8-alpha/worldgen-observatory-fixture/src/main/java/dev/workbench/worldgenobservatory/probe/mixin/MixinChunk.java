package dev.workbench.worldgenobservatory.probe.mixin;

import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.world.chunk.Chunk;
import net.minecraft.world.chunk.IChunkProvider;
import net.minecraft.world.gen.IChunkGenerator;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;

import javax.annotation.Nullable;

@Mixin(Chunk.class)
public abstract class MixinChunk {

    @Shadow
    @Final
    private World world;

    @Shadow
    @Final
    public int x;

    @Shadow
    @Final
    public int z;

    @WrapMethod(
        method = "populate(Lnet/minecraft/world/chunk/IChunkProvider;Lnet/minecraft/world/gen/IChunkGenerator;)V",
        require = 1,
        expect = 1,
        allow = 1
    )
    private void workbench$observePublicPopulate(
        IChunkProvider provider,
        IChunkGenerator generator,
        Operation<Void> original
    ) {
        if (!ProbeRuntime.enabled()) {
            original.call(provider, generator);
            return;
        }
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:chunk.populate_neighbors",
            "generation_phase",
            ProbeRuntime.Actor.target(
                Chunk.class.getName(),
                "populate",
                "(Lnet/minecraft/world/chunk/IChunkProvider;Lnet/minecraft/world/gen/IChunkGenerator;)V"
            ),
            ProbeRuntime.scope(workbench$dimension(), x, z),
            x,
            z,
            ProbeRuntime.className(provider),
            ProbeRuntime.className(generator)
        );
        try {
            original.call(provider, generator);
            ProbeRuntime.chunkAccess(
                span,
                "populate",
                "cleanroom-worldgen:chunk.populate_neighbors",
                x,
                z,
                true,
                "populated"
            );
            ProbeRuntime.returned(span, "void");
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(
                span,
                "populate",
                "cleanroom-worldgen:chunk.populate_neighbors",
                x,
                z,
                true,
                "threw"
            );
            ProbeRuntime.threw(span, originalFailure);
            ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    @WrapMethod(
        method = "populate(Lnet/minecraft/world/gen/IChunkGenerator;)V",
        require = 1,
        expect = 1,
        allow = 1
    )
    private void workbench$observeOwnedPopulate(IChunkGenerator generator, Operation<Void> original) {
        if (!ProbeRuntime.enabled()) {
            original.call(generator);
            return;
        }
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:chunk.populate_owned",
            "generation_phase",
            ProbeRuntime.Actor.target(
                Chunk.class.getName(),
                "populate",
                "(Lnet/minecraft/world/gen/IChunkGenerator;)V"
            ),
            ProbeRuntime.scope(workbench$dimension(), x, z),
            x,
            z,
            ProbeRuntime.className(generator)
        );
        try {
            original.call(generator);
            ProbeRuntime.chunkAccess(
                span,
                "populate",
                "cleanroom-worldgen:chunk.populate_owned",
                x,
                z,
                true,
                "populated"
            );
            ProbeRuntime.returned(span, "void");
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(
                span,
                "populate",
                "cleanroom-worldgen:chunk.populate_owned",
                x,
                z,
                true,
                "threw"
            );
            ProbeRuntime.threw(span, originalFailure);
            ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    @WrapOperation(
        method = "populate(Lnet/minecraft/world/gen/IChunkGenerator;)V",
        at = @At(
            value = "INVOKE",
            target = "Lnet/minecraft/world/gen/IChunkGenerator;populate(II)V"
        ),
        require = 1,
        expect = 1,
        allow = 1
    )
    private void workbench$observeGeneratorPopulateCall(
        IChunkGenerator generator,
        int chunkX,
        int chunkZ,
        Operation<Void> original
    ) {
        if (!ProbeRuntime.enabled()) {
            original.call(generator, chunkX, chunkZ);
            return;
        }
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:chunk.generator_populate_call",
            "generator_call",
            ProbeRuntime.Actor.runtime(generator, "populate", "(II)V"),
            ProbeRuntime.scope(workbench$dimension(), chunkX, chunkZ),
            chunkX,
            chunkZ,
            ProbeRuntime.className(generator)
        );
        try {
            original.call(generator, chunkX, chunkZ);
            ProbeRuntime.chunkAccess(
                span,
                "populate",
                "cleanroom-worldgen:chunk.generator_populate_call",
                chunkX,
                chunkZ,
                true,
                "populated"
            );
            ProbeRuntime.returned(span, "void");
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(
                span,
                "populate",
                "cleanroom-worldgen:chunk.generator_populate_call",
                chunkX,
                chunkZ,
                true,
                "threw"
            );
            ProbeRuntime.threw(span, originalFailure);
            ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    @WrapMethod(
        method = "setBlockState(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)Lnet/minecraft/block/state/IBlockState;",
        require = 1,
        expect = 1,
        allow = 1
    )
    @Nullable
    private IBlockState workbench$observeChunkSetBlockState(
        BlockPos position,
        IBlockState requested,
        Operation<IBlockState> original
    ) {
        if (!ProbeRuntime.hasActiveWorldgenSpan()) {
            return original.call(position, requested);
        }
        ProbeRuntime.WriteToken write = ProbeRuntime.beginWrite(
            "cleanroom-worldgen:write.chunk_storage",
            "chunk_storage",
            position.getX(),
            position.getY(),
            position.getZ(),
            null,
            requested,
            null,
            Chunk.class.getName()
        );
        try {
            IBlockState before = original.call(position, requested);
            ProbeRuntime.finishWrite(
                write,
                before,
                before == null ? null : requested,
                true,
                null
            );
            return before;
        } catch (Throwable originalFailure) {
            ProbeRuntime.finishWrite(write, null, null, true, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    private Integer workbench$dimension() {
        try {
            return world.provider.getDimension();
        } catch (Throwable ignored) {
            return null;
        }
    }
}
