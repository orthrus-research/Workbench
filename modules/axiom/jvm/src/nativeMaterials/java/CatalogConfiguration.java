package research.orthrus.axiom.nativeconstruction;

import java.lang.reflect.*;
import java.nio.file.*;
import java.util.*;
import net.minecraftforge.common.config.*;

/** Original parser and loading synchronization, restricted to source-projected reached fields. */
record CatalogConfiguration(boolean generateLowQualityGems, boolean allUniqueStoneTypes, List<String> modPriorities, Map<String,Object> evidence) {
    CatalogConfiguration { modPriorities = List.copyOf(modPriorities); }
    static CatalogConfiguration load(Path file, Class<?> projection) throws Exception {
        if (projection.getClassLoader() != CatalogConfiguration.class.getClassLoader())
            throw new IllegalArgumentException("Configuration projection belongs to another class space");
        for (Path p = file.toAbsolutePath(); p != null; p = p.getParent())
            if (Files.isSymbolicLink(p)) throw new IllegalArgumentException("Indirect configuration input");
        if (!Files.isRegularFile(file) || Files.size(file) > 1024 * 1024)
            throw new IllegalArgumentException("Explicit bounded configuration file required");
        byte[] before = Files.readAllBytes(file);
        // Native constructor recovery can rename/recreate a corrupt file. Give it
        // an owned disposable copy, never the caller's source configuration.
        Path temporary = Files.createTempDirectory("axiom-native-config-");
        try { return parse(Files.write(temporary.resolve("gregtech.cfg"), before), before, projection); }
        finally {
            try (var files = Files.newDirectoryStream(temporary)) { for (Path child : files) Files.delete(child); }
            Files.delete(temporary);
        }
    }
    private static CatalogConfiguration parse(Path file, byte[] before, Class<?> projection) throws Exception {
        var annotation = Objects.requireNonNull(projection.getAnnotation(Config.class));
        var cfg = new Configuration(file.toFile());
        var sync = ConfigManager.class.getDeclaredMethod("sync", Configuration.class, Class.class, String.class, String.class, boolean.class, Object.class);
        sync.setAccessible(true);
        try { sync.invoke(null, cfg, projection, annotation.modid(), annotation.category(), true, null); }
        catch (InvocationTargetException failure) {
            if (failure.getCause() instanceof Exception exception) throw exception;
            throw (Error)failure.getCause();
        }
        // Parser recovery cannot qualify the original bytes.
        if (!Arrays.equals(before, Files.readAllBytes(file))) throw new IllegalArgumentException("Native parser changed configuration input");
        var values = new TreeMap<String,Object>();
        for (String name : List.of("recipes", "worldgen", "compat")) {
            var holder = projection.getField(name);
            String category = annotation.category() + Configuration.CATEGORY_SPLITTER + holder.getAnnotation(Config.Name.class).value().toLowerCase(Locale.ENGLISH);
            Object instance = holder.get(null);
            String field = switch (name) { case "recipes" -> "generateLowQualityGems"; case "worldgen" -> "allUniqueStoneTypes"; default -> "modPriorities"; };
            Property property = cfg.getCategory(category).get(field);
            if (name.equals("compat")) {
                values.put(field, List.of((String[])holder.getType().getField(field).get(instance)));
                values.put(field + ".property", List.of(category, List.of(property.getStringList()), List.of(property.getDefaults())));
            } else {
                values.put(field, holder.getType().getField(field).getBoolean(instance));
                values.put(field + ".property", List.of(category, property.getString(), property.getDefault()));
            }
        }
        @SuppressWarnings("unchecked") var priorities = (List<String>)values.get("modPriorities");
        return new CatalogConfiguration((boolean)values.get("generateLowQualityGems"), (boolean)values.get("allUniqueStoneTypes"), priorities, Collections.unmodifiableMap(values));
    }
}
