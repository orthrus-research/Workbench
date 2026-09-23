"""Interpret actual RecipeMap branch execution; never reproduce its matching algorithm."""
import json
import re

RECIPE = "Lgregtech/api/recipes/Recipe;"
LIST = "Ljava/util/List;"
BRANCH = "Lgregtech/api/recipes/map/Branch;"
PREDICATE = "Ljava/util/function/Predicate;"
LOOKUP = f"findRecipe(J{LIST}{LIST}Z){RECIPE}"
SITES = {
    f"compileRecipe({RECIPE})Z": {
        1: ("recipe-present", "null-recipe"), 24: ("insertion-failed", "insertion-succeeded")},
    f"recurseIngredientTreeAdd({RECIPE}{LIST}{BRANCH}II)Z": {
        130: ("insertion-subtree", "insertion-leaf"),
        142: ("different-recipe-blocks-insertion", "same-recipe-leaf"),
        177: ("child-insertion-succeeded", "child-insertion-failed"),
        190: ("inspect-failed-child", "remove-failed-terminal"),
        251: ("retain-nonempty-child", "remove-empty-child")},
    f"lambda$recurseIngredientTreeAdd$9(I{LIST}{RECIPE}{BRANCH}Lgregtech/api/recipes/map/AbstractMapIngredient;Lgregtech/api/recipes/map/Either;)Lgregtech/api/recipes/map/Either;": {
        14: ("terminal-slot-empty", "terminal-slot-occupied"),
        25: ("terminal-occupied-subtree", "terminal-occupied-recipe"),
        37: ("terminal-same-recipe", "terminal-different-recipe")},
    f"recurseIngredientTreeFindRecipe({LIST}{BRANCH}{PREDICATE}IIJ){RECIPE}": {
        8: ("lookup-depth-continues", "lookup-depth-exhausted"),
        38: ("lookup-alternatives-exhausted", "lookup-next-alternative"),
        77: ("lookup-key-missing", "lookup-key-present"),
        112: ("lookup-branch-no-selection", "lookup-branch-selected")},
    f"lambda$recurseIngredientTreeFindRecipe$5({PREDICATE}{RECIPE}){RECIPE}": {
        7: ("candidate-predicate-failed", "candidate-predicate-passed")},
    f"lambda$findRecipe$4(ZJ{LIST}{LIST}{RECIPE})Z": {
        12: ("exact-voltage-passed", "exact-voltage-failed"),
        25: ("voltage-limit-passed", "voltage-limit-failed")},
}
METHODS = set(SITES) | {LOOKUP}
SITE_OUTCOMES = {f"{method}:{offset}": values for method, sites in SITES.items() for offset, values in sites.items()}
BLOCKING = {"different-recipe-blocks-insertion", "null-recipe"}


def _reference(value, prefix, bound):
    return isinstance(value, str) and re.fullmatch(prefix + r"[0-9]{1,5}", value) is not None and int(value[1:]) < bound


