// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;



import java.util.Collection;

interface AttributedFluid {

    /**
     * @return the attributes on the fluid
     */


    Collection<FluidAttribute> getAttributes();

    /**
     * @param attribute the attribute to add
     */
    void addAttribute( FluidAttribute attribute);


    FluidState getState();
}
