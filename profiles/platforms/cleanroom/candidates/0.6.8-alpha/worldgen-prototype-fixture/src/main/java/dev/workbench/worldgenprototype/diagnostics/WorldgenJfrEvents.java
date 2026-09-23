package dev.workbench.worldgenprototype.diagnostics;

import jdk.jfr.Category;
import jdk.jfr.Description;
import jdk.jfr.Enabled;
import jdk.jfr.Event;
import jdk.jfr.EventType;
import jdk.jfr.Label;
import jdk.jfr.Name;
import jdk.jfr.StackTrace;
import jdk.jfr.Threshold;

/** Low-overhead JFR events for production-shaped world-generation profiling. */
public final class WorldgenJfrEvents {

    private static final EventType CHUNK_SAMPLE_TYPE =
            EventType.getEventType(ChunkSampleEvent.class);
    private static final EventType CHUNK_STAGE_TYPE =
            EventType.getEventType(ChunkStageEvent.class);
    private static final EventType BIOME_AREA_TYPE =
            EventType.getEventType(BiomeAreaEvent.class);
    private static final EventType WATERSHED_TILE_TYPE =
            EventType.getEventType(WatershedTileEvent.class);
    private static final boolean TRACE_BIOME_POINTS = Boolean.parseBoolean(
            System.getProperty("workbench.worldgen.jfr.biome_points", "false")
    );

    private WorldgenJfrEvents() {
    }

    public static ChunkSampleEvent beginChunkSample(
            long seed,
            int chunkX,
            int chunkZ,
            long planVersion,
            String planHash
    ) {
        if (!CHUNK_SAMPLE_TYPE.isEnabled()) {
            return null;
        }
        ChunkSampleEvent event = new ChunkSampleEvent();
        event.seed = seed;
        event.chunkX = chunkX;
        event.chunkZ = chunkZ;
        event.planVersion = planVersion;
        event.planHash = planHash;
        event.begin();
        return event;
    }

    public static void endChunkSample(ChunkSampleEvent event, boolean cacheHit, int cacheSize) {
        if (event == null) {
            return;
        }
        event.cacheHit = cacheHit;
        event.cacheSize = cacheSize;
        event.end();
        event.commit();
    }

    public static ChunkStageEvent beginChunkStage(
            long seed,
            int dimension,
            int chunkX,
            int chunkZ,
            String stage,
            long planVersion,
            String planHash
    ) {
        if (!CHUNK_STAGE_TYPE.isEnabled()) {
            return null;
        }
        ChunkStageEvent event = new ChunkStageEvent();
        event.seed = seed;
        event.dimension = dimension;
        event.chunkX = chunkX;
        event.chunkZ = chunkZ;
        event.stage = stage;
        event.planVersion = planVersion;
        event.planHash = planHash;
        event.begin();
        return event;
    }

    public static void endChunkStage(ChunkStageEvent event) {
        if (event == null) {
            return;
        }
        event.end();
        event.commit();
    }

    public static BiomeAreaEvent beginBiomeArea(
            int areaX,
            int areaZ,
            int width,
            int height,
            int coordinateScale,
            boolean chunkFastPath,
            long planVersion,
            String planHash
    ) {
        if (!BIOME_AREA_TYPE.isEnabled()
                || (!TRACE_BIOME_POINTS && width == 1 && height == 1)) {
            return null;
        }
        BiomeAreaEvent event = new BiomeAreaEvent();
        event.areaX = areaX;
        event.areaZ = areaZ;
        event.width = width;
        event.height = height;
        event.coordinateScale = coordinateScale;
        event.chunkFastPath = chunkFastPath;
        event.planVersion = planVersion;
        event.planHash = planHash;
        event.begin();
        return event;
    }

    public static void endBiomeArea(BiomeAreaEvent event) {
        if (event == null) {
            return;
        }
        event.end();
        event.commit();
    }

    public static WatershedTileEvent beginWatershedTile(
            long seed,
            long tileX,
            long tileZ,
            int cellSizeBlocks,
            int tileSizeCells,
            int haloCells,
            String algorithmVersion
    ) {
        if (!WATERSHED_TILE_TYPE.isEnabled()) {
            return null;
        }
        WatershedTileEvent event = new WatershedTileEvent();
        event.seed = seed;
        event.tileX = tileX;
        event.tileZ = tileZ;
        event.cellSizeBlocks = cellSizeBlocks;
        event.tileSizeCells = tileSizeCells;
        event.haloCells = haloCells;
        event.algorithmVersion = algorithmVersion;
        event.begin();
        return event;
    }

