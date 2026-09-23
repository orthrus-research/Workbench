#!/usr/bin/env python3
"""Offline qualification of the complete GT catalog at the native FROZEN material checkpoint."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import axiom_catalog_sources as sources
import axiom_native_material_sources as linking
import axiom_native_material_conformance as materials
import axiom_native_identity_conformance as identities
import build_axiom_native_materials as build
from axiom_source_conformance import engine_inputs, LOCK as TARGET_LOCK
from axiom_runtime import checked_path, verify_runtime

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "modules/axiom/tests/oracles/NativeCatalogConformance.java"
FIXTURE = ROOT / "modules/axiom/tests/oracles/NativeCatalogProbe.java"
TRANSFORM_DRIVER = ROOT / "modules/axiom/tests/oracles/NativeCatalogTransformConformance.java"


def validate_config_matrix(results):
    base = results[False,False]["snapshot"]
    for (gems,stones), result in results.items():
        config = result["configuration"]
        if config["generateLowQualityGems"] != gems or config["allUniqueStoneTypes"] != stones:
            raise ValueError("native configuration values differ")
        snapshot = result["snapshot"]
        if {k:v for k,v in snapshot.items() if k not in ("prefixes","liveCatalogReads")} != {k:v for k,v in base.items() if k not in ("prefixes","liveCatalogReads")}:
            raise ValueError("configuration changed material declarations")
        if (gems or stones) and snapshot["prefixes"] == base["prefixes"]:
            raise ValueError("configuration effect was not observed")


def qualify(java, home, images, libraries, program, roots, report):
    java,home,images,libraries,program = [identities.ordinary(p).resolve(strict=True) for p in (java,home,images,libraries,program)]
    report=identities.ordinary(report)
    if report.exists(): raise ValueError("catalog report must be new")
    tracked = [DRIVER,FIXTURE,TRANSFORM_DRIVER,TARGET_LOCK,identities.POLICY,identities.LOCK,
               *sorted((ROOT/"modules/axiom/sources").glob("*.lock.json")),
               *sorted((ROOT/"tools").glob("axiom_*.py")),Path(build.__file__),
               *sorted(linking.HOST.glob("*.java")),*[linking.SHARED/(n+".java") for n in (*linking.NAMES,"NativeMaterialClassLoader","NativeVanillaIdentities","NativeMaterialAccess")]]
    frozen={p:p.read_bytes() for p in tracked}
    originals=sources.verify_sources({r:roots[r] for r in ("gtceu","cleanroom","supersymmetry")})
    runtime=verify_runtime(java,compiler=True); policy=json.loads(frozen[identities.POLICY])
    shared,host,engine_manifest,_=build.engine_sources(home)
    regenerated,_,_,material_evidence=materials.original_sources({r:roots[r] for r in ("gtceu","susy-core","cleanroom")},shared)
    for name in ("MaterialEvent","MaterialRegistryEvent","PostMaterialEvent"):
        if host[name] != sources.event_source(name,originals["gtceu",sources.lifecycle.EVENTS+name+".java"]):
            raise ValueError("native material event differs from original source: "+name)
    _,manifest,jars=engine_inputs(home,frozen[TARGET_LOCK])
    with zipfile.ZipFile(next(j for j in jars if j.name.startswith("workbench-axiom-engine-"))) as jar:
        if jar.read("axiom/material-catalog.lock.json")!=frozen[sources.LOCK]: raise ValueError("installed catalog source lock differs")
    with zipfile.ZipFile(next((home/"sources").glob("*-sources.jar"))) as jar:
        for name in ("NativeMaterialClassLoader","NativeVanillaIdentities","NativeMaterialAccess"):
            if jar.read("research/orthrus/axiom/"+name+".java")!=frozen[linking.SHARED/(name+".java")]: raise ValueError("installed native catalog loader source differs")
    dependencies=[images/r["path"] for r in policy["images"]]+[libraries/r["path"] for r in policy["libraries"]]
    program_files={p.name:p.read_bytes() for p in program.iterdir() if p.is_file()}
    if set(program_files)!={"program.json","native-materials.jar","native-materials-sources.jar"}: raise ValueError("native program inventory differs")
    environment={k:v for k,v in os.environ.items() if k not in {"JAVA_TOOL_OPTIONS","JDK_JAVA_OPTIONS","_JAVA_OPTIONS","CLASSPATH","LD_PRELOAD","LD_LIBRARY_PATH"}}
    with tempfile.TemporaryDirectory(prefix="axiom-catalog-conformance-") as temporary:
        work=Path(temporary)
        build.build(java,home,images,libraries,work/"rebuilt")
        if any((work/"rebuilt"/n).read_bytes()!=raw for n,raw in program_files.items()): raise ValueError("native program differs from installed-source rebuild")
        classes=build.compile_sources(java,linking.assemble(regenerated,host),dependencies,work/"source")
        build.jar_bytes(work/"source.jar",classes)
        if (work/"source.jar").read_bytes()!=program_files["native-materials.jar"]: raise ValueError("native kernel differs from pinned source reconstruction")
        build.jar_bytes(work/"driver.jar",build.compile_sources(java,{p.stem:frozen[p].decode() for p in (DRIVER,TRANSFORM_DRIVER)},jars,work/"driver"))
        def producer(name,original):
            files={**sources.producer_sources(original),FIXTURE.stem:frozen[FIXTURE].decode()}
            build.jar_bytes(work/(name+".jar"),build.compile_sources(java,files,[program/"native-materials.jar",*dependencies],work/name))
            return work/(name+".jar")
        baseline=producer("catalog",originals)
        run_number=0
        def run(config, *, jar=baseline, language="en", failure="none", rejection=None, kernel=None, locale_failure=False, config_mode=None):
            nonlocal run_number
            run_number+=1; directory=work/("run-"+str(run_number)); directory.mkdir()
            cfg=directory/"gregtech.cfg"; cfg.write_text(config)
            selected=cfg
            if config_mode=="missing": selected=directory/"missing.cfg"
            if config_mode=="indirect": selected=directory/"link.cfg"; selected.symlink_to(cfg)
            if config_mode=="oversize": cfg.write_bytes(b"#"*(1024*1024+1))
            config_bytes=cfg.read_bytes()
            request=directory/"request.properties"; request.write_text("config="+str(selected)+"\nfailure="+failure+"\nlocaleFailure="+str(locale_failure).lower()+"\n")
            kernel=kernel or program/"native-materials.jar"
            command=[str(java/"bin/java"),"--enable-native-access=ALL-UNNAMED","-Xmx512m","-Duser.language="+language,
                     "-cp",os.pathsep.join(map(str,[work/"driver.jar",*jars])),"research.orthrus.axiom.NativeCatalogConformance",
                     str(images),str(libraries),str(kernel),sha256(kernel.read_bytes()).hexdigest(),str(jar),sha256(jar.read_bytes()).hexdigest(),str(request)]
            completed=subprocess.run(command,cwd=directory,env=environment,capture_output=True,text=True,timeout=90)
            if cfg.read_bytes()!=config_bytes: raise ValueError("native configuration parsing modified caller input")
            if rejection:
                if completed.returncode==0 or rejection not in completed.stderr: raise ValueError("catalog negative input not rejected: "+completed.stderr[-6000:])
                return
            if completed.returncode: raise ValueError("native catalog execution failed:\n"+completed.stdout[-1000:]+completed.stderr[-7000:])
            value=json.loads(completed.stdout)
            if value.get("kernelIsolation") is not True or any(value.get(k) is not False for k in ("wholePackParity","minecraftLaunched","generatedContent")):
                raise ValueError("catalog execution boundary differs")
            return value
        pack_config=originals["supersymmetry",sources.PACK_CONFIG]
        accepted=run(pack_config)
        if accepted!=run(pack_config,kernel=work/"source.jar"): raise ValueError("source/release catalog traces differ")
        expected_events={"net.minecraftforge.fml.common.eventhandler.GenericEvent",
                         "net.minecraftforge.event.RegistryEvent","net.minecraftforge.event.RegistryEvent$Register",
                         *(linking.PACKAGE+"."+n for n in ("MaterialEvent","MaterialRegistryEvent","PostMaterialEvent"))}
        if set(accepted["eventTransformations"])!=expected_events: raise ValueError("native event transformation closure differs: "+repr(set(accepted["eventTransformations"])))
        transformer=originals["cleanroom","src/main/java/net/minecraftforge/fml/common/asm/transformers/EventSubscriptionTransformer.java"]
        build.jar_bytes(work/"transformer.jar",build.compile_sources(java,{"EventSubscriptionTransformer":transformer},dependencies,work/"transformer"))
        command=[str(java/"bin/java"),"--enable-native-access=ALL-UNNAMED","-Xmx512m","-cp",os.pathsep.join(map(str,[work/"driver.jar",*jars])),
                 "research.orthrus.axiom.NativeCatalogTransformConformance",",".join(sorted(expected_events)),*map(str,[work/"transformer.jar",program/"native-materials.jar",*dependencies])]
        transformed=subprocess.run(command,cwd=work,env=environment,capture_output=True,text=True,timeout=60)
        if transformed.returncode or json.loads(transformed.stdout)!=accepted["eventTransformations"]:
            raise ValueError("source/release event transformation bytes differ: "+transformed.stderr[-4000:])
        configs={}; configuration_runs=[]
        for gems in (False,True):
            for stones in (False,True):
                cfg='general {\n "recipe options" {\n B:generateLowQualityGems='+str(gems).lower()+'\n }\n "worldgen options" {\n B:allUniqueStoneTypes='+str(stones).lower()+'\n }\n}\n'
                value=run(cfg); configs[gems,stones]=value["result"]
                configuration_runs.append({"gems":gems,"stones":stones,"configSha256":sha256(cfg.encode()).hexdigest(),"traceDigest":value["traceDigest"]})
        validate_config_matrix(configs)
        if accepted["result"]!=configs[False,False]: raise ValueError("pinned pack defaults differ from explicit false configuration")
        defaults=run("general {\n}\n")
        if defaults["result"]!=accepted["result"]: raise ValueError("original configuration defaults differ")
        default_originals=dict(originals); config_key="gtceu","src/main/java/gregtech/common/ConfigHolder.java"
        default_originals[config_key]=default_originals[config_key].replace("public boolean generateLowQualityGems = false;","public boolean generateLowQualityGems = true;")
        default_jar=producer("changed-default",default_originals)
        changed_default=run("general {\n}\n",jar=default_jar)["result"]
        expected_default=deepcopy(configs[True,False]); expected_default["configuration"]["generateLowQualityGems.property"][2]="true"
        if changed_default!=expected_default: raise ValueError("source configuration default did not propagate")
        invalid=run('general {\n "recipe options" {\n B:generateLowQualityGems=not-a-boolean\n }\n}\n')
        if invalid["result"]["snapshot"]!=accepted["result"]["snapshot"] or invalid["result"]["configuration"]["generateLowQualityGems"] is not False:
            raise ValueError("native invalid boolean fallback differs")
        run("}\n",rejection="Native parser changed configuration input")
        for mode,rejection in (("missing","Explicit bounded configuration file required"),("indirect","Indirect configuration input"),("oversize","Explicit bounded configuration file required")):
            run(pack_config,config_mode=mode,rejection=rejection)
        failure_runs=[]
        for phase,count in (("PRE",0),("OPEN",602),("CLOSED",602)):
            value=run(pack_config,failure=phase)
            if value["result"]["phase"]!=phase or value["result"]["materialCount"]!=count: raise ValueError("native lifecycle failure state differs")
            failure_runs.append(value)
        changed_originals=dict(originals)
        key="gtceu",sources.BASE+"materials/MaterialFlagAddition.java"
        before="OreProperty oreProp = Aluminium.getProperty(PropertyKey.ORE);\n        oreProp.setOreByProducts(Bauxite, Bauxite, Ilmenite, Rutile);\n        oreProp.setWashedIn(SodiumPersulfate);"
        if changed_originals[key].count(before)!=1: raise ValueError("source-edit witness boundary differs")
        changed_originals[key]=changed_originals[key].replace(before,before.replace("SodiumPersulfate","Water"))
        edited=producer("edited",changed_originals); changed=run(pack_config,jar=edited)
        expected=deepcopy(accepted["result"])
        ore=expected["snapshot"]["materials"]["gregtech:aluminium"]["properties"]["ore"]
        ore["washedIn"]="gregtech:water"
        if changed["result"]!=expected: raise ValueError("cross-material source-edit propagation differs")
        locale=run(pack_config,language="tr",locale_failure=True)
        if locale!=run(pack_config,language="tr",locale_failure=True,kernel=work/"source.jar"):
            raise ValueError("source/release locale initialization failures differ")
        result={"schema":"axiom.material-catalog-conformance.v1","status":"passed",
                "engineManifestSha256":sha256(engine_manifest).hexdigest(),"engineJars":manifest["jars"],
                "programSha256":sha256(program_files["native-materials.jar"]).hexdigest(),
                "programManifestSha256":sha256(program_files["program.json"]).hexdigest(),
                "producerPrograms":{p.stem:sha256(p.read_bytes()).hexdigest() for p in (baseline,edited,default_jar)},
                "sourceInputs":{**material_evidence,**{r+":"+p:sha256(t.encode()).hexdigest() for (r,p),t in originals.items()}},
                "qualificationInputs":{p.relative_to(ROOT).as_posix():sha256(raw).hexdigest() for p,raw in frozen.items()},
                "images":policy["images"],"libraryInputs":policy["libraries"],"runtimeInputs":runtime["runtimeFiles"]+runtime["compilerFiles"],
                "baseline":accepted,"configurationRuns":configuration_runs,"failureRuns":failure_runs,
                "sourceEditWitness":"Aluminium ore washing reference: SodiumPersulfate -> Water; all other state unchanged",
                "localeWitness":{"language":"tr",**locale},
                "configDefaults":True,"configDefaultSourceEdit":True,"sourceEventTransformerIdentical":True,
                "invalidBooleanNativeFallback":True,"negativeConfigurationInputs":4,"callerConfigurationUnchanged":True,
                "sourceRebuildIdentical":True,"sourceExecutionIdentical":True,"ownerUniverse":"explicit-native-container-fixtures",
                "wholePackParity":False,"installedCompositionQualified":False,"generatedContent":False}
    if any(p.read_bytes()!=raw for p,raw in frozen.items()) or sources.verify_sources({r:roots[r] for r in ("gtceu","cleanroom","supersymmetry")})!=originals:
        raise ValueError("catalog qualification inputs changed")
    if engine_inputs(home,frozen[TARGET_LOCK])[0]!=engine_manifest or any((program/n).read_bytes()!=raw for n,raw in program_files.items()): raise ValueError("installed catalog program changed")
    for root,rows in ((images,policy["images"]),(libraries,policy["libraries"]),(java,runtime["runtimeFiles"]+runtime["compilerFiles"])):
        for row in rows: checked_path(root,row)
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open("x") as out: json.dump(result,out,indent=2); out.write("\n")
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("java-home","engine-home","images","library-root","program","gtceu","susy-core","cleanroom","supersymmetry","report"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args(argv)
    result=qualify(args.java_home,args.engine_home,args.images,args.library_root,args.program,
                   {r:getattr(args,r.replace("-","_")) for r in ("gtceu","susy-core","cleanroom","supersymmetry")},args.report)
    print(json.dumps({"status":result["status"],"materials":result["baseline"]["result"]["materialCount"],"phase":"FROZEN","wholePackParity":False}))
    return 0


if __name__=="__main__": raise SystemExit(main())
