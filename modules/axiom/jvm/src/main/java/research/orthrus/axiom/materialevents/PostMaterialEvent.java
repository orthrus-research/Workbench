// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.
package research.orthrus.axiom.materialevents;

import research.orthrus.axiom.MaterialState;

import research.orthrus.axiom.nativeevents.GenericEvent;

/**
 * Event to modify and perform post-processing on materials
 */
public class PostMaterialEvent extends GenericEvent<MaterialState> {

    public PostMaterialEvent() {
        super(MaterialState.class);
    }
}
