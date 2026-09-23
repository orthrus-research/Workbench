package research.orthrus.axiom;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;

/** Portable source composition, independently verified without Git or game initialization. */
public final class SourceTarget implements AutoCloseable {
    static final int FILE_BOUND = 16 * 1024 * 1024;
    static final long TOTAL_BOUND = 256L * 1024 * 1024;
    private final ZipFile archive;
    private final Map<String, ZipEntry> entries = new HashMap<>();
    private final Set<String> used = new HashSet<>();
    private final Map<String, SourceFile> files = new TreeMap<>();
    private final Map<String, Object> manifest, policy;
    private final Object sourceLock;
    private final String id;
    private long expanded;
    private int treeVisits;

    record SourceFile(String repository, String path, String sha256, int size, String blob, byte[] overlay) {
        String key() { return repository + ":" + path; }
        Map<String, Object> identity() { return Map.of("repository", repository, "path", path, "sha256", sha256, "size", size); }
    }

    public SourceTarget(Path path) throws IOException {
        this(path, Target.BASELINE, Target.ID);
    }
    SourceTarget(Path path, Object expectedLock, String expectedBinding) throws IOException {
        if (Files.isSymbolicLink(path) || !Files.isRegularFile(path) || Files.size(path) > TOTAL_BOUND)
            throw Failure.request("Target must be an ordinary bounded source-package file");
        archive = new ZipFile(path.toFile(), StandardCharsets.UTF_8);
        try {
            var enumeration = archive.entries();
            long total = 0;
            while (enumeration.hasMoreElements()) {
                ZipEntry entry = enumeration.nextElement();
                String name = entry.getName();
                if (!(Set.of("manifest.json", "policy.json", "source-lock.json").contains(name)
                        || name.matches("trees/[0-9a-f]{40}") || name.matches("blobs/[0-9a-f]{64}"))
                        || entry.isDirectory() || entry.getSize() < 0 || entry.getSize() > FILE_BOUND
                        || entries.putIfAbsent(name, entry) != null || entries.size() > 50000)
                    throw Failure.request("Invalid, duplicate or oversized target entry: " + name);
                total += entry.getSize();
                if (total > TOTAL_BOUND) throw Failure.request("Target exceeds its expanded byte bound");
            }
            byte[] raw = bytes("manifest.json");
            id = "axiom-source-target:sha256:" + Json.bytesDigest(raw);
            manifest = Json.object(Json.parse(Main.utf8(raw), FILE_BOUND));
            Json.keys(manifest, "schema", "profile", "bindingId", "sourceLockSha256", "policySha256", "repositories");
            if (!"axiom.target.v1".equals(manifest.get("schema")) || !expectedBinding.equals(manifest.get("bindingId")))
                throw Failure.unsupported("target.binding", "Target package does not bind the installed source/rule composition");
            byte[] lockBytes = bytes("source-lock.json"), policyBytes = bytes("policy.json");
            if (!Json.bytesDigest(lockBytes).equals(manifest.get("sourceLockSha256"))
                    || !Json.bytesDigest(policyBytes).equals(manifest.get("policySha256")))
                throw Failure.request("Target policy or source lock digest differs");
            Object lock = Json.parse(Main.utf8(lockBytes));
            sourceLock = lock;
            if (!Json.digest(lock).equals(Json.digest(expectedLock)))
                throw Failure.unsupported("target.source-lock", "Source lock differs from the installed qualified binding selection");
            policy = Json.object(Json.parse(Main.utf8(policyBytes)));
            Json.keys(policy, "schema", "profile", "scope", "repositories", "scriptRoot", "runConfig", "artifactDirectory", "qualification");
            if (!"axiom.target-policy.v1".equals(policy.get("schema")) || !"source-composition".equals(policy.get("scope"))
                    || !"supersymmetry".equals(policy.get("profile")) || !policy.get("profile").equals(manifest.get("profile")))
                throw Failure.request("Invalid source target profile policy");
            Map<String, Object> selected = Json.object(policy.get("repositories"));
            Map<String, Map<String, Object>> locked = new TreeMap<>();
            for (Object item : Json.array(Json.object(lock).get("repositories"))) {
                Map<String, Object> row = Json.object(item); locked.put(Json.string(row.get("id")), row);
            }
            if (!selected.keySet().equals(locked.keySet())) throw Failure.request("Target policy omits locked source owners");
            Set<String> owners = new HashSet<>();
            for (Object item : Json.array(manifest.get("repositories"))) {
                Map<String, Object> row = Json.object(item);
                Json.keys(row, "id", "commit", "tree", "files");
                String owner = Json.string(row.get("id"));
                if (!owners.add(owner) || !locked.containsKey(owner)
                        || !locked.get(owner).get("commit").equals(row.get("commit"))
                        || !locked.get(owner).get("tree").equals(row.get("tree")))
                    throw Failure.request("Target repository differs from the selected locked source");
                Map<String, Object> selection = Json.object(selected.get(owner));
                validateSelection(selection);
                Map<String, String> expected = new TreeMap<>();
                tree(Json.string(row.get("tree")), "", selection, expected, 0);
                Set<String> observed = new HashSet<>();
                for (Object file : Json.array(row.get("files"))) {
                    Map<String, Object> record = Json.object(file);
                    Json.keys(record, "path", "blob", "sha256", "size");
                    String name = path(Json.string(record.get("path"))), digest = Json.string(record.get("sha256"));
                    if (!observed.add(name) || !digest.matches("[0-9a-f]{64}")
                            || !Objects.equals(expected.get(name), record.get("blob")) || !expected.containsKey(name))
                        throw Failure.request("Source is absent, duplicated or differs from its Git tree: " + name);
                    SourceFile source = new SourceFile(owner, name, digest, Json.number(record.get("size")), Json.string(record.get("blob")), null);
                    byte[] content = read(source);
                    if (!gitDigest("blob", content).equals(source.blob())) throw Failure.request("Source Git blob differs: " + name);
                    files.put(source.key(), source);
                }
                if (!observed.equals(expected.keySet())) throw Failure.request("Selected source files are missing from the target: " + owner);
            }
            if (!owners.equals(locked.keySet())) throw Failure.request("Target omits source repositories");
            for (Object value : Json.array(Json.object(lock).get("references"))) {
                Map<String, Object> reference = Json.object(value);
                SourceFile file = files.get(reference.get("repository") + ":" + reference.get("path"));
                if (file == null || !file.sha256().equals(reference.get("sha256")))
                    throw Failure.request("Target omits or changes a required source binding: " + reference.get("id"));
            }
            if (!used.equals(entries.keySet())) throw Failure.request("Target has unreferenced archive entries");
        } catch (IOException | RuntimeException | Error failure) {
            archive.close(); throw failure;
        }
    }

