#!/usr/bin/env python3
"""Qualify composed prefix-item/compressed-block/frame registration on the pinned JVM."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import axiom_material_block_sources as sources


def first_difference(a, b, path="result"):
    if type(a) is not type(b): return path + ": different types"
    if isinstance(a, dict):
        if a.keys() != b.keys(): return path + ": different keys"
        for key in a:
            if a[key] != b[key]: return first_difference(a[key], b[key], path + "." + key)
    elif isinstance(a, list):
        if len(a) != len(b): return f"{path}: lengths {len(a)} != {len(b)}"
        for i, (left, right) in enumerate(zip(a, b)):
            if left != right: return first_difference(left, right, f"{path}[{i}]")
    return f"{path}: {str(a)[:150]} != {str(b)[:150]}"


def without_frames(value):
    value = deepcopy(value)
    removed = [b for b in value["inventory"]["blocks"] if b["prefix"] == "frameGt"]
    names = {b["registryName"] for b in removed}
    for snapshot in [value["inventory"], *value["checkpoints"]]:
        snapshot["blocks"] = [b for b in snapshot["blocks"] if b["registryName"] not in names]
    value["events"] = [e for e in value["events"] if e[1][0] not in names]
    value["checks"]["generatedBlockVariants"] -= sum(v["material"] != "gregtech:null" for b in removed for v in b["variants"])
    value["checks"]["nullSlots"] -= sum(v["material"] == "gregtech:null" for b in removed for v in b["variants"])
    return value


def exercise(run, compile_kernel, originals, pack, baseline):
    failure = run(pack, "block-failure")["result"]
    sparse = run(pack, "sparse")["result"]
    fixture = [b for b in sparse["inventory"]["blocks"] if b["registryName"].startswith("fixture_")]
    identities = {(v["material"], b["prefix"], v["metadata"]) for b in fixture for v in b["variants"] if v["material"] != "gregtech:null"}
    expected = {(f"{mod}:boundary_{i}", p, i % 16) for mod in ("fixture_a", "fixture_b") for i in (15, 16, 31, 32) for p in ("block", "frameGt")}
    if identities != expected: raise ValueError("sparse namespace/metadata witnesses differ")
    edited = deepcopy(originals); key = "gtceu", sources.GT + "common/blocks/MetaBlocks.java"
    before = "m -> m.hasProperty(PropertyKey.DUST) && m.hasFlag(GENERATE_FRAME)"
    if edited[key].count(before) != 1: raise ValueError("frame predicate source witness differs")
    edited[key] = edited[key].replace(before, "m -> false && m.hasProperty(PropertyKey.DUST) && m.hasFlag(GENERATE_FRAME)")
    changed = run(pack, kernel=compile_kernel("frame-generation-edit", edited))
    expected = without_frames(baseline["result"])
    actual = deepcopy(changed["result"])
    # Removing frame construction changes the first identity-hash uses of the
    # shared materials. Preserve the native hash-map traversal in both receipts;
    # only the comparison of this mutation's compressed ore segment is unordered.
    names = {b["registryName"] for b in actual["inventory"]["blocks"]}
    for value in (actual, expected):
        events = value["events"]
        split = next(i for i, e in enumerate(events) if e[1][0] in names)
        if any(e[1][0] not in names for e in events[split:]): raise ValueError("frame edit changed ore phase ordering")
        value["events"] = events[:split] + sorted(events[split:], key=lambda e: json.dumps(e))
    if actual != expected: raise ValueError("frame source predicate edit differs beyond native map iteration: " + first_difference(actual, expected))
    frame_edit = changed["result"]
    edited = deepcopy(originals)
    if edited[key].count('"meta_block_compressed_"') != 1: raise ValueError("block name source witness differs")
    edited[key] = edited[key].replace('"meta_block_compressed_"', '"source_block_compressed_"')
    changed = run(pack, kernel=compile_kernel("block-name-edit", edited))
    expected = json.loads(json.dumps(baseline["result"]).replace("gregtech:meta_block_compressed_", "gregtech:source_block_compressed_"))
    if changed["result"] != expected: raise ValueError("block and ItemBlock source name edit did not propagate exactly")
    edited = deepcopy(originals)
    before = '"meta_block_compressed_" + index'
    if edited[key].count(before) != 1: raise ValueError("collision source witness differs")
    edited[key] = edited[key].replace(before, '"meta_block_compressed_" + 0')
    collision = run(pack, "collision", kernel=compile_kernel("block-collision", edited))["result"]
    return {"blockFailure": failure, "sparseNamespaceFixtures": sparse, "blockGenerationSourceEdit": True,
            "frameGenerationEditedResult": frame_edit, "frameEditComparison": "exact-except-native-compressed-ore-map-iteration",
            "blockCollision": collision, "blockRegistryNameSourceEdit": True, "allBlockBehaviorQualified": False}


def main(argv=None):
    from axiom_material_item_conformance import qualify
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("java-home", "engine-home", "images", "library-root", "program", "gtceu", "susy-core", "cleanroom", "supersymmetry", "groovyscript", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    a = parser.parse_args(argv)
    result = qualify(a.java_home, a.engine_home, a.images, a.library_root, a.program,
                     {r: getattr(a, r.replace("-", "_")) for r in ("gtceu", "susy-core", "cleanroom", "supersymmetry", "groovyscript")}, a.report, block_family=True)
    print(json.dumps({"status": result["status"], "runs": result["runs"], "family": result["family"],
                      "checks": result["baseline"]["result"]["checks"], "wholePackParity": False}))
    return 0


if __name__ == "__main__": raise SystemExit(main())
