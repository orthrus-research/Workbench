"""Pinned ore/host-stone members and named registration excerpts.

This closes ore identity and world-independent drop selection, not worldgen,
complete addon material producers, decorative behavior, or installed mod loading.
"""
from hashlib import sha1, sha256
import json
from pathlib import Path
import re

import axiom_material_item_sources as members
import axiom_material_block_sources as blocks
import axiom_item_sources as items
import axiom_native_material_sources as native
from axiom_stack_sources import relocate
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/material-ores.lock.json"
GT = members.GT
SUSY = "src/main/java/supersymmetry/"
PATHS = {n: ("gtceu", GT + p + n + ".java") for p, names in (
    ("api/unification/ore/", ("StoneType", "StoneTypes")),
    ("common/blocks/", ("BlockOre", "OreItemBlock", "StoneVariantBlock")),
    ("common/blocks/properties/", ("PropertyStoneType",)),
    ("api/block/", ("VariantBlock", "IStateHarvestLevel", "IWalkingSpeedBonus")),
    ("api/util/", ("IBlockOre",))) for n in names}
PATHS.update({"SusyStoneTypes": ("susy-core", SUSY + "api/unification/ore/SusyStoneTypes.java"),
              "SusyStoneVariantBlock": ("susy-core", SUSY + "common/blocks/SusyStoneVariantBlock.java")})
AUDITED = {( "gtceu", GT + p) for p in ("common/CommonProxy.java", "common/blocks/MetaBlocks.java",
    "api/GregTechAPI.java", "api/worldgen/config/OreConfigUtils.java", "api/util/Mods.java", "core/CoreModule.java")}
AUDITED |= {("susy-core", SUSY + p) for p in ("api/unification/ore/SusyOrePrefix.java", "common/CommonProxy.java",
    "Supersymmetry.java", "common/blocks/SuSyBlocks.java", "common/materials/SusyMaterials.java",
    "common/materials/SuSyFirstDegreeMaterials.java", "common/materials/SuSySecondDegreeMaterials.java", "api/util/SuSyUtility.java")}
AT = "src/main/resources/gregtech_at.cfg"
AT_SOURCE = "src/main/java/net/minecraftforge/fml/common/asm/transformers/AccessTransformer.java"
EXPECTED = set(PATHS.values()) | AUDITED | {("cleanroom", p) for p in (*blocks.CLEANROOM, AT_SOURCE)} | {("groovyscript", items.MAPPINGS), ("gtceu", AT), ("susy-core", "LICENSE")}
EXTERNAL_NAMES = {"SusyStoneTypes", "SusyStoneVariantBlock", "SusyStonePrefixes", "SusyStoneMaterials", "SusyOreHostBlocks"}
NAMES = tuple(n for n in (*PATHS, "OreDeclarations", "OreHostBlocks", "OreAccessRules") if n not in EXTERNAL_NAMES)
NOTICE = "// Source-qualified GTCEu/Susy-Core members; see sources/material-ores.lock.json and spec/material-ores.md.\n"
HOST_MATERIALS = {
    "SuSyFirstDegreeMaterials": ("Anorthite", "Albite", "Labradorite", "Bytownite", "Clinochlore", "Augite", "Dolomite", "Muscovite", "Fluorite", "Forsterite", "Lizardite"),
    "SuSySecondDegreeMaterials": ("Gabbro", "Gneiss", "Limestone", "Phyllite", "Shale", "Slate", "Kimberlite", "Anorthosite"),
}


def revisions():
    from axiom_source_conformance import LOCK as target
    result = json.loads(items.LOCK.read_bytes())["revisions"]
    result["susy-core"] = next(r["commit"] for r in json.loads(target.read_bytes())["repositories"] if r["id"] == "susy-core")
    return result


