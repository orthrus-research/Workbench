"""Pinned generated material block/state/ItemBlock members, not a whole mod callback.

Native object behavior is source retained. This file only selects members and
binds owners to the selected Cleanroom image; it does not reimplement metadata.
"""
from hashlib import sha1, sha256
import json
from pathlib import Path
import re

import axiom_material_item_sources as members
import axiom_item_sources as items
import axiom_native_material_sources as native
from axiom_stack_sources import relocate
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/material-blocks.lock.json"
GT = members.GT
PATHS = {n: GT + "common/blocks/" + n + ".java" for n in
         ("BlockMaterialBase", "BlockCompressed", "BlockFrame", "MaterialItemBlock")}
PATHS["PropertyMaterial"] = GT + "common/blocks/properties/PropertyMaterial.java"
AUDITED_GT = tuple(GT + p for p in ("common/blocks/MetaBlocks.java", "common/CommonProxy.java",
    "core/CoreModule.java", "api/util/GTUtility.java", "api/recipes/ModHandler.java", "api/items/toolitem/ToolClasses.java"))
CLEANROOM = ("src/main/java/net/minecraftforge/event/RegistryEvent.java",
    "patches/minecraft/net/minecraft/block/Block.java.patch",
    "patches/minecraft/net/minecraft/block/state/BlockStateContainer.java.patch",
    "patches/minecraft/net/minecraft/item/ItemBlock.java.patch")
NAMES = (*PATHS, "MaterialBlockDeclarations")
NOTICE = "// Source-qualified GTCEu members, LGPL-3.0; see sources/material-blocks.lock.json and spec/material-blocks.md.\n"

# Descriptor/owner-qualified spellings; Forge-only overloads are not renamed.
BINDINGS = {
    "createBlockState": ("block/Block", "func_180661_e"),
    "getStateFromMeta": ("block/Block", "func_176203_a"),
    "getMetaFromState": ("block/Block", "func_176201_c"),
    "damageDropped": ("block/Block", "func_180651_a"),
    "getSubBlocks": ("block/Block", "func_149666_a"),
    "getDefaultState": ("block/Block", "func_176223_P"),
    "setHardness": ("block/Block", "func_149711_c"),
    "setResistance": ("block/Block", "func_149752_b"),
    "withProperty": ("block/state/IBlockState", "func_177226_a"),
    "getValue": ("block/state/IBlockState", "func_177229_b"),
    "getValidStates": ("block/state/BlockStateContainer", "func_177619_a"),
    "getAllowedValues": ("block/properties/IProperty", "func_177700_c"),
    "parseValue": ("block/properties/IProperty", "func_185929_b"),
    "setHasSubtypes": ("item/Item", "func_77627_a"),
    "getItemStackDisplayName": ("item/Item", "func_77653_i"),
    "getMaterialMapColor": ("block/material/Material", "func_151565_r"),
}


def validate_bindings(mapping):
    fields, methods = items.native_symbols(mapping)
    for name, (owner, srg) in BINDINGS.items():
        if methods.get(("net/minecraft/" + owner, name)) != {srg}:
            raise ValueError("material block native binding differs: " + name)
    if methods.get(("net/minecraft/block/properties/IProperty", "getName")) != {"func_177701_a", "func_177702_a"}:
        raise ValueError("material property name overloads differ")
    if fields.get(("net/minecraft/block/Block", "blockState")) != "field_176227_L":
        raise ValueError("native block state field differs")


