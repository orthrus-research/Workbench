package dev.workbench.worldgenprototype.world.hydrology;

import dev.workbench.worldgenprototype.diagnostics.WorldgenJfrEvents;
import it.unimi.dsi.fastutil.longs.Long2ObjectLinkedOpenHashMap;
import java.util.Arrays;

/**
 * Deterministic, bounded macro-tile drainage engine.
 *
 * <p>Each canonical tile samples a coarse elevation/rainfall/lithology grid
 * with a halo, resolves sinks with Priority-Flood, assigns D8 receivers, and
 * accumulates runoff toward those receivers. Only immutable core cells are
 * retained. Cache misses may repeat work, but request order never changes a
 * cell result.</p>
 */
public final class WatershedEngine {

    public static final String ALGORITHM_VERSION = "priority-flood-d8-v1";
    public static final int COLUMN_COUNT = 16 * 16;

    private static final byte OUTLET = 8;
    private static final float MINIMUM_DRAINAGE_DROP = 0.001F;
    private static final long FINGERPRINT_OFFSET = 0xcbf29ce484222325L;
    private static final long FINGERPRINT_PRIME = 0x100000001b3L;
    private static final int[] DIRECTION_X = {0, 1, 1, 1, 0, -1, -1, -1};
    private static final int[] DIRECTION_Z = {-1, -1, 0, 1, 1, 1, 0, -1};
    private static final String[] DIRECTION_NAMES = {
            "north",
            "north_east",
            "east",
            "south_east",
            "south",
            "south_west",
            "west",
            "north_west",
            "outlet"
    };

    private final long seed;
    private final Configuration configuration;
    private final int tileCacheCapacity;
    private final CellSampler sampler;
    private final Long2ObjectLinkedOpenHashMap<WatershedTile> tileCache;
    private final ThreadLocal<SegmentWindow> scalarWindows;

    private long tileCacheHits;
    private long tileCacheMisses;
    private long tileCacheEvictions;
    private long computedGridCells;

    public WatershedEngine(
            long seed,
            Configuration configuration,
            int tileCacheCapacity,
            CellSampler sampler
    ) {
        if (tileCacheCapacity < 1) {
            throw new IllegalArgumentException("watershed tile cache must be positive");
        }
        this.seed = seed;
        this.configuration = configuration;
        this.tileCacheCapacity = tileCacheCapacity;
        this.sampler = sampler;
        this.tileCache = new Long2ObjectLinkedOpenHashMap<>(tileCacheCapacity, 0.75F);
        this.scalarWindows = ThreadLocal.withInitial(
                () -> new SegmentWindow(
                        configuration.windowCellCapacity(),
                        configuration.windowTileCapacity()
                )
        );
    }

    public Configuration configuration() {
        return configuration;
    }

    public ChunkHydrology sampleChunk(int chunkX, int chunkZ) {
        int originX = chunkX << 4;
        int originZ = chunkZ << 4;
        int cellRadius = configuration.channelSearchRadiusCells();
        long minimumCellX = Math.floorDiv(originX, configuration.cellSizeBlocks())
                - cellRadius;
        long minimumCellZ = Math.floorDiv(originZ, configuration.cellSizeBlocks())
                - cellRadius;
        long maximumCellX = Math.floorDiv(originX + 15, configuration.cellSizeBlocks())
                + cellRadius;
        long maximumCellZ = Math.floorDiv(originZ + 15, configuration.cellSizeBlocks())
                + cellRadius;
        SegmentWindow window = new SegmentWindow(
                configuration.windowCellCapacity(),
                configuration.windowTileCapacity()
        );
        populateWindow(window, minimumCellX, minimumCellZ, maximumCellX, maximumCellZ);
        ChunkHydrology output = new ChunkHydrology(
                chunkX,
                chunkZ,
                configuration.cellSizeBlocks(),
                configuration.tileSizeCells()
        );
        MutableHydrologySample scratch = new MutableHydrologySample();
        for (int localZ = 0; localZ < 16; localZ++) {
            for (int localX = 0; localX < 16; localX++) {
                window.sample(originX + localX, originZ + localZ, scratch);
                output.set(localX, localZ, scratch);
            }
        }
        return output;
    }

    public void sampleBlock(int blockX, int blockZ, MutableHydrologySample output) {
        int cellSize = configuration.cellSizeBlocks();
        int radius = configuration.channelSearchRadiusCells();
        long cellX = Math.floorDiv(blockX, cellSize);
        long cellZ = Math.floorDiv(blockZ, cellSize);
        SegmentWindow window = scalarWindows.get();
        populateWindow(
                window,
                cellX - radius,
                cellZ - radius,
                cellX + radius,
                cellZ + radius
        );
        window.sample(blockX, blockZ, output);
    }

    public void clearCache() {
        synchronized (tileCache) {
            tileCache.clear();
        }
        scalarWindows.remove();
    }

    public int tileCacheCapacity() {
        return tileCacheCapacity;
    }

    public int tileCacheSize() {
        synchronized (tileCache) {
            return tileCache.size();
        }
    }

    public long tileCacheHits() {
        synchronized (tileCache) {
            return tileCacheHits;
        }
    }

