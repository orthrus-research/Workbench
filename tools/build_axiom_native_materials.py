#!/usr/bin/env python3
"""Compile a native-linked material program from the verified installed engine's sources."""
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import axiom_native_material_sources as source
import axiom_native_identity_conformance as identities
from axiom_runtime import checked_path, verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK


import sys

_assembly_root = Path(__file__).resolve().parents[1]
for _assembly_source in (_assembly_root / 'api/src', _assembly_root / 'modules/axiom/src'):
    if str(_assembly_source) not in sys.path:
        sys.path.insert(0, str(_assembly_source))
from workbench_axiom.native_assembly import jar_bytes


def engine_sources(home):
    manifest_raw, manifest, jars = engine_inputs(home, TARGET_LOCK.read_bytes())
    sources = list((home / "sources").glob("*-sources.jar"))
    if len(sources) != 1: raise ValueError("one engine source archive required")
    with zipfile.ZipFile(sources[0]) as jar:
        shared = {name: jar.read("research/orthrus/axiom/" + name + ".java").decode() for name in source.NAMES}
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as jar:
        host = {p.stem: jar.read("axiom/native-materials/" + p.name).decode() for p in source.HOST.glob("*.java")}
        if jar.read("axiom/native-identity-runtime.json") != identities.POLICY.read_bytes():
            raise ValueError("installed native input policy differs")
    if shared != {n: (source.SHARED / (n + ".java")).read_text() for n in source.NAMES} or host != {p.stem: p.read_text() for p in source.HOST.glob("*.java")}:
        raise ValueError("installed native material source differs")
    return shared, host, manifest_raw, sha256(sources[0].read_bytes()).hexdigest()


def compile_sources(java, files, dependencies, output):
    root = output / "source"; classes = output / "classes"
    root.mkdir(parents=True); classes.mkdir()
    if "OreAccessRules" in files:
        from axiom_material_access import compiler_overlay
        dependencies = [compiler_overlay(java, files, dependencies, output / "access"), *dependencies]
    for name, text in files.items():
        # Qualified source paths support distinct upstream types with the same
        # simple name. The class/source is never renamed to avoid that collision.
        if not name or any(not part.isidentifier() for part in name.split('/')):
            raise ValueError("native Java source path differs: " + name)
        path = root / (name + ".java")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    environment = {k: v for k, v in os.environ.items() if k not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}}
    result = subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", os.pathsep.join(map(str, dependencies)),
                    "-d", str(classes), *map(str, sorted(root.rglob("*.java")))], cwd=output, env=environment,
                    capture_output=True, text=True)
    if result.returncode:
        raise ValueError("native material compilation failed:\n" + result.stdout + result.stderr)
    return {p.relative_to(classes).as_posix(): p.read_bytes() for p in classes.rglob("*.class")}


def build(java, home, images, libraries, output):
    output = identities.ordinary(output)
    if output.exists(): raise ValueError("native material program output must be new")
    inputs = [identities.POLICY, identities.LOCK, identities.PLATFORM, TARGET_LOCK, Path(__file__),
              *sorted((source.ROOT / "tools").glob("axiom_*sources.py")),
              source.ROOT / "tools/axiom_material_access.py", source.SHARED / "NativeMaterialAccess.java",
              Path(identities.__file__), Path(verify_runtime.__code__.co_filename), Path(engine_inputs.__code__.co_filename)]
    frozen = {p: p.read_bytes() for p in inputs}
    policy = json.loads(frozen[identities.POLICY])
    identities.verify_policy(policy, json.loads(frozen[identities.LOCK]), json.loads(frozen[identities.PLATFORM]))
    java = identities.ordinary(java).resolve(strict=True); runtime = verify_runtime(java, compiler=True)
    images = identities.ordinary(images).resolve(strict=True); libraries = identities.ordinary(libraries).resolve(strict=True)
    for root, rows in ((images, policy["images"]), (libraries, policy["libraries"])):
        for row in rows: checked_path(root, row)
    home = identities.ordinary(home).resolve(strict=True)
    shared, host, engine_manifest, engine_source_hash = engine_sources(home)
    generated = source.assemble(shared, host)
    dependencies = [images / r["path"] for r in policy["images"]] + [libraries / r["path"] for r in policy["libraries"]]
    with tempfile.TemporaryDirectory(prefix="axiom-native-material-build-") as temporary:
        work = Path(temporary)
        classes = compile_sources(java, generated, dependencies, work / "compile")
        if not classes or any(not n.startswith(source.PACKAGE.replace('.', '/') + '/') for n in classes):
            raise ValueError("native material artifact class boundary differs")
        jar_bytes(work / "native-materials.jar", classes)
        jar_bytes(work / "native-materials-sources.jar", {source.PACKAGE.replace('.', '/') + '/' + n + '.java': t.encode() for n,t in generated.items()})
        report = {"schema": "axiom.native-material-program.v1", "sourcePackage": source.PACKAGE,
                  "engineManifestSha256": sha256(engine_manifest).hexdigest(), "engineSourcesSha256": engine_source_hash,
                  "inputPolicySha256": sha256(frozen[identities.POLICY]).hexdigest(), "images": policy["images"],
                  "libraryInputs": policy["libraries"], "runtimeInputs": runtime["runtimeFiles"] + runtime["compilerFiles"],
                  "recipes": {p.name: sha256(raw).hexdigest() for p,raw in frozen.items()},
                  "sourceFiles": {n: sha256(t.encode()).hexdigest() for n,t in generated.items()},
                  "classes": {n: sha256(raw).hexdigest() for n,raw in classes.items()},
                  "artifacts": [{"path": n, "sha256": sha256((work/n).read_bytes()).hexdigest(), "size": (work/n).stat().st_size}
                                for n in ("native-materials.jar", "native-materials-sources.jar")],
                  "wholePackParity": False, "producerExecutionQualified": False}
        if any(p.read_bytes() != raw for p,raw in frozen.items()) or engine_sources(home) != (shared,host,engine_manifest,engine_source_hash):
            raise ValueError("native material build inputs changed")
        for root, rows in ((images, policy["images"]), (libraries, policy["libraries"]), (java, runtime["runtimeFiles"] + runtime["compilerFiles"])):
            for row in rows: checked_path(root,row)
        output.parent.mkdir(parents=True, exist_ok=True); output.mkdir()
        for row in report["artifacts"]:
            with (output / row["path"]).open("xb") as f: f.write((work / row["path"]).read_bytes())
        with (output / "program.json").open("x") as f: json.dump(report, f, indent=2); f.write("\n")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("java-home", "engine-home", "images", "library-root", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    args=parser.parse_args(argv)
    result=build(args.java_home,args.engine_home,args.images,args.library_root,args.output)
    print(json.dumps({"classes":len(result["classes"]),"sourceFiles":len(result["sourceFiles"]),"output":str(args.output),"wholePackParity":False}))
    return 0


if __name__=="__main__": raise SystemExit(main())
