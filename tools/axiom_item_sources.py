"""Complete GT ore-unifier bodies linked to native Cleanroom items and ore events."""
from hashlib import sha1, sha256
import json
from pathlib import Path
import re

import axiom_native_material_sources as native
from axiom_stack_sources import relocate
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/native-items.lock.json"
MAPPINGS = "src/main/resources/assets/groovyscript/mappings.srg"
GT = "src/main/java/gregtech/"
STACKS = ("ItemAndMetadata", "ItemMaterialInfo", "UnificationEntry", "ItemVariantMap",
          "SingleItemVariantMap", "MultiItemVariantMap", "EmptyVariantMap", "UnmodifiableSetViewVariantMap")
PATHS = {**{n: GT + "api/unification/stack/" + n + ".java" for n in STACKS},
         "OreDictUnifier": GT + "api/unification/OreDictUnifier.java",
         "CustomModPriorityComparator": GT + "api/util/CustomModPriorityComparator.java",
         "ItemConstants": GT + "api/GTValues.java"}
AUDITED_GT = (GT + "api/util/GTUtility.java", GT + "common/ConfigHolder.java",
              GT + "common/CommonProxy.java", GT + "core/CoreModule.java")
CLEANROOM = tuple("patches/minecraft/net/minecraft/" + p + ".java.patch" for p in (
    "item/Item", "item/ItemBlock", "item/ItemStack", "item/crafting/CraftingManager",
    "item/crafting/Ingredient", "item/crafting/ShapedRecipes", "item/crafting/ShapelessRecipes")) + tuple(
    "src/main/java/net/minecraftforge/" + p + ".java" for p in (
        "oredict/OreDictionary", "oredict/OreIngredient", "event/AttachCapabilitiesEvent",
        "event/ForgeEventFactory", "common/capabilities/CapabilityDispatcher",
        "common/capabilities/Capability", "common/capabilities/ICapabilityProvider",
        "common/capabilities/ICapabilitySerializable", "common/util/INBTSerializable"))

# Only the native Item/ItemStack symbols reached by these complete bodies.
# Their owners and descriptors are additionally exercised against the pinned image.
METHODS = {"getItem": "func_77973_b", "getItemDamage": "func_77952_i", "getHasSubtypes": "func_77614_k",
           "isEmpty": "func_190926_b", "copy": "func_77946_l", "getCount": "func_190916_E",
           "getNamespace": "func_110624_b"}


