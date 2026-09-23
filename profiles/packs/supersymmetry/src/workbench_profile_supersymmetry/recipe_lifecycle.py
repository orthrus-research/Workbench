"""Exact current-stack attachment policy and conservative lifecycle explanations."""
from base64 import urlsafe_b64encode
from hashlib import sha256
from pathlib import Path
import json
import re
import zipfile

from workbench_project_intelligence.saved_candidate import candidate_manifest
from workbench_pack_program_studio.source_locations import source_location

TARGETS = {
    "gregtech/api/recipes/RecipeMap": "ce0d34e2ccb8898a8cb01cba196c44988cfbdc6ffa8e690b084e4af12243468d",
    "gregtech/api/recipes/RecipeBuilder": "ad2e2e107090f71a48b1ab1586739d565f33d7d7bde8784625ac109c652fe755",
    "com/cleanroommc/groovyscript/sandbox/CustomGroovyScriptEngine": "59b368bd606ec9cfde5bfb6f3adc31b4b3bd4950a43977b79afa4b77f6a0cca5",
    "org/apache/groovy/parser/antlr4/AstBuilder": "62dcd22f0b809deef202ab71bf0153e8f81284e706e1be510255472bdaaafd7b",
}
ARTIFACTS = {
    "gregtech-1.12.2-2.8.10-beta.jar": "54744bb11ea4679df4b55846d8073e9bb2ef8c1c53aae8aee41cda3fc22d927e",
    "groovyscript-1.4.3.jar": "07617b7ce9170a857199bd61d730a0db3af2685cf83d31734a2d4b628fda7533",
}
DEFINITIONS = {
    "gregtech/api/recipes/RecipeMap": "c289027fb61f976730f01102f77d3a566fba96c157c84b4d74ffa62a68519c49",
    "gregtech/api/recipes/RecipeBuilder": "08c9cb72f76e533fb7bea2c305fe048f4c2132020c088d085276220940ede49e",
    "com/cleanroommc/groovyscript/sandbox/CustomGroovyScriptEngine": TARGETS["com/cleanroommc/groovyscript/sandbox/CustomGroovyScriptEngine"],
    "org/apache/groovy/parser/antlr4/AstBuilder": TARGETS["org/apache/groovy/parser/antlr4/AstBuilder"],
}
HOOKS = {
    "gregtech/api/recipes/RecipeMap": {"addRecipe", "postValidateRecipe", "compileRecipe", "removeRecipe", "removeAllRecipes"},
    "gregtech/api/recipes/RecipeBuilder": {"buildAndRegister", "build", "validate"},
    "com/cleanroommc/groovyscript/sandbox/CustomGroovyScriptEngine": {"onCompileClass"},
    "org/apache/groovy/parser/antlr4/AstBuilder": {"createCharStream"},
}
OPERATIONS = {"registration", "build", "validation", "add", "post-validation", "insertion", "removal", "clear"}


def attachment(inputs, nonce, *, expectation):
    if expectation is None or expectation["support"] != "supported":
        return None
    sources = {name: raw for name, raw in inputs.files if name.startswith("groovy/") and name.endswith(".groovy")}
    if len(sources) > 8192 or any(len(name) > 512 or len(raw) > 1024**2 for name, raw in sources.items()):
        raise ValueError("lifecycle source binding exceeds bounds; use --no-trace for a snapshot-only check")
    configuration = {"nonce": nonce, "candidate": candidate_manifest(inputs)["id"]}
    configuration.update({"target." + name: digest for name, digest in DEFINITIONS.items()})
    configuration.update({"source." + urlsafe_b64encode(name.encode()).decode().rstrip("="): sha256(raw).hexdigest() for name, raw in sources.items()})
    directory = Path(__file__).with_name("observer")
    return {
        "sources": {name: (directory / name).read_bytes() for name in ("RecipeAgent.java", "RecipeTrace.java", "RecipeDecisionHooks.java", "RecipeDecisions.java")},
        "main_class": "dev.workbench.recipe.RecipeAgent",
        "bootstrap_classes": ["dev.workbench.recipe.RecipeTrace", "dev.workbench.recipe.RecipeDecisions"],
        "configuration": "".join(key + "=" + value + "\n" for key, value in sorted(configuration.items())).encode(),
        "policy": {"id": "supersymmetry-mixer-registration", "version": 2,
                   "mechanism": "java-instrumentation-classfile-25", "platform": "cleanroom-0.6.12-alpha",
                   "retransform": False, "external_dependencies": [], "targets": TARGETS,
                   "definition_targets": DEFINITIONS, "definition_normalization": "mixin-accessor-session-uuid-only-v1",
                   "bridge": "reserved-recipe-map-observation-methods-v2",
                   "decisions": "exact-branch-sites-v1", "max_decision_steps": 40000, "max_query_steps": 4096,
                   "artifacts": ARTIFACTS, "max_events": 20000, "max_recipes": 4096},
    }