def verify_sources(roots, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    if lock.get("schema") != "axiom.material-ores-source-lock.v1" or lock.get("revisions") != revisions() or set(roots) != set(revisions()):
        raise ValueError("invalid material ore roots or revisions")
    result = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        if (repo, path) not in EXPECTED or (repo, path) in result:
            raise ValueError("unexpected or duplicate material ore source")
        raw = git(roots[repo], "show", lock["revisions"][repo] + ":" + path)
        if sha256(raw).hexdigest() != row["sha256"] or sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["gitBlob"]:
            raise ValueError("material ore source identity differs: " + path)
        result[repo, path] = raw.decode()
    if set(result) != EXPECTED: raise ValueError("material ore source closure differs")
    blocks.validate_bindings(result["groovyscript", items.MAPPINGS])
    return result


def guard(text, marker, *, override=False):
    body = members.block(text, marker)
    signature = body[:body.index("{")]
    return text.replace(body, ("@Override\n" if override else "") + signature +
        '{ throw new Failure("incomplete", "ore.behavior", "Unqualified ore/host-stone method: ' + marker.split("(")[0] + '"); }')


def project(name, text):
    if name == "StoneType":
        text = guard(text, "public static StoneType computeStoneType(")
        text = text.replace("Mods.JustEnoughItems.isModLoaded()", 'net.minecraftforge.fml.common.Loader.isModLoaded("jei")')
        text = text.replace("OreByProduct.addOreByProductPrefix(this.processingPrefix);",
            'throw new Failure("incomplete", "ore.jei", "JEI integration is outside this native content context");')
    elif name == "BlockOre":
        for marker in ("public boolean isFireSource(", "public boolean canRenderInLayer(", "public void onModelRegister("):
            text = guard(text, marker)
    elif name == "OreItemBlock":
        text = guard(text, "public String getItemStackDisplayName(")
    elif name == "VariantBlock":
        text = guard(text, "public void addInformation(")
    elif name == "IWalkingSpeedBonus":
        text = guard(text, "default boolean bonusSpeedCondition(")
    elif name in ("StoneVariantBlock", "SusyStoneVariantBlock"):
        text = guard(text, "public Item getItemDropped(")
        if name == "SusyStoneVariantBlock":
            # Full enum/state identities are retained, not unadmitted material
            # bindings for decorative-only concrete/leucobasalt variants.
            text = guard(text, "public gregtech.api.unification.material.Material getMaterial(")
            text = text.replace("protected BlockStateContainer createBlockState()", "@Override\n    protected BlockStateContainer createBlockState()")
        else:
            text = guard(text, "public boolean canCreatureSpawn(")
    return text


def bind(name, text, mapping):
    fields, methods = items.native_symbols(mapping)
    text = re.sub(r"^package [^;]+;", "package " + native.PACKAGE + ";", text, count=1, flags=re.MULTILINE)
    # Keep original native imports, remove source owners now linked in-package.
    text = re.sub(r"^import (?:static )?(?:gregtech|supersymmetry|org\.jetbrains|org\.jspecify|net\.minecraftforge\.client)\.[^;]+;\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"@(?:NotNull|Nullable|NonNull)\b", "", text)
    text = re.sub(r"@ApiStatus\.ScheduledForRemoval\([^\n]+\)", "", text)
    if name == "StoneTypes": text = text.replace("StoneVariant.SMOOTH", "StoneVariantBlock.StoneVariant.SMOOTH")
    if name in ("VariantBlock", "SusyStoneVariantBlock"):
        text = text.replace("Material materialIn", "net.minecraft.block.material.Material materialIn").replace("Material.ROCK", "net.minecraft.block.material.Material.ROCK")
        text = text.replace("import net.minecraft.block.material.Material;", "")
    text = text.replace("gregtech.api.unification.material.Material", "FluidMaterial")
    text = text.replace("net.minecraft.block.material.Material", "AXIOM_NATIVE_MATERIAL")
    text = relocate(text, {"Material": "FluidMaterial", "SusyMaterials": "SusyStoneMaterials", "SusyOrePrefix": "SusyStonePrefixes"})
    text = text.replace("AXIOM_NATIVE_MATERIAL", "net.minecraft.block.material.Material")
    text = text.replace("ConfigHolder.worldgen.allUniqueStoneTypes", "PrefixDependencies.allUniqueStoneTypes()")
    text = re.sub(r"\bMaterials\.(\w+)", lambda m: 'PrefixDependencies.material("' + m[1] + '")', text)
    text = text.replace("new GTControlledRegistry<>(128)", "new GTControlledRegistry<>(FluidEnvironment.current().runtime(), 128)")
    text = text.replace("GregTechAPI.materialManager", "FluidEnvironment.current().runtime().materials()")
    text = text.replace("GregTechAPI.oreBlockTable", "OreDeclarations.oreBlockTable")
    text = re.sub(r"GregTechAPI\.(TAB_GREGTECH\w*)", r"OreHostBlocks.\1", text)
    text = text.replace("MetaBlocks.STONE_BLOCKS", "OreHostBlocks.STONE_BLOCKS").replace("SuSyBlocks.SUSY_STONE_BLOCKS", "SusyOreHostBlocks.SUSY_STONE_BLOCKS")
    text = text.replace("MetaBlocks.ORES", "OreDeclarations.ORES").replace("OreConfigUtils.getOreForMaterial", "OreDeclarations.getOreForMaterial")
    text = text.replace("GTUtility.toItem", "MaterialBlockDeclarations.toItem").replace("ToolClasses.", "MaterialBlockDeclarations.")
    text = text.replace("for (FluidMaterial material : materialRegistry) {", "for (MaterialState state : materialRegistry) {\nFluidMaterial material = (FluidMaterial) state;")
    text = text.replace("SuSyUtility.susyId", "susyId")
    return bind_native_symbols(name, text, mapping)


def bind_native_symbols(name, text, mapping):
    """Native linkage without GT package, material or configuration substitution."""
    fields, methods = items.native_symbols(mapping)
    # Owner-specific names. In particular enum.getMaterial is not Block.getMaterial.
    owners = {
        "block/Block": ("createBlockState", "getDefaultState", "setDefaultState", "setTranslationKey", "setCreativeTab", "setSoundType", "setHardness", "setResistance", "getStateFromMeta", "getMetaFromState", "damageDropped", "getItemDropped", "getSilkTouchDrop", "getSubBlocks", "addInformation", "getTranslationKey", "canSilkHarvest"),
        "block/state/IBlockState": ("withProperty", "getValue", "getBlock"),
        "block/state/BlockStateContainer": ("getBaseState", "getValidStates"),
        "block/properties/IProperty": ("getAllowedValues", "parseValue"),
        "item/Item": ("setHasSubtypes", "getItemFromBlock", "getItemStackDisplayName"),
        "item/ItemStack": ("getItemDamage",),
    }
    for owner, names in owners.items():
        for method in names:
            choices = methods.get(("net/minecraft/" + owner, method))
            # Forge-added methods such as setSoundType retain MCP spellings.
            if not choices:
                if method == "setSoundType": continue
                raise ValueError("missing ore native method: " + owner + "." + method)
            if len(choices) != 1: raise ValueError("ambiguous ore native method: " + method)
            text = re.sub(r"\b" + method + r"(?=\()", next(iter(choices)), text)
    # Method references do not have a following parenthesis.
    text = text.replace("::getDefaultState", "::func_176223_P")
    if name == "BlockOre": text = text.replace(" getMaterial(", " func_149688_o(")
    if name == "OreItemBlock": text = text.replace("getMetadata(", "func_77647_b(")
    if name == "PropertyStoneType": text = text.replace("String getName(", "String func_177702_a(")
    if name in ("StoneVariantBlock", "SusyStoneVariantBlock"): text = text.replace("String getName(", "String func_176610_l(")
    text = text.replace("this.blockState", "this.field_176227_L")
    if name == "BlockOre": text = re.sub(r"\bblockState\b", "field_176227_L", text)
    for owner in ("BlockStone", "BlockSandStone", "BlockRedSandstone", "SoundType", "Blocks", "CreativeTabs", "MapColor", "PropertyEnum"):
        full = {"Blocks": "init/Blocks", "CreativeTabs": "creativetab/CreativeTabs", "MapColor": "block/material/MapColor", "PropertyEnum": "block/properties/PropertyEnum"}.get(owner, "block/" + owner)
        text = re.sub(r"\b" + owner + r"\.([A-Z][A-Z0-9_]*)\b", lambda m: owner + "." + fields.get(("net/minecraft/" + full, m[1]), m[1]), text)
    text = re.sub(r"net\.minecraft\.block\.material\.Material\.([A-Z_]+)", lambda m: "net.minecraft.block.material.Material." + fields["net/minecraft/block/material/Material", m[1]], text)
    text = text.replace("PropertyEnum.create(", "PropertyEnum.func_177709_a(")
    return text


def shell(name, body, imports=""):
    return "package " + native.PACKAGE + ";\nimport java.util.*;\nimport java.util.stream.*;\nimport net.minecraft.block.*;\nimport net.minecraft.block.state.*;\nimport net.minecraft.item.*;\nimport net.minecraftforge.registries.IForgeRegistry;\n" + imports + "\nfinal class " + name + " {\n" + body + "\n}\n"


def projected_sources(originals):
    mapping = originals["groovyscript", items.MAPPINGS]
    result = {n: NOTICE + bind(n, project(n, originals[key]), mapping) for n, key in PATHS.items()}
    proxy = originals["gtceu", GT + "common/CommonProxy.java"]
    meta = originals["gtceu", GT + "common/blocks/MetaBlocks.java"]
    api = originals["gtceu", GT + "api/GregTechAPI.java"]
    body = [members.field(meta, "public static final List<BlockOre> ORES"), members.field(api, "public static final Map<Material, Map<StoneType, IBlockOre>> oreBlockTable")]
    body += [members.block(proxy, m) for m in ("private static void createOreBlock(Material material)", "private static <T> T[] copyNotNull(", "private static void createOreBlock(Material material, StoneType[]", "private static <T extends Block> ItemBlock createItemBlock(")]
    callback = members.block(proxy, "public static void registerBlocks(")
    condition = members.block(callback, "if (material.hasProperty(PropertyKey.ORE)")
    body.append("static void generate() {\nStoneType.init();\nfor (MaterialRegistry materialRegistry : GregTechAPI.materialManager.getRegistries()) {\nfor (Material material : materialRegistry) {\n" + condition + "\n}}\n}")
    registration = "for (BlockOre block : ORES) registry.register(block);"
    if callback.count(registration) != 1: raise ValueError("ore block registration excerpt differs")
    body.append("static void registerBlocks(IForgeRegistry<Block> registry) { " + registration + " }")
    body.append("static void registerItems(IForgeRegistry<Item> registry) {\n" + members.block(members.block(proxy, "public static void registerItems("), "for (BlockOre block : ORES)") + "\n}")
    body.append("static void registerOres() {\n" + members.block(members.block(meta, "public static void registerOreDict()"), "for (BlockOre blockOre : ORES)") + "\n}")
    body.append(members.block(originals["gtceu", GT + "api/worldgen/config/OreConfigUtils.java"], "public static Map<StoneType, IBlockState> getOreForMaterial("))
    result["OreDeclarations"] = NOTICE + bind("OreDeclarations", shell("OreDeclarations", "\n".join(body), "import org.apache.commons.lang3.ArrayUtils;\nimport java.util.function.Function;\nimport net.minecraft.util.ResourceLocation;\n"), mapping)
    # Source-native smooth host instances. Other shapes are deliberately not an
    # admitted decorative family; this is an explicit construction selection.
    body = members.field(meta, "public static final EnumMap<StoneVariantBlock.StoneVariant, StoneVariantBlock> STONE_BLOCKS") + "\n"
    addon_hosts = members.field(originals["susy-core", SUSY + "common/blocks/SuSyBlocks.java"], "public static final EnumMap<SusyStoneVariantBlock.StoneVariant, SusyStoneVariantBlock> SUSY_STONE_BLOCKS")
    result["SusyOreHostBlocks"] = NOTICE + bind("SusyOreHostBlocks", shell("SusyOreHostBlocks", addon_hosts), mapping)
    for tab in ("TAB_GREGTECH", "TAB_GREGTECH_DECORATIONS", "TAB_GREGTECH_ORES"):
        declaration = members.field(api, "public static final BaseCreativeTab " + tab + " =")
        if tab != "TAB_GREGTECH_ORES":
            declaration = re.sub(r"\(\) -> .*, true\);", '() -> { throw new Failure("incomplete", "ore.tab-icon", "Unqualified creative icon"); }, true);', declaration)
        body += declaration.replace("GTValues.MODID", '"gregtech"') + "\n"
    result["OreHostBlocks"] = NOTICE + bind("OreHostBlocks", shell("OreHostBlocks", body), mapping)
    stones = originals["susy-core", SUSY + "api/unification/ore/SusyStoneTypes.java"]
    prefixes = list(dict.fromkeys(re.findall(r"SusyOrePrefix\.(ore\w+)", stones)))
    prefix_source = originals["susy-core", SUSY + "api/unification/ore/SusyOrePrefix.java"]
    body = "\n".join(members.field(prefix_source, "public static final OrePrefix " + n + " =") for n in prefixes)
    result["SusyStonePrefixes"] = NOTICE + bind("SusyStonePrefixes", shell("SusyStonePrefixes", body), mapping)
    body = []
    fields = originals["susy-core", SUSY + "common/materials/SusyMaterials.java"]
    for names in HOST_MATERIALS.values():
        body += [members.field(fields, "public static Material " + n + ";") for n in names]
    declarations = []
    for group, names in HOST_MATERIALS.items():
        source = originals["susy-core", SUSY + "common/materials/" + group + ".java"]
        declarations += [members.field(source, n + " = new Material.Builder(") for n in names]
    body.append("static void registerHostMaterials() {\n" + "\n".join(declarations) + "\n}")
    body.append(members.block(originals["susy-core", SUSY + "api/util/SuSyUtility.java"], "public static ResourceLocation susyId("))
    text = shell("SusyStoneMaterials", "\n".join(body), "import net.minecraft.util.ResourceLocation;\nimport static " + native.PACKAGE + ".MaterialFlags.*;\nimport static " + native.PACKAGE + ".MaterialIconSet.*;\n")
    text = text.replace("Supersymmetry.MODID", '"susy"')
    # Original static GT material imports resolve live fields from the catalog.
    known = set(re.findall(r"public static Material (\w+);", fields))
    for n in ("Olivine", "Biotite", "SiliconDioxide", "Calcite", "Clay", "Andradite", "Calcium", "Aluminium", "Silicon", "Oxygen", "Sodium", "Magnesium", "Hydrogen", "Iron", "Carbon", "Potassium", "Fluorine", "Sulfur"):
        if n in known: raise ValueError("ambiguous GT/Susy material import: " + n)
        text = re.sub(r"\b" + n + r"\b", "Materials." + n, text)
    result["SusyStoneMaterials"] = NOTICE + bind("SusyStoneMaterials", text, mapping)
    rules = [line for line in originals["gtceu", AT].splitlines() if line.split("#")[0].strip().split()[1:2] == ["net.minecraft.block.Block"]]
    if len(rules) != 2: raise ValueError("GT Block access rule closure differs")
    result["OreAccessRules"] = NOTICE + "package " + native.PACKAGE + ";\npublic final class OreAccessRules { public static final String RULES = " + json.dumps("\n".join(rules) + "\n") + "; }\n"
    return result


def retained_sources(originals):
    """Distributable GT members only; no Susy GPL implementation in the engine."""
    return {n: text for n, text in projected_sources(originals).items() if n not in EXTERNAL_NAMES}


def addon_sources(originals):
    """Separately acquired GPL source, generated only into private build space."""
    return {n: text for n, text in projected_sources(originals).items() if n in EXTERNAL_NAMES}
