package dev.workbench.worldgenobservatory.probe.mixin;

import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import com.llamalad7.mixinextras.injector.wrapoperation.WrapOperation;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import net.minecraft.world.WorldServer;
import net.minecraft.world.chunk.Chunk;
import net.minecraft.world.gen.ChunkProviderServer;
import net.minecraft.world.gen.IChunkGenerator;
import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;

import javax.annotation.Nullable;

@Mixin(ChunkProviderServer.class)
public abstract class MixinChunkProviderServer {

    @Shadow
    @Final
    public WorldServer world;

    @Shadow
    public abstract Chunk getLoadedChunk(int x, int z);

    @WrapMethod(
        method = "provideChunk(II)Lnet/minecraft/world/chunk/Chunk;",
        require = 1,
        expect = 1,
        allow = 1
    )
    private Chunk workbench$observeProvideChunk(int x, int z, Operation<Chunk> original) {
        if (!ProbeRuntime.enabled()) {
            return original.call(x, z);
        }
        boolean loadedBefore = workbench$isLoaded(x, z);
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:cps.provide_chunk",
            "chunk_request",
            ProbeRuntime.Actor.target(
                ChunkProviderServer.class.getName(),
                "provideChunk",
                "(II)Lnet/minecraft/world/chunk/Chunk;"
            ),
            ProbeRuntime.scope(workbench$dimension(), x, z),
            x,
            z,
            loadedBefore
        );
        try {
            Chunk result = original.call(x, z);
            ProbeRuntime.chunkAccess(
                span,
                "lookup",
                "cleanroom-worldgen:cps.provide_chunk",
                x,
                z,
                loadedBefore,
                result == null ? "miss" : (loadedBefore ? "already_loaded" : "loaded")
            );
            ProbeRuntime.returned(span, result == null ? "null" : result.getClass().getName());
            return result;
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(
                span,
                "lookup",
                "cleanroom-worldgen:cps.provide_chunk",
                x,
                z,
                loadedBefore,
                "threw"
            );
            ProbeRuntime.threw(span, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    @WrapMethod(
        method = "loadChunk(II)Lnet/minecraft/world/chunk/Chunk;",
        require = 1,
        expect = 1,
        allow = 1
    )
    private Chunk workbench$observeLoadChunk(int x, int z, Operation<Chunk> original) {
        return workbench$observeLoadBoundary(
            "cleanroom-worldgen:cps.load_chunk",
            x,
            z,
            null,
            original
        );
    }

    @WrapMethod(
        method = "loadChunk(IILjava/lang/Runnable;)Lnet/minecraft/world/chunk/Chunk;",
        require = 1,
        expect = 1,
        allow = 1
    )
    private Chunk workbench$observeLoadChunkCallback(
        int x,
        int z,
        @Nullable Runnable callback,
        Operation<Chunk> original
    ) {
        if (!ProbeRuntime.enabled()) {
            return original.call(x, z, callback);
        }
        boolean loadedBefore = workbench$isLoaded(x, z);
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:cps.load_chunk_callback",
            "chunk_request",
            ProbeRuntime.Actor.target(
                ChunkProviderServer.class.getName(),
                "loadChunk",
                "(IILjava/lang/Runnable;)Lnet/minecraft/world/chunk/Chunk;"
            ),
            ProbeRuntime.scope(workbench$dimension(), x, z),
            x,
            z,
            loadedBefore,
            callback != null
        );
        try {
            Chunk result = original.call(x, z, callback);
            ProbeRuntime.chunkAccess(
                span,
                "load",
                "cleanroom-worldgen:cps.load_chunk_callback",
                x,
                z,
                loadedBefore,
                result == null ? "miss" : (loadedBefore ? "already_loaded" : "loaded")
            );
            ProbeRuntime.returned(span, result == null ? "null" : result.getClass().getName());
            return result;
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(
                span,
                "load",
                "cleanroom-worldgen:cps.load_chunk_callback",
                x,
                z,
                loadedBefore,
                "threw"
            );
            ProbeRuntime.threw(span, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    @WrapOperation(
        method = "provideChunk(II)Lnet/minecraft/world/chunk/Chunk;",
        at = @At(
            value = "INVOKE",
            target = "Lnet/minecraft/world/gen/IChunkGenerator;generateChunk(II)Lnet/minecraft/world/chunk/Chunk;"
        ),
        require = 1,
        expect = 1,
        allow = 1
    )
    private Chunk workbench$observeGenerateChunkCall(
        IChunkGenerator generator,
        int x,
        int z,
        Operation<Chunk> original
    ) {
        if (!ProbeRuntime.enabled()) {
            return original.call(generator, x, z);
        }
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            "cleanroom-worldgen:cps.generate_chunk_call",
            "generator_call",
            ProbeRuntime.Actor.runtime(
                generator,
                "generateChunk",
                "(II)Lnet/minecraft/world/chunk/Chunk;"
            ),
            ProbeRuntime.scope(workbench$dimension(), x, z),
            x,
            z,
            ProbeRuntime.className(generator)
        );
        try {
            Chunk result = original.call(generator, x, z);
            ProbeRuntime.chunkAccess(
                span,
                "generate",
                "cleanroom-worldgen:cps.generate_chunk_call",
                x,
                z,
                false,
                result == null ? "miss" : "generated"
            );
            ProbeRuntime.returned(span, result == null ? "null" : result.getClass().getName());
            return result;
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(
                span,
                "generate",
                "cleanroom-worldgen:cps.generate_chunk_call",
                x,
                z,
                false,
                "threw"
            );
            ProbeRuntime.threw(span, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    private Chunk workbench$observeLoadBoundary(
        String hookId,
        int x,
        int z,
        @Nullable Runnable ignored,
        Operation<Chunk> original
    ) {
        if (!ProbeRuntime.enabled()) {
            return original.call(x, z);
        }
        boolean loadedBefore = workbench$isLoaded(x, z);
        ProbeRuntime.SpanToken span = ProbeRuntime.enter(
            hookId,
            "chunk_request",
            ProbeRuntime.Actor.target(
                ChunkProviderServer.class.getName(),
                "loadChunk",
                "(II)Lnet/minecraft/world/chunk/Chunk;"
            ),
            ProbeRuntime.scope(workbench$dimension(), x, z),
            x,
            z,
            loadedBefore
        );
        try {
            Chunk result = original.call(x, z);
            ProbeRuntime.chunkAccess(
                span,
                "load",
                hookId,
                x,
                z,
                loadedBefore,
                result == null ? "miss" : (loadedBefore ? "already_loaded" : "loaded")
            );
            ProbeRuntime.returned(span, result == null ? "null" : result.getClass().getName());
            return result;
        } catch (Throwable originalFailure) {
            ProbeRuntime.chunkAccess(span, "load", hookId, x, z, loadedBefore, "threw");
            ProbeRuntime.threw(span, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    private boolean workbench$isLoaded(int x, int z) {
        try {
            return getLoadedChunk(x, z) != null;
        } catch (Throwable ignored) {
            return false;
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
