// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import java.util.List;
import java.util.function.Consumer;

final class FluidAttribute {

    private final NativeLocation resourceLocation;
    private final Consumer<List<String>> fluidTooltip;
    private final Consumer<List<String>> containerTooltip;
    private final int hashCode;

    public FluidAttribute( NativeLocation resourceLocation,
                           Consumer<List< String>> fluidTooltip,
                           Consumer<List< String>> containerTooltip) {
        this.resourceLocation = resourceLocation;
        this.fluidTooltip = fluidTooltip;
        this.containerTooltip = containerTooltip;
        this.hashCode = resourceLocation.hashCode();
    }

    public  NativeLocation getResourceLocation() {
        return resourceLocation;
    }

    public void appendFluidTooltips( List< String> tooltip) {
        fluidTooltip.accept(tooltip);
    }

    public void appendContainerTooltips( List< String> tooltip) {
        containerTooltip.accept(tooltip);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;

        FluidAttribute that = (FluidAttribute) o;

        return resourceLocation.equals(that.getResourceLocation());
    }

    @Override
    public int hashCode() {
        return hashCode;
    }

    @Override
    public  String toString() {
        return "FluidAttribute{" + resourceLocation + '}';
    }
}
