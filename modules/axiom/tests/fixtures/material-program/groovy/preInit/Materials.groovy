package preInit

import material.DeveloperMaterials
import classes.MaterialEdits
import gregtech.api.unification.material.event.MaterialEvent
import gregtech.api.unification.material.event.PostMaterialEvent
import net.minecraftforge.fml.common.eventhandler.EventPriority

eventManager.listen(EventPriority.LOWEST) { MaterialEvent event ->
    DeveloperMaterials.register()
}

eventManager.listen(EventPriority.NORMAL) { PostMaterialEvent event ->
    MaterialEdits.apply()
}
