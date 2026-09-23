package dev.workbench.worldgenobservatory.fixture;

import com.cleanroommc.discovery.CleanroomModDiscoverer;
import dev.workbench.worldgenobservatory.evidence.SourceArtifactDigest;
import dev.workbench.worldgenobservatory.probe.ProbeRuntime;
import dev.workbench.worldgenobservatory.trace.ChunkCheckpoint;
import dev.workbench.worldgenobservatory.world.ObservingChunkGenerator;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.dedicated.DedicatedServer;
import net.minecraft.util.math.ChunkPos;
import net.minecraft.world.WorldServer;
import net.minecraft.world.chunk.Chunk;
import net.minecraft.world.gen.ChunkProviderServer;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.ModContainer;
import net.minecraftforge.fml.common.eventhandler.EventBus;

/**
 * Opt-in, non-interactive exact-candidate route. It requests chunks through
 * ChunkProviderServer and asks each returned Chunk to run its normal populate
 * eligibility logic; it never invokes a decorator or posts an event itself.
 */
public final class DedicatedServerFixtureDriver {

    public static final String ENABLE_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.enabled";
    public static final String ORDER_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.order";
    public static final String EXPECTED_SEED_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.expected_seed";
    public static final String RESULT_PATH_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.result";
    public static final String ROUTE_ANCHOR_X_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.route_anchor_x";
    public static final String ROUTE_ANCHOR_Z_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.route_anchor_z";
    public static final String ROUTE_SIZE_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.route_size";

    private static final int DIMENSION = 0;
    private DedicatedServerFixtureDriver() {
    }

    public static boolean enabled() {
        return Boolean.parseBoolean(System.getProperty(ENABLE_PROPERTY, "false"));
    }

    public static void run(MinecraftServer server) {
        if (!enabled()) {
            return;
        }

        String routeOrder = "unresolved";
        RouteSelection selection = RouteSelection.fromProperties();
        List<ChunkPos> route = new ArrayList<>();
        List<String> selectedChunks = new ArrayList<>();
        String selectorSha256 = sha256(routeMaterial(selection.forwardRoute));
        String routeSha256 = sha256("unresolved");
        boolean captureArmed = false;
        boolean shutdownRequested = false;

        try {
            routeOrder = routeOrder();
            route.addAll(selection.forwardRoute);
            if ("reverse".equals(routeOrder)) {
                Collections.reverse(route);
            }
            selectedChunks = labels(route);
            routeSha256 = sha256(routeMaterial(route));
            if (!(server instanceof DedicatedServer)) {
                throw new IllegalStateException("Fixture driver requires a physical dedicated server");
            }

            long expectedSeed = expectedSeed();
            WorldServer world = server.getWorld(DIMENSION);
            if (world == null) {
                throw new IllegalStateException("Fixture Overworld is unavailable");
            }
            if (world.getSeed() != expectedSeed) {
                throw new IllegalStateException(
                    "Fixture seed mismatch: expected configured seed but loaded world differs"
                );
            }

            ChunkProviderServer provider = world.getChunkProvider();
            if (!(provider.chunkGenerator instanceof ObservingChunkGenerator)) {
                throw new IllegalStateException(
                    "Fixture requires level-type=wb_observe and its cooperative wrapper"
                );
            }
            if (ProbeRuntime.requested() && !ProbeRuntime.transportEnabled()) {
                throw new IllegalStateException("Raw probe was requested but its writer is unavailable");
            }
            if (ProbeRuntime.transportEnabled()) {
                captureArmed = ProbeRuntime.armForFixtureDriver(
                    expectedSeed,
                    DIMENSION,
                    routeOrder,
                    selectorSha256,
                    routeSha256,
                    selectedChunks
                );
                if (!captureArmed) {
                    throw new IllegalStateException("Raw probe could not arm at the fixture boundary");
                }
                ProbeRuntime.fixtureDriverStarted(
                    expectedSeed,
                    DIMENSION,
                    routeOrder,
                    routeSha256,
                    selectedChunks
                );
            }

            List<Chunk> requestedChunks = new ArrayList<>(route.size());
            for (ChunkPos position : route) {
                Chunk chunk = provider.provideChunk(position.x, position.z);
                if (chunk.x != position.x || chunk.z != position.z) {
                    throw new IllegalStateException("Chunk provider returned the wrong coordinate");
                }
                requestedChunks.add(chunk);
            }
            for (Chunk chunk : requestedChunks) {
                chunk.populate(provider, provider.chunkGenerator);
            }

            server.saveAllWorlds(true);
            world.flush();

            List<ChunkResult> results = new ArrayList<>(route.size());
            for (int index = 0; index < route.size(); index++) {
                ChunkPos position = route.get(index);
                Chunk chunk = requestedChunks.get(index);
                results.add(new ChunkResult(
                    position.x,
                    position.z,
                    ChunkCheckpoint.blockStateSha256(chunk)
                ));
            }

            server.initiateShutdown();
            shutdownRequested = true;
            String resultJson = resultJson(
                expectedSeed,
                routeOrder,
                selection,
                selectorSha256,
                routeSha256,
                results,
                runtimeModInventory()
            );
            writeResult(resultJson);

            if (captureArmed) {
                boolean completionRecorded = ProbeRuntime.fixtureDriverCompleted(
                    expectedSeed,
                    DIMENSION,
                    routeOrder,
                    routeSha256,
                    sha256(resultJson),
                    selectedChunks
                );
                if (!completionRecorded) {
                    throw new IllegalStateException("Raw fixture completion marker was not written");
                }
                boolean stopped = ProbeRuntime.stopFixtureCapture(
                    DIMENSION,
                    routeOrder,
                    routeSha256,
                    true
                );
                captureArmed = false;
                if (!stopped) {
                    throw new IllegalStateException("Raw fixture capture did not close cleanly");
                }
            }
        } catch (Throwable originalFailure) {
            if (captureArmed) {
                ProbeRuntime.fixtureDriverFailed(
                    DIMENSION,
                    routeOrder,
                    routeSha256,
                    originalFailure
                );
                ProbeRuntime.stopFixtureCapture(DIMENSION, routeOrder, routeSha256, false);
                captureArmed = false;
            }
            if (!shutdownRequested) {
                try {
                    server.initiateShutdown();
                } catch (Throwable shutdownFailure) {
                    originalFailure.addSuppressed(shutdownFailure);
                }
            }
            ProbeRuntime.sneakyThrow(originalFailure);
        }
    }

