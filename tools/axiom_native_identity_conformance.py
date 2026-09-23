#!/usr/bin/env python3
"""Build and compare pinned Cleanroom constructor inputs offline; never launch a game."""
import argparse
from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import zipfile

from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK, MATERIAL_ROOT
from build_axiom_target import git, ordinary_path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/native-identities.lock.json"
POLICY = ROOT / "profiles/platforms/cleanroom/native-identity-runtime.json"
PLATFORM = ROOT / "profiles/platforms/cleanroom/runtime-toolchain.json"
BUILD = ROOT / "modules/axiom/tests/oracles/NativeIdentityInputs.java"
DRIVER = ROOT / "modules/axiom/tests/oracles/NativeIdentityConformance.java"
COMPILED = (
    "src/main/java/net/minecraftforge/fml/common/patcher/ClassPatchManager.java",
    "src/main/java/net/minecraftforge/fml/common/patcher/ClassPatch.java",
    "src/main/java/net/minecraftforge/fml/common/asm/transformers/deobf/FMLDeobfuscatingRemapper.java",
    "src/main/java/net/minecraftforge/fml/common/asm/transformers/deobf/FMLRemappingAdapter.java",
    "src/main/java/net/minecraftforge/fml/common/asm/transformers/AccessTransformer.java",
    "src/main/java/net/minecraftforge/fml/common/asm/transformers/EventSubscriptionTransformer.java",
)
AUDITED = (
    "src/main/java/net/minecraftforge/registries/GameData.java",
    "src/main/java/net/minecraftforge/fluids/FluidRegistry.java",
    "patches/minecraft/net/minecraft/init/Bootstrap.java.patch",
    "patches/minecraft/net/minecraft/block/Block.java.patch",
    "patches/minecraft/net/minecraft/block/BlockLiquid.java.patch",
    "patches/minecraft/net/minecraft/enchantment/Enchantment.java.patch",
    "src/main/resources/forge_at.cfg",
)
PRODUCERS = tuple("src/main/java/gregtech/api/unification/material/materials/" + name + ".java"
                  for name in ("FirstDegreeMaterials", "SecondDegreeMaterials"))
ORIGINS = ("https://repo.maven.apache.org/maven2/", "https://repo.cleanroommc.com/releases/",
           "https://libraries.minecraft.net/", "https://piston-data.mojang.com/v1/objects/")


def ordinary(root):
    root = root.absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("indirect native identity path")
    return root


def verify_sources(roots, lock):
    if lock.get("schema") != "axiom.native-identity-source-lock.v1" or set(lock["revisions"]) != {"cleanroom", "gtceu"}:
        raise ValueError("invalid native identity source lock")
    originals = {}
    for row in lock["references"]:
        repo, path = row["repository"], ordinary_path(row["path"])
        if repo not in roots or (repo, path) in originals:
            raise ValueError("unexpected or duplicate native identity source")
        revision = lock["revisions"][repo]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("mutable native identity revision")
        raw = git(roots[repo], "show", revision + ":" + path)
        if (sha256(raw).hexdigest() != row["sha256"] or
                sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != row["gitBlob"]):
            raise ValueError("native identity source differs: " + path)
        originals[repo, path] = raw
    expected = {("cleanroom", p) for p in COMPILED + AUDITED} | {("gtceu", p) for p in PRODUCERS}
    if set(originals) != expected:
        raise ValueError("native identity source closure differs")
    return originals


def expressions(originals):
    """Capture complete toolStats arguments from immutable producer source, not a recipe parser."""
    result = []
    for path in PRODUCERS:
        source = originals["gtceu", path].decode()
        offset = 0
        while (start := source.find(".toolStats(", offset)) >= 0:
            begin = start + len(".toolStats(")
            depth, end = 1, begin
            while depth and end < len(source):
                token = source[end]
                if token in "\"'" or source[end:end+2] in {"/*", "//"}:
                    raise ValueError("unqualified tool-stat expression syntax")
                depth += (token == "(") - (token == ")")
                end += 1
            if depth:
                raise ValueError("unterminated tool-stat expression")
            body = source[begin:end-1]
            if "Enchantments." in body:
                if not body.startswith("ToolProperty.Builder.of("):
                    raise ValueError("unqualified native tool-property producer")
                result.append({"path": path, "line": source[:begin].count("\n") + 1, "source": body,
                               "sha256": sha256(body.encode()).hexdigest()})
            offset = end
    if len(result) != 5:
        raise ValueError("native enchanted tool-stat inventory differs")
    return {"expressions": result}


