"""Compile Axiom-owned native assembly inputs from source or installed resources.

The caller owns selected artifact/JVM verification, the private work directory,
process supervision and retention. This step uses the existing original SERVER
input builder and host sources; it does not dispatch native initialization.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import zipfile
from .java_runtime import java_tool, vm_arguments

from workbench_api.resources import module_root


BUILDER_SOURCE = "tests/oracles/NativeIdentityInputs.java"
SCANNER_SOURCE = "jvm/src/materialProgramTooling/java/NativeAddonInventory.java"
HOST_SOURCES = (
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRootClassSpace.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeCoremodPrefix.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeEarlyLaunchPrefix.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeEarlyClassSpace.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeLoaderPrefix.java', 'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeServerOwnerPrefix.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeSelectionClassSpace.java', 'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeServerOwnerClassSpace.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeConstructionClassSpace.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeConfigurationObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeSelectedMaterialObservations.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeGroovyInitializationPrefix.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeGroovyClassSpace.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeProgramObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeObservationAccess.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeMaterialObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeMaterialPropertyState.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRegistrationEffects.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeMetaItemObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeFluidObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeGeneratedContentObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeBiomeObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRecipeValues.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeEffectiveRecipeObservations.java',
    'jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeVanillaRecipeObservations.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeFoundationBootstrap.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialCallGate.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialBytecodeGate.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialRecordBytecode.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialTraitClasses.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialTraitLoaderHook.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialAdmissionPolicy.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialMapperBindings.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialPropertyBindings.java', 'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialScriptBindings.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/RecipeStateCatalog.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeRecipeFunctionObservations.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeDiagnosticOrigins.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/NativeContainedArtifacts.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialDiagnosticCauses.java',
    'jvm/src/main/java/research/orthrus/axiom/materialhost/MaterialDiagnosticGroups.java',
)


def source_groups():
    root = module_root(__file__, "axiom")
    groups = {"builder": [root / BUILDER_SOURCE],
              "host": [root / path for path in HOST_SOURCES],
              "scanner": [root / SCANNER_SOURCE]}
    if any(not path.is_file() for paths in groups.values() for path in paths):
        raise ValueError("Axiom native assembly resources are incomplete; reinstall the selected module")
    return groups


def compile_inputs(java_home, classpath, work, run):
    """Compile the existing builder and host through the caller's process service.

    ``run(label, argv, cwd)`` must retain output and raise if the process fails.
    Classpath order is selected by the owning assembler and preserved unchanged.
    """
    java_home, work = Path(java_home), Path(work)
    groups = source_groups()
    frozen = {path: path.read_bytes() for paths in groups.values() for path in paths}
    root = module_root(__file__, "axiom")
    source = work / "assembly-sources"
    source.mkdir()
    for path, raw in frozen.items():
        target = source / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    outputs = {}
    for name, paths in groups.items():
        destination = work / name
        destination.mkdir()
        run("compile-" + name, [java_tool(java_home, 'javac'), "--release", "25", "-proc:none",
            "-encoding", "UTF-8", "-cp", os.pathsep.join(map(str, classpath)), "-d", destination,
            *[source / path.relative_to(root) for path in paths]], work)
        outputs[name] = destination
    if any(path.read_bytes() != raw for path, raw in frozen.items()):
        raise ValueError("Axiom native assembly resources changed during compilation")
    return {**outputs, "sourceInputs": {
        "modules/axiom/" + path.relative_to(root).as_posix(): sha256(raw).hexdigest()
        for path, raw in frozen.items()}}


def build_server_witnesses(java_home, builder, classpath, server, cleanroom, output, run):
    """Run the same original SERVER patch/remap/access input builder once."""
    java_home, output = Path(java_home), Path(output)
    run("generate-server-witnesses", [java_tool(java_home), *vm_arguments(), "-cp",
        os.pathsep.join(map(str, [builder, *classpath])),
        "research.orthrus.axiom.NativeIdentityInputs", "server-root", server, cleanroom, output],
        output.parent)
    return output / "root-class-space.json"


def jar_bytes(path, files):
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED) as jar:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)); info.external_attr = 0o100644 << 16
            jar.writestr(info, raw)


def manifest_inputs(archive):
    """Retain raw manifest identity, including sections, folding and entry casing."""
    result = []
    for entry in archive.infolist():
        if entry.filename.lower() != 'meta-inf/manifest.mf' or entry.is_dir():
            continue
        if entry.file_size > 1 << 20:
            raise ValueError('candidate manifest exceeds bound')
        raw = archive.read(entry)
        result.append({'entry': entry.filename, 'size': len(raw), 'sha256': sha256(raw).hexdigest()})
    return result


def verify_method_mappings(admission, raw):
    """Bind author-facing names to exact selected SRG descriptor families."""
    selected=[]
    lines=[line.split() for line in raw.decode().splitlines() if line.startswith('MD: ')]
    for owner,aliases in admission['nativeMethodMappings'].items():
        for authored,native in aliases.items():
            prefix=owner.replace('.','/')+'/'
            rows=[parts for parts in lines if len(parts)==5 and parts[3]==prefix+authored]
            expected={value for value in admission['nativeMethods'].get(owner,[]) if value.startswith(native+'(')}
            if not expected:
                # SRG records the declaring class; admission may allow only its
                # concrete receivers (for example Mudball and Cog, not Item).
                # Bind the exact descriptor family without granting the method
                # to its declaration owner. The JVM gate still verifies the
                # selected receiver and method against nativeMethods.
                expected={value for methods in admission['nativeMethods'].values()
                          for value in methods if value.startswith(native+'(')}
            if (not rows or not expected or any(parts[1]!=prefix+native or parts[2]!=parts[4] for parts in rows)
                    or {native+parts[2] for parts in rows}!=expected or len(rows)!=len(expected)):
                raise ValueError('Native method mapping differs from selected source: '+owner+'#'+authored)
            selected.append({'owner':owner,'authoringName':authored,'nativeName':native,'descriptors':sorted(expected)})
    return {'source':'assets/groovyscript/mappings.srg','sha256':sha256(raw).hexdigest(),'methods':selected}


def _verified_file(root, row):
    path = Path(root) / row["path"]
    if not path.is_file() or path.stat().st_size != row["size"] or sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        raise ValueError("Native assembly input differs from selected bytes: " + row["path"])
    return path


def _policies(pack_profile, platform_profile, context_id):
    from workbench_api.profile_extensions import require_profile_extension
    pack = require_profile_extension("workbench.axiom_targets", pack_profile)
    platform = require_profile_extension("workbench.axiom_targets", platform_profile)
    selected = pack.assembly_policy(context_id)
    native = platform.assembly_policy()
    jvm = platform.jvm_policy()
    catalog = json.loads(selected["contextPolicy"])
    context, = [row for row in catalog["contexts"] if row["id"] == context_id]
    if (selected["profile"] != pack_profile or native["profile"] != platform_profile
            or context["platformProfile"] != platform_profile or context["side"] != "server"
            or jvm["profile"] != platform_profile):
        raise ValueError("Native assembly profile/context selection differs")
    return selected, native, jvm, context


def assembly_inputs(engine_home, java_home, pack_profile, platform_profile, context_id):
    """Reobserve selected engine, JVM, source resources and profile policy bytes."""
    from .cli import installation
    from workbench_api.profile_extensions import profile_extension_identity
    selected, native, jvm, context = _policies(pack_profile, platform_profile, context_id)
    engine, _, _, engine_digest = installation(Path(engine_home))
    locks, library_policies = [], []
    for name in engine["jars"]:
        with zipfile.ZipFile(Path(engine_home) / "lib" / name) as archive:
            if "axiom/supersymmetry.lock.json" in archive.namelist():
                locks.append(json.loads(archive.read("axiom/supersymmetry.lock.json")))
            if "axiom/native-identity-runtime.json" in archive.namelist():
                library_policies.append(sha256(archive.read("axiom/native-identity-runtime.json")).hexdigest())
    if (len(locks) != 1 or {row["id"]: row["commit"] for row in locks[0]["repositories"]} != context["sourceRevisions"]
            or library_policies != [native["sourceInputs"]["profiles/platforms/cleanroom/native-identity-runtime.json"]]):
        raise ValueError("Selected engine source/platform bindings differ from native assembly")
    runtime_inputs = jvm["policy"]["runtimeFiles"] + jvm["policy"]["compilerFiles"]
    for row in runtime_inputs:
        _verified_file(java_home, row)
    module = module_root(__file__, "axiom")
    sources = {"modules/axiom/" + path.relative_to(module).as_posix(): sha256(path.read_bytes()).hexdigest()
               for paths in source_groups().values() for path in paths}
    sources["modules/axiom/src/workbench_axiom/native_assembly.py"] = sha256(Path(__file__).read_bytes()).hexdigest()
    return {"engineManifestSha256": engine_digest, "engineVersion": engine["version"],
            "context": context, "contextPolicySha256": sha256(selected["contextPolicy"]).hexdigest(),
            "admissionPolicySha256": sha256(selected["admissionPolicy"]).hexdigest(),
            "platformJvmPolicySha256": jvm["sha256"], "runtimeInputs": runtime_inputs,
            "sourceInputs": {**sources, **selected["sourceInputs"], **native["sourceInputs"]},
            "profileOwners": [profile_extension_identity("workbench.axiom_targets", name)
                              for name in (pack_profile, platform_profile)]}


def stage_runtime(host, descriptor, libraries, server, library_root, package, native_home_files, admission_raw, *, maven_layout=True):
    """Stage the existing host and original artifacts with their native layout."""
    package = Path(package)
    package.mkdir()
    lib = package / "lib"
    lib.mkdir()
    shutil.copyfile(descriptor, package / "root-class-space.json")
    jar_bytes(lib / "root-stage-host.jar", {p.relative_to(host).as_posix(): p.read_bytes() for p in host.rglob("*.class")})
    classpath = ["lib/root-stage-host.jar"]
    for index, source in enumerate([*libraries, server]):
        name = source.relative_to(library_root).as_posix() if maven_layout and source != server else f"{index:03d}-{source.name}"
        target = lib / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        classpath.append("lib/" + name)
    for name, source in native_home_files.items():
        target = package / "native-home" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if admission_raw is not None:
        (package / "admission-policy.json").write_bytes(admission_raw)
    return classpath


def scan_context(java_home, scanner, classpath, native_home, rows, work, run):
    """Read original metadata with Cleanroom's existing ASMModParser scanner."""
    output = Path(work) / "native-scan.json"
    run("scan-native-context", [java_tool(java_home), *vm_arguments(), "-cp",
        os.pathsep.join(map(str, [scanner, *classpath])), "research.orthrus.axiom.tooling.NativeAddonInventory",
        native_home / "mods", output], work)
    scan = json.loads(output.read_bytes())
    observed = {row["file"]: row for row in scan["artifacts"]}
    if (len(observed) != len(scan["artifacts"]) or set(observed) != {Path(row["path"]).name for row in rows}
            or scan.get("parser") != "net.minecraftforge.fml.common.discovery.asm.ASMModParser"
            or scan.get("modClassesDefined") is not False or scan.get("modCallbacksExecuted") is not False):
        raise ValueError("Original native scan differs from the selected artifact scope")
    result = []
    for row in rows:
        original = _verified_file(native_home, row)
        metadata = observed[Path(row["path"]).name]
        with zipfile.ZipFile(original) as archive:
            manifests = manifest_inputs(archive)
        result.append({"descriptor": row["descriptor"], "descriptorSha256": row["descriptorSha256"],
                       "outputPath": row["path"], "size": row["size"], "sha256": row["sha256"],
                       "manifestInputs": manifests, "manifest": metadata["manifest"],
                       "coremodPlugin": metadata["manifest"].get("FMLCorePlugin")})
    return result


