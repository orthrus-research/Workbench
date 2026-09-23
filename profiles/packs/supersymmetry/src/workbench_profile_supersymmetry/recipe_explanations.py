"""Explain observed MIXER outcomes, never infer registration-time causation."""
from collections import defaultdict
import json

from workbench_crucible.check_explanations import MAX_BYTES, seal_explanation, section_text
from workbench_project_intelligence.saved_candidate import candidate_manifest

from .recipe_checks import _normalized, canonical, source_recipes

MAX_DETAILS = 64
MAX_SOURCE_BYTES = 8 * 1024 * 1024
LIMITATIONS = [
    "Assertion status and startup status are separate. Captured values do not override incomplete evidence or failed execution gates.",
    "Lookup uses the captured inputs at the stated maximum voltage, not a powered machine or gameplay simulation.",
    "Registration and query references are local to this capture, not stable identities across launches.",
    "A source candidate is a matching declaration, not proof of execution, rejection, overwrite, removal or causation.",
    "Source inspection covers only the bounded literal postInit subset; unmatched runtime registrations remain unattributed.",
]


def _section(identity, title, *, notes=(), properties=(), sources=(), evidence=()):
    return dict(id=identity, title=title, notes=list(notes), properties=list(properties), sources=list(sources), evidence=list(evidence))


def _bounded_report(summary, sections):
    """Presentation bounds must not downgrade otherwise valid observations."""
    retained, used, omitted = [], 0, 0
    for row in sections:
        notes = [note for note in row["notes"] if len(note) <= 4096]
        properties = [field for field in row["properties"] if all(len(text) <= 4096 for text in field.values())]
        if len(notes) != len(row["notes"]) or len(properties) != len(row["properties"]):
            notes.append("Over-bound property/selector text omitted; inspect the original structured observation for full values.")
        row = {**row, "notes": notes, "properties": properties}
        # The sealed payload retains structure, per-section text, and full text.
        size = len(json.dumps([row, section_text(row), section_text(row)]).encode())
        if used + size > MAX_BYTES - 16384 or len(retained) >= 322:
            omitted += 1
            continue
        retained.append(row)
        used += size
    limits = [*LIMITATIONS, *([f"Explanation byte or section bound reached; {omitted} section(s) omitted. The original structured observations are retained."] if omitted else [])]
    return seal_explanation(summary, retained, limits)


def _item(row):
    return row["item"] + (":" + str(row["metadata"]) if row["metadata"] else "")


def _identity(row, field):
    if field.startswith("fluid"):
        return row["name"]
    if field == "item_inputs":
        return "ore(" + row["ore"] + ")" if row["ore"] is not None else " | ".join(_item(stack) for stack in row["stacks"])
    return _item(row)


def _amounts(rows, field):
    values = defaultdict(list)
    for row in rows:
        values[_identity(row, field)].append(row["amount"])
    return {key: sorted(amounts) for key, amounts in values.items()}


def _quantity(values):
    if not values:
        return "none"
    return str(values[0]) if len(values) == 1 else "occurrence amounts [" + ", ".join(map(str, values)) + "]"


def differences(expected, observed):
    """No nearest-recipe pairing; compare one explicitly identified observation."""
    rows = []
    for field, label in (("duration", "Duration (ticks)"), ("eut", "EU/t")):
        if expected[field] != observed[field]:
            rows.append(dict(label=label, expected=str(expected[field]), observed=str(observed[field])))
    for field, label in (("item_inputs", "Item input"), ("fluid_inputs", "Fluid input"),
                         ("item_outputs", "Item output"), ("fluid_outputs", "Fluid output")):
        before, after = _amounts(expected[field], field), _amounts(observed[field], field)
        for identity in sorted(before.keys() | after.keys()):
            if before.get(identity) != after.get(identity):
                rows.append(dict(label=f"{label} · {identity} amount", expected=_quantity(before.get(identity)), observed=_quantity(after.get(identity))))
    return rows


def _source_candidates(inputs, resolution):
    """Bounded investigation index; does not evaluate Groovy or resolve new aliases."""
    index, size, count, skipped = defaultdict(list), 0, 0, 0
    reasons = []
    for name, raw in sorted(inputs.files):
        if not name.startswith("groovy/postInit/") or not name.endswith(".groovy"):
            continue
        size += len(raw)
        if size > MAX_SOURCE_BYTES:
            reasons.append("Source inspection stopped at its 8 MiB bound; additional files were not inspected.")
            break
        try:
            rows = list(source_recipes(name, raw))
        except (ValueError, UnicodeError):
            skipped += 1
            continue
        for row in rows:
            if row["support"] != "supported":
                skipped += 1
                continue
            resolved = dict(resolution)
            for field in ("item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs"):
                for ingredient in row["recipe"][field]:
                    key = canonical({key: ingredient[key] for key in ("kind", "name", "metadata")})
                    if ingredient["kind"] == "item":
                        resolved.setdefault(key, [dict(item=ingredient["name"], metadata=ingredient["metadata"])])
                    elif ingredient["kind"] in {"fluid", "ore"}:
                        resolved.setdefault(key, ingredient["name"] if ingredient["kind"] == "fluid" else [])
            try:
                normalized = _normalized(row["recipe"], resolved)
            except (KeyError, ValueError):
                skipped += 1
                continue
            index[canonical(normalized)].append(row["location"])
            count += 1
            if count >= 4096:
                reasons.append("Source inspection stopped at 4096 supported declarations; additional declarations were not inspected.")
                return index, [*reasons, f"Skipped unsupported or unresolved sources/declarations: {skipped}."]
    return index, [*reasons, f"Inspected supported declarations: {count}; skipped unsupported or unresolved sources/declarations: {skipped}."]


