package dev.workbench.worldgenobservatory.probe.mixin;

import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import net.minecraft.block.state.IBlockState;
import net.minecraft.world.chunk.ChunkPrimer;
import org.spongepowered.asm.mixin.Mixin;

@Mixin(ChunkPrimer.class)
public abstract class MixinChunkPrimer {

    @WrapMethod(
        method = "setBlockState(IIILnet/minecraft/block/state/IBlockState;)V",
        require = 1,
        expect = 1,
        allow = 1
    )
    private void workbench$observePrimerSetBlockState(
        int localX,
        int y,
        int localZ,
        IBlockState requested,
        Operation<Void> original
    ) {
        if (!ProbeRuntime.hasActiveWorldgenSpan()) {
            original.call(localX, y, localZ, requested);
            return;
        }
        ChunkPrimer self = (ChunkPrimer) (Object) this;
        IBlockState before = ProbeRuntime.safePrimerState(self, localX, y, localZ);
        int worldX = ProbeRuntime.primerWorldX(localX);
        int worldZ = ProbeRuntime.primerWorldZ(localZ);
        ProbeRuntime.WriteToken write = ProbeRuntime.beginWrite(
            "cleanroom-worldgen:write.chunk_primer",
            "chunk_primer",
            worldX,
            y,
            worldZ,
            before,
            requested,
            null,
            ChunkPrimer.class.getName()
        );
        try {
            original.call(localX, y, localZ, requested);
            ProbeRuntime.finishWrite(write, before, requested, true, null);
        } catch (Throwable originalFailure) {
            ProbeRuntime.finishWrite(write, before, null, true, originalFailure);
            ProbeRuntime.sneakyThrow(originalFailure);
        }
    }
}