def validate_image(runtime):
    for name, digest in ARTIFACTS.items():
        path = runtime / "mods" / name
        if path.is_symlink() or not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("lifecycle observer requires exact current artifacts; use --no-trace for snapshot-only checks: " + name)
        with zipfile.ZipFile(path) as archive:
            for target, expected in TARGETS.items():
                if target + ".class" in archive.namelist() and sha256(archive.read(target + ".class")).hexdigest() != expected:
                    raise ValueError("lifecycle target bytes differ: " + target)


def interpret(inputs, expectation, capture):
    """Lifecycle eligibility is separate from the valid final-state assertion."""
    empty = {"state": "unavailable", "reasons": [], "events": [], "links": {}}
    if capture is None:
        return {**empty, "reasons": ["No lifecycle observer was attached or its capture was unavailable."]}
    try:
        if (not isinstance(capture, dict) or set(capture) != {"format", "nonce", "candidate_id", "state", "problems", "hooks", "events", "links", "total_events", "source_bindings", "scope"}
                or capture["format"] != "workbench-recipe-lifecycle-v1" or capture["candidate_id"] != expectation["candidate_id"]
                or len(json.dumps(capture, allow_nan=False)) > 1024**2
                or type(capture["total_events"]) is not int or not 0 <= capture["total_events"] <= 20000
                or type(capture["source_bindings"]) is not int or not 0 <= capture["source_bindings"] <= 8192
                or not isinstance(capture["problems"], list) or len(capture["problems"]) > 33
                or not isinstance(capture["scope"], str) or len(capture["scope"]) > 512
                or not isinstance(capture["links"], dict)
                or not isinstance(capture["events"], list) or len(capture["events"]) > capture["total_events"]):
            raise ValueError("invalid lifecycle envelope")
        if capture["state"] != "complete" or capture["problems"]:
            return {**empty, "state": "incomplete", "reasons": ["Lifecycle coverage incomplete.", *[str(reason)[:512] for reason in capture["problems"][:32]]]}
        if set(capture["hooks"]) != set(TARGETS):
            raise ValueError("missing required hook definitions")
        for name, hook in capture["hooks"].items():
            if (hook["definition_sha256"] != DEFINITIONS[name] or re.fullmatch(r"[0-9a-f]{64}", hook["input_sha256"]) is None or set(hook["methods"]) != HOOKS[name]
                    or re.fullmatch(r"[0-9a-f]{64}", hook["output_sha256"]) is None):
                raise ValueError("hook identities differ from the admitted observer")
        ids, finished = {}, set()
        last_id = -1
        rows = []
        for row in capture["events"]:
            if (set(row) != {"id", "operation", "parent", "thread", "source", "outcome", "recipe_id", "properties", "validation", "returned", "finished"}
                    or row["operation"] not in OPERATIONS or re.fullmatch(r"e[0-9]{1,5}", row["id"]) is None or row["id"] in ids
                    or not last_id < int(row["id"][1:]) < capture["total_events"]
                    or row["outcome"] not in {"returned", "threw"} or type(row["thread"]) is not int or row["thread"] < 1
                    or type(row["finished"]) is not int or not 1 <= row["finished"] <= capture["total_events"] or row["finished"] in finished
                    or row["validation"] not in {None, "VALID", "INVALID", "SKIP"}
                    or row["returned"] is not None and type(row["returned"]) not in (bool, str)
                    or isinstance(row["returned"], str) and len(row["returned"]) > 512
                    or row["properties"] is not None and (not isinstance(row["properties"], dict) or len(json.dumps(row["properties"])) > 128 * 1024)
                    or row["recipe_id"] is not None and re.fullmatch(r"o[0-9]{1,4}", row["recipe_id"]) is None):
                raise ValueError("invalid lifecycle event")
            parent = row["parent"]
            if parent is not None and (parent not in ids or ids[parent]["thread"] != row["thread"] or ids[parent]["finished"] <= row["finished"]):
                raise ValueError("lifecycle nesting or thread binding differs")
            ids[row["id"]] = row; finished.add(row["finished"])
            last_id = int(row["id"][1:])
            source = row["source"]
            location = None
            if source is not None:
                if (set(source) != {"path", "sha256", "line", "class_name", "compiled_sha256"}
                        or not isinstance(source["class_name"], str) or not 1 <= len(source["class_name"]) <= 512):
                    raise ValueError("invalid compiled source binding")
                raw = inputs.sources[source["path"]]
                if sha256(raw).hexdigest() != source["sha256"] or re.fullmatch(r"[0-9a-f]{64}", source["compiled_sha256"]) is None:
                    raise ValueError("compiler source differs from the saved candidate")
                lines = raw.splitlines(keepends=True)
                line = source["line"]
                if type(line) is not int or not 1 <= line <= len(lines):
                    raise ValueError("executed source line is outside the saved candidate")
                start = sum(map(len, lines[:line - 1]))
                location = source_location(raw, source["path"], start, start + len(lines[line - 1].rstrip(b"\r\n")))
            rows.append({**row, "location": location})
        linked_recipes = {row["recipe_id"] for row in rows if row["recipe_id"] is not None}
        if any(re.fullmatch(r"r[0-9]{1,4}", key) is None or value not in linked_recipes for key, value in capture["links"].items()):
            raise ValueError("final snapshot links lack observed recipe identities")
        return {"state": "complete", "reasons": [], "events": rows, "links": capture["links"]}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {**empty, "state": "incomplete", "reasons": ["Lifecycle evidence cannot be interpreted: " + str(exc)[:1000]]}


