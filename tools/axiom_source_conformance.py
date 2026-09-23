#!/usr/bin/env python3
"""Compare retained domain rules with locked GTCEu source executed by the selected JVM."""

import argparse
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import zipfile

from axiom_runtime import checked_path, verify_runtime
import axiom_material_sources as materials

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "modules/axiom/sources/supersymmetry.lock.json"
DRIVER = ROOT / "modules/axiom/tests/oracles/SourceConformance.java"
MATERIAL_DRIVER = ROOT / "modules/axiom/tests/oracles/MaterialConformance.java"
MATERIAL_ROOT = ROOT / "modules/axiom/jvm/src/main/java/research/orthrus/axiom"


def material_inputs(originals, root=MATERIAL_ROOT):
    """Bind every retained extraction and the two deliberately non-game carriers."""
    retained = {name: (root / (name + ".java")).read_text() for name in (*materials.CLASSES, "MaterialState", "MaterialPhase")}
    for name in materials.CLASSES:
        if retained[name] != materials.extract(name, originals[materials.PREFIX + name + ".java"]):
            raise ValueError("retained material source differs from audited extraction: " + name)
    materials.checked_carriers(originals, retained["MaterialState"], retained["MaterialPhase"])
    return retained


def source(root, relative, lock=None):
    lock = json.loads(LOCK.read_bytes()) if lock is None else lock
    row = next(row for row in lock["references"] if row["repository"] == "gtceu" and row["path"] == relative)
    raw = (root / relative).read_bytes()
    if sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("oracle source differs from the locked file: " + relative)
    return raw.decode()


def engine_inputs(home, source_lock):
    raw = (home / "engine-manifest.json").read_bytes()
    manifest = json.loads(raw)
    if not manifest.get("jars") or any(Path(name).name != name for name in manifest["jars"]):
        raise ValueError("invalid oracle engine library inventory")
    if {path.name for path in (home / "lib").iterdir()} != set(manifest["jars"]):
        raise ValueError("unlisted or missing oracle engine library")
    jars = [checked_path(home, {"path": "lib/" + name, "sha256": digest}) for name, digest in manifest["jars"].items()]
    bindings = []
    for jar in jars:
        with zipfile.ZipFile(jar) as archive:
            if "axiom/supersymmetry.lock.json" in archive.namelist():
                bindings.append(archive.read("axiom/supersymmetry.lock.json"))
    if bindings != [source_lock]:
        raise ValueError("installed source binding differs from comparison source")
    return raw, manifest, jars


def method(text, name):
    marker = "private Pair<Boolean, int[]> " + name + "("
    start = text.index(marker)
    end = text.index("\n    }", start) + len("\n    }")
    return text[start:end].replace("private ", "public ", 1)


