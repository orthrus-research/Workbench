package material

import gregtech.api.unification.material.Material
import net.minecraft.util.ResourceLocation

import static gregtech.api.unification.material.Materials.*
import static gregtech.api.unification.material.info.MaterialFlags.*
import static gregtech.api.unification.material.info.MaterialIconSet.*

class DeveloperMaterials {
    static Material Aluminosilicate
    static Material Phosphate
    static Material Titanate

    static Material.Builder named(int id, String name) {
        return new Material.Builder(id, new ResourceLocation('supersymmetry', name))
    }

    static void register() {
        log.infoMC('Registering the developer material program')
        Aluminosilicate = named(31000, 'developer_aluminosilicate')
                .dust().ore().color(0x88bbaa).flags(NO_SMELTING)
                .components(Lithium, Aluminium, Silicon * 4, Oxygen * 10)
                .build()
        Phosphate = named(31001, 'developer_phosphate')
                .dust().color(0xbbaacc)
                .components(Lithium, Aluminium, Phosphorus, Oxygen * 4, Fluorine)
                .build()
        Titanate = named(31002, 'developer_titanate')
                .gem().ore().iconSet(SHINY).color(0x445566)
                .components(Calcium, Titanium, Oxygen * 3)
                .build()
    }
}