    private static String routeOrder() {
        String value = System.getProperty(ORDER_PROPERTY, "forward").trim().toLowerCase(Locale.ROOT);
        if (!"forward".equals(value) && !"reverse".equals(value)) {
            throw new IllegalArgumentException("Fixture order must be forward or reverse");
        }
        return value;
    }

    private static long expectedSeed() {
        String value = System.getProperty(EXPECTED_SEED_PROPERTY);
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalArgumentException("Fixture expected_seed JVM property is required");
        }
        return Long.parseLong(value.trim());
    }

    private static List<String> labels(List<ChunkPos> positions) {
        List<String> result = new ArrayList<>(positions.size());
        for (ChunkPos position : positions) {
            result.add(position.x + "," + position.z);
        }
        return result;
    }

    private static String routeMaterial(List<ChunkPos> positions) {
        return String.join(";", labels(positions));
    }

    private static String resultJson(
        long worldSeed,
        String routeOrder,
        RouteSelection selection,
        String selectorSha256,
        String routeSha256,
        List<ChunkResult> results,
        List<ModInventoryEntry> modInventory
    ) {
        StringBuilder json = new StringBuilder(1400);
        json.append('{');
        field(json, "schema", selection.versioned
            ? "workbench.worldgen-observatory.fixture-result.v2"
            : "workbench.worldgen-observatory.fixture-result.v1").append(',');
        field(json, "fixture", selection.versioned
            ? "dedicated_server_rectangular_region_v2"
            : "dedicated_server_fixed_region_v1").append(',');
        field(json, "completion_state", "complete").append(',');
        field(json, "save_state", "flushed").append(',');
        field(json, "shutdown_state", "requested").append(',');
        field(json, "world_seed_sha256", sha256(Long.toString(worldSeed))).append(',');
        json.append("\"dimension\":").append(DIMENSION).append(',');
        field(json, "route_order", routeOrder).append(',');
        if (selection.versioned) {
            json.append("\"selection\":{")
                .append("\"anchor_chunk_x\":").append(selection.anchorX).append(',')
                .append("\"anchor_chunk_z\":").append(selection.anchorZ).append(',')
                .append("\"route_size\":").append(selection.size)
                .append("},");
        }
        field(json, "selector_sha256", selectorSha256).append(',');
        field(json, "route_sha256", routeSha256).append(',');
        json.append("\"selected_chunks\":[");
        for (int index = 0; index < results.size(); index++) {
            if (index > 0) {
                json.append(',');
            }
            ChunkResult result = results.get(index);
            json.append('{')
                .append("\"chunk_x\":").append(result.chunkX).append(',')
                .append("\"chunk_z\":").append(result.chunkZ).append(',');
            field(json, "semantic_state_sha256", result.semanticStateSha256);
            json.append('}');
        }
        json.append("],\"runtime_mod_inventory\":[");
        for (int index = 0; index < modInventory.size(); index++) {
            if (index > 0) {
                json.append(',');
            }
            ModInventoryEntry entry = modInventory.get(index);
            json.append('{');
            field(json, "mod_id", entry.modId).append(',');
            field(json, "source_sha256", entry.sourceSha256).append(',');
            field(json, "mod_class_name", entry.modClassName);
            json.append('}');
        }
        return json.append("]}\n").toString();
    }

    private static List<ModInventoryEntry> runtimeModInventory() {
        List<ModInventoryEntry> result = new ArrayList<>();
        for (ModContainer container : Loader.instance().getModList()) {
            String modClassName = "unavailable";
            try {
                Object instance = container.getMod();
                if (instance != null) {
                    modClassName = instance.getClass().getName();
                }
            } catch (Throwable ignored) {
                // Explicit unavailable value is retained.
            }
            result.add(new ModInventoryEntry(
                container.getModId(),
                modSourceSha256(container),
                modClassName
            ));
        }
        result.sort(java.util.Comparator
            .comparing((ModInventoryEntry entry) -> entry.modId)
            .thenComparing(entry -> entry.modClassName)
            .thenComparing(entry -> entry.sourceSha256));
        return result;
    }

    private static void writeResult(String resultJson) throws Exception {
        Path output = Paths.get(System.getProperty(
            RESULT_PATH_PROPERTY,
            "logs/worldgen-observatory.fixture-result.json"
        ));
        Path parent = output.toAbsolutePath().normalize().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Files.write(
            output,
            resultJson.getBytes(StandardCharsets.UTF_8),
            StandardOpenOption.CREATE,
            StandardOpenOption.WRITE,
            StandardOpenOption.TRUNCATE_EXISTING
        );
    }

    private static String sha256(String value) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256")
                .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte element : bytes) {
                result.append(Character.forDigit((element >>> 4) & 0x0F, 16));
                result.append(Character.forDigit(element & 0x0F, 16));
            }
            return result.toString();
        } catch (Exception failure) {
            throw new IllegalStateException("SHA-256 unavailable", failure);
        }
    }

    private static String modSourceSha256(ModContainer container) {
        try {
            String platformDigest = null;
            if ("minecraft".equals(container.getModId())) {
                platformDigest = SourceArtifactDigest.sha256ClassSource(Chunk.class);
            } else if ("forge".equals(container.getModId())) {
                platformDigest = SourceArtifactDigest.sha256ClassSource(EventBus.class);
            }
            if (platformDigest != null) {
                return platformDigest;
            }
            File source = container.getSource();
            String digest = source == null ? null : SourceArtifactDigest.sha256(source.toPath());
            if (digest == null) {
                CleanroomModDiscoverer discoverer = CleanroomModDiscoverer.instance();
                Set<File> discoveredSources = new LinkedHashSet<>();
                for (String discoveredModId : discoverer.presentMods()) {
                    if (discoveredModId.equalsIgnoreCase(container.getModId())) {
                        discoveredSources.addAll(discoverer.modSources(discoveredModId));
                    }
                }
                if (discoveredSources.size() == 1) {
                    digest = SourceArtifactDigest.sha256(
                        discoveredSources.iterator().next().toPath()
                    );
                }
            }
            return digest == null ? "unavailable" : digest;
        } catch (Exception failure) {
            return "unavailable";
        }
    }

    private static StringBuilder field(StringBuilder json, String name, String value) {
        quote(json, name).append(':');
        return quote(json, value);
    }

    private static StringBuilder quote(StringBuilder json, String value) {
        json.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"':
                    json.append("\\\"");
                    break;
                case '\\':
                    json.append("\\\\");
                    break;
                case '\n':
                    json.append("\\n");
                    break;
                case '\r':
                    json.append("\\r");
                    break;
                case '\t':
                    json.append("\\t");
                    break;
                default:
                    if (character < 0x20) {
                        json.append(String.format(Locale.ROOT, "\\u%04x", (int) character));
                    } else {
                        json.append(character);
                    }
            }
        }
        return json.append('"');
    }

    private static final class ChunkResult {
        private final int chunkX;
        private final int chunkZ;
        private final String semanticStateSha256;

        private ChunkResult(int chunkX, int chunkZ, String semanticStateSha256) {
            this.chunkX = chunkX;
            this.chunkZ = chunkZ;
            this.semanticStateSha256 = semanticStateSha256;
        }
    }

    private static final class RouteSelection {
        private static final int MIN_SIZE = 2;
        private static final int MAX_SIZE = 32;

        private final boolean versioned;
        private final int anchorX;
        private final int anchorZ;
        private final int size;
        private final List<ChunkPos> forwardRoute;

        private RouteSelection(boolean versioned, int anchorX, int anchorZ, int size) {
            this.versioned = versioned;
            this.anchorX = anchorX;
            this.anchorZ = anchorZ;
            this.size = size;
            List<ChunkPos> positions = new ArrayList<>(size * size);
            for (int zOffset = 0; zOffset < size; zOffset++) {
                for (int xOffset = 0; xOffset < size; xOffset++) {
                    positions.add(new ChunkPos(anchorX + xOffset, anchorZ + zOffset));
                }
            }
            this.forwardRoute = Collections.unmodifiableList(positions);
        }

        private static RouteSelection fromProperties() {
            String anchorX = System.getProperty(ROUTE_ANCHOR_X_PROPERTY);
            String anchorZ = System.getProperty(ROUTE_ANCHOR_Z_PROPERTY);
            String size = System.getProperty(ROUTE_SIZE_PROPERTY);
            boolean anyConfigured = anchorX != null || anchorZ != null || size != null;
            if (!anyConfigured) {
                return new RouteSelection(false, 64, 64, 2);
            }
            if (anchorX == null || anchorZ == null || size == null) {
                throw new IllegalArgumentException(
                    "Versioned fixture route requires route_anchor_x, route_anchor_z, and route_size"
                );
            }
            int parsedSize = Integer.parseInt(size.trim());
            if (parsedSize < MIN_SIZE || parsedSize > MAX_SIZE) {
                throw new IllegalArgumentException(
                    "Fixture route_size must be between " + MIN_SIZE + " and " + MAX_SIZE
                );
            }
            int parsedAnchorX = Integer.parseInt(anchorX.trim());
            int parsedAnchorZ = Integer.parseInt(anchorZ.trim());
            if (parsedAnchorX > Integer.MAX_VALUE - parsedSize
                    || parsedAnchorZ > Integer.MAX_VALUE - parsedSize
                    || parsedAnchorX < Integer.MIN_VALUE + parsedSize
                    || parsedAnchorZ < Integer.MIN_VALUE + parsedSize) {
                throw new IllegalArgumentException("Fixture route exceeds integer chunk coordinates");
            }
            return new RouteSelection(true, parsedAnchorX, parsedAnchorZ, parsedSize);
        }
    }

    private static final class ModInventoryEntry {
        private final String modId;
        private final String sourceSha256;
        private final String modClassName;

        private ModInventoryEntry(String modId, String sourceSha256, String modClassName) {
            this.modId = modId;
            this.sourceSha256 = sourceSha256;
            this.modClassName = modClassName;
        }
    }
}
