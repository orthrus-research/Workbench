package research.orthrus.axiom;

import java.io.IOException;
import java.net.URI;
import java.security.MessageDigest;
import java.util.*;
import org.tomlj.*;

/** Declared pack inputs, not installed mods, class loading, or registry execution. */
final class TargetComposition {
    @FunctionalInterface interface Reader { byte[] read(SourceTarget.SourceFile file) throws IOException; }
    private final Reader reader;
    private final Map<String, SourceTarget.SourceFile> files;
    private final List<Map<String, Object>> diagnostics = new ArrayList<>();
    private final Map<String, Map<String, Object>> indexEntries = new TreeMap<>();
    private final Map<String, Map<String, Object>> bindings = new HashMap<>();
    private final Map<String, Object> options;
    private final String side;
    private boolean indexVerified;

    TargetComposition(Reader reader, Map<String, SourceTarget.SourceFile> files, Object lock, Object selection) {
        this.reader = reader; this.files = files;
        Map<String, Object> request = selection == null ? Map.of() : Json.object(selection);
        Json.keys(request, "side", "options");
        side = request.containsKey("side") ? Json.string(request.get("side")) : "unspecified";
        if (!Set.of("client", "server", "unspecified").contains(side) || (request.containsKey("side") && side.equals("unspecified")))
            throw Failure.request("Composition side must be client or server");
        options = request.containsKey("options") ? Json.object(request.get("options")) : Map.of();
        if (!options.isEmpty() && side.equals("unspecified")) throw Failure.request("Composition options require an explicit side");
        options.forEach((path, enabled) -> { SourceTarget.path(path); Json.bool(enabled); });
        for (Object raw : Json.array(Json.object(lock).getOrDefault("selected_dependencies", List.of()))) {
            Map<String, Object> dependency = Json.object(raw);
            bindings.put(Json.string(dependency.get("manifest")), dependency);
        }
    }