def verify_sources(roots, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    from axiom_native_identity_conformance import LOCK as identity_lock
    revisions = json.loads(identity_lock.read_bytes())["revisions"]
    from axiom_source_conformance import LOCK as target_lock
    revisions["groovyscript"] = next(r["commit"] for r in json.loads(target_lock.read_bytes())["repositories"] if r["id"]=="groovyscript")
    if lock.get("schema") != "axiom.native-items-source-lock.v1" or lock.get("revisions") != revisions or set(roots) != set(revisions):
        raise ValueError("invalid native item roots or revisions")
    expected = {("gtceu", p) for p in (*PATHS.values(), *AUDITED_GT)} | {("cleanroom", p) for p in CLEANROOM} | {("groovyscript",MAPPINGS)}
    result = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        if (repo,path) not in expected or (repo,path) in result: raise ValueError("unexpected or duplicate item source")
        raw = git(roots[repo], "show", revisions[repo] + ":" + path)
        if sha256(raw).hexdigest() != row["sha256"] or sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["gitBlob"]:
            raise ValueError("native item source identity differs: " + path)
        result[repo,path] = raw.decode()
    if set(result) != expected: raise ValueError("native item source closure differs")
    validate_bindings(result["groovyscript",MAPPINGS])
    return result


def extract(name, source):
    if name not in PATHS: raise ValueError("unadmitted native item source: " + name)
    if name == "ItemConstants":
        declaration = "public static final short W = OreDictionary.WILDCARD_VALUE;"
        if source.count(declaration) != 1: raise ValueError("GT wildcard declaration differs")
        source = "package gregtech.api;\nimport net.minecraftforge.oredict.OreDictionary;\nfinal class ItemConstants {\n" + declaration + "\n}\n"
    source = re.sub(r"^package [^;]+;", "package " + native.PACKAGE + ";", source, count=1, flags=re.MULTILINE)
    source = re.sub(r"^import gregtech\.[^;]+;\n", "", source, flags=re.MULTILINE)
    # Compile-time JetBrains annotations are not a selected native runtime input.
    source = re.sub(r"^import org\.jetbrains\.annotations\.[^;]+;\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"@(?:NotNull|Nullable|UnmodifiableView|Unmodifiable)\b", "", source)
    source = source.replace("import static gregtech.api.GTValues.M;", "import static " + native.PACKAGE + ".MaterialVoltages.M;")
    source = relocate(source, {"Material": "FluidMaterial"})
    source = source.replace("ConfigHolder.compat.modPriorities", "FluidEnvironment.current().modPriorities()")
    source = source.replace("GregTechAPI.materialManager", "FluidEnvironment.current().runtime().materials()")
    source = source.replace("GregTechAPI.markerMaterialRegistry", "FluidEnvironment.current().markers()")
    source = source.replace("GTUtility.toLowerCaseUnderscore", "FluidSupport.toLowerCaseUnderscore")
    source = source.replace("GTValues.W", "ItemConstants.W").replace("GTValues#W", "ItemConstants#W")
    # The shared material registry has a base carrier type; native catalog values
    # are actual FluidMaterial instances. A Java cast preserves null unchanged.
    source = source.replace("= registry.getObject(underscoreName)", "= (FluidMaterial) registry.getObject(underscoreName)")
    for method, srg in METHODS.items():
        if method == "copy":
            for before in ("event.getOre().copy()", "itemStacks.get(0).copy()", "ItemStack::copy"):
                source = source.replace(before, before.replace("copy", srg))
        elif method == "isEmpty":
            source = source.replace("itemStack.isEmpty()", "itemStack." + srg + "()")
        else:
            source = source.replace("." + method + "()", "." + srg + "()")
    source = source.replace("ItemStack.EMPTY", "ItemStack.field_190927_a")
    source = source.replace("this.item.getTranslationKey(toItemStack())", "this.item.func_77667_c(toItemStack())")
    return "// Retained pinned GTCEu source; LGPL-3.0. See sources/native-items.lock.json and spec/native-items.md.\n" + source


def retained_sources(originals):
    return {n: extract(n, originals["gtceu", p]) for n,p in PATHS.items()}


def native_symbols(original):
    """Immutable owner-qualified MCP/SRG mapping. Reject overload/name ambiguity."""
    fields, methods = {}, {}
    for line in original.splitlines():
        parts=line.split()
        if parts[0]=="FD:":
            owner,name=parts[2].rsplit("/",1); srg=parts[1].rsplit("/",1)[1]
            fields[owner,name]=srg
        elif parts[0]=="MD:":
            owner,name=parts[3].rsplit("/",1); srg=parts[1].rsplit("/",1)[1]
            methods.setdefault((owner,name),set()).add(srg)
    return fields,methods


def validate_bindings(original):
    _,methods=native_symbols(original)
    for method,srg in METHODS.items():
        owner=("net/minecraft/item/Item" if method=="getHasSubtypes" else
               "net/minecraft/util/ResourceLocation" if method=="getNamespace" else "net/minecraft/item/ItemStack")
        if methods.get((owner,method))!={srg}: raise ValueError("native item symbol differs: "+method)
    if methods.get(("net/minecraft/item/Item","getTranslationKey"))!={"func_77658_a","func_77667_c"}:
        raise ValueError("native Item translation overloads differ")


def cleanroom_sources(originals):
    """Compile complete original OreDictionary/OreIngredient bodies for differential tests only."""
    fields,methods=native_symbols(originals["groovyscript",MAPPINGS])
    owners={"Blocks":"net/minecraft/init/Blocks","Items":"net/minecraft/init/Items",
            "Item":"net/minecraft/item/Item","ItemStack":"net/minecraft/item/ItemStack",
            "CraftingManager":"net/minecraft/item/crafting/CraftingManager","CreativeTabs":"net/minecraft/creativetab/CreativeTabs"}
    reached={"create":"net/minecraft/util/NonNullList","getMetadata":"net/minecraft/item/ItemStack",
             "getItemDamage":"net/minecraft/item/ItemStack","getItem":"net/minecraft/item/ItemStack",
             "getIDForObject":"net/minecraft/util/registry/RegistryNamespaced","copy":"net/minecraft/item/ItemStack",
             "getRecipeOutput":"net/minecraft/item/crafting/IRecipe","getIngredients":"net/minecraft/item/crafting/IRecipe",
             "getMatchingStacks":"net/minecraft/item/crafting/Ingredient","getPath":"net/minecraft/util/ResourceLocation",
             "getSubItems":"net/minecraft/item/Item","pack":"net/minecraft/client/util/RecipeItemHelper",
             "getValidItemStacksPacked":"net/minecraft/item/crafting/Ingredient"}
    result={}
    for name in ("OreDictionary","OreIngredient"):
        text=originals["cleanroom","src/main/java/net/minecraftforge/oredict/"+name+".java"]
        for owner,qualified in owners.items():
            text=re.sub(r"\b"+owner+r"\.([A-Z][A-Z0-9_]*)\b",lambda m:owner+"."+fields[qualified,m[1]],text)
        # BlockPrismarine.EnumType has its own getMetadata owner, unlike stacks.
        text=re.sub(r"(BlockPrismarine\.EnumType\.\w+)\.getMetadata\(\)",lambda m:m[1]+"."+next(iter(methods["net/minecraft/block/BlockPrismarine$EnumType","getMetadata"]))+"()",text)
        for method,owner in reached.items():
            choices=methods[owner,method]
            if len(choices)!=1: raise ValueError("ambiguous native method binding: "+method)
            text=re.sub(r"\b"+method+r"(?=\()",next(iter(choices)),text)
        for receiver in ("stack","ore","input","target","output"):
            text=text.replace(receiver+".isEmpty()",receiver+".func_190926_b()")
        result[name]=text
    return result
