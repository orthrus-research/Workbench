"""Source-qualified material-prefix item construction, not complete GT item behavior.

Method bodies are retained whole. Unqualified native overrides throw instead of
inheriting vanilla fallbacks. Whole-class and method-level retention are distinct
in the source inventory; future content families must close their own dependencies.
"""
from hashlib import sha1, sha256
import json
from pathlib import Path
import re

import axiom_item_sources as items
import axiom_native_material_sources as native
from axiom_stack_sources import relocate
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/material-items.lock.json"
GT = "src/main/java/gregtech/"
PATHS = {"MetaItem": GT + "api/items/metaitem/MetaItem.java",
         "StandardMetaItem": GT + "api/items/metaitem/StandardMetaItem.java",
         "MetaPrefixItem": GT + "api/items/materialitem/MetaPrefixItem.java",
         "IItemComponent": GT + "api/items/metaitem/stats/IItemComponent.java",
         "IItemCapabilityProvider": GT + "api/items/metaitem/stats/IItemCapabilityProvider.java",
         "CombinedCapabilityProvider": GT + "api/capability/impl/CombinedCapabilityProvider.java",
         "BaseCreativeTab": GT + "api/util/BaseCreativeTab.java"}
AUDITED_GT = tuple(GT + p for p in ("common/items/MetaItems.java", "common/CommonProxy.java",
    "core/CoreModule.java", "api/GregTechAPI.java", "api/GTValues.java", "common/blocks/MetaBlocks.java",
    "common/items/ToolItems.java"))
CLEANROOM = ("src/main/java/net/minecraftforge/event/RegistryEvent.java",)
NAMES = (*PATHS, "MaterialItemDeclarations")
NOTICE = "// Source-qualified GTCEu members, LGPL-3.0; see sources/material-items.lock.json and spec/material-items.md.\n"


def verify_sources(roots, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    revisions = json.loads(items.LOCK.read_bytes())["revisions"]
    if lock.get("schema") != "axiom.material-items-source-lock.v1" or lock.get("revisions") != revisions or set(roots) != set(revisions):
        raise ValueError("invalid material item roots or revisions")
    expected = {("gtceu", p) for p in (*PATHS.values(), *AUDITED_GT)} | {("cleanroom", p) for p in CLEANROOM} | {("groovyscript", items.MAPPINGS)}
    result = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        if (repo, path) not in expected or (repo, path) in result:
            raise ValueError("unexpected or duplicate material item source")
        raw = git(roots[repo], "show", revisions[repo] + ":" + path)
        if sha256(raw).hexdigest() != row["sha256"] or sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["gitBlob"]:
            raise ValueError("material item source identity differs: " + path)
        result[repo, path] = raw.decode()
    if set(result) != expected:
        raise ValueError("material item source closure differs")
    validate_bindings(result["groovyscript", items.MAPPINGS])
    return result


def masked(text):
    # Preserve positions and newlines while excluding braces in comments/literals.
    return re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: re.sub(r"[^\n]", " ", m[0]), text)


def block(text, marker):
    if text.count(marker) != 1:
        raise ValueError("material item member boundary differs: " + marker)
    start = text.index(marker)
    code = masked(text)
    opening = code.index("{", start)
    depth = 1
    for i in range(opening + 1, len(code)):
        depth += (code[i] == "{") - (code[i] == "}")
        if depth == 0:
            return text[start:i + 1]
    raise ValueError("unclosed material item member")


def field(text, marker):
    if text.count(marker) != 1:
        raise ValueError("material item field boundary differs: " + marker)
    start = text.index(marker)
    return text[start:masked(text).index(";", start) + 1]


def methods(text):
    """Only direct four-space-indented methods of the pinned outer class."""
    pattern = r"^    ((?:public|protected|private) [^;{=]+?\([^;{]*?\)\s*\{)"
    result = []
    for match in re.finditer(pattern, text, re.MULTILINE):
        signature = match[1][:-1].rstrip()
        name = re.search(r"(\w+)\(", signature)[1]
        result.append((name, signature, block(text, match[1])))
    return result


