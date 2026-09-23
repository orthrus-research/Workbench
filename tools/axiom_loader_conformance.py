#!/usr/bin/env python3
"""Compare loader order/admission to pinned GroovyScript methods on real temporary files.

Only source-method control flow is claimed: linked-map, string-count and log
dependencies use explicit adapters, not the complete GroovyScript runtime.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groovyscript", type=Path, required=True)
    parser.add_argument("--java-home", type=Path, required=True)
    parser.add_argument("--engine-home", type=Path, required=True)
    args = parser.parse_args(argv)
    lock = json.loads((ROOT / "modules/axiom/sources/supersymmetry.lock.json").read_text())

    def source(name):
        path = "src/main/java/com/cleanroommc/groovyscript/sandbox/" + name + ".java"
        record = next(row for row in lock["references"] if row["repository"] == "groovyscript" and row["path"] == path)
        raw = (args.groovyscript / path).read_bytes()
        if sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("loader oracle source differs from its lock")
        return raw.decode()

    def method(text, marker):
        start = text.index(marker)
        end = text.index("\n    }", start) + len("\n    }")
        return text[start:end].replace("private static", "public static", 1)

    sandbox, config = source("SandboxData"), source("RunConfig")
    selected = method(sandbox, "static Collection<File> getSortedFilesOf(")
    selected += "\n" + method(sandbox, "public static boolean isGroovyFile(")
    selected += "\n" + method(config, "private static String sanitizePath(")
    selected += "\n" + method(config, "private static boolean checkValid(")
    prefix = '''package research.orthrus.axiom;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;
public class UpstreamLoader {
 static final FileVisitOption[] FOLLOW_LINKS={FileVisitOption.FOLLOW_LINKS}, NO_VISIT_OPTIONS={};
 static final String[] GROOVY_SUFFIXES={".groovy",".gvy",".gy",".gsh"};
 // Independent minimal container adapters for operations used by the methods.
 static class Object2IntLinkedOpenHashMap<K> {
  final List<K> order=new ArrayList<>(); final Map<K,Integer> values=new HashMap<>();
  boolean containsKey(K key){return values.containsKey(key);} int getInt(K key){return values.get(key);}
  void put(K key,int value){if(!values.containsKey(key))order.add(key);values.put(key,value);}
  void putAndMoveToLast(K key,int value){order.remove(key);order.add(key);values.put(key,value);}
  Collection<K> keySet(){return order;}
 }
 static class StringUtils { static int countMatches(String text,String needle){int count=0,at=0;while((at=text.indexOf(needle,at))>=0){count++;at+=needle.length();}return count;} }
 record Pair<L,R>(L key,R value){ L getKey(){return key;} R getValue(){return value;} }
 static class GroovyLog { static class Msg {final List<String> messages=new ArrayList<>();List<String> getSubMessages(){return messages;}void add(String text,Object... values){messages.add(text+Arrays.toString(values));}} }
'''
    home = args.engine_home.resolve(strict=True)
    manifest = json.loads((home / "engine-manifest.json").read_text())
    jars = []
    for name, digest in manifest["jars"].items():
        if Path(name).name != name or (home / "lib" / name).is_symlink() or sha256((home / "lib" / name).read_bytes()).hexdigest() != digest:
            raise ValueError("loader oracle engine library identity differs")
        jars.append(str(home / "lib" / name))
    with tempfile.TemporaryDirectory(prefix="axiom-loader-conformance-") as temporary:
        root = Path(temporary)
        (root / "UpstreamLoader.java").write_text(prefix + selected + "\n}\n")
        classpath = os.pathsep.join(jars)
        java = args.java_home.resolve(strict=True) / "bin"
        subprocess.run([str(java / "javac"), "--release", "17", "-cp", classpath, "-d", str(root), str(root / "UpstreamLoader.java"),
                        str(ROOT / "modules/axiom/tests/oracles/LoaderConformance.java")], check=True, timeout=60)
        subprocess.run([str(java / "java"), "-Xmx192m", "-cp", str(root) + os.pathsep + classpath,
                        "research.orthrus.axiom.LoaderConformance"], cwd=root, check=True, timeout=45)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
