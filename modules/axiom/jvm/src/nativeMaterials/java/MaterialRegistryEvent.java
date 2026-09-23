// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom.nativeconstruction;


import research.orthrus.axiom.nativeconstruction.MaterialRegistry;

import net.minecraftforge.fml.common.eventhandler.GenericEvent;

/**
 * Event to add a material registry in.
 *
 * Registry creation is supplied by the lifecycle owner.
 */
public class MaterialRegistryEvent extends GenericEvent<MaterialRegistry> {

    public MaterialRegistryEvent() {
        super(MaterialRegistry.class);
    }
}
