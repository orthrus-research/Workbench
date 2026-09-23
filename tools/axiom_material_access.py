"""Run the original access transformer for the disposable compiler classpath."""
import base64
import json
from pathlib import Path
import re
import subprocess
import os
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "modules/axiom/jvm/src/main/java/research/orthrus/axiom/NativeMaterialAccess.java"


def transform(java, dependencies, rules, work):
    work.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}}
    source = work / DRIVER.name
    source.write_bytes(DRIVER.read_bytes())
    result = subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-d", str(work), str(source)], capture_output=True, text=True, env=env, timeout=30)
    if result.returncode: raise ValueError("access transform driver failed: " + result.stderr[-3000:])
    result = subprocess.run([str(java / "bin/java"), "-cp", str(work), "research.orthrus.axiom.NativeMaterialAccess",
                             base64.b64encode(rules.encode()).decode(), *map(str, dependencies)], capture_output=True, text=True, env=env, timeout=30)
    if result.returncode: raise ValueError("native access transform failed: " + result.stderr[-4000:])
    return base64.b64decode(result.stdout.strip(), validate=True)


def compiler_overlay(java, files, dependencies, work):
    marker = re.search(r'String RULES = ("(?:\\.|[^"\\])*");', files["OreAccessRules"])
    if marker is None: raise ValueError("missing source-qualified Block access rules")
    raw = transform(java, dependencies, json.loads(marker[1]), work)
    destination = work / "block-access.jar"
    with zipfile.ZipFile(destination, "x") as jar:
        jar.writestr("net/minecraft/block/Block.class", raw)
    return destination
