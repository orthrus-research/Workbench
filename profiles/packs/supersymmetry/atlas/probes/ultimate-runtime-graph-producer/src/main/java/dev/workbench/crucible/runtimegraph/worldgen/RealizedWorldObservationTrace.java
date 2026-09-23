package dev.workbench.crucible.runtimegraph.worldgen;

import ivorius.reccomplex.events.StructureGenerationEventLite;

import net.minecraft.world.World;
import net.minecraftforge.event.terraingen.PopulateChunkEvent;
import net.minecraftforge.event.world.ChunkEvent;
import net.minecraftforge.event.world.WorldEvent;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Lossless, non-invasive custody for naturally observed world-generation stages.
 *
 * <p>The trace never asks a generator to run. Forge, Recurrent Complex, and the
 * Cave Generator mixin seams call it only while the pack performs its ordinary
 * dedicated-server world load and spawn preparation.</p>
 */
public final class RealizedWorldObservationTrace {
    private static final RealizedWorldObservationTrace GLOBAL =
        new RealizedWorldObservationTrace();

    private final List<WorldStageEvent> worldStages = new ArrayList<WorldStageEvent>();
    private final List<RecurrentComplexEvent> recurrentComplex =
        new ArrayList<RecurrentComplexEvent>();
    private final List<CaveStageEvent> caveStages = new ArrayList<CaveStageEvent>();
    private boolean registered;

    public static RealizedWorldObservationTrace global() { return GLOBAL; }

    public synchronized void markRegistered() {
        if (registered) throw new IllegalStateException("realized-world observer registered twice");
        registered = true;
    }

    @SubscribeEvent
    public void worldLoaded(WorldEvent.Load event) {
        World world = event.getWorld();
        if (!serverWorld(world)) return;
        addWorldStage(world, 0, 0, "world-load");
    }

    @SubscribeEvent
    public void chunkLoaded(ChunkEvent.Load event) {
        World world = event.getWorld();
        if (!serverWorld(world)) return;
        addWorldStage(
            world,
            event.getChunk().x,
            event.getChunk().z,
            "chunk-load"
        );
    }

    @SubscribeEvent
    public void populationStarted(PopulateChunkEvent.Pre event) {
        World world = event.getWorld();
        if (!serverWorld(world)) return;
        addWorldStage(world, event.getChunkX(), event.getChunkZ(), "population-pre");
    }

    @SubscribeEvent
    public void populationCompleted(PopulateChunkEvent.Post event) {
        World world = event.getWorld();
        if (!serverWorld(world)) return;
        addWorldStage(world, event.getChunkX(), event.getChunkZ(), "population-post");
    }

    @SubscribeEvent
    public void recurrentComplexStarted(StructureGenerationEventLite.Pre event) {
        addRecurrentComplex(event, "placement-pre");
    }

    @SubscribeEvent
    public void recurrentComplexCompleted(StructureGenerationEventLite.Post event) {
        addRecurrentComplex(event, "placement-post");
    }

    public synchronized void caveHookCompleted(
        String stage,
        World world,
        int chunkX,
        int chunkZ,
        List<String> controllerIds
    ) {
        if (!serverWorld(world)) return;
        if (!"early-map".equals(stage) && !"feature".equals(stage)) {
            throw new IllegalStateException("unsupported Cave Generator stage " + stage);
        }
        if (controllerIds == null) {
            throw new IllegalStateException("Cave Generator controller IDs are null");
        }
        List<String> ids = new ArrayList<String>(controllerIds);
        Collections.sort(ids);
        for (int index = 0; index < ids.size(); index++) {
            String id = ids.get(index);
            if (!valid(id) || (index > 0 && id.equals(ids.get(index - 1)))) {
                throw new IllegalStateException("invalid Cave Generator controller identity");
            }
            caveStages.add(new CaveStageEvent(
                world.getSeed(),
                world.provider.getDimension(),
                chunkX,
                chunkZ,
                stage,
                id
            ));
        }
    }

    public synchronized Snapshot snapshot() {
        if (!registered) {
            throw new IllegalStateException("realized-world observer was not registered");
        }
        return new Snapshot(worldStages, recurrentComplex, caveStages);
    }

