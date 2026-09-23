package research.orthrus.axiom;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

/** Installed source identity; runtime never locates a Workbench checkout. */
public final class Target {
    private Target() {}
    static final Object BASELINE = Json.parse(resource("/axiom/supersymmetry.lock.json"));
    static final Object RULES = Json.parse(resource("/axiom/rules.json"));
    public static final String VERSION = resource("/axiom/version.txt").trim();
    public static final String ENGINE_ID = "axiom-engine:sha256:" + Json.digest(runtimeCodeIdentities());
    public static final String ID = "axiom-target:sha256:" + Json.digest(Map.of("baseline", BASELINE, "rules", RULES));
    static Map<String, String> runtimeCodeIdentities() {
        Map<String, String> identities = new TreeMap<>();
        for (String name : List.of(Target.class.getName(), "groovy.lang.GroovySystem", "org.tomlj.Toml",
                "org.antlr.v4.runtime.Parser", "org.checkerframework.checker.nullness.qual.Nullable",
                "it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap")) {
            try { identities.put(name, codeIdentity(Class.forName(name, false, Target.class.getClassLoader()))); }
            catch (ClassNotFoundException failure) { throw new IllegalStateException("Missing locked engine dependency: " + name, failure); }
        }
        return identities;
    }
    static String resource(String path) {
        try (var stream = Target.class.getResourceAsStream(path)) {
            if (stream == null) throw new IllegalStateException("Missing installed engine resource: " + path);
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException exception) { throw new IllegalStateException(exception); }
    }
    private static String codeIdentity(Class<?> owner) {
        try {
            Path location = Path.of(owner.getProtectionDomain().getCodeSource().getLocation().toURI());
            if (Files.isRegularFile(location)) return Json.bytesDigest(Files.readAllBytes(location));
            // Java-library tests may run from class directories. Installed use
            // hashes the complete actual engine and parser JARs instead.
            Map<String, Object> classes = new TreeMap<>();
            try (var paths = Files.walk(location)) {
                for (Path path : paths.filter(Files::isRegularFile).sorted().toList())
                    classes.put(location.relativize(path).toString(), Json.bytesDigest(Files.readAllBytes(path)));
            }
            return Json.digest(classes);
        } catch (Exception exception) { throw new IllegalStateException("Cannot identify loaded engine code", exception); }
    }
    public static Map<String, Object> coverage() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("targetId", ID); out.put("engineVersion", VERSION); out.put("engineId", ENGINE_ID);
        out.put("baseline", BASELINE); out.put("rules", RULES);
        out.put("runtime", NativeRuntime.require());
        out.put("scope", "explicit-definition-program/ordinary-mixer-idle-start");
        out.put("wholePackParity", false); out.put("installedCompositionQualified", false);
        out.put("execution", Map.of("javaSemantics", "profile-pinned-native-jvm",
                "customJvmImplemented", false, "recipeConstruction", "bounded-groovy-ast-model",
                "upstreamRecipeEnvironmentExecuted", false, "minecraftLaunched", false,
                "builderFieldValidation", "source-extracted-GTCEu-validateGroovy",
                "standaloneWorkerIsolation", "bubblewrap-and-thread-synchronized-linux-kernel-policy"));
        out.put("implemented", List.of("Groovy 4.0.30 parse/convert with admitted AST interpretation",
                "MIXER and BLENDER ordinary builder operations",
                "MIXER to BLENDER field projection before original validation",
                "ordered tree compilation and lookup", "greedy matching and nonconsumable circuits",
                "LV..IV ordinary MIXER input loading and idle start admission",
                "portable source-package Git membership and policy completeness verification",
                "phased loader ordering and non-executing syntax inventory with explicit candidate overlays",
                "Packwiz declaration inventory, index integrity and explicit composition gaps",
                "offline artifact hash verification and binary declaration inventory",
                "explicit platform metadata/library input binding",
                "profile-pinned native JVM file admission and runtime-bound program identity"));
        out.put("unsupported", List.of("whole pack loader and material/Java registration closure",
                "artifact-to-source and active mixin qualification",
                "arbitrary Groovy helpers, loops, imports, metaprogramming and Java source",
                "custom NBT predicates, wildcard expansion and item capabilities",
                "NBT list/array/float/double kinds", "recipe removals, chance outputs, properties and cleanroom recipes",
                "BLENDER or other machine processing", "processing ticks, completion, interruptions, RNG and output recovery"));
        return out;
    }
}
