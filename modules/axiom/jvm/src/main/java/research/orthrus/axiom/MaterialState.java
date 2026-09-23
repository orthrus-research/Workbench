package research.orthrus.axiom;

import java.util.function.BooleanSupplier;

/**
 * Name/property/phase carrier for extracted native material verification.
 * Optional explicit registry identity; not a GT material builder, flags or composition.
 */
public class MaterialState {
    private final String name;
    private final BooleanSupplier canModifyMaterials;
    protected final MaterialProperties properties;
    private final Integer id;
    private final String modid;

    MaterialState(String name, BooleanSupplier canModifyMaterials) {
        this(null, null, name, canModifyMaterials);
    }

    MaterialState(Integer id, String modid, String name, BooleanSupplier canModifyMaterials) {
        this(id, modid, name, canModifyMaterials, new MaterialProperties());
    }

    MaterialState(Integer id, String modid, String name, BooleanSupplier canModifyMaterials, MaterialProperties properties) {
        this(id, modid, name, canModifyMaterials, properties, true);
    }

    MaterialState(Integer id, String modid, String name, BooleanSupplier canModifyMaterials,
                  MaterialProperties properties, boolean bindOwner) {
        this.id = id;
        this.modid = modid;
        this.name = name;
        this.canModifyMaterials = canModifyMaterials;
        this.properties = java.util.Objects.requireNonNull(properties);
        if (bindOwner) properties.setMaterial(this);
    }

    public int getId() {
        if (id == null) throw new IllegalStateException("Material registry ID was not supplied");
        return id;
    }
    public String getModid() {
        if (modid == null) throw new IllegalStateException("Material registry namespace was not supplied");
        return modid;
    }

    public MaterialProperties getProperties() {
        return properties;
    }

    // GT Material.setProperty, with only the material manager access substituted.
    public <T extends IMaterialProperty> void setProperty(PropertyKey<T> key, IMaterialProperty property) {
        if (!canModifyMaterials.getAsBoolean()) {
            throw new IllegalStateException("Cannot add properties to a Material when registry is frozen!");
        }
        properties.setProperty(key, property);
        properties.verify();
    }

    @Override public String toString() { return name; }
}
