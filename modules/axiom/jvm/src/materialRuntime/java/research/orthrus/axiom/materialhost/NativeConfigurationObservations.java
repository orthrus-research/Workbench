package research.orthrus.axiom.materialhost;

import com.google.common.collect.Multimap;
import net.minecraft.launchwrapper.Launch;
import net.minecraftforge.common.config.Config;
import net.minecraftforge.common.config.ConfigManager;
import net.minecraftforge.common.config.Configuration;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.discovery.ASMDataTable;
import java.nio.file.Path;
import java.util.*;

/** Read bindings populated by original construction; never load, synchronize or save a config. */
public final class NativeConfigurationObservations {
    private NativeConfigurationObservations() {}

    static Map<String,Object> collect(Loader loader) {
        var result = new LinkedHashMap<String,Object>();
        result.put("schema", "axiom.native-configuration-bindings.v1");
        result.put("scope", "original-active-annotation-config-bindings-before-preinit");
        result.put("application", "original-config-manager-sync-during-construction");
        var gaps = new ArrayList<String>();
        try {
            Path home = Launch.minecraftHome.toPath().toRealPath();
            Path directory = loader.getConfigDir().toPath().toRealPath();
            boolean savedDirectory = directory.equals(home.resolve("config").toRealPath());
            result.put("savedDirectoryIdentity", savedDirectory);
            if (!savedDirectory) gaps.add("Original native configuration directory differs from the saved program home");

            @SuppressWarnings("unchecked")
            var metadata = (Map<String,Multimap<Config.Type,ASMDataTable.ASMData>>)field("asm_data");
            @SuppressWarnings("unchecked")
            var registered = (Map<String,Set<Class<?>>>)field("MOD_CONFIG_CLASSES");
            @SuppressWarnings("unchecked")
            var configurations = (Map<String,Configuration>)field("CONFIGS");
            var expected = new TreeMap<String,String>();
            for (var container : loader.getModList()) {
                var declarations = metadata.get(container.getModId());
                if (declarations == null) continue;
                for (var declaration : declarations.get(Config.Type.INSTANCE))
                    expected.put(declaration.getClassName(), container.getModId());
            }
            var rows = new ArrayList<Map<String,Object>>();
            var observed = new HashSet<String>();
            for (var owner : registered.entrySet()) for (Class<?> type : owner.getValue()) {
                Config annotation = type.getAnnotation(Config.class);
                if (annotation == null) {
                    gaps.add("Original registered configuration annotation is absent: " + type.getName());
                    continue;
                }
                String name = annotation.name().isEmpty() ? annotation.modid() : annotation.name();
                Path path = directory.resolve(name + ".cfg").normalize();
                Configuration configuration = configurations.get(path.toFile().getAbsolutePath());
                boolean identity = configuration != null && configuration.getConfigFile().toPath().toRealPath().equals(path.toRealPath());
                boolean ownerIdentity = owner.getKey().equals(annotation.modid());
                if (!path.startsWith(directory) || !identity || !ownerIdentity) {
                    gaps.add("Original configuration object, owner or saved path differs: " + type.getName());
                    continue;
                }
                observed.add(type.getName());
                rows.add(Map.of("class", type.getName(), "owner", owner.getKey(),
                        "path", home.relativize(path).toString().replace('\\', '/'),
                        "nativeConfigurationIdentity", true, "nativeOwnerIdentity", true,
                        "declaredForSelectedMod", owner.getKey().equals(expected.get(type.getName()))));
            }
            rows.sort(Comparator.comparing(row -> row.get("class").toString()));
            var missing = new TreeSet<>(expected.keySet()); missing.removeAll(observed);
            for (String name : missing) gaps.add("Original selected configuration class was not synchronized: " + name);
            if (expected.isEmpty() || rows.isEmpty()) gaps.add("Original selected configuration bindings were not observed");
            result.put("declaredClasses", expected);
            result.put("classes", rows);
        } catch (Exception | LinkageError failure) {
            gaps.add("Original configuration observation failed: " + failure.getClass().getName() + ": " + failure.getMessage());
        }
        result.put("status", gaps.isEmpty() ? "observed" : "incomplete");
        result.put("observationsComplete", gaps.isEmpty());
        result.put("affectingGaps", gaps);
        result.put("meaning", "original-config-bindings-not-all-captured-file-applicability-or-postinit-validation");
        return result;
    }

    private static Object field(String name) throws ReflectiveOperationException {
        var field = ConfigManager.class.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(null);
    }
}
