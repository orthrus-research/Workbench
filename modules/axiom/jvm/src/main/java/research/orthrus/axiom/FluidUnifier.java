// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import it.unimi.dsi.fastutil.Hash;
import it.unimi.dsi.fastutil.objects.Object2ObjectOpenCustomHashMap;

import java.util.Map;
import java.util.Objects;

/**
 * Provides NativeFluid to FluidMaterial mappings.
 * <p>
 * Currently only used for surface rock placement during World Generation.
 * May be changed or removed in the future.
 */

final class FluidUnifier {

    private final Map<NativeFluid, FluidMaterial> fluidToMaterial = new Object2ObjectOpenCustomHashMap<>(
            new Hash.Strategy<>() {

                @Override
                public int hashCode( NativeFluid o) {
                    return o == null ? 0 : o.getName().hashCode();
                }

                @Override
                public boolean equals( NativeFluid a,  NativeFluid b) {
                    return Objects.equals(a == null ? null : a.getName(), b == null ? null : b.getName());
                }
            });

    FluidUnifier() {}

    /**
     * Register a material to associate with a fluid. Will overwrite existing associations.
     *
     * @param fluid    the fluid
     * @param material the material to associate
     */

    public void registerFluid( NativeFluid fluid,  FluidMaterial material) {
        fluidToMaterial.put(fluid, material);
    }

    /**
     * @param fluid the fluid to retrieve a material for
     * @return the material associated with the fluid
     */

    public  FluidMaterial getMaterialFromFluid( NativeFluid fluid) {
        return fluidToMaterial.get(fluid);
    }
}