def interpret(expectation, capture, lifecycle):
    """Decision eligibility is independent of snapshot assertion eligibility."""
    empty = {"state": "unavailable", "reasons": [], "steps": [], "queries": [], "links": {}}
    raw = capture.get("decisions") if isinstance(capture, dict) else None
    if raw is None:
        return {**empty, "reasons": ["No runtime decision evidence was captured."]}
    try:
        if (not isinstance(raw, dict) or set(raw) != {"format", "nonce", "candidate_id", "state", "problems", "methods", "steps", "queries", "links", "total_steps"}
                or raw["format"] != "workbench-recipe-decisions-v1" or len(json.dumps(raw, allow_nan=False)) > 1024**2
                or raw["nonce"] != capture["nonce"] or raw["candidate_id"] != expectation["candidate_id"]
                or type(raw["total_steps"]) is not int or not 0 <= raw["total_steps"] <= 40000
                or not isinstance(raw["problems"], list) or len(raw["problems"]) > 32
                or not all(isinstance(value, str) and len(value) <= 512 for value in raw["problems"])
                or not isinstance(raw["steps"], list) or len(raw["steps"]) > raw["total_steps"]
                or not isinstance(raw["queries"], list) or not 1 <= len(raw["queries"]) <= 64
                or not isinstance(raw["links"], dict)):
            raise ValueError("invalid decision envelope")
        if raw["state"] != "complete" or raw["problems"]:
            return {**empty, "state": "incomplete", "reasons": ["Decision observation incomplete.", *raw["problems"]]}
        if lifecycle["state"] != "complete" or set(raw["methods"]) != METHODS or len(raw["methods"]) != len(METHODS):
            raise ValueError("missing admitted hooks or lifecycle custody")
        operations = {row["id"]: row for row in lifecycle["events"]}
        queries = {row["id"]: row for row in capture["queries"]}
        links = raw["links"]
        if (set(links) != {row["id"] for row in capture["records"]}
                or any(not _reference(value, "o", 4096) for value in links.values())
                or len(set(links.values())) != len(links)
                or any(links.get(key) != value for key, value in lifecycle["links"].items())):
            raise ValueError("decision identities differ from the final snapshot")
        by_query = {}; previous_end = 0
        for row in raw["queries"]:
            if (not isinstance(row, dict) or set(row) != {"id", "calls", "thread", "arguments", "outcome", "selected", "exception", "step_start", "step_end"}
                    or row["id"] in by_query or row["id"] not in queries or type(row["calls"]) is not int or row["calls"] != 1
                    or type(row["thread"]) is not int or row["thread"] < 1
                    or row["outcome"] != "returned" or row["exception"] is not None
                    or type(row["step_start"]) is not int or type(row["step_end"]) is not int
                    or not previous_end <= row["step_start"] <= row["step_end"] <= raw["total_steps"]
                    or row["step_end"] - row["step_start"] > 4096):
                raise ValueError("query was not one complete original invocation")
            query, args = queries[row["id"]], row["arguments"]
            if (not isinstance(args, dict) or set(args) != {"map", "items", "fluids", "voltage_limit", "exact_voltage", "item_slots", "fluid_slots"}
                    or args["map"] != "mixer" or args["exact_voltage"] is not False
                    or type(args["voltage_limit"]) is not int or args["voltage_limit"] != query["voltage_limit"]
                    or args["items"] != query["items"] or args["fluids"] != query["fluids"]
                    or type(args["item_slots"]) is not int or not len(args["items"]) <= args["item_slots"] <= 64
                    or type(args["fluid_slots"]) is not int or not len(args["fluids"]) <= args["fluid_slots"] <= 64
                    or row["selected"] != links.get(query["winner_id"])):
                raise ValueError("actual lookup arguments or selection contradict the final capture")
            by_query[row["id"]] = row; previous_end = row["step_end"]
        if set(by_query) != set(queries):
            raise ValueError("missing observed query")
        previous = -1
        for row in raw["steps"]:
            if (not isinstance(row, dict) or set(row) != {"id", "site", "outcome", "operation", "query", "thread", "recipe", "competing", "key", "depth", "branch"}
                    or not _reference(row["id"], "d", raw["total_steps"]) or not previous < int(row["id"][1:])
                    or row["site"] not in SITE_OUTCOMES or row["outcome"] not in SITE_OUTCOMES[row["site"]]
                    or type(row["depth"]) is not int or not -1 <= row["depth"] <= 128
                    or row["branch"] is not None and not _reference(row["branch"], "b", 8192)
                    or any(row[key] is not None and not _reference(row[key], "o", 4096) for key in ("recipe", "competing"))
                    or row["key"] is not None and (not isinstance(row["key"], dict) or len(json.dumps(row["key"])) > 4096)
                    or type(row["thread"]) is not int or row["thread"] < 1):
                raise ValueError("invalid decision step or unaudited branch outcome")
            previous = int(row["id"][1:])
            if row["outcome"] in {"candidate-predicate-passed", "candidate-predicate-failed", "lookup-branch-selected",
                                  "exact-voltage-passed", "exact-voltage-failed", "voltage-limit-passed", "voltage-limit-failed"} and row["recipe"] is None:
                raise ValueError("candidate decision lacks its observed recipe identity")
            if row["query"] is not None:
                query = by_query[row["query"]]
                if (row["operation"] is not None or row["thread"] != query["thread"]
                        or not query["step_start"] <= previous < query["step_end"]
                        or "IngredientTreeAdd" in row["site"] or row["site"].startswith("compileRecipe")):
                    raise ValueError("decision is outside its original query")
            else:
                operation = operations[row["operation"]]
                if (operation["operation"] != "insertion" or operation["thread"] != row["thread"]
                        or operation["recipe_id"] != row["recipe"]
                        or not ("IngredientTreeAdd" in row["site"] or row["site"].startswith("compileRecipe"))):
                    raise ValueError("decision differs from its registration operation")
            if row["outcome"] in {"different-recipe-blocks-insertion", "terminal-different-recipe"}:
                if row["recipe"] is None or row["competing"] is None or row["recipe"] == row["competing"]:
                    raise ValueError("conflict lacks two distinct observed recipe identities")
        for query in raw["queries"]:
            selected_steps = [row for row in raw["steps"] if row["query"] == query["id"]]
            if len(selected_steps) != query["step_end"] - query["step_start"]:
                raise ValueError("query decision path has missing steps")
            passed = [row["recipe"] for row in selected_steps if row["outcome"] == "candidate-predicate-passed"]
            if passed != ([] if query["selected"] is None else [query["selected"]]):
                raise ValueError("returned recipe lacks its directly observed predicate decision")
        return {"state": "complete", "reasons": [], **{key: raw[key] for key in ("steps", "queries", "links")}}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        return {**empty, "state": "incomplete", "reasons": ["Decision evidence cannot be interpreted: " + str(exc)[:1000]]}


