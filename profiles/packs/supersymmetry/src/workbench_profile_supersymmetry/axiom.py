"""Select this profile's Axiom source-package policy; no recipe algorithms."""

from importlib.resources import files
from hashlib import sha256
import json

PROFILE_API_VERSION = 1


def target_policy():
    raw = files(__package__).joinpath("axiom-target-policy.json").read_bytes()
    return {"profile": "supersymmetry", "sha256": sha256(raw).hexdigest(),
            "policy": json.loads(raw)}


def material_contexts():
    """Profile-owned context selection; pending qualification is not availability.

    Axiom must additionally bind an installed qualified native program, compiler,
    transformations and runtime. Candidate source cannot supply those authorities.
    """
    raw = files(__package__).joinpath("axiom-material-contexts.json").read_bytes()
    return {"profile": "supersymmetry", "sha256": sha256(raw).hexdigest(),
            "policy": json.loads(raw)}


def preparation_inputs(context_id):
    """Own the complete original artifact inputs for the selected native scope."""
    catalog = material_contexts()
    context = next((row for row in catalog["policy"]["contexts"] if row["id"] == context_id), None)
    if context is None or context["side"] != "server":
        raise ValueError("Unknown SERVER material preparation context")
    if context_id == "supersymmetry:material-authoring-gt-base":
        return None  # This bounded context has no required-early pack selection.
    raw = files(__package__).joinpath("axiom-native-inputs.json").read_bytes()
    native_raw = files(__package__).joinpath("axiom-native-early-context.json").read_bytes()
    inputs, native = json.loads(raw), json.loads(native_raw)
    if (inputs.get("schema") != "axiom.pack-native-inputs.v1"
            or inputs.get("profile") != "supersymmetry" or inputs.get("context") != context_id
            or inputs.get("side") != context["side"] or native.get("side") != context["side"]
            or inputs.get("nativeContext") != native.get("id")
            or inputs.get("nativeContextSha256") != sha256(native_raw).hexdigest()
            or inputs.get("packSource", {}).get("revision") != native.get("packRevision")
            or native.get("packRevision") != context["sourceRevisions"]["supersymmetry"]
            or [row.get("descriptor") for row in inputs["artifacts"]] != native["artifactDescriptors"]):
        raise ValueError("Pack native preparation differs from the selected original context")
    return {
        "schema": "axiom.native-input-preparation.v1", "profile": "supersymmetry",
        "side": "server", "inputStage": "raw-original-artifacts",
        "policySha256": {"axiom-native-inputs.json": sha256(raw).hexdigest(),
                         "axiom-native-early-context.json": sha256(native_raw).hexdigest()},
        "artifacts": [{key: row[key] for key in ("path", "url", "sha256", "size")}
                      for row in inputs["artifacts"]],
    }


def assembly_policy(context_id):
    """Supply original selected context resources to Axiom's native assembler."""
    requirements = preparation_inputs(context_id)
    if requirements is None:
        raise ValueError("This context does not declare automatic native assembly")
    names = ("axiom-native-inputs.json", "axiom-native-early-context.json",
             "axiom-material-contexts.json", "axiom-material-admission.json")
    resources = {name: files(__package__).joinpath(name).read_bytes() for name in names}
    if any(sha256(resources[name]).hexdigest() != digest
           for name, digest in requirements["policySha256"].items()):
        raise ValueError("Pack assembly policy changed during selection")
    return {"profile": "supersymmetry", "context": context_id,
            "nativeContext": json.loads(resources["axiom-native-early-context.json"]),
            "artifactInputs": json.loads(resources["axiom-native-inputs.json"])["artifacts"],
            "contextPolicy": resources["axiom-material-contexts.json"],
            "admissionPolicy": bind_material_admission(resources["axiom-material-admission.json"], context_id),
            "sourceInputs": {"profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/" + name:
                             sha256(raw).hexdigest() for name, raw in resources.items()}}


def bind_material_admission(raw, context_id):
    """Bind the common vocabulary to a declared context, with reproducible bytes.

    This does not add lifecycle support. The context catalog carries composition
    and qualification independently from shared authoring-language admission.
    """
    policy = json.loads(raw)
    base = "supersymmetry:material-authoring-gt-base"
    if policy.get("schema") != "axiom.material-admission.v1" or policy.get("profile") != "supersymmetry" or policy.get("context") != base:
        raise ValueError("Material admission policy owner differs")
    if context_id == base:
        return raw
    if context_id != "supersymmetry:material-authoring-pack":
        raise ValueError("Unknown material admission context")
    policy["context"] = context_id
    # Original ModifyRecycling's native event callback uses these registered
    # bindings. Identity admission leaves lookup and argument dispatch native.
    policy["nativeObjectMappers"] = ["material", "metaitem", "item", "ore", "fluid", "liquid", "recipemap"]
    # The pack host registers the original complete Susy subscriber before
    # material dispatch. Native static field reads remain native (including null
    # if source accesses the catalog before its producer has run).
    policy["staticFields"].extend([
        "supersymmetry.common.materials.SusyMaterials",
        "gregtech.api.recipes.RecipeMaps",
        "supersymmetry.api.recipes.SuSyRecipeMaps",
    ])
    # Exact public constructor families from the selected native artifacts.
    # Original Material.setProperty owns verification and dependency insertion;
    # admission neither constructs substitute properties nor runs producers.
    policy["constructors"].update({
        "supersymmetry.api.unification.material.properties.FiberProperty": ["()V", "(ZZZ)V"],
        "supersymmetry.api.unification.material.properties.MillBallProperty": ["(I)V"],
        "supersymmetry.api.unification.material.properties.DummyABSProperty": ["()V", "(I)V"],
        "supercritical.api.unification.material.properties.CoolantProperty": [
            "(Lgregtech/api/unification/material/Material;Lgregtech/api/unification/material/Material;Lgregtech/api/fluids/store/FluidStorageKey;DDDDD)V"
        ],
    })
    fuel = "supercritical.api.unification.material.properties.FissionFuelProperty"
    fuel_builder = fuel + "$FissionFuelPropertyBuilder"
    builder_descriptor = "L" + fuel_builder.replace(".", "/") + ";"
    policy["nativeMethods"].update({
        "net.minecraft.item.ItemStack": policy["nativeMethods"].get("net.minecraft.item.ItemStack", []) + [
            "multiply(Ljava/lang/Number;)Lcom/cleanroommc/groovyscript/api/IResourceStack;"
        ],
        "com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient": [
            "multiply(Ljava/lang/Number;)Lcom/cleanroommc/groovyscript/api/IResourceStack;"
        ],
        "supersymmetry.loaders.recipes.handlers.RecyclingManager": [
            "addRecyclingGroovy(Lnet/minecraft/item/ItemStack;Ljava/util/List;)V"
        ],
        fuel: ["builder(Ljava/lang/String;IID)" + builder_descriptor],
        fuel_builder: [name + "(" + descriptor + ")" + builder_descriptor for name, descriptor in (
            ("id", "Ljava/lang/String;"), ("maxTemperature", "I"), ("duration", "I"),
            ("slowNeutronCaptureCrossSection", "D"), ("fastNeutronCaptureCrossSection", "D"),
            ("slowNeutronFissionCrossSection", "D"), ("fastNeutronFissionCrossSection", "D"),
            ("neutronGenerationTime", "D"), ("releasedNeutrons", "D"), ("requiredNeutrons", "D"),
            ("releasedHeatEnergy", "D"), ("decayRate", "D"))] + ["build()L" + fuel.replace(".", "/") + ";"],
        "gregtech.api.unification.material.properties.BlastProperty": ["getBlastTemperature()I"],
        "supersymmetry.api.unification.material.properties.MillBallProperty": ["durability()I"],
        "supersymmetry.api.unification.material.properties.DummyABSProperty": [
            "getTemperature()I", "setTemperature(I)V"
        ],
        "supercritical.api.unification.material.properties.CoolantProperty": [
            "setAccumulatesHydrogen(Z)Lsupercritical/api/unification/material/properties/CoolantProperty;",
            "setSlowAbsorptionFactor(D)Lsupercritical/api/unification/material/properties/CoolantProperty;",
            "setFastAbsorptionFactor(D)Lsupercritical/api/unification/material/properties/CoolantProperty;",
        ],
    })
    policy["nativeExtensions"].update({
        "gregtech.api.unification.material.Material": [
            "gregtech/integration/groovy/MaterialPropertyExpansion#addIngot(Lgregtech/api/unification/material/Material;)V",
            "supersymmetry/integration/groovyscript/SuSyExpansions#setBaseProof(Lgregtech/api/unification/material/Material;Z)V"
        ],
        "java.lang.String": [
            "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/CharSequence;Ljava/lang/Object;)Ljava/lang/String;",
            "org/codehaus/groovy/runtime/StringGroovyMethods#plus(Ljava/lang/String;Ljava/lang/CharSequence;)Ljava/lang/String;"
        ],
        "gregtech.api.fluids.FluidBuilder": [
            "gregtech/integration/groovy/GroovyExpansions#acidic(Lgregtech/api/fluids/FluidBuilder;)Lgregtech/api/fluids/FluidBuilder;",
            "supersymmetry/integration/groovyscript/SuSyExpansions#basic(Lgregtech/api/fluids/FluidBuilder;)Lgregtech/api/fluids/FluidBuilder;"
        ]
    })
    policy["nativeReadFields"] = {"gregtech.api.fluids.FluidBuilder": ["temperature:I"]}
    _material_mutations(policy)
    _custom_items(policy)
    _recipe_mutations(policy)
    return (json.dumps(policy, indent=2) + "\n").encode()