    public long tileCacheMisses() {
        synchronized (tileCache) {
            return tileCacheMisses;
        }
    }

    public long tileCacheEvictions() {
        synchronized (tileCache) {
            return tileCacheEvictions;
        }
    }

    public long computedGridCells() {
        synchronized (tileCache) {
            return computedGridCells;
        }
    }

    public static String directionName(int direction) {
        return direction >= 0 && direction < DIRECTION_NAMES.length
                ? DIRECTION_NAMES[direction]
                : "unknown";
    }

    private void populateWindow(
            SegmentWindow window,
            long minimumCellX,
            long minimumCellZ,
            long maximumCellX,
            long maximumCellZ
    ) {
        window.reset(minimumCellX, minimumCellZ, maximumCellX, maximumCellZ);
        CellCursor upstream = new CellCursor();
        CellCursor downstream = new CellCursor();
        for (long cellZ = minimumCellZ; cellZ <= maximumCellZ; cellZ++) {
            for (long cellX = minimumCellX; cellX <= maximumCellX; cellX++) {
                window.readCell(cellX, cellZ, upstream);
                window.addCell(cellX, cellZ, upstream);
                if (upstream.direction == OUTLET
                        || upstream.discharge < configuration.streamDischarge()) {
                    continue;
                }
                long receiverX = cellX + DIRECTION_X[upstream.direction];
                long receiverZ = cellZ + DIRECTION_Z[upstream.direction];
                window.readCell(receiverX, receiverZ, downstream);
                window.addSegment(cellX, cellZ, upstream, receiverX, receiverZ, downstream);
            }
        }
    }

    private WatershedTile tile(long tileX, long tileZ) {
        long key = coordinateKey(tileX, tileZ);
        synchronized (tileCache) {
            WatershedTile cached = tileCache.getAndMoveToLast(key);
            if (cached != null) {
                tileCacheHits++;
                return cached;
            }
        }

        WorldgenJfrEvents.WatershedTileEvent event =
                WorldgenJfrEvents.beginWatershedTile(
                        seed,
                        tileX,
                        tileZ,
                        configuration.cellSizeBlocks(),
                        configuration.tileSizeCells(),
                        configuration.haloCells(),
                        ALGORITHM_VERSION
                );
        WatershedTile computed = null;
        try {
            computed = computeTile(tileX, tileZ);
            synchronized (tileCache) {
                WatershedTile raced = tileCache.getAndMoveToLast(key);
                if (raced != null) {
                    tileCacheHits++;
                    return raced;
                }
                tileCache.putAndMoveToLast(key, computed);
                tileCacheMisses++;
                computedGridCells += computed.computedGridCells;
                if (tileCache.size() > tileCacheCapacity) {
                    tileCache.removeFirst();
                    tileCacheEvictions++;
                }
                return computed;
            }
        } finally {
            WorldgenJfrEvents.endWatershedTile(
                    event,
                    computed == null ? 0 : computed.computedGridCells(),
                    computed == null ? 0 : computed.filledCellCount(),
                    computed == null ? 0.0F : computed.maximumFillDepth(),
                    computed == null ? 0.0F : computed.maximumDischarge(),
                    tileCacheSize()
            );
        }
    }

