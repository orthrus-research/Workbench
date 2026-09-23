package dev.workbench.crucible.runtimegraph.adapter;

import com.cleanroommc.discovery.CleanroomModDiscoverer;
import com.cleanroommc.discovery.DiscoveredMod;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.Hashing;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;

import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.ModContainer;
import net.minecraftforge.fml.common.versioning.ArtifactVersion;
import net.minecraftforge.fml.relauncher.CoreModManager;

import java.io.File;
import java.io.IOException;
import java.lang.reflect.Field;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.jar.Attributes;

/** Exact active FML containers and independently classified runtime artifacts. */
public final class FmlModContainerAdapter implements CaptureAdapter {
    private static final Field DISCOVERED_FILES = ReflectionAccess.requireAssignableField(
        CleanroomModDiscoverer.class,
        "discoveredFiles",
        Map.class
    );

    @Override public String adapterId() { return "fml-mod-containers"; }
    @Override public String categoryId() { return "runtime-mods"; }
    @Override public String checkpointId() { return CaptureCoordinator.SERVER_STARTED; }

    @Override
    public AdapterSnapshot snapshot() {
        List<ModContainer> containers = activeContainers();
        List<JsonObject> records = new ArrayList<JsonObject>();
        List<String> diagnostics = new ArrayList<String>();
        Map<String, ArtifactFacts> artifacts = new HashMap<String, ArtifactFacts>();
        CleanroomModDiscoverer discoverer = CleanroomModDiscoverer.instance();

        enumerateGameModArtifacts(artifacts);

        Set<String> ids = new HashSet<String>();
        for (ModContainer container : containers) {
            if (container == null || !ids.add(container.getModId())) {
                throw new IllegalStateException("active FML mod IDs are invalid or duplicated");
            }
            File source = container.getSource();
            records.add(containerRecord(
                container,
                source,
                discoverer.isModBuiltIn(container.getModId()),
                diagnostics
            ));
            if (source != null && source.isFile()) {
                facts(artifacts, source).activeModIds.add(container.getModId());
            }
        }

        for (Map.Entry<File, DiscoveredMod> entry : discoveredFiles(discoverer).entrySet()) {
            if (entry.getKey() == null || entry.getValue() == null) {
                throw new IllegalStateException("Cleanroom discovered-file map contains null");
            }
            ArtifactFacts value = facts(artifacts, entry.getKey());
            if (value.discovered != null && !value.discovered.equals(entry.getValue())) {
                throw new IllegalStateException("artifact has conflicting Cleanroom discovery records");
            }
            value.discovered = entry.getValue();
            value.discoveredModIds.addAll(entry.getValue().modIds());
            value.discoveredModIds.addAll(discoverer.modsFromSource(entry.getKey()));
        }

        List<File> nonModLibraries = discoverer.getNonModLibs();
        if (nonModLibraries != null) {
            for (File file : nonModLibraries) {
                if (file == null) throw new IllegalStateException("Cleanroom non-mod library is null");
                facts(artifacts, file).nonModLibrary = true;
            }
        }

        Set<String> classpathSources = classpathSources(artifacts, diagnostics);
        Set<String> retainedTweakers = retainedTweakers();
        Set<String> pendingTweakers = pendingTweakers();
        Set<String> ignoredNames = new HashSet<String>(CoreModManager.getIgnoredMods());
        Set<String> reparseableNames = new HashSet<String>(
            CoreModManager.getReparseableCoremods()
        );

        List<ArtifactFacts> orderedArtifacts = new ArrayList<ArtifactFacts>(artifacts.values());
        Collections.sort(orderedArtifacts, Comparator.comparing(value -> stablePath(value.file)));
        for (ArtifactFacts artifact : orderedArtifacts) {
            artifact.classpathAttached = classpathSources.contains(canonicalKey(artifact.file));
            artifact.ignoredByFml = ignoredNames.contains(artifact.file.getName());
            artifact.reparseableCoremod = reparseableNames.contains(artifact.file.getName());
            if (artifact.discovered != null && artifact.discovered.coremod() != null) {
                artifact.coremodLoaded = CoreModManager.isCoreModLoaded(
                    artifact.discovered.coremod()
                );
            }
            if (artifact.discovered != null && artifact.discovered.tweaker() != null) {
                artifact.tweakerRetainedAtCheckpoint = retainedTweakers.contains(
                    artifact.discovered.tweaker()
                );
                artifact.tweakerPendingAtCheckpoint = pendingTweakers.contains(
                    artifact.discovered.tweaker()
                );
            }
            records.add(artifactRecord(artifact, diagnostics));
        }
        return new AdapterSnapshot(records, diagnostics, diagnostics.size());
    }