META_METHODS = {"MetaItem", "getMetaItems", "addItem", "getAllItems", "getItem", "formatRawItemDamage",
                "registerSubItems", "initCapabilities", "getItemBurnTime", "getCreativeTabs", "setCreativeTab",
                "setCreativeTabs", "addAdditionalCreativeTabs", "isInCreativeTab", "setTranslationKey", "getTranslationKey"}
PREFIX_METHODS = {"MetaPrefixItem", "registerSubItems", "registerOreDict", "registerSpecialOreDict", "canGenerate",
                  "getMaterial", "tryGetMaterial", "getOrePrefix", "getItemStackLimit", "getItemBurnTime", "isBeaconPayment"}
# These native Item overrides must not silently fall through to Item's defaults.
NATIVE_OVERRIDES = {"showDurabilityBar", "getDurabilityForDisplay", "getRarity", "getItemStackLimit", "getItemUseAction",
    "getMaxItemUseDuration", "onUsingTick", "onPlayerStoppedUsing", "onItemUseFinish", "onLeftClickEntity",
    "itemInteractionForEntity", "onItemRightClick", "onItemUseFirst", "onItemUse", "onUpdate", "hasContainerItem",
    "getContainerItem", "getAttributeModifiers", "canDisableShield", "canApplyAtEnchantingTable", "isEnchantable",
    "getItemEnchantability", "shouldCauseReequipAnimation", "getItemStackDisplayName", "addInformation", "getSubItems",
    "onEntityItemUpdate", "getRGBDurabilityForDisplay"}


def blocked(signature, name):
    return '@Override\n' + signature + ' { throw new Failure("incomplete", "item.behavior", "Unqualified material item method: ' + name + '"); }'


def project_meta(text):
    fields = [field(text, marker) for marker in (
        "private static final List<MetaItem<?>> META_ITEMS", "private final Map<String, T> names",
        "protected final Short2ObjectMap<T> metaItems", "protected final Short2ObjectMap<ModelResourceLocation> metaItemsModels",
        "protected final Short2ObjectMap<ModelResourceLocation[]> specialItemsModels", "protected static final ModelResourceLocation MISSING_LOCATION",
        "protected final short metaItemOffset", "private CreativeTabs[] defaultCreativeTabs",
        "private final Set<CreativeTabs> additionalCreativeTabs", 'private String translationKey =')]
    retained = [body if name in META_METHODS else blocked(signature, name)
                for name, signature, body in methods(text) if name in META_METHODS | NATIVE_OVERRIDES]
    inner = block(text, "public class MetaValueItem {")
    value_fields = [field(inner, marker) for marker in ("public final int metaValue", "public final String unlocalizedName",
                    "private final List<IItemComponent> allStats", "private int burnValue =")]
    value_methods = [block(inner, marker) for marker in ("public MetaItem<T> getMetaItem()", "protected MetaValueItem(",
        "public int getMetaValue()", "public List<IItemComponent> getAllStats()", "public int getBurnValue()",
        "public ItemStack getStackForm(int", "public ItemStack getStackForm()", "public boolean isItemEqual(",
        "public MetaValueItem setBurnValue(", "public MetaValueItem setMaterialInfo(", "public MetaValueItem setUnificationData(",
        "public MetaValueItem addOreDict(String")]
    return ('public abstract class MetaItem<T extends MetaItem<?>.MetaValueItem> extends Item {\n' + '\n'.join(fields + retained) +
            '\nprotected abstract T constructMetaValueItem(short metaValue, String unlocalizedName);\n' +
            'public class MetaValueItem {\n' + '\n'.join(value_fields + value_methods) + '\n}\n}\n')