def _recipe_mutations(policy):
    """Original recipe read/remove/rebuild calls; native dispatch owns effects."""
    recipe = "Lgregtech/api/recipes/Recipe;"
    builder = "Lgregtech/api/recipes/RecipeBuilder;"
    chance = "Lgregtech/api/recipes/chance/output/ChancedOutputList;"
    logic = "Lgregtech/api/recipes/chance/output/ChancedOutputLogic;"
    # The saved recycling helper uses this original private accessor. Groovy
    # owns its invocation; Axiom does not change native visibility or metaclasses.
    policy["nativePrivateMethods"] = {"gregtech.api.recipes.RecipeMap": [
        "getGroovyScriptRecipeMap()Lgregtech/integration/groovy/VirtualizedRecipeMap;",
    ]}
    policy["nativeMethods"]["gregtech.api.recipes.RecipeMap"].extend([
        "getRecipeList()Ljava/util/Collection;", "recipeBuilder()" + builder,
        "removeRecipe(" + recipe + ")Z",
    ])
    policy["nativeMethods"].update({
        "gregtech.api.recipes.Recipe": [
            "getInputs()Ljava/util/List;", "getOutputs()Lnet/minecraft/util/NonNullList;",
            "getFluidInputs()Ljava/util/List;", "getFluidOutputs()Ljava/util/List;",
            "getChancedOutputs()" + chance, "getChancedFluidOutputs()" + chance,
            "getDuration()I", "getEUt()I", "getPropertyRaw(Ljava/lang/String;)Ljava/lang/Object;",
        ],
        "gregtech.api.recipes.chance.output.ChancedOutputList": [
            "getChancedEntries()Ljava/util/List;", "getChancedOutputLogic()" + logic,
        ],
        "gregtech.api.unification.OreDictUnifier": [
            "getMaterial(Lnet/minecraft/item/ItemStack;)Lgregtech/api/unification/stack/MaterialStack;",
        ],
        "gregtech.api.recipes.builders.BlastRecipeBuilder": [
            name + "(" + args + ")" + builder for name, args in (
                ("inputIngredients", "Ljava/util/Collection;"),
                ("outputs", "[Lnet/minecraft/item/ItemStack;"), ("outputs", "Ljava/util/Collection;"),
                ("fluidInputs", "Ljava/util/Collection;"),
                ("fluidInputs", "Lgregtech/api/recipes/ingredients/GTRecipeInput;"),
                ("fluidInputs", "[Lnet/minecraftforge/fluids/FluidStack;"),
                ("fluidOutputs", "[Lnet/minecraftforge/fluids/FluidStack;"),
                ("fluidOutputs", "Ljava/util/Collection;"),
                ("chancedOutputs", "Ljava/util/List;"), ("chancedOutputLogic", logic),
                ("chancedFluidOutputs", "Ljava/util/List;"), ("chancedFluidOutputLogic", logic),
                ("duration", "I"), ("EUt", "I"),
            )
        ] + ["buildAndRegister()V", "blastFurnaceTemp(I)Lgregtech/api/recipes/builders/BlastRecipeBuilder;"],
    })
    # Thermodynamics reaches these four concrete native families. Preserve
    # inherited descriptors and each original build/validation implementation.
    susy_builder = "supersymmetry.api.recipes.builders.SusyRecipeBuilder"
    fluid_recipe_methods = [
        name + "(" + args + ")" + builder for name, args in (
            ("circuitMeta", "I"), ("duration", "I"), ("EUt", "I"),
            ("fluidInputs", "Ljava/util/Collection;"),
            ("fluidInputs", "Lgregtech/api/recipes/ingredients/GTRecipeInput;"),
            ("fluidInputs", "[Lnet/minecraftforge/fluids/FluidStack;"),
            ("fluidOutputs", "[Lnet/minecraftforge/fluids/FluidStack;"),
            ("fluidOutputs", "Ljava/util/Collection;"),
        )
    ] + ["buildAndRegister()V"]
    for owner in (susy_builder, "gregtech.api.recipes.builders.SimpleRecipeBuilder",
                  "gregtech.api.recipes.builders.FuelRecipeBuilder",
                  "supersymmetry.api.recipes.builders.NoEnergyRecipeBuilder"):
        policy["nativeMethods"][owner] = fluid_recipe_methods.copy()
        policy["nativeExtensions"][owner] = [
            "dev/tianmi/sussypatches/integration/grs/GroovyExpansions#info("
            + builder + "Ljava/lang/String;[Ljava/lang/Object;)" + builder,
        ]
    policy["nativeMethods"]["supersymmetry.api.recipes.builders.BathCondenserRecipeBuilder"] = fluid_recipe_methods.copy()
    policy["nativeMethods"][susy_builder].append("duration(I)L" + susy_builder.replace(".", "/") + ";")
    policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].extend([
        "notConsumable(" + args + ")" + builder for args in (
            "Lgregtech/api/recipes/ingredients/GTRecipeInput;", "Lnet/minecraft/item/ItemStack;",
            "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;I",
            "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;",
            "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;",
            "Lnet/minecraftforge/fluids/Fluid;I", "Lnet/minecraftforge/fluids/Fluid;",
            "Lnet/minecraftforge/fluids/FluidStack;", "Lcom/cleanroommc/groovyscript/api/IIngredient;",
        )
    ])
    # Saved postInit fermentation recipes add both native stacks and Groovy
    # ingredients; all overloads remain selected by the original metaclass.
    for owner in (susy_builder, "gregtech.api.recipes.builders.SimpleRecipeBuilder"):
        policy["nativeMethods"][owner].extend([
            name + "(" + args + ")" + builder for name, args in (
                ("inputs", "[Lnet/minecraft/item/ItemStack;"),
                ("inputs", "[Lgregtech/api/recipes/ingredients/GTRecipeInput;"),
                ("inputs", "Lcom/cleanroommc/groovyscript/api/IIngredient;"),
                ("inputs", "[Lcom/cleanroommc/groovyscript/api/IIngredient;"),
                ("inputs", "Ljava/util/Collection;"),
                ("outputs", "[Lnet/minecraft/item/ItemStack;"), ("outputs", "Ljava/util/Collection;"),
            )
        ])
    policy["nativeMethods"]["java.util.ArrayList"].append("contains(Ljava/lang/Object;)Z")
    # ProjectRed formats the saved loop index through the selected JDK Integer.
    policy["nativeMethods"]["java.lang.Integer"] = [
        "toString()Ljava/lang/String;", "toString(I)Ljava/lang/String;", "toString(II)Ljava/lang/String;",
    ]
    # Original numeric division in saved fluid quantities; operands remain
    # restricted to the exact native numeric types by MaterialCallGate.
    policy["numericOperations"].extend(["div", "intdiv", "mod", "leftShift"])
    policy["nativeMethods"]["java.lang.String"].extend([
        "replace(CC)Ljava/lang/String;", "replace(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Ljava/lang/String;",
        "split(Ljava/lang/String;)[Ljava/lang/String;", "split(Ljava/lang/String;I)[Ljava/lang/String;",
        "substring(I)Ljava/lang/String;", "substring(II)Ljava/lang/String;",
        "toUpperCase()Ljava/lang/String;", "toUpperCase(Ljava/util/Locale;)Ljava/lang/String;",
    ])
    policy["nativeExtensions"]["java.lang.String"].append(
        "org/codehaus/groovy/runtime/StringGroovyMethods#capitalize(Ljava/lang/CharSequence;)Ljava/lang/String;"
    )
    # The saved cupola duration calls the selected Number conversion extension.
    policy["nativeExtensions"]["java.lang.Double"] = [
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#toInteger(Ljava/lang/Number;)Ljava/lang/Integer;",
    ]
    # Groovy assigns Math.ceil's result through its original boxed integer cast.
    policy["referenceCasts"]["java.lang.Double"] = ["java.lang.Integer"]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.vanilla.Crafting"] = [
        operation + "(" + prefix + "Lnet/minecraft/item/ItemStack;Ljava/util/List;)V"
        for operation in ("addShapeless", "addShaped")
        for prefix in ("", "Ljava/lang/String;", "Lnet/minecraft/util/ResourceLocation;")
    ]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.vanilla.Furnace"] = [
        "removeByOutput(Lcom/cleanroommc/groovyscript/api/IIngredient;)Z",
        "removeByInput(Lcom/cleanroommc/groovyscript/api/IIngredient;)Z",
        "add(Lcom/cleanroommc/groovyscript/api/IIngredient;Lnet/minecraft/item/ItemStack;)V",
        "add(Lcom/cleanroommc/groovyscript/api/IIngredient;Lnet/minecraft/item/ItemStack;F)V",
        "add(Lcom/cleanroommc/groovyscript/api/IIngredient;Lnet/minecraft/item/ItemStack;FI)V",
        "add(Lcom/cleanroommc/groovyscript/compat/vanilla/Furnace$Recipe;)V",
    ]
    policy["nativeMethods"]["gregtech.api.recipes.machines.RecipeMapDistillationTower"] = [
        "recipeBuilder()" + builder,
    ]
    policy["nativeMethods"]["gregtech.api.recipes.machines.RecipeMapCrackerUnit"] = [
        "recipeBuilder()" + builder,
    ]
    policy["nativeMethods"]["gregtech.api.recipes.machines.RecipeMapFormingPress"] = [
        "recipeBuilder()" + builder,
    ]
    policy["nativeMethods"]["gregtech.api.recipes.machines.RecipeMapAssemblyLine"] = [
        "recipeBuilder()" + builder,
    ]
    policy["nativeMethods"]["gregtech.api.recipes.machines.RecipeMapFluidCanner"] = [
        "recipeBuilder()" + builder,
    ]
    policy["nativeMethods"]["supersymmetry.api.recipes.RecipeMapOreSorter"] = [
        "recipeBuilder()" + builder,
    ]
    policy["nativeMethods"]["gregtech.api.recipes.builders.UniversalDistillationRecipeBuilder"] = fluid_recipe_methods.copy()
    # The paper-chain roaster uses this native subclass. These inherited calls
    # retain their RecipeBuilder descriptors; catalyst-specific calls stay closed.
    policy["nativeMethods"]["supersymmetry.api.recipes.builders.CatalystRecipeBuilder"] = (
        policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    )
    policy["nativeExtensions"]["supersymmetry.api.recipes.builders.CatalystRecipeBuilder"] = (
        policy["nativeExtensions"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    )
    # Original sintering build() adds its default property before validation.
    policy["nativeMethods"]["supersymmetry.api.recipes.builders.SinteringRecipeBuilder"] = (
        policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    )
    policy["nativeMethods"]["gregtech.api.recipes.builders.AssemblerRecipeBuilder"] = (
        policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    )
    # ChemistryOverhaul's phase separator preserves the native primitive build
    # property. Chance outputs and the distillery toggle keep original validation.
    policy["nativeMethods"]["gregtech.api.recipes.builders.PrimitiveRecipeBuilder"] = (
        fluid_recipe_methods + ["outputs(" + args + ")" + builder for args in (
            "[Lnet/minecraft/item/ItemStack;", "Ljava/util/Collection;",
        )]
    )
    for owner in (susy_builder, "gregtech.api.recipes.builders.SimpleRecipeBuilder",
                  "supersymmetry.api.recipes.builders.CatalystRecipeBuilder",
                  "supersymmetry.api.recipes.builders.SinteringRecipeBuilder",
                  "gregtech.api.recipes.builders.UniversalDistillationRecipeBuilder"):
        policy["nativeMethods"][owner].extend([
            "chancedOutput(" + args + ")" + builder for args in (
                "Lnet/minecraft/item/ItemStack;II",
                "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;III",
                "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;II",
                "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;III",
                "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;II",
            )
        ])
    policy["nativeMethods"]["gregtech.api.recipes.builders.UniversalDistillationRecipeBuilder"].append(
        "disableDistilleryRecipes()Lgregtech/api/recipes/builders/UniversalDistillationRecipeBuilder;"
    )
    policy["nativeMethods"]["gregtech.api.recipes.builders.UniversalDistillationRecipeBuilder"].extend([
        "outputs(" + args + ")" + builder for args in (
            "[Lnet/minecraft/item/ItemStack;", "Ljava/util/Collection;",
        )
    ])
    policy["nativeMethods"]["gregtech.api.recipes.builders.BlastRecipeBuilder"].extend([
        signature for signature in policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith(("inputs(", "circuitMeta(", "chancedOutput("))
    ])
    evaporation = "supersymmetry.api.recipes.builders.EvaporationPoolRecipeBuilder"
    policy["nativeMethods"][evaporation] = (
        policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
        + ["EUt(I)L" + evaporation.replace(".", "/") + ";"]
    )
    settler = "supersymmetry.api.recipes.builders.MixerSettlerRecipeBuilder"
    policy["nativeMethods"][settler] = (
        policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
        + ["requiredCells(I)L" + settler.replace(".", "/") + ";"]
    )
    policy["nativeMethods"][susy_builder].extend([
        signature for signature in policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith("notConsumable(")
    ])
    policy["nativeMethods"]["gregtech.api.recipes.builders.PrimitiveRecipeBuilder"].extend([
        signature for signature in policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith(("inputs(", "notConsumable("))
    ])
    policy["nativeExtensions"]["gregtech.api.recipes.builders.PrimitiveRecipeBuilder"] = (
        policy["nativeExtensions"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    )
    # Copper beneficiation uses the original reverberatory furnace's primitive
    # build and the blast furnace's non-consumable pipe input.
    policy["nativeMethods"]["supersymmetry.api.recipes.builders.NoEnergyRecipeBuilder"].extend([
        signature for signature in policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith(("inputs(", "outputs(", "chancedOutput("))
    ])
    policy["nativeMethods"]["gregtech.api.recipes.builders.BlastRecipeBuilder"].extend([
        signature for signature in policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith("notConsumable(")
    ])
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.vanilla.Crafting"].extend([
        "removeByOutput(Lcom/cleanroommc/groovyscript/api/IIngredient;)V",
        "removeByOutput(Lcom/cleanroommc/groovyscript/api/IIngredient;Z)V",
        "removeByInput(Lcom/cleanroommc/groovyscript/api/IIngredient;)V",
        "removeByInput(Lcom/cleanroommc/groovyscript/api/IIngredient;Z)V",
        "remove(Ljava/lang/String;)V", "remove(Lnet/minecraft/util/ResourceLocation;)V",
        "remove(Lnet/minecraftforge/registries/IForgeRegistryEntry;)Z",
    ] + [name + "(" + prefix + "Lnet/minecraft/item/ItemStack;Ljava/util/List;)V"
         for name in ("replaceShaped", "replaceShapeless")
         for prefix in ("", "Ljava/lang/String;", "Lnet/minecraft/util/ResourceLocation;")])
    policy["listOperations"]["net.minecraft.util.NonNullList"] = ["collect"]
    # ERFTemps uses the original Groovy truth conversion of its selected range.
    policy["referenceCasts"]["java.util.ArrayList"] = ["boolean", "[I"]
    policy["nativeReadFields"]["gregtech.api.unification.stack.MaterialStack"] = [
        "material:Lgregtech/api/unification/material/Material;",
    ]
    policy["staticFields"].extend([
        "gregicality.multiblocks.api.recipes.GCYMRecipeMaps",
        "gregtechfoodoption.recipe.GTFORecipeMaps",
        "gregtech.api.gui.GuiTextures", "supersymmetry.api.gui.SusyGuiTextures",
        "java.lang.Integer",
    ])
    policy["nativeMethods"].update({
        "gregtech.api.recipes.GTRecipeHandler": [
            "removeAllRecipes(Lgregtech/api/recipes/RecipeMap;)V",
        ],
        "gregtech.core.unification.material.internal.MaterialRegistryManager": [
            "getRegisteredMaterials()Ljava/util/Collection;",
        ],
        "gregtech.integration.groovy.VirtualizedRecipeMap": [
            "removeByInput(JLjava/util/List;Ljava/util/List;)Z",
            "recipeBuilder()" + builder,
            "streamRecipes()Lcom/cleanroommc/groovyscript/helper/SimpleObjectStream;",
        ],
        "com.cleanroommc.groovyscript.helper.SimpleObjectStream": [
            "removeIf(Ljava/util/function/Predicate;)Z",
            "filter(Lgroovy/lang/Closure;)Lcom/cleanroommc/groovyscript/helper/SimpleObjectStream;",
            "removeAll()Lcom/cleanroommc/groovyscript/helper/SimpleObjectStream;",
            "removeAll(Ljava/util/Collection;)Z",
        ],
        "java.util.Collections": [
            "singletonList(Ljava/lang/Object;)Ljava/util/List;",
            "emptyList()Ljava/util/List;",
        ],
    })
    policy["nativeMethods"]["gregtech.api.recipes.RecipeMap"].extend([
        "findRecipe(JLnet/minecraftforge/items/IItemHandlerModifiable;Lgregtech/api/capability/IMultipleTankHandler;)" + recipe,
        "findRecipe(JLjava/util/List;Ljava/util/List;)" + recipe,
        "findRecipe(JLjava/util/List;Ljava/util/List;Z)" + recipe,
        "setSlotOverlay(ZZLgregtech/api/gui/resources/TextureArea;)Lgregtech/api/recipes/RecipeMap;",
        "setSlotOverlay(ZZZLgregtech/api/gui/resources/TextureArea;)Lgregtech/api/recipes/RecipeMap;",
    ])
    policy["nativeMethods"]["gregtech.api.unification.OreDictUnifier"].extend([
        "get(Lgregtech/api/unification/stack/UnificationEntry;)Lnet/minecraft/item/ItemStack;",
        "get(Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;)Lnet/minecraft/item/ItemStack;",
        "get(Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;I)Lnet/minecraft/item/ItemStack;",
        "get(Ljava/lang/String;)Lnet/minecraft/item/ItemStack;",
    ])
    for owner in ("gregtech.api.recipes.ingredients.GTRecipeItemInput",
                  "gregtech.api.recipes.ingredients.GTRecipeOreInput",
                  "gregtech.api.recipes.ingredients.IntCircuitIngredient"):
        policy["nativeMethods"][owner] = ["acceptsStack(Lnet/minecraft/item/ItemStack;)Z"]
    policy["nativeMethods"]["gregtech.api.recipes.ingredients.GTRecipeFluidInput"] = [
        "getInputFluidStack()Lnet/minecraftforge/fluids/FluidStack;",
    ]
    # Zinc's rotary kiln keeps its original alternative item input constructor.
    policy["constructors"]["gregtech.api.recipes.ingredients.GTRecipeItemInput"] = [
        "(" + argument + suffix + ")V"
        for argument in ("Lnet/minecraft/item/ItemStack;", "[Lnet/minecraft/item/ItemStack;",
                         "Lgregtech/api/recipes/ingredients/GTRecipeInput;")
        for suffix in ("", "I")
    ]
    policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].extend([
        "input(" + arguments + ")" + builder for arguments in (
            "Lgregtech/api/recipes/ingredients/GTRecipeInput;",
            "Ljava/lang/String;", "Ljava/lang/String;I",
            "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;",
            "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;I",
            "Lnet/minecraft/item/Item;", "Lnet/minecraft/item/Item;I",
            "Lnet/minecraft/item/Item;II", "Lnet/minecraft/item/Item;IZ",
            "Lnet/minecraft/block/Block;", "Lnet/minecraft/block/Block;I", "Lnet/minecraft/block/Block;IZ",
            "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;",
            "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;I",
            "Lgregtech/api/metatileentity/MetaTileEntity;", "Lgregtech/api/metatileentity/MetaTileEntity;I",
        )
    ])
    # Tungsten selects an entry; GregTech iterates copied native ore stacks.
    policy["nativeExtensions"].setdefault("com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient", []).extend([
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#first(Ljava/lang/Iterable;)Ljava/lang/Object;",
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#each(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;",
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#each(Ljava/lang/Iterable;Lgroovy/lang/Closure;)Ljava/lang/Iterable;",
    ])
    policy["listOperations"]["it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap"] = ["each", "iterator"]
    policy["nativeMethods"].setdefault("it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap", []).extend([
        "size()I",
        "put(Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
        "containsKey(Ljava/lang/Object;)Z",
        "values()Ljava/util/Collection;", "values()Lit/unimi/dsi/fastutil/objects/ObjectCollection;",
        "entrySet()Ljava/util/Set;", "entrySet()Lit/unimi/dsi/fastutil/objects/ObjectSet;",
        "entrySet()Lit/unimi/dsi/fastutil/objects/ObjectSortedSet;",
    ])
    policy["listOperations"]["it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap$1"] = ["iterator"]
    policy["nativeMethods"]["it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap$MapEntry"] = [
        "getKey()Ljava/lang/Object;", "getValue()Ljava/lang/Object;",
    ]
    policy["nativeExtensions"]["it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap$MapEntrySet"] = [
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#toList(Ljava/lang/Iterable;)Ljava/util/List;",
    ]
    policy["nativeExtensions"]["java.util.ArrayList"] = [
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#leftShift(Ljava/util/Collection;Ljava/lang/Object;)Ljava/util/Collection;",
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#leftShift(Ljava/util/List;Ljava/lang/Object;)Ljava/util/List;",
    ]
    policy["listOperations"]["java.util.Collections$UnmodifiableCollection"] = ["forEach"]
    policy["listOperations"]["java.util.Collections$EmptyList"] = ["iterator"]
    policy["listOperations"]["java.util.Arrays$ArrayList"] = ["iterator"]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.mods.jei.Ingredient"] = [
        "yeet(" + argument + ")V" for argument in (
            "Lcom/cleanroommc/groovyscript/api/IIngredient;",
            "[Lcom/cleanroommc/groovyscript/api/IIngredient;", "Ljava/lang/Iterable;",
        )
    ]
    policy["nativeMethods"]["groovy.lang.IntRange"] = ["iterator()Ljava/util/Iterator;"]
    policy["listOperations"]["java.util.ArrayList"].extend(["plus", "iterator", "flatten", "forEach", "join", "any"])
    policy["nativeMethods"]["java.util.ArrayList"].extend([
        "indexOf(Ljava/lang/Object;)I",
        "addAll(Ljava/util/Collection;)Z", "addAll(ILjava/util/Collection;)Z",
    ])
    policy["nativeExtensions"].setdefault("java.util.ArrayList", []).extend([
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#" + signature for signature in (
            "multiply(Ljava/lang/Iterable;Ljava/lang/Number;)Ljava/util/Collection;",
            "multiply(Ljava/util/List;Ljava/lang/Number;)Ljava/util/List;",
            "combinations(Ljava/lang/Iterable;)Ljava/util/List;",
            "combinations(Ljava/lang/Iterable;Lgroovy/lang/Closure;)Ljava/util/List;",
        )
    ])
    policy["listOperations"]["[Lnet.minecraft.item.ItemStack;"] = ["getAt", "iterator"]
    policy["listOperations"]["[Lgregtech.api.unification.material.Material;"] = ["getAt", "length"]
    policy["listOperations"]["groovy.lang.IntRange"] = ["iterator", "collect"]
    policy["mapTypes"].append("java.util.EnumMap")
    policy["staticFields"].extend([
        "supersymmetry.common.blocks.SuSyBlocks", "gregtech.common.blocks.MetaBlocks",
        "supersymmetry.common.blocks.SusyStoneVariantBlock$StoneVariant",
        "gregtech.common.blocks.StoneVariantBlock$StoneVariant",
    ])
    for owner in ("supersymmetry.common.blocks.SusyStoneVariantBlock", "gregtech.common.blocks.StoneVariantBlock"):
        policy["nativeMethods"][owner] = [
            "getItemVariant(Ljava/lang/Enum;)Lnet/minecraft/item/ItemStack;",
            "getItemVariant(Ljava/lang/Enum;I)Lnet/minecraft/item/ItemStack;",
        ]
        enum = owner + "$StoneType"
        policy["staticFields"].append(enum)
        policy["nativeMethods"][enum] = ["values()[L" + enum.replace(".", "/") + ";"]
        policy["listOperations"]["[L" + enum + ";"] = ["iterator"]
    policy["nativeMethods"]["net.minecraftforge.oredict.OreDictionary"] = [
        "registerOre(Ljava/lang/String;L" + owner + ";)V" for owner in (
            "net/minecraft/item/Item", "net/minecraft/block/Block", "net/minecraft/item/ItemStack",
        )
    ]
    policy["nativeMethods"]["net.minecraft.item.ItemStack"].extend([
        "reuse()Lnet/minecraft/item/ItemStack;", "noReturn()Lnet/minecraft/item/ItemStack;",
        "transform(Lcom/cleanroommc/groovyscript/compat/vanilla/ItemStackTransformer;)Lnet/minecraft/item/ItemStack;",
        "transform(Lnet/minecraft/item/ItemStack;)Lnet/minecraft/item/ItemStack;",
        "withNbt(Lnet/minecraft/nbt/NBTTagCompound;)Lcom/cleanroommc/groovyscript/api/INBTResourceStack;",
        "withNbt(Ljava/util/Map;)Lcom/cleanroommc/groovyscript/api/INBTResourceStack;",
    ])
    policy["nativeMethods"]["net.minecraftforge.fluids.FluidStack"] = [
        "multiply(Ljava/lang/Number;)Lcom/cleanroommc/groovyscript/api/IResourceStack;",
        "getFluid()Lnet/minecraftforge/fluids/Fluid;",
    ]
    for owner in ("net.minecraft.item.ItemStack", "com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient"):
        policy["nativeMethods"][owner].extend([
            "withAmount(I)Lcom/cleanroommc/groovyscript/api/IIngredient;",
            "withAmount(I)Lcom/cleanroommc/groovyscript/api/IResourceStack;",
            "getAmount()I", "getMatchingStacks()[Lnet/minecraft/item/ItemStack;", "isEmpty()Z",
        ])
    policy["nativeMethods"]["com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient"].extend([
        operation + "(" + argument + ")V" for operation in ("add", "remove") for argument in (
            "Lnet/minecraft/item/ItemStack;", "[Lnet/minecraft/item/ItemStack;", "Ljava/lang/Iterable;",
        )
    ] + ["add(Lcom/cleanroommc/groovyscript/helper/ingredient/OreDictIngredient;)V"])
    _recipe_recycling(policy)
    _recipe_builder_properties(policy)
    _recipe_crafting(policy)
    policy["nativeMethods"]["dev.tianmi.sussypatches.common.helper.DimDisplayRegistry"] = [
        "setDisplayItem(ILnet/minecraft/item/ItemStack;)V",
    ]
    # Invasions registers the original suppliers and modifiers for later game
    # events. These constructors/setters store them without spawning entities.
    policy["compilerStaticCalls"].append("java/lang/Byte#valueOf(B)Ljava/lang/Byte;")
    policy["compilerStaticFields"].append("java/lang/Byte#TYPE:Ljava/lang/Class;")
    horde = "supersymmetry.api.event.MobHordeEvent"
    horde_result = "L" + horde.replace(".", "/") + ";"
    policy["constructors"][horde] = [
        "(Ljava/util/function/Function;IILjava/lang/String;" + tail + ")V" for tail in ("", "I")
    ]
    policy["nativeMethods"][horde] = [
        name + "(" + args + ")" + horde_result for name, args in (
            ("setNightOnly", "Z"), ("setTimer", "II"), ("setCanUsePods", "Z"),
            ("setPostSpawnModifier", "Ljava/util/function/Function;"),
            ("triggerOnAdvancement", "Lnet/minecraft/util/ResourceLocation;"),
            ("runOnce", ""), ("minHate", "Ljava/lang/String;I"),
            ("addPattern", "Ljava/util/function/Function;Ljava/util/List;Ljava/util/function/Function;Ljava/util/function/Function;"),
            ("setDistribution", "[Ljava/lang/Double;"), ("setExactDistribution", "[Ljava/lang/Integer;"),
            ("runCommandOnLanding", "[Ljava/lang/String;"),
        )
    ] + ["baseline(Lnet/minecraft/util/ResourceLocation;I)V"]
    policy["nativeMethods"]["java.util.Arrays"] = ["asList([Ljava/lang/Object;)Ljava/util/List;"]
    policy["nativeReadFields"]["java.lang.System"] = ["out:Ljava/io/PrintStream;"]
    policy["nativeMethods"]["java.io.PrintStream"] = [
        "println(" + argument + ")V" for argument in ("", "Z", "C", "I", "J", "F", "D", "[C", "Ljava/lang/String;", "Ljava/lang/Object;")
    ]
    policy["nativeMethods"]["net.minecraftforge.fml.common.TracingPrintStream"] = list(policy["nativeMethods"]["java.io.PrintStream"])
    policy["nativeMetaClassMethods"] = {
        "supersymmetry.common.tileentities.TileEntityFlare": ["callGroovySpawn"],
    }
    policy["nativeMethods"].update({
        "appeng.api.AEApi": ["instance()Lappeng/api/IAppEngApi;"],
        "appeng.core.Api": ["registries()Lappeng/api/features/IRegistryContainer;"],
        "appeng.core.features.registries.RegistryContainer": ["grinder()Lappeng/api/features/IGrinderRegistry;"],
        "appeng.core.features.registries.grinder.GrinderRecipeManager": [
            "builder()Lappeng/api/features/IGrinderRecipeBuilder;",
            "addRecipe(Lappeng/api/features/IGrinderRecipe;)Z",
        ],
        "appeng.core.features.registries.grinder.GrinderRecipeManager$Builder": [
            "withInput(Lnet/minecraft/item/ItemStack;)Lappeng/api/features/IGrinderRecipeBuilder;",
            "withOutput(Lnet/minecraft/item/ItemStack;)Lappeng/api/features/IGrinderRecipeBuilder;",
            "withTurns(I)Lappeng/api/features/IGrinderRecipeBuilder;",
            "build()Lappeng/api/features/IGrinderRecipe;",
        ],
    })
    policy["nativeExtensions"]["gregtech.api.unification.material.Material"].append(
        "gregtech/integration/groovy/MaterialExpansion#oreMultiplier(Lgregtech/api/unification/material/Material;)I"
    )
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.mods.chisel.Carving"] = [
        "addGroup(Ljava/lang/String;)V", "addVariation(Ljava/lang/String;Lnet/minecraft/item/ItemStack;)V",
    ]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.vanilla.OreDict"] = [
        "getItems(Ljava/lang/String;)Ljava/util/List;",
        "remove(Ljava/lang/String;Lnet/minecraft/item/ItemStack;)Z",
    ]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.mods.jei.Ingredient"].extend([
        "hide(" + args + ")V" for args in (
            "Lmezz/jei/api/recipe/IIngredientType;Ljava/util/Collection;",
            "Lmezz/jei/api/recipe/IIngredientType;[Ljava/lang/Object;",
            "Lcom/cleanroommc/groovyscript/api/IIngredient;",
            "[Lcom/cleanroommc/groovyscript/api/IIngredient;", "Ljava/lang/Iterable;",
        )
    ])
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.mods.jei.Catalyst"] = [
        operation + "(Ljava/lang/String;" + argument + ")V"
        for operation in ("add", "remove") for argument in (
            "Lnet/minecraft/item/ItemStack;", "[Lnet/minecraft/item/ItemStack;", "Ljava/util/Collection;",
        )
    ]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.compat.mods.jei.Category"] = ["hideCategory(Ljava/lang/String;)V"]
    _pyrotech_recipes(policy)
    policy["nativeMethods"]["java.lang.Math"] = [
        "max(" + kind + kind + ")" + kind for kind in ("I", "J", "F", "D")
    ] + ["ceil(D)D"]
    policy["nativeMethods"]["com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient"].extend([
        "getAt(I)Lnet/minecraft/item/ItemStack;", "getAt(Lgroovy/lang/IntRange;)Ljava/lang/Iterable;",
        "getFirst()Lnet/minecraft/item/ItemStack;",
    ])
    policy["nativeMethods"]["gregtech.api.util.GTUtility"] = ["toLowerCaseUnderscore(Ljava/lang/String;)Ljava/lang/String;"]
    policy["nativeMethods"]["gregtechfoodoption.utils.GTFOUtils"] = [
        "getFish()Ljava/util/List;", "getMeat()Ljava/util/List;",
        "addBakingOvenRecipes(Lnet/minecraft/item/ItemStack;Lnet/minecraft/item/ItemStack;III)V",
    ]
    policy["nativeMethods"]["gregtechfoodoption.item.GTFOMetaItem$GTFOMetaValueItem"] = [
        "getStackForm()Lnet/minecraft/item/ItemStack;", "getStackForm(I)Lnet/minecraft/item/ItemStack;",
    ]
    policy["nativeReadFields"]["gregtechfoodoption.item.GTFOMetaItem"] = [
        state + "_" + shape + ":Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;"
        for shape in ("DITALINI", "RIGATONI", "LASAGNA", "SPAGHETTI", "TAGLIATELLE")
        for state in ("RAW", "DRIED")
    ]
    # Saved rolling-stock recipes construct original UMC wrappers and NBT.
    policy["constructors"]["cam72cam.mod.serialization.TagCompound"] = ["()V", "(Lnet/minecraft/nbt/NBTTagCompound;)V", "([B)V"]
    policy["constructors"]["cam72cam.mod.item.ItemStack"] = [
        "(Lcam72cam/mod/item/CustomItem;I)V", "(Lnet/minecraft/item/ItemStack;)V",
        "(Lcam72cam/mod/serialization/TagCompound;)V", "(Ljava/lang/String;II)V",
    ]
    policy["nativeMethods"]["cam72cam.mod.serialization.TagCompound"] = [
        "setString(Ljava/lang/String;Ljava/lang/String;)Lcam72cam/mod/serialization/TagCompound;",
        "setFloat(Ljava/lang/String;Ljava/lang/Float;)Lcam72cam/mod/serialization/TagCompound;",
    ]
    policy["nativeMethods"]["cam72cam.mod.item.ItemStack"] = ["setTagCompound(Lcam72cam/mod/serialization/TagCompound;)V"]
    policy["nativeReadFields"]["cam72cam.mod.item.ItemStack"] = ["internal:Lnet/minecraft/item/ItemStack;"]
    policy["nativeReadFields"]["cam72cam.immersiverailroading.IRItems"] = ["ITEM_ROLLING_STOCK:Lcam72cam/immersiverailroading/items/ItemRollingStock;"]
    policy["nativeReadFields"]["trackapi.lib.Gauges"] = ["STANDARD:D"]
    policy["compilerStaticFields"].append("java/lang/Float#TYPE:Ljava/lang/Class;")
    # Latex recipes read existing log-state containers through original Block.
    for owner in ("net.minecraft.block.Block", "net.minecraft.block.BlockOldLog",
                  "net.minecraft.block.BlockNewLog", "gregtech.common.blocks.wood.BlockRubberLog"):
        policy["nativeMethods"][owner] = ["func_176194_O()Lnet/minecraft/block/state/BlockStateContainer;"]
    policy["nativeMethodMappings"]["net.minecraft.block.Block"] = {"getBlockState": "func_176194_O"}
    policy["nativeReadFields"]["net.minecraft.init.Blocks"] = [
        "LOG:Lnet/minecraft/block/Block;", "LOG2:Lnet/minecraft/block/Block;",
        "NETHERRACK:Lnet/minecraft/block/Block;",
    ]
    # Vanilla's saved hardness assignment uses original mapped field metadata.
    policy["nativeWriteFields"] = {
        owner: ["blockHardness:F"]
        for owner in ("net.minecraft.block.Block", "net.minecraft.block.BlockNetherrack")
    }
    policy["nativeReadFields"]["gregtechfoodoption.worldgen.trees.GTFOTrees"] = [
        "RAINBOWWOOD_TREE:Lgregtechfoodoption/worldgen/trees/RainbowwoodTree;",
    ]
    for owner in ("gregtechfoodoption.worldgen.trees.GTFOTree", "gregtechfoodoption.worldgen.trees.RainbowwoodTree"):
        policy["nativeReadFields"][owner] = ["logState:Lnet/minecraft/block/state/IBlockState;"]
    policy["nativeReadFields"]["net.minecraft.block.BlockLog"] = ["LOG_AXIS:Lnet/minecraft/block/properties/PropertyEnum;"]
    policy["staticFields"].append("net.minecraft.block.BlockLog$EnumAxis")
    state="net.minecraft.block.state.BlockStateContainer$StateImplementation"
    policy["nativeMethods"][state] = ["func_177226_a(Lnet/minecraft/block/properties/IProperty;Ljava/lang/Comparable;)Lnet/minecraft/block/state/IBlockState;"]
    policy["nativeMethodMappings"][state] = {"withProperty": "func_177226_a"}
    # RubberChain runs original DGM.tap callbacks against these native builders.
    for owner in ("supersymmetry.api.recipes.builders.CatalystRecipeBuilder",
                  "gregtech.api.recipes.builders.SimpleRecipeBuilder",
                  "gregtech.api.recipes.builders.PrimitiveRecipeBuilder"):
        policy["nativeExtensions"][owner].append(
            "org/codehaus/groovy/runtime/DefaultGroovyMethods#tap(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;"
        )


