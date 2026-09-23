"""Verify immutable source inputs for native Groovy material authoring.

Source availability is not execution qualification. This inventory deliberately
keeps upstream pack examples separate from the complete authored test program.
"""
from hashlib import sha1, sha256
import json
from pathlib import Path

from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/material-program.lock.json"
TARGET = ROOT / "modules/axiom/sources/supersymmetry.lock.json"
PLATFORM = ROOT / "profiles/platforms/cleanroom/native-identity-runtime.json"
GT = "src/main/java/gregtech/"
GR = "src/main/java/com/cleanroommc/groovyscript/"

# These are research/qualification inputs, not an implicit admission allowlist.
# G1 must bind the full reached source and bytecode closure before execution.
PATHS = {
    "gtceu": (
        GT + "api/GTValues.java",
        GT + "api/recipes/RecipeMaps.java",
        GT + "api/recipes/RecipeMap.java",
        GT + "api/gui/resources/TextureArea.java",
        GT + "common/ConfigHolder.java",
        GT + "api/items/metaitem/MetaItem.java",
        GT + "api/items/metaitem/StandardMetaItem.java",
        GT + "api/items/metaitem/ElectricStats.java",
        GT + "integration/baubles/BaubleBehavior.java",
        GT + "api/capability/impl/ElectricItem.java",
        GT + "api/capability/impl/CombinedCapabilityProvider.java",
        GT + "api/capability/SimpleCapabilityManager.java",
        GT + "api/unification/material/properties/WireProperties.java",
        GT + "api/unification/material/properties/OreProperty.java",
        GT + "api/util/GTControlledRegistry.java",
        GT + "api/fluids/attribute/FluidAttribute.java",
        GT + "api/unification/material/properties/FluidPipeProperties.java",
        GT + "api/unification/material/Material.java",
        GT + "api/unification/material/Materials.java",
        GT + "api/unification/stack/MaterialStack.java",
        GT + "api/unification/material/properties/MaterialProperties.java",
        GT + "api/unification/material/event/MaterialEvent.java",
        GT + "api/unification/material/event/PostMaterialEvent.java",
        GT + "core/CoreModule.java",
        GT + "core/unification/material/internal/MaterialRegistryImpl.java",
        GT + "core/unification/material/internal/MaterialRegistryManager.java",
        GT + "integration/groovy/GroovyMaterialBuilderExpansion.java",
        GT + "integration/groovy/GroovyScriptModule.java",
        GT + "integration/groovy/MaterialPropertyExpansion.java",
        GT + "api/fluids/FluidBuilder.java",
        GT + "api/fluids/store/FluidStorageImpl.java",
        GT + "api/unification/material/properties/FluidProperty.java",
        GT + "api/unification/material/properties/BlastProperty.java",
    ),
    "groovyscript": (
        "gradle.properties",
        GR + "GroovyScript.java",
        GR + "mapper/ObjectMapperManager.java",
        GR + "mapper/ObjectMapper.java",
        GR + "mapper/AbstractObjectMapper.java",
        GR + "mapper/ObjectMapperMetaMethod.java",
        GR + "api/IObjectParser.java",
        GR + "sandbox/GroovyScriptSandbox.java",
        GR + "sandbox/SandboxData.java",
        GR + "sandbox/RunConfig.java",
        GR + "sandbox/CustomGroovyScriptEngine.java",
        GR + "sandbox/Preprocessor.java",
        GR + "sandbox/ClosureHelper.java",
        GR + "sandbox/GroovyLogImpl.java",
        GR + "sandbox/meta/ClassScriptMetaClass.java",
        GR + "sandbox/meta/ClassMetaClass.java",
        GR + "sandbox/transformer/GroovyScriptEarlyCompiler.java",
        GR + "sandbox/transformer/GroovyScriptCompiler.java",
        GR + "sandbox/transformer/GroovyScriptTransformer.java",
        GR + "sandbox/transformer/AbstractCompileCustomizer.java",
        GR + "sandbox/transformer/AbstractTransformer.java",
        GR + "sandbox/transformer/AsmDecompileHelper.java",
        GR + "sandbox/transformer/GroovyCodeFactory.java",
        GR + "sandbox/mapper/GroovyDeobfMapper.java",
        GR + "sandbox/security/GroovySecurityManager.java",
        GR + "event/GroovyEventManager.java",
        GR + "event/EventBusExtended.java",
        GR + "core/mixin/EventBusMixin.java",
        GR + "core/mixin/groovy/ModuleNodeAccessor.java",
        GR + "core/mixin/groovy/ClosureMixin.java",
        GR + "core/mixin/groovy/MetaClassImplMixin.java",
        GR + "core/mixin/groovy/AsmDecompilerMixin.java",
        GR + "core/mixin/groovy/ClassNodeResolverMixin.java",
        GR + "core/mixin/groovy/CompUnitClassGenMixin.java",
        GR + "core/mixin/groovy/Java8Mixin.java",
        GR + "core/mixin/groovy/ModuleNodeMixin.java",
        GR + "core/mixin/groovy/ResolveVisitorMixin.java",
        "src/main/resources/mixin.groovyscript.json",
    ),
    "supersymmetry": (
        "groovy/runConfig.json",
        "config/gregtech/gregtech.cfg",
        "config/supercritical.cfg",
        "config/gregtechfoodoption.cfg",
        "groovy/material/OreMaterials.groovy",
        "groovy/material/SuSyMaterials.groovy",
        "groovy/material/IsotopeMaterials.groovy",
        "groovy/material/FirstDegreeMaterialsA.groovy",
        "groovy/material/FirstDegreeMaterialsB.groovy",
        "groovy/material/SecondDegreeMaterials.groovy",
        "groovy/material/ThirdDegreeMaterials.groovy",
        "groovy/globals/Globals.groovy",
        "groovy/classes/ChangeFlags.groovy",
        "groovy/preInit/MaterialChanges.groovy",
        "groovy/preInit/RegisterMetaItems.groovy",
        "groovy/classes/Battery.groovy",
        "groovy/globals/Batteries.groovy",
    ),
    "susy-core": (
        "gradle.properties",
        "src/main/java/supersymmetry/Supersymmetry.java",
        "src/main/java/supersymmetry/mixins/SuSyLateMixinLoader.java",
        "src/main/java/supersymmetry/mixins/gcym/GCYMEventHandlersMixin.java",
        "src/main/resources/mixins.susy.gcym.json",
        "src/main/java/supersymmetry/api/util/SuSyUtility.java",
        "src/main/java/supersymmetry/integration/groovyscript/SuSyExpansions.java",
        "src/main/java/supersymmetry/api/recipes/SuSyRecipeMaps.java",
        "src/main/java/supersymmetry/mixins/gregtech/RecipeMapsMixin.java",
        "src/main/resources/mixins.susy.gregtech.json",
        "src/main/java/supersymmetry/integration/groovyscript/GrSModule.java",
        "src/main/java/supersymmetry/api/fluids/SuSyFluidAttributes.java",
        "src/main/java/supersymmetry/common/CommonProxy.java",
        "src/main/java/supersymmetry/common/materials/SusyMaterials.java",
        "src/main/java/supersymmetry/common/materials/SuSyFirstDegreeMaterials.java",
        "src/main/java/supersymmetry/common/materials/SuSyElementMaterials.java",
        "src/main/java/supersymmetry/common/materials/SuSySecondDegreeMaterials.java",
        "src/main/java/supersymmetry/common/materials/SuSyOrganicChemistryMaterials.java",
        "src/main/java/supersymmetry/common/materials/SuSyHighDegreeMaterials.java",
        "src/main/java/supersymmetry/common/materials/SuSyUnknownCompositionMaterials.java",
        "src/main/java/supersymmetry/api/fluids/SusyGeneratedFluidHandler.java",
        "src/main/java/supersymmetry/api/unification/material/properties/FiberProperty.java",
        "src/main/java/supersymmetry/api/unification/material/properties/DummyABSProperty.java",
        "src/main/java/supersymmetry/api/unification/material/properties/MillBallProperty.java",
        "src/main/java/supersymmetry/api/unification/material/properties/SuSyPropertyKey.java",
        "src/main/java/supersymmetry/api/unification/material/info/SuSyMaterialFlags.java",
    ),
    "cleanroom": (
        "src/main/java/net/minecraftforge/fml/relauncher/FMLInjectionData.java",
        "src/main/java/net/minecraftforge/fml/relauncher/CoreModManager.java",
        "src/main/java/net/minecraftforge/fml/relauncher/FMLLaunchHandler.java",
        "src/main/java/net/minecraftforge/fml/common/launcher/FMLTweaker.java",
        "src/main/java/net/minecraftforge/fml/common/launcher/FMLServerTweaker.java",
        "src/main/java/net/minecraftforge/fml/common/asm/transformers/PatchingTransformer.java",
        "src/main/java/net/minecraftforge/fml/common/patcher/ClassPatchManager.java",
        "src/main/java/net/minecraftforge/common/ForgeEarlyConfig.java",
        "src/main/java/com/cleanroommc/client/modlist/ModListConfig.java",
        "src/main/java/com/cleanroommc/common/CleanroomContainer.java",
        "src/main/java/com/cleanroommc/common/ConfigAnytimeContainer.java",
        "src/main/java/zone/rong/mixinbooter/MixinBooterModContainer.java",
        "src/main/java/net/minecraftforge/fml/common/InjectedModContainer.java",
        "src/main/java/net/minecraftforge/common/config/ConfigManager.java",
        "src/main/java/net/minecraftforge/registries/IForgeRegistryEntry.java",
        "src/main/java/net/minecraftforge/registries/GameData.java",
        "src/main/java/net/minecraftforge/oredict/OreDictionary.java",
        "src/main/java/net/minecraftforge/fml/common/asm/transformers/ModAPITransformer.java",
        "src/main/java/net/minecraftforge/fml/common/Loader.java",
        "src/main/java/com/cleanroommc/discovery/CleanroomModDiscoverer.java",
        "src/main/java/net/minecraftforge/fml/relauncher/libraries/LibraryManager.java",
        "src/main/java/net/minecraftforge/fml/relauncher/libraries/ModList.java",
        "src/main/java/net/minecraftforge/fml/common/ModClassLoader.java",
        "src/main/java/net/minecraftforge/fml/common/discovery/JarDiscoverer.java",
        "src/main/java/net/minecraftforge/fml/common/discovery/ITypeDiscoverer.java",
        "src/main/java/net/minecraftforge/fml/common/discovery/ContainerType.java",
        "src/main/java/net/minecraftforge/fml/common/MetadataCollection.java",
        "src/main/java/net/minecraftforge/fml/common/versioning/DependencyParser.java",
        "src/main/java/net/minecraftforge/fml/common/versioning/DefaultArtifactVersion.java",
        "src/main/java/net/minecraftforge/fml/common/versioning/VersionParser.java",
        "src/main/java/net/minecraftforge/fml/common/discovery/ModCandidate.java",
        "src/main/java/net/minecraftforge/fml/common/ModContainerFactory.java",
        "src/main/java/net/minecraftforge/fml/common/FMLModContainer.java",
        "src/main/java/net/minecraftforge/fml/common/LoadController.java",
        "src/main/java/net/minecraftforge/fml/common/LoaderState.java",
        "src/main/java/net/minecraftforge/fml/common/event/FMLLoadEvent.java",
        "src/main/java/net/minecraftforge/fml/common/ModAPIManager.java",
        "src/main/java/net/minecraftforge/fml/common/API.java",
        "src/main/java/net/minecraftforge/fml/common/discovery/ASMDataTable.java",
        "src/main/java/net/minecraftforge/fml/common/toposort/ModSorter.java",
        "patches/minecraft/net/minecraft/util/text/translation/LanguageMap.java.patch",
        "src/main/java/net/minecraftforge/fml/common/FMLCommonHandler.java",
        "src/main/java/net/minecraftforge/fml/server/FMLServerHandler.java",
        "src/main/java/net/minecraftforge/common/MinecraftForge.java",
        "src/main/java/net/minecraftforge/common/UsernameCache.java",
        "src/main/java/net/minecraftforge/common/ForgeHooks.java",
        "src/main/java/net/minecraftforge/fluids/FluidRegistry.java",
        "src/main/java/net/minecraftforge/fml/common/discovery/asm/ASMModParser.java",
        "src/main/java/net/minecraftforge/fml/common/asm/transformers/EventSubscriptionTransformer.java",
        "src/main/java/net/minecraftforge/fml/common/eventhandler/EventBus.java",
        "src/main/java/net/minecraftforge/fml/common/eventhandler/ASMEventHandler.java",
        "src/main/java/net/minecraftforge/fml/common/eventhandler/EventListenerFactory.java",
        "src/main/java/net/minecraftforge/fml/common/eventhandler/ListenerList.java",
    ),
}


