#!/usr/bin/env python3
"""Compare native fluid stacks and default rebinding; never qualify vanilla identities."""
import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

import axiom_stack_sources as extraction
import axiom_construction_sources as construction
import axiom_fluid_sources as fluids
import axiom_registry_runtime as registry_inputs
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK, MATERIAL_ROOT
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/native-fluid-stacks.lock.json"
EVENT_LOCK = ROOT / "modules/axiom/sources/cleanroom-events.lock.json"
DRIVER = ROOT / "modules/axiom/tests/oracles/FluidStackConformance.java"
PATHS = {
    **{name: ("cleanroom", path) for name, path in {**extraction.PATHS, **fluids.FORGE}.items()},
    **{name: ("gtceu", fluids.PATHS[name]) for name in ("FluidMaterial", "FluidProperty")},
}


def verify_references(roots, lock):
    if lock.get("schema") != "axiom.native-fluid-stack-source-lock.v1" or set(lock["revisions"]) != {"cleanroom", "gtceu"}:
        raise ValueError("invalid fluid-stack source lock")
    result = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        if repo not in lock["revisions"] or (repo, path) in result:
            raise ValueError("unexpected or duplicate fluid-stack source")
        revision = lock["revisions"][repo]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("invalid fluid-stack revision")
        raw = git(roots[repo], "show", revision + ":" + path)
        blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != row["gitBlob"] or sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("fluid-stack source identity differs: " + path)
        result[repo,path] = raw.decode()
    if set(result) != set(PATHS.values()):
        raise ValueError("fluid-stack source closure differs")
    return result


def extracted(originals):
    return {name: (extraction.extract(name, originals[key]) if name in extraction.PATHS else
                   fluids.extract(name, originals[key])) for name, key in PATHS.items()}


def retained_inputs(originals, root=MATERIAL_ROOT):
    retained = {name: (root / (name + ".java")).read_text() for name in PATHS}
    if retained != extracted(originals):
        raise ValueError("retained fluid-stack differs from source extraction")
    return retained