    private WatershedTile computeTile(long tileX, long tileZ) {
        int coreSize = configuration.tileSizeCells();
        int halo = configuration.haloCells();
        int gridSize = coreSize + halo * 2;
        int cellCount = gridSize * gridSize;
        long coreOriginX = tileX * coreSize;
        long coreOriginZ = tileZ * coreSize;
        long gridOriginX = coreOriginX - halo;
        long gridOriginZ = coreOriginZ - halo;

        float[] rawElevation = new float[cellCount];
        float[] filledElevation = new float[cellCount];
        float[] runoff = new float[cellCount];
        float[] discharge = new float[cellCount];
        float[] erodibility = new float[cellCount];
        int[] receiver = new int[cellCount];
        byte[] streamOrder = new byte[cellCount];
        int[] maximumIncomingOrder = new int[cellCount];
        int[] maximumIncomingCount = new int[cellCount];
        int[] visitOrder = new int[cellCount];
        boolean[] visited = new boolean[cellCount];
        Arrays.fill(receiver, -1);

        CellData cell = new CellData();
        for (int localZ = 0; localZ < gridSize; localZ++) {
            long cellZ = gridOriginZ + localZ;
            int blockZ = cellCenterBlock(cellZ);
            for (int localX = 0; localX < gridSize; localX++) {
                long cellX = gridOriginX + localX;
                int blockX = cellCenterBlock(cellX);
                sampler.sample(blockX, blockZ, cell);
                int index = localX + localZ * gridSize;
                rawElevation[index] = (float) cell.elevation;
                filledElevation[index] = rawElevation[index];
                float localRunoff = (float) Math.max(
                        0.0D,
                        configuration.baseRunoff()
                                + configuration.rainfallScale()
                                * cell.rainfall
                                * (1.0D - cell.permeability
                                * configuration.permeabilityInfluence())
                );
                runoff[index] = localRunoff;
                discharge[index] = localRunoff;
                erodibility[index] = (float) cell.erodibility;
                streamOrder[index] = localRunoff > 0.0F ? (byte) 1 : 0;
            }
        }

        PrimitiveCellHeap frontier = new PrimitiveCellHeap(
                cellCount,
                filledElevation,
                gridOriginX,
                gridOriginZ,
                gridSize,
                seed
        );
        for (int localZ = 0; localZ < gridSize; localZ++) {
            for (int localX = 0; localX < gridSize; localX++) {
                if (localX != 0 && localZ != 0
                        && localX != gridSize - 1 && localZ != gridSize - 1) {
                    continue;
                }
                int index = localX + localZ * gridSize;
                visited[index] = true;
                frontier.add(index);
            }
        }

        int visitedCount = 0;
        while (!frontier.isEmpty()) {
            int current = frontier.removeFirst();
            visitOrder[visitedCount++] = current;
            int currentX = current % gridSize;
            int currentZ = current / gridSize;
            for (int direction = 0; direction < DIRECTION_X.length; direction++) {
                int nextX = currentX + DIRECTION_X[direction];
                int nextZ = currentZ + DIRECTION_Z[direction];
                if (nextX < 0 || nextZ < 0 || nextX >= gridSize || nextZ >= gridSize) {
                    continue;
                }
                int next = nextX + nextZ * gridSize;
                if (visited[next]) {
                    continue;
                }
                visited[next] = true;
                receiver[next] = current;
                filledElevation[next] = Math.max(
                        rawElevation[next],
                        filledElevation[current] + MINIMUM_DRAINAGE_DROP
                );
                frontier.add(next);
            }
        }
        if (visitedCount != cellCount) {
            throw new IllegalStateException("Priority-Flood did not visit every grid cell");
        }
        resolveD8Receivers(
                filledElevation,
                receiver,
                gridOriginX,
                gridOriginZ,
                gridSize
        );

        for (int orderIndex = visitedCount - 1; orderIndex >= 0; orderIndex--) {
            int current = visitOrder[orderIndex];
            int incomingOrder = maximumIncomingOrder[current];
            if (incomingOrder > 0) {
                int resolved = maximumIncomingCount[current] >= 2
                        ? incomingOrder + 1
                        : incomingOrder;
                streamOrder[current] = (byte) Math.min(255, Math.max(
                        Byte.toUnsignedInt(streamOrder[current]),
                        resolved
                ));
            }
            int target = receiver[current];
            if (target < 0) {
                continue;
            }
            discharge[target] += discharge[current];
            int order = Byte.toUnsignedInt(streamOrder[current]);
            if (order > maximumIncomingOrder[target]) {
                maximumIncomingOrder[target] = order;
                maximumIncomingCount[target] = 1;
            } else if (order == maximumIncomingOrder[target]) {
                maximumIncomingCount[target]++;
            }
        }

        WatershedTile output = new WatershedTile(tileX, tileZ, coreSize, cellCount);
        for (int localZ = 0; localZ < coreSize; localZ++) {
            for (int localX = 0; localX < coreSize; localX++) {
                int gridX = localX + halo;
                int gridZ = localZ + halo;
                int source = gridX + gridZ * gridSize;
                int target = localX + localZ * coreSize;
                output.rawElevation[target] = rawElevation[source];
                output.filledElevation[target] = filledElevation[source];
                output.runoff[target] = runoff[source];
                output.discharge[target] = discharge[source];
                output.erodibility[target] = erodibility[source];
                output.streamOrder[target] = streamOrder[source];
                int receiverIndex = receiver[source];
                if (receiverIndex >= 0) {
                    int receiverX = receiverIndex % gridSize;
                    int receiverZ = receiverIndex / gridSize;
                    output.direction[target] = directionFor(
                            receiverX - gridX,
                            receiverZ - gridZ
                    );
                } else {
                    output.direction[target] = OUTLET;
                }
                float fillDepth = Math.max(
                        0.0F,
                        filledElevation[source] - rawElevation[source]
                );
                output.fillDepth[target] = fillDepth;
                if (fillDepth > 0.01F) {
                    output.filledCellCount++;
                    output.maximumFillDepth = Math.max(output.maximumFillDepth, fillDepth);
                }
                output.maximumDischarge = Math.max(
                        output.maximumDischarge,
                        discharge[source]
                );
            }
        }
        return output;
    }

    private void resolveD8Receivers(
            float[] filledElevation,
            int[] receiver,
            long gridOriginX,
            long gridOriginZ,
            int gridSize
    ) {
        Arrays.fill(receiver, -1);
        for (int localZ = 1; localZ < gridSize - 1; localZ++) {
            for (int localX = 1; localX < gridSize - 1; localX++) {
                int current = localX + localZ * gridSize;
                double bestSlope = Double.NEGATIVE_INFINITY;
                long bestTie = 0L;
                int bestReceiver = -1;
                for (int direction = 0; direction < DIRECTION_X.length; direction++) {
                    int nextX = localX + DIRECTION_X[direction];
                    int nextZ = localZ + DIRECTION_Z[direction];
                    int next = nextX + nextZ * gridSize;
                    double drop = filledElevation[current] - filledElevation[next];
                    if (drop <= 0.0D) {
                        continue;
                    }
                    boolean diagonal = DIRECTION_X[direction] != 0
                            && DIRECTION_Z[direction] != 0;
                    double slope = diagonal ? drop * 0.7071067811865476D : drop;
                    long tie = mix64(
                            seed
                                    ^ coordinateKey(
                                    gridOriginX + nextX,
                                    gridOriginZ + nextZ
                            )
                    );
                    int comparison = Double.compare(slope, bestSlope);
                    if (comparison > 0
                            || (comparison == 0
                            && Long.compareUnsigned(tie, bestTie) < 0)) {
                        bestSlope = slope;
                        bestTie = tie;
                        bestReceiver = next;
                    }
                }
                if (bestReceiver < 0) {
                    throw new IllegalStateException(
                            "filled watershed cell has no downhill D8 receiver"
                    );
                }
                receiver[current] = bestReceiver;
            }
        }
    }