def _recipe_crafting(policy):
    """Original crafting builders and powered-tool capacity registration."""
    native = policy["nativeMethods"]
    owner = "com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipeBuilder"
    base = "Lcom/cleanroommc/groovyscript/registry/AbstractCraftingRecipeBuilder"
    native["com.cleanroommc.groovyscript.compat.vanilla.Crafting"].extend([
        name + "()L" + owner.replace(".", "/") + "$" + kind + ";"
        for name, kind in (("shapedBuilder", "Shaped"), ("shapelessBuilder", "Shapeless"))
    ])
    common = [name + "(" + args + ")" + base + ";" for name, args in (
        ("name", "Ljava/lang/String;"), ("name", "Lnet/minecraft/util/ResourceLocation;"),
        ("output", "Lnet/minecraft/item/ItemStack;"), ("recipeFunction", "Lgroovy/lang/Closure;"),
        ("replace", ""), ("replaceByName", ""),
    )] + ["register()Lnet/minecraft/item/crafting/IRecipe;", "register()Ljava/lang/Object;"]
    native[owner + "$Shaped"] = common + [
        name + "(" + args + ")" + base + "$AbstractShaped;" for name, args in (
            ("shape", "Ljava/util/List;"), ("shape", "[Ljava/lang/String;"),
            ("matrix", "Ljava/util/List;"), ("matrix", "[Ljava/lang/String;"),
            ("row", "Ljava/lang/String;"),
            ("key", "CLcom/cleanroommc/groovyscript/api/IIngredient;"),
            ("key", "Ljava/lang/String;Lcom/cleanroommc/groovyscript/api/IIngredient;"),
            ("key", "Ljava/util/Map;"),
        )
    ]
    native[owner + "$Shapeless"] = common + [
        "input(" + args + ")" + base + "$AbstractShapeless;" for args in (
            "Lcom/cleanroommc/groovyscript/api/IIngredient;",
            "[Lcom/cleanroommc/groovyscript/api/IIngredient;", "Ljava/util/Collection;",
        )
    ]
    policy["staticFields"].append("gregtech.api.capability.GregtechCapabilities")
    native["net.minecraft.item.ItemStack"].extend([
        "getCapability(Lnet/minecraftforge/common/capabilities/Capability;Lnet/minecraft/util/EnumFacing;)Ljava/lang/Object;",
        "mark(Ljava/lang/String;)Lcom/cleanroommc/groovyscript/api/IMarkable;",
    ])
    native["gregtech.api.capability.impl.ElectricItem"] = ["setMaxChargeOverride(J)V", "getMaxCharge()J"]
    native["java.lang.String"].append("replaceAll(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;")
    policy["nativeExtensions"]["it.unimi.dsi.fastutil.objects.Object2ObjectLinkedOpenHashMap"] = [
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#get(Ljava/util/Map;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/lang/Object;Ljava/lang/String;Ljava/lang/Object;)V",
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#putAt(Ljava/util/Map;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;",
    ]
    policy["constructors"]["net.minecraft.nbt.NBTTagCompound"] = ["()V"]
    policy["nativeMethodMappings"]["net.minecraft.item.ItemStack"] = {
        "copy": "func_77946_l", "getTagCompound": "func_77978_p", "setTagCompound": "func_77982_d",
        "getMetadata": "func_77960_j", "getItem": "func_77973_b",
    }
    native["net.minecraft.item.ItemStack"].extend([
        "func_77946_l()Lnet/minecraft/item/ItemStack;",
        "func_77978_p()Lnet/minecraft/nbt/NBTTagCompound;",
        "func_77982_d(Lnet/minecraft/nbt/NBTTagCompound;)V",
        "func_77960_j()I",
        "func_77973_b()Lnet/minecraft/item/Item;",
    ])
    # MiscRecipes changes the original BOP mudball's inherited Item stack limit.
    native["biomesoplenty.common.item.ItemMudball"] = ["func_77625_d(I)Lnet/minecraft/item/Item;"]
    native["com.codetaylor.mc.pyrotech.modules.tech.machine.item.ItemCog"] = ["func_77625_d(I)Lnet/minecraft/item/Item;"]
    policy["nativeMethodMappings"]["net.minecraft.item.Item"] = {"setMaxStackSize": "func_77625_d"}
    policy["nativeMethodMappings"]["net.minecraft.nbt.NBTTagCompound"] = {
        "getLong": "func_74763_f", "setLong": "func_74772_a", "getCompoundTag": "func_74775_l",
        "setString": "func_74778_a", "setTag": "func_74782_a", "setIntArray": "func_74783_a",
    }
    native["net.minecraft.nbt.NBTTagCompound"] = [
        "func_74763_f(Ljava/lang/String;)J", "func_74772_a(Ljava/lang/String;J)V",
        "func_74775_l(Ljava/lang/String;)Lnet/minecraft/nbt/NBTTagCompound;",
        "func_74778_a(Ljava/lang/String;Ljava/lang/String;)V",
        "func_74782_a(Ljava/lang/String;Lnet/minecraft/nbt/NBTBase;)V",
        "func_74783_a(Ljava/lang/String;[I)V",
    ]
    policy["nativeExtensions"]["net.minecraft.nbt.NBTTagCompound"] = [
        "org/codehaus/groovy/runtime/DefaultGroovyMethods#tap(Ljava/lang/Object;Lgroovy/lang/Closure;)Ljava/lang/Object;"
    ]
    for ingredient in ("net.minecraft.item.ItemStack",
                       "com.cleanroommc.groovyscript.helper.ingredient.OreDictIngredient",
                       "com.cleanroommc.groovyscript.helper.ingredient.OrIngredient"):
        native.setdefault(ingredient, []).extend([
            "or(Lcom/cleanroommc/groovyscript/api/IIngredient;)Lcom/cleanroommc/groovyscript/api/IIngredient;",
            "or(Ljava/util/function/Predicate;)Ljava/util/function/Predicate;",
        ])
    native["gregtech.api.recipes.builders.AssemblerRecipeBuilder"].extend([
        signature for signature in native["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith("input(")
    ])
    nbt = "Lgregtech/api/recipes/ingredients/nbtmatch/"
    for kind in ("NBTMatcher", "NBTCondition"):
        policy["nativeReadFields"]["gregtech.api.recipes.ingredients.nbtmatch." + kind] = ["ANY:" + nbt + kind + ";"]
    # OpenComputers and OpenModularTurrets use the original inputNBT family. Each overload
    # delegates to the native input/condition checks and stores the same objects.
    nbt_inputs = [
        "inputNBT(" + args + nbt + "NBTMatcher;" + nbt + "NBTCondition;)Lgregtech/api/recipes/RecipeBuilder;"
        for args in (
            "Lgregtech/api/recipes/ingredients/GTRecipeInput;",
            "Ljava/lang/String;", "Ljava/lang/String;I",
            "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;",
            "Lgregtech/api/unification/ore/OrePrefix;Lgregtech/api/unification/material/Material;I",
            "Lnet/minecraft/item/Item;", "Lnet/minecraft/item/Item;I", "Lnet/minecraft/item/Item;II",
            "Lnet/minecraft/item/Item;IZ", "Lnet/minecraft/block/Block;", "Lnet/minecraft/block/Block;I",
            "Lnet/minecraft/block/Block;IZ", "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;I",
            "Lgregtech/api/items/metaitem/MetaItem$MetaValueItem;",
            "Lgregtech/api/metatileentity/MetaTileEntity;", "Lgregtech/api/metatileentity/MetaTileEntity;I",
            "Lnet/minecraft/item/ItemStack;",
        )
    ]
    for owner in ("gregtech.api.recipes.builders.AssemblerRecipeBuilder",
                  "gregtech.api.recipes.builders.SimpleRecipeBuilder"):
        native[owner].extend(nbt_inputs)
    native["gregtech.api.recipes.builders.FuelRecipeBuilder"].extend([
        signature for signature in native["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
        if signature.startswith("inputs(")
    ])
    policy["nativeExtensions"]["gregtech.api.recipes.builders.AssemblerRecipeBuilder"] = (
        policy["nativeExtensions"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    )


def _recipe_builder_properties(policy):
    """Shared native builder families in saved component and furnace recipes."""
    simple = policy["nativeMethods"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"]
    simple.append("chancedFluidOutput(Lnet/minecraftforge/fluids/FluidStack;II)Lgregtech/api/recipes/RecipeBuilder;")
    circuit = "gregtech.api.recipes.builders.CircuitAssemblerRecipeBuilder"
    resistance = "supersymmetry.api.recipes.builders.ResistanceFurnaceRecipeBuilder"
    common = ("inputs(", "outputs(", "fluidInputs(", "fluidOutputs(", "notConsumable(",
              "circuitMeta(", "duration(", "EUt(", "buildAndRegister(")
    for owner in (circuit, resistance):
        policy["nativeMethods"][owner] = [signature for signature in simple if signature.startswith(common)]
    # Motors and the saved GregTech integration use these remaining original
    # families. Research generation and property validation stay native.
    for owner, names in (
        ("gregtech.api.recipes.builders.AssemblyLineRecipeBuilder",
         ("inputs(", "outputs(", "fluidInputs(", "duration(", "EUt(", "buildAndRegister(")),
        ("gregtech.api.recipes.builders.GasCollectorRecipeBuilder",
         ("fluidOutputs(", "duration(", "EUt(", "circuitMeta(", "buildAndRegister(")),
        ("supersymmetry.api.recipes.builders.PseudoMultiRecipeBuilder",
         ("fluidOutputs(", "duration(", "EUt(", "notConsumable(", "buildAndRegister(")),
    ):
        policy["nativeMethods"][owner] = [signature for signature in simple if signature.startswith(names)]
    # Bind the complete original overload family. The native call gate limits
    # this operation to its ItemStack operand; research builder callbacks have
    # a separate surface. Original configuration/property/scanner generation
    # still executes in AssemblyLineRecipeBuilder and AssemblyLineManager.
    assembly = "gregtech.api.recipes.builders.AssemblyLineRecipeBuilder"
    policy["nativeMethods"][assembly].extend([
        "scannerResearch(" + operand + ")L" + assembly.replace(".", "/") + ";"
        for operand in ("Lnet/minecraft/item/ItemStack;", "Ljava/util/function/UnaryOperator;")
    ])
    for owner in ("gregtech.api.recipes.builders.GasCollectorRecipeBuilder",
                  "supersymmetry.api.recipes.builders.SusyRecipeBuilder"):
        policy["nativeMethods"][owner].append("dimension(I)L" + owner.replace(".", "/") + ";")
    policy["nativeMethods"]["supersymmetry.api.recipes.builders.SusyRecipeBuilder"].append(
        "biomes([Ljava/lang/String;)Lsupersymmetry/api/recipes/builders/SusyRecipeBuilder;"
    )
    pseudo = "supersymmetry.api.recipes.builders.PseudoMultiRecipeBuilder"
    policy["nativeMethods"][pseudo].extend([
        "blockStates(Ljava/lang/String;[Lnet/minecraft/block/state/" + state + ";)L" + pseudo.replace(".", "/") + ";"
        for state in ("IBlockState", "BlockStateContainer")
    ])
    policy["nativeExtensions"][pseudo] = policy["nativeExtensions"]["gregtech.api.recipes.builders.SimpleRecipeBuilder"].copy()
    policy["nativeMethods"]["supersymmetry.api.recipes.builders.BathCondenserRecipeBuilder"].extend([
        signature for signature in simple if signature.startswith("notConsumable(")
    ])
    policy["nativeMethods"][resistance].append(
        "temperature(I)Lsupersymmetry/api/recipes/builders/ResistanceFurnaceRecipeBuilder;"
    )
    # Original cleanroom() honors ConfigHolder.machines.enableCleanroom and
    # stores the selected native type. No property or config is synthesized.
    policy["staticFields"].append("gregtech.api.metatileentity.multiblock.CleanroomType")
    for owner in ("gregtech.api.recipes.builders.SimpleRecipeBuilder",
                  "gregtech.api.recipes.builders.AssemblerRecipeBuilder",
                  "supersymmetry.api.recipes.builders.CatalystRecipeBuilder", circuit):
        policy["nativeMethods"][owner].append(
            "cleanroom(Lgregtech/api/metatileentity/multiblock/CleanroomType;)Lgregtech/api/recipes/RecipeBuilder;"
        )


def _recipe_recycling(policy):
    """Selected native collection/value operations used by RecyclingHelper."""
    amounts = "it.unimi.dsi.fastutil.objects.Object2LongOpenHashMap"
    policy["constructors"][amounts] = [
        "()V", "(I)V", "(IF)V", "(Ljava/util/Map;)V", "(Ljava/util/Map;F)V",
        "(Lit/unimi/dsi/fastutil/objects/Object2LongMap;)V",
        "(Lit/unimi/dsi/fastutil/objects/Object2LongMap;F)V",
        "([Ljava/lang/Object;[J)V", "([Ljava/lang/Object;[JF)V",
    ]
    policy["constructors"]["gregtech.api.unification.stack.MaterialStack"] = [
        "(Lgregtech/api/unification/material/Material;J)V",
    ]
    policy["nativeMethods"][amounts] = [
        name + "(" + args + ")" + result
        for name in ("getOrDefault", "put")
        for args, result in (("Ljava/lang/Object;J", "J"),
                             ("Ljava/lang/Object;Ljava/lang/Long;", "Ljava/lang/Long;"),
                             ("Ljava/lang/Object;Ljava/lang/Object;", "Ljava/lang/Object;"))
    ] + ["entrySet()Lit/unimi/dsi/fastutil/objects/ObjectSet;", "entrySet()Ljava/util/Set;"]
    policy["nativeMethods"][amounts + "$MapEntrySet"] = ["stream()Ljava/util/stream/Stream;"]
    policy["nativeMethods"][amounts + "$MapEntry"] = [
        "getKey()Ljava/lang/Object;", "getValue()Ljava/lang/Long;", "getValue()Ljava/lang/Object;",
    ]
    policy["nativeMethods"]["gregtech.api.unification.OreDictUnifier"].extend([
        "getMaterialInfo(Lnet/minecraft/item/ItemStack;)Lgregtech/api/unification/stack/ItemMaterialInfo;",
        "getPrefix(Lnet/minecraft/item/ItemStack;)Lgregtech/api/unification/ore/OrePrefix;",
    ])
    policy["nativeMethods"]["gregtech.api.unification.stack.ItemMaterialInfo"] = [
        "getMaterials()Lcom/google/common/collect/ImmutableList;",
    ]
    policy["nativeReadFields"]["gregtech.api.unification.stack.MaterialStack"].append("amount:J")
    policy["nativeReadFields"]["gregtech.api.unification.ore.OrePrefix"] = ["secondaryMaterials:Ljava/util/List;"]
    policy["nativeMethods"]["java.util.ArrayList"].append("isEmpty()Z")
    for owner in ("com.google.common.collect.RegularImmutableList", "com.google.common.collect.SingletonImmutableList"):
        policy["listOperations"][owner] = ["iterator"]
    policy["nativeMethods"]["java.util.stream.ReferencePipeline$Head"] = [
        "map(Ljava/util/function/Function;)Ljava/util/stream/Stream;",
    ]
    policy["nativeMethods"]["java.util.stream.ReferencePipeline$3"] = [
        "sorted()Ljava/util/stream/Stream;", "sorted(Ljava/util/Comparator;)Ljava/util/stream/Stream;",
    ]
    policy["nativeMethods"]["java.util.stream.SortedOps$OfRef"] = [
        "collect(Ljava/util/stream/Collector;)Ljava/lang/Object;",
        "collect(Ljava/util/function/Supplier;Ljava/util/function/BiConsumer;Ljava/util/function/BiConsumer;)Ljava/lang/Object;",
    ]
    policy["nativeMethods"]["java.util.Comparator"] = [
        "comparingLong(Ljava/util/function/ToLongFunction;)Ljava/util/Comparator;",
    ]
    policy["nativeMethods"]["java.util.stream.Collectors"] = ["toList()Ljava/util/stream/Collector;"]
    policy["nativeMethods"]["gregtech.loaders.recipe.RecyclingRecipes"] = [
        "registerRecyclingRecipes(Lnet/minecraft/item/ItemStack;Ljava/util/List;ZLgregtech/api/unification/ore/OrePrefix;)V",
    ]


def _custom_items(policy):
    """Pack component on the common native GT item family; no item-name rules."""
    policy["constructors"]["gregtech.integration.baubles.BaubleBehavior"] = ["(Lbaubles/api/BaubleType;)V"]
    policy["staticFields"].append("baubles.api.BaubleType")


def _material_mutations(policy):
    """Selected native descriptor families, not implementations or material lists."""
    material = "Lgregtech/api/unification/material/Material;"
    storage = "Lgregtech/api/fluids/store/FluidStorageKey;"
    gt = "gregtech/integration/groovy/MaterialPropertyExpansion#"
    susy = "supersymmetry/integration/groovyscript/SuSyExpansions#"
    extensions = policy["nativeExtensions"]["gregtech.api.unification.material.Material"]
    extensions.extend(gt + name + "(" + material + args + ")V" for name, args in (
        ("addDust", ""), ("addDust", "I"), ("addDust", "II"),
        ("addOre", ""), ("addOre", "Z"), ("addOre", "II"), ("addOre", "IIZ"),
        ("addBlastProperty", "I"), ("addBlastProperty", "ILjava/lang/String;"),
        ("addBlastProperty", "ILjava/lang/String;I"),
        ("addBlastProperty", "ILjava/lang/String;II"),
        ("addBlastProperty", "ILjava/lang/String;IIII"),
        ("addFluidPipes", "IIZ"), ("addFluidPipes", "IIZZZZ"),
    ))
    extensions.extend(susy + name + "(" + material + args + ")V" for name, args in (
        ("addFluidPipes", "IIZZZZZ"), ("addMillBall", "I"),
        ("setBasic", storage), ("setAcidic", storage),
        ("setupSlurries", ""), ("setupFluidTypes", "[" + storage),
        ("setupFluidTypes", "I[" + storage), ("setOreByProducts", "[" + material),
    ))
    moderator = "supercritical.api.unification.material.properties.ModeratorProperty"
    builder = moderator + "$ModeratorPropertyBuilder"
    result = "L" + builder.replace(".", "/") + ";"
    policy["nativeMethods"].update({
        moderator: ["builder()" + result, "getMaxTemperature()I",
                    "getModerationFactor()D", "getAbsorptionFactor()D"],
        builder: [name + "(" + descriptor + ")" + result for name, descriptor in (
            ("maxTemperature", "I"), ("moderationFactor", "D"), ("absorptionFactor", "D"))]
            + ["build()L" + moderator.replace(".", "/") + ";"],
        "gregtech.api.unification.material.properties.WireProperties": [
            "setAmperage(I)V", "getAmperage()I"],
        "gregtech.api.unification.material.properties.FluidPipeProperties": [
            "setCryoProof(Z)V", "isCryoProof()Z",
            "setCanContain(Lgregtech/api/fluids/attribute/FluidAttribute;Z)V",
            "canContain(Lgregtech/api/fluids/attribute/FluidAttribute;)Z",
            "canContain(Lgregtech/api/fluids/FluidState;)Z",
        ],
    })
    policy["nativeMethods"]["java.util.ArrayList"].append("clear()V")


def _pyrotech_recipes(policy):
    """Selected saved Pyrotech registry calls; original builders own validation."""
    prefix = "com.cleanroommc.groovyscript.compat.mods.pyrotech."
    native = policy["nativeMethods"]
    for name in ("PitKiln", "StoneKiln", "BrickKiln", "Anvil", "Barrel", "StoneOven", "BrickOven"):
        native[prefix + name] = ["removeAll()V"]
    for name in ("SoakingPot", "CompactingBin", "CrudeDryingRack", "DryingRack"):
        native[prefix + name] = [
            "remove(Ljava/lang/String;)V", "remove(Lnet/minecraft/util/ResourceLocation;)V",
            "remove(Lnet/minecraftforge/registries/IForgeRegistryEntry;)Z",
        ]
    abstract = "Lcom/cleanroommc/groovyscript/helper/recipe/AbstractRecipeBuilder;"
    ingredient = "Lcom/cleanroommc/groovyscript/api/IIngredient;"
    stack = "Lnet/minecraft/item/ItemStack;"
    common = ["name(" + argument + ")" + abstract for argument in (
        "Ljava/lang/String;", "Lnet/minecraft/util/ResourceLocation;",
    )] + [operation + "(" + argument + ")" + abstract
          for operation, value in (("input", ingredient), ("output", stack))
          for argument in (value, "[" + value, "Ljava/util/Collection;")]
    for name, module, recipe, setters in (
        ("PitKiln", "basic", "KilnPitRecipe", (("burnTime", "I"), ("failureChance", "F"))),
        ("StoneKiln", "machine", "StoneKilnRecipe", (("burnTime", "I"), ("failureChance", "F"))),
        ("BrickKiln", "machine", "BrickKilnRecipe", (("burnTime", "I"), ("failureChance", "F"))),
        ("StoneOven", "machine", "StoneOvenRecipe", (("duration", "I"),)),
        ("BrickOven", "machine", "BrickOvenRecipe", (("duration", "I"),)),
        ("CrudeDryingRack", "basic", "CrudeDryingRackRecipe", (("dryTime", "I"),)),
        ("DryingRack", "basic", "DryingRackRecipe", (("dryTime", "I"),)),
        ("SoakingPot", "basic", "SoakingPotRecipe", (("time", "I"), ("campfireRequired", "Z"))),
        ("Anvil", "basic", "AnvilRecipe", (("hits", "I"), ("typeHammer", ""),
                                         ("typePickaxe", ""), ("tierGranite", ""))),
    ):
        owner = prefix + name
        builder = owner + "$RecipeBuilder"
        result = "L" + builder.replace(".", "/") + ";"
        native[owner].append("recipeBuilder()" + result)
        native[builder] = common + [operation + "(" + argument + ")" + result
                                    for operation, argument in setters] + [
            "register()Lcom/codetaylor/mc/pyrotech/modules/tech/" + module + "/recipe/" + recipe + ";",
            "register()Ljava/lang/Object;",
        ]
        if name in ("PitKiln", "StoneKiln", "BrickKiln"):
            native[builder].extend(["failureOutput(" + argument + ")" + result
                                    for argument in (stack, "[" + stack, "Ljava/lang/Iterable;")])
        if name == "SoakingPot":
            native[builder].extend(["fluidInput(" + argument + ")" + abstract for argument in (
                "Lnet/minecraftforge/fluids/FluidStack;", "[Lnet/minecraftforge/fluids/FluidStack;",
                "Ljava/util/Collection;",
            )])
    native[prefix + "CompactingBin"].extend([
        "add(Lnet/minecraftforge/registries/IForgeRegistryEntry;)V",
        *["add(Ljava/lang/String;" + ingredient + stack + tail
          + ")Lcom/codetaylor/mc/pyrotech/modules/tech/basic/recipe/CompactingBinRecipe;"
          for tail in ("[I", "Z[I")],
    ])


def material_admission(context_id):
    """Explicit profile-owned input, not qualification or installed availability."""
    contexts = material_contexts()
    selected = [row for row in contexts["policy"]["contexts"] if row["id"] == context_id]
    if len(selected) != 1 or selected[0].get("admissionPolicy") != "axiom-material-admission.json":
        raise ValueError("Unknown material admission context")
    raw = bind_material_admission(files(__package__).joinpath("axiom-material-admission.json").read_bytes(), context_id)
    policy = json.loads(raw)
    return {"profile": "supersymmetry", "context": context_id, "sha256": sha256(raw).hexdigest(),
            "contextPolicySha256": contexts["sha256"], "policy": policy}