def verify_policy(policy, lock, platform=None):
    if (policy.get("schema") != "axiom.native-identity-runtime.v1" or policy.get("profile") != "cleanroom"
            or policy.get("cleanroomRevision") != lock["revisions"]["cleanroom"]):
        raise ValueError("native identity policy revision differs")
    if [r["path"] for r in policy["images"]] != ["construction-prefix.jar", "cleanroom-classes.jar"]:
        raise ValueError("native identity image order differs")
    rows = policy["inputs"] + policy["libraries"]
    if len(rows) != 115 or len({r["path"] for r in rows}) != 115 or len(policy["inputs"]) != 2:
        raise ValueError("native identity library inventory differs")
    for row in rows + policy["images"]:
        ordinary_path(row["path"])
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or type(row["size"]) is not int or row["size"] <= 0:
            raise ValueError("invalid native identity artifact pin")
    if policy.get("qualification") != {"fullVanillaBootstrap": False, "installedCompositionQualified": False, "wholePackParity": False}:
        raise ValueError("unqualified native identity capability claim")
    if any(not r.get("url", "").startswith(ORIGINS) for r in rows):
        raise ValueError("unadmitted native input origin")
    if platform is not None:
        if (policy["cleanroomRevision"] != platform["source_revision"] or
                policy["bootstrapSha256"] != platform["bootstrap"]["sha256"]):
            raise ValueError("native identity platform selection differs")
        by_url = {r["url"]: r for r in rows}
        for artifact in platform["artifacts"]:
            row = by_url.get(artifact["url"], {})
            if any(row.get(key) != artifact[key] for key in ("sha256", "size")):
                raise ValueError("native identity platform artifact differs")