    Map<String, Object> inspect(Map<String, Object> policy, String candidateId) throws IOException {
        Map<String, Object> pack = Map.of(), index = new LinkedHashMap<>();
        var packFile = files.get("supersymmetry:pack.toml");
        if (packFile == null) diagnostic("requires-context", "composition.pack-missing", "pack.toml", "Pack manifest is not captured");
        else {
            try {
                pack = document(packFile);
                string(pack, "name");
                if (!string(pack, "pack-format").equals("packwiz:1.1.0"))
                    throw Failure.unsupported("composition.pack-format", "Only explicit packwiz:1.1.0 is admitted");
                Map<String, Object> versions = table(pack.get("versions"));
                string(versions, "minecraft");
                for (Object version : versions.values()) string(version);
                index = index(table(pack.get("index")));
            } catch (Failure failure) { diagnostic(failure, "pack.toml"); }
        }
        String directory = Json.string(policy.getOrDefault("artifactDirectory", "mods/"));
        if (!directory.endsWith("/")) throw Failure.request("Artifact directory requires a directory boundary");
        SourceTarget.path(directory.substring(0, directory.length() - 1));
        Set<String> descriptors = new TreeSet<>();
        List<Map<String, Object>> loose = new ArrayList<>();
        for (var file : files.values()) if (file.repository().equals("supersymmetry") && file.path().startsWith(directory)) {
            if (file.path().endsWith(".pw.toml")) descriptors.add(file.path());
            else {
                Map<String, Object> row = new LinkedHashMap<>(file.identity());
                row.put("indexMembership", indexMembership(file.path())); row.put("activation", "unresolved"); loose.add(row);
            }
        }
        indexEntries.forEach((path, row) -> { if (Boolean.TRUE.equals(row.get("metafile"))) descriptors.add(path); });
        List<Map<String, Object>> artifacts = new ArrayList<>();
        Map<String, String> outputs = new HashMap<>();
        Set<String> admissibleOptions = new HashSet<>();
        int parsed = 0, selected = 0, referenceSources = 0;
        for (String path : descriptors) {
            var file = files.get("supersymmetry:" + path);
            if (file == null) { diagnostic("requires-context", "composition.descriptor-missing", path, "Indexed metadata is outside the captured candidate"); continue; }
            Map<String, Object> row = new LinkedHashMap<>(file.identity());
            row.put("indexMembership", indexMembership(path));
            row.put("artifactVerified", false); row.put("activation", "unresolved");
            try {
                Map<String, Object> metadata = document(file);
                row.put("name", string(metadata, "name"));
                String output = relative(path, string(metadata, "filename"));
                row.put("outputPath", output);
                String prior = outputs.putIfAbsent(output.toLowerCase(Locale.ROOT), path);
                if (prior != null) diagnostic("source-error", "composition.output-collision", path, "Portable artifact output collides with " + prior);
                String declaredSide = defaultString(metadata, "side", "both");
                if (!Set.of("both", "client", "server").contains(declaredSide)) throw shape("Unknown artifact side");
                Map<String, Object> option = metadata.containsKey("option") ? table(metadata.get("option")) : Map.of();
                boolean optional = booleanValue(option, "optional", false), defaultEnabled = booleanValue(option, "default", false);
                if (optional) admissibleOptions.add(path);
                boolean enabled = !optional || (options.containsKey(path) ? Json.bool(options.get(path)) : defaultEnabled);
                boolean chosen = !side.equals("unspecified") && (declaredSide.equals("both") || declaredSide.equals(side)) && enabled;
                row.put("side", declaredSide); row.put("optional", optional); row.put("defaultEnabled", defaultEnabled);
                row.put("selection", side.equals("unspecified") ? "unspecified" : chosen ? "included-declaration" : "excluded-declaration");
                row.put("optionOrigin", options.containsKey(path) ? "explicit" : optional ? "declared-default" : "required");
                if (chosen) selected++;
                Map<String, Object> download = table(metadata.get("download"));
                var hash = hash(download);
                row.put("downloadHash", hash);
                String mode = defaultString(download, "mode", "url");
                Map<String, Object> locator = new LinkedHashMap<>(); locator.put("mode", mode);
                if (mode.equals("url")) {
                    String url = string(download, "url");
                    try {
                        URI uri = URI.create(url);
                        if (!Set.of("https", "http").contains(uri.getScheme()) || uri.getHost() == null || uri.getRawUserInfo() != null)
                            throw shape("Download URL must be credential-free HTTP(S)");
                    } catch (IllegalArgumentException failure) { throw shape("Invalid download URL"); }
                    locator.put("url", url);
                } else if (mode.equals("metadata:curseforge")) {
                    Map<String, Object> curse = table(table(metadata.get("update")).get("curseforge"));
                    locator.put("projectId", positive(curse.get("project-id"))); locator.put("fileId", positive(curse.get("file-id")));
                } else throw Failure.unsupported("composition.download-mode", "Unmodeled download mode: " + mode);
                row.put("download", locator);
                Map<String, Object> binding = bindings.get(path);
                String source = "no-source-selection";
                if (binding != null && binding.get("source_repository") instanceof String repository) {
                    Map<String, Object> declared = Json.object(binding.get("declared_download_hash"));
                    boolean matches = string(metadata, "filename").equals(binding.get("filename"))
                            && hash.get("algorithm").equals(declared.get("algorithm"))
                            && hash.get("value").equals(declared.get("value"));
                    row.put("referenceRepository", repository);
                    var repositoryFiles = files.values().stream().filter(member -> member.repository().equals(repository))
                            .map(SourceTarget.SourceFile::identity).toList();
                    row.put("candidateSourceId", Json.digest(repositoryFiles));
                    source = !matches ? "descriptor-differs-from-source-lock" : repositoryFiles.isEmpty() ? "reference-source-missing"
                            : "reference-source-captured; binary-equivalence-unverified";
                    if (matches && !repositoryFiles.isEmpty()) referenceSources++;
                    if (!matches) diagnostic("requires-context", "composition.source-binding-drift", path, "Changed artifact identity cannot inherit the pinned source correspondence");
                    else if (repositoryFiles.isEmpty()) diagnostic("requires-context", "composition.reference-source-missing", path, "Selected reference source is absent from candidate");
                }
                row.put("sourceCorrespondence", source); row.put("metadataStatus", "parsed"); parsed++;
            } catch (Failure failure) { diagnostic(failure, path); row.put("metadataStatus", failure.kind); }
            artifacts.add(row);
        }
        if (!admissibleOptions.containsAll(options.keySet())) throw Failure.request("Composition options must name captured optional metadata paths");
        for (var output : outputs.entrySet()) {
            // No downloaded output may overwrite candidate source or another output's directory.
            for (var file : files.values()) if (file.repository().equals("supersymmetry")) {
                String source = file.path().toLowerCase(Locale.ROOT);
                if (source.equals(output.getKey()) || source.startsWith(output.getKey() + "/") || output.getKey().startsWith(source + "/"))
                    diagnostic("source-error", "composition.output-source-collision", output.getValue(), "Downloaded output overlaps captured source: " + file.path());
            }
            int slash = output.getKey().indexOf('/');
            while (slash >= 0) {
                if (outputs.containsKey(output.getKey().substring(0, slash)))
                    diagnostic("source-error", "composition.output-directory-collision", output.getValue(), "Artifact output is also an artifact directory");
                slash = output.getKey().indexOf('/', slash + 1);
            }
        }
        List<Map<String, Object>> configurations = files.values().stream().filter(file -> file.repository().equals("supersymmetry")
                && file.path().startsWith("config/")).map(SourceTarget.SourceFile::identity).toList();
        List<Map<String, Object>> transformations = transformations();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("schema", "axiom.source-composition.v1"); result.put("scope", "captured declarations; not installed composition");
        result.put("compositionId", "axiom-source-composition:sha256:" + Json.digest(Map.of("candidateId", candidateId, "side", side, "options", options)));
        result.put("side", side); result.put("options", options);
        result.put("pack", pack); result.put("index", index);
        result.put("descriptorFiles", descriptors.size()); result.put("parsedDescriptors", parsed);
        result.put("declarationInventoryComplete", parsed == descriptors.size());
        result.put("metadataConsistent", diagnostics.isEmpty());
        result.put("selectedDeclarations", selected); result.put("referenceSourceDeclarations", referenceSources);
        result.put("artifacts", artifacts); result.put("looseArtifactFiles", loose);
        result.put("configurationFiles", configurations.size()); result.put("configurationId", Json.digest(configurations));
        result.put("transformationResources", transformations); result.put("diagnostics", diagnostics);
        result.put("indexVerified", indexVerified); result.put("artifactBytesVerified", false);
        result.put("activeTransformationsResolved", false); result.put("registryConstructed", false);
        result.put("installedCompositionQualified", false);
        result.put("gaps", List.of("complete installed artifact and platform bytes", "source-to-binary correspondence for every active dependency",
                "mod discovery, build-time substitutions, mixin/coremod activation and transformed bytecode", "registry/material bootstrap"));
        return result;
    }

