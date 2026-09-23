// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


import it.unimi.dsi.fastutil.objects.Object2BooleanMap;
import it.unimi.dsi.fastutil.objects.Object2BooleanOpenHashMap;

import java.util.Collection;
import java.util.Objects;

class FluidPipeProperties implements IMaterialProperty {

    private final Object2BooleanMap<FluidAttribute> containmentPredicate = new Object2BooleanOpenHashMap<>();

    private int throughput;
    private final int tanks;

    private int maxFluidTemperature;
    private boolean gasProof;
    private boolean cryoProof;
    private boolean plasmaProof;

    public FluidPipeProperties(int maxFluidTemperature, int throughput, boolean gasProof, boolean acidProof,
                               boolean cryoProof, boolean plasmaProof) {
        this(maxFluidTemperature, throughput, gasProof, acidProof, cryoProof, plasmaProof, 1);
    }

    /**
     * Should only be called from
     * {@link gregtech.common.pipelike.fluidpipe.FluidPipeType#modifyProperties(FluidPipeProperties)}
     */
    public FluidPipeProperties(int maxFluidTemperature, int throughput, boolean gasProof, boolean acidProof,
                               boolean cryoProof, boolean plasmaProof, int tanks) {
        this.maxFluidTemperature = maxFluidTemperature;
        this.throughput = throughput;
        this.gasProof = gasProof;
        if (acidProof) setCanContain(FluidEnvironment.current().attributes().ACID, true);
        this.cryoProof = cryoProof;
        this.plasmaProof = plasmaProof;
        this.tanks = tanks;
    }

    /**
     * Default property constructor.
     */
    public FluidPipeProperties() {
        this(300, 1, false, false, false, false);
    }


    public void verifyProperty(MaterialProperties properties) {
        if (!properties.hasProperty(PropertyKey.WOOD)) {
            properties.ensureSet(PropertyKey.INGOT, true);
        }

        if (properties.hasProperty(PropertyKey.ITEM_PIPE)) {
            throw new IllegalStateException(
                    "Material " + properties.getMaterial() +
                            " has both Fluid and Item Pipe Property, which is not allowed!");
        }
    }

    public int getTanks() {
        return tanks;
    }

    public int getThroughput() {
        return throughput;
    }

    public void setThroughput(int throughput) {
        this.throughput = throughput;
    }


    public int getMaxFluidTemperature() {
        return maxFluidTemperature;
    }

    public void setMaxFluidTemperature(int maxFluidTemperature) {
        this.maxFluidTemperature = maxFluidTemperature;
    }


    public boolean canContain( FluidState state) {
        return switch (state) {
            case LIQUID -> true;
            case GAS -> gasProof;
            case PLASMA -> plasmaProof;
        };
    }


    public boolean canContain( FluidAttribute attribute) {
        return containmentPredicate.getBoolean(attribute);
    }


    public void setCanContain( FluidAttribute attribute, boolean canContain) {
        this.containmentPredicate.put(attribute, canContain);
    }


    public   Collection< FluidAttribute> getContainedAttributes() {
        return containmentPredicate.keySet();
    }

    public boolean isGasProof() {
        return gasProof;
    }

    public void setGasProof(boolean gasProof) {
        this.gasProof = gasProof;
    }

    public boolean isAcidProof() {
        return canContain(FluidEnvironment.current().attributes().ACID);
    }

    public boolean isCryoProof() {
        return cryoProof;
    }

    public void setCryoProof(boolean cryoProof) {
        this.cryoProof = cryoProof;
    }

    public boolean isPlasmaProof() {
        return plasmaProof;
    }

    public void setPlasmaProof(boolean plasmaProof) {
        this.plasmaProof = plasmaProof;
    }


    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof FluidPipeProperties that)) return false;
        return getThroughput() == that.getThroughput() &&
                getTanks() == that.getTanks() &&
                getMaxFluidTemperature() == that.getMaxFluidTemperature() &&
                isGasProof() == that.isGasProof() &&
                isCryoProof() == that.isCryoProof() &&
                isPlasmaProof() == that.isPlasmaProof() &&
                containmentPredicate.equals(that.containmentPredicate);
    }


    public int hashCode() {
        return Objects.hash(getThroughput(), getTanks(), getMaxFluidTemperature(), gasProof, cryoProof, plasmaProof,
                containmentPredicate);
    }


    public String toString() {
        return "FluidPipeProperties{" +
                "throughput=" + throughput +
                ", tanks=" + tanks +
                ", maxFluidTemperature=" + maxFluidTemperature +
                ", gasProof=" + gasProof +
                ", cryoProof=" + cryoProof +
                ", plasmaProof=" + plasmaProof +
                ", containmentPredicate=" + containmentPredicate +
                '}';
    }
}
