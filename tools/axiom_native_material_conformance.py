#!/usr/bin/env python3
"""Qualify complete selected producers in one native Cleanroom material/fluid context.

Offline, developer-only. Owner/listener fixtures are explicit, not mod discovery.
Separately acquired producer sources and compiled test programs remain temporary.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

import axiom_native_material_sources as linking
import build_axiom_native_materials as build
import axiom_native_identity_conformance as identities
import axiom_construction_conformance as construction
import axiom_construction_sources as construction_sources
import axiom_fluid_conformance as fluids
import axiom_fluid_sources as fluid_sources
import axiom_prefix_conformance as prefixes
import axiom_registry_sources as registry_sources
import axiom_material_sources as material_sources
import axiom_bootstrap_sources as lifecycle_sources
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK
from axiom_runtime import checked_path, verify_runtime
from build_axiom_target import git

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "modules/axiom/tests/oracles/NativeMaterialConformance.java"
FIXTURE = ROOT / "modules/axiom/tests/oracles/NativeProducerConformance.java"
HOST_AUDIT = tuple("src/main/java/net/minecraftforge/fml/common/" + p + ".java"
                   for p in ("Loader", "LoadController", "DummyModContainer", "ModMetadata"))


def original_sources(roots, shared):
    co = construction.verify_references(roots["gtceu"], json.loads(construction.LOCK.read_bytes()))
    fo = fluids.verify_references(roots, json.loads(fluids.LOCK.read_bytes()))
    po = prefixes.verify_references(roots["gtceu"], json.loads(prefixes.LOCK.read_bytes()))
    target = json.loads(TARGET_LOCK.read_bytes())
    revisions = {r["id"]: r["commit"] for r in target["repositories"]}
    for lock in (construction.LOCK, fluids.LOCK, prefixes.LOCK):
        for repo, revision in json.loads(lock.read_bytes())["revisions"].items():
            expected = (json.loads(identities.LOCK.read_bytes())["revisions"]["cleanroom"] if repo == "cleanroom" else revisions[repo])
            if revision != expected: raise ValueError("native material source revisions differ")
    extra = {}
    paths = set(registry_sources.PATHS.values()) | {registry_sources.FLUID_PATH, material_sources.MATERIAL, material_sources.MANAGER} | set(material_sources.PATHS)
    for path in sorted(paths):
        row = next(r for r in target["references"] if r["repository"] == "gtceu" and r["path"] == path)
        raw = git(roots["gtceu"], "show", revisions["gtceu"] + ":" + path)
        if sha256(raw).hexdigest() != row["sha256"]: raise ValueError("native material source differs: " + path)
        extra[path] = raw.decode()
    regenerated = {**construction.extracted(co), **prefixes.extracted(po)}
    regenerated.update({n: fluid_sources.extract(n, fo["gtceu", p]) for n,p in fluid_sources.PATHS.items()})
    regenerated.update({n: registry_sources.extract(n, extra[p]) for n,p in registry_sources.PATHS.items()})
    regenerated["FluidRegistration"] = registry_sources.fluid_storage(extra[registry_sources.FLUID_PATH])
    regenerated["MaterialLifecycle"] = lifecycle_sources.sources(roots["gtceu"])["MaterialLifecycle.java"]
    regenerated.update({n: material_sources.extract(n, extra[material_sources.PREFIX + n + ".java"]) for n in material_sources.CLASSES})
    material_sources.checked_carriers(extra, shared["MaterialState"], shared["MaterialPhase"])
    if any(shared[n] != text for n,text in regenerated.items()): raise ValueError("native material retained source differs from immutable source extraction")
    expected_ports = {"Failure", "FluidDomain", "MaterialState", "MaterialPhase", "PrefixDependencies", "ConstructionDependencies"}
    if set(shared) - set(regenerated) != expected_ports: raise ValueError("native material source/host coverage differs")
    # These are explicit host carriers, not independently recovered algorithms.
    regenerated.update({n: shared[n] for n in expected_ports})
    evidence = {"gtceu:"+p: sha256(t.encode()).hexdigest() for p,t in {**co, **po, **extra}.items()}
    evidence.update({r+":"+p: sha256(t.encode()).hexdigest() for (r,p),t in fo.items()})
    cleanroom_revision = json.loads(identities.LOCK.read_bytes())["revisions"]["cleanroom"]
    for path in HOST_AUDIT:
        evidence["cleanroom:"+path] = sha256(git(roots["cleanroom"], "show", cleanroom_revision + ":" + path)).hexdigest()
    return regenerated, co, fo, evidence


def producer_files(co, fo):
    files = {**construction_sources.producer_sources(co), "LockedSusyProducer": fluids.producer_source(fo)}
    return {**{n: linking.linked(n,t) for n,t in files.items()}, "NativeProducerConformance": FIXTURE.read_text()}


def qualify(java, home, images, libraries, program, roots, report):
    java, home, images, libraries, program = [identities.ordinary(p).resolve(strict=True) for p in (java,home,images,libraries,program)]
    report = identities.ordinary(report)
    if report.exists(): raise ValueError("native material qualification report must be new")
    tracked = [DRIVER, FIXTURE, TARGET_LOCK, identities.POLICY, identities.LOCK, identities.PLATFORM,
               *sorted((ROOT / "modules/axiom/sources").glob("*.lock.json")),
               *sorted((ROOT / "tools").glob("axiom_*.py")), Path(build.__file__),
               *sorted(linking.HOST.glob("*.java")), *[linking.SHARED / (n+".java") for n in linking.NAMES]]
    frozen = {p: p.read_bytes() for p in tracked}
    program_files = {p.name: p.read_bytes() for p in program.iterdir() if p.is_file()}
    if set(program_files) != {"program.json", "native-materials.jar", "native-materials-sources.jar"}:
        raise ValueError("native material program inventory differs")
    policy = json.loads(frozen[identities.POLICY]); runtime = verify_runtime(java, compiler=True)
    shared, host, engine_manifest, _ = build.engine_sources(home)
    regenerated, co, fo, evidence = original_sources(roots, shared)
    _, manifest, jars = engine_inputs(home, frozen[TARGET_LOCK])
    dependencies = [images/r["path"] for r in policy["images"]] + [libraries/r["path"] for r in policy["libraries"]]
    environment = {k:v for k,v in os.environ.items() if k not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}}
    with tempfile.TemporaryDirectory(prefix="axiom-native-material-conformance-") as temporary:
        work = Path(temporary)
        # Installed source program must reproduce exactly, including its custody manifest.
        build.build(java, home, images, libraries, work / "rebuilt")
        if any((work / "rebuilt" / n).read_bytes() != raw for n,raw in program_files.items()):
            raise ValueError("native material program differs from installed-source rebuild")
        original_classes = build.compile_sources(java, linking.assemble(regenerated, host), dependencies, work / "source")
        build.jar_bytes(work / "source.jar", original_classes)
        if (work / "source.jar").read_bytes() != program_files["native-materials.jar"]:
            raise ValueError("native material classes differ from upstream-source rebuild")
        driver = build.compile_sources(java, {DRIVER.stem: frozen[DRIVER].decode()}, jars, work / "driver")
        build.jar_bytes(work / "driver.jar", driver)
        def fixture(name, original):
            classes = build.compile_sources(java, producer_files(original,fo), [program / "native-materials.jar", *dependencies], work / name)
            build.jar_bytes(work / (name+".jar"), classes)
            return work / (name+".jar")
        baseline = fixture("producer", co)
        edited = fixture("edited", construction.edited_producer(co))
        def run(producer, mode, language="en", kernel=None, expected_digest=None, rejection=None):
            kernel = kernel or program / "native-materials.jar"
            command = [str(java/"bin/java"), "--enable-native-access=ALL-UNNAMED", "-Xmx512m", "-Duser.language="+language,
                       "-cp", os.pathsep.join(map(str,[work/"driver.jar", *jars])), "research.orthrus.axiom.NativeMaterialConformance",
                       str(images), str(libraries), str(kernel), expected_digest or sha256(kernel.read_bytes()).hexdigest(),
                       str(producer), sha256(producer.read_bytes()).hexdigest(), mode]
            result = subprocess.run(command, cwd=work, env=environment, capture_output=True, text=True, timeout=60)
            if rejection:
                if result.returncode == 0 or rejection not in result.stderr: raise ValueError("native material negative input not rejected")
                return
            if result.returncode: raise ValueError("native material execution failed:\n" + result.stdout[-2000:] + result.stderr[-7000:])
            value = json.loads(result.stdout)
            if value.get("kernelIsolation") is not True or any(value.get(k) is not False for k in ("wholePackParity", "minecraftLaunched")):
                raise ValueError("native material execution boundary differs")
            return value
        runs = []
        for mode in ("producers", "reuse", "listener-failure"):
            left, right = run(baseline,mode), run(baseline,mode,"tr")
            if left != right: raise ValueError("native material locale comparison differs: " + mode)
            runs.append({"mode": mode, "languages": ["en","tr"], **left})
        original = runs[0]["result"]
        changed = run(edited,"producers")["result"]
        differences = [n for n in original["materials"] if original["materials"][n] != changed["materials"][n]]
        expected = deepcopy(original)
        aluminium = expected["materials"]["gregtech:aluminium"]
        aluminium[1] += 1
        for fluid in aluminium[3]: fluid[6] += 1
        if differences != ["gregtech:aluminium"] or changed != expected:
            raise ValueError("native producer source-edit witness differs: " + repr(differences) + ", sameEvents=" + str(original["events"] == changed["events"]))
        run(baseline,"producers", expected_digest="0"*64, rejection="program digest differs")
        build.jar_bytes(work/"escaped.jar", {"net/minecraft/Unexpected.class": b"not executable"})
        run(baseline,"producers", kernel=work/"escaped.jar", rejection="program class boundary differs")
        (work/"duplicate.jar").write_bytes(baseline.read_bytes())
        run(baseline,"producers", kernel=work/"duplicate.jar", rejection="program class boundary differs")
        result = {"schema": "axiom.native-material-conformance.v1", "status": "passed",
                  "engineManifestSha256": sha256(engine_manifest).hexdigest(), "engineJars": manifest["jars"],
                  "programManifestSha256": sha256(program_files["program.json"]).hexdigest(),
                  "programSha256": sha256(program_files["native-materials.jar"]).hexdigest(),
                  "sourceInputs": evidence, "qualificationInputs": {p.relative_to(ROOT).as_posix():sha256(raw).hexdigest() for p,raw in frozen.items()},
                  "images": policy["images"], "libraryInputs": policy["libraries"], "runtimeInputs":runtime["runtimeFiles"]+runtime["compilerFiles"],
                  "runs": runs, "sourceEditWitness":{"material":"gregtech:aluminium", "before":original["materials"]["gregtech:aluminium"],
                      "after":changed["materials"]["gregtech:aluminium"], "allOtherResultsUnchanged":True}, "negativeInputs":3,
                  "producerPrograms":{p.stem:sha256(p.read_bytes()).hexdigest() for p in (baseline,edited)},
                  "sourceRebuildIdentical":True, "ownerUniverse":"explicit-native-container-fixtures",
                  "wholePackParity":False, "fullVanillaBootstrap":False, "installedCompositionQualified":False}
    if any(p.read_bytes()!=raw for p,raw in frozen.items()) or original_sources(roots,shared)[3] != evidence:
        raise ValueError("native material qualification inputs changed")
    if engine_inputs(home,frozen[TARGET_LOCK])[0] != engine_manifest or any((program/n).read_bytes()!=raw for n,raw in program_files.items()):
        raise ValueError("native material installed program changed")
    for root,rows in ((images,policy["images"]),(libraries,policy["libraries"]),(java,runtime["runtimeFiles"]+runtime["compilerFiles"])):
        for row in rows: checked_path(root,row)
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open("x") as out: json.dump(result,out,indent=2); out.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("java-home","engine-home","images","library-root","program","gtceu","susy-core","cleanroom","report"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args(argv)
    result=qualify(args.java_home,args.engine_home,args.images,args.library_root,args.program,
                   {"gtceu":args.gtceu,"susy-core":args.susy_core,"cleanroom":args.cleanroom},args.report)
    print(json.dumps({"status":result["status"],"runs":len(result["runs"]),"sourceEditWitness":result["sourceEditWitness"]["material"],"wholePackParity":False}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
