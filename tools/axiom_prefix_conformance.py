#!/usr/bin/env python3
"""Qualify native prefix/marker source retention; fixture catalogs are NOT pack membership."""
import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

import axiom_prefix_sources as extraction
import axiom_construction_sources as construction
import axiom_fluid_sources as fluids
import axiom_registry_runtime as registry_inputs
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK, MATERIAL_ROOT
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/native-prefixes.lock.json"
DRIVER = ROOT / "modules/axiom/tests/oracles/PrefixConformance.java"
PATHS = {**extraction.PATHS, "MaterialVoltages": construction.PATHS["MaterialVoltages"]}


def fixture_metadata(originals):
    prefix = originals[PATHS["OrePrefix"]]
    return {"materialFields": sorted(set(re.findall(r"\bMaterials\.(\w+)", prefix))),
            "prefixDeclarations": len(re.findall(r"public static final OrePrefix \w+ = new OrePrefix\(", prefix)),
            "iconDeclarations": len(re.findall(r"public static final MaterialIconType \w+ = new MaterialIconType\(", originals[PATHS["MaterialIconType"]]))}


def verify_references(root, lock):
    if lock.get("schema") != "axiom.native-prefix-source-lock.v1" or set(lock["revisions"]) != {"gtceu"}:
        raise ValueError("invalid prefix source lock")
    revision = lock["revisions"]["gtceu"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid prefix revision")
    result = {}
    for row in lock["references"]:
        path = ordinary_path(row["path"])
        if row["repository"] != "gtceu" or path in result:
            raise ValueError("unexpected or duplicate prefix source")
        raw = git(root, "show", revision + ":" + path)
        blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != row["gitBlob"] or sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("prefix source identity differs: " + path)
        result[path] = raw.decode()
    if set(result) != set(PATHS.values()) or fixture_metadata(result) != lock.get("fixtureMetadata"):
        raise ValueError("prefix source closure or fixture metadata differs")
    return result


def extracted(originals):
    return {name: (construction.extract(name, originals[path]) if name == "MaterialVoltages" else
                   extraction.extract(name, originals[path])) for name, path in PATHS.items()}


def retained_inputs(originals, root=MATERIAL_ROOT):
    retained = {name: (root / (name + ".java")).read_text() for name in PATHS}
    if retained != extracted(originals):
        raise ValueError("retained prefix differs from source extraction")
    return retained


def edited_source(originals):
    path = PATHS["OrePrefix"]
    before = 'new OrePrefix("dustTiny", M / 9,'
    if originals[path].count(before) != 1:
        raise ValueError("dustTiny source-edit boundary differs")
    return {**originals, path: originals[path].replace(before, 'new OrePrefix("dustTiny", M / 10,')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gtceu", "java-home", "engine-home", "registry-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report is not None and (args.report.exists() or args.report.is_symlink()):
        raise ValueError("report must be new")
    raw_lock, raw_target, driver = LOCK.read_bytes(), TARGET_LOCK.read_bytes(), DRIVER.read_bytes()
    recipes = {Path(module.__file__): Path(module.__file__).read_bytes() for module in (extraction, construction, fluids)}
    lock = json.loads(raw_lock)
    if next(row["commit"] for row in json.loads(raw_target)["repositories"] if row["id"] == "gtceu") != lock["revisions"]["gtceu"]:
        raise ValueError("prefix revision differs from selected target")
    originals = verify_references(args.gtceu, lock)
    retained = retained_inputs(originals)
    java = args.java_home.resolve(strict=True)
    runtime = verify_runtime(java, compiler=True)
    registry = registry_inputs.verify(args.registry_root).resolve(strict=True)
    registry_policy = registry_inputs.POLICY.read_bytes()
    home = args.engine_home.resolve(strict=True)
    manifest_raw, manifest, jars = engine_inputs(home, raw_target)
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as archive:
        if archive.read("axiom/native-prefixes.lock.json") != raw_lock:
            raise ValueError("installed prefix lock differs")
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
            raise ValueError("prefix collection library differs: " + name)
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    classpath = os.pathsep.join(map(str, jars))
    metadata = fixture_metadata(originals)
    changed = edited_source(originals)
    with tempfile.TemporaryDirectory(prefix="axiom-prefix-") as temporary:
        directory = Path(temporary)
        def compile_group(name, contents):
            root = directory / name; root.mkdir()
            for filename, source in contents.items():
                (root / (filename + ".java")).write_text(source)
            subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", classpath,
                            "-d", str(root), *map(str, root.glob("*.java"))],
                           env=environment, cwd=directory, check=True, timeout=60)
            return str(root)
        probe = compile_group("probe", {"PrefixConformance": driver.decode()})
        original = compile_group("original", extracted(originals))
        edited = compile_group("edited", extracted(changed))
        def run(prefixes, unique):
            result = subprocess.run([str(java / "bin/java"), "-Xmx256m", "-cp", os.pathsep.join([*prefixes, probe, classpath]),
                                     "research.orthrus.axiom.PrefixConformance", str(registry), ",".join(metadata["materialFields"]),
                                     str(unique).lower(), str(metadata["prefixDeclarations"]), str(metadata["iconDeclarations"])],
                                    cwd=directory, env=environment, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError("prefix comparison failed:\n" + result.stdout[-3000:] + result.stderr[-6000:])
            return json.loads(result.stdout)
        baselines, edits = [], []
        for unique in (False, True):
            baseline = run([], unique)
            if (baseline != run([original], unique) or baseline.get("vectors") != 4096 or
                    baseline.get("wholePackParity") is not False or baseline.get("kernelIsolation") is not True):
                raise ValueError("native prefix comparison differs or is incomplete")
            candidate = run([edited], unique)
            if (baseline["catalogDigest"] == candidate["catalogDigest"] or baseline["markerDigest"] != candidate["markerDigest"] or
                    baseline["traceDigest"] != candidate["traceDigest"]):
                raise ValueError("source-edit witness does not show the bounded prefix amount effect")
            baselines.append(baseline); edits.append(candidate)
        if baselines[0]["catalogDigest"] == baselines[1]["catalogDigest"]:
            raise ValueError("unique stone configuration has no observed effect")
    if (LOCK.read_bytes() != raw_lock or TARGET_LOCK.read_bytes() != raw_target or DRIVER.read_bytes() != driver
            or any(p.read_bytes() != raw for p, raw in recipes.items()) or retained_inputs(originals) != retained
            or verify_references(args.gtceu, lock) != originals or registry_inputs.POLICY.read_bytes() != registry_policy
            or engine_inputs(home, raw_target)[0] != manifest_raw or sha256(sources[0].read_bytes()).hexdigest() != sources_digest
            or {p.relative_to(MATERIAL_ROOT).as_posix(): p.read_bytes() for p in MATERIAL_ROOT.rglob("*.java")} != shared):
        raise ValueError("prefix inputs changed during comparison")
    registry_inputs.verify(registry)
    for row in runtime["runtimeFiles"] + runtime["compilerFiles"]:
        checked_path(java, row)
    report = {"schema": "axiom.native-prefix-conformance.v1", "status": "passed", "baselines": baselines, "edited": edits,
              "sourceLockSha256": sha256(raw_lock).hexdigest(), "sourceRevisions": lock["revisions"],
              "driverSha256": sha256(driver).hexdigest(), "extractionRecipes": {p.name: sha256(raw).hexdigest() for p, raw in recipes.items()},
              "engineJars": manifest["jars"], "sourcesJarSha256": sources_digest,
              "runtimeInputs": runtime["runtimeFiles"] + runtime["compilerFiles"], "registryPolicySha256": sha256(registry_policy).hexdigest(),
              "sourceInputs": {path: sha256(src.encode()).hexdigest() for path, src in originals.items()},
              "editedSourceSha256": sha256(changed[PATHS["OrePrefix"]].encode()).hexdigest(),
              "sharedSources": {path: sha256(raw).hexdigest() for path, raw in shared.items()},
              "substitutions": ["Relocated native prefix/marker/icon metadata; annotations removed; client localization API not exposed",
                                "Shared native material builder, selected JVM, native Minecraft dye enum and collection implementations",
                                "Explicit synthetic material field inputs and live configuration fixtures; NOT GT producers or native config loading",
                                "Existing server texture identifier projection; NOT client resource-pack lookup"],
              "notQualified": ["GT/Susy/pack/addon producers", "native Forge configuration loading", "applied mixins and installed composition",
                               "generated item/fluid/ore membership", "recipe or machine validity", "whole-pack parity"]}
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
    print(json.dumps({"baselines": baselines, "edited": edits}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
