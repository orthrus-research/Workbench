#!/usr/bin/env python3
"""Verify native fluid source retention, independent builder execution and a locked Susy producer.

This is a developer qualification tool, not a pack-validity API. It compiles
separately acquired GPL producer source only in temporary test space.
"""
import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

import axiom_fluid_sources as extraction
import axiom_construction_sources as construction
import axiom_stack_sources as stacks
import axiom_registry_runtime as registry_inputs
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK, MATERIAL_ROOT
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/native-fluids.lock.json"
DRIVER = ROOT / "modules/axiom/tests/oracles/FluidConformance.java"
PRODUCER = "src/main/java/supersymmetry/common/materials/SuSyUnknownCompositionMaterials.java"
UTILITY = "src/main/java/supersymmetry/api/util/SuSyUtility.java"


def verify_references(roots, lock):
    if lock.get("schema") != "axiom.native-fluid-source-lock.v1" or set(roots) != set(lock["revisions"]):
        raise ValueError("invalid fluid source lock or roots")
    result = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        revision = lock["revisions"][repo]
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or (repo,path) in result:
            raise ValueError("invalid revision or duplicate fluid reference")
        # Deliberately read immutable Git objects, not the current checkout.
        raw = git(roots[repo], "show", revision + ":" + path)
        blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if sha256(raw).hexdigest() != row["sha256"] or blob != row["gitBlob"]:
            raise ValueError("fluid source identity differs: " + path)
        result[repo,path] = raw.decode()
    return result


def retained_inputs(originals, root=MATERIAL_ROOT):
    retained = {}
    for repo, paths in (("gtceu",extraction.PATHS),("cleanroom",extraction.FORGE)):
        for name,path in paths.items():
            retained[name] = (root / (name + ".java")).read_text()
            if retained[name] != extraction.extract(name, originals[repo,path]):
                raise ValueError("retained fluid source differs from extraction: " + name)
    return retained


def producer_source(originals):
    original = originals["susy-core",PRODUCER]
    body = extraction.member(original, "public static void init()")
    fields = re.findall(r"^\s*(\w+) = new Material\.Builder\(", body, re.MULTILINE)
    if len(fields) != 10 or len(set(fields)) != 10:
        raise ValueError("producer assignment boundary differs")
    utility = extraction.member(originals["susy-core",UTILITY], "public static ResourceLocation susyId(")
    if not re.search(r"^modId\s*=\s*susy\s*$", originals["susy-core","gradle.properties"], re.MULTILINE):
        raise ValueError("producer namespace declaration differs")
    utility = utility.replace("Supersymmetry.MODID", '"susy"')
    body = body.replace("SuSyUtility.susyId", "susyId")
    body = body.replace("FluidStorageKeys.", "FluidEnvironment.current().storageKeys().")
    body = body.replace(".flags(FLAMMABLE)", ".flags(MaterialFlags.FLAMMABLE)")
    # No producer declaration is synthesized; retain the entire original init.
    text = extraction.PACKAGE + "final class LockedSusyProducer {\n"
    text += "static Material " + ", ".join(fields) + ";\n" + utility + "\n" + body
    text += "\nstatic java.util.List<Material> values() { return java.util.List.of(" + ", ".join(fields) + "); }\n}\n"
    return extraction.clean(text)


