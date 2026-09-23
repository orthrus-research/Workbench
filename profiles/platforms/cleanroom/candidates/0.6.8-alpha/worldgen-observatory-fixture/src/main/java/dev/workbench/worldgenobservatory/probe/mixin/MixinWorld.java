package dev.workbench.worldgenobservatory.probe.mixin;

import com.llamalad7.mixinextras.injector.wrapmethod.WrapMethod;
import com.llamalad7.mixinextras.injector.wrapoperation.Operation;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import org.spongepowered.asm.mixin.Mixin;

@Mixin(World.class)
public abstract class MixinWorld {

    @WrapMethod(
        method = "setBlockState(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;I)Z",
        require = 1,
        expect = 1,
        allow = 1
    )
    private boolean workbench$observeWorldSetBlockState(
        BlockPos position,
        IBlockState requested,
        int flags,
        Operation<Boolean> original
    ) {
        if (!ProbeRuntime.hasActiveWorldgenSpan()) {
            return original.call(position, requested, flags);
        }
        ProbeRuntime.WriteToken write = ProbeRuntime.beginWrite(
            "cleanroom-worldgen:write.world_api",
            "world_api",
            position.getX(),
            position.getY(),
            position.getZ(),
            null,
            requested,
            flags,
            World.class.getName()
        );
        try {
            boolean changed = original.call(position, requested, flags);
            ProbeRuntime.finishWrite(
                write,
                null,
                changed ? requested : null,
                true,
                null
            );
            return changed;
        } catch (Throwable originalFailure) {
            ProbeRuntime.finishWrite(write, null, null, true, originalFailure);
            return ProbeRuntime.sneakyThrow(originalFailure);
        }
    }
}