    private byte[] bytes(String name) throws IOException {
        ZipEntry entry = entries.get(name);
        if (entry == null) throw Failure.request("Missing source-package entry: " + name);
        try (InputStream input = archive.getInputStream(entry)) {
            byte[] raw = input.readNBytes(FILE_BOUND + 1);
            if (raw.length != entry.getSize() || raw.length > FILE_BOUND) throw Failure.request("Source-package entry exceeds declared size");
            if (used.add(name)) expanded += raw.length;
            if (expanded > TOTAL_BOUND) throw Failure.request("Source-package expansion exceeds byte bound");
            return raw;
        }
    }
    byte[] read(SourceFile file) throws IOException {
        byte[] result = file.overlay() == null ? bytes("blobs/" + file.sha256()) : file.overlay().clone();
        if (result.length != file.size() || !Json.bytesDigest(result).equals(file.sha256()))
            throw Failure.request("Source content differs from target identity: " + file.path());
        return result;
    }
    static String gitDigest(String kind, byte[] content) {
        try {
            MessageDigest hash = MessageDigest.getInstance("SHA-1");
            hash.update((kind + " " + content.length + "\0").getBytes(StandardCharsets.US_ASCII));
            return HexFormat.of().formatHex(hash.digest(content));
        } catch (java.security.NoSuchAlgorithmException exception) { throw new AssertionError(exception); }
    }
    static String path(String value) {
        if (value.isEmpty() || value.startsWith("/") || value.contains("\\") || value.contains(":")
                || value.chars().anyMatch(ch -> ch < 32) || value.length() > 4096)
            throw Failure.request("Unsafe target source path");
        for (String part : value.split("/", -1)) if (part.isEmpty() || part.equals(".") || part.equals(".."))
            throw Failure.request("Unsafe target source path");
        return value;
    }
    private static void validateSelection(Map<String, Object> policy) {
        Json.keys(policy, "prefixes", "paths");
        for (Object value : Json.array(policy.get("paths"))) path(Json.string(value));
        for (Object value : Json.array(policy.get("prefixes"))) {
            String prefix = Json.string(value);
            if (!prefix.endsWith("/")) throw Failure.request("Source selection prefix requires a directory boundary");
            path(prefix.substring(0, prefix.length() - 1));
        }
    }
    private static boolean selected(String name, Map<String, Object> policy) {
        if (Json.array(policy.get("paths")).contains(name)) return true;
        return Json.array(policy.get("prefixes")).stream().anyMatch(prefix -> name.startsWith(Json.string(prefix)));
    }
    private void tree(String id, String prefix, Map<String, Object> selection, Map<String, String> selected, int depth) throws IOException {
        if (++treeVisits > 50000 || depth > 128) throw Failure.request("Git tree exceeds traversal bound");
        byte[] raw = bytes("trees/" + id);
        if (!gitDigest("tree", raw).equals(id)) throw Failure.request("Git tree object digest differs");
        int position = 0;
        Set<String> names = new HashSet<>();
        while (position < raw.length) {
            int start = position;
            while (position < raw.length && raw[position] != ' ') position++;
            if (position == raw.length) throw Failure.request("Malformed Git tree mode");
            String mode = new String(raw, start, position++ - start, StandardCharsets.US_ASCII);
            start = position;
            while (position < raw.length && raw[position] != 0) position++;
            if (position + 21 > raw.length) throw Failure.request("Malformed Git tree entry");
            String name = path(Main.utf8(Arrays.copyOfRange(raw, start, position++)));
            if (name.contains("/") || !names.add(name)) throw Failure.request("Invalid Git tree name");
            String object = HexFormat.of().formatHex(raw, position, position + 20); position += 20;
            String relative = prefix + name;
            if (mode.equals("40000")) tree(object, relative + "/", selection, selected, depth + 1);
            else if (Set.of("100644", "100755", "120000", "160000").contains(mode)) {
                if (selected(relative, selection)) {
                    if (!Set.of("100644", "100755").contains(mode)) throw Failure.request("Selected source is a link or submodule: " + relative);
                    selected.put(relative, object);
                }
            } else throw Failure.request("Unknown Git tree mode");
        }
    }

