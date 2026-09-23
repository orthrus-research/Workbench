#!/usr/bin/env python3
"""Qualify source-retained GT material-prefix items on the pinned native JVM."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import axiom_material_item_sources as sources
import axiom_material_block_sources as blocks
import axiom_item_sources as items
import axiom_catalog_sources as catalog
import axiom_native_material_sources as linking
import axiom_native_material_conformance as materials
import axiom_native_identity_conformance as identities
import build_axiom_native_materials as build
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK
from axiom_runtime import checked_path, verify_runtime

ROOT = Path(__file__).resolve().parents[1]
ORACLES = ROOT / "modules/axiom/tests/oracles"
DRIVER = ORACLES / "NativeMaterialItemConformance.java"
FIXTURE = ORACLES / "NativeMaterialItemProbe.java"
TRANSFORM_DRIVER = ORACLES / "NativeCatalogTransformConformance.java"


def without_plate(value):
    """Exact expected trace change when original canGenerate excludes only plate."""
    value = deepcopy(value)
    count = 0
    for item in value["inventory"]["items"]:
        if item["prefix"] == "plate":
            count += len(item["variants"]); item["variants"] = []
    for checkpoint in value["checkpoints"]:
        for item in checkpoint["items"]:
            if item["prefix"] == "plate": item["variants"] = []
    value["events"] = [event for event in value["events"] if event[1][0] != "gregtech:meta_plate"]
    value["checks"]["generatedStackVectors"] -= count
    return value


def qualify(java, home, images, libraries, program, roots, report, *, block_family=False, ore_family=False):
    if block_family and ore_family: raise ValueError("select one qualification family")
    driver_source = ORACLES / ("NativeMaterialOreConformance.java" if ore_family else "NativeMaterialBlockConformance.java") if (block_family or ore_family) else DRIVER
    fixture_source = ORACLES / ("NativeMaterialOreProbe.java" if ore_family else "NativeMaterialBlockProbe.java") if (block_family or ore_family) else FIXTURE
    from axiom_material_ore_sources import verify_sources as verify_ores, retained_sources as retained_ores, addon_sources, LOCK as ORE_LOCK
    from axiom_material_access import DRIVER as ACCESS_DRIVER
    extra_fixtures = [ORACLES / "NativeMaterialBlockProbe.java", ORACLES / "NativeOreAddonProbe.java"] if ore_family else []
    java, home, images, libraries, program = [identities.ordinary(p).resolve(strict=True) for p in (java, home, images, libraries, program)]
    report = identities.ordinary(report)
    if report.exists(): raise ValueError("material item report must be new")
    tracked = [driver_source, fixture_source, *extra_fixtures, ACCESS_DRIVER, TRANSFORM_DRIVER, TARGET_LOCK, identities.POLICY, identities.LOCK, identities.PLATFORM,
               *sorted((ROOT / "modules/axiom/sources").glob("*.lock.json")), *sorted((ROOT / "tools").glob("axiom_*.py")), Path(build.__file__),
               *sorted(linking.HOST.glob("*.java")), *[linking.SHARED / (n + ".java") for n in (*linking.NAMES, "NativeMaterialClassLoader", "NativeVanillaIdentities")]]
    frozen = {p: p.read_bytes() for p in tracked}
    selected = {r: roots[r] for r in ("gtceu", "cleanroom", "groovyscript")}
    originals = sources.verify_sources(selected); item_originals = items.verify_sources(selected)
    block_originals = blocks.verify_sources(selected)
    ore_roots = {r: roots[r] for r in ("gtceu", "susy-core", "cleanroom", "groovyscript")}
    ore_originals = verify_ores(ore_roots)
    producer_roots = {r: roots[r] for r in ("gtceu", "cleanroom", "supersymmetry")}
    producers = catalog.verify_sources(producer_roots)
    policy = json.loads(frozen[identities.POLICY]); runtime = verify_runtime(java, compiler=True)
    identities.verify_policy(policy, json.loads(frozen[identities.LOCK]), json.loads(frozen[identities.PLATFORM]))
    shared, host, engine_manifest, _ = build.engine_sources(home)
    regenerated, _, _, evidence = materials.original_sources({r: roots[r] for r in ("gtceu", "susy-core", "cleanroom")}, shared)
    retained = {**items.retained_sources(item_originals), **sources.retained_sources(originals), **blocks.retained_sources(block_originals), **retained_ores(ore_originals)}
    for name in ("MaterialEvent", "MaterialRegistryEvent", "PostMaterialEvent"):
        retained[name] = catalog.event_source(name, producers["gtceu", catalog.lifecycle.EVENTS + name + ".java"])
    if any(host[n] != text for n, text in retained.items()): raise ValueError("retained material item source differs")
    _, manifest, jars = engine_inputs(home, frozen[TARGET_LOCK])
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as jar:
        if jar.read("axiom/material-items.lock.json") != frozen[sources.LOCK]: raise ValueError("installed material item source lock differs")
        if jar.read("axiom/material-blocks.lock.json") != frozen[blocks.LOCK]: raise ValueError("installed material block source lock differs")
        if jar.read("axiom/material-ores.lock.json") != frozen[ORE_LOCK]: raise ValueError("installed material ore source lock differs")
    with zipfile.ZipFile(next((home / "sources").glob("*-sources.jar"))) as jar:
        for name in ("NativeMaterialClassLoader", "NativeVanillaIdentities", "NativeMaterialAccess"):
            if jar.read("research/orthrus/axiom/" + name + ".java") != frozen[linking.SHARED / (name + ".java")]:
                raise ValueError("installed material item loader differs")
    dependencies = [images / r["path"] for r in policy["images"]] + [libraries / r["path"] for r in policy["libraries"]]
    program_files = {p.name: p.read_bytes() for p in program.iterdir() if p.is_file()}
    if set(program_files) != {"program.json", "native-materials.jar", "native-materials-sources.jar"}: raise ValueError("native program inventory differs")
    environment = {k: v for k, v in os.environ.items() if k not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}}
    with tempfile.TemporaryDirectory(prefix="axiom-material-item-conformance-") as temporary:
        work = Path(temporary)
        build.build(java, home, images, libraries, work / "rebuilt")
        if any((work / "rebuilt" / n).read_bytes() != raw for n, raw in program_files.items()):
            raise ValueError("material item program differs from installed-source rebuild")
        def compile_jar(name, files, deps):
            path = work / (name + ".jar"); build.jar_bytes(path, build.compile_sources(java, files, deps, work / name)); return path
        source_kernel = compile_jar("source-kernel", linking.assemble(regenerated, {**host, **retained}), dependencies)
        if source_kernel.read_bytes() != program_files["native-materials.jar"]:
            raise ValueError("material item kernel differs from immutable-source reconstruction")
        driver = compile_jar("driver", {p.stem: frozen[p].decode() for p in (driver_source, TRANSFORM_DRIVER)}, jars)
        fixture_files = {**catalog.producer_sources(producers), **{p.stem: frozen[p].decode() for p in (fixture_source, *extra_fixtures)}}
        addon_files = addon_sources(ore_originals) if ore_family else {}
        producer = compile_jar("producer", {**fixture_files, **addon_files}, [source_kernel, *dependencies])
        count = 0
        def run(config, scenario="baseline", kernel=None):
            nonlocal count
            count += 1; directory = work / ("run-" + str(count)); directory.mkdir()
            cfg = directory / "gregtech.cfg"; cfg.write_text(config); before = cfg.read_bytes()
            request = directory / "request.properties"; request.write_text("config=" + str(cfg) + "\nscenario=" + scenario + "\n")
            kernel = kernel or program / "native-materials.jar"
            kernel, selected_producer = kernel if isinstance(kernel, tuple) else (kernel, producer)
            command = [str(java / "bin/java"), "--enable-native-access=ALL-UNNAMED", "-Xmx512m", "-Duser.language=en", "-cp",
                       os.pathsep.join(map(str, [driver, *jars])), "research.orthrus.axiom." + driver_source.stem,
                       *map(str, [images, libraries, kernel]), sha256(kernel.read_bytes()).hexdigest(), str(selected_producer), sha256(selected_producer.read_bytes()).hexdigest(), str(request)]
            completed = subprocess.run(command, cwd=directory, env=environment, capture_output=True, text=True, timeout=60)
            if cfg.read_bytes() != before: raise ValueError("material item configuration changed caller bytes")
            if completed.returncode: raise ValueError("material item execution failed:\n" + completed.stdout[-1000:] + completed.stderr[-8000:])
            value = json.loads(completed.stdout)
            if value.get("kernelIsolation") is not True or any(value.get(k) is not False for k in ("minecraftLaunched", "wholePackParity", "fullRecipeRegistration")):
                raise ValueError("material item execution boundary differs")
            return value
        pack = producers["supersymmetry", catalog.PACK_CONFIG]
        baseline = run(pack); rebuilt = run(pack, kernel=source_kernel)
        if baseline != rebuilt: raise ValueError("source/installed material item traces differ")
        access_evidence = {}
        if ore_family:
            from axiom_material_ore_conformance import qualify_access
            access_evidence = qualify_access(java, dependencies, ore_originals, baseline, work, compile_jar)
        expected_events = {"net.minecraftforge.fml.common.eventhandler.GenericEvent", "net.minecraftforge.event.AttachCapabilitiesEvent",
                           "net.minecraftforge.oredict.OreDictionary$OreRegisterEvent", "net.minecraftforge.event.RegistryEvent", "net.minecraftforge.event.RegistryEvent$Register",
                           *(linking.PACKAGE + "." + n for n in ("MaterialEvent", "MaterialRegistryEvent", "PostMaterialEvent"))}
        if set(baseline["eventTransformations"]) != expected_events: raise ValueError("material item event closure differs")
        transformer = producers["cleanroom", "src/main/java/net/minecraftforge/fml/common/asm/transformers/EventSubscriptionTransformer.java"]
        transformer_jar = compile_jar("transformer", {"EventSubscriptionTransformer": transformer}, dependencies)
        command = [str(java / "bin/java"), "--enable-native-access=ALL-UNNAMED", "-Xmx512m", "-cp", os.pathsep.join(map(str, [driver, *jars])),
                   "research.orthrus.axiom.NativeCatalogTransformConformance", ",".join(sorted(expected_events)), *map(str, [transformer_jar, source_kernel, *dependencies])]
        transformed = subprocess.run(command, cwd=work, env=environment, capture_output=True, text=True, timeout=60)
        if transformed.returncode or json.loads(transformed.stdout) != baseline["eventTransformations"]:
            raise ValueError("material item source/release event transforms differ: " + transformed.stderr[-3000:])
        failures = [run(pack, scenario=s)["result"] for s in ("failure", "registration-failure")]
        config_runs = []
        for option in (False, True):
            config = 'general {\n "recipe options" {\n B:generateLowQualityGems=' + str(option).lower() + '\n }\n}\n'
            result = run(config)
            if result["result"]["configuration"]["generateLowQualityGems"] != option: raise ValueError("native material item config parser differs")
            config_runs.append(result)
        low = config_runs[0]["result"]["checks"]["generatedStackVectors"]; high = config_runs[1]["result"]["checks"]["generatedStackVectors"]
        if high <= low: raise ValueError("low-quality gem configuration did not affect generated content")
        edited = deepcopy(originals); key = ("gtceu", sources.PATHS["MetaPrefixItem"])
        before = "return orePrefix.doGenerateItem(material);"
        if edited[key].count(before) != 1: raise ValueError("material generation witness boundary differs")
        edited[key] = edited[key].replace(before, "return orePrefix != OrePrefix.plate && orePrefix.doGenerateItem(material);")
        changed = compile_jar("generation-edit", linking.assemble(regenerated, {**host, **retained, **sources.retained_sources(edited)}), dependencies)
        generation_edit = run(pack, kernel=changed)
        if generation_edit["result"] != without_plate(baseline["result"]): raise ValueError("original generation source edit did not propagate exactly")
        edited = deepcopy(originals); key = ("gtceu", sources.GT + "common/items/MetaItems.java")
        before = 'String.format("meta_%s", regName)'
        if edited[key].count(before) != 1: raise ValueError("material registry name witness boundary differs")
        edited[key] = edited[key].replace(before, 'String.format("form_%s", regName)')
        changed = compile_jar("name-edit", linking.assemble(regenerated, {**host, **retained, **sources.retained_sources(edited)}), dependencies)
        name_edit = run(pack, kernel=changed)
        expected = json.dumps(baseline["result"])
        for entry in baseline["result"]["inventory"]["items"]:
            name = entry["registryName"]
            expected = expected.replace('"' + name + '"', '"' + name.replace(":meta_", ":form_", 1) + '"')
        expected = json.loads(expected)
        if name_edit["result"] != expected:
            from axiom_material_block_conformance import first_difference
            raise ValueError("original item registry name edit did not propagate exactly: " + first_difference(name_edit["result"], expected))
        additional = {}
        if ore_family:
            from axiom_material_ore_conformance import exercise
            def ore_kernel(label, modified):
                kernel = compile_jar(label, linking.assemble(regenerated, {**host, **retained, **retained_ores(modified)}), dependencies)
                addon = compile_jar(label + "-addon", {**fixture_files, **addon_sources(modified)}, [kernel, *dependencies])
                return kernel, addon
            additional = exercise(run, ore_kernel, ore_originals, pack, baseline)
            additional.update(addonSourceFiles={n: sha256(t.encode()).hexdigest() for n, t in addon_files.items()},
                              addonProgramSha256=sha256(producer.read_bytes()).hexdigest(), addonSourcesBundled=False)
        if block_family:
            from axiom_material_block_conformance import exercise
            def block_kernel(label, modified):
                return compile_jar(label, linking.assemble(regenerated, {**host, **retained, **blocks.retained_sources(modified)}), dependencies)
            additional = exercise(run, block_kernel, block_originals, pack, baseline)
        result = {"schema": "axiom.material-ore-conformance.v1" if ore_family else "axiom.material-block-conformance.v1" if block_family else "axiom.material-item-conformance.v1", "status": "passed", "runs": count,
                  "family": "gt-susy-ore-content" if ore_family else "gt-material-items-and-blocks" if block_family else "gt-material-prefix-items", "engineManifestSha256": sha256(engine_manifest).hexdigest(), "engineJars": manifest["jars"],
                  "programSha256": sha256(program_files["native-materials.jar"]).hexdigest(), "programManifestSha256": sha256(program_files["program.json"]).hexdigest(),
                  "sourceInputs": {**evidence, **{r + ":" + p: sha256(t.encode()).hexdigest() for (r, p), t in {**originals, **item_originals, **block_originals, **ore_originals, **producers}.items()}},
                  "qualificationInputs": {p.relative_to(ROOT).as_posix(): sha256(raw).hexdigest() for p, raw in frozen.items()},
                  "images": policy["images"], "libraryInputs": policy["libraries"], "runtimeInputs": runtime["runtimeFiles"] + runtime["compilerFiles"],
                  "baseline": baseline, "failureRuns": failures, "configurationRuns": config_runs,
                  "sourceRebuildIdentical": True, "sourceExecutionIdentical": True, "nativeEventTransformationIdentical": True,
                  "generationSourceEdit": True, "registryNameSourceEdit": True, "callerConfigurationUnchanged": True,
                  "wholePackParity": False, "installedCompositionQualified": False, "generatedGTContent": False,
                  "allItemBehaviorQualified": False, "fullRecipeRegistration": False, "minecraftLaunched": False, **access_evidence, **additional}
    if any(p.read_bytes() != raw for p, raw in frozen.items()): raise ValueError("material item qualification inputs changed")
    if sources.verify_sources(selected) != originals or items.verify_sources(selected) != item_originals or blocks.verify_sources(selected) != block_originals or catalog.verify_sources(producer_roots) != producers or verify_ores(ore_roots) != ore_originals:
        raise ValueError("material item upstream inputs changed")
    if engine_inputs(home, frozen[TARGET_LOCK])[0] != engine_manifest or any((program / n).read_bytes() != raw for n, raw in program_files.items()):
        raise ValueError("installed material item program changed")
    for root, rows in ((images, policy["images"]), (libraries, policy["libraries"]), (java, runtime["runtimeFiles"] + runtime["compilerFiles"])):
        for row in rows: checked_path(root, row)
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x") as out: json.dump(result, out, indent=2); out.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("java-home", "engine-home", "images", "library-root", "program", "gtceu", "susy-core", "cleanroom", "supersymmetry", "groovyscript", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    a = parser.parse_args(argv)
    result = qualify(a.java_home, a.engine_home, a.images, a.library_root, a.program,
                     {r: getattr(a, r.replace("-", "_")) for r in ("gtceu", "susy-core", "cleanroom", "supersymmetry", "groovyscript")}, a.report)
    print(json.dumps({"status": result["status"], "runs": result["runs"], "family": result["family"],
                      "variants": result["baseline"]["result"]["checks"]["generatedStackVectors"], "wholePackParity": False}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