def builder_methods(text):
    """Extract complete locked method declarations, not a reimplementation of conditions."""
    markers = ("protected void validateGroovy(", "protected static String getRequiredString(")
    extracted = []
    for marker in markers:
        if text.count(marker) != 1:
            raise ValueError("builder source method boundary differs: " + marker)
        start = text.index(marker)
        end = text.index("\n    }", start) + len("\n    }")
        extracted.append(text[start:end].replace("@NotNull ", ""))
    return "\n".join(extracted)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtceu", required=True, type=Path)
    parser.add_argument("--java-home", required=True, type=Path)
    parser.add_argument("--engine-home", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Write a new identity-bound comparison receipt")
    args = parser.parse_args(argv)
    if args.report is not None and args.report.exists():
        raise ValueError("report already exists")
    raw_lock = LOCK.read_bytes()
    lock = json.loads(raw_lock)
    driver = DRIVER.read_bytes()
    material_driver = MATERIAL_DRIVER.read_bytes()
    java = args.java_home.resolve(strict=True)
    policy = verify_runtime(java, compiler=True)
    runtime = policy["runtimeFiles"] + policy["compilerFiles"]
    home = args.engine_home.resolve(strict=True)
    raw_manifest, manifest, jars = engine_inputs(home, raw_lock)
    # These substitutions only remove annotation/type dependencies. The copied
    # control flow and arithmetic remain those of the independently read source.
    source_paths = ("src/main/java/gregtech/api/recipes/logic/OverclockingLogic.java", "src/main/java/gregtech/api/recipes/Recipe.java",
                    "src/main/java/gregtech/api/recipes/RecipeBuilder.java", *materials.PATHS, materials.MATERIAL, materials.MANAGER)
    originals = {name: source(args.gtceu, name, lock) for name in source_paths}
    retained_materials = material_inputs(originals)
    extraction_recipe = Path(materials.__file__).read_bytes()
    overclock = originals[source_paths[0]]
    overclock = overclock.replace("import org.jetbrains.annotations.NotNull;", "").replace("@NotNull", "")
    recipe = originals[source_paths[1]]
    methods = method(recipe, "matchesItems") + "\n" + method(recipe, "matchesFluid")
    substitutions = {"GTRecipeInput": "OracleInput", "ItemStack": "Item", "FluidStack": "Fluid", ".isEmpty()": ".empty()", ".getCount()": ".count"}
    for original, replacement in substitutions.items():
        methods = methods.replace(original, replacement)
    builder_prefix = '''package research.orthrus.axiom;
import java.util.*;
import java.util.function.Supplier;
public class UpstreamBuilderValidation {
 static class GroovyLog {
  static class Msg {
   final List<String> messages = new ArrayList<>();
   void add(boolean when, Supplier<String> message) { if (when) messages.add(message.get()); }
  }
 }
 record MapShape(int maxInputs, int maxOutputs, int maxFluidInputs, int maxFluidOutputs) {
  int getMaxInputs() { return maxInputs; } int getMaxOutputs() { return maxOutputs; }
  int getMaxFluidInputs() { return maxFluidInputs; } int getMaxFluidOutputs() { return maxFluidOutputs; }
 }
 int EUt, duration;
 List<?> inputs, outputs, fluidInputs, fluidOutputs;
 MapShape recipeMap;
 UpstreamBuilderValidation(int eut, int time, List<?> items, List<?> products, List<?> fluids, List<?> fluidProducts, int[] shape) {
  EUt=eut; duration=time; inputs=items; outputs=products; fluidInputs=fluids; fluidOutputs=fluidProducts;
  recipeMap=new MapShape(shape[0], shape[1], shape[2], shape[3]);
 }
 List<String> errors() { var log=new GroovyLog.Msg(); validateGroovy(log); return log.messages; }
'''
    prefix = '''package research.orthrus.axiom;
import java.util.*;
import static research.orthrus.axiom.Domain.*;
public class UpstreamMatching {
 public static class Pair<L,R> { final L left; final R right; Pair(L l,R r){left=l;right=r;} static <L,R> Pair<L,R> of(L l,R r){return new Pair<>(l,r);} }
 final List<OracleInput> inputs, fluidInputs;
 UpstreamMatching(List<Ingredient> items,List<Ingredient> fluids,Registry registry){
  inputs=items.stream().map(i->new OracleInput(i,registry)).toList();
  fluidInputs=fluids.stream().map(i->new OracleInput(i,registry)).toList();
 }
 record OracleInput(Ingredient value,Registry registry){
  int getAmount(){return value.amount();} boolean isNonConsumable(){return value.nonConsumable();}
  boolean acceptsStack(Item item){return value.accepts(item,registry);} boolean acceptsFluid(Fluid fluid){return value.accepts(fluid);}
 }
'''
    with tempfile.TemporaryDirectory(prefix="axiom-source-conformance-") as temporary:
        directory = Path(temporary)
        (directory / "OverclockingLogic.java").write_text(overclock)
        (directory / "UpstreamMatching.java").write_text(prefix + methods + "\n}\n")
        (directory / "UpstreamBuilderValidation.java").write_text(builder_prefix + builder_methods(originals[source_paths[2]]) + "\n}\n")
        (directory / "SourceConformance.java").write_bytes(driver)
        (directory / "MaterialConformance.java").write_bytes(material_driver)
        material_directory = directory / "material-originals"
        material_directory.mkdir()
        material_java = []
        for name in materials.CLASSES:
            path = material_directory / (name + ".java")
            body = materials.extract(name, originals[materials.PREFIX + name + ".java"], materials.PACKAGE + ".oracle")
            if name == "PropertyKey":
                # This older comparison deliberately exercises only dust/gem/ingot
                # graphs. The production catalog is now complete; do not create
                # fake extra classes in the comparison's isolated namespace.
                body = re.sub(r"    public static final PropertyKey<[^;]+;\n",
                              lambda match: match[0] if re.search(r"\b(?:DUST|GEM|INGOT|EMPTY) =", match[0]) else "", body)
            path.write_text(body)
            material_java.append(str(path))
        for name in ("MaterialState", "MaterialPhase"):
            path = material_directory / (name + ".java")
            path.write_text(retained_materials[name].replace("package " + materials.PACKAGE + ";", "package " + materials.PACKAGE + ".oracle;"))
            material_java.append(str(path))
        classpath = os.pathsep.join(map(str, jars))
        environment = dict(os.environ)
        for name in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"):
            environment.pop(name, None)
        subprocess.run([str(java / "bin/javac"), "--release", "25", "-proc:none", "-cp", classpath, "-d", str(directory),
                        str(directory / "OverclockingLogic.java"), str(directory / "UpstreamMatching.java"),
                        str(directory / "UpstreamBuilderValidation.java"), str(directory / "SourceConformance.java"),
                        str(directory / "MaterialConformance.java"), *material_java],
                       cwd=directory, env=environment, check=True, timeout=60)
        run = subprocess.run([str(java / "bin/java"), "-Xmx192m", "-cp", str(directory) + os.pathsep + classpath,
                              "research.orthrus.axiom.SourceConformance"], cwd=directory, env=environment, capture_output=True, text=True, timeout=60)
        if run.returncode:
            raise AssertionError("native source comparison failed:\n" + run.stdout[-4000:] + run.stderr[-6000:])
        result = json.loads(run.stdout)
        if (result.get("overclockVectors") != 20320 or result.get("allocationVectors") != 40000
                or result.get("builderValidationVectors") != 20000
                or result.get("materialPropertyOperations") != 48000 or result.get("materialPropertyGraphs") != 2000
                or result.get("execution") != "native-jvm" or result.get("sourceMethodsCompared") != 5
                or result.get("wholePackParity") is not False):
            raise ValueError("incomplete native source comparison")
    if engine_inputs(home, raw_lock)[0] != raw_manifest or LOCK.read_bytes() != raw_lock or DRIVER.read_bytes() != driver:
        raise ValueError("comparison binding changed")
    if (material_inputs(originals) != retained_materials or MATERIAL_DRIVER.read_bytes() != material_driver
            or Path(materials.__file__).read_bytes() != extraction_recipe):
        raise ValueError("material comparison binding changed")
    for row in runtime:
        checked_path(java, row)
    for name, original in originals.items():
        if source(args.gtceu, name, lock) != original:
            raise ValueError("comparison source changed")
    if args.report is not None:
        report = {"schema": "axiom.source-conformance.v1", "status": "passed", "sourceLockSha256": sha256(raw_lock).hexdigest(),
                  "sourceInputs": {name: sha256(text.encode()).hexdigest() for name, text in originals.items()},
                  "engineJars": manifest["jars"], "runtimeInputs": runtime, "driverSha256": sha256(driver).hexdigest(), "result": result,
                  "materialDriverSha256": sha256(material_driver).hexdigest(),
                  "materialExtractionRecipeSha256": sha256(extraction_recipe).hexdigest(),
                  "retainedMaterialSources": {name: sha256(text.encode()).hexdigest() for name, text in retained_materials.items()},
                  "sourceSubstitutions": ["OverclockingLogic: remove NotNull annotation/import only",
                                          "Recipe allocation: public visibility, shared explicit domain types/predicates",
                                          "RecipeBuilder validation: remove NotNull; independent list, map-shape and lazy-message carriers; no registry or builder lifecycle executed",
                                          "Material property classes: package/visibility, Nullable, MaterialState type; dust/gem/ingot/empty catalog only; debug log omitted",
                                          "MaterialState name/property/phase carrier shared; setProperty and phase predicate checked against locked methods; no material registration or flags"],
                  "execution": "Compiled locked source methods execute directly on the profile-selected JVM",
                  "notQualified": ["installed artifact/source correspondence", "full upstream predicates and capabilities",
                                   "actual pack registration environment", "whole-pack validity"]}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
