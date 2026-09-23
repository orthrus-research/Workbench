// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;

import java.util.Map;
import java.util.function.Function;

final class FluidStorageKey implements FluidRegistration.Key {

    // Catalog is owned by the explicit fluid environment, not a process-global registry.

    private final NativeLocation resourceLocation;
    private final MaterialIconType iconType;
    private final Function<FluidMaterial, String> registryNameFunction;
    private final Function<FluidMaterial, String> translationKeyFunction;
    private final int hashCode;
    private final FluidState defaultFluidState;
    private final int registrationPriority;

    public FluidStorageKey( NativeLocation resourceLocation,  MaterialIconType iconType,
                            Function< FluidMaterial,  String> registryNameFunction,
                            Function< FluidMaterial,  String> translationKeyFunction) {
        this(resourceLocation, iconType, registryNameFunction, translationKeyFunction, null);
    }

    public FluidStorageKey( NativeLocation resourceLocation,  MaterialIconType iconType,
                            Function< FluidMaterial,  String> registryNameFunction,
                            Function< FluidMaterial,  String> translationKeyFunction,
                            FluidState defaultFluidState) {
        this(resourceLocation, iconType, registryNameFunction, translationKeyFunction, defaultFluidState, 0);
    }

    public FluidStorageKey( NativeLocation resourceLocation,  MaterialIconType iconType,
                            Function< FluidMaterial,  String> registryNameFunction,
                            Function< FluidMaterial,  String> translationKeyFunction,
                            FluidState defaultFluidState, int registrationPriority) {
        this.resourceLocation = resourceLocation;
        this.iconType = iconType;
        this.registryNameFunction = registryNameFunction;
        this.translationKeyFunction = translationKeyFunction;
        this.hashCode = resourceLocation.hashCode();
        this.defaultFluidState = defaultFluidState;
        this.registrationPriority = registrationPriority;
        if (FluidEnvironment.current().keys().containsKey(resourceLocation)) {
            throw new IllegalArgumentException("Cannot create duplicate keys");
        }
        FluidEnvironment.current().keys().put(resourceLocation, this);
    }

    public static  FluidStorageKey getByName( NativeLocation location) {
        return FluidEnvironment.current().keys().get(location);
    }

    public  NativeLocation getResourceLocation() {
        return this.resourceLocation;
    }

    public  MaterialIconType getIconType() {
        return this.iconType;
    }

    /**
     * @param baseName the base name of the fluid
     * @return the registry name to use
     */
    public  String getRegistryNameFor( FluidMaterial baseName) {
        return registryNameFunction.apply(baseName);
    }

    /**
     * @return the translation key for fluids with this key
     */
    public  String getTranslationKeyFor( FluidMaterial material) {
        return this.translationKeyFunction.apply(material);
    }

    /**
     * @return the default fluid state for this storage key, if it exists.
     */
    public  FluidState getDefaultFluidState() {
        return defaultFluidState;
    }

    /**
     * @return The registration priority for this fluid type, determining the build order for fluids.
     *         Useful for when your fluid building requires some properties from previous fluids.
     */
    public int getRegistrationPriority() {
        return registrationPriority;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;

        FluidStorageKey fluidKey = (FluidStorageKey) o;

        return resourceLocation.equals(fluidKey.getResourceLocation());
    }

    @Override
    public int hashCode() {
        return this.hashCode;
    }

    @Override
    public  String toString() {
        return "FluidStorageKey{" + resourceLocation + '}';
    }
}