    private int cellCenterBlock(long cellCoordinate) {
        long block = cellCoordinate * configuration.cellSizeBlocks()
                + configuration.cellSizeBlocks() / 2L;
        return Math.toIntExact(block);
    }

    private static byte directionFor(int deltaX, int deltaZ) {
        for (byte direction = 0; direction < DIRECTION_X.length; direction++) {
            if (DIRECTION_X[direction] == deltaX && DIRECTION_Z[direction] == deltaZ) {
                return direction;
            }
        }
        return OUTLET;
    }

    private static long coordinateKey(long x, long z) {
        if (x < Integer.MIN_VALUE || x > Integer.MAX_VALUE
                || z < Integer.MIN_VALUE || z > Integer.MAX_VALUE) {
            throw new IllegalArgumentException("watershed coordinate exceeds exact cache key range");
        }
        return (x << 32) ^ (z & 0xffffffffL);
    }

    private static long mix64(long value) {
        value = (value ^ (value >>> 30)) * 0xbf58476d1ce4e5b9L;
        value = (value ^ (value >>> 27)) * 0x94d049bb133111ebL;
        return value ^ (value >>> 31);
    }

    /** Java 25 record used only as immutable cold-path configuration. */
    public record Configuration(
            int cellSizeBlocks,
            int tileSizeCells,
            int haloCells,
            double baseRunoff,
            double rainfallScale,
            double permeabilityInfluence,
            double streamDischarge,
            double riverDischarge,
            double streamHalfWidthBlocks,
            double riverHalfWidthBlocks,
            double streamDepthBlocks,
            double riverDepthBlocks,
            double bankBlendBlocks,
            double lakeMinimumFillDepth
    ) {

        public Configuration {
            requirePowerOfTwo("watershed cell size", cellSizeBlocks, 4, 64);
            requirePowerOfTwo("watershed tile size", tileSizeCells, 8, 128);
            if (haloCells < 2 || haloCells > 64
                    || tileSizeCells + haloCells * 2 > 256) {
                throw new IllegalArgumentException(
                        "watershed halo must be in [2, 64] with an extended grid at most 256"
                );
            }
            finiteNonNegative("base runoff", baseRunoff);
            finitePositive("rainfall scale", rainfallScale);
            if (!Double.isFinite(permeabilityInfluence)
                    || permeabilityInfluence < 0.0D
                    || permeabilityInfluence > 1.0D) {
                throw new IllegalArgumentException(
                        "permeability influence must be finite and in [0, 1]"
                );
            }
            finitePositive("stream discharge", streamDischarge);
            finitePositive("river discharge", riverDischarge);
            if (riverDischarge <= streamDischarge) {
                throw new IllegalArgumentException(
                        "river discharge must exceed stream discharge"
                );
            }
            finitePositive("stream half width", streamHalfWidthBlocks);
            finitePositive("river half width", riverHalfWidthBlocks);
            if (riverHalfWidthBlocks < streamHalfWidthBlocks) {
                throw new IllegalArgumentException(
                        "river half width must not be narrower than stream half width"
                );
            }
            finitePositive("stream depth", streamDepthBlocks);
            finitePositive("river depth", riverDepthBlocks);
            finitePositive("bank blend", bankBlendBlocks);
            finitePositive("lake minimum fill depth", lakeMinimumFillDepth);
        }

        public int channelSearchRadiusCells() {
            return Math.max(
                    1,
                    (int) Math.ceil(
                            (riverHalfWidthBlocks + bankBlendBlocks) / cellSizeBlocks
                    ) + 1
            );
        }

        private int windowCellCapacity() {
            int maximumChunkCells = (15 + cellSizeBlocks - 1) / cellSizeBlocks + 1;
            int diameter = channelSearchRadiusCells() * 2 + maximumChunkCells;
            return diameter * diameter;
        }

        private int windowTileCapacity() {
            int maximumChunkCells = (15 + cellSizeBlocks - 1) / cellSizeBlocks + 1;
            int diameter = channelSearchRadiusCells() * 2 + maximumChunkCells;
            int tileSpan = (diameter + tileSizeCells - 1) / tileSizeCells + 2;
            return tileSpan * tileSpan;
        }

        private static void requirePowerOfTwo(
                String label,
                int value,
                int minimum,
                int maximum
        ) {
            if (value < minimum || value > maximum || (value & (value - 1)) != 0) {
                throw new IllegalArgumentException(
                        label + " must be a power of two in [" + minimum + ", " + maximum + "]"
                );
            }
        }

        private static void finitePositive(String label, double value) {
            if (!Double.isFinite(value) || value <= 0.0D) {
                throw new IllegalArgumentException(label + " must be finite and positive");
            }
        }

        private static void finiteNonNegative(String label, double value) {
            if (!Double.isFinite(value) || value < 0.0D) {
                throw new IllegalArgumentException(label + " must be finite and non-negative");
            }
        }
    }