    private static List<ModContainer> activeContainers() {
        List<ModContainer> result = new ArrayList<ModContainer>(
            Loader.instance().getActiveModList()
        );
        Collections.sort(result, Comparator.comparing(ModContainer::getModId));
        if (result.isEmpty()) throw new IllegalStateException("active FML mod list is empty");
        return result;
    }

    private static JsonObject containerRecord(
        ModContainer container,
        File source,
        boolean platformBuiltIn,
        List<String> diagnostics
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "fml-mod-container");
        row.addProperty("mod_id", container.getModId());
        row.addProperty("name", container.getName());
        row.addProperty("version", container.getVersion());
        row.addProperty("container_class", container.getClass().getName());
        row.addProperty("platform_builtin", platformBuiltIn);
        row.add("requirements", versions(container.getRequirements()));
        row.add("dependencies", versions(container.getDependencies()));
        row.add("dependants", versions(container.getDependants()));
        if (source == null) {
            row.addProperty("artifact_kind", "unbound-container");
            row.add("artifact_name", JsonNull.INSTANCE);
            row.add("artifact_size", JsonNull.INSTANCE);
            row.add("artifact_sha256", JsonNull.INSTANCE);
            diagnostics.add("missing-mod-artifact|" + container.getModId());
        } else if (!source.isFile()) {
            // Built-ins, dummy containers, and coremod-only containers may use
            // the runtime directory. They are containers, not file artifacts.
            row.addProperty("artifact_kind", "runtime-container");
            row.addProperty("artifact_name", source.getName());
            row.add("artifact_size", JsonNull.INSTANCE);
            row.add("artifact_sha256", JsonNull.INSTANCE);
        } else {
            row.addProperty("artifact_kind", "jar");
            row.addProperty("artifact_name", source.getName());
            row.addProperty("artifact_size", source.length());
            try {
                row.addProperty("artifact_sha256", Hashing.sha256(source.toPath()));
            } catch (IOException exception) {
                row.add("artifact_sha256", JsonNull.INSTANCE);
                diagnostics.add("unreadable-mod-artifact|" + container.getModId());
            }
        }
        return row;
    }

    private static JsonObject artifactRecord(
        ArtifactFacts artifact,
        List<String> diagnostics
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "loaded-artifact-binding");
        row.addProperty("artifact_path", stablePath(artifact.file));
        row.addProperty("artifact_name", artifact.file.getName());
        row.addProperty("artifact_kind", artifact.file.isFile() ? "file" : "directory");
        if (artifact.file.isFile()) {
            row.addProperty("artifact_size", artifact.file.length());
            try {
                row.addProperty("artifact_sha256", Hashing.sha256(artifact.file.toPath()));
            } catch (IOException exception) {
                row.add("artifact_sha256", JsonNull.INSTANCE);
                diagnostics.add("unreadable-runtime-artifact|" + stablePath(artifact.file));
            }
        } else {
            row.add("artifact_size", JsonNull.INSTANCE);
            row.add("artifact_sha256", JsonNull.INSTANCE);
            diagnostics.add("non-file-runtime-artifact|" + stablePath(artifact.file));
        }

        row.add("active_mod_ids", strings(artifact.activeModIds));
        row.add("discovered_mod_ids", strings(artifact.discoveredModIds));
        row.addProperty("classpath_attached", artifact.classpathAttached);
        row.addProperty(
            "game_mod_directory_candidate",
            artifact.gameModDirectoryCandidate
        );
        row.addProperty("non_mod_library", artifact.nonModLibrary);
        row.addProperty("ignored_by_fml", artifact.ignoredByFml);
        row.addProperty("reparseable_coremod", artifact.reparseableCoremod);
        row.addProperty("coremod_loaded", artifact.coremodLoaded);
        row.addProperty(
            "tweaker_instance_retained_at_checkpoint",
            artifact.tweakerRetainedAtCheckpoint
        );
        row.addProperty(
            "tweaker_pending_at_checkpoint",
            artifact.tweakerPendingAtCheckpoint
        );

        DiscoveredMod discovered = artifact.discovered;
        if (discovered == null) {
            row.add("discovery_mod_type", JsonNull.INSTANCE);
            row.add("coremod_class", JsonNull.INSTANCE);
            row.add("tweaker_class", JsonNull.INSTANCE);
            row.addProperty("has_mixin_manifest_attributes", false);
            row.addProperty("mixin_tweaker_force_mod", false);
            row.addProperty("coremod_contains_mod", false);
            row.add("mixin_configs", new JsonArray());
            row.add("mixin_connector", JsonNull.INSTANCE);
        } else {
            addNullable(row, "discovery_mod_type", discovered.modType());
            addNullable(row, "coremod_class", discovered.coremod());
            addNullable(row, "tweaker_class", discovered.tweaker());
            row.addProperty(
                "has_mixin_manifest_attributes",
                discovered.hasMixinManifestAttributes()
            );
            row.addProperty("mixin_tweaker_force_mod", discovered.mixinTweakerForceMod());
            row.addProperty("coremod_contains_mod", discovered.coreModContainsMod());
            Attributes attributes = discovered.attributes();
            row.add("mixin_configs", commaSeparatedAttribute(attributes, "MixinConfigs"));
            addNullable(
                row,
                "mixin_connector",
                attributes == null ? null : attributes.getValue("MixinConnector")
            );
        }

        TreeSet<String> roles = new TreeSet<String>();
        if (!artifact.activeModIds.isEmpty()) roles.add("active-fml-container-source");
        if (artifact.discovered != null) roles.add("cleanroom-discovered-artifact");
        if (artifact.classpathAttached) roles.add("launch-classpath-source");
        if (artifact.coremodLoaded) roles.add("loaded-coremod-plugin-source");
        if (artifact.discovered != null && artifact.discovered.tweaker() != null) {
            roles.add("tweaker-manifest-declared-source");
        }
        if (artifact.tweakerRetainedAtCheckpoint) {
            roles.add("retained-tweaker-instance-source");
        }
        if (artifact.nonModLibrary) roles.add("cleanroom-non-mod-library");
        if (artifact.discovered != null && artifact.discovered.hasMixinManifestAttributes()) {
            roles.add("mixin-manifest-declared-source");
        }
        row.add("runtime_roles", strings(roles));
        row.addProperty(
            "runtime_attachment_state",
            artifact.classpathAttached
                ? "classpath-attached"
                : !roles.isEmpty()
                    ? "runtime-evidenced-not-on-classpath"
                    : artifact.gameModDirectoryCandidate
                        ? "present-unattached"
                        : "unclassified"
        );
        return row;
    }

    private static void enumerateGameModArtifacts(Map<String, ArtifactFacts> artifacts) {
        int[] visited = new int[] {0};
        enumerateGameModArtifacts(
            canonicalFile(new File(gameRoot(), "mods")),
            artifacts,
            visited,
            0
        );
        enumerateGameModArtifacts(
            canonicalFile(new File(gameRoot(), "coremods")),
            artifacts,
            visited,
            0
        );
    }

    private static void enumerateGameModArtifacts(
        File directory,
        Map<String, ArtifactFacts> artifacts,
        int[] visited,
        int depth
    ) {
        if (!directory.exists()) return;
        if (!directory.isDirectory() || java.nio.file.Files.isSymbolicLink(directory.toPath())) {
            throw new IllegalStateException("game mod root is not a regular directory: " + directory);
        }
        if (depth > 8) throw new IllegalStateException("game mod tree exceeds depth bound");
        File[] children = directory.listFiles();
        if (children == null) {
            throw new IllegalStateException("cannot enumerate game mod directory: " + directory);
        }
        java.util.Arrays.sort(children, Comparator.comparing(File::getName));
        for (File child : children) {
            if (++visited[0] > 8192) {
                throw new IllegalStateException("game mod tree exceeds entry bound");
            }
            if (java.nio.file.Files.isSymbolicLink(child.toPath())) {
                throw new IllegalStateException("game mod tree contains a symlink: " + child);
            }
            if (child.isDirectory()) {
                enumerateGameModArtifacts(child, artifacts, visited, depth + 1);
            } else if (child.isFile() && child.getName().toLowerCase(java.util.Locale.ROOT)
                .endsWith(".jar")) {
                facts(artifacts, child).gameModDirectoryCandidate = true;
            }
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<File, DiscoveredMod> discoveredFiles(
        CleanroomModDiscoverer discoverer
    ) {
        return (Map<File, DiscoveredMod>) ReflectionAccess.read(DISCOVERED_FILES, discoverer);
    }

    private static Set<String> classpathSources(
        Map<String, ArtifactFacts> artifacts,
        List<String> diagnostics
    ) {
        Set<String> result = new HashSet<String>();
        if (Launch.classLoader == null) {
            throw new IllegalStateException("Launch classloader is unavailable");
        }
        for (URL source : Launch.classLoader.getSources()) {
            if (source == null || !"file".equals(source.getProtocol())) continue;
            final File file;
            try {
                URI uri = source.toURI();
                file = canonicalFile(new File(uri));
            } catch (URISyntaxException | IllegalArgumentException exception) {
                diagnostics.add("invalid-classpath-source|" + source.toExternalForm());
                continue;
            }
            result.add(canonicalKey(file));
            if (file.isFile() && isGameModArtifact(file)) facts(artifacts, file);
        }
        return result;
    }

    private static Set<String> retainedTweakers() {
        Set<String> result = new HashSet<String>();
        Object value = Launch.blackboard.get("Tweaks");
        if (value instanceof Iterable<?>) {
            for (Object tweaker : (Iterable<?>) value) {
                if (tweaker != null) result.add(tweaker.getClass().getName());
            }
        }
        return result;
    }

    private static Set<String> pendingTweakers() {
        Set<String> result = new HashSet<String>();
        Object value = Launch.blackboard.get("TweakClasses");
        if (value instanceof Iterable<?>) {
            for (Object tweaker : (Iterable<?>) value) {
                if (tweaker != null) result.add(String.valueOf(tweaker));
            }
        }
        return result;
    }

    private static boolean isGameModArtifact(File file) {
        Path home = gameRoot().toPath();
        Path source = canonicalFile(file).toPath();
        if (!source.startsWith(home)) return false;
        String relative = home.relativize(source).toString().replace(File.separatorChar, '/');
        return relative.startsWith("mods/") || relative.startsWith("coremods/");
    }

    private static ArtifactFacts facts(Map<String, ArtifactFacts> values, File file) {
        File canonical = canonicalFile(file);
        String key = canonicalKey(canonical);
        ArtifactFacts result = values.get(key);
        if (result == null) {
            result = new ArtifactFacts(canonical);
            values.put(key, result);
        }
        return result;
    }

    private static File canonicalFile(File file) {
        try {
            return file.getCanonicalFile();
        } catch (IOException exception) {
            throw new IllegalStateException("cannot canonicalize runtime artifact " + file, exception);
        }
    }

    private static String canonicalKey(File file) {
        return canonicalFile(file).getPath();
    }

    private static String stablePath(File file) {
        File canonical = canonicalFile(file);
        Path home = gameRoot().toPath();
        Path source = canonical.toPath();
        if (source.startsWith(home)) {
            String relative = home.relativize(source).toString();
            return relative.isEmpty()
                ? "."
                : relative.replace(File.separatorChar, '/');
        }
        return "external/" + canonical.getName();
    }

    private static File gameRoot() {
        File config = Loader.instance().getConfigDir();
        if (config == null || config.getParentFile() == null) {
            throw new IllegalStateException("FML canonical game root is unavailable");
        }
        return canonicalFile(config.getParentFile());
    }

    private static void addNullable(JsonObject row, String key, String value) {
        if (value == null || value.trim().isEmpty()) row.add(key, JsonNull.INSTANCE);
        else row.addProperty(key, value);
    }

    private static JsonArray commaSeparatedAttribute(Attributes attributes, String name) {
        if (attributes == null) return new JsonArray();
        String value = attributes.getValue(name);
        if (value == null || value.trim().isEmpty()) return new JsonArray();
        Set<String> rows = new TreeSet<String>();
        for (String component : value.split(",")) {
            String normalized = component.trim();
            if (!normalized.isEmpty()) rows.add(normalized);
        }
        return strings(rows);
    }

    private static JsonArray strings(Collection<String> values) {
        List<String> rows = new ArrayList<String>();
        if (values != null) {
            for (String value : values) {
                if (value == null) throw new IllegalStateException("string collection contains null");
                rows.add(value);
            }
        }
        Collections.sort(rows);
        JsonArray result = new JsonArray();
        for (String row : rows) result.add(row);
        return result;
    }

    private static JsonArray versions(Iterable<ArtifactVersion> values) {
        List<String> rows = new ArrayList<String>();
        if (values != null) {
            for (ArtifactVersion value : values) {
                if (value == null) throw new IllegalStateException("FML dependency is null");
                rows.add(value.toString());
            }
        }
        Collections.sort(rows);
        JsonArray result = new JsonArray();
        for (String row : rows) result.add(row);
        return result;
    }

    private static final class ArtifactFacts {
        private final File file;
        private final Set<String> activeModIds = new LinkedHashSet<String>();
        private final Set<String> discoveredModIds = new LinkedHashSet<String>();
        private DiscoveredMod discovered;
        private boolean gameModDirectoryCandidate;
        private boolean classpathAttached;
        private boolean nonModLibrary;
        private boolean ignoredByFml;
        private boolean reparseableCoremod;
        private boolean coremodLoaded;
        private boolean tweakerRetainedAtCheckpoint;
        private boolean tweakerPendingAtCheckpoint;

        private ArtifactFacts(File file) {
            this.file = file;
        }
    }
}
