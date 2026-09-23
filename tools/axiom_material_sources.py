"""Audited extraction recipe for Axiom's internal native material-property kernel.

This is not a material/ore registry builder. It retains complete property class
bodies, with an explicit dust/gem/ingot/empty key domain and a name/property
carrier in place of game-dependent Material. No fluid or flag stub is supplied.
"""

import re

PREFIX = "src/main/java/gregtech/api/unification/material/properties/"
CLASSES = ("IMaterialProperty", "MaterialProperties", "PropertyKey", "DustProperty", "GemProperty", "IngotProperty")
PATHS = tuple(PREFIX + name + ".java" for name in CLASSES)
MATERIAL = "src/main/java/gregtech/api/unification/material/Material.java"
MANAGER = "src/main/java/gregtech/api/unification/material/registry/IMaterialRegistryManager.java"
PACKAGE = "research.orthrus.axiom"
NOTICE = "// Extracted from pinned GTCEu; LGPL-3.0. See sources/NOTICE.md and spec/material-properties.md.\n"


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("material extraction boundary differs: " + before)
    return text.replace(before, after, 1)


def declaration(text, marker):
    if text.count(marker) != 1:
        raise ValueError("material method boundary differs: " + marker)
    start = text.index(marker)
    end = text.index("\n    }", start) + len("\n    }")
    return text[start:end]


def checked_carriers(originals, carrier, phase):
    marker = "public <T extends IMaterialProperty> void setProperty("
    expected = declaration(originals[MATERIAL], marker).replace(
        "GregTechAPI.materialManager.canModifyMaterials()", "canModifyMaterials.getAsBoolean()")
    if declaration(carrier, marker) != expected:
        raise ValueError("material mutation guard differs from source extraction")
    expected = declaration(originals[MANAGER], "default boolean canModifyMaterials()")
    expected = expected.replace("default boolean", "boolean").replace("this.getPhase()", "this").replace("Phase.", "MaterialPhase.")
    if declaration(phase, "boolean canModifyMaterials()") != expected:
        raise ValueError("material phase predicate differs from source extraction")
    # Phase names and order are part of the carrier contract, checked against source.
    enum = originals[MANAGER].split("enum Phase {", 1)[1]
    names = re.findall(r"^        ([A-Z]+)[,\n]", enum, re.MULTILINE)
    if names != ["PRE", "OPEN", "CLOSED", "FROZEN"] or "PRE, OPEN, CLOSED, FROZEN;" not in phase:
        raise ValueError("material phase domain differs")


def extract(name, text, package=PACKAGE):
    """All substitutions are explicit; no guessed method parser or fallback."""
    if name not in CLASSES:
        raise ValueError("unadmitted material source class: " + name)
    text = replace_once(text, "package gregtech.api.unification.material.properties;", "package " + package + ";")
    if name in ("MaterialProperties", "IngotProperty"):
        text = replace_once(text, "import gregtech.api.unification.material.Material;\n", "")
        text = re.sub(r"\bMaterial\b", "MaterialState", text)
        # The diagnostic is upstream text, not a type identifier.
        text = text.replace('"MaterialState ', '"Material ')
    if name == "IngotProperty":
        text = replace_once(text, "import org.jetbrains.annotations.Nullable;\n", "")
        text = text.replace("@Nullable", "")
    if name == "MaterialProperties":
        text = replace_once(text, "import gregtech.api.util.GTLog;\n", "")
        text = replace_once(text, "import gregtech.common.ConfigHolder;\n", "")
        text = replace_once(text, "PropertyKey.FLUID, PropertyKey.DUST,", "PropertyKey.DUST,")
        text = replace_once(text, '''                if (ConfigHolder.misc.debug) {
                    GTLog.logger.debug("Creating empty placeholder MaterialState {}", material);
                }
''', "")
    if name == "PropertyKey":
        # The material construction domain now supplies the complete GT key
        # catalog. Merely looking up a key does not fabricate its dependencies.
        pass
    # Deliberately internal until profile construction can supply real materials.
    text = replace_once(text, "public " + ("interface" if name == "IMaterialProperty" else "class") + " " + name,
                        ("interface" if name == "IMaterialProperty" else "class") + " " + name)
    return NOTICE + text