def edited_source(originals):
    path = PATHS["NativeFluidStack"]
    source = originals[path]
    if source.count("this.amount = amount;") != 1:
        raise ValueError("stack amount source-edit boundary differs")
    return {**originals, path: source.replace("this.amount = amount;", "this.amount = amount + 1;", 1)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cleanroom", "gtceu", "java-home", "engine-home", "registry-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report is not None and (args.report.exists() or args.report.is_symlink()):
        raise ValueError("report must be new")
    raw_lock, raw_target, raw_event, driver = LOCK.read_bytes(), TARGET_LOCK.read_bytes(), EVENT_LOCK.read_bytes(), DRIVER.read_bytes()
    recipes = {Path(module.__file__): Path(module.__file__).read_bytes() for module in (extraction, fluids, construction)}
    lock = json.loads(raw_lock)
    if (json.loads(raw_event)["revision"] != lock["revisions"]["cleanroom"] or
            next(row["commit"] for row in json.loads(raw_target)["repositories"] if row["id"] == "gtceu") != lock["revisions"]["gtceu"]):
        raise ValueError("fluid-stack revision differs from selected events")
    roots = {"cleanroom": args.cleanroom, "gtceu": args.gtceu}
    originals = verify_references(roots, lock)
    retained = retained_inputs(originals)
    java = args.java_home.resolve(strict=True)
    runtime = verify_runtime(java, compiler=True)
    registry = registry_inputs.verify(args.registry_root).resolve(strict=True)
    registry_policy = registry_inputs.POLICY.read_bytes()
    home = args.engine_home.resolve(strict=True)
    manifest_raw, manifest, jars = engine_inputs(home, raw_target)
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as archive:
        if archive.read("axiom/native-fluid-stacks.lock.json") != raw_lock:
            raise ValueError("installed fluid-stack lock differs")
        if archive.read("axiom/registry-runtime.json") != registry_policy:
            raise ValueError("installed native metadata policy differs")
    sources = list((home / "sources").glob("*-sources.jar"))
    if len(sources) != 1:
        raise ValueError("one installed source archive required")
    sources_digest = sha256(sources[0].read_bytes()).hexdigest()
    shared = {p.relative_to(MATERIAL_ROOT).as_posix(): p.read_bytes() for p in MATERIAL_ROOT.rglob("*.java")}
    with zipfile.ZipFile(sources[0]) as archive:
        for path, raw in shared.items():
            if archive.read("research/orthrus/axiom/" + path) != raw:
                raise ValueError("installed shared source differs: " + path)
    for row in json.loads(registry_policy)["runtimeFiles"]:
        name = Path(row["path"]).name
        if name.startswith(("guava-", "commons-lang3-")) and manifest["jars"].get(name) != row["sha256"]:
            raise ValueError("Forge collection library differs: " + name)
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    classpath = os.pathsep.join(map(str, jars))
    changed = edited_source(originals)
    with tempfile.TemporaryDirectory(prefix="axiom-fluid-stack-") as temporary:
        directory = Path(temporary)
        def compile_group(name, contents):
            root = directory / name; root.mkdir()
            for filename, source in contents.items():
                (root / (filename + ".java")).write_text(source)
            subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", classpath,
                            "-d", str(root), *map(str, root.glob("*.java"))],
                           env=environment, cwd=directory, check=True, timeout=60)
            return str(root)
        probe = compile_group("probe", {"FluidStackConformance": driver.decode()})
        original = compile_group("original", extracted(originals))
        edited = compile_group("edited", extracted(changed))
        def run(prefixes, language="en", mode="normal"):
            result = subprocess.run([str(java / "bin/java"), "-Xmx256m", "-Duser.language=" + language,
                                     "-cp", os.pathsep.join([*prefixes, probe, classpath]),
                                     "research.orthrus.axiom.FluidStackConformance", str(registry), mode],
                                    cwd=directory, env=environment, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError("fluid-stack comparison failed:\n" + result.stdout[-3000:] + result.stderr[-6000:])
            return json.loads(result.stdout)
        baselines = []
        for language in ("en", "tr"):
            baseline = run([], language)
            if (baseline != run([original], language) or baseline.get("stackVectors") != 8192 or baseline.get("nbtVectors") != 4096 or baseline.get("scenarios") != 6
                    or baseline.get("wholePackParity") is not False or baseline.get("kernelIsolation") is not True
                    or baseline.get("minecraftLaunched") is not False):
                raise ValueError("native fluid-stack comparison differs or is incomplete")
            baselines.append({"language": language, **baseline})
        witness = run([], mode="source-edit")
        candidate = run([edited], mode="source-edit")
        if (witness != run([original], mode="source-edit") or witness.get("amount") != 7
                or candidate != {**witness, "amount": 8}):
            raise ValueError("source-edit witness does not show the bounded stack amount effect")
    if (LOCK.read_bytes() != raw_lock or TARGET_LOCK.read_bytes() != raw_target or EVENT_LOCK.read_bytes() != raw_event
            or DRIVER.read_bytes() != driver or any(p.read_bytes() != raw for p, raw in recipes.items())
            or retained_inputs(originals) != retained or verify_references(roots, lock) != originals
            or registry_inputs.POLICY.read_bytes() != registry_policy or engine_inputs(home, raw_target)[0] != manifest_raw
            or sha256(sources[0].read_bytes()).hexdigest() != sources_digest
            or {p.relative_to(MATERIAL_ROOT).as_posix(): p.read_bytes() for p in MATERIAL_ROOT.rglob("*.java")} != shared):
        raise ValueError("fluid-stack inputs changed during comparison")
    registry_inputs.verify(registry)
    for row in runtime["runtimeFiles"] + runtime["compilerFiles"]:
        checked_path(java, row)
    report = {"schema": "axiom.native-fluid-stack-conformance.v1", "status": "passed", "baselines": baselines,
              "sourceEdit": {"baseline": witness, "edited": candidate}, "sourceLockSha256": sha256(raw_lock).hexdigest(),
              "sourceRevisions": lock["revisions"], "eventLockSha256": sha256(raw_event).hexdigest(),
              "driverSha256": sha256(driver).hexdigest(), "extractionRecipes": {p.name: sha256(raw).hexdigest() for p, raw in recipes.items()},
              "engineJars": manifest["jars"], "sourcesJarSha256": sources_digest,
              "runtimeInputs": runtime["runtimeFiles"] + runtime["compilerFiles"], "registryPolicySha256": sha256(registry_policy).hexdigest(),
              "sourceInputs": {repo + ":" + path: sha256(src.encode()).hexdigest() for (repo, path), src in originals.items()},
              "editedSourceSha256": sha256(changed[PATHS["NativeFluidStack"]].encode()).hexdigest(),
              "sharedSources": {path: sha256(raw).hexdigest() for path, raw in shared.items()},
              "substitutions": ["Relocated Forge FluidStack excluding item-container equality and client localization",
                                "Native in-memory fluid default/delegate lifecycle; block lookup cache is not exposed",
                                "Selected original NBT bytecode through typed identity-preserving bindings; no copied NBT implementation",
                                "Existing explicit active-owner and registration event ports; no Loader or listener universe",
                                "Native GT material fluid-stack overloads; controlled fluid fixtures, not WATER/LAVA identities"],
              "notQualified": ["vanilla blocks and WATER/LAVA bootstrap", "actual enchantments", "file/network NBT persistence",
                               "item containers/capabilities and client localization", "native config and Loader ownership integration",
                               "GT/Susy/pack/addon complete producers", "applied mixins and installed composition",
                               "recipe or machine validity", "whole-pack parity"]}
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
    print(json.dumps({"baselines": baselines, "sourceEdit": report["sourceEdit"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
