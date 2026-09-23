// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom.materialevents;


import research.orthrus.axiom.MaterialRegistry;

import research.orthrus.axiom.nativeevents.GenericEvent;

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
