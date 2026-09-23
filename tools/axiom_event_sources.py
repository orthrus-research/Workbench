"""Reproducible relocation of the selected Cleanroom event implementation.

No dispatch algorithm is rewritten. Loader ownership is an explicit port, and
LaunchWrapper's transformer interface is replaced by the same three-argument
contract. The event class-space owns the transformers so recursive superclass
loading uses the native JVM and the correct candidate definitions.
"""

import hashlib
import json
from pathlib import Path
import re
import subprocess

PACKAGE = "research.orthrus.axiom.nativeevents"
UPSTREAM = "net.minecraftforge.fml.common.eventhandler"
ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/cleanroom-events.lock.json"
OUTPUT = ROOT / "modules/axiom/jvm/src/main/java" / PACKAGE.replace(".", "/")


def extract(source):
    """Only declared namespace/host-port substitutions, including ASM descriptors."""
    text = source.replace(UPSTREAM, PACKAGE).replace(UPSTREAM.replace(".", "/"), PACKAGE.replace(".", "/"))
    text = text.replace("package net.minecraftforge.fml.common.asm.transformers;", "package " + PACKAGE + ";")
    for name in ("FMLLog", "Loader", "ModContainer"):
        text = text.replace("import net.minecraftforge.fml.common." + name + ";\n", "")
    text = text.replace("import net.minecraft.launchwrapper.IClassTransformer;\n", "")
    text = re.sub(r"\bIClassTransformer\b", "BytecodeTransformer", text)
    text = re.sub(r"\bModContainer\b", "EventContext.Owner", text)
    text = text.replace("Loader.instance()", "EventContext.instance()")
    text = text.replace("FMLLog.log", "EventContext.log")
    return "// Relocated selected Cleanroom source; see spec/native-events.md.\n" + "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def sources(repository):
    lock = json.loads(LOCK.read_text())
    result = {}
    for row in lock["references"]:
        raw = subprocess.check_output(["git", "-C", str(repository), "show", lock["revision"] + ":" + row["path"]])
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("Event source hash mismatch: " + row["path"])
        if "/eventhandler/" in row["path"] or row["path"].endswith(("EventSubscriptionTransformer.java", "EventSubscriberTransformer.java")):
            result[Path(row["path"]).name] = extract(raw.decode("utf-8"))
    return result


def check(repository):
    for name, expected in sources(repository).items():
        if (OUTPUT / name).read_text() != expected:
            raise ValueError("Event source extraction drift: " + name)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanroom-root", required=True, type=Path)
    args = parser.parse_args()
    check(args.cleanroom_root)
    print("Selected Cleanroom event source retention verified")