def sections(expectation, observation):
    from .recipe_explanations import _section
    detail = observation["details"]
    lifecycle = detail["lifecycle"]
    value = interpret(expectation, detail.get("capture"), lifecycle)
    detail["decisions"] = value
    rows = [_section("decisions", "GTCEu runtime decisions: " + value["state"], notes=[
        *value["reasons"], "Original GTCEu lookup execution, not a simulated selection algorithm.",
        "Selection under captured inputs and voltage is not full recipe validity or successful machine execution.",
        "Only tagged probe queries are traced; independent acceptance scans are not lookup decisions.",
    ], evidence=observation["evidence"])]
    if value["state"] != "complete":
        return rows
    def sources(ids):
        result, seen = [], set()
        for event in lifecycle["events"]:
            location = event["location"]
            if event["recipe_id"] in ids and location is not None:
                key = (event["recipe_id"], location["path"], location["start"]["line"])
                if key not in seen and len(result) < 16:
                    seen.add(key)
                    result.append(dict(label="Executed source for " + event["recipe_id"], basis="executed-source-line",
                                       candidate_id=expectation["candidate_id"], location=location))
        return result
    failures = [event for event in lifecycle["events"] if event["operation"] == "insertion" and event["returned"] is False]
    for event in failures[:32]:
        steps = [row for row in value["steps"] if row["operation"] == event["id"]]
        blocking = next((row for row in steps if row["outcome"] in BLOCKING), None)
        related = {event["recipe_id"]}
        notes = ["Registration operation: " + event["id"], "GTCEu insertion returned false."]
        if blocking:
            related.add(blocking["competing"])
            notes += ["First observed blocking decision: " + blocking["outcome"],
                      "Evidence step: " + blocking["id"] + " · " + blocking["site"],
                      "Attempted recipe: " + str(blocking["recipe"]) + "; competing recipe: " + str(blocking["competing"]),
                      "Observed ingredient key: " + json.dumps(blocking["key"], sort_keys=True)]
        else:
            notes.append("No directly attributable blocking branch was retained; no collision reason is inferred.")
        notes.append("A failed insertion does not establish atomic rollback or unchanged tree membership.")
        rows.append(_section("decision-" + event["id"], "Registration not inserted: " + str(event["recipe_id"]),
                             notes=notes, sources=sources(related - {None}), evidence=observation["evidence"]))
    occupied = [row for row in value["steps"] if row["outcome"] == "terminal-occupied-subtree"]
    operations = {row["id"]: row for row in lifecycle["events"]}
    for step in occupied[:32]:
        event = operations[step["operation"]]
        rows.append(_section("decision-" + step["id"], "Registration encountered an occupied subtree", notes=[
            "Observed step: " + step["id"] + " · " + step["site"],
            "Recipe: " + str(step["recipe"]) + "; insertion outcome: " + event["outcome"] + "; returned: " + str(event["returned"]),
            "Observed ingredient key: " + json.dumps(step["key"], sort_keys=True),
            "The occupied node is a subtree; no unique conflicting recipe is asserted.",
            "This branch alone does not establish rejection. Insertion's returned value and final lookup/category membership are separate evidence.",
        ], sources=sources({step["recipe"]}), evidence=observation["evidence"]))
    capture_queries = {row["id"]: row for row in detail["capture"]["queries"]}
    for query in value["queries"]:
        steps = [row for row in value["steps"] if row["query"] == query["id"]]
        evaluated = {row["recipe"] for row in steps if row["outcome"].startswith("candidate-predicate-")}
        accepting = {value["links"][key] for key in capture_queries[query["id"]]["accepting"]}
        notes = ["Original findRecipe invocations: 1; outcome: returned.",
                 "Actual query arguments: " + json.dumps(query["arguments"], sort_keys=True),
                 "Selected recipe: " + str(query["selected"]),
                 "Predicate-evaluated recipes: " + (", ".join(sorted(evaluated)) or "none"),
                 "Accepting recipes not evaluated by this lookup: " + (", ".join(sorted(accepting - evaluated)) or "none"),
                 "Selection is the first accepted candidate reached by this traversal, not a best-recipe ranking."]
        notes += [row["id"] + " · " + row["outcome"] + " · recipe=" + str(row["recipe"])
                  + " · depth=" + str(row["depth"]) + " · key=" + json.dumps(row["key"], sort_keys=True) for row in steps[:32]]
        if len(steps) > 32:
            notes.append(f"Showing 32 of {len(steps)} steps; the complete bounded path remains in retained evidence.")
        if any(row["key"] and row["key"].get("unavailable") for row in steps):
            notes.append("Some visited key details are unavailable; branch outcomes do not establish unsupported ingredient semantics.")
        rows.append(_section("decision-" + query["id"], "Actual GTCEu lookup " + query["id"], notes=notes,
                             sources=sources(evaluated | accepting), evidence=observation["evidence"]))
    return rows
