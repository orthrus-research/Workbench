package postInit

import classes.Helper
import static prePostInit.Recipemaps.*

MIXER.recipeBuilder()
    .inputs(ore('dustAlpha'))
    .fluidInputs(fluid('water') * 1000)
    .outputs(metaitem('dustBeta'))
    .duration(20)
    .EUt(8)
    .buildAndRegister()

crafting.addShaped('fixture:alpha', item('minecraft:stone'), [
    [ore('dustAlpha')]
])
