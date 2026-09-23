package research.orthrus.axiom.nativeconstruction;

import java.lang.reflect.*;
import java.util.*;

/** Live static-field access, not a populated copy or an inferred material registry. */
final class CatalogInputs implements PrefixDependencies.Inputs {
    private final Map<String, Field> fields = new HashMap<>();
    private final CatalogConfiguration configuration;
    private final List<Object> reads = new ArrayList<>();
    CatalogInputs(Class<?> catalog, CatalogConfiguration configuration) {
        if (catalog.getClassLoader() != CatalogInputs.class.getClassLoader())
            throw new IllegalArgumentException("Catalog must belong to this native class space");
        this.configuration = Objects.requireNonNull(configuration);
        for (Field field : catalog.getDeclaredFields()) {
            if (FluidMaterial.class.isAssignableFrom(field.getType()) && Modifier.isPublic(field.getModifiers()) && Modifier.isStatic(field.getModifiers())) {
                field.setAccessible(true); fields.put(field.getName(), field);
            }
        }
        if (fields.isEmpty()) throw new IllegalArgumentException("Empty material source catalog");
    }
    public FluidMaterial material(String name) {
        Field field = fields.get(name);
        if (field == null) throw new Failure("incomplete", "material.catalog-field", "Unbound source field: " + name);
        try {
            FluidMaterial material = (FluidMaterial)field.get(null);
            reads.add(Arrays.asList(name, material == null ? null : material.getRegistryName()));
            return material; // Null is a real not-yet-assigned field, not an absent binding.
        } catch (IllegalAccessException failure) { throw new IllegalStateException(failure); }
    }
    public boolean generateLowQualityGems() { return configuration.generateLowQualityGems(); }
    public boolean allUniqueStoneTypes() { return configuration.allUniqueStoneTypes(); }
    List<String> modPriorities() { return configuration.modPriorities(); }
    List<Object> reads() { return List.copyOf(reads); }
}