def project_prefix(text):
    header = text[text.index("public class MetaPrefixItem"):text.index("    public MetaPrefixItem(")]
    retained = [body if name in PREFIX_METHODS else blocked(signature, name)
                for name, signature, body in methods(text) if name in PREFIX_METHODS | NATIVE_OVERRIDES]
    return header + '\n'.join(retained) + '\n}\n'


METHOD_BINDINGS = {"setHasSubtypes": ("Item", "func_77627_a"), "setCreativeTab": ("Item", "func_77637_a"),
    "isInCreativeTab": ("Item", "func_194125_a"), "getItemDamage": ("ItemStack", "func_77952_i"),
    "getMetadata": ("ItemStack", "func_77960_j"), "setTranslationKey": ("Item", "func_77655_b"),
    "createIcon": ("CreativeTabs", "func_78016_d"), "setBackgroundImageName": ("CreativeTabs", "func_78025_a"),
    "getTabLabel": ("CreativeTabs", "func_78013_b")}


def validate_bindings(original):
    _, mapped = items.native_symbols(original)
    for name, (owner, srg) in METHOD_BINDINGS.items():
        qualified = "net/minecraft/" + ("creativetab/" if owner == "CreativeTabs" else "item/") + owner
        if mapped.get((qualified, name)) != {srg}:
            raise ValueError("material item native binding differs: " + name)


IMPORTS = '''
import java.util.*;
import net.minecraft.item.*;
import net.minecraft.block.Block;
import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.client.renderer.block.model.ModelResourceLocation;
import net.minecraft.client.util.ITooltipFlag;
import net.minecraft.entity.*;
import net.minecraft.entity.item.EntityItem;
import net.minecraft.entity.player.EntityPlayer;
import net.minecraft.entity.ai.attributes.AttributeModifier;
import net.minecraft.inventory.EntityEquipmentSlot;
import net.minecraft.enchantment.Enchantment;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.util.*;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import net.minecraftforge.common.capabilities.*;
import net.minecraftforge.oredict.OreDictionary;
import com.google.common.collect.Multimap;
import org.apache.commons.lang3.ArrayUtils;
import org.apache.commons.lang3.Validate;
import it.unimi.dsi.fastutil.objects.*;
import it.unimi.dsi.fastutil.shorts.*;
'''


def bind(text, mapping):
    text = re.sub(r"^package [^;]+;", "package " + native.PACKAGE + ";", text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^import (?:gregtech|org\.jetbrains\.annotations)\.[^;]+;\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"@(?:NotNull|Nullable)\b", "", text)
    text = relocate(text, {"Material": "FluidMaterial"})
    text = text.replace("GregTechAPI.materialManager", "FluidEnvironment.current().runtime().materials()")
    text = text.replace("GregTechAPI.TAB_GREGTECH", "MaterialItemDeclarations.TAB_GREGTECH")
    text = re.sub(r"\bMaterials\.(\w+)", lambda m: 'PrefixDependencies.material("' + m[1] + '")', text)
    text = text.replace("GTValues.M", "MaterialVoltages.M").replace("GTLog.logger", "org.apache.logging.log4j.LogManager.getLogger(\"axiom.material-items\")")
    text = text.replace("for (FluidMaterial material : registry)", "for (MaterialState state : registry)")
    if "for (MaterialState state : registry)" in text:
        text = text.replace("for (MaterialState state : registry) {", "for (MaterialState state : registry) {\n            FluidMaterial material = (FluidMaterial) state;")
    text = text.replace("return registry.getObjectById(", "return (FluidMaterial) registry.getObjectById(")
    text = text.replace("return Objects.requireNonNull(registry.getObjectById(", "return (FluidMaterial) Objects.requireNonNull(registry.getObjectById(")
    return bind_native_symbols(text, mapping)


