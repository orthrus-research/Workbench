// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


enum FluidState {

    LIQUID("gregtech.fluid.state_liquid"),
    GAS("gregtech.fluid.state_gas"),
    PLASMA("gregtech.fluid.state_plasma");

    private final String translationKey;

    FluidState( String translationKey) {
        this.translationKey = translationKey;
    }

    public  String getTranslationKey() {
        return this.translationKey;
    }
}
