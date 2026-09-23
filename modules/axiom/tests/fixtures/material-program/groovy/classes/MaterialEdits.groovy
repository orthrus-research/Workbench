package classes

import static material.DeveloperMaterials.*
import static gregtech.api.unification.material.info.MaterialFlags.*

class MaterialEdits {
    static void apply() {
        Phosphate.setFormula('(Li,Na)AlPO4(F,OH)', true)
        Titanate.addFlags(NO_SMELTING)
        Aluminosilicate.setMaterialRGB(0x99ccbb)
    }
}