    @FunctionalInterface
    public interface CellSampler {

        void sample(int blockX, int blockZ, CellData output);
    }

    /** Reused mutable handoff from the terrain fields into a tile build. */
    public static final class CellData {

        private double elevation;
        private double rainfall;
        private double permeability;
        private double erodibility;

        public void set(
                double elevation,
                double rainfall,
                double permeability,
                double erodibility
        ) {
            if (!Double.isFinite(elevation)
                    || !Double.isFinite(rainfall)
                    || !Double.isFinite(permeability)
                    || !Double.isFinite(erodibility)) {
                throw new IllegalArgumentException("watershed cell values must be finite");
            }
            this.elevation = elevation;
            this.rainfall = clamp01(rainfall);
            this.permeability = clamp01(permeability);
            this.erodibility = clamp01(erodibility);
        }
    }

    /** Reused block-space result; no object is created for individual columns. */
    public static final class MutableHydrologySample {

        private long cellX;
        private long cellZ;
        private long tileX;
        private long tileZ;
        private double riverDistance = -1.0D;
        private double streamDistance = -1.0D;
        private double riverStrength;
        private double streamStrength;
        private double discharge;
        private double runoff;
        private double drainageElevation;
        private double fillDepth;
        private double erodibility;
        private int streamOrder;
        private int flowDirection = OUTLET;

        public long cellX() {
            return cellX;
        }

        public long cellZ() {
            return cellZ;
        }

        public long tileX() {
            return tileX;
        }

        public long tileZ() {
            return tileZ;
        }

        public double riverDistance() {
            return riverDistance;
        }

        public double streamDistance() {
            return streamDistance;
        }

        public double riverStrength() {
            return riverStrength;
        }

        public double streamStrength() {
            return streamStrength;
        }

        public double discharge() {
            return discharge;
        }

        public double runoff() {
            return runoff;
        }

        public double drainageElevation() {
            return drainageElevation;
        }

        public double fillDepth() {
            return fillDepth;
        }

        public double erodibility() {
            return erodibility;
        }

        public int streamOrder() {
            return streamOrder;
        }

        public int flowDirection() {
            return flowDirection;
        }

        public boolean filledDepression(double minimumDepth) {
            return fillDepth >= minimumDepth;
        }
    }

    /** Compact block-space result consumed by one Minecraft chunk sample. */
    public static final class ChunkHydrology {

        private final int chunkX;
        private final int chunkZ;
        private final int cellSize;
        private final int tileSize;
        private final float[] riverDistance = new float[COLUMN_COUNT];
        private final float[] streamDistance = new float[COLUMN_COUNT];
        private final float[] riverStrength = new float[COLUMN_COUNT];
        private final float[] streamStrength = new float[COLUMN_COUNT];
        private final float[] discharge = new float[COLUMN_COUNT];
        private final float[] runoff = new float[COLUMN_COUNT];
        private final float[] drainageElevation = new float[COLUMN_COUNT];
        private final float[] fillDepth = new float[COLUMN_COUNT];
        private final float[] erodibility = new float[COLUMN_COUNT];
        private final byte[] streamOrder = new byte[COLUMN_COUNT];
        private final byte[] flowDirection = new byte[COLUMN_COUNT];
        private long centerCellX;
        private long centerCellZ;
        private long centerTileX;
        private long centerTileZ;

        private ChunkHydrology(int chunkX, int chunkZ, int cellSize, int tileSize) {
            this.chunkX = chunkX;
            this.chunkZ = chunkZ;
            this.cellSize = cellSize;
            this.tileSize = tileSize;
            Arrays.fill(riverDistance, -1.0F);
            Arrays.fill(streamDistance, -1.0F);
            Arrays.fill(flowDirection, OUTLET);
        }

        public void sample(int localX, int localZ, MutableHydrologySample output) {
            int index = index(localX, localZ);
            output.cellX = Math.floorDiv((chunkX << 4) + localX, cellSize);
            output.cellZ = Math.floorDiv((chunkZ << 4) + localZ, cellSize);
            output.tileX = Math.floorDiv(output.cellX, tileSize);
            output.tileZ = Math.floorDiv(output.cellZ, tileSize);
            output.riverDistance = riverDistance[index];
            output.streamDistance = streamDistance[index];
            output.riverStrength = riverStrength[index];
            output.streamStrength = streamStrength[index];
            output.discharge = discharge[index];
            output.runoff = runoff[index];
            output.drainageElevation = drainageElevation[index];
            output.fillDepth = fillDepth[index];
            output.erodibility = erodibility[index];
            output.streamOrder = Byte.toUnsignedInt(streamOrder[index]);
            output.flowDirection = Byte.toUnsignedInt(flowDirection[index]);
            if (localX == 8 && localZ == 8) {
                output.cellX = centerCellX;
                output.cellZ = centerCellZ;
                output.tileX = centerTileX;
                output.tileZ = centerTileZ;
            }
        }

