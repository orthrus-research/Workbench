package research.orthrus.axiom.materialhost;

import gregtech.api.GregTechAPI;
import gregtech.api.unification.ore.OrePrefix;
import gregtech.common.items.MetaItems;
import supercritical.api.unification.material.SCMaterials;
import supercritical.api.unification.ore.SCOrePrefix;
import java.util.*;

/** Observe completed native material/prefix setup; never initialize an unvisited
 * catalog, invoke damage callbacks, generate items or register queued fluids. */
public final class NativeSupercriticalObservations {
    private NativeSupercriticalObservations() {}

    public static Map<String,Object> collect(Object composition) throws Exception {
        if(!(composition instanceof Map<?,?> state)||!"registered".equals(state.get("status"))
                ||!Set.of("CLOSED","FROZEN").contains(GregTechAPI.materialManager.getPhase().name()))
            return Map.of("status","not-observed-before-material-callback-completion");
        var field=MetaItems.class.getDeclaredField("orePrefixes");field.setAccessible(true);
        var declared=(List<?>)field.get(null);
        var radiation=OrePrefix.class.getDeclaredField("sc$radiationDamageFunction");radiation.setAccessible(true);
        var rows=new ArrayList<Map<String,Object>>();
        for(String name:List.of("fuelRod","fuelRodDepleted","fuelRodHotDepleted","fuelPelletRaw",
                "fuelPellet","fuelPelletDepleted","dustSpentFuel","dustBredFuel","dustFissionByproduct")) {
            var prefix=(OrePrefix)SCOrePrefix.class.getField(name).get(null);
            rows.add(Map.of("name",name,"nativePrefixIdentity",OrePrefix.getPrefix(name)==prefix,
                    "metaItemDeclarationCount",declared.stream().filter(value->value==prefix).count(),
                    "radiationFunctionPresent",radiation.get(prefix)!=null,
                    "heatFunctionPresent",prefix.heatDamageFunction!=null));
        }
        var corium=SCMaterials.Corium;
        return Map.of("status","observed","coriumStaticIdentity",corium!=null&&
                        GregTechAPI.materialManager.getMaterial("supercritical:corium")==corium,
                "coriumStorageRegistry",corium==null?"":corium.getRegistry().getModid(),
                "prefixes",List.copyOf(rows),"damageFunctionsInvoked",false,
                "generatedItemsRegistered",false,"fluidsRegistered",false);
    }
}
