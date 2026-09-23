package dev.workbench.worldgenobservatory.world;

import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import dev.workbench.worldgenobservatory.trace.ChunkCheckpoint;
import dev.workbench.worldgenobservatory.trace.RngLanes;
import dev.workbench.worldgenobservatory.trace.TraceContext;
import dev.workbench.worldgenobservatory.trace.TraceRecord;
import dev.workbench.worldgenobservatory.trace.TraceSink;
import dev.workbench.worldgenobservatory.trace.TraceSinks;
import java.util.Random;
import net.minecraft.world.World;
import net.minecraft.world.chunk.IChunkProvider;
import net.minecraft.world.gen.IChunkGenerator;
import net.minecraftforge.fml.common.IWorldGenerator;

/**
 * A read-only, standard Forge IWorldGenerator used to checkpoint the external
 * world-generator window. It names no external producer and has no adapter.
 */
public final class FinalCheckpointWorldGenerator implements IWorldGenerator {

    public static final int WEIGHT = Integer.MAX_VALUE;
    private static final String OWNER =
            "dev.workbench.worldgenobservatory.world.FinalCheckpointWorldGenerator";

    @Override
    public void generate(
            Random random,
            int chunkX,
            int chunkZ,
            World world,
            IChunkGenerator chunkGenerator,
            IChunkProvider chunkProvider
    ) {
        if (!(chunkGenerator instanceof ObservingChunkGenerator)) {
            return;
        }

        TraceSink sink = TraceSinks.current();
        boolean rawEnabled = ProbeRuntime.enabled();
        if (!sink.enabled() && !rawEnabled) {
            return;
        }

        TraceContext context = TraceContext.of(world, chunkX, chunkZ);
        long worldSeed = world.getSeed();
        int dimension = world.provider.getDimension();
        if (rawEnabled) {
            ProbeRuntime.cooperativeStage(
                "forge.world_generators.final",
                "callback",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            ProbeRuntime.cooperativeRng(
                "forge.world_generators.final",
                RngLanes.Lane.FINAL_CHECKPOINT.name().toLowerCase(java.util.Locale.ROOT),
                RngLanes.seed(
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    RngLanes.Lane.FINAL_CHECKPOINT
                ),
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            ProbeRuntime.cooperativeDecision(
                "forge.world_generators.final",
                "read_only_checkpoint=complete",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            try {
                ProbeRuntime.cooperativeCheckpoint(
                    "forge.world_generators.final",
                    ChunkCheckpoint.blockStateSha256(world.getChunk(chunkX, chunkZ)),
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    OWNER
                );
            } catch (Throwable observationFailure) {
                ProbeRuntime.cooperativeFailure(
                    "CRW929_FINAL_CHECKPOINT_FAILED",
                    "forge.world_generators.final",
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    OWNER,
                    observationFailure
                );
            }
        }
        if (sink.enabled()) {
            sink.emit(TraceRecord.stage(context, "forge.world_generators.final", OWNER));
            sink.emit(TraceRecord.rng(
                    context,
                    "forge.world_generators.final",
                    RngLanes.Lane.FINAL_CHECKPOINT,
                    OWNER
            ));
            sink.emit(TraceRecord.decision(
                    context,
                    "forge.world_generators.final",
                    "read_only_checkpoint=complete",
                    OWNER
            ));
            if (sink.checkpointsEnabled()) {
                sink.emit(TraceRecord.checkpoint(
                        context,
                        "forge.world_generators.final",
                        ChunkCheckpoint.blockStateDigest(world.getChunk(chunkX, chunkZ)),
                        OWNER
                ));
            }
        }
    }
}