    Map<String, SourceFile> candidate(Object raw) {
        Map<String, Object> request = Json.object(raw);
        Json.keys(request, "schema", "targetId", "overlays", "includeFiles", "sourcePaths", "composition", "artifactPaths");
        if (!"axiom.target-request.v1".equals(request.get("schema")) || !id.equals(request.get("targetId")))
            throw Failure.request("Target inspection requires its exact base target identity");
        Map<String, SourceFile> result = new TreeMap<>(files);
        List<Object> overlays = Json.array(request.get("overlays"));
        if (overlays.size() > 256) throw Failure.request("Too many target overlays");
        Set<String> touched = new HashSet<>();
        for (Object value : overlays) {
            Map<String, Object> overlay = Json.object(value);
            Json.keys(overlay, "repository", "path", "expectedSha256", "text");
            if (!overlay.keySet().containsAll(Set.of("repository", "path", "expectedSha256", "text")))
                throw Failure.request("Overlay requires explicit expected identity and text or deletion");
            String owner = Json.string(overlay.get("repository")), name = path(Json.string(overlay.get("path"))), key = owner + ":" + name;
            Map<String, Object> selections = Json.object(policy.get("repositories"));
            if (!selections.containsKey(owner) || !selected(name, Json.object(selections.get(owner))) || !touched.add(key))
                throw Failure.request("Overlay is duplicated or outside the selected source policy");
            SourceFile previous = files.get(key);
            if (!Objects.equals(previous == null ? null : previous.sha256(), overlay.get("expectedSha256")))
                throw Failure.request("Overlay expected identity differs from source package: " + name);
            if (overlay.get("text") == null) {
                if (previous == null) throw Failure.request("Cannot delete absent source");
                result.remove(key);
            } else {
                byte[] data = Json.string(overlay.get("text")).getBytes(StandardCharsets.UTF_8);
                if (data.length > 1_048_576) throw Failure.request("Overlay source exceeds byte bound");
                result.put(key, new SourceFile(owner, name, Json.bytesDigest(data), data.length, null, data));
            }
        }
        for (SourceFile file : result.values()) {
            for (int slash = file.path().indexOf('/'); slash >= 0; slash = file.path().indexOf('/', slash + 1)) {
                if (result.containsKey(file.repository() + ":" + file.path().substring(0, slash)))
                    throw Failure.request("Candidate path is both a file and a directory: " + file.path());
            }
        }
        return result;
    }