    private synchronized void addWorldStage(
        World world,
        int chunkX,
        int chunkZ,
        String stage
    ) {
        worldStages.add(new WorldStageEvent(
            world.getSeed(),
            world.provider.getDimension(),
            chunkX,
            chunkZ,
            stage
        ));
    }

    private synchronized void addRecurrentComplex(
        StructureGenerationEventLite event,
        String stage
    ) {
        World world = event.getWorld();
        if (!serverWorld(world)) return;
        if (!valid(event.getStructureName()) || event.getBoundingBox() == null) {
            throw new IllegalStateException("invalid Recurrent Complex placement event");
        }
        net.minecraft.world.gen.structure.StructureBoundingBox box =
            event.getBoundingBox();
        recurrentComplex.add(new RecurrentComplexEvent(
            world.getSeed(),
            world.provider.getDimension(),
            event.getStructureName(),
            box.minX,
            box.minY,
            box.minZ,
            box.maxX,
            box.maxY,
            box.maxZ,
            event.getGenerationLayer(),
            event.isFirstTime(),
            stage
        ));
    }

    private static boolean serverWorld(World world) {
        return world != null && !world.isRemote && world.provider != null;
    }

    private static boolean valid(String value) {
        return value != null && !value.isEmpty()
            && value.indexOf('\r') < 0 && value.indexOf('\n') < 0
            && value.indexOf('\0') < 0;
    }

    public static final class Snapshot {
        public final List<WorldStageEvent> worldStages;
        public final List<RecurrentComplexEvent> recurrentComplex;
        public final List<CaveStageEvent> caveStages;

        private Snapshot(
            List<WorldStageEvent> worldStages,
            List<RecurrentComplexEvent> recurrentComplex,
            List<CaveStageEvent> caveStages
        ) {
            this.worldStages = Collections.unmodifiableList(
                new ArrayList<WorldStageEvent>(worldStages)
            );
            this.recurrentComplex = Collections.unmodifiableList(
                new ArrayList<RecurrentComplexEvent>(recurrentComplex)
            );
            this.caveStages = Collections.unmodifiableList(
                new ArrayList<CaveStageEvent>(caveStages)
            );
        }
    }

    public static final class WorldStageEvent {
        public final long worldSeed;
        public final int dimensionId;
        public final int chunkX;
        public final int chunkZ;
        public final String stage;

        private WorldStageEvent(
            long worldSeed,
            int dimensionId,
            int chunkX,
            int chunkZ,
            String stage
        ) {
            this.worldSeed = worldSeed;
            this.dimensionId = dimensionId;
            this.chunkX = chunkX;
            this.chunkZ = chunkZ;
            this.stage = stage;
        }
    }

    public static final class RecurrentComplexEvent {
        public final long worldSeed;
        public final int dimensionId;
        public final String structureId;
        public final int minX;
        public final int minY;
        public final int minZ;
        public final int maxX;
        public final int maxY;
        public final int maxZ;
        public final int generationLayer;
        public final boolean firstTime;
        public final String stage;

        private RecurrentComplexEvent(
            long worldSeed,
            int dimensionId,
            String structureId,
            int minX,
            int minY,
            int minZ,
            int maxX,
            int maxY,
            int maxZ,
            int generationLayer,
            boolean firstTime,
            String stage
        ) {
            this.worldSeed = worldSeed;
            this.dimensionId = dimensionId;
            this.structureId = structureId;
            this.minX = minX;
            this.minY = minY;
            this.minZ = minZ;
            this.maxX = maxX;
            this.maxY = maxY;
            this.maxZ = maxZ;
            this.generationLayer = generationLayer;
            this.firstTime = firstTime;
            this.stage = stage;
        }
    }

    public static final class CaveStageEvent {
        public final long worldSeed;
        public final int dimensionId;
        public final int chunkX;
        public final int chunkZ;
        public final String stage;
        public final String controllerId;

        private CaveStageEvent(
            long worldSeed,
            int dimensionId,
            int chunkX,
            int chunkZ,
            String stage,
            String controllerId
        ) {
            this.worldSeed = worldSeed;
            this.dimensionId = dimensionId;
            this.chunkX = chunkX;
            this.chunkZ = chunkZ;
            this.stage = stage;
            this.controllerId = controllerId;
        }
    }
}
