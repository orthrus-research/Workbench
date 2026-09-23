// Extracted from pinned GTCEu/Cleanroom source. See spec/native-fluids.md and sources/NOTICE.md.
package research.orthrus.axiom;


import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;

import java.util.Collection;
import java.util.Collections;
import java.util.Map;

final class MarkerMaterialRegistry {



    private final Map<String, MarkerMaterial> map = new Object2ObjectOpenHashMap<>();

    MarkerMaterialRegistry() {}



    /**
     * @param markerMaterial the MarkerMaterial to register
     * @return the registered MarkerMaterial
     */
    public  MarkerMaterial registerMarkerMaterial( MarkerMaterial markerMaterial) {
        MarkerMaterial existing = map.get(markerMaterial.getName());
        if (existing != null) return existing;

        map.put(markerMaterial.getName(), markerMaterial);
        return markerMaterial;
    }

    /**
     * @param name the name of the MarkerMaterial
     * @return the MarkerMaterial associated with the name
     */
    public  MarkerMaterial getMarkerMaterial( String name) {
        return map.get(name);
    }

    /**
     * @return all registered marker materials
     */
    public   Collection< MarkerMaterial> getAll() {
        return Collections.unmodifiableCollection(map.values());
    }
}
