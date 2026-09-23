"""Saved-source MIXER expectations, independent of Blueprint construction.

This deliberately supports a lexical subset, not arbitrary Groovy evaluation.
Runtime captures are independent recipe values plus bounded input lookups.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

from workbench_crucible.check_assertions import seal, validate_expectation
from workbench_pack_program_studio.lexer import Argument, calls, linked_calls, literal_string, tokenize
from workbench_pack_program_studio.source_locations import character_location
from workbench_project_intelligence.saved_candidate import candidate_manifest

PREFIX = "[WORKBENCH-RECIPE-CAPTURE]"
VOLTAGES = dict(zip(("ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV", "UEV", "UIV"),
                    (7, 30, 120, 480, 1920, 7680, 30720, 122880, 491520, 1966080, 7864320, 31457280)))
MAX_RECIPES = 4096
MAX_QUERIES = 256
MAX_ACCEPTANCES = 16384


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _integer(argument):
    number = argument.integer()
    if number is None or not 1 <= number <= 2**31 - 1:
        raise ValueError("Expected one positive bounded integer literal")
    return number


def _ingredient(argument, *, fluid=False, output=False):
    tokens = list(argument.tokens)
    if len(tokens) < 4 or tokens[0].kind != "identifier" or tokens[1].value != "(" or tokens[2].kind != "string":
        raise ValueError("Ingredient must be one literal ore, metaitem, item or fluid identity")
    kind, name = tokens[0].value, literal_string(tokens[2])
    if name is None or not re.fullmatch(r"[A-Za-z0-9_:.\-/]{1,256}", name):
        raise ValueError("Dynamic or malformed ingredient identity")
    if kind not in ({"fluid"} if fluid else {"item", "metaitem"} if output else {"item", "metaitem", "ore"}):
        raise ValueError("Ingredient kind is outside this deterministic recipe subset")
    end, metadata = 3, 0
    if kind == "item" and len(tokens) > 5 and tokens[3].value == ",":
        metadata = Argument(tuple(tokens[4:5])).integer()
        end = 5
    if kind == "item" and re.fullmatch(r"[^:]+:[^:]+:\d+", name):
        name, suffix = name.rsplit(":", 1)
        metadata = int(suffix)
    if tokens[end].value != ")" or metadata is None or not 0 <= metadata < 32767:
        raise ValueError("Only literal item metadata without NBT is supported")
    rest = tokens[end + 1:]
    amount = 1 if not rest else _integer(Argument(tuple(rest[1:]))) if rest[0].value == "*" else None
    if amount is None:
        raise ValueError("Only a literal ingredient amount is supported")
    if kind == "item" and not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]+", name):
        raise ValueError("Item must have an exact registry name")
    return {"kind": kind, "name": name, "metadata": metadata, "amount": amount}


def _spec(chain):
    if chain[0].qualifier not in {"MIXER", "Recipemaps.MIXER"}:
        raise ValueError("Only the ordinary MIXER recipe map is supported")
    if chain[0].arguments or chain[-1].terminal != "buildAndRegister" or chain[-1].arguments:
        raise ValueError("A complete, ordinary recipeBuilder/buildAndRegister chain is required")
    value = {"map": "mixer", "item_inputs": [], "fluid_inputs": [], "item_outputs": [], "fluid_outputs": []}
    groups = {"inputs": "item_inputs", "fluidInputs": "fluid_inputs", "outputs": "item_outputs", "fluidOutputs": "fluid_outputs"}
    for call in chain[1:-1]:
        if call.terminal in groups and call.arguments:
            key = groups[call.terminal]
            value[key].extend(_ingredient(arg, fluid=key.startswith("fluid"), output=key.endswith("outputs")) for arg in call.arguments)
        elif call.terminal in {"duration", "EUt"} and len(call.arguments) == 1:
            key = "duration" if call.terminal == "duration" else "eut"
            if key in value:
                raise ValueError("Repeated duration or EU/t setters are not inferred")
            expression = "".join(token.value for token in call.arguments[0].tokens)
            tier = re.fullmatch(r"VA\[([A-Za-z]+)\]", expression)
            value[key] = VOLTAGES[tier[1]] if key == "eut" and tier and tier[1] in VOLTAGES else _integer(call.arguments[0])
        else:
            raise ValueError("Unsupported builder call: " + call.terminal)
    if "duration" not in value or "eut" not in value or not (value["item_inputs"] or value["fluid_inputs"]):
        raise ValueError("Explicit duration, EU/t and inputs are required")
    if not (value["item_outputs"] or value["fluid_outputs"]) or any(len(value[key]) > 16 for key in groups.values()):
        raise ValueError("Recipe inputs and outputs exceed the supported bounds")
    for key in ("item_inputs", "fluid_inputs"):
        identities = [canonical({field: row[field] for field in ("kind", "name", "metadata")}) for row in value[key]]
        if len(set(identities)) != len(identities):
            raise ValueError("Repeated input identities require explicit aggregation semantics")
    return value


def source_recipes(name, raw):
    """One bounded source parser shared by selection and diagnostic attribution."""
    if len(raw) > 1024 * 1024:
        raise ValueError("Recipe source file exceeds 1 MiB; narrow the source before checking")
    tokens = tokenize(raw.decode("utf-8"))
    found = calls(tokens)
    by_token = {call.token_start: call for call in found}
    control = any(token.value in {"if", "for", "while", "switch", "try", "catch", "class", "def", "return", "throw", "{", "}", "@", "="} for token in tokens)
    for call in found:
        if call.terminal != "recipeBuilder":
            continue
        chain = linked_calls(tokens, by_token, call)
        reasons, spec = [], None
        try:
            if control:
                raise ValueError("Control flow, declarations or closures in this file require an explicit future interpreter; no host evaluation")
            spec = _spec(chain)
        except ValueError as exc:
            reasons.append(str(exc))
        yield seal("saved-recipe", {"location": character_location(raw, name, call.start, chain[-1].end), "map": call.qualifier,
                                   "support": "unsupported" if reasons else "supported", "reasons": reasons, "recipe": spec})


def catalog(inputs, path=None, *, identity=None):
    candidate = candidate_manifest(inputs)
    rows = []
    for name, raw in inputs.files:
        if not name.startswith("groovy/postInit/") or not name.endswith(".groovy") or path is not None and name != path:
            continue
        for sealed in source_recipes(name, raw):
            if identity is not None and sealed["id"] != identity:
                continue
            rows.append(sealed)
            if len(rows) > MAX_RECIPES:
                raise ValueError("Recipe catalog exceeds its bound; select an exact --path")
    return {"format": "workbench-saved-recipe-catalog-v1", "candidate_id": candidate["id"], "recipes": rows,
            "meaning": "Literal source candidates, not proof of execution. No arbitrary Groovy is evaluated."}


def expectation(inputs, source_inputs, recipe_id, *, mode="present", reference=None):
    matches = catalog(source_inputs, identity=recipe_id)["recipes"]
    if len(matches) != 1:
        raise ValueError("Selected recipe source changed or is unavailable; inspect recipes again")
    row = matches[0]
    if mode == "absent" and reference is None:
        raise ValueError("Absence requires an explicit retained source reference")
    recipe = row["recipe"]
    selector = None if recipe is None else {key: recipe[key] for key in ("map", "item_inputs", "fluid_inputs")}
    body = {"format": "workbench-check-expectation-v1", "candidate_id": candidate_manifest(inputs)["id"],
            "source": {"candidate_id": candidate_manifest(source_inputs)["id"], "reference": reference},
            "subject": {**row, "selector": selector}, "mode": mode,
            "support": row["support"], "reasons": row["reasons"],
            "expected": {"exact_registrations": 1 if mode == "present" else 0, "lookup_satisfies_expectation": True,
                         **({"competing_registrations": 0} if mode == "present" else {})}}
    return validate_expectation(seal("check-expectation", body))


def probe(expectation, nonce, *, trace=False):
    """The observer receives identifiers/input queries, never expected recipe outputs or timing."""
    if expectation is None or expectation["support"] != "supported":
        return None
    recipe = expectation["subject"]["recipe"]
    identities = [{key: row[key] for key in ("kind", "name", "metadata")} for key in ("item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs") for row in recipe[key]]
    spec = {"nonce": nonce, "expectation_id": expectation["id"], "identities": identities,
            "selector": expectation["subject"]["selector"], "max_queries": MAX_QUERIES, "max_recipes": MAX_RECIPES,
            "max_acceptances": MAX_ACCEPTANCES, "source_path": expectation["subject"]["location"]["path"]}
    template = Path(__file__).with_name("recipe_probe.groovy").read_text()
    return (template.replace("__SPEC_JSON__", json.dumps(canonical(spec)))
            .replace("__TRACE_RECIPES__", "RecipeMap.workbenchTraceRecipes()" if trace else "[]")
            .replace("__TRACE_QUERY_BEGIN__", "RecipeMap.workbenchTraceQuery(queryId as String)" if trace else "null")
            .replace("__TRACE_QUERY_END__", "RecipeMap.workbenchTraceQuery(null)" if trace else "null")
            .replace("__TRACE_DECISIONS__", "RecipeMap.workbenchTraceDecisions(references, result.lifecycle)" if trace else "null")
            .replace("__TRACE_SNAPSHOT__", "RecipeMap.workbenchTraceSnapshot(references, selectedTraceRecipes, spec.source_path as String)" if trace else "null")).encode()


def marker(logs, nonce, expectation):
    if expectation is None:
        return None
    lines = (logs.get("logs/latest.log", {}).get("text") or "").splitlines()
    loaded = [i for i, line in enumerate(lines, 1) if re.search(r"Forge Mod Loader has successfully loaded \d+ mods", line)]
    matches = []
    for line, text in enumerate(lines, 1):
        if PREFIX not in text:
            continue
        try:
            value = json.loads(text.split(PREFIX, 1)[1])
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("nonce") == nonce and value.get("expectation_id") == expectation["id"]:
            matches.append((value, {"log": "logs/latest.log", "line": line}))
    if len(matches) != 1 or not loaded or matches[0][1]["line"] <= max(loaded):
        return None
    return matches[0]


def _normalized(recipe, resolution):
    result = {key: recipe[key] for key in ("map", "duration", "eut")}
    for key in ("item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs"):
        rows = []
        for row in recipe[key]:
            identity = {field: row[field] for field in ("kind", "name", "metadata")}
            resolved = resolution[canonical(identity)]
            if key.startswith("fluid"):
                value = {"name": row["name"], "amount": row["amount"]}
            elif key == "item_inputs":
                value = {"ore": row["name"] if row["kind"] == "ore" else None, "stacks": [] if row["kind"] == "ore" else resolved,
                         "amount": row["amount"]}
            else:
                if len(resolved) != 1:
                    raise ValueError("Output does not resolve to one item")
                value = {**resolved[0], "amount": row["amount"]}
            rows.append(value)
        # Ingredient/output order has no semantic identity in this subset; preserve multiplicity.
        result[key] = sorted(rows, key=canonical)
    return result


def _observed(recipe):
    if recipe is None:
        return None
    if not isinstance(recipe, dict) or set(recipe) != {"map", "duration", "eut", "item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs"}:
        raise ValueError("Unexpected observed recipe contract")
    value = dict(recipe)
    if value["map"] != "mixer" or any(type(value[key]) is not int or not 1 <= value[key] <= 2**31 - 1 for key in ("duration", "eut")):
        raise ValueError("Observed recipe map, duration or EU/t is outside bounds")
    for key in ("item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs"):
        if not isinstance(value[key], list) or len(value[key]) > 64:
            raise ValueError("Observed recipe exceeds field bounds")
        rows = [{**row, "stacks": sorted(row["stacks"], key=canonical)} if key == "item_inputs" else row for row in value[key]]
        value[key] = sorted(rows, key=canonical)
        for row in rows:
            if not isinstance(row, dict) or type(row.get("amount")) is not int or not 1 <= row["amount"] <= 2**31 - 1:
                raise ValueError("Observed ingredient amount is outside bounds")
            if key.startswith("fluid"):
                if set(row) != {"name", "amount"} or not isinstance(row["name"], str) or re.fullmatch(r"[A-Za-z0-9_:.\-/]{1,256}", row["name"]) is None:
                    raise ValueError("Observed fluid identity is malformed")
            elif key == "item_inputs":
                if set(row) != {"ore", "stacks", "amount"} or not isinstance(row["stacks"], list) or len(row["stacks"]) > MAX_QUERIES:
                    raise ValueError("Observed item selector exceeds bounds")
                if row["ore"] is not None:
                    if not isinstance(row["ore"], str) or re.fullmatch(r"[A-Za-z0-9_:.\-/]{1,256}", row["ore"]) is None or row["stacks"]:
                        raise ValueError("Observed ore selector is malformed")
                elif not row["stacks"]:
                    raise ValueError("Observed concrete selector is empty")
                for stack in row["stacks"]:
                    _stack(stack)
            else:
                _stack(row, amount=True)
    return value


def _stack(value, *, amount=False):
    if (not isinstance(value, dict) or set(value) != {"item", "metadata", *( ["amount"] if amount else [])}
            or not isinstance(value["item"], str) or re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9_./-]{1,256}", value["item"]) is None
            or type(value["metadata"]) is not int or not 0 <= value["metadata"] < 32767
            or amount and (type(value["amount"]) is not int or not 1 <= value["amount"] <= 2**31 - 1)):
        raise ValueError("Observed concrete item is malformed")


def interpret(logs, nonce, expectation):
    if expectation is None:
        return None
    empty = {"state": "incomplete", "facts": {}, "evidence": [], "details": {}, "reasons": []}
    if expectation["support"] == "unsupported":
        return {**empty, "state": "unsupported", "reasons": expectation["reasons"]}
    retained = marker(logs, nonce, expectation)
    if retained is None:
        return {**empty, "reasons": ["One exact post-startup recipe capture was not observed."]}
    capture, evidence = retained
    empty.update(evidence=[evidence], details={"capture": capture, "capture_prefix": PREFIX, "capture_sha256": sha256(canonical(capture).encode()).hexdigest()})
    if capture.get("state") != "complete":
        return {**empty, "reasons": ["Recipe capture incomplete: " + str(capture.get("error", "unknown"))]}
    try:
        if set(capture) != {"format", "nonce", "expectation_id", "state", "phase", "resolution", "records", "queries", "map", "error", "lifecycle", "decisions"} or capture["format"] != "workbench-recipe-capture-v4" or capture["map"] != "mixer" or capture["phase"] != "render-tick-after-load-complete":
            raise ValueError("Unexpected capture contract or recipe map")
        if capture["error"] is not None:
            raise ValueError("A complete capture cannot carry an observer error")
        # JSON object-key order is transport detail, including encoded map keys.
        resolution = {canonical(json.loads(key)): value for key, value in capture["resolution"].items()}
        if len(resolution) != len(capture["resolution"]):
            raise ValueError("Ambiguous duplicate runtime identity resolutions")
        expected = _normalized(expectation["subject"]["recipe"], resolution)
        records, queries = capture["records"], capture["queries"]
        if not isinstance(records, list) or len(records) > MAX_RECIPES or not isinstance(queries, list) or not 1 <= len(queries) <= MAX_QUERIES:
            raise ValueError("Capture rows exceed their declared bounds")
        records = [{**row, "recipe": _observed(row["recipe"])} for row in records]
        queries = [{**row, "winner": _observed(row["winner"])} for row in queries]
        by_id = {}
        for row in records:
            if (set(row) != {"id", "recipe", "lookup_active", "category_present", "unsupported_reason"}
                    or not isinstance(row["id"], str) or re.fullmatch(r"r[0-9]{1,4}", row["id"]) is None or row["id"] in by_id
                    or any(type(row[key]) is not bool for key in ("lookup_active", "category_present"))
                    or not (row["lookup_active"] or row["category_present"])
                    or (row["recipe"] is None and (not isinstance(row["unsupported_reason"], str) or not 1 <= len(row["unsupported_reason"]) <= 2000))
                    or (row["recipe"] is not None and row["unsupported_reason"] is not None)):
                raise ValueError("Registration lacks exact inventory membership")
            by_id[row["id"]] = row
        query_ids, seen_records = set(), set()
        for query in queries:
            if (set(query) != {"id", "accepting", "winner_id", "items", "fluids", "voltage_limit", "winner", "unsupported"}
                    or not isinstance(query["id"], str) or re.fullmatch(r"q[0-9]{1,3}", query["id"]) is None or query["id"] in query_ids
                    or type(query["unsupported"]) is not bool or query["voltage_limit"] != 2**31 - 1
                    or not isinstance(query["items"], list) or not isinstance(query["fluids"], list)
                    or not isinstance(query["accepting"], list) or any(not isinstance(ref, str) or ref not in by_id for ref in query["accepting"])
                    or len(query["accepting"]) != len(set(query["accepting"]))):
                raise ValueError("Lookup contradicts captured inventory or query contract")
            if len(query["items"]) > 64 or len(query["fluids"]) > 64:
                raise ValueError("Query inputs exceed bounds")
            for item in query["items"]:
                _stack(item, amount=True)
            for fluid in query["fluids"]:
                if (not isinstance(fluid, dict) or set(fluid) != {"name", "amount"}
                        or not isinstance(fluid["name"], str) or re.fullmatch(r"[A-Za-z0-9_:.\-/]{1,256}", fluid["name"]) is None
                        or type(fluid["amount"]) is not int or not 1 <= fluid["amount"] <= 2**31 - 1):
                    raise ValueError("Query fluid input is malformed")
            winner = query["winner_id"]
            if winner is None:
                if query["winner"] is not None or query["unsupported"]:
                    raise ValueError("Null lookup reference contradicts its winner")
            elif (not isinstance(winner, str) or winner not in query["accepting"] or not by_id[winner]["lookup_active"]
                    or query["winner"] != by_id[winner]["recipe"] or query["unsupported"] != (query["winner"] is None)):
                raise ValueError("Lookup winner contradicts its accepting registration")
            query_ids.add(query["id"])
            seen_records.update(query["accepting"])
        if seen_records != set(by_id):
            raise ValueError("Captured registration accepts no captured query")
        if sum(len(row["accepting"]) for row in queries) > MAX_ACCEPTANCES:
            raise ValueError("Capture acceptance links exceed their declared bound")
        # Capture already restricts rows to recipes accepting at least one query.
        unsupported = [row for row in records if row["recipe"] is None]
        exact = [row for row in records if row["recipe"] == expected]
        # Cross-run comparisons use semantic multisets, not observation-local IDs.
        semantic = lambda row: {key: row[key] for key in ("recipe", "lookup_active", "category_present", "unsupported_reason")}
        observations = {"records": sorted((semantic(row) for row in records), key=canonical),
                        "queries": sorted(({**{key: row[key] for key in ("items", "fluids", "voltage_limit", "winner", "unsupported")},
                                            "accepting_signatures": sorted(sha256(canonical(semantic(by_id[ref])).encode()).hexdigest() for ref in row["accepting"])} for row in queries), key=canonical)}
        query_keys = {canonical({key: row[key] for key in ("kind", "name", "metadata")})
                      for key in ("item_inputs", "fluid_inputs") for row in expectation["subject"]["recipe"][key]}
        details = {**empty["details"], "resolution": resolution, "query_resolution": {key: resolution[key] for key in sorted(query_keys)},
                   "expected_recipe": expected, "observed": observations, "registrations": records, "lookups": queries,
                   "semantic_signatures": sorted(sha256(canonical(row["recipe"]).encode()).hexdigest() for row in records if row["recipe"] is not None)}
        if unsupported or any(query["unsupported"] for query in queries):
            return {**empty, "state": "unsupported", "details": details, "reasons": ["A matching registration or lookup winner uses unsupported recipe semantics."]}
        if len(exact) > 1:
            return {**empty, "state": "ambiguous", "details": details, "reasons": ["Multiple exact registration occurrences; no individual source or duplicate pairing is inferred."]}
        mode = expectation["mode"]
        facts = {"exact_registrations": len(exact),
                 "lookup_satisfies_expectation": all((query["winner"] == expected) == (mode == "present") for query in queries)}
        if mode == "present":
            facts["competing_registrations"] = len(records) - len(exact)
        return {"state": "complete", "facts": facts, "evidence": [evidence], "details": details, "reasons": []}
    except (ValueError, TypeError, KeyError) as exc:
        return {**empty, "reasons": ["Recipe observation cannot be interpreted: " + str(exc)]}
