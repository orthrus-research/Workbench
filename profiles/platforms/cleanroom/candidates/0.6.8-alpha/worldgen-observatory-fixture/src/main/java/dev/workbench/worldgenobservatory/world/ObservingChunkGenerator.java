package dev.workbench.worldgenobservatory.world;

import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import dev.workbench.worldgenobservatory.trace.ChunkCheckpoint;
import dev.workbench.worldgenobservatory.trace.RngLanes;
import dev.workbench.worldgenobservatory.trace.TraceContext;
import dev.workbench.worldgenobservatory.trace.TraceRecord;
import dev.workbench.worldgenobservatory.trace.TraceSink;
import dev.workbench.worldgenobservatory.trace.TraceSinks;
import java.util.List;
import javax.annotation.Nullable;
import net.minecraft.entity.EnumCreatureType;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraft.world.biome.Biome;
import net.minecraft.world.chunk.Chunk;
import net.minecraft.world.gen.IChunkGenerator;

/**
 * Read-only observation wrapper around a normal Forge/Cleanroom generator.
 * It never replaces the delegate's Random and never re-posts lifecycle events.
 */
public final class ObservingChunkGenerator implements IChunkGenerator {

    public static final String OWNER = "dev.workbench.worldgenobservatory.world.ObservingChunkGenerator";

    private final World world;
    private final IChunkGenerator delegate;
    private final String delegateDecision;

    public ObservingChunkGenerator(World world, IChunkGenerator delegate) {
        this.world = world;
        this.delegate = delegate;
        this.delegateDecision = "delegate=" + delegate.getClass().getName();
    }

    @Override
    public Chunk generateChunk(int chunkX, int chunkZ) {
        TraceSink sink = TraceSinks.current();
        TraceContext context = TraceContext.of(world, chunkX, chunkZ);
        long worldSeed = world.getSeed();
        int dimension = world.provider.getDimension();
        if (ProbeRuntime.enabled()) {
            ProbeRuntime.cooperativeStage(
                "chunk.generate",
                "enter",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            ProbeRuntime.cooperativeRng(
                "chunk.generate",
                RngLanes.Lane.TERRAIN.name().toLowerCase(java.util.Locale.ROOT),
                RngLanes.seed(worldSeed, dimension, chunkX, chunkZ, RngLanes.Lane.TERRAIN),
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            ProbeRuntime.cooperativeDecision(
                "chunk.generate",
                delegateDecision,
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
        }
        if (sink.enabled()) {
            sink.emit(TraceRecord.stage(context, "chunk.generate.enter", OWNER));
            sink.emit(TraceRecord.rng(context, "chunk.generate", RngLanes.Lane.TERRAIN, OWNER));
            sink.emit(TraceRecord.decision(
                    context,
                    "chunk.generate",
                    delegateDecision,
                    OWNER
            ));
        }

        Chunk chunk = delegate.generateChunk(chunkX, chunkZ);

        if (sink.checkpointsEnabled()) {
            sink.emit(TraceRecord.checkpoint(
                    context,
                    "chunk.generate.exit",
                    ChunkCheckpoint.blockStateDigest(chunk),
                    OWNER
            ));
        }
        if (ProbeRuntime.enabled()) {
            try {
                ProbeRuntime.cooperativeCheckpoint(
                    "chunk.generate.exit",
                    ChunkCheckpoint.blockStateSha256(chunk),
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    OWNER
                );
            } catch (Throwable observationFailure) {
                ProbeRuntime.cooperativeFailure(
                    "CRW927_GENERATE_CHECKPOINT_FAILED",
                    "chunk.generate.exit",
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    OWNER,
                    observationFailure
                );
            }
            ProbeRuntime.cooperativeStage(
                "chunk.generate",
                "exit",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
        }
        if (sink.enabled()) {
            sink.emit(TraceRecord.stage(context, "chunk.generate.exit", OWNER));
        }
        return chunk;
    }

    @Override
    public void populate(int chunkX, int chunkZ) {
        TraceSink sink = TraceSinks.current();
        TraceContext context = TraceContext.of(world, chunkX, chunkZ);
        long worldSeed = world.getSeed();
        int dimension = world.provider.getDimension();
        if (ProbeRuntime.enabled()) {
            ProbeRuntime.cooperativeStage(
                "chunk.populate",
                "enter",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            ProbeRuntime.cooperativeRng(
                "chunk.populate",
                RngLanes.Lane.POPULATION.name().toLowerCase(java.util.Locale.ROOT),
                RngLanes.seed(worldSeed, dimension, chunkX, chunkZ, RngLanes.Lane.POPULATION),
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
            ProbeRuntime.cooperativeDecision(
                "chunk.populate",
                "standard_lifecycle_owner=delegate",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
        }
        if (sink.enabled()) {
            sink.emit(TraceRecord.stage(context, "chunk.populate.enter", OWNER));
            sink.emit(TraceRecord.rng(context, "chunk.populate", RngLanes.Lane.POPULATION, OWNER));
            sink.emit(TraceRecord.decision(
                    context,
                    "chunk.populate",
                    "standard_lifecycle_owner=delegate",
                    OWNER
            ));
        }

        // ChunkGeneratorOverworld owns and posts the standard Forge populate
        // and biome-decoration events. The wrapper must not duplicate them.
        delegate.populate(chunkX, chunkZ);

        if (sink.checkpointsEnabled()) {
            sink.emit(TraceRecord.checkpoint(
                    context,
                    "chunk.populate.exit",
                    ChunkCheckpoint.blockStateDigest(world.getChunk(chunkX, chunkZ)),
                    OWNER
            ));
        }
        if (ProbeRuntime.enabled()) {
            try {
                ProbeRuntime.cooperativeCheckpoint(
                    "chunk.populate.exit",
                    ChunkCheckpoint.blockStateSha256(world.getChunk(chunkX, chunkZ)),
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    OWNER
                );
            } catch (Throwable observationFailure) {
                ProbeRuntime.cooperativeFailure(
                    "CRW928_POPULATE_CHECKPOINT_FAILED",
                    "chunk.populate.exit",
                    worldSeed,
                    dimension,
                    chunkX,
                    chunkZ,
                    OWNER,
                    observationFailure
                );
            }
            ProbeRuntime.cooperativeStage(
                "chunk.populate",
                "exit",
                worldSeed,
                dimension,
                chunkX,
                chunkZ,
                OWNER
            );
        }
        if (sink.enabled()) {
            sink.emit(TraceRecord.stage(context, "chunk.populate.exit", OWNER));
        }
    }

    @Override
    public boolean generateStructures(Chunk chunk, int chunkX, int chunkZ) {
        return delegate.generateStructures(chunk, chunkX, chunkZ);
    }

    @Override
    public List<Biome.SpawnListEntry> getPossibleCreatures(
            EnumCreatureType creatureType,
            BlockPos position
    ) {
        return delegate.getPossibleCreatures(creatureType, position);
    }

    @Nullable
    @Override
    public BlockPos getNearestStructurePos(
            World world,
            String structureName,
            BlockPos position,
            boolean findUnexplored
    ) {
        return delegate.getNearestStructurePos(world, structureName, position, findUnexplored);
    }

    @Override
    public boolean isInsideStructure(World world, String structureName, BlockPos position) {
        return delegate.isInsideStructure(world, structureName, position);
    }

    @Override
    public void recreateStructures(Chunk chunk, int chunkX, int chunkZ) {
        delegate.recreateStructures(chunk, chunkX, chunkZ);
    }
}