def explain(inputs, expectation, observation):
    if expectation is None:
        return None
    detail, evidence = observation["details"], observation["evidence"]
    expected_source = dict(label="Selected expected declaration", basis="expected-declaration",
                           candidate_id=expectation["source"]["candidate_id"], location=expectation["subject"]["location"])
    sections = [_section("expectation", "Selected expectation: " + expectation["mode"],
                         notes=["Expected source candidate: " + expectation["source"]["candidate_id"],
                                "Checked source candidate: " + expectation["candidate_id"]], sources=[expected_source])]
    from .recipe_lifecycle import sections as lifecycle_sections
    lifecycle = lifecycle_sections(inputs, expectation, observation)
    from .recipe_decisions import sections as decision_sections
    sections.extend(decision_sections(expectation, observation))
    sections.extend(lifecycle)
    if "registrations" not in detail:
        sections.append(_section("unavailable", "No interpretable recipe snapshot", notes=observation["reasons"], evidence=evidence))
        return _bounded_report("Recipe explanation unavailable; no absence or registration failure is inferred.", sections)
    expected = detail["expected_recipe"]
    registrations, lookups = detail["registrations"], detail["lookups"]
    index, scan_notes = _source_candidates(inputs, detail["resolution"])
    candidate_id = candidate_manifest(inputs)["id"]
    by_id = {row["id"]: row for row in registrations}
    exact = sum(row["recipe"] == expected for row in registrations)
    sections.append(_section("inventory", "Captured accepting registrations", notes=[
        f"{len(registrations)} registration occurrence(s); {exact} exact expectation match(es); {len(lookups)} bounded lookup(s).",
        *(observation["reasons"]), *scan_notes,
        "Identical declarations and registrations remain separate; even one source match does not establish runtime origin.",
        *( [f"Registration details limited to {MAX_DETAILS}; {len(registrations) - MAX_DETAILS} further occurrences remain in the retained capture."] if len(registrations) > MAX_DETAILS else []),
    ], evidence=evidence))
    # Winners first ensures the useful observations receive the bounded detail budget.
    winners = {row["winner_id"] for row in lookups if row["winner_id"] is not None}
    selected = sorted(registrations, key=lambda row: (row["id"] not in winners, row["id"]))[:MAX_DETAILS]
    source_budget = MAX_DETAILS
    for row in selected:
        recipe = row["recipe"]
        matches = [] if recipe is None else index.get(canonical(recipe), [])
        shown = matches[:source_budget]
        source_budget -= len(shown)
        notes = [f"Active lookup membership: {row['lookup_active']}; category membership: {row['category_present']}."]
        if recipe is None:
            notes.append("Unsupported observation: " + row["unsupported_reason"])
        elif recipe == expected:
            notes.append("All supported properties equal the expectation.")
        else:
            notes.append("These observed properties differ from the selected expectation; this is not source-change pairing.")
        notes.append(f"Matching literal source candidates: {len(matches)}; " + ("unattributed within inspected source." if not matches else "declarations only, not verified runtime origins."))
        if len(shown) < len(matches):
            notes.append(f"Source links limited; {len(matches) - len(shown)} additional matches omitted.")
        sections.append(_section("registration-" + row["id"], "Registration " + row["id"], notes=notes,
            properties=[] if recipe is None else differences(expected, recipe), evidence=evidence,
            sources=[dict(label="Matching literal declaration", basis="literal-candidate", candidate_id=candidate_id, location=location) for location in shown]))
    for query in lookups:
        winner = query["winner_id"]
        if winner is None:
            title = "No recipe selected by the bounded lookup"
        elif query["unsupported"]:
            title = "Selected recipe uses unsupported semantics"
        else:
            equal = query["winner"] == expected
            title = "Selected recipe equals the expectation" if equal else "Selected recipe differs from the expectation"
        notes = ["Items: " + (", ".join(f"{_item(row)} × {row['amount']}" for row in query["items"]) or "none"),
                 "Fluids: " + (", ".join(f"{row['name']} × {row['amount']}" for row in query["fluids"]) or "none"),
                 "Voltage limit: " + str(query["voltage_limit"]), "Selected recipe: " + (winner or "none"),
                 "Accepting registration references: " + (", ".join(sorted(query["accepting"])[:MAX_DETAILS]) or "none")]
        if len(query["accepting"]) > MAX_DETAILS:
            notes.append(f"{len(query['accepting']) - MAX_DETAILS} additional accepting references omitted from this summary; retained capture is complete.")
        if winner is not None:
            notes.append(f"Selected recipe inventory membership: active={by_id[winner]['lookup_active']}, category={by_id[winner]['category_present']}.")
        if len(query["accepting"]) > 1:
            notes.append("Multiple registrations accept these inputs. The selected recipe does not prove overwrite or rejection of another registration.")
        sections.append(_section("lookup-" + query["id"], query["id"] + " · " + title, notes=notes,
                                 properties=[] if query["winner"] is None else differences(expected, query["winner"]), evidence=evidence))
    return _bounded_report(f"Captured recipe evidence: {len(registrations)} accepting registration occurrence(s), {len(lookups)} bounded lookup(s).", sections)