    private Map<String, Object> index(Map<String, Object> declaration) throws IOException {
        String path = relative("pack.toml", string(declaration, "file"));
        Map<String, Object> expected = hash(declaration), result = new LinkedHashMap<>();
        result.put("path", path); result.put("declaredHash", expected);
        var source = files.get("supersymmetry:" + path);
        if (source == null) {
            diagnostic("requires-context", "composition.index-missing", path, "Declared index is not captured");
            result.put("state", "missing"); return result;
        }
        byte[] raw = reader.read(source);
        result.put("sha256", source.sha256()); result.put("size", raw.length);
        String observed = digest(raw, string(expected, "algorithm")); result.put("observedHash", observed);
        boolean exact = observed.equals(expected.get("value"));
        if (!exact) diagnostic("source-error", "composition.index-hash", path, "Committed index bytes differ from the pack manifest hash");
        result.put("state", exact ? "hash-verified" : "hash-mismatch");
        int before = diagnostics.size();
        try {
            Map<String, Object> index = document(source);
            String algorithm = string(index, "hash-format");
            digest(new byte[0], algorithm); // Validate even when there are no entries.
            List<?> entries = array(index.getOrDefault("files", List.of()));
            if (entries.size() > 20000) throw new Failure("incomplete", "composition.index-bound", "Index exceeds 20,000 entries");
            Set<String> outputs = new HashSet<>();
            for (Object item : entries) {
                try {
                Map<String, Object> entry = table(item); String member = relative(path, string(entry, "file"));
                if (indexEntries.putIfAbsent(member, entry) != null || !outputs.add(member.toLowerCase(Locale.ROOT)))
                    diagnostic("source-error", "composition.index-duplicate", member, "Index repeats a portable path");
                boolean metafile = booleanValue(entry, "metafile", false);
                if (entry.containsKey("alias")) diagnostic("unsupported", "composition.index-alias", member, "Index alias installation semantics are not admitted");
                if (booleanValue(entry, "preserve", false)) diagnostic("requires-context", "composition.preserved-input", member, "Installed preserved bytes require a separate explicit identity");
                Map<String, Object> hashFields = new HashMap<>(entry); hashFields.putIfAbsent("hash-format", algorithm);
                Map<String, Object> expectedMember = hash(hashFields);
                var file = files.get("supersymmetry:" + member);
                if (file == null) diagnostic("requires-context", "composition.index-member-missing", member, "Indexed input is not captured by the source policy");
                else if (!digest(reader.read(file), string(expectedMember, "algorithm")).equals(expectedMember.get("value")))
                    diagnostic("source-error", "composition.index-member-hash", member, "Indexed input differs from its declared hash");
                entry.put("metafile", metafile);
                } catch (Failure failure) { diagnostic(failure, path); }
            }
            result.put("entries", entries.size());
            result.put("metafiles", indexEntries.values().stream().filter(row -> Boolean.TRUE.equals(row.get("metafile"))).count());
        } catch (Failure failure) { diagnostic(failure, path); }
        indexVerified = exact && diagnostics.size() == before;
        result.put("membersVerified", indexVerified);
        return result;
    }

