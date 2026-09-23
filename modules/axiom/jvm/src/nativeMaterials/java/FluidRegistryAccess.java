package research.orthrus.axiom.nativeconstruction;

import com.google.common.collect.BiMap;
import net.minecraftforge.fluids.*;

/** Live original references, never a copied fluid map or a second delegate store. */
final class FluidRegistryAccess {
    final BiMap<String, Fluid> masterFluidReference = field("masterFluidReference");
    final BiMap<String, String> defaultFluidName = field("defaultFluidName");
    @SuppressWarnings("unchecked") private static <T> T field(String name) {
        try {
            var field = FluidRegistry.class.getDeclaredField(name); field.setAccessible(true);
            return (T) field.get(null);
        } catch (ReflectiveOperationException failure) { throw new IllegalStateException("Native fluid registry field unavailable: " + name, failure); }
    }
    Fluid getFluid(String name) { return FluidRegistry.getFluid(name); }
    boolean registerFluid(Fluid fluid) { return FluidRegistry.registerFluid(fluid); }
    boolean addBucketForFluid(Fluid fluid) { return FluidRegistry.addBucketForFluid(fluid); }
}
