"""Link the shared, source-retained material kernel to actual Cleanroom classes.

The native program never includes Axiom's isolated fluid, stack or registry models.
Generated source is build output, not a second maintained implementation.
"""
from pathlib import Path
import re
import axiom_fluid_sources as fluids
import axiom_construction_sources as construction
import axiom_material_sources as materials
import axiom_registry_sources as registries
import axiom_prefix_sources as prefixes
from axiom_stack_sources import relocate

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "modules/axiom/jvm/src/main/java/research/orthrus/axiom"
HOST = ROOT / "modules/axiom/jvm/src/nativeMaterials/java"
PACKAGE = "research.orthrus.axiom.nativeconstruction"
NAMES = tuple(sorted(set(fluids.PATHS) | set(construction.PATHS) | set(materials.CLASSES) |
                     set(registries.PATHS) | set(prefixes.PATHS) | {
                         "FluidRegistration", "FluidDomain", "MaterialState", "MaterialPhase", "Failure",
                         "PrefixDependencies", "ConstructionDependencies", "MaterialLifecycle"}))
TYPES = {
    "NativeFluidStack": "net.minecraftforge.fluids.FluidStack",
    "NativeFluid": "net.minecraftforge.fluids.Fluid",
    "NativeLocation": "net.minecraft.util.ResourceLocation",
    "EnchantmentIdentity": "net.minecraft.enchantment.Enchantment",
    "NativeDyeColor": "net.minecraft.item.EnumDyeColor",
    "NativeBiMap": "com.google.common.collect.BiMap",
}


def linked(name, text):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise ValueError("invalid native material source name")
    text = text.replace("research.orthrus.axiom", PACKAGE)
    text = relocate(text, TYPES)
    # Bind MCP accessor spellings to the selected SRG utility image, not new logic.
    text = text.replace(".getNamespace()", ".func_110624_b()").replace(".getPath()", ".func_110623_a()")
    text = text.replace("RegistryRuntime.ActiveMod", "net.minecraftforge.fml.common.ModContainer")
    if name == "MarkerMaterials":
        text = text.replace("color.getName()", "color.func_176610_l()")
    if name == "MaterialLifecycle":
        text = text.replace(PACKAGE + ".materialevents.", PACKAGE + ".")
    if name == "ConstructionDependencies":
        before = 'return (int) FluidEnvironment.current().runtime().utility("rk", "a",\n                new Class<?>[]{int.class, int.class, int.class}, null, value, minimum, maximum);'
        if text.count(before) != 1:
            raise ValueError("native clamp binding changed")
        text = text.replace(before, "return net.minecraft.util.math.MathHelper.func_76125_a(value, minimum, maximum);")
    return text


def assemble(shared=None, host=None):
    shared = shared if shared is not None else {name: (SHARED / (name + ".java")).read_text() for name in NAMES}
    if set(shared) != set(NAMES):
        raise ValueError("native material kernel closure differs")
    host = host if host is not None else {p.stem: p.read_text() for p in HOST.glob("*.java")}
    if set(host) != {"FluidEnvironment", "RegistryRuntime", "NativeNamedRegistry", "FluidRegistryAccess",
                     "CatalogInputs", "CatalogConfiguration", "MaterialEvents", "MaterialEvent",
                     "MaterialRegistryEvent", "PostMaterialEvent",
                     "OreDictUnifier", "CustomModPriorityComparator", "ItemAndMetadata", "ItemMaterialInfo",
                     "UnificationEntry", "ItemVariantMap", "SingleItemVariantMap", "MultiItemVariantMap",
                     "EmptyVariantMap", "UnmodifiableSetViewVariantMap", "ItemConstants",
                     "MetaItem", "StandardMetaItem", "MetaPrefixItem", "IItemComponent", "IItemCapabilityProvider",
                     "CombinedCapabilityProvider", "BaseCreativeTab", "MaterialItemDeclarations", "MaterialContentFamily",
                     "BlockMaterialBase", "BlockCompressed", "BlockFrame", "PropertyMaterial", "MaterialItemBlock", "MaterialBlockDeclarations",
                     "StoneType", "StoneTypes", "BlockOre", "OreItemBlock", "StoneVariantBlock", "PropertyStoneType",
                     "VariantBlock", "IStateHarvestLevel", "IWalkingSpeedBonus", "IBlockOre",
                     "OreDeclarations", "OreHostBlocks", "OreAccessRules", "OreContentFamily", "OreAddon"}:
        raise ValueError("native material host closure differs")
    result = {name: linked(name, text) for name, text in shared.items()}
    result.update(host)
    for name, text in result.items():
        if re.search(r"\b(?:NativeFluid|NativeFluidStack|FluidRegistryState|NativeLocation)\b", re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', text)):
            raise ValueError("isolated identity leaked into native material source: " + name)
    return result