def provision_libraries(root, policy):
    """Explicit acquisition only; altered existing files are never overwritten."""
    root = ordinary(root)
    for row in policy["inputs"] + policy["libraries"]:
        path = ordinary(root / ordinary_path(row["path"]))
        if path.exists():
            checked_path(root, row)
            continue
        if not row["url"].startswith(ORIGINS):
            raise ValueError("unadmitted native input origin")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".axiom-native-download-") as temporary:
            with urllib.request.urlopen(row["url"], timeout=45) as response:
                digest, count = sha256(), 0
                while block := response.read(65536):
                    count += len(block)
                    if count > row["size"]:
                        raise ValueError("native input download exceeds pinned size")
                    digest.update(block); temporary.write(block)
            if count != row["size"] or digest.hexdigest() != row["sha256"]:
                raise ValueError("native input download differs from pin")
            temporary.flush(); os.link(temporary.name, path)
        checked_path(root, row)
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cleanroom", "gtceu", "java-home", "engine-home", "library-root", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--provision-libraries", action="store_true", help="Explicitly acquire profile-pinned external inputs; default is offline")
    args = parser.parse_args(argv)
    output = ordinary(args.output)
    if output.exists() or (args.report is not None and (ordinary(args.report).exists())):
        raise ValueError("native identity outputs must be new")
    tracked = [LOCK, POLICY, PLATFORM, TARGET_LOCK, BUILD, DRIVER, Path(__file__)]
    frozen = {p: p.read_bytes() for p in tracked}
    lock, policy = json.loads(frozen[LOCK]), json.loads(frozen[POLICY])
    verify_policy(policy, lock, json.loads(frozen[PLATFORM]))
    target = json.loads(frozen[TARGET_LOCK])
    if next(r["commit"] for r in target["repositories"] if r["id"] == "gtceu") != lock["revisions"]["gtceu"]:
        raise ValueError("native identity producer revision differs from target")
    roots = {"cleanroom": args.cleanroom, "gtceu": args.gtceu}
    originals = verify_sources(roots, lock)
    producer = expressions(originals)
    java = ordinary(args.java_home).resolve(strict=True)
    runtime = verify_runtime(java, compiler=True)
    libraries = (provision_libraries(args.library_root, policy) if args.provision_libraries else ordinary(args.library_root)).resolve(strict=True)
    for row in policy["inputs"] + policy["libraries"]:
        checked_path(libraries, row)
    for row in policy["libraries"]:
        with zipfile.ZipFile(libraries / row["path"]) as jar:
            manifest = jar.read("META-INF/MANIFEST.MF") if "META-INF/MANIFEST.MF" in jar.namelist() else b""
            if b"class-path:" in manifest.lower():
                raise ValueError("unadmitted manifest classpath: " + row["path"])
    home = ordinary(args.engine_home).resolve(strict=True)
    manifest_raw, manifest, jars = engine_inputs(home, frozen[TARGET_LOCK])
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as jar:
        if jar.read("axiom/native-identity-runtime.json") != frozen[POLICY] or jar.read("axiom/native-identities.lock.json") != frozen[LOCK]:
            raise ValueError("installed identity policy differs")
    shared = {p: p.read_bytes() for p in MATERIAL_ROOT.rglob("*.java")}
    source_jars = list((home / "sources").glob("*-sources.jar"))
    if len(source_jars) != 1:
        raise ValueError("one installed source archive required")
    sources_raw = source_jars[0].read_bytes()
    with zipfile.ZipFile(source_jars[0]) as jar:
        for p, raw in shared.items():
            if jar.read("research/orthrus/axiom/" + p.relative_to(MATERIAL_ROOT).as_posix()) != raw:
                raise ValueError("installed identity source differs")
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    engine_cp = os.pathsep.join(map(str, jars))
    minecraft = next(libraries / r["path"] for r in policy["inputs"] if "/minecraft/" in r["path"])
    cleanroom = next(libraries / r["path"] for r in policy["inputs"] if "/cleanroom/" in r["path"])
    build_cp = os.pathsep.join([engine_cp, str(cleanroom), *(str(libraries / r["path"]) for r in policy["libraries"])])
    with tempfile.TemporaryDirectory(prefix="axiom-native-identities-") as temporary:
        work = Path(temporary)
        def compile_group(name, files, classpath):
            group = work / name; group.mkdir()
            paths = []
            for filename, content in files.items():
                path = group / filename; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content); paths.append(path)
            subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", classpath,
                            "-d", str(group), *map(str, paths)], cwd=work, env=environment, check=True, timeout=60)
            return group
        build = compile_group("builder", {BUILD.name: frozen[BUILD]}, build_cp)
        probe = compile_group("probe", {DRIVER.name: frozen[DRIVER]}, engine_cp)
        source = compile_group("original-source", {p.removeprefix("src/main/java/"): originals["cleanroom", p] for p in COMPILED}, build_cp)
        input_file = work / "producer.json"; input_file.write_text(json.dumps(producer))
        def run(classpath, main, *arguments, language="en"):
            result = subprocess.run([str(java / "bin/java"), "-Xmx512m", "-Duser.language=" + language, "-cp", classpath, main, *map(str, arguments)],
                                    cwd=work, env=environment, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise AssertionError("Native identity qualification failed:\n" + result.stdout[-2000:] + result.stderr[-7000:])
            return json.loads(result.stdout)
        baseline, rebuilt = work / "binary", work / "source"
        built = run(os.pathsep.join([str(build), build_cp]), "research.orthrus.axiom.NativeIdentityInputs", minecraft, cleanroom, baseline)
        compared = run(os.pathsep.join([str(source), str(build), build_cp]), "research.orthrus.axiom.NativeIdentityInputs", minecraft, cleanroom, rebuilt)
        if built != compared or built != {"classes": 5185, "binaryPatches": 1165, "prefixDriftRejections": 11, "minecraftLaunched": False}:
            raise ValueError("native transformation comparison differs")
        for name in ("construction-prefix.jar", "cleanroom-classes.jar", "transforms.json"):
            if (baseline / name).read_bytes() != (rebuilt / name).read_bytes():
                raise ValueError("source-built native transformation differs: " + name)
        for row in policy["images"]:
            checked_path(baseline, row)
        traces = []
        for language in ("en", "tr"):
            receipt = run(os.pathsep.join([str(probe), engine_cp]), "research.orthrus.axiom.NativeIdentityConformance", baseline, libraries, input_file, language=language)
            if (receipt.get("status") != "passed" or receipt.get("blocks") != 254 or receipt.get("enchantments") != 30
                    or receipt.get("stackVectors") != 8192 or receipt.get("gtToolExpressions") != 5 or receipt.get("kernelIsolation") is not True
                    or any(receipt.get(k) is not False for k in ("minecraftLaunched", "wholePackParity", "fullVanillaBootstrap"))):
                raise ValueError("native identity consumer comparison incomplete")
            traces.append({"language": language, **receipt})
        if traces[0]["traceDigest"] != traces[1]["traceDigest"]:
            raise ValueError("native identity locale comparison differs")
        # Do not reseal changed inputs. These paths must fail against the installed profile pin.
        damaged = work / "damaged"; damaged.mkdir()
        for row in policy["images"]:
            raw = (baseline / row["path"]).read_bytes()
            (damaged / row["path"]).write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        indirect = work / "indirect"; indirect.symlink_to(baseline, target_is_directory=True)
        rejected = []
        for images, inputs in ((damaged, libraries), (indirect, libraries), (baseline, work / "missing-libraries")):
            receipt = run(os.pathsep.join([str(probe), engine_cp]), "research.orthrus.axiom.NativeIdentityConformance", images, inputs, "reject-input")
            if receipt.get("status") != "rejected":
                raise ValueError("changed native inputs admitted")
            rejected.append(receipt)
        if (any(p.read_bytes() != raw for p, raw in frozen.items()) or verify_sources(roots, lock) != originals
                or any(p.read_bytes() != raw for p, raw in shared.items()) or source_jars[0].read_bytes() != sources_raw
                or engine_inputs(home, frozen[TARGET_LOCK])[0] != manifest_raw):
            raise ValueError("native identity inputs changed during qualification")
        for row in policy["inputs"] + policy["libraries"]:
            checked_path(libraries, row)
        for row in runtime["runtimeFiles"] + runtime["compilerFiles"]:
            checked_path(java, row)
        report = {"schema": "axiom.native-identity-qualification.v1", "status": "passed", "baselines": traces,
                  "sourceRevisions": lock["revisions"], "sourceLockSha256": sha256(frozen[LOCK]).hexdigest(),
                  "policySha256": sha256(frozen[POLICY]).hexdigest(), "images": policy["images"],
                  "platformPolicySha256": sha256(frozen[PLATFORM]).hexdigest(),
                  "engineJars": manifest["jars"], "runtimeInputs": runtime["runtimeFiles"] + runtime["compilerFiles"],
                  "builderSha256": sha256(frozen[BUILD]).hexdigest(), "driverSha256": sha256(frozen[DRIVER]).hexdigest(),
                  "toolSha256": sha256(frozen[Path(__file__)]).hexdigest(), "engineSourcesSha256": sha256(sources_raw).hexdigest(),
                  "targetLockSha256": sha256(frozen[TARGET_LOCK]).hexdigest(),
                  "producerExpressions": producer["expressions"], "transformsSha256": sha256((baseline / "transforms.json").read_bytes()).hexdigest(),
                  "prefixDriftRejections": built["prefixDriftRejections"], "inputRejections": rejected,
                  "notQualified": ["complete vanilla Bootstrap.register", "vanilla snapshot/freeze and items", "pack Loader discovery and listener universe",
                                   "full transformation or mixin composition", "complete GT/Susy material producers and generated membership",
                                   "fluid binding into the isolated material-producer context", "recipe or machine validity"]}
        # Promote only verified bytes, without overwriting existing output. A partial promotion fails admission.
        output.parent.mkdir(parents=True, exist_ok=True); output.mkdir()
        for name in ("construction-prefix.jar", "cleanroom-classes.jar", "transforms.json"):
            with (output / name).open("xb") as destination: destination.write((baseline / name).read_bytes())
        with (output / "qualification.json").open("x") as destination: json.dump(report, destination, indent=2); destination.write("\n")
    if args.report is not None:
        with args.report.open("x") as destination: json.dump(report, destination, indent=2); destination.write("\n")
    print(json.dumps({"status": "passed", "output": str(output), "blocks": 254, "enchantments": 30, "gtToolExpressions": 5, "wholePackParity": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