        public double discharge(int localX, int localZ) {
            return discharge[index(localX, localZ)];
        }

        public double fillDepth(int localX, int localZ) {
            return fillDepth[index(localX, localZ)];
        }

        public long fingerprint() {
            long hash = FINGERPRINT_OFFSET;
            for (int index = 0; index < COLUMN_COUNT; index++) {
                hash = fingerprint(hash, Float.floatToIntBits(riverStrength[index]));
                hash = fingerprint(hash, Float.floatToIntBits(streamStrength[index]));
                hash = fingerprint(hash, Float.floatToIntBits(discharge[index]));
                hash = fingerprint(hash, Float.floatToIntBits(fillDepth[index]));
                hash = fingerprint(hash, Byte.toUnsignedInt(streamOrder[index]));
                hash = fingerprint(hash, Byte.toUnsignedInt(flowDirection[index]));
            }
            return hash;
        }

        private void set(int localX, int localZ, MutableHydrologySample sample) {
            int index = index(localX, localZ);
            riverDistance[index] = (float) sample.riverDistance;
            streamDistance[index] = (float) sample.streamDistance;
            riverStrength[index] = (float) sample.riverStrength;
            streamStrength[index] = (float) sample.streamStrength;
            discharge[index] = (float) sample.discharge;
            runoff[index] = (float) sample.runoff;
            drainageElevation[index] = (float) sample.drainageElevation;
            fillDepth[index] = (float) sample.fillDepth;
            erodibility[index] = (float) sample.erodibility;
            streamOrder[index] = (byte) sample.streamOrder;
            flowDirection[index] = (byte) sample.flowDirection;
            if (localX == 8 && localZ == 8) {
                centerCellX = sample.cellX;
                centerCellZ = sample.cellZ;
                centerTileX = sample.tileX;
                centerTileZ = sample.tileZ;
            }
        }

        private static int index(int localX, int localZ) {
            return localX + localZ * 16;
        }

        private static long fingerprint(long hash, int value) {
            hash ^= value & 0xffffffffL;
            return hash * FINGERPRINT_PRIME;
        }
    }

    private final class SegmentWindow {

        private final long[] cellX;
        private final long[] cellZ;
        private final float[] cellFilledElevation;
        private final float[] cellRunoff;
        private final float[] cellDischarge;
        private final float[] cellFillDepth;
        private final float[] cellErodibility;
        private final byte[] cellStreamOrder;
        private final byte[] cellDirection;
        private final double[] segmentX1;
        private final double[] segmentZ1;
        private final double[] segmentX2;
        private final double[] segmentZ2;
        private final float[] segmentStartElevation;
        private final float[] segmentEndElevation;
        private final float[] segmentDischarge;
        private final float[] segmentErodibility;
        private final byte[] segmentStreamOrder;
        private final byte[] segmentDirection;
        private final long[] resolvedTileX;
        private final long[] resolvedTileZ;
        private final WatershedTile[] resolvedTiles;
        private int cellCount;
        private int segmentCount;
        private int resolvedTileCount;

        private SegmentWindow(int capacity, int tileCapacity) {
            cellX = new long[capacity];
            cellZ = new long[capacity];
            cellFilledElevation = new float[capacity];
            cellRunoff = new float[capacity];
            cellDischarge = new float[capacity];
            cellFillDepth = new float[capacity];
            cellErodibility = new float[capacity];
            cellStreamOrder = new byte[capacity];
            cellDirection = new byte[capacity];
            segmentX1 = new double[capacity];
            segmentZ1 = new double[capacity];
            segmentX2 = new double[capacity];
            segmentZ2 = new double[capacity];
            segmentStartElevation = new float[capacity];
            segmentEndElevation = new float[capacity];
            segmentDischarge = new float[capacity];
            segmentErodibility = new float[capacity];
            segmentStreamOrder = new byte[capacity];
            segmentDirection = new byte[capacity];
            resolvedTileX = new long[tileCapacity];
            resolvedTileZ = new long[tileCapacity];
            resolvedTiles = new WatershedTile[tileCapacity];
        }

        private void reset(long minX, long minZ, long maxX, long maxZ) {
            int required = Math.toIntExact((maxX - minX + 1L) * (maxZ - minZ + 1L));
            if (required > cellX.length) {
                throw new IllegalStateException("hydrology window capacity was under-sized");
            }
            cellCount = 0;
            segmentCount = 0;
            resolvedTileCount = 0;
        }