    public static void endWatershedTile(
            WatershedTileEvent event,
            int computedGridCells,
            int filledCoreCells,
            float maximumFillDepth,
            float maximumDischarge,
            int cacheSize
    ) {
        if (event == null) {
            return;
        }
        event.computedGridCells = computedGridCells;
        event.filledCoreCells = filledCoreCells;
        event.maximumFillDepth = maximumFillDepth;
        event.maximumDischarge = maximumDischarge;
        event.cacheSize = cacheSize;
        event.end();
        event.commit();
    }

    @Name("dev.workbench.worldgen.ChunkSample")
    @Label("World Studio Chunk Sample")
    @Description("Computes or reuses one immutable primitive-array terrain sample")
    @Category({"Workbench", "World Generation"})
    @Enabled(true)
    @StackTrace(false)
    @Threshold("0 ns")
    public static final class ChunkSampleEvent extends Event {

        @Label("World Seed")
        public long seed;

        @Label("Chunk X")
        public int chunkX;

        @Label("Chunk Z")
        public int chunkZ;

        @Label("Plan Version")
        public long planVersion;

        @Label("Plan Hash")
        public String planHash;

        @Label("Cache Hit")
        public boolean cacheHit;

        @Label("Cache Size")
        public int cacheSize;
    }

    @Name("dev.workbench.worldgen.ChunkStage")
    @Label("World Studio Chunk Stage")
    @Description("Measures one generator or population stage on the Minecraft thread")
    @Category({"Workbench", "World Generation"})
    @Enabled(true)
    @StackTrace(false)
    @Threshold("0 ns")
    public static final class ChunkStageEvent extends Event {

        @Label("World Seed")
        public long seed;

        @Label("Dimension")
        public int dimension;

        @Label("Chunk X")
        public int chunkX;

        @Label("Chunk Z")
        public int chunkZ;

        @Label("Stage")
        public String stage;

        @Label("Plan Version")
        public long planVersion;

        @Label("Plan Hash")
        public String planHash;
    }

    @Name("dev.workbench.worldgen.BiomeArea")
    @Label("World Studio Biome Area")
    @Description("Measures one Forge-facing GenLayer area request and its sampling mode")
    @Category({"Workbench", "World Generation"})
    @Enabled(true)
    @StackTrace(false)
    @Threshold("0 ns")
    public static final class BiomeAreaEvent extends Event {

        @Label("Area X")
        public int areaX;

        @Label("Area Z")
        public int areaZ;

        @Label("Width")
        public int width;

        @Label("Height")
        public int height;

        @Label("Coordinate Scale")
        public int coordinateScale;

        @Label("Chunk Fast Path")
        public boolean chunkFastPath;

        @Label("Plan Version")
        public long planVersion;

        @Label("Plan Hash")
        public String planHash;
    }

    @Name("dev.workbench.worldgen.WatershedTile")
    @Label("World Studio Watershed Tile")
    @Description("Builds one haloed Priority-Flood/D8 drainage tile")
    @Category({"Workbench", "World Generation"})
    @Enabled(true)
    @StackTrace(false)
    @Threshold("0 ns")
    public static final class WatershedTileEvent extends Event {

        @Label("World Seed")
        public long seed;

        @Label("Tile X")
        public long tileX;

        @Label("Tile Z")
        public long tileZ;

        @Label("Cell Size in Blocks")
        public int cellSizeBlocks;

        @Label("Core Tile Size in Cells")
        public int tileSizeCells;

        @Label("Halo Cells")
        public int haloCells;

        @Label("Algorithm Version")
        public String algorithmVersion;

        @Label("Computed Grid Cells")
        public int computedGridCells;

        @Label("Filled Core Cells")
        public int filledCoreCells;

        @Label("Maximum Fill Depth")
        public float maximumFillDepth;

        @Label("Maximum Discharge")
        public float maximumDischarge;

        @Label("Tile Cache Size")
        public int cacheSize;
    }
}