def selected_revisions():
    selected = {row["id"]: row["commit"] for row in json.loads(TARGET.read_bytes())["repositories"]}
    selected["cleanroom"] = json.loads(PLATFORM.read_bytes())["cleanroomRevision"]
    return selected


def source_identity(raw):
    return {"size": len(raw), "sha256": sha256(raw).hexdigest(),
            "gitBlob": sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}


def verify_sources(roots, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    if lock.get("schema") != "axiom.material-program-source-lock.v1" or set(roots) != set(PATHS):
        raise ValueError("material program source owners or schema differ")
    revisions = selected_revisions()
    if lock.get("revisions") != revisions:
        raise ValueError("material program source revision differs from selected authorities")
    expected = {(owner, path) for owner, paths in PATHS.items() for path in paths}
    originals = {}
    for row in lock["references"]:
        owner, path = row["repository"], ordinary_path(row["path"])
        if (owner, path) not in expected or (owner, path) in originals:
            raise ValueError("unexpected or duplicate material program source")
        raw = git(roots[owner], "show", revisions[owner] + ":" + path)
        if source_identity(raw) != {key: row[key] for key in ("size", "sha256", "gitBlob")}:
            raise ValueError("material program source identity differs: " + path)
        originals[owner, path] = raw.decode("utf-8")
    if set(originals) != expected:
        raise ValueError("material program source inventory is incomplete")
    return originals