        private void readCell(long x, long z, CellCursor output) {
            int tileSize = configuration.tileSizeCells();
            long tileX = Math.floorDiv(x, tileSize);
            long tileZ = Math.floorDiv(z, tileSize);
            WatershedTile resolved = null;
            for (int index = 0; index < resolvedTileCount; index++) {
                if (resolvedTileX[index] == tileX && resolvedTileZ[index] == tileZ) {
                    resolved = resolvedTiles[index];
                    break;
                }
            }
            if (resolved == null) {
                if (resolvedTileCount >= resolvedTiles.length) {
                    throw new IllegalStateException(
                            "hydrology tile lookaside capacity was under-sized"
                    );
                }
                resolved = tile(tileX, tileZ);
                resolvedTileX[resolvedTileCount] = tileX;
                resolvedTileZ[resolvedTileCount] = tileZ;
                resolvedTiles[resolvedTileCount++] = resolved;
            }
            resolved.read(
                    (int) Math.floorMod(x, tileSize),
                    (int) Math.floorMod(z, tileSize),
                    output
            );
        }

        private void addCell(long x, long z, CellCursor cell) {
            int index = cellCount++;
            cellX[index] = x;
            cellZ[index] = z;
            cellFilledElevation[index] = cell.filledElevation;
            cellRunoff[index] = cell.runoff;
            cellDischarge[index] = cell.discharge;
            cellFillDepth[index] = cell.fillDepth;
            cellErodibility[index] = cell.erodibility;
            cellStreamOrder[index] = cell.streamOrder;
            cellDirection[index] = cell.direction;
        }

        private void addSegment(
                long upstreamX,
                long upstreamZ,
                CellCursor upstream,
                long downstreamX,
                long downstreamZ,
                CellCursor downstream
        ) {
            int index = segmentCount++;
            double offset = configuration.cellSizeBlocks() * 0.5D;
            segmentX1[index] = upstreamX * configuration.cellSizeBlocks() + offset;
            segmentZ1[index] = upstreamZ * configuration.cellSizeBlocks() + offset;
            segmentX2[index] = downstreamX * configuration.cellSizeBlocks() + offset;
            segmentZ2[index] = downstreamZ * configuration.cellSizeBlocks() + offset;
            segmentStartElevation[index] = upstream.filledElevation;
            segmentEndElevation[index] = downstream.filledElevation;
            segmentDischarge[index] = upstream.discharge;
            segmentErodibility[index] = upstream.erodibility;
            segmentStreamOrder[index] = upstream.streamOrder;
            segmentDirection[index] = upstream.direction;
        }

        private void sample(int blockX, int blockZ, MutableHydrologySample output) {
            long targetCellX = Math.floorDiv(blockX, configuration.cellSizeBlocks());
            long targetCellZ = Math.floorDiv(blockZ, configuration.cellSizeBlocks());
            int cellIndex = findCell(targetCellX, targetCellZ);
            if (cellIndex < 0) {
                throw new IllegalStateException("hydrology window omitted its target cell");
            }
            output.cellX = targetCellX;
            output.cellZ = targetCellZ;
            output.tileX = Math.floorDiv(targetCellX, configuration.tileSizeCells());
            output.tileZ = Math.floorDiv(targetCellZ, configuration.tileSizeCells());
            output.riverDistance = -1.0D;
            output.streamDistance = -1.0D;
            output.riverStrength = 0.0D;
            output.streamStrength = 0.0D;
            output.discharge = cellDischarge[cellIndex];
            output.runoff = cellRunoff[cellIndex];
            output.drainageElevation = cellFilledElevation[cellIndex];
            output.fillDepth = cellFillDepth[cellIndex];
            output.erodibility = cellErodibility[cellIndex];
            output.streamOrder = Byte.toUnsignedInt(cellStreamOrder[cellIndex]);
            output.flowDirection = Byte.toUnsignedInt(cellDirection[cellIndex]);

            double strongestChannel = 0.0D;
            for (int segment = 0; segment < segmentCount; segment++) {
                double x1 = segmentX1[segment];
                double z1 = segmentZ1[segment];
                double dx = segmentX2[segment] - x1;
                double dz = segmentZ2[segment] - z1;
                double lengthSquared = dx * dx + dz * dz;
                double projection = lengthSquared == 0.0D
                        ? 0.0D
                        : clamp01(((blockX - x1) * dx + (blockZ - z1) * dz)
                        / lengthSquared);
                double nearestX = x1 + dx * projection;
                double nearestZ = z1 + dz * projection;
                double distanceX = blockX - nearestX;
                double distanceZ = blockZ - nearestZ;
                double distance = StrictMath.sqrt(
                        distanceX * distanceX + distanceZ * distanceZ
                );
                double localDischarge = segmentDischarge[segment];
                boolean river = localDischarge >= configuration.riverDischarge();
                double halfWidth = river
                        ? configuration.riverHalfWidthBlocks()
                        : configuration.streamHalfWidthBlocks();
                double strength = 1.0D - smoothstep(
                        halfWidth,
                        halfWidth + configuration.bankBlendBlocks(),
                        distance
                );
                if (river) {
                    if (output.riverDistance < 0.0D || distance < output.riverDistance) {
                        output.riverDistance = distance;
                    }
                    output.riverStrength = Math.max(output.riverStrength, strength);
                } else {
                    if (output.streamDistance < 0.0D || distance < output.streamDistance) {
                        output.streamDistance = distance;
                    }
                    output.streamStrength = Math.max(output.streamStrength, strength);
                }
                if (strength > strongestChannel) {
                    strongestChannel = strength;
                    output.discharge = localDischarge;
                    output.drainageElevation = lerp(
                            segmentStartElevation[segment],
                            segmentEndElevation[segment],
                            projection
                    );
                    output.erodibility = segmentErodibility[segment];
                    output.streamOrder = Byte.toUnsignedInt(segmentStreamOrder[segment]);
                    output.flowDirection = Byte.toUnsignedInt(segmentDirection[segment]);
                }
            }
            output.streamStrength *= 1.0D - output.riverStrength;
        }