    public Map<String, Object> inspect(Object request) throws IOException {
        return inspect(request, null);
    }
    public Map<String, Object> inspect(Object request, Path artifacts) throws IOException {
        Map<String, SourceFile> candidate = request == null ? new TreeMap<>(files) : candidate(request);
        boolean include = request != null && Json.object(request).containsKey("includeFiles") && Json.bool(Json.object(request).get("includeFiles"));
        String candidateId = "axiom-source-candidate:sha256:" + Json.digest(Map.of("target", id,
                "files", candidate.values().stream().map(SourceFile::identity).toList()));
        Map<String, Long> counts = new TreeMap<>();
        for (SourceFile file : candidate.values()) counts.merge(file.repository(), 1L, Long::sum);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("targetId", id); result.put("candidateId", candidateId);
        result.put("bindingId", manifest.get("bindingId")); result.put("profile", manifest.get("profile"));
        result.put("policySha256", manifest.get("policySha256")); result.put("repositoryFiles", counts);
        result.put("sourceTreeMembershipVerified", true); result.put("policySelectionComplete", true);
        result.put("membershipScope", "base package; candidate overlays have separate content identity");
        Set<String> sourcePaths = null;
        if (request != null && Json.object(request).containsKey("sourcePaths")) {
            List<Object> paths = Json.array(Json.object(request).get("sourcePaths"));
            if (paths.isEmpty() || paths.size() > 64) throw Failure.request("sourcePaths needs 1..64 explicit script paths");
            sourcePaths = new HashSet<>();
            for (Object value : paths) {
                String name = path(Json.string(value));
                if (!sourcePaths.add(name) || !candidate.containsKey("supersymmetry:" + name) || !SourcePlan.script(name))
                    throw Failure.request("Unknown or repeated script inventory path");
            }
        }
        result.put("sourcePlan", SourcePlan.inspect(this, candidate, policy, include, sourcePaths));
        Object selection = request == null ? null : Json.object(request).get("composition");
        if (request != null && Json.object(request).containsKey("composition") && selection == null)
            throw Failure.request("Composition selection must be an object when supplied");
        result.put("composition", new TargetComposition(this::read, candidate, sourceLock, selection).inspect(policy, candidateId));
        Set<String> artifactDetails = new HashSet<>();
        if (request != null && Json.object(request).containsKey("artifactPaths")) {
            if (artifacts == null) throw Failure.request("Artifact details require an explicit artifact bundle");
            List<Object> paths = Json.array(Json.object(request).get("artifactPaths"));
            if (paths.isEmpty() || paths.size() > 16) throw Failure.request("artifactPaths needs 1..16 explicit descriptor paths");
            for (Object value : paths) if (!artifactDetails.add(path(Json.string(value)))) throw Failure.request("Repeated artifact detail path");
        }
        if (artifacts != null) result.put("artifactInspection", ArtifactBundle.inspect(artifacts, result, artifactDetails));
        result.put("wholePackParity", false); result.put("installedCompositionQualified", false);
        result.put("registryState", "not-constructed");
        result.put("gaps", List.of("remaining pack mod source/artifact and active transformation closure",
                "registry/material bootstrap and complete construction execution",
                "machine-family and processing qualification"));
        return result;
    }
    @Override public void close() throws IOException { archive.close(); }
}