def bind_native_symbols(text, mapping):
    """Only native Minecraft linkage; preserve GT packages and object identities."""
    validate_bindings(mapping)
    for name, (_, srg) in METHOD_BINDINGS.items():
        text = re.sub(r"\b" + name + r"(?=\()", srg, text)
    # getItem on MetaItem is GT's overload, not ItemStack.getItem.
    text = re.sub(r"\b(itemStack|stack)\.getItem\(\)", r"\1.func_77973_b()", text)
    # Preserve the GT-only getTranslationKey(T) overload name.
    text = text.replace("String getTranslationKey()", "String func_77658_a()")
    text = text.replace("String getTranslationKey( ItemStack stack)", "String func_77667_c( ItemStack stack)")
    fields, methods_map = items.native_symbols(mapping)
    for name in NATIVE_OVERRIDES:
        # Forge adds stack-aware overloads of these vanilla no-argument methods.
        # They have no SRG rename; mapping by name alone silently loses dispatch.
        if name in {"getItemStackLimit", "hasContainerItem", "getContainerItem", "getItemEnchantability"}:
            continue
        choices = methods_map.get(("net/minecraft/item/Item", name))
        if choices:
            if len(choices) != 1: raise ValueError("ambiguous unqualified native override: " + name)
            text = re.sub(r"\b" + name + r"(?=\()", next(iter(choices)), text)
    for owner, qualified in (("CreativeTabs", "net/minecraft/creativetab/CreativeTabs"), ("Blocks", "net/minecraft/init/Blocks")):
        text = re.sub(r"\b" + owner + r"\.([A-Z][A-Z0-9_]*)\b", lambda m: owner + "." + fields[qualified, m[1]], text)
    return text.replace("ItemStack.EMPTY", "ItemStack.field_190927_a")


def retained_sources(originals):
    mapping = originals["groovyscript", items.MAPPINGS]
    result = {}
    for name, path in PATHS.items():
        text = originals["gtceu", path]
        if name in ("MetaItem", "MetaPrefixItem"):
            text = "package " + native.PACKAGE + ";\n" + IMPORTS + (project_meta(text) if name == "MetaItem" else project_prefix(text))
        result[name] = NOTICE + bind(text, mapping)
    source = originals["gtceu", GT + "common/items/MetaItems.java"]
    prefix_field = field(source, "private static final List<OrePrefix> orePrefixes")
    prefix_block = block(source, "    static {")
    construction = block(source, "        for (OrePrefix prefix : orePrefixes)")
    proxy = originals["gtceu", GT + "common/CommonProxy.java"]
    registration = block(proxy, "        for (MetaItem<?> item : MetaItems.ITEMS)")
    ore = block(block(source, "public static void registerOreDict()"), "        for (MetaItem<?> item : ITEMS)")
    api = originals["gtceu", GT + "api/GregTechAPI.java"]
    tabs = '\n'.join(field(api, "public static final BaseCreativeTab " + n + " =") for n in ("TAB_GREGTECH", "TAB_GREGTECH_MATERIALS"))
    tabs = tabs.replace('GTValues.MODID', '"gregtech"').replace("MetaItems.LOGO.getStackForm()", "unqualifiedLogo()")
    text = 'package ' + native.PACKAGE + ';\nimport java.util.*;\nimport com.google.common.base.CaseFormat;\nimport net.minecraft.item.*;\nimport net.minecraftforge.registries.IForgeRegistry;\n'
    text += 'final class MaterialItemDeclarations {\n' + prefix_field + '\n' + prefix_block + '\n' + tabs
    text += '\nprivate static ItemStack unqualifiedLogo() { throw new Failure("incomplete", "item.family", "MetaItem1 LOGO is not qualified"); }\n'
    text += '\nstatic void construct() {\n' + construction + '\n}\n'
    text += '\nstatic void register(IForgeRegistry<Item> registry) {\n' + registration.replace('MetaItems.ITEMS', 'MetaItem.getMetaItems()') + '\n}\n'
    text += '\nstatic void registerOres() {\n' + ore.replace(' : ITEMS)', ' : MetaItem.getMetaItems())') + '\n}\n}\n'
    result["MaterialItemDeclarations"] = NOTICE + bind(text, mapping)
    return result
