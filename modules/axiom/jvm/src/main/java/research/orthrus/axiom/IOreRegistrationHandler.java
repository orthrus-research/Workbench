// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


@FunctionalInterface
interface IOreRegistrationHandler {

    void processMaterial(OrePrefix orePrefix, FluidMaterial material);
}
