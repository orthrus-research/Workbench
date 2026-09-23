package research.orthrus.axiom;

import java.io.*;
import java.net.URI;
import java.util.*;
import java.util.zip.*;

/** Independent interpretation of the selected bootstrap's library declarations, never a launcher. */
final class PlatformMetadata {
    static final String PRISM_SOURCE = "ea87ffcfbc22c3bb37c75b97160fe836aeb130be";
    private PlatformMetadata() {}

    static Map<String, Object> inspect(byte[] bootstrap, Map<String, Object> policy, Map<String, Object> selection) throws IOException {
        Json.keys(selection, "side", "os", "architecture", "javaMajor");
        if (!"client".equals(selection.get("side")) || !"linux".equals(selection.get("os"))
                || !"x86_64".equals(selection.get("architecture")))
            throw Failure.unsupported("platform.selection", "Platform metadata currently admits an explicit Linux x86_64 client selection only");
        int javaMajor = Json.number(selection.get("javaMajor"));
        if (!"workbench-cleanroom-native-runtime-lock-v1".equals(policy.get("format")))
            throw Failure.unsupported("platform.policy", "Unsupported platform policy format");
        String revision = Json.string(policy.get("source_revision"));
        if (!revision.matches("[0-9a-f]{40}")) throw Failure.request("Invalid platform source revision");
        Map<String, Object> bootstrapPolicy = Json.object(policy.get("bootstrap"));
        if (!Json.bytesDigest(bootstrap).equals(bootstrapPolicy.get("sha256")))
            throw Failure.request("Bootstrap bytes differ from platform policy");
        Map<String, byte[]> files = readBootstrap(bootstrap);
        Map<String, Object> pack = document(files, "mmc-pack.json");
        Json.keys(pack, "components", "formatVersion");
        if (Json.number(pack.get("formatVersion")) != 1) throw Failure.unsupported("platform.format", "Unsupported bootstrap component format");
        List<Object> components = Json.array(pack.get("components"));
        if (components.isEmpty() || components.size() > 32) throw Failure.request("Expected 1..32 platform components");
        Map<String, String> versions = new LinkedHashMap<>();
        List<Map<String, Object>> metadata = new ArrayList<>(), componentRecords = new ArrayList<>();
        for (Object value : components) {
            Map<String, Object> component = Json.object(value);
            Json.keys(component, "uid", "version", "cachedName", "cachedVersion", "cachedRequires", "cachedVolatile", "dependencyOnly", "important");
            String uid = Json.string(component.get("uid")), version = Json.string(component.get("version"));
            if (!uid.matches("[A-Za-z0-9_.-]+") || versions.putIfAbsent(uid, version) != null)
                throw Failure.request("Invalid or duplicate platform component");
            String path = "patches/" + uid + ".json";
            Map<String, Object> data = document(files, path);
            Json.keys(data, "formatVersion", "name", "releaseTime", "type", "uid", "version", "volatile", "libraries",
                    "requires", "mainClass", "mainJar", "+tweakers", "+jvmArgs", "compatibleJavaMajors", "assetIndex", "minecraftArguments");
            if (Json.number(data.get("formatVersion")) != 1 || !uid.equals(data.get("uid")) || !version.equals(data.get("version")))
                throw Failure.request("Platform component identity differs from its original metadata");
            metadata.add(data);
            componentRecords.add(Map.of("uid", uid, "version", version, "metadataPath", path, "sha256", Json.bytesDigest(files.get(path))));
        }
        String platformEntry = "patches/net.minecraftforge.json";
        if (!platformEntry.equals(bootstrapPolicy.get("metadata_entry")) || !Objects.equals(versions.get("net.minecraftforge"), policy.get("cleanroom_version")))
            throw Failure.request("Bootstrap does not select the declared Cleanroom platform");
        for (String name : files.keySet()) {
            if (name.startsWith("patches/") && name.endsWith(".json")
                    && componentRecords.stream().noneMatch(row -> row.get("metadataPath").equals(name)))
                throw Failure.unsupported("platform.extra-patch", "Unselected bootstrap patch: " + name);
        }
        LinkedHashMap<String, Map<String, Object>> jars = new LinkedHashMap<>(), natives = new LinkedHashMap<>();
        List<Map<String, Object>> declarations = new ArrayList<>(), requirements = new ArrayList<>();
        List<Object> javaMajors = new ArrayList<>(), tweakers = new ArrayList<>(), jvmArgs = new ArrayList<>();
        Map<String, Object> mainJar = null;
        String mainClass = null;
        for (Map<String, Object> data : metadata) {
            String uid = Json.string(data.get("uid"));
            for (Object value : Json.array(data.getOrDefault("requires", List.of()))) {
                Map<String, Object> required = Json.object(value); Json.keys(required, "uid", "equals", "suggests");
                String dependency = Json.string(required.get("uid"));
                if (!versions.containsKey(dependency) || required.containsKey("equals") && !Objects.equals(versions.get(dependency), required.get("equals")))
                    throw Failure.request("Missing or incompatible platform component requirement: " + dependency);
                Map<String, Object> row = new LinkedHashMap<>(required); row.put("component", uid); row.put("selectedVersion", versions.get(dependency));
                requirements.add(row); // Suggestions are not equality constraints or cached component authority.
            }
            javaMajors.addAll(Json.array(data.getOrDefault("compatibleJavaMajors", List.of())));
            tweakers.addAll(strings(data.getOrDefault("+tweakers", List.of())));
            jvmArgs.addAll(strings(data.getOrDefault("+jvmArgs", List.of())));
            if (data.containsKey("mainClass")) mainClass = Json.string(data.get("mainClass"));
            int ordinal = 0;
            for (Object raw : Json.array(data.getOrDefault("libraries", List.of()))) {
                if (declarations.size() >= 2000) throw Failure.request("Too many platform library declarations");
                Map<String, Object> lib = Json.object(raw);
                Map<String, Object> row = library(lib, uid, ordinal++, selection);
                declarations.add(row);
                if (!"active".equals(row.get("selection"))) continue;
                var destination = "native-extraction".equals(row.get("role")) ? natives : jars;
                String key = Json.string(row.get("matchKey"));
                Map<String, Object> previous = destination.get(key);
                if (previous == null) destination.put(key, row);
                else if (!Objects.equals(previous.get("version"), row.get("version"))) {
                    // The pinned Linux bootstrap has no active unequal-version collisions. Never substitute SemVer.
                    throw Failure.unsupported("platform.version-order", "Unequal active library versions need qualified launcher version ordering: " + key);
                } else {
                    row.put("selection", "duplicate-equal-version");
                    row.put("retainedDeclaration", previous.get("declarationId"));
                }
            }
            if (data.containsKey("mainJar")) {
                mainJar = library(Json.object(data.get("mainJar")), uid, -1, selection);
                if (!"active".equals(mainJar.get("selection")) || !"classpath".equals(mainJar.get("role")))
                    throw Failure.unsupported("platform.main-jar", "Conditional or native main JAR is not admitted");
                mainJar.put("role", "main-jar");
            }
        }
        if (mainJar == null || mainClass == null || !mainClass.equals(Json.object(policy.get("native_service_expectations")).get("launch_main_class")))
            throw Failure.request("Platform main JAR or entrypoint differs from policy");
        if (javaMajors.stream().mapToInt(Json::number).noneMatch(value -> value == javaMajor))
            throw Failure.request("Selected target Java major is not advertised by the bootstrap");
        List<Map<String, Object>> libraries = new ArrayList<>(jars.values());
        libraries.add(mainJar); libraries.addAll(natives.values());
        Set<String> paths = new HashSet<>();
        for (Map<String, Object> row : libraries) {
            if (!paths.add(Json.string(row.get("path")))) throw Failure.unsupported("platform.path-collision", "Selected platform artifacts share a storage path");
        }
        // The profile's short lock strengthens selected hashes; it is not a substitute for the full bootstrap.
        Set<String> pinned = new HashSet<>();
        for (Object value : Json.array(policy.get("artifacts"))) {
            Map<String, Object> pin = Json.object(value);
            String coordinate = Json.string(pin.get("coordinate"));
            if (!pinned.add(coordinate)) throw Failure.request("Repeated policy artifact");
            List<Map<String, Object>> matches = libraries.stream().filter(row -> row.get("coordinate").equals(coordinate)).toList();
            if (matches.size() != 1) throw Failure.request("Policy artifact is not uniquely selected: " + coordinate);
            Map<String, Object> match = matches.get(0);
            String digest = Json.string(pin.get("sha256"));
            if (!digest.matches("[0-9a-f]{64}") || !Objects.equals(match.get("sha1"), pin.get("sha1"))
                    || Json.integer(match.get("size")) != Json.integer(pin.get("size")))
                throw Failure.request("Policy artifact differs from original bootstrap declaration: " + coordinate);
            match.put("policySha256", digest);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("schema", "axiom.platform-metadata.v1"); out.put("components", componentRecords); out.put("requirements", requirements);
        out.put("selection", selection); out.put("sourceRevision", revision); out.put("metadataRulesSource", "PrismLauncher/PrismLauncher@" + PRISM_SOURCE);
        out.put("mainClass", mainClass); out.put("tweakers", tweakers); out.put("jvmArguments", jvmArgs); out.put("compatibleJavaMajors", javaMajors);
        out.put("declarations", declarations); out.put("libraries", libraries);
        out.put("classpath", libraries.stream().filter(row -> !row.get("role").equals("native-extraction")).map(row -> row.get("path")).toList());
        out.put("nativeExtractions", libraries.stream().filter(row -> row.get("role").equals("native-extraction")).map(row -> row.get("path")).toList());
        out.put("selectionComplete", true); out.put("classesLoaded", false); out.put("installedCompositionQualified", false);
        return out;
    }

    private static List<Object> strings(Object value) {
        List<Object> values = Json.array(value); values.forEach(Json::string); return values;
    }
    private static Map<String, Object> library(Map<String, Object> lib, String component, int ordinal, Map<String, Object> selection) {
        Json.keys(lib, "name", "downloads", "rules", "natives", "extract");
        Coordinate coordinate = Coordinate.parse(Json.string(lib.get("name")));
        boolean nativeLibrary = lib.containsKey("natives") && !Json.object(lib.get("natives")).isEmpty();
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("declarationId", component + ":" + (ordinal < 0 ? "mainJar" : "libraries/" + ordinal));
        row.put("coordinate", lib.get("name")); row.put("version", coordinate.version()); row.put("matchKey", coordinate.key());
        row.put("role", nativeLibrary ? "native-extraction" : "classpath");
        row.put("declarationSha256", Json.digest(lib));
        if (!active(lib.getOrDefault("rules", List.of()), selection)) {
            row.put("selection", "inactive-rules"); return row;
        }
        Map<String, Object> downloads = Json.object(lib.get("downloads"));
        Json.keys(downloads, "artifact", "classifiers");
        Map<String, Object> download;
        if (nativeLibrary) {
            Map<String, Object> choices = Json.object(lib.get("natives"));
            Object classifier = choices.getOrDefault("linux-x86_64", choices.get("linux"));
            if (classifier == null) { row.put("selection", "inactive-native-platform"); return row; }
            String selected = Json.string(classifier).replace("${arch}", "64");
            coordinate = coordinate.classified(selected);
            download = Json.object(Json.object(downloads.get("classifiers")).get(selected));
            if (lib.containsKey("extract")) {
                Map<String, Object> extract = Json.object(lib.get("extract")); Json.keys(extract, "exclude");
                row.put("extractExclude", strings(extract.getOrDefault("exclude", List.of())));
            }
        } else {
            if (lib.containsKey("extract")) throw Failure.unsupported("platform.extract", "Non-native extraction declaration is not admitted");
            download = Json.object(downloads.get("artifact"));
        }
        Json.keys(download, "path", "url", "sha1", "size");
        String hash = Json.string(download.get("sha1")), url = Json.string(download.get("url"));
        long size = Json.integer(download.get("size"));
        if (!hash.matches("[0-9a-f]{40}") || size <= 0 || size > ArtifactBundle.FILE_BYTES) throw Failure.request("Invalid platform artifact declaration");
        try {
            URI uri = URI.create(url);
            if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null)
                throw Failure.request("Expected an HTTPS artifact URL without credentials");
        } catch (IllegalArgumentException failure) { throw Failure.request("Malformed artifact URL"); }
        // Storage follows the coordinate, even when the download metadata suggests a different path.
        row.put("path", coordinate.path()); row.put("url", url); row.put("sha1", hash); row.put("size", size); row.put("selection", "active");
        if (download.containsKey("path")) row.put("declaredDownloadPath", Json.string(download.get("path")));
        return row;
    }
    static boolean active(Object rawRules, Map<String, Object> selection) {
        List<Object> rules = Json.array(rawRules);
        if (rules.isEmpty()) return true;
        boolean allowed = false;
        for (Object raw : rules) {
            Map<String, Object> rule = Json.object(raw); Json.keys(rule, "action", "os");
            String action = Json.string(rule.get("action"));
            if (!Set.of("allow", "disallow").contains(action)) throw Failure.unsupported("platform.rule-action", "Unknown library rule action");
            boolean applies = true;
            if (rule.containsKey("os")) {
                Map<String, Object> os = Json.object(rule.get("os")); Json.keys(os, "name", "version");
                String name = Json.string(os.get("name"));
                applies = name.equals(selection.get("os")) || name.equals(selection.get("os") + "-" + selection.get("architecture"));
                if (os.containsKey("version")) Json.string(os.get("version")); // Prism retains but does not test this field.
            }
            if (applies) allowed = action.equals("allow");
        }
        return allowed;
    }
    record Coordinate(String group, String artifact, String version, String classifier, String extension) {
        static Coordinate parse(String name) {
            String[] extension = name.split("@", -1), parts = extension[0].split(":", -1);
            if (extension.length > 2 || parts.length < 3 || parts.length > 4) throw Failure.request("Invalid Maven coordinate");
            for (String part : parts) if (!part.matches("[A-Za-z0-9_.+-]+") || part.equals(".") || part.equals(".."))
                throw Failure.request("Unsupported or unsafe Maven coordinate");
            String suffix = extension.length == 2 ? extension[1] : "jar";
            if (!suffix.matches("[A-Za-z0-9]+") || Arrays.stream(parts[0].split("\\.", -1)).anyMatch(String::isEmpty))
                throw Failure.request("Invalid Maven extension or group");
            return new Coordinate(parts[0], parts[1], parts[2], parts.length == 4 ? parts[3] : "", suffix);
        }
        Coordinate classified(String value) { return parse(group + ":" + artifact + ":" + version + ":" + value + "@" + extension); }
        String key() { return group + ":" + artifact + ":" + classifier; }
        String path() { return SourceTarget.path(group.replace('.', '/') + "/" + artifact + "/" + version + "/" + artifact + "-" + version
                + (classifier.isEmpty() ? "" : "-" + classifier) + "." + extension); }
    }
    private static Map<String, Object> document(Map<String, byte[]> files, String path) {
        byte[] raw = files.get(path);
        if (raw == null) throw Failure.request("Missing original bootstrap metadata: " + path);
        return Json.object(Json.parse(Main.utf8(raw)));
    }
    private static Map<String, byte[]> readBootstrap(byte[] bytes) throws IOException {
        if (bytes.length > 16 * 1024 * 1024) throw Failure.request("Bootstrap exceeds byte bound");
        Map<String, byte[]> files = new LinkedHashMap<>(); Set<String> names = new HashSet<>(); long expanded = 0;
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(bytes))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                String name = entry.getName();
                if (!names.add(name) || names.size() > 256) throw Failure.request("Duplicate or excessive bootstrap members");
                SourceTarget.path(entry.isDirectory() ? name.substring(0, name.length() - 1) : name);
                byte[] raw = Main.read(zip, 1_048_576); expanded += raw.length;
                if (expanded > 16 * 1024 * 1024 || entry.isDirectory() && raw.length != 0) throw Failure.request("Oversized bootstrap");
                if (!entry.isDirectory()) files.put(name, raw);
            }
        }
        return files;
    }
}