def sections(inputs, expectation, observation):
    from .recipe_explanations import _section
    capture = observation["details"].get("capture", {})
    lifecycle = capture.get("lifecycle")
    value = interpret(inputs, expectation, lifecycle)
    if isinstance(lifecycle, dict) and lifecycle.get("nonce") != capture.get("nonce"):
        value = {"state": "incomplete", "reasons": ["Lifecycle nonce differs from the final snapshot."], "events": [], "links": {}}
    if value["state"] == "complete" and not set(value["links"]).issubset({row["id"] for row in capture.get("records", [])}):
        value = {"state": "incomplete", "reasons": ["Lifecycle references are absent from the final snapshot."], "events": [], "links": {}}
    observation["details"]["lifecycle"] = value
    rows = [_section("lifecycle", "Registration lifecycle: " + value["state"], notes=[
        *value["reasons"], "Observed operations are not branch coverage. Missing events do not establish non-execution.",
        "Source links identify compiler-bound executed lines, not necessarily a unique declaration or the reason a recipe was selected.",
    ], evidence=observation["evidence"])]
    if value["state"] == "complete":
        attributed = sum(row["source"] is not None for row in value["events"])
        rows[0]["notes"].append(f"{len(value['events'])} selected operations; {attributed} have verified executed source lines.")
        failures = sorted((row for row in value["events"] if row["outcome"] == "threw" or row["returned"] is False
                           or row["validation"] == "INVALID" or row["returned"] == "INVALID"), key=lambda row: row["finished"])
        if failures:
            row = failures[0]
            rows[0]["notes"].append(f"First observed unsuccessful stage: {row['id']} {row['operation']}. This identifies an observed outcome, not its root cause.")
    for row in value["events"][:64]:
        links = sorted(key for key, target in value["links"].items() if target == row["recipe_id"])
        notes = ["Operation: " + row["operation"], "Outcome: " + row["outcome"] + "; returned " + str(row["returned"]),
                 "Parent operation: " + str(row["parent"]), "Validation status: " + str(row["validation"]),
                 "Recipe occurrence: " + str(row["recipe_id"]) + "; final snapshot references: " + (", ".join(links) or "none")]
        if row["properties"] is not None:
            notes.append("Operation-time properties: " + json.dumps(row["properties"], sort_keys=True))
        if row["operation"] == "insertion" and row["returned"] is False:
            notes.append("Lookup insertion returned false; an internal rejection reason requires separate decision evidence.")
        if row["source"] is None:
            notes.append("Executed source origin unavailable; no static match is substituted.")
        rows.append(_section("lifecycle-" + row["id"], row["id"] + " · " + row["operation"], notes=notes, evidence=observation["evidence"],
            sources=[] if row["location"] is None else [dict(label="Executed source line", basis="executed-source-line",
                candidate_id=expectation["candidate_id"], location=row["location"])]))
    if len(value["events"]) > 64:
        rows[0]["notes"].append("Lifecycle display limited to 64 events; all selected events remain in the retained capture.")
    return rows