def provision_cleanroom(destination, revision):
    destination = registry_inputs.ordinary_root(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Cleanroom source destination must be new")
    if not re.fullmatch(r"[0-9a-f]{40}",revision):
        raise ValueError("invalid Cleanroom revision")
    destination.mkdir(parents=True)
    git(destination, "init", "--quiet")
    git(destination, "-c", "credential.helper=", "-c", "core.hooksPath=" + str(destination / ".git/disabled-hooks"),
        "fetch", "--no-tags", "--depth=1", "https://github.com/CleanroomMC/Cleanroom.git", revision)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gtceu","susy-core","java-home","engine-home","registry-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cleanroom",type=Path)
    group.add_argument("--provision-cleanroom",type=Path,help="Fetch the locked commit into a NEW source directory")
    parser.add_argument("--report",type=Path)
    args = parser.parse_args(argv)
    if args.report is not None and (args.report.exists() or args.report.is_symlink()):
        raise ValueError("report must be new")
    raw_lock, raw_target, driver, recipe = LOCK.read_bytes(), TARGET_LOCK.read_bytes(), DRIVER.read_bytes(), Path(extraction.__file__).read_bytes()
    construction_recipe = Path(construction.__file__).read_bytes()
    stack_recipe = Path(stacks.__file__).read_bytes()
    lock = json.loads(raw_lock)
    target = json.loads(raw_target)
    for row in target["repositories"]:
        if row["id"] in lock["revisions"] and row["commit"] != lock["revisions"][row["id"]]:
            raise ValueError("fluid lock differs from selected source target")
    cleanroom_lock = (LOCK.parent / "cleanroom-events.lock.json").read_bytes()
    if json.loads(cleanroom_lock)["revision"] != lock["revisions"]["cleanroom"]:
        raise ValueError("fluid lock differs from selected Cleanroom")
    roots = {"gtceu":args.gtceu,"susy-core":args.susy_core,
             "cleanroom":args.cleanroom or provision_cleanroom(args.provision_cleanroom,lock["revisions"]["cleanroom"])}
    originals = verify_references(roots,lock)
    retained = retained_inputs(originals)
    if (LOCK.parent / "licenses/forge/LGPL-2.1.txt").read_text() != originals["cleanroom","LICENSE"]:
        raise ValueError("Forge license differs from upstream")
    shared = {p.name:p.read_bytes() for p in MATERIAL_ROOT.glob("*.java")}
    root = registry_inputs.verify(args.registry_root).resolve(strict=True)
    registry_policy = registry_inputs.POLICY.read_bytes()
    java = args.java_home.resolve(strict=True)
    runtime = verify_runtime(java,compiler=True)
    home = args.engine_home.resolve(strict=True)
    manifest_raw, manifest, jars = engine_inputs(home,raw_target)
    for resource,expected in (("native-fluids.lock.json",raw_lock),("registry-runtime.json",registry_policy)):
        bound = []
        for jar in jars:
            with zipfile.ZipFile(jar) as archive:
                if "axiom/" + resource in archive.namelist():
                    bound.append(archive.read("axiom/" + resource))
        if bound != [expected]:
            raise ValueError("installed fluid resource differs: " + resource)
    source_jars = list((home / "sources").glob("*-sources.jar"))
    if len(source_jars) != 1:
        raise ValueError("engine requires one bound sources JAR")
    with zipfile.ZipFile(source_jars[0]) as archive:
        for name,raw in shared.items():
            if archive.read("research/orthrus/axiom/" + name) != raw:
                raise ValueError("engine source archive differs: " + name)
    source_jar_digest = sha256(source_jars[0].read_bytes()).hexdigest()
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","_JAVA_OPTIONS","CLASSPATH","LD_PRELOAD","LD_LIBRARY_PATH"):
        environment.pop(name,None)
    with tempfile.TemporaryDirectory(prefix="axiom-fluid-conformance-") as temporary:
        directory = Path(temporary)
        for name,repo,paths in (("FluidBuilder","gtceu",extraction.PATHS),("NativeFluid","cleanroom",extraction.FORGE)):
            original = extraction.extract(name,originals[repo,paths[name]])
            # Distinct compiled source class, shared explicitly audited dependency ports.
            original = re.sub(r"\b" + name + r"\b", "Original" + name, original)
            (directory / ("Original" + name + ".java")).write_text(original)
        (directory / "LockedSusyProducer.java").write_text(producer_source(originals))
        (directory / "FluidConformance.java").write_bytes(driver)
        classpath = os.pathsep.join(map(str,jars))
        subprocess.run([str(java / "bin/javac"),"--release","25","-proc:none","-cp",classpath,
                        "-d",str(directory),*map(str,directory.glob("*.java"))],cwd=directory,env=environment,check=True,timeout=60)
        run = subprocess.run([str(java / "bin/java"),"-Xmx256m","-cp",str(directory) + os.pathsep + classpath,
                              "research.orthrus.axiom.FluidConformance",str(root)],
                             cwd=directory,env=environment,capture_output=True,text=True,timeout=120)
        if run.returncode:
            raise AssertionError("native fluid comparison failed:\n" + run.stdout[-4000:] + run.stderr[-6000:])
        result = json.loads(run.stdout)
        if (result.get("builderScenarios") != 4096 or result.get("scalarScenarios") != 4096
                or result.get("producerDeclarations") != 10 or result.get("wholePackParity") is not False):
            raise ValueError("incomplete fluid comparison")
    if (LOCK.read_bytes()!=raw_lock or TARGET_LOCK.read_bytes()!=raw_target or DRIVER.read_bytes()!=driver
            or Path(extraction.__file__).read_bytes()!=recipe or retained_inputs(originals)!=retained
            or Path(construction.__file__).read_bytes()!=construction_recipe
            or Path(stacks.__file__).read_bytes()!=stack_recipe
            or registry_inputs.POLICY.read_bytes()!=registry_policy
            or (LOCK.parent / "cleanroom-events.lock.json").read_bytes()!=cleanroom_lock
            or engine_inputs(home,raw_target)[0]!=manifest_raw
            or sha256(source_jars[0].read_bytes()).hexdigest()!=source_jar_digest
            or {p.name:p.read_bytes() for p in MATERIAL_ROOT.glob("*.java")}!=shared
            or verify_references(roots,lock)!=originals):
        raise ValueError("fluid comparison inputs changed during execution")
    registry_inputs.verify(root)
    for row in runtime["runtimeFiles"] + runtime["compilerFiles"]:
        checked_path(java,row)
    if args.report is not None:
        report = {"schema":"axiom.fluid-conformance.v1","status":"passed","result":result,
                  "sourceLockSha256":sha256(raw_lock).hexdigest(),"sourceRevisions":lock["revisions"],
                  "driverSha256":sha256(driver).hexdigest(),"extractionRecipeSha256":sha256(recipe).hexdigest(),
                  "constructionRecipeSha256":sha256(construction_recipe).hexdigest(),
                  "stackRecipeSha256":sha256(stack_recipe).hexdigest(),
                  "registryPolicySha256":sha256(registry_policy).hexdigest(),"engineJars":manifest["jars"],
                  "sourcesJarSha256":source_jar_digest,"runtimeInputs":runtime["runtimeFiles"]+runtime["compilerFiles"],
                  "sourceInputs":{repo+":"+path:sha256(raw.encode()).hexdigest() for (repo,path),raw in originals.items()},
                  "retainedSources":{name:sha256(raw.encode()).hexdigest() for name,raw in retained.items()},
                  "sharedSourceBindings":{name:sha256(raw).hexdigest() for name,raw in shared.items()},
                  "substitutions":["Package/types/annotations relocated; no legacy namespace adapters",
                      "Original FluidBuilder and scalar Forge Fluid compiled independently; shared dependency extraction",
                      "Material registry, selected property catalog, native utility binding, attributes and isolated event boundary shared",
                      "Whole Susy initializer and susyId method compiled from locked source in temporary space; namespace from gradle.properties",
                      "No-listener isolated registry; server texture identifiers; lazy tooltip binding only"],
                  "notQualified":["vanilla bootstrap and default fluid universe","original Forge event dispatch and pack listeners",
                      "installed transforms/source-artifact equivalence","client resources/world blocks/addons",
                      "full material catalog/ores","recipe registration or machine decisions","whole-pack parity"]}
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report,stream,indent=2); stream.write("\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
