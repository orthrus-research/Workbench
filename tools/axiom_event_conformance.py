#!/usr/bin/env python3
"""Compare retained Cleanroom events with separately compiled original source.

The original classes are never packaged with Axiom. Minimal Loader, ModContainer,
FMLLog and transformer-interface ports exist only in temporary comparison space.
Dispatch, reflection, native listener factories and ASM transforms are original.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
import urllib.request

import axiom_event_sources as extraction
from axiom_runtime import verify_runtime
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "modules/axiom/tests/oracles/EventConformance.java"
FIXTURE = ROOT / "modules/axiom/tests/fixtures/events/EventScenarios.java"


def provision_libraries(root, lock):
    if root.exists() or root.is_symlink():
        raise ValueError("Event library destination must be new")
    root.mkdir(parents=True)
    for row in lock["oracleLibraries"]:
        name=row["path"]
        if not name.endswith(".jar") or any(part in ("", ".", "..") for part in name.split("/")) or "\\" in name:
            raise ValueError("Unsafe event library path")
        with urllib.request.urlopen("https://repo.maven.apache.org/maven2/"+name,timeout=60) as response:
            raw=response.read(32*1024*1024+1)
        if len(raw)>32*1024*1024 or sha256(raw).hexdigest()!=row["sha256"]:
            raise ValueError("Downloaded event library differs: "+name)
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("xb") as output:output.write(raw)
    return root.resolve(strict=True)


def originals(repository, lock):
    result = {}
    for row in lock["references"]:
        raw = subprocess.check_output(["git", "-C", str(repository), "show", lock["revision"] + ":" + row["path"]], timeout=30)
        if sha256(raw).hexdigest() != row["sha256"] or row["path"] in result:
            raise ValueError("Event source identity or duplicate differs: " + row["path"])
        result[row["path"]] = raw
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("cleanroom-root", "java-home", "engine-home", "library-root"):
        parser.add_argument("--" + flag, required=True, type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--provision-libraries", action="store_true", help="Acquire locked Maven JARs into a NEW library root")
    args = parser.parse_args(argv)
    for name in ("cleanroom_root", "java_home", "engine_home"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    if args.report.exists() or args.report.is_symlink():
        raise ValueError("Event report must be new")
    lock_raw = extraction.LOCK.read_bytes()
    lock = json.loads(lock_raw)
    args.library_root = provision_libraries(args.library_root,lock) if args.provision_libraries else args.library_root.resolve(strict=True)
    original = originals(args.cleanroom_root, lock)
    extraction.check(args.cleanroom_root)
    runtime = verify_runtime(args.java_home, compiler=True)
    manifest_raw, manifest, jars = engine_inputs(args.engine_home, TARGET_LOCK.read_bytes())
    source_jars = list((args.engine_home / "sources").glob("*-sources.jar"))
    if len(source_jars) != 1:
        raise ValueError("Expected one sources JAR")
    with zipfile.ZipFile(source_jars[0]) as archive:
        for path in extraction.OUTPUT.glob("*.java"):
            if archive.read(extraction.PACKAGE.replace(".", "/") + "/" + path.name) != path.read_bytes():
                raise ValueError("Installed event sources differ: " + path.name)
        space = ROOT / "modules/axiom/jvm/src/main/java/research/orthrus/axiom/NativeEventSpace.java"
        if archive.read("research/orthrus/axiom/NativeEventSpace.java") != space.read_bytes():
            raise ValueError("Installed event class space differs")
    bound = []
    for jar in jars:
        with zipfile.ZipFile(jar) as archive:
            if "axiom/cleanroom-events.lock.json" in archive.namelist():
                bound.append(archive.read("axiom/cleanroom-events.lock.json"))
    if bound != [lock_raw]:
        raise ValueError("Installed event source lock differs")
    libraries = []
    for row in lock["oracleLibraries"]:
        path = args.library_root / row["path"]
        if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Original event library differs: " + row["path"])
        libraries.append(str(path.resolve(strict=True)))
    before = {str(p): sha256(p.read_bytes()).hexdigest() for p in
              [DRIVER, FIXTURE, Path(__file__), Path(extraction.__file__), space, source_jars[0], extraction.LOCK] + list(extraction.OUTPUT.glob("*.java"))}
    environment = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="axiom-native-events-") as temporary:
        root = Path(temporary)
        source = root / "original-source"; source.mkdir()
        files = []
        for name, raw in original.items():
            path = source / name.removeprefix("src/main/java/")
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); files.append(str(path))
        # Host ports only; upstream event methods are unchanged.
        ports = {
            "net/minecraftforge/fml/common/ModContainer.java": '''package net.minecraftforge.fml.common;
public record ModContainer(String modId,String name) { public String getModId(){return modId;} public String getName(){return name;} }''',
            "net/minecraftforge/fml/common/Loader.java": '''package net.minecraftforge.fml.common;
public class Loader { private static final Loader INSTANCE=new Loader(); private ModContainer active,minecraft;
public static Loader instance(){return INSTANCE;} public ModContainer activeModContainer(){return active;}
public void setActiveModContainer(ModContainer owner){active=owner;} public void setMinecraftModContainer(ModContainer owner){minecraft=owner;}
public ModContainer getMinecraftModContainer(){if(minecraft==null)throw new IllegalStateException();return minecraft;} }''',
            "net/minecraftforge/fml/common/FMLLog.java": '''package net.minecraftforge.fml.common;
public class FMLLog { public static final org.apache.logging.log4j.Logger log=org.apache.logging.log4j.LogManager.getLogger("axiom.original-events"); }''',
            "net/minecraft/launchwrapper/IClassTransformer.java": '''package net.minecraft.launchwrapper;
public interface IClassTransformer {byte[] transform(String name,String transformedName,byte[] bytes);}''',
        }
        for name, raw in ports.items():
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(raw);files.append(str(path))
        fixture=source/"EventScenarios.java"
        fixture.write_text(FIXTURE.read_text().replace(extraction.PACKAGE+".*",extraction.UPSTREAM+".*")
                           .replace("EventContext.Owner","net.minecraftforge.fml.common.ModContainer")
                           .replace("EventContext.instance()","net.minecraftforge.fml.common.Loader.instance()"))
        files.append(str(fixture))
        java=args.java_home.resolve(strict=True)/"bin"
        original_out=root/"original";candidate_out=root/"candidate";driver_out=root/"driver"
        cp=os.pathsep.join(map(str,jars))
        for output, inputs, classpath in ((original_out,files,os.pathsep.join(libraries)),
                (candidate_out,[str(FIXTURE)],cp),(driver_out,[str(DRIVER)],cp)):
            output.mkdir()
            subprocess.run([str(java/"javac"),"--release","25","-cp",classpath,"-d",str(output),*inputs],
                           check=True,env=environment,cwd=root,timeout=60,capture_output=True)
        compiled = {str(p.relative_to(root)):sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.class")}
        process=subprocess.run([str(java/"java"),"-Xmx256m","-cp",str(driver_out)+os.pathsep+cp,
                                "research.orthrus.axiom.EventConformance",str(original_out),str(candidate_out)],
                               check=True,capture_output=True,text=True,env=environment,cwd=root,timeout=60)
        result=json.loads(process.stdout)
    if any(sha256(Path(p).read_bytes()).hexdigest()!=digest for p,digest in before.items()):
        raise ValueError("Event conformance input changed during comparison")
    if engine_inputs(args.engine_home,TARGET_LOCK.read_bytes())[0]!=manifest_raw:
        raise ValueError("Engine changed during comparison")
    if verify_runtime(args.java_home,compiler=True)!=runtime:
        raise ValueError("Selected runtime changed during comparison")
    for row,path in zip(lock["oracleLibraries"],libraries,strict=True):
        if sha256(Path(path).read_bytes()).hexdigest()!=row["sha256"]:
            raise ValueError("Original library changed during comparison")
    result.update(sourceLockSha256=sha256(lock_raw).hexdigest(),engine=manifest,runtime=runtime,
                  inputs=before,compiledClasses=compiled,originalSourceFiles=len(original),
                  limitations=["explicit loader owner and logging ports", "no mod discovery or active mixin qualification",
                               "no material producers or generated item/fluid/ore registry"])
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open("x") as report: json.dump(result,report,indent=2);report.write("\n")
    print(json.dumps({"status":result["status"],"scenarios":len(result["scenarios"]),"report":str(args.report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
