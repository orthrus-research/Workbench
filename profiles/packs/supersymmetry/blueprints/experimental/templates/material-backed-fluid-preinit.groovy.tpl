package preInit

import gregtech.api.unification.material.Material
import gregtech.api.unification.material.event.MaterialEvent
import static gregtech.api.unification.material.info.MaterialFlags.FLAMMABLE

import supersymmetry.api.util.SuSyUtility

eventManager.listen {
    MaterialEvent event ->
        new Material.Builder({{material_id}}, SuSyUtility.susyId('{{registry_name}}'))
                .liquid()
                .color({{color}})
                .flags(FLAMMABLE)
                .build()
}
