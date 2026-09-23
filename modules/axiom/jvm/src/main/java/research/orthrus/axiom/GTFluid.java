// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import it.unimi.dsi.fastutil.objects.ObjectLinkedOpenHashSet;

import java.util.Collection;

class GTFluid extends NativeFluid implements AttributedFluid {

    private final Collection<FluidAttribute> attributes = new ObjectLinkedOpenHashSet<>();
    private final FluidState state;

    public GTFluid( String fluidName, NativeLocation still, NativeLocation flowing,
                    FluidState state) {
        super(fluidName, still, flowing);
        setGaseous(state != FluidState.LIQUID);
        this.state = state;
    }

    @Override
    public  FluidState getState() {
        return state;
    }

    @Override
    public   Collection<FluidAttribute> getAttributes() {
        return attributes;
    }

    @Override
    public void addAttribute( FluidAttribute attribute) {
        attributes.add(attribute);
    }

    public static class GTMaterialFluid extends GTFluid {

        private final FluidMaterial material;
        private final String translationKey;

        public GTMaterialFluid( String fluidName, NativeLocation still, NativeLocation flowing,
                                FluidState state,  String translationKey,  FluidMaterial material) {
            super(fluidName, still, flowing, state);
            this.material = material;
            this.translationKey = translationKey;
        }

        public  FluidMaterial getMaterial() {
            return this.material;
        }





    }
}
