// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom.nativeconstruction;

import research.orthrus.axiom.nativeconstruction.FluidMaterial;

import net.minecraftforge.fml.common.eventhandler.GenericEvent;

/**
 * Event to register and modify materials in
 */
public class MaterialEvent extends GenericEvent<FluidMaterial> {

    public MaterialEvent() {
        super(FluidMaterial.class);
    }
}
