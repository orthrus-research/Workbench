"""Original-name GT material API build inputs for the native Groovy path.

Retain upstream material/registry/property bodies and native static identities.
External service boundaries are explicit throwing ports, not simulated services.
This is separate from the earlier relocated conformance model: no wrapper
Material or MaterialState identity is compiled into this class space.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

from axiom_fluid_sources import member
from axiom_item_sources import native_symbols, MAPPINGS
from axiom_material_program_sources import selected_revisions
from build_axiom_target import git
import axiom_material_content_sources as content

ROOT = Path(__file__).resolve().parents[1]
GT = "src/main/java/gregtech/"
HOST = "research.orthrus.axiom.materialhost.NativeBoundary"
PROPERTIES = ("PropertyKey", "IMaterialProperty", "MaterialProperties", "BlastProperty", "DustProperty",
              "FluidPipeProperties", "FluidProperty", "GemProperty", "IngotProperty", "ItemPipeProperties",
              "OreProperty", "PolymerProperty", "RotorProperty", "ToolProperty", "WireProperties", "WoodProperty")
PRODUCERS = ("ElementMaterials", "FirstDegreeMaterials", "OrganicChemistryMaterials", "UnknownCompositionMaterials",
             "SecondDegreeMaterials", "HigherDegreeMaterials", "MaterialFlagAddition")
CLASSES = (
    "api/unification/Element", "api/unification/Elements", "api/unification/stack/MaterialStack",
    "api/unification/material/Material", "api/unification/material/Materials",
    "api/unification/material/MarkerMaterial", "api/unification/material/MarkerMaterials",
    *("api/unification/material/properties/" + n for n in PROPERTIES),
    *("api/unification/material/materials/" + n for n in PRODUCERS),
    *("api/unification/material/info/" + n for n in ("MaterialFlag", "MaterialFlags", "MaterialIconSet", "MaterialIconType")),
    *("api/unification/material/registry/" + n for n in ("IMaterialRegistryManager", "MaterialRegistry", "MarkerMaterialRegistry")),
    *("core/unification/material/internal/" + n for n in ("MaterialRegistryManager", "MaterialRegistryImpl")),
    *("api/unification/material/event/" + n for n in ("MaterialEvent", "MaterialRegistryEvent", "PostMaterialEvent")),
    "api/util/GTControlledRegistry", "api/util/GTLog", "api/util/SmallDigits", "api/util/function/TriConsumer", "api/util/FluidTooltipUtil",
    "api/items/toolitem/EnchantmentLevel", "api/unification/ore/OrePrefix", "api/unification/ore/IOreRegistrationHandler",
    "api/capability/IFilter", "api/capability/IPropertyFluidFilter",
    *("api/fluids/" + n for n in ("FluidConstants", "FluidState", "FluidBuilder", "GTFluid", "GTFluidRegistration")),
    *("api/fluids/attribute/" + n for n in ("FluidAttribute", "FluidAttributes", "AttributedFluid")),
    *("api/fluids/store/" + n for n in ("FluidStorage", "FluidStorageImpl", "FluidStorageKey", "FluidStorageKeys")),
    "api/unification/FluidUnifier",
)
SUPPORT = ("api/GregTechAPI", "api/GTValues", "api/util/GTUtility", "common/ConfigHolder", "core/CoreModule",
           *content.CLASSES, *content.SUPPORT)
RESOURCES = ('src/main/resources/gregtech_at.cfg',)


def read_sources(gtceu, groovyscript):
    revisions = selected_revisions()
    sources = {n: git(gtceu, "show", revisions["gtceu"] + ":" + GT + n + ".java").decode()
               for n in (*CLASSES, *SUPPORT)}
    sources.update({'resource:'+p:git(gtceu,'show',revisions['gtceu']+':'+p).decode() for p in RESOURCES})
    mappings = git(groovyscript, "show", revisions["groovyscript"] + ":" + MAPPINGS).decode()
    return sources, mappings


def annotations(text):
    """Only compile-time JetBrains and unadmitted CraftTweaker annotations.

    Retain Forge SideOnly and every executable body, literal and Groovy annotation.
    """
    text = re.sub(r'^import (?:org\.jetbrains\.annotations|crafttweaker\.annotations|stanhebben\.zenscript\.annotations)\.[^;]+;\n', '', text, flags=re.M)
    return re.sub(r'@(?:NotNull|Nullable|UnmodifiableView|Unmodifiable|ZenRegister|ZenMethod|ZenGetter|ZenProperty|ZenClass|ZenOperator|ApiStatus\.[A-Za-z]+)\b(?:\([^\n]*?\))?', '', text)


def substitute(text, before, after):
    if before not in text:
        raise ValueError("original API linkage anchor missing: " + before)
    return text.replace(before, after)


def declaration(text, marker):
    if text.count(marker) != 1:
        raise ValueError("API declaration boundary differs: " + marker)
    start = text.index(marker)
    return text[start:text.index(';', start) + 1]


def unit(name, imports, body):
    package, simple = name.rsplit('.', 1)
    return f'package {package};\n{imports}\npublic class {simple} {{\n{body}\n}}\n'


def assemble(originals, mappings):
    fields, methods = native_symbols(mappings)
    def method(owner, name):
        choices = methods[owner, name]
        if len(choices) != 1:
            raise ValueError("ambiguous API MCP/SRG method: " + owner + "." + name)
        return next(iter(choices))
    result = {}
    for path in CLASSES:
        if path == 'api/unification/material/properties/BlastProperty':
            # Keep the complete selected binary owner (including its builders).
            # Native Groovy enum parsing is now linked; do not replace gas-tier
            # validation with the old unavailable-service body.
            continue
        text = annotations(originals[path])
        # Explicit owner-derived name bindings into the selected SRG image.
        for spelling, owner in (("getNamespace", "net/minecraft/util/ResourceLocation"),
                                ("getPath", "net/minecraft/util/ResourceLocation")):
            text = text.replace('.' + spelling + '()', '.' + method(owner, spelling) + '()')
        for owner in ("Enchantments", "EnumDyeColor"):
            qualified = "net/minecraft/" + ("init/" if owner == "Enchantments" else "item/") + owner
            text = re.sub(r'\b' + owner + r'\.([A-Z][A-Z0-9_]*)\b',
                          lambda m: owner + '.' + fields.get((qualified, m[1]), m[1]), text)
        if path.endswith('/FluidTooltipUtil'):
            text = substitute(text, 'TextFormatting.YELLOW', 'TextFormatting.' + fields['net/minecraft/util/text/TextFormatting', 'YELLOW'])
            # Original tooltip registration stores a lazy Supplier. Keep that
            # storage and timing; only actual client translation is unavailable.
            text = substitute(text, 'I18n.format(', HOST + '.fluidTooltipTranslation(')
        if path.endswith('/MarkerMaterials'):
            text = text.replace('color.getName()', 'color.' + method('net/minecraft/item/EnumDyeColor', 'getName') + '()')
        if path.endswith('/GTControlledRegistry'):
            for spelling in ('putObject', 'getObject', 'getObjectById', 'getIDForObject', 'getNameForObject'):
                owner = 'net/minecraft/util/registry/RegistrySimple' if spelling in ('putObject','getObject') else 'net/minecraft/util/registry/RegistryNamespaced'
                text = re.sub(r'\b' + spelling + r'(?=\()', method(owner, spelling), text)
            text = text.replace('underlyingIntegerMap.put(', 'underlyingIntegerMap.' + method('net/minecraft/util/IntIdentityHashBiMap','put') + '(')
        for spelling, owner in (('registryObjects', 'net/minecraft/util/registry/RegistrySimple'),
                                ('underlyingIntegerMap', 'net/minecraft/util/registry/RegistryNamespaced')):
            text = re.sub(r'\b' + spelling + r'\b', fields[owner, spelling], text)
        if path.endswith('/MaterialRegistryManager'):
            text = text.replace('.getObject(', '.' + method('net/minecraft/util/registry/RegistrySimple','getObject') + '(')
        if path.endswith('/OreProperty'):
            rows = [line.split() for line in mappings.splitlines() if line.startswith('MD:')]
            clamp = [r[1].rsplit('/',1)[1] for r in rows if r[3] == 'net/minecraft/util/math/MathHelper/clamp' and r[2] == '(III)I']
            if len(clamp) != 1: raise ValueError('integer clamp mapping differs')
            text = substitute(text, 'MathHelper.clamp(', 'MathHelper.' + clamp[0] + '(')
        if path.endswith('/FluidBuilder'):
            # Retain queue/construction behavior and prior effects; no world block
            # or external optional-mod context has been admitted by this target.
            block = member(text, 'if (hasFluidBlock)')
            text = text.replace(block, 'if (hasFluidBlock) { throw ' + HOST + '.unsupported("fluid.world-block"); }')
            text = text.replace('import io.github.drmanganese.topaddons.reference.Colors;', '').replace('import gregtech.api.util.Mods;', '')
            text = text.replace('Mods.TOPAddons.isModLoaded()', 'net.minecraftforge.fml.common.Loader.isModLoaded("topaddons")')
            text = substitute(text, 'Colors.FLUID_NAME_COLOR_MAP.put(name, displayColor);', 'throw ' + HOST + '.unsupported("fluid.topaddons-colors");')
        if path.endswith('/GTFluidRegistration'):
            text = text.replace('import gregtech.common.blocks.MetaBlocks;', '')
            text = text.replace('MetaBlocks.FLUID_BLOCKS.add(block);', 'throw ' + HOST + '.unsupported("fluid.world-block-registry");')
            text = text.replace('textureMap.registerSprite(', 'textureMap.' + method('net/minecraft/client/renderer/texture/TextureMap','registerSprite') + '(')
        for spelling, owner in (('canTranslate','net/minecraft/util/text/translation/I18n'),
                                ('hasKey','net/minecraft/client/resources/I18n'), ('format','net/minecraft/client/resources/I18n')):
            text = text.replace('I18n.' + spelling + '(', 'I18n.' + method(owner,spelling) + '(')
        result['gregtech.' + path.replace('/', '.')] = text
    api = originals['api/GregTechAPI']
    result['gregtech.api.GregTechAPI'] = unit('gregtech.api.GregTechAPI',
        'import gregtech.api.unification.material.registry.*; import gregtech.api.util.BaseCreativeTab; '
        'import gregtech.api.unification.OreDictUnifier; import gregtech.api.unification.ore.OrePrefix; '
        'import gregtech.api.unification.material.Materials; import gregtech.api.modules.IModuleManager;',
        '\n'.join(declaration(api, marker) for marker in ('public static IModuleManager moduleManager;', 'public static IMaterialRegistryManager materialManager;', 'public static MarkerMaterialRegistry markerMaterialRegistry;',
            'public static final BaseCreativeTab TAB_GREGTECH =', 'public static final BaseCreativeTab TAB_GREGTECH_MATERIALS =',
            'public static final BaseCreativeTab TAB_GREGTECH_DECORATIONS =', 'public static final BaseCreativeTab TAB_GREGTECH_ORES =',
            'public static final BaseCreativeTab TAB_GREGTECH_TOOLS ='))
        .replace('ToolItems.HARD_HAMMER.get(Materials.Aluminium)', HOST + '.itemLogo()')
        .replace('MetaItems.LOGO.getStackForm()', HOST + '.itemLogo()')
        .replace('MetaBlocks.WARNING_SIGN.getItemVariant(BlockWarningSign.SignType.YELLOW_STRIPES)', HOST + '.itemLogo()'))
    # GTValues and ConfigHolder now come from the complete selected artifact,
    # not reduced source reconstructions. Recipe catalog initialization depends
    # on the original date-sensitive suppliers and full configuration objects.
    utility = originals['api/util/GTUtility']
    result['gregtech.api.util.GTUtility'] = unit('gregtech.api.util.GTUtility', 'import gregtech.api.GTValues; import net.minecraft.util.ResourceLocation; import net.minecraft.block.state.IBlockState; import net.minecraft.item.ItemStack;',
        '\n'.join(annotations(member(utility,m)) for m in ('public static ResourceLocation gregtechId(', 'public static String toLowerCaseUnderscore(',
                    'public static String lowerUnderscoreToUpperCamel(', 'public static int convertRGBtoARGB(int colorValue)',
                    'public static int convertRGBtoARGB(int colorValue, int opacity)')) + '\n' +
        content.blocks.bind_native_symbols('GTUtility', '\n'.join(member(utility, m) for m in (
            'public static ItemStack toItem(IBlockState state)', 'public static ItemStack toItem(IBlockState state, int amount)')), mappings))
    logger = declaration(originals['core/CoreModule'], 'public static final Logger logger =')
    result['gregtech.core.CoreModule'] = unit('gregtech.core.CoreModule', 'import org.apache.logging.log4j.*;', logger)
    result['gregtech.api.util.LocalizationUtils'] = unit('gregtech.api.util.LocalizationUtils','',
        'public static String format(String key, Object... args) { throw ' + HOST + '.unsupported("material.localization"); }\n'
        'public static boolean hasKey(String key) { throw ' + HOST + '.unsupported("material.localization"); }')
    result['gregtech.api.gui.resources.ResourceHelper'] = unit('gregtech.api.gui.resources.ResourceHelper','import net.minecraft.util.ResourceLocation;',
        'public static boolean doResourcepacksHaveResource(ResourceLocation path) { throw ' + HOST + '.unsupported("material.resource-packs"); }')
    result['gregtech.client.utils.TooltipHelper'] = unit('gregtech.client.utils.TooltipHelper', '',
        'public static boolean isShiftDown() { throw ' + HOST + '.unsupported("material.client-keyboard"); }')
    result[HOST] = (ROOT / 'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeBoundary.java').read_text()
    result.update(content.assemble(originals, mappings, annotations))
    result['research.orthrus.axiom.materialhost.NativeMaterialContent'] = (ROOT / 'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeMaterialContent.java').read_text()
    result['research.orthrus.axiom.materialhost.NativeMaterialBlockAccess'] = (ROOT / 'modules/axiom/jvm/src/materialProgram/java/research/orthrus/axiom/materialhost/NativeMaterialBlockAccess.java').read_text()
    return result
