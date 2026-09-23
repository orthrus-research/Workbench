// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;
final class FluidRegistrationService {
private static void fixFluidRegistryName( NativeFluid fluid,  String modid) {
        if ("gregtech".equals(modid)) return;

        var MASTER_FLUID_REFERENCE = FluidEnvironment.current().registry().masterFluidReference;
        var DEFAULT_FLUID_NAME = FluidEnvironment.current().registry().defaultFluidName;
        String masterKey = MASTER_FLUID_REFERENCE.inverse().get(fluid);
        if (masterKey != null && masterKey.startsWith("gregtech" + ":")) {
            MASTER_FLUID_REFERENCE.inverse().put(fluid, modid + ':' + fluid.getName());
        }

        String defaultName = DEFAULT_FLUID_NAME.get(fluid.getName());
        if (defaultName.startsWith("gregtech" + ":")) {
            DEFAULT_FLUID_NAME.put(fluid.getName(), modid + ':' + fluid.getName());
        }
    }
public void registerFluid( NativeFluid fluid,  String modid, boolean generateBucket) {
        boolean didExist = FluidEnvironment.current().registry().getFluid(fluid.getName()) != null;
        FluidEnvironment.current().registry().registerFluid(fluid);
        if (!didExist) {
            // If it didn't exist, that means that this is a fresh fluid of our own
            // creation and not one which is being transformed by a FluidMaterial.
            FluidEnvironment.current().sprites().add(fluid.getStill());
            FluidEnvironment.current().sprites().add(fluid.getFlowing());
            fixFluidRegistryName(fluid, modid);
        }
        if (generateBucket) {
            FluidEnvironment.current().registry().addBucketForFluid(fluid);
        }
    }
}
