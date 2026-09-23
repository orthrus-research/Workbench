#!/usr/bin/env python3
"""Qualify native GT/Susy ore variants, registration, and world-independent drop selection."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

import axiom_material_ore_sources as sources
import axiom_material_access as access


def qualify_access(java, dependencies, originals, baseline, work, compile_jar):
    retained = sources.retained_sources(originals)
    rules = json.loads(re.search(r'String RULES = ("(?:\\.|[^"\\])*");', retained["OreAccessRules"])[1])
    original = compile_jar("original-access-transformer", {"AccessTransformer": originals["cleanroom", sources.AT_SOURCE]}, dependencies)
    release = access.transform(java, dependencies, rules, work / "access-release")
    rebuilt = access.transform(java, [original, *dependencies], rules, work / "access-original")
    if release != rebuilt: raise ValueError("source/release access transformation differs")
    evidence = baseline["accessTransformations"]
    if set(evidence) != {"net.minecraft.block.Block"}: raise ValueError("native access transform closure differs")
    row = evidence["net.minecraft.block.Block"]
    if row["outputSha256"] != sha256(release).hexdigest() or row["rulesSha256"] != sha256(rules.encode()).hexdigest():
        raise ValueError("compile/runtime access transformation differs")
    without = access.transform(java, dependencies, "", work / "access-without-rules")
    if without == release or row["inputSha256"] != sha256(without).hexdigest(): raise ValueError("access rule no-op witness differs")
    return {"sourceReleaseAccessTransformIdentical": True, "compileRuntimeAccessTransformIdentical": True, "accessTransformations": evidence}


def exercise(run, compile_kernel, originals, pack, baseline):
    cases = {s: run(pack, s)["result"] for s in ("gt-only", "disabled", "block-failure", "host-drift", "addon-host-drift", "stone-drift", "stone-orphan", "ore-state-drift")}
    config = []
    for unique in (False, True):
        text = 'general {\n "worldgen options" {\n B:allUniqueStoneTypes=' + str(unique).lower() + '\n }\n}\n'
        result = run(text)["result"]
        if result["configuration"]["allUniqueStoneTypes"] != unique: raise ValueError("original stone config parser differs")
        checks = result["checks"]
        if unique:
            if checks["ordinaryDropRedirects"] or checks["silkOrdinaryDifferences"]: raise ValueError("unique stone drops redirected")
            if any(len(v) != 1 for v in checks["secondaryMaterials"].values()): raise ValueError("Susy secondary material declarations did not run")
        elif checks["ordinaryDropRedirects"] <= 0 or checks["silkOrdinaryDifferences"] <= 0 or any(checks["secondaryMaterials"].values()):
            raise ValueError("non-unique ordinary/silk distinction differs")
        config.append(result)
    variants = baseline["result"]["checks"]["generatedOreVariants"]
    if cases["disabled"]["checks"]["generatedOreVariants"] != variants - 22: raise ValueError("disabled Fluorite did not remove exactly its 22 variants")
    if len(cases["gt-only"]["inventory"]["ores"]["stoneTypes"]) != 12: raise ValueError("GT-only native stone universe differs")
    key = "gtceu", sources.GT + "common/CommonProxy.java"
    def changed(label, path, before, after, scenario="baseline"):
        edited = deepcopy(originals)
        if edited[path].count(before) != 1: raise ValueError("ore source witness boundary differs: " + before)
        edited[path] = edited[path].replace(before, after)
        return run(pack, scenario, kernel=compile_kernel(label, edited))["result"]
    renamed = changed("ore-names", key, '"ore_" + material', '"source_ore_" + material')
    expected = json.loads(json.dumps(baseline["result"]).replace("gregtech:ore_", "gregtech:source_ore_"))
    if renamed != expected: raise ValueError("source ore name did not propagate exactly to native states/items/memberships")
    no_generation = changed("ore-generation", key, "material.hasProperty(PropertyKey.ORE) && !material.hasFlag(MaterialFlags.DISABLE_ORE_BLOCK)",
                            "material.hasProperty(PropertyKey.ORE) && !material.hasFlag(MaterialFlags.DISABLE_ORE_BLOCK) && false", "no-generation")
    if no_generation["inventory"]["ores"]["oreBlocks"] or no_generation["checks"]["generatedOreVariants"]: raise ValueError("source predicate edit still generated ore blocks")
    collision = changed("ore-collision", key, '"ore_" + material + "_" + index', '"ore_" + material + "_" + 0', "collision")
    stone_key = sources.PATHS["SusyStoneTypes"]
    sparse = changed("stone-sparse", stone_key, 'new StoneType(13, "gneiss"', 'new StoneType(23, "gneiss"', "sparse")
    gap = changed("stone-gap-start", stone_key, 'new StoneType(16, "quartzite"', 'new StoneType(24, "quartzite"', "gap-start")
    duplicate = changed("stone-duplicate", stone_key, 'new StoneType(13, "gneiss"', 'new StoneType(12, "gneiss"', "duplicate-stone")
    if "reassign id 12" not in duplicate["failureMessage"]: raise ValueError("duplicate stone witness failed outside the native registry")
    if gap["partialOres"] < 1 or gap["registeredBlocks"]: raise ValueError("gap-at-start did not preserve constructor-phase partial generation")
    edited = deepcopy(originals); at_key = "gtceu", sources.AT
    edited[at_key] = edited[at_key].replace("public-f net.minecraft.block.Block field_176227_L", "public net.minecraft.block.Block field_176227_L")
    try:
        compile_kernel("missing-required-access", edited)
    except ValueError as error:
        if "cannot assign a value to final variable field_176227_L" not in str(error): raise
    else: raise ValueError("missing final-removal rule did not reject the original constructor")
    return {"cases": cases, "stoneConfigurationRuns": config, "oreRegistryNameSourceEdit": True,
            "generationEditedResult": no_generation, "collision": collision, "sparseStoneResult": sparse,
            "gapAtGroupStart": gap, "duplicateStone": duplicate, "missingRequiredAccessRuleRejected": True,
            "worldGenerationQualified": False, "harvestingEventsQualified": False}


def main(argv=None):
    from axiom_material_item_conformance import qualify
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("java-home", "engine-home", "images", "library-root", "program", "gtceu", "susy-core", "cleanroom", "supersymmetry", "groovyscript", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    a = parser.parse_args(argv)
    result = qualify(a.java_home, a.engine_home, a.images, a.library_root, a.program,
                     {r: getattr(a, r.replace("-", "_")) for r in ("gtceu", "susy-core", "cleanroom", "supersymmetry", "groovyscript")}, a.report, ore_family=True)
    print(json.dumps({"status": result["status"], "runs": result["runs"], "family": result["family"],
                      "checks": result["baseline"]["result"]["checks"], "wholePackParity": False}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
