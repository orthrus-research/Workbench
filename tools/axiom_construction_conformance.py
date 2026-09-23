#!/usr/bin/env python3
"""Qualify source-retaining material construction and the complete GT element producer.

Developer-only, not an admitted pack bootstrap endpoint. Both runs use actual
selected JVMs; original source is independently compiled in temporary space.
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

import axiom_construction_sources as extraction
import axiom_fluid_sources as fluids
import axiom_material_sources as materials
import axiom_registry_runtime as registry_inputs
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK, MATERIAL_ROOT
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/material-construction.lock.json"
DRIVER = ROOT / "modules/axiom/tests/oracles/ConstructionConformance.java"
PATHS = {**extraction.PATHS, "FluidMaterial": fluids.PATHS["FluidMaterial"],
         "MaterialFlags": fluids.PATHS["MaterialFlags"], "PropertyKey": materials.PREFIX + "PropertyKey.java"}


def verify_references(root, lock):
    if lock.get("schema") != "axiom.material-construction-source-lock.v1" or set(lock["revisions"]) != {"gtceu"}:
        raise ValueError("invalid construction source lock")
    revision = lock["revisions"]["gtceu"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid construction revision")
    result = {}
    for row in lock["references"]:
        path = ordinary_path(row["path"])
        if row["repository"] != "gtceu" or path in result:
            raise ValueError("unexpected or duplicate construction source")
        raw = git(root, "show", revision + ":" + path)
        blob = sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != row["gitBlob"] or sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("construction source identity differs: " + path)
        result[path] = raw.decode()
    if set(result) != set(PATHS.values()) | set(extraction.PRODUCER_PATHS.values()):
        raise ValueError("construction source closure differs")
    return result


def extracted(originals):
    return {name: (extraction.extract(name, originals[path]) if name in extraction.PATHS else
                   materials.extract(name, originals[path]) if name == "PropertyKey" else
                   fluids.extract(name, originals[path])) for name, path in PATHS.items()}


def retained_inputs(originals, root=MATERIAL_ROOT):
    retained = {name: (root / (name + ".java")).read_text() for name in PATHS}
    if retained != extracted(originals):
        raise ValueError("retained construction differs from source extraction")
    return retained


def edited_producer(originals):
    path = extraction.PRODUCER_PATHS["elements"]
    source = originals[path]
    marker = 'Aluminium = new Material.Builder(2, gregtechId("aluminium"))'
    if source.count(marker) != 1:
        raise ValueError("Aluminium declaration boundary differs")
    start = source.index(marker)
    end = source.index(";", start)
    declaration = source[start:end]
    before, after = ".color(0x80C8F0)", ".color(0x80C8F1)"
    if declaration.count(before) != 1:
        raise ValueError("Aluminium color boundary differs")
    edited = source[:start] + declaration.replace(before, after) + source[end:]
    return {**originals, path: edited}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gtceu", "java-home", "engine-home", "registry-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report is not None and (args.report.exists() or args.report.is_symlink()):
        raise ValueError("report must be new")
    raw_lock, raw_target, driver = LOCK.read_bytes(), TARGET_LOCK.read_bytes(), DRIVER.read_bytes()
    recipes = {Path(module.__file__): Path(module.__file__).read_bytes() for module in (extraction, fluids, materials)}
    lock = json.loads(raw_lock)
    if next(row["commit"] for row in json.loads(raw_target)["repositories"] if row["id"] == "gtceu") != lock["revisions"]["gtceu"]:
        raise ValueError("construction revision differs from selected target")
    originals = verify_references(args.gtceu, lock)
    retained = retained_inputs(originals)
    java = args.java_home.resolve(strict=True)
    runtime = verify_runtime(java, compiler=True)
    registry = registry_inputs.verify(args.registry_root).resolve(strict=True)
    registry_policy = registry_inputs.POLICY.read_bytes()
    home = args.engine_home.resolve(strict=True)
    manifest_raw, manifest, jars = engine_inputs(home, raw_target)
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as archive:
        if archive.read("axiom/material-construction.lock.json") != raw_lock:
            raise ValueError("installed construction lock differs")
    sources = list((home / "sources").glob("*-sources.jar"))
    if len(sources) != 1:
        raise ValueError("one installed source archive required")
    sources_digest = sha256(sources[0].read_bytes()).hexdigest()
    shared = {p.relative_to(MATERIAL_ROOT).as_posix(): p.read_bytes() for p in MATERIAL_ROOT.rglob("*.java")}
    with zipfile.ZipFile(sources[0]) as archive:
        for path, raw in shared.items():
            if archive.read("research/orthrus/axiom/" + path) != raw:
                raise ValueError("installed shared source differs: " + path)
    # The pair and immutable-list implementations must be the selected libraries,
    # not merely similarly named artifacts accepted by a mutable caller manifest.
    for row in json.loads(registry_policy)["runtimeFiles"]:
        name = Path(row["path"]).name
        if name.startswith(("guava-", "commons-lang3-")) and manifest["jars"].get(name) != row["sha256"]:
            raise ValueError("construction collection library differs: " + name)
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    classpath = os.pathsep.join(map(str, jars))
    count = originals[extraction.PRODUCER_PATHS["elements"]].count("new Material.Builder(")
    changed = edited_producer(originals)
    with tempfile.TemporaryDirectory(prefix="axiom-construction-") as temporary:
        directory = Path(temporary)
        def compile_group(name, contents, dependencies):
            root = directory / name; root.mkdir()
            for filename, source in contents.items():
                (root / (filename + ".java")).write_text(source)
            subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", dependencies,
                            "-d", str(root), *map(str, root.glob("*.java"))],
                           env=environment, cwd=directory, check=True, timeout=60)
            return str(root)
        probe = compile_group("probe", {**extraction.producer_sources(originals), "ConstructionConformance": driver.decode()}, classpath)
        original = compile_group("original", extracted(originals), classpath)
        edited = compile_group("edited", extraction.producer_sources(changed), classpath)
        def run(prefixes):
            result = subprocess.run([str(java / "bin/java"), "-Xmx256m", "-cp", os.pathsep.join([*prefixes, classpath]),
                                     "research.orthrus.axiom.ConstructionConformance", str(registry), str(count)],
                                    cwd=directory, env=environment, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError("construction comparison failed:\n" + result.stdout[-3000:] + result.stderr[-6000:])
            return json.loads(result.stdout)
        baseline = run([probe])
        independently_compiled = run([original, probe])
        if baseline != independently_compiled or baseline.get("vectors") != 4096 or baseline.get("wholePackParity") is not False:
            raise ValueError("native construction comparison differs or is incomplete")
        candidate = run([edited, probe])
        if (baseline["aluminiumColor"] != 0x80C8F0 or candidate["aluminiumColor"] != 0x80C8F1 or
                baseline["producerDigest"] == candidate["producerDigest"] or baseline["vectorDigest"] != candidate["vectorDigest"]):
            raise ValueError("source-edit witness does not show the expected bounded effect")
    if (LOCK.read_bytes() != raw_lock or TARGET_LOCK.read_bytes() != raw_target or DRIVER.read_bytes() != driver
            or any(p.read_bytes() != raw for p, raw in recipes.items()) or retained_inputs(originals) != retained
            or verify_references(args.gtceu, lock) != originals or registry_inputs.POLICY.read_bytes() != registry_policy
            or engine_inputs(home, raw_target)[0] != manifest_raw or sha256(sources[0].read_bytes()).hexdigest() != sources_digest
            or {p.relative_to(MATERIAL_ROOT).as_posix(): p.read_bytes() for p in MATERIAL_ROOT.rglob("*.java")} != shared):
        raise ValueError("construction inputs changed during comparison")
    registry_inputs.verify(registry)
    for row in runtime["runtimeFiles"] + runtime["compilerFiles"]:
        checked_path(java, row)
    report = {"schema": "axiom.material-construction-conformance.v1", "status": "passed", "baseline": baseline,
              "edited": candidate, "sourceLockSha256": sha256(raw_lock).hexdigest(), "sourceRevisions": lock["revisions"],
              "driverSha256": sha256(driver).hexdigest(), "extractionRecipes": {p.name: sha256(raw).hexdigest() for p, raw in recipes.items()},
              "engineJars": manifest["jars"], "sourcesJarSha256": sources_digest,
              "runtimeInputs": runtime["runtimeFiles"] + runtime["compilerFiles"], "registryPolicySha256": sha256(registry_policy).hexdigest(),
              "sourceInputs": {path: sha256(src.encode()).hexdigest() for path, src in originals.items()},
              "editedSourceSha256": sha256(changed[extraction.PRODUCER_PATHS["elements"]].encode()).hexdigest(),
              "sharedSources": {path: sha256(raw).hexdigest() for path, raw in shared.items()},
              "substitutions": ["Native material bodies and full builder; relocated names/annotations and explicit registry owner",
                                "Shared original JVM/registry utilities and existing fluid/property dependencies",
                                "Opaque tool-enchantment keys; no enchantment identity registry fabricated",
                                "Unresolved ore-prefix ignore operation fails incomplete; lazy presentation is unavailable",
                                "Complete ElementMaterials.register; original Materials static declarations/initializer; its aggregate register method omitted"],
              "notQualified": ["all GT material producer groups", "Susy/pack/addon material bootstrap",
                               "applied mixins/configuration/installed composition", "generated item/fluid/ore registry", "recipe or machine validity", "whole-pack parity"]}
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2); stream.write("\n")
    print(json.dumps({"baseline": baseline, "sourceEdit": {"aluminiumColor": candidate["aluminiumColor"], "producerDigest": candidate["producerDigest"]}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
