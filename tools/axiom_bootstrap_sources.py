"""Retain GT material lifecycle ordering and its three event declarations."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/"modules/axiom/sources/material-lifecycle.lock.json"
OUTPUT=ROOT/"modules/axiom/jvm/src/main/java/research/orthrus/axiom"
CORE="src/main/java/gregtech/core/CoreModule.java"
EVENTS="src/main/java/gregtech/api/unification/material/event/"
NOTICE="// Extracted pinned GTCEu source, LGPL-3.0; see spec/native-events.md.\n"


def extract(path, source):
    if path.startswith(EVENTS):
        return NOTICE+source.replace("gregtech.api.unification.material.event", "research.orthrus.axiom.materialevents")\
            .replace("import gregtech.api.unification.material.registry.IMaterialRegistryManager;", "")\
            .replace("gregtech.api.unification.material.registry.MaterialRegistry", "research.orthrus.axiom.MaterialRegistry")\
            .replace("gregtech.api.unification.material.Material", "research.orthrus.axiom.MaterialState")\
            .replace("GenericEvent<Material>", "GenericEvent<MaterialState>").replace("super(Material.class)", "super(MaterialState.class)")\
            .replace("@see IMaterialRegistryManager#createRegistry(String)", "Registry creation is supplied by the lifecycle owner.")\
            .replace("net.minecraftforge.fml.common.eventhandler", "research.orthrus.axiom.nativeevents")
    if path!=CORE:raise ValueError("Unadmitted material lifecycle source")
    begin="        /* Start Material Registration */";end="        /* End Material Registration */"
    if source.count(begin)!=1 or source.count(end)!=1:raise ValueError("Material lifecycle boundary differs")
    body=source[source.index(begin)+len(begin):source.index(end)]
    replacements={
        "GregTechAPI.markerMaterialRegistry = MarkerMaterialRegistry.getInstance();":"dependencies.initializeMarkers();",
        "(MaterialRegistryManager) GregTechAPI.materialManager":"runtime.materials()",
        "MaterialRegistryManager.getInstance()":"runtime.materials()",
        "GTValues.MODID":'"gregtech"',
        "Materials.register();":"dependencies.registerMaterials();",
        "Materials.Aluminium":"dependencies.aluminium()",
        "MinecraftForge.EVENT_BUS.post":"events.post",
        "MaterialEvent materialEvent = new MaterialEvent();":'Object materialEvent = events.construct("research.orthrus.axiom.materialevents.MaterialEvent");',
        "new MaterialRegistryEvent()":'events.construct("research.orthrus.axiom.materialevents.MaterialRegistryEvent")',
        "new PostMaterialEvent()":'events.construct("research.orthrus.axiom.materialevents.PostMaterialEvent")',
    }
    for old,new in replacements.items():
        if old not in body:raise ValueError("Missing lifecycle substitution: "+old)
        body=body.replace(old,new)
    return NOTICE+'''package research.orthrus.axiom;

/** Material-registration block only. Dependency ports are mandatory; this does
 * not imply that any GT, addon, pack or marker producer has been supplied. */
final class MaterialLifecycle {
    interface Dependencies {
        void initializeMarkers();
        void registerMaterials();
        MaterialState aluminium();
    }
    private static final org.apache.logging.log4j.Logger logger = org.apache.logging.log4j.LogManager.getLogger("axiom.material-lifecycle");
    private final RegistryRuntime runtime;
    private final MaterialEvents events;
    private final Dependencies dependencies;
    private boolean started;
    MaterialLifecycle(RegistryRuntime runtime, MaterialEvents events, Dependencies dependencies) {
        this.runtime=java.util.Objects.requireNonNull(runtime);
        this.events=java.util.Objects.requireNonNull(events);
        this.dependencies=java.util.Objects.requireNonNull(dependencies);
    }
    void execute() {
        if (started || runtime.materials().getPhase()!=MaterialPhase.PRE)
            throw new IllegalStateException("Material lifecycle requires one fresh registry universe");
        started=true;
        registerMaterials();
    }
    private void registerMaterials() {
'''+body+"    }\n}\n"


def sources(repository):
    lock=json.loads(LOCK.read_text());result={}
    for row in lock["references"]:
        raw=subprocess.check_output(["git","-C",str(repository),"show",lock["revision"]+":"+row["path"]],timeout=30)
        if hashlib.sha256(raw).hexdigest()!=row["sha256"]:raise ValueError("Lifecycle source hash differs")
        name="MaterialLifecycle.java" if row["path"]==CORE else "materialevents/"+Path(row["path"]).name
        result[name]=extract(row["path"],raw.decode())
    return result


def check(repository):
    for name,text in sources(repository).items():
        if (OUTPUT/name).read_text()!=text:raise ValueError("Retained lifecycle source differs: "+name)


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--gtceu",required=True,type=Path)
    check(parser.parse_args().gtceu)
    print("Selected GT material lifecycle source retention verified")