def assemble_runtime(engine_home, java_home, artifact_root, package, work, pack_profile, platform_profile, context_id, run):
    """Assemble supplied verified original inputs; never dispatch initialization.

    Core owns the work/output directories, process callback, retention and final
    setup publication. No checkout, saved program, prepared image or old scanner
    inventory is required by this installed step.
    """
    engine_home, java_home, artifact_root, package, work = map(Path, (engine_home, java_home, artifact_root, package, work))
    inputs = assembly_inputs(engine_home, java_home, pack_profile, platform_profile, context_id)
    selected, native, jvm, context = _policies(pack_profile, platform_profile, context_id)
    root_policy, library_policy = native["root"], native["libraries"]
    universal, server = [_verified_file(artifact_root, row) for row in root_policy["inputs"]]
    libraries = [universal, *[_verified_file(artifact_root, row) for row in library_policy["libraries"]]]
    native_files = {row["path"]: _verified_file(artifact_root, row) for row in selected["artifactInputs"]}
    with zipfile.ZipFile(universal) as archive:
        for name in root_policy["requiredResources"]:
            if not archive.read(name):
                raise ValueError("Required original root resource is empty: " + name)
    engine = json.loads((engine_home / "engine-manifest.json").read_bytes())
    dependencies = [*[engine_home / "lib" / name for name in engine["jars"]], *libraries]
    compiled = compile_inputs(java_home, dependencies, work, run)
    descriptor = build_server_witnesses(java_home, compiled["builder"], dependencies, server, universal, work / "generated", run)
    classpath = stage_runtime(compiled["host"], descriptor, libraries, server, artifact_root, package,
                              native_files, selected["admissionPolicy"])
    native_context = selected["nativeContext"]
    native_context["artifacts"] = scan_context(java_home, compiled["scanner"], dependencies,
        package / "native-home", selected["artifactInputs"], work, run)
    artifacts = {row["descriptor"]: native_files[row["outputPath"]] for row in native_context["artifacts"]}
    for witness in [native_context["definitionWitness"], *native_context["selection"]["definitionWitnesses"]]:
        artifact = universal if witness["artifact"] == "cleanroom" else artifacts[witness["artifact"]]
        with zipfile.ZipFile(artifact) as archive:
            witness["inputSha256"] = sha256(archive.read(witness["target"].replace(".", "/") + ".class")).hexdigest()
    groovy = artifacts["mods/groovyscript.pw.toml"]
    with zipfile.ZipFile(groovy) as archive:
        mappings = verify_method_mappings(json.loads(selected["admissionPolicy"]), archive.read("assets/groovyscript/mappings.srg"))
    (package / "context-policy.json").write_bytes(selected["contextPolicy"])
    receipt = {"schema": "axiom.installed-native-assembly.v1", "status": "assembled-unqualified-native-inputs",
               "inputs": inputs, "scanner": {"parser": "net.minecraftforge.fml.common.discovery.asm.ASMModParser",
               "artifacts": len(native_context["artifacts"]), "modClassesDefined": False, "modCallbacksExecuted": False},
               "minecraftLaunched": False, "initializationExecuted": False}
    (package / "native-assembly.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if assembly_inputs(engine_home, java_home, pack_profile, platform_profile, context_id) != inputs:
        raise ValueError("Selected native assembly inputs changed during execution")
    for row in [*root_policy["inputs"], *library_policy["libraries"], *selected["artifactInputs"]]:
        _verified_file(artifact_root, row)
    files = [{"path": path.relative_to(package).as_posix(), "size": path.stat().st_size,
              "sha256": sha256(path.read_bytes()).hexdigest()} for path in sorted(package.rglob("*")) if path.is_file()]
    manifest = {"schema": "axiom.material-runtime.v1", "context": context,
        "contextPolicySha256": inputs["contextPolicySha256"], "admissionPolicySha256": inputs["admissionPolicySha256"],
        "nativeMethodMappings": mappings, "nativeInitialization": {"schema": "axiom.original-native-initialization.v1",
            "inputStage": "raw-original-artifacts", "scope": (
                "original-preinit-through-available-recipe-registries" if context.get("initializationStage") == "recipes"
                else "original-preinit-through-non-recipe-registry-events"),
            "nativeContext": native_context}, "classpath": classpath, "files": files,
        "revisions": {**context["sourceRevisions"], "cleanroom": root_policy["cleanroomRevision"]},
        "engineBuildInputSha256": inputs["engineManifestSha256"],
        "hostSources": {path: digest for path, digest in compiled["sourceInputs"].items() if path.endswith(".java")},
        "recipeInputs": inputs["sourceInputs"], "runtimeInputs": inputs["runtimeInputs"],
        "qualification": "observed-not-qualified", "groovyExecutionQualified": False, "wholePackParity": False,
        "distribution": "local-explicit-native-inputs-only"}
    (package / "runtime.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