        private int findCell(long x, long z) {
            for (int index = 0; index < cellCount; index++) {
                if (cellX[index] == x && cellZ[index] == z) {
                    return index;
                }
            }
            return -1;
        }
    }

    /** Immutable primitive-array core tile. */
    public static final class WatershedTile {

        private final long tileX;
        private final long tileZ;
        private final int size;
        private final int computedGridCells;
        private final float[] rawElevation;
        private final float[] filledElevation;
        private final float[] runoff;
        private final float[] discharge;
        private final float[] fillDepth;
        private final float[] erodibility;
        private final byte[] streamOrder;
        private final byte[] direction;
        private int filledCellCount;
        private float maximumFillDepth;
        private float maximumDischarge;

        private WatershedTile(long tileX, long tileZ, int size, int computedGridCells) {
            this.tileX = tileX;
            this.tileZ = tileZ;
            this.size = size;
            this.computedGridCells = computedGridCells;
            int cells = size * size;
            rawElevation = new float[cells];
            filledElevation = new float[cells];
            runoff = new float[cells];
            discharge = new float[cells];
            fillDepth = new float[cells];
            erodibility = new float[cells];
            streamOrder = new byte[cells];
            direction = new byte[cells];
            Arrays.fill(direction, OUTLET);
        }

        public long tileX() {
            return tileX;
        }

        public long tileZ() {
            return tileZ;
        }

        public int computedGridCells() {
            return computedGridCells;
        }

        public int filledCellCount() {
            return filledCellCount;
        }

        public float maximumFillDepth() {
            return maximumFillDepth;
        }

        public float maximumDischarge() {
            return maximumDischarge;
        }

        private void read(int localX, int localZ, CellCursor output) {
            int index = localX + localZ * size;
            output.rawElevation = rawElevation[index];
            output.filledElevation = filledElevation[index];
            output.runoff = runoff[index];
            output.discharge = discharge[index];
            output.fillDepth = fillDepth[index];
            output.erodibility = erodibility[index];
            output.streamOrder = streamOrder[index];
            output.direction = direction[index];
        }
    }

    private static final class CellCursor {

        private float rawElevation;
        private float filledElevation;
        private float runoff;
        private float discharge;
        private float fillDepth;
        private float erodibility;
        private byte streamOrder;
        private byte direction;
    }

    /** Primitive min-heap avoids one allocation per Priority-Flood frontier cell. */
    private static final class PrimitiveCellHeap {

        private final int[] heap;
        private final float[] priority;
        private final long originX;
        private final long originZ;
        private final int gridSize;
        private final long seed;
        private int size;

        private PrimitiveCellHeap(
                int capacity,
                float[] priority,
                long originX,
                long originZ,
                int gridSize,
                long seed
        ) {
            heap = new int[capacity];
            this.priority = priority;
            this.originX = originX;
            this.originZ = originZ;
            this.gridSize = gridSize;
            this.seed = seed;
        }

        private void add(int value) {
            int cursor = size++;
            while (cursor > 0) {
                int parent = (cursor - 1) >>> 1;
                if (!less(value, heap[parent])) {
                    break;
                }
                heap[cursor] = heap[parent];
                cursor = parent;
            }
            heap[cursor] = value;
        }

        private int removeFirst() {
            int first = heap[0];
            int replacement = heap[--size];
            if (size == 0) {
                return first;
            }
            int cursor = 0;
            int half = size >>> 1;
            while (cursor < half) {
                int child = (cursor << 1) + 1;
                int right = child + 1;
                if (right < size && less(heap[right], heap[child])) {
                    child = right;
                }
                if (!less(heap[child], replacement)) {
                    break;
                }
                heap[cursor] = heap[child];
                cursor = child;
            }
            heap[cursor] = replacement;
            return first;
        }

        private boolean isEmpty() {
            return size == 0;
        }

        private boolean less(int left, int right) {
            int elevation = Float.compare(priority[left], priority[right]);
            if (elevation != 0) {
                return elevation < 0;
            }
            return Long.compareUnsigned(tieBreak(left), tieBreak(right)) < 0;
        }

        private long tieBreak(int index) {
            long x = originX + index % gridSize;
            long z = originZ + index / gridSize;
            return mix64(seed ^ coordinateKey(x, z));
        }
    }

    private static double smoothstep(double edge0, double edge1, double value) {
        if (edge0 == edge1) {
            return value < edge0 ? 0.0D : 1.0D;
        }
        double normalized = clamp01((value - edge0) / (edge1 - edge0));
        return normalized * normalized * (3.0D - 2.0D * normalized);
    }

    private static double clamp01(double value) {
        return Math.max(0.0D, Math.min(1.0D, value));
    }

    private static double lerp(double left, double right, double amount) {
        return left + (right - left) * amount;
    }
}
