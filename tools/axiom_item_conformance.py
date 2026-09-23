#!/usr/bin/env python3
"""Offline native item/ore qualification; no game, installed pack or recipe-validity claim."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import axiom_item_sources as sources
import axiom_catalog_sources as catalog
import axiom_native_material_sources as linking
import axiom_native_material_conformance as materials
import axiom_native_identity_conformance as identities
import build_axiom_native_materials as build
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK
from axiom_runtime import checked_path, verify_runtime

ROOT=Path(__file__).resolve().parents[1]
ORACLES=ROOT/"modules/axiom/tests/oracles"
DRIVER=ORACLES/"NativeItemConformance.java"
SOURCE_DRIVER=ORACLES/"NativeItemSourceConformance.java"
FIXTURE=ORACLES/"NativeItemProbe.java"
TRANSFORM_DRIVER=ORACLES/"NativeCatalogTransformConformance.java"


def priority_config(priorities):
    return 'general {\n "compatibility options" {\n S:modPriorities <\n'+''.join(' '+p+'\n' for p in priorities)+' >\n }\n}\n'


def assert_order(value, expected):
    if value["result"]["unifier"]["selectedIron"]!=expected: raise ValueError("native configured item order differs")


def qualify(java,home,images,libraries,program,roots,report):
    java,home,images,libraries,program=[identities.ordinary(p).resolve(strict=True) for p in (java,home,images,libraries,program)]
    report=identities.ordinary(report)
    if report.exists(): raise ValueError("native item report must be new")
    tracked=[DRIVER,SOURCE_DRIVER,FIXTURE,TRANSFORM_DRIVER,TARGET_LOCK,identities.POLICY,identities.LOCK,
             *sorted((ROOT/"modules/axiom/sources").glob("*.lock.json")),*sorted((ROOT/"tools").glob("axiom_*.py")),Path(build.__file__),
             *sorted(linking.HOST.glob("*.java")),*[linking.SHARED/(n+".java") for n in (*linking.NAMES,"NativeMaterialClassLoader","NativeVanillaIdentities","NativeMaterialAccess")]]
    frozen={p:p.read_bytes() for p in tracked}
    original=sources.verify_sources({r:roots[r] for r in ("gtceu","cleanroom","groovyscript")})
    producers=catalog.verify_sources({r:roots[r] for r in ("gtceu","cleanroom","supersymmetry")})
    policy=json.loads(frozen[identities.POLICY]); runtime=verify_runtime(java,compiler=True)
    identities.verify_policy(policy,json.loads(frozen[identities.LOCK]),json.loads(identities.PLATFORM.read_bytes()))
    shared,host,engine_manifest,_=build.engine_sources(home)
    regenerated,_,_,evidence=materials.original_sources({r:roots[r] for r in ("gtceu","susy-core","cleanroom")},shared)
    native_sources=sources.retained_sources(original)
    if any(host[n]!=t for n,t in native_sources.items()): raise ValueError("retained native item source differs")
    for name in ("MaterialEvent","MaterialRegistryEvent","PostMaterialEvent"):
        native_sources[name]=catalog.event_source(name,producers["gtceu",catalog.lifecycle.EVENTS+name+".java"])
        if host[name]!=native_sources[name]: raise ValueError("retained native material event differs")
    _,manifest,jars=engine_inputs(home,frozen[TARGET_LOCK])
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as jar:
        if jar.read("axiom/native-items.lock.json")!=frozen[sources.LOCK]: raise ValueError("installed native item source lock differs")
    with zipfile.ZipFile(next((home/"sources").glob("*-sources.jar"))) as jar:
        for name in ("NativeMaterialClassLoader","NativeVanillaIdentities","NativeMaterialAccess"):
            if jar.read("research/orthrus/axiom/"+name+".java")!=frozen[linking.SHARED/(name+".java")]: raise ValueError("installed native item loader differs")
    dependencies=[images/r["path"] for r in policy["images"]]+[libraries/r["path"] for r in policy["libraries"]]
    program_files={p.name:p.read_bytes() for p in program.iterdir() if p.is_file()}
    if set(program_files)!={"program.json","native-materials.jar","native-materials-sources.jar"}: raise ValueError("native program inventory differs")
    environment={k:v for k,v in os.environ.items() if k not in {"JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","_JAVA_OPTIONS","CLASSPATH","LD_PRELOAD","LD_LIBRARY_PATH"}}
    with tempfile.TemporaryDirectory(prefix="axiom-item-conformance-") as temporary:
        work=Path(temporary)
        build.build(java,home,images,libraries,work/"rebuilt")
        if any((work/"rebuilt"/n).read_bytes()!=raw for n,raw in program_files.items()): raise ValueError("native item program differs from installed-source rebuild")
        def compile_jar(name,files,deps):
            path=work/(name+".jar"); build.jar_bytes(path,build.compile_sources(java,files,deps,work/name)); return path
        kernel_sources=linking.assemble(regenerated,{**host,**native_sources})
        source_kernel=compile_jar("source-kernel",kernel_sources,dependencies)
        if source_kernel.read_bytes()!=program_files["native-materials.jar"]: raise ValueError("native item kernel differs from immutable source reconstruction")
        driver=compile_jar("driver",{p.stem:frozen[p].decode() for p in (DRIVER,SOURCE_DRIVER,TRANSFORM_DRIVER)},jars)
        def producer(name,original):
            return compile_jar(name,{**catalog.producer_sources(original),FIXTURE.stem:frozen[FIXTURE].decode()},[source_kernel,*dependencies])
        baseline_producer=producer("producer",producers)
        source_native=compile_jar("source-native",sources.cleanroom_sources(original),dependencies)
        expected_native={"net/minecraftforge/oredict/OreDictionary.class","net/minecraftforge/oredict/OreDictionary$OreRegisterEvent.class","net/minecraftforge/oredict/OreIngredient.class"}
        with zipfile.ZipFile(source_native) as jar:
            if set(jar.namelist())!=expected_native: raise ValueError("source-only native override closure differs")
        count=0
        def run(config,*,recipes=False,kernel=None,producer=baseline_producer,native=None,source_mode=False):
            nonlocal count
            count+=1; directory=work/("run-"+str(count)); directory.mkdir()
            cfg=directory/"gregtech.cfg"; cfg.write_text(config); before=cfg.read_bytes()
            request=directory/"request.properties"; request.write_text("config="+str(cfg)+"\nrecipes="+str(recipes).lower()+"\n")
            kernel=kernel or program/"native-materials.jar"
            command=[str(java/"bin/java"),"--enable-native-access=ALL-UNNAMED","-Xmx512m","-Duser.language=en","-cp",os.pathsep.join(map(str,[driver,*jars]))]
            if source_mode:
                command += ["research.orthrus.axiom.NativeItemSourceConformance",str(request),*map(str,[native or source_native,kernel,producer,*dependencies])]
            else:
                command += ["research.orthrus.axiom.NativeItemConformance",str(images),str(libraries),str(kernel),sha256(kernel.read_bytes()).hexdigest(),str(producer),sha256(producer.read_bytes()).hexdigest(),str(request)]
            completed=subprocess.run(command,cwd=directory,env=environment,capture_output=True,text=True,timeout=60)
            if cfg.read_bytes()!=before: raise ValueError("native item configuration changed caller bytes")
            if completed.returncode: raise ValueError("native item execution failed:\n"+completed.stdout[-1000:]+completed.stderr[-8000:])
            value=json.loads(completed.stdout)
            if value.get("kernelIsolation") is not True or any(value.get(k) is not False for k in ("minecraftLaunched","wholePackParity","generatedGTContent")):
                raise ValueError("native item execution boundary differs")
            return value
        pack=producers["supersymmetry",catalog.PACK_CONFIG]
        baseline=run(pack)
        rebuilt=run(pack,kernel=source_kernel)
        if baseline!=rebuilt: raise ValueError("source/release native item traces differ")
        expected_events={"net.minecraftforge.fml.common.eventhandler.GenericEvent","net.minecraftforge.event.AttachCapabilitiesEvent",
                         "net.minecraftforge.event.RegistryEvent","net.minecraftforge.event.RegistryEvent$Register",
                         "net.minecraftforge.oredict.OreDictionary$OreRegisterEvent",*(linking.PACKAGE+"."+n for n in ("MaterialEvent","MaterialRegistryEvent","PostMaterialEvent"))}
        if set(baseline["eventTransformations"])!=expected_events: raise ValueError("native item event closure differs")
        transformer=producers["cleanroom","src/main/java/net/minecraftforge/fml/common/asm/transformers/EventSubscriptionTransformer.java"]
        transformer_jar=compile_jar("transformer",{"EventSubscriptionTransformer":transformer},dependencies)
        command=[str(java/"bin/java"),"--enable-native-access=ALL-UNNAMED","-Xmx512m","-cp",os.pathsep.join(map(str,[driver,*jars])),
                 "research.orthrus.axiom.NativeCatalogTransformConformance",",".join(sorted(expected_events)),*map(str,[transformer_jar,source_kernel,*dependencies])]
        transformed=subprocess.run(command,cwd=work,env=environment,capture_output=True,text=True,timeout=60)
        if transformed.returncode or json.loads(transformed.stdout)!=baseline["eventTransformations"]: raise ValueError("native item source/release event transform differs: "+transformed.stderr[-3000:])
        recipe_run=run(pack,recipes=True)
        for recipes,accepted in ((False,baseline),(True,recipe_run)):
            original_run=run(pack,recipes=recipes,source_mode=True)
            if original_run["result"]!=accepted["result"]: raise ValueError("original Cleanroom source/release ore traces differ")
        default_order=["minecraft:iron_ingot","gregtech:first","gregtech:second","zzz:first","aaa:first"]
        assert_order(baseline,default_order)
        configs=[]
        for priorities,expected in (([],["aaa:first","gregtech:first","gregtech:second","minecraft:iron_ingot","zzz:first"]),
                                    (["zzz"],["zzz:first","minecraft:iron_ingot","gregtech:first","gregtech:second","aaa:first"]),
                                    (["aaa","aaa","gregtech"],["aaa:first","gregtech:first","gregtech:second","zzz:first","minecraft:iron_ingot"])):
            accepted=run(priority_config(priorities)); assert_order(accepted,expected)
            if accepted["result"]["configuration"]["modPriorities"]!=priorities: raise ValueError("original native string-list parser differs")
            if run(priority_config(priorities),source_mode=True)["result"]!=accepted["result"]: raise ValueError("configured source/release ore traces differ")
            configs.append({"priorities":priorities,"selectedIron":expected,"traceDigest":accepted["traceDigest"]})
        defaults=run(""); assert_order(defaults,default_order)
        edited=deepcopy(producers); key=("gtceu","src/main/java/gregtech/common/ConfigHolder.java")
        before='public String[] modPriorities = {\n                "minecraft",\n                "gregtech"\n        };'
        if edited[key].count(before)!=1: raise ValueError("config default source witness boundary differs")
        edited[key]=edited[key].replace(before,before.replace('"minecraft"','"aaa"'))
        default_edit=run("",producer=producer("default-edit",edited))
        assert_order(default_edit,["aaa:first","gregtech:first","gregtech:second","zzz:first","minecraft:iron_ingot"])
        edited=deepcopy(original); key=("gtceu",sources.PATHS["CustomModPriorityComparator"])
        before="return firstModId.compareTo(secondModId);"
        if edited[key].count(before)!=1: raise ValueError("comparator source witness boundary differs")
        edited[key]=edited[key].replace(before,"return secondModId.compareTo(firstModId);")
        edited_kernel=compile_jar("comparator-edit",linking.assemble(regenerated,{**host,**sources.retained_sources(edited)}),dependencies)
        comparator_edit=run(pack,kernel=edited_kernel)
        expected=deepcopy(baseline["result"]); expected["unifier"]["selectedIron"][-2:]=["aaa:first","zzz:first"]
        if comparator_edit["result"]!=expected: raise ValueError("complete retained comparator source edit did not propagate exactly")
        edited=deepcopy(original); key=("cleanroom","src/main/java/net/minecraftforge/oredict/OreDictionary.java")
        before='registerOre("ingotIron",     Items.IRON_INGOT);'
        if edited[key].count(before)!=1: raise ValueError("native ore source witness boundary differs")
        edited[key]=edited[key].replace(before,'registerOre("ingotIron",     new ItemStack(Items.IRON_INGOT, 2));')
        edited_native=compile_jar("ore-source-edit",sources.cleanroom_sources(edited),dependencies)
        ore_edit=run(pack,native=edited_native,source_mode=True)
        expected=deepcopy(baseline["result"]); stack=expected["oreBaseline"]["names"]["ingotIron"][0]; stack[1]=2; stack[3]=stack[3].replace("Count:1b","Count:2b")
        if ore_edit["result"]!=expected: raise ValueError("original native ore source edit did not propagate exactly")
        result={"schema":"axiom.native-item-conformance.v1","status":"passed","runs":count,
                "engineManifestSha256":sha256(engine_manifest).hexdigest(),"engineJars":manifest["jars"],
                "programSha256":sha256(program_files["native-materials.jar"]).hexdigest(),"programManifestSha256":sha256(program_files["program.json"]).hexdigest(),
                "sourceInputs":{**evidence,**{r+":"+p:sha256(t.encode()).hexdigest() for (r,p),t in {**original,**producers}.items()}},
                "qualificationInputs":{p.relative_to(ROOT).as_posix():sha256(raw).hexdigest() for p,raw in frozen.items()},
                "images":policy["images"],"libraryInputs":policy["libraries"],"runtimeInputs":runtime["runtimeFiles"]+runtime["compilerFiles"],
                "baseline":baseline,"recipeRewrite":recipe_run["result"]["oreBaseline"],"configurationRuns":configs,
                "sourceRebuildIdentical":True,"sourceExecutionIdentical":True,"cleanroomSourceExecutionIdentical":True,
                "nativeEventTransformationIdentical":True,"configDefaultSourceEdit":True,"comparatorSourceEdit":True,"nativeOreSourceEdit":True,
                "callerConfigurationUnchanged":True,"ownerUniverse":"explicit-native-fixtures","wholePackParity":False,
                "installedCompositionQualified":False,"generatedGTContent":False,"fullRecipeRegistration":False,"minecraftLaunched":False}
    if any(p.read_bytes()!=raw for p,raw in frozen.items()): raise ValueError("native item qualification source changed")
    if sources.verify_sources({r:roots[r] for r in ("gtceu","cleanroom","groovyscript")})!=original or catalog.verify_sources({r:roots[r] for r in ("gtceu","cleanroom","supersymmetry")})!=producers: raise ValueError("native item upstream inputs changed")
    if engine_inputs(home,frozen[TARGET_LOCK])[0]!=engine_manifest or any((program/n).read_bytes()!=raw for n,raw in program_files.items()): raise ValueError("installed item program changed")
    for root,rows in ((images,policy["images"]),(libraries,policy["libraries"]),(java,runtime["runtimeFiles"]+runtime["compilerFiles"])):
        for row in rows: checked_path(root,row)
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open("x") as out: json.dump(result,out,indent=2); out.write("\n")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("java-home","engine-home","images","library-root","program","gtceu","susy-core","cleanroom","supersymmetry","groovyscript","report"):
        parser.add_argument("--"+name,type=Path,required=True)
    a=parser.parse_args(argv)
    result=qualify(a.java_home,a.engine_home,a.images,a.library_root,a.program,{r:getattr(a,r.replace("-","_")) for r in ("gtceu","susy-core","cleanroom","supersymmetry","groovyscript")},a.report)
    print(json.dumps({"status":result["status"],"runs":result["runs"],"items":result["baseline"]["result"]["identities"]["count"],"oreNames":result["baseline"]["result"]["oreBaseline"]["nameCount"],"wholePackParity":False}))
    return 0


if __name__=="__main__": raise SystemExit(main())
