package research.orthrus.axiom;

import java.nio.file.*;
import java.util.*;

/** Original source methods run over a real disposable regular-file tree. No game classes. */
public final class LoaderConformance {
    public static void main(String[] args) throws Exception {
        Path root = Files.createDirectory(Path.of("scripts"));
        List<String> names = List.of("a/a.groovy", "a/x.gsh", "a/sub/a.gvy", "a/sub/b.gy", "a/sub/no.txt", "b/a.groovy", "b/sub/a.groovy", "b/no.txt", "root.groovy");
        for (String name : names) { Path file = root.resolve(name); Files.createDirectories(file.getParent()); Files.writeString(file, "// fixture"); }
        Random random = new Random(0x4158494f4dL);
        List<String> choices = List.of("", "a", "a/sub", "b", "missing", "a/a.groovy", "b/sub/a.groovy", "root.groovy");
        for (int vector = 0; vector < 2000; vector++) {
            List<String> paths = new ArrayList<>();
            for (int count = random.nextInt(10); count > 0; count--) paths.add(choices.get(random.nextInt(choices.size())));
            List<String> actual = SourcePlan.ordered(names, paths);
            List<String> expected = UpstreamLoader.getSortedFilesOf(root.toFile(), paths, false).stream().map(file -> root.relativize(file.toPath()).toString()).toList();
            if (!expected.equals(actual)) throw new AssertionError("Loader order differs at vector " + vector + ": " + paths);
            Map<String, Object> declared = new LinkedHashMap<>();
            declared.put("preInit", randomPaths(random)); declared.put("postInit", randomPaths(random));
            Map<String, List<String>> expectedPaths = new LinkedHashMap<>();
            List<UpstreamLoader.Pair<String,String>> previous = new ArrayList<>();
            var log = new UpstreamLoader.GroovyLog.Msg();
            for (var entry : declared.entrySet()) {
                List<String> accepted = new ArrayList<>();
                for (Object raw : (List<?>)entry.getValue()) {
                    String path = UpstreamLoader.sanitizePath(((String)raw).replace('\\','/'));
                    if (!accepted.contains(path) && UpstreamLoader.checkValid(log, previous, entry.getKey(), path)) accepted.add(path);
                }
                expectedPaths.put(entry.getKey(), accepted);
                for (String path : accepted) previous.add(new UpstreamLoader.Pair<>(entry.getKey(), path));
            }
            var actualPaths = SourcePlan.loaders(Map.of("loaders", declared), new ArrayList<>());
            if (!expectedPaths.equals(actualPaths)) throw new AssertionError("Loader path admission differs at vector " + vector);
        }
        System.out.println("{\"loaderOrderVectors\":2000,\"pathAdmissionVectors\":2000,\"sourceMethodsCompared\":3,\"suffixHelperCompared\":true,\"dependencyAdapters\":true,\"wholePackParity\":false}");
    }
    private static List<String> randomPaths(Random random) {
        List<String> choices = List.of("a", "a/", "a/sub", "b/", "b\\sub\\", "a/a.groovy", "aLongerPrefix", "missing");
        List<String> result = new ArrayList<>();
        for (int i = random.nextInt(10); i > 0; i--) result.add(choices.get(random.nextInt(choices.size())));
        return result;
    }
}
