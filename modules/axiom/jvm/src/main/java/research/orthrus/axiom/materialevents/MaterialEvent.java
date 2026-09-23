// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom.materialevents;

import research.orthrus.axiom.MaterialState;

import research.orthrus.axiom.nativeevents.GenericEvent;

/**
 * Event to register and modify materials in
 */
public class MaterialEvent extends GenericEvent<MaterialState> {

    public MaterialEvent() {
        super(MaterialState.class);
    }
}