def verify_sources(roots, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    revisions = json.loads(items.LOCK.read_bytes())["revisions"]
    if lock.get("schema") != "axiom.material-blocks-source-lock.v1" or lock.get("revisions") != revisions or set(roots) != set(revisions):
        raise ValueError("invalid material block roots or revisions")
    expected = {("gtceu", p) for p in (*PATHS.values(), *AUDITED_GT)} | {("cleanroom", p) for p in CLEANROOM} | {("groovyscript", items.MAPPINGS)}
    result = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        if (repo, path) not in expected or (repo, path) in result:
            raise ValueError("unexpected or duplicate material block source")
        raw = git(roots[repo], "show", revisions[repo] + ":" + path)
        if sha256(raw).hexdigest() != row["sha256"] or sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["gitBlob"]:
            raise ValueError("material block source identity differs: " + path)
        result[repo, path] = raw.decode()
    if set(result) != expected:
        raise ValueError("material block source closure differs")
    validate_bindings(result["groovyscript", items.MAPPINGS])
    return result


BLOCKED = {"getFlammability", "getFireSpreadSpeed", "replaceWithFramedPipe", "removeFrame",
           "onBlockActivated", "onEntityCollision", "addInformation", "onModelRegister"}
GT_ONLY = {"replaceWithFramedPipe", "removeFrame", "onModelRegister"}


def project(name, text):
    if name == "PropertyMaterial": return text
    if name == "MaterialItemBlock":
        body = members.block(text, "public String getItemStackDisplayName(")
        return text.replace(body, 'public String getItemStackDisplayName(ItemStack stack) { throw new Failure("incomplete", "block.behavior", "Material ItemBlock localization is not qualified"); }')
    # Keep imports for native signatures, not unreachable GT rendering/pipe code.
    header = text[:text.index("public abstract class " + name)]
    header = re.sub(r"^import (?:gregtech|org\.jetbrains|net\.minecraftforge\.client)\.[^;]+;\n", "", header, flags=re.MULTILINE)
    superclass = "Block" if name == "BlockMaterialBase" else "BlockMaterialBase"
    body = "public abstract class " + name + " extends " + superclass + " {\n"
    if name == "BlockFrame": body += members.field(text, "public static final AxisAlignedBB COLLISION_BOX") + "\n"
    for method, signature, retained in members.methods(text):
        if method in BLOCKED:
            body += ("" if method in GT_ONLY else "@Override\n") + signature + ' { throw new Failure("incomplete", "block.behavior", "Unqualified material block method: ' + method + '"); }\n'
        else:
            # Compile-time @Override gates for every source-native method.
            start = text.index(retained)
            annotations = text[text.rfind("}", 0, start) + 1:start]
            if "@Override" in annotations: body += "@Override\n"
            body += retained + "\n"
    if name == "BlockMaterialBase": body += "public abstract PropertyMaterial getVariantProperty();\n"
    return header + body + "}\n"


def bind(name, text, mapping):
    fields, methods = items.native_symbols(mapping)
    text = re.sub(r"^package [^;]+;", "package " + native.PACKAGE + ";", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^import (?:gregtech|org\.jetbrains\.annotations)\.[^;]+;\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"@(?:NotNull|Nullable)\b", "", text)
    # Do not relocate Minecraft's Material type.
    text = text.replace("net.minecraft.block.material.Material", "AXIOM_NATIVE_MATERIAL")
    text = relocate(text, {"Material": "FluidMaterial"}).replace("AXIOM_NATIVE_MATERIAL", "net.minecraft.block.material.Material")
    text = text.replace("GregTechAPI.materialManager", "FluidEnvironment.current().runtime().materials()")
    text = text.replace("GregTechAPI.TAB_GREGTECH_MATERIALS", "MaterialItemDeclarations.TAB_GREGTECH_MATERIALS")
    text = re.sub(r"\bMaterials\.(\w+)", lambda m: 'PrefixDependencies.material("' + m[1] + '")', text)
    text = text.replace("GTUtility.toItem", "MaterialBlockDeclarations.toItem").replace("ModHandler.isMaterialWood", "MaterialBlockDeclarations.isMaterialWood")
    text = text.replace("ToolClasses.", "MaterialBlockDeclarations.").replace("MetaBlocks::", "MaterialBlockDeclarations::")
    text = re.sub(r"(?<![\w.])(GENERATE_FRAME|FORCE_GENERATE_BLOCK)(?![\w])", r"MaterialFlags.\1", text)
    text = text.replace("for (FluidMaterial material : registry) {", "for (MaterialState state : registry) {\nFluidMaterial material = (FluidMaterial) state;")
    text = text.replace("= FluidEnvironment.current().runtime().materials().getMaterial(materialName)", "= (FluidMaterial) FluidEnvironment.current().runtime().materials().getMaterial(materialName)")
    text = text.replace(".materials().getMaterial(", ".materials().AXIOM_CATALOG_LOOKUP(")
    return bind_native_symbols(name, text, mapping).replace("AXIOM_CATALOG_LOOKUP", "getMaterial")


def bind_native_symbols(name, text, mapping):
    """Bind native owners without relocating GT's Material or registry identities."""
    validate_bindings(mapping)
    fields, methods = items.native_symbols(mapping)
    text = text.replace('GregTechAPI.materialManager.getMaterial(', 'GregTechAPI.materialManager.AXIOM_CATALOG_LOOKUP(')
    for method, (_, srg) in BINDINGS.items():
        text = re.sub(r"\b" + method + r"(?=\()", srg, text)
    # Same MCP spelling, different owners/descriptors.
    text = text.replace("stack.getMetadata()", "stack.func_77960_j()")
    text = text.replace("stack.getItem()", "stack.func_77973_b()")
    text = text.replace("entry.func_177229_b()", "entry.getValue()")
    text = text.replace("state.getBlock()", "state.func_177230_c()")
    text = text.replace("((ItemBlock) item).getBlock()", "((ItemBlock) item).func_179223_d()")
    if name == "PropertyMaterial":
        text = text.replace("String getName(", "String func_177702_a(")
    if name == "MaterialItemBlock":
        text = text.replace("BlockMaterialBase getBlock()", "BlockMaterialBase func_179223_d()")
        text = text.replace("int getMetadata(int", "int func_77647_b(int")
    for method in ("setTranslationKey", "setCreativeTab", "getTranslationKey", "getMaterial", "getPushReaction", "isOpaqueCube",
                   "onEntityCollision", "onBlockActivated", "getCollisionBoundingBox", "getRenderLayer", "getBlockFaceShape", "getMapColor", "addInformation"):
        choices = methods.get(("net/minecraft/block/Block", method))
        if not choices or len(choices) != 1: raise ValueError("ambiguous native block method: " + method)
        text = re.sub(r"\b" + method + r"(?=\()", next(iter(choices)), text)
    text = re.sub(r"\bblockState\.", "field_176227_L.", text)
    for owner, qualified in (("net.minecraft.block.material.Material", "block/material/Material"), ("SoundType", "block/SoundType"),
                             ("EnumPushReaction", "block/material/EnumPushReaction"), ("BlockRenderLayer", "util/BlockRenderLayer"),
                             ("BlockFaceShape", "block/state/BlockFaceShape")):
        def replace_field(m):
            # Enum constants retain their names where the mapping has no entry.
            return owner + "." + fields.get(("net/minecraft/" + qualified, m[1]), m[1])
        text = re.sub(re.escape(owner) + r"\.([A-Z][A-Z0-9_]*)\b", replace_field, text)
    return text.replace("AXIOM_CATALOG_LOOKUP", "getMaterial")


def family_declarations(originals):
    """Complete selected native members/loops, independent of their host owner."""
    source = originals["gtceu", GT + "common/blocks/MetaBlocks.java"]
    proxy = originals["gtceu", GT + "common/CommonProxy.java"]
    declarations = [members.field(source, "public static final " + signature) for signature in
        ("Map<Material, BlockCompressed> COMPRESSED", "Map<Material, BlockFrame> FRAMES", "List<BlockCompressed> COMPRESSED_BLOCKS", "List<BlockFrame> FRAME_BLOCKS")]
    declarations += [members.block(source, marker) for marker in
        ("protected static void createGeneratedBlock(", "private static void createCompressedBlock(", "private static void createFrameBlock(")]
    # Precisely named statements, never no-op unrelated portions of init.
    init = members.block(source, "public static void init()")
    construction = []
    for end in ("MetaBlocks::createFrameBlock);", "MetaBlocks::createCompressedBlock);"):
        stop = init.index(end) + len(end); start = init.rfind("createGeneratedBlock(", 0, stop)
        construction.append(init[start:stop])
    declarations.append("static void construct() {\n" + "\n".join(construction) + "\n}")
    lines = []
    for typ, collection in (("BlockCompressed", "COMPRESSED_BLOCKS"), ("BlockFrame", "FRAME_BLOCKS")):
        marker = f"for ({typ} block : {collection}) registry.register(block);"
        if proxy.count(marker) != 1: raise ValueError("native block registration excerpt differs")
        lines.append(marker)
    declarations.append("static void registerBlocks(IForgeRegistry<Block> registry) {\n" + "\n".join(lines) + "\n}")
    callback = members.block(proxy, "public static void registerItems(")
    declarations.append("static void registerItems(IForgeRegistry<Item> registry) {\n" + "\n".join(
        members.block(callback, "for (" + typ + " block : " + collection + ")") for typ, collection in
        (("BlockCompressed", "COMPRESSED_BLOCKS"), ("BlockFrame", "FRAME_BLOCKS"))) + "\n}")
    declarations.append(members.block(proxy, "private static <T extends Block> ItemBlock createItemBlock("))
    callback = members.block(source, "public static void registerOreDict()")
    declarations.append("static void registerOres() {\n" + "\n".join(members.block(callback, marker) for marker in
        ("for (Entry<Material, BlockCompressed> entry : COMPRESSED.entrySet())", "for (Entry<Material, BlockFrame> entry : FRAMES.entrySet())")) + "\n}")
    return declarations


def retained_sources(originals):
    mapping = originals["groovyscript", items.MAPPINGS]
    result = {n: NOTICE + bind(n, project(n, originals["gtceu", p]), mapping) for n, p in PATHS.items()}
    declarations = family_declarations(originals)
    utility = originals["gtceu", GT + "api/util/GTUtility.java"]
    declarations += [members.block(utility, marker) for marker in ("public static ItemStack toItem(IBlockState state)", "public static ItemStack toItem(IBlockState state, int amount)")]
    declarations.append(members.block(originals["gtceu", GT + "api/recipes/ModHandler.java"], "public static boolean isMaterialWood("))
    tools = originals["gtceu", GT + "api/items/toolitem/ToolClasses.java"]
    declarations += [members.field(tools, "public static final String " + n + " =") for n in ("AXE", "PICKAXE", "SHOVEL", "WRENCH")]
    text = "package " + native.PACKAGE + ";\nimport java.util.*;\nimport java.util.Map.Entry;\nimport java.util.function.*;\nimport it.unimi.dsi.fastutil.ints.*;\nimport it.unimi.dsi.fastutil.objects.*;\nimport net.minecraft.block.Block;\nimport net.minecraft.block.state.IBlockState;\nimport net.minecraft.item.*;\nimport net.minecraft.util.ResourceLocation;\nimport net.minecraftforge.registries.IForgeRegistry;\nfinal class MaterialBlockDeclarations {\n"
    result["MaterialBlockDeclarations"] = NOTICE + bind("MaterialBlockDeclarations", text + "\n".join(declarations) + "\n}\n", mapping)
    return result
