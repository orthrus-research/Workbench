// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom.nativeconstruction;

import research.orthrus.axiom.nativeconstruction.FluidMaterial;

import net.minecraftforge.fml.common.eventhandler.GenericEvent;

/**
 * Event to modify and perform post-processing on materials
 */
public class PostMaterialEvent extends GenericEvent<FluidMaterial> {

    public PostMaterialEvent() {
        super(FluidMaterial.class);
    }
}