    private String indexMembership(String path) {
        if (!indexEntries.containsKey(path)) return "not-indexed";
        return indexVerified ? "verified-index-member" : "unverified-index-member";
    }

    private List<Map<String, Object>> transformations() throws IOException {
        List<Map<String, Object>> result = new ArrayList<>();
        for (var file : files.values()) {
            String name = file.path().substring(file.path().lastIndexOf('/') + 1);
            if (!file.path().startsWith("src/main/resources/") || !name.matches("mixins?\\..*\\.json")) continue;
            Map<String, Object> row = new LinkedHashMap<>(file.identity());
            row.put("activation", "unresolved; resource presence does not establish loader selection");
            try { row.put("declaration", Json.object(Json.parse(Main.utf8(reader.read(file))))); }
            catch (Failure failure) { diagnostic(failure, file.key()); row.put("metadataStatus", failure.kind); }
            result.add(row);
        }
        return result;
    }

    private Map<String, Object> document(SourceTarget.SourceFile source) throws IOException {
        byte[] raw = reader.read(source);
        if (raw.length > 1_048_576) throw new Failure("incomplete", "composition.document-bound", "TOML document exceeds 1 MiB");
        String text = Main.utf8(raw);
        complexity(text);
        TomlParseResult parsed = Toml.parse(text);
        if (parsed.hasErrors()) throw new Failure("source-error", "composition.toml", parsed.errors().get(0).toString());
        return table(convert(parsed, 0));
    }
    /** Lexical resource guard only; TomlJ remains the syntax/typing authority. */
    private static void complexity(String text) {
        int nesting = 0, dots = 0;
        char quote = 0; boolean triple = false, comment = false;
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (comment) { if (ch == '\n' || ch == '\r') { comment = false; dots = 0; } continue; }
            if (quote != 0) {
                if (quote == '"' && ch == '\\') { i++; continue; }
                if (ch == quote && (!triple || (i + 2 < text.length() && text.charAt(i + 1) == quote && text.charAt(i + 2) == quote))) {
                    if (triple) i += 2;
                    quote = 0;
                }
                continue;
            }
            if (ch == '#') { comment = true; continue; }
            if (ch == '\'' || ch == '"') {
                quote = ch; triple = i + 2 < text.length() && text.charAt(i + 1) == ch && text.charAt(i + 2) == ch;
                if (triple) i += 2;
                continue;
            }
            if (ch == '[' || ch == '{') { if (++nesting > 48) throw new Failure("incomplete", "composition.nesting-bound", "TOML delimiter nesting exceeds resource policy"); }
            else if (ch == ']' || ch == '}') nesting = Math.max(0, nesting - 1);
            if (ch == '.' && ++dots > 48) throw new Failure("incomplete", "composition.nesting-bound", "TOML dotted path exceeds resource policy");
            if (ch == '\n' || ch == '\r' || ch == ',' || ch == '=') dots = 0;
        }
    }
    private static Object convert(Object value, int depth) {
        if (depth > 48) throw new Failure("incomplete", "composition.nesting-bound", "TOML value nesting exceeds resource policy");
        if (value instanceof TomlTable table) {
            Map<String, Object> result = new LinkedHashMap<>();
            for (String key : table.keySet()) result.put(key, convert(table.get(List.of(key)), depth + 1));
            return result;
        }
        if (value instanceof TomlArray array) {
            List<Object> result = new ArrayList<>(); for (int i = 0; i < array.size(); i++) result.add(convert(array.get(i), depth + 1)); return result;
        }
        if (value instanceof String || value instanceof Boolean || value instanceof Long) return value;
        throw Failure.unsupported("composition.toml-type", "Pack metadata uses a non-admitted TOML value type");
    }
    static String relative(String owner, String relative) {
        try {
            SourceTarget.path(relative);
            int slash = owner.lastIndexOf('/'); return SourceTarget.path((slash < 0 ? "" : owner.substring(0, slash + 1)) + relative);
        } catch (Failure failure) { throw shape("Non-canonical or unsafe pack-relative path"); }
    }
    static Map<String, Object> hash(Map<String, Object> source) {
        String algorithm = string(source, "hash-format"), value = string(source, "hash");
        int length = switch (algorithm) { case "sha256" -> 64; case "sha512" -> 128; case "sha1" -> 40; case "md5" -> 32;
            default -> throw Failure.unsupported("composition.hash", "Unmodeled hash algorithm: " + algorithm); };
        if (value.length() != length || !value.matches("[0-9a-fA-F]+")) throw shape("Malformed declared content hash");
        return Map.of("algorithm", algorithm, "value", value.toLowerCase(Locale.ROOT));
    }
    static String digest(byte[] raw, String algorithm) {
        String java = switch (algorithm) { case "sha256" -> "SHA-256"; case "sha512" -> "SHA-512"; case "sha1" -> "SHA-1"; case "md5" -> "MD5";
            default -> throw Failure.unsupported("composition.hash", "Unmodeled hash algorithm: " + algorithm); };
        try { return HexFormat.of().formatHex(MessageDigest.getInstance(java).digest(raw)); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
    private static String string(Object value) { if (value instanceof String text && !text.isEmpty()) return text; throw shape("Expected nonempty string"); }
    private static String string(Map<String, Object> value, String key) { return string(value.get(key)); }
    private static String defaultString(Map<String, Object> table, String key, String fallback) {
        if (!table.containsKey(key)) return fallback;
        if (table.get(key) instanceof String value) return value.isEmpty() ? fallback : value;
        throw shape("Expected string for " + key);
    }
    @SuppressWarnings("unchecked") private static Map<String, Object> table(Object value) {
        if (value instanceof Map<?, ?> map) return (Map<String, Object>)map; throw shape("Expected TOML table");
    }
    private static List<?> array(Object value) { if (value instanceof List<?> list) return list; throw shape("Expected TOML array"); }
    private static boolean booleanValue(Map<String, Object> table, String key, boolean fallback) {
        if (!table.containsKey(key)) return fallback; if (table.get(key) instanceof Boolean value) return value; throw shape("Expected boolean for " + key);
    }
    private static long positive(Object value) { if (value instanceof Long number && number > 0) return number; throw shape("Expected positive integer identifier"); }
    private static Failure shape(String message) { return new Failure("source-error", "composition.metadata", message); }
    private void diagnostic(Failure failure, String path) {
        diagnostic(failure.kind.equals("request-error") ? "source-error" : failure.kind, failure.rule, path, failure.getMessage());
    }
    private void diagnostic(String kind, String rule, String path, String message) {
        if (diagnostics.size() >= 2000) throw new Failure("incomplete", "composition.diagnostics-bound", "Composition exceeds 2,000 diagnostics");
        diagnostics.add(Map.of("status", kind, "rule", rule, "path", path, "message", message));
    }
}
