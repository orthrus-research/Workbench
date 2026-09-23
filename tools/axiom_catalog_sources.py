"""Complete GT catalog and reached configuration source linking; no declaration filtering."""
from hashlib import sha1, sha256
import json
from pathlib import Path
import re

import axiom_bootstrap_sources as lifecycle
import axiom_fluid_sources as fluids
import axiom_native_material_sources as native
from axiom_stack_sources import relocate
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/material-catalog.lock.json"
BASE = "src/main/java/gregtech/api/unification/material/"
GROUPS = ("ElementMaterials", "FirstDegreeMaterials", "OrganicChemistryMaterials", "UnknownCompositionMaterials", "SecondDegreeMaterials", "HigherDegreeMaterials", "MaterialFlagAddition")
GT_PATHS = (BASE + "Materials.java", *(BASE + "materials/" + n + ".java" for n in GROUPS),
            "src/main/java/gregtech/common/ConfigHolder.java", lifecycle.CORE,
            *(lifecycle.EVENTS + n + ".java" for n in ("MaterialRegistryEvent", "MaterialEvent", "PostMaterialEvent")))
CLEANROOM_PATHS = tuple("src/main/java/net/minecraftforge/common/config/" + n + ".java" for n in
                        ("Config", "Configuration", "Property", "ConfigCategory", "ConfigManager", "FieldWrapper", "TypeAdapters", "IFieldWrapper", "ITypeAdapter")) + (
    "src/main/java/net/minecraftforge/fml/common/asm/transformers/EventSubscriptionTransformer.java",
    "src/main/java/net/minecraftforge/fml/common/eventhandler/GenericEvent.java",
    "src/main/java/net/minecraftforge/fml/relauncher/FMLInjectionData.java")
PACK_CONFIG = "config/gregtech/gregtech.cfg"
ENCHANTMENTS = {"BANE_OF_ARTHROPODS":"field_180312_n", "EFFICIENCY":"field_185305_q", "SMITE":"field_185303_l",
                "FORTUNE":"field_185308_t", "LOOTING":"field_185304_p", "FIRE_ASPECT":"field_77334_n"}


def verify_sources(roots, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    expected = {("gtceu",p) for p in GT_PATHS} | {("cleanroom",p) for p in CLEANROOM_PATHS} | {("supersymmetry",PACK_CONFIG)}
    if lock.get("schema") != "axiom.material-catalog-source-lock.v1" or set(roots) != {"gtceu","cleanroom","supersymmetry"}:
        raise ValueError("invalid material catalog source roots or lock")
    from axiom_source_conformance import LOCK as target_path
    from axiom_native_identity_conformance import LOCK as identity_path
    revisions = {r["id"]:r["commit"] for r in json.loads(target_path.read_bytes())["repositories"]}
    revisions["cleanroom"] = json.loads(identity_path.read_bytes())["revisions"]["cleanroom"]
    if lock["revisions"] != {r:revisions[r] for r in roots}: raise ValueError("catalog revision differs from target")
    originals = {}
    for row in lock["references"]:
        repo,path = row["repository"],ordinary_path(row["path"])
        if (repo,path) not in expected or (repo,path) in originals: raise ValueError("unexpected or duplicate catalog source")
        raw = git(roots[repo],"show",lock["revisions"][repo]+":"+path)
        if sha256(raw).hexdigest() != row["sha256"] or sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest() != row["gitBlob"]:
            raise ValueError("catalog source identity differs: "+path)
        originals[repo,path] = raw.decode()
    if set(originals) != expected: raise ValueError("catalog source closure differs")
    return originals


def event_source(name, original):
    text = lifecycle.extract(lifecycle.EVENTS + name + ".java", original)
    text = text.replace("research.orthrus.axiom.materialevents", native.PACKAGE)
    text = text.replace("research.orthrus.axiom.nativeevents", "net.minecraftforge.fml.common.eventhandler")
    text = text.replace("research.orthrus.axiom.MaterialRegistry", native.PACKAGE + ".MaterialRegistry")
    text = text.replace("research.orthrus.axiom.MaterialState", native.PACKAGE + ".FluidMaterial")
    return relocate(text,{"MaterialState":"FluidMaterial"})


def annotated_field(source, marker):
    if source.count(marker) != 1: raise ValueError("configuration field boundary differs: "+marker)
    end = source.index(";", source.index(marker)) + 1
    start = source.rfind("    @Config.Comment",0,source.index(marker))
    if start < 0 or ";" in source[start:source.index(marker)]: raise ValueError("configuration annotations differ")
    return source[start:end]


def configuration_source(original):
    annotation = re.search(r'^@Config\([^\n]+\)',original,re.MULTILINE)
    if annotation is None: raise ValueError("missing GT config annotation")
    text = "package " + native.PACKAGE + ";\nimport net.minecraftforge.common.config.Config;\n"
    text += annotation[0].replace("GTValues.MODID",'"gregtech"') + "\npublic final class SourceCatalogConfiguration {\n"
    for holder, child, field, kind in (("recipes","RecipeOptions","generateLowQualityGems","boolean"),("worldgen","WorldGenOptions","allUniqueStoneTypes","boolean"),("compat","CompatibilityOptions","modPriorities","String[]")):
        text += annotated_field(original, "public static " + child + " " + holder + " =") + "\n"
        text += "public static class " + child + " {\n" + annotated_field(original, "public " + kind + " " + field + " =") + "\n}\n"
    return "// Selected original GT configuration declarations, LGPL-3.0; see sources/material-catalog.lock.json.\n" + text + "}\n"


def producer_sources(originals):
    result = {}
    imports = "\n".join(("import static " + native.PACKAGE + ".MaterialVoltages.*;",
                         "import static " + native.PACKAGE + ".MaterialFlags.*;",
                         "import static " + native.PACKAGE + ".MaterialIconSet.*;",
                         "import static " + native.PACKAGE + ".FluidSupport.gregtechId;",
                         "import static " + native.PACKAGE + ".SourceMaterialCatalog.*;",
                         "import " + native.PACKAGE + ".BlastProperty.GasTier;",
                         "import net.minecraftforge.fluids.FluidRegistry;",
                         "import net.minecraft.init.Enchantments;"))
    for name,path in [("SourceMaterialCatalog",BASE+"Materials.java"), *[(n,BASE+"materials/"+n+".java") for n in GROUPS]]:
        text = fluids.clean(originals["gtceu",path])
        text = re.sub(r"^import static gregtech\.[^;]+;", "", text, flags=re.MULTILINE)
        text = relocate(text,{"Materials":"SourceMaterialCatalog", "GTValues":"MaterialVoltages"})
        text = text.replace("FluidStorageKeys.","FluidEnvironment.current().storageKeys().")
        text = text.replace("FluidAttributes.","FluidEnvironment.current().attributes().")
        for symbol,field in ENCHANTMENTS.items(): text = text.replace("Enchantments."+symbol,"Enchantments."+field)
        text = native.linked(name,text)
        text = text.replace("package " + native.PACKAGE + ";", "package " + native.PACKAGE + ";\n" + imports)
        result[name] = "// Complete pinned GT source body, LGPL-3.0; see sources/material-catalog.lock.json.\n" + text
    result["SourceCatalogConfiguration"] = configuration_source(originals["gtceu","src/main/java/gregtech/common/ConfigHolder.java"])
    return result
