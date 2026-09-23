"""Bounded assessment of one owner-validated proposed finite recipe.

This module consumes a small product-generic projection of a recipe plan.  It
does not validate a Blueprints or pack-owned plan, render source, mutate the
graph, or execute a runtime.  The caller must first use the plan owner's
validator and then translate only the validated request and authority binding.

The resulting signals are deliberately structural.  They identify exact
observed resource keys where possible, existing finite recipes with the same
resolved request shape, output producers and consumers, definition-level quest
references, and bounded output-to-input dependency-cycle candidates.  None of
those signals proves a gameplay bypass, player reachability, or broken
progression.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING, cast


if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .view import GraphRecipeHealthView


PROPOSED_ASSESSMENT_FORMAT = "workbench-atlas-proposed-recipe-assessment-v1"

_RECIPE_KIND = "gt-recipe"
_INPUT_RELATIONS = frozenset(
    {
        "accepts-gt-item-alternative",
        "accepts-ore-dictionary-class",
        "accepts-gt-fluid-input",
    }
)
_OUTPUT_RELATIONS = frozenset({"produces-gt-item", "produces-gt-fluid"})
_SELECTOR_RELATIONS = frozenset(
    {"has-item-input-selector", "has-fluid-input-selector"}
)
_MACHINE_MAP_RELATIONS = frozenset(
    {"uses-recipe-map", "currently-selects-recipe-map"}
)
_QUEST_RESOURCE_RELATIONS = frozenset(
    {"observes-progression-item-variant", "requires-progression-fluid"}
)
_TASK_RESOURCE_RELATIONS = frozenset(
    {"has-progression-item-requirement", "has-progression-fluid-requirement"}
)
_CONTENT_ID = re.compile(r"^[a-z][a-z0-9-]*:sha256:[0-9a-f]{64}$")
_SINGLE_LINE = re.compile(r"^[^\r\n\x00]+$")
_VOLTAGE_TIERS = (
    "ULV",
    "LV",
    "MV",
    "HV",
    "EV",
    "IV",
    "LuV",
    "ZPM",
    "UV",
    "UHV",
    "UEV",
    "UIV",
)


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_id": node["id"],
        "kind": node["kind"],
        "semantic_key": node["semantic_key"],
        "properties": node["properties"],
        "evidence": node["evidence"],
    }


def _edge_summary(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation": edge["relation"],
        "semantic_key": edge["semantic_key"],
        "properties": edge["properties"],
        "evidence": edge["evidence"],
    }


def _text(value: object, label: str) -> str:
    from .view import RecipeHealthError

    if (
        type(value) is not str
        or not value
        or len(value) > 1_024
        or _SINGLE_LINE.fullmatch(value) is None
    ):
        raise RecipeHealthError(f"{label} must be bounded nonempty single-line text")
    return value


def _positive(value: object, label: str) -> int:
    from .view import RecipeHealthError

    if type(value) is not int or type(value) is bool or not 1 <= value <= 2_147_483_647:
        raise RecipeHealthError(f"{label} must be a positive 32-bit integer")
    return value


def _bound(value: object, label: str, *, minimum: int, maximum: int) -> int:
    from .view import RecipeHealthError

    if type(value) is not int or type(value) is bool or not minimum <= value <= maximum:
        raise RecipeHealthError(
            f"proposed-recipe {label} is outside {minimum}..{maximum}"
        )
    return value


def _ingredient(
    value: object,
    label: str,
    *,
    output: bool,
) -> dict[str, Any]:
    from .view import RecipeHealthError

    if type(value) is not dict:
        raise RecipeHealthError(f"{label} must be an object")
    row = cast(dict[str, Any], value)
    if set(row) - {"kind", "name", "amount", "metadata"} or not {
        "kind",
        "name",
        "amount",
    } <= set(row):
        raise RecipeHealthError(f"{label} fields changed")
    kind = _text(row.get("kind"), f"{label} kind")
    name = _text(row.get("name"), f"{label} name")
    amount = _positive(row.get("amount"), f"{label} amount")
    if kind not in {"ore", "metaitem", "item"} or output and kind == "ore":
        raise RecipeHealthError(f"{label} kind is unsupported")
    metadata = row.get("metadata")
    if metadata is not None and (
        kind != "item"
        or type(metadata) is not int
        or type(metadata) is bool
        or not 0 <= metadata <= 32_767
    ):
        raise RecipeHealthError(f"{label} metadata is invalid")
    normalized = {"kind": kind, "name": name, "amount": amount}
    if metadata is not None:
        normalized["metadata"] = metadata
    return normalized


def _fluid(value: object, label: str) -> dict[str, Any]:
    from .view import RecipeHealthError

    if type(value) is not dict or set(value) != {"name", "amount"}:
        raise RecipeHealthError(f"{label} fields changed")
    row = cast(dict[str, Any], value)
    return {
        "name": _text(row.get("name"), f"{label} name"),
        "amount": _positive(row.get("amount"), f"{label} amount"),
    }


def _validate_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the generic translation, not its pack-owned source plan."""

    from .view import RecipeHealthError

    expected = {
        "proposal_id",
        "source_format",
        "source_kind",
        "mutation",
        "recipe_map",
        "duration",
        "voltage_tier",
        "item_inputs",
        "fluid_inputs",
        "item_outputs",
        "fluid_outputs",
    }
    if type(value) is not dict or set(value) != expected:
        raise RecipeHealthError("proposed-recipe translation fields changed")
    proposal_id = _text(value.get("proposal_id"), "proposed-recipe proposal ID")
    source_kind = _text(value.get("source_kind"), "proposed-recipe source kind")
    if (
        _CONTENT_ID.fullmatch(proposal_id) is None
        or not proposal_id.startswith(source_kind + ":sha256:")
        or value.get("mutation") != "add"
    ):
        raise RecipeHealthError(
            "proposed-recipe translation is not one content-bound ADD plan"
        )
    lists: dict[str, list[dict[str, Any]]] = {}
    for key, output in (("item_inputs", False), ("item_outputs", True)):
        rows = value.get(key)
        if type(rows) is not list or len(rows) > 16:
            raise RecipeHealthError(f"proposed-recipe {key} is not bounded")
        lists[key] = [
            _ingredient(row, f"proposed-recipe {key}[{index}]", output=output)
            for index, row in enumerate(rows)
        ]
    for key in ("fluid_inputs", "fluid_outputs"):
        rows = value.get(key)
        if type(rows) is not list or len(rows) > 16:
            raise RecipeHealthError(f"proposed-recipe {key} is not bounded")
        lists[key] = [
            _fluid(row, f"proposed-recipe {key}[{index}]")
            for index, row in enumerate(rows)
        ]
    if (
        not lists["item_inputs"]
        and not lists["fluid_inputs"]
        or not lists["item_outputs"]
        and not lists["fluid_outputs"]
    ):
        raise RecipeHealthError("proposed-recipe requires inputs and outputs")
    voltage_tier = _text(value.get("voltage_tier"), "proposed-recipe voltage tier")
    if voltage_tier not in _VOLTAGE_TIERS:
        raise RecipeHealthError("proposed-recipe voltage tier is unsupported")
    return {
        "proposal_id": proposal_id,
        "source_format": _text(
            value.get("source_format"), "proposed-recipe source format"
        ),
        "source_kind": source_kind,
        "mutation": "add",
        "recipe_map": _text(value.get("recipe_map"), "proposed-recipe recipe map"),
        "duration": _positive(value.get("duration"), "proposed-recipe duration"),
        "voltage_tier": voltage_tier,
        **lists,
    }


@dataclass
class _Budget:
    maximum: int
    seen: set[str] = field(default_factory=set)
    frontiers: list[dict[str, Any]] = field(default_factory=list)
    _frontier_keys: set[tuple[object, ...]] = field(default_factory=set)

    def admit(self, node_id: str, *, phase: str, from_node_id: str | None = None) -> bool:
        if node_id in self.seen:
            return True
        if len(self.seen) < self.maximum:
            self.seen.add(node_id)
            return True
        self.frontier(
            "node-bound",
            phase=phase,
            from_node_id=from_node_id,
            omitted_at_least=1,
        )
        return False

    def frontier(self, kind: str, **detail: Any) -> None:
        key = (
            kind,
            detail.get("phase"),
            detail.get("from_node_id"),
            detail.get("depth"),
        )
        if key in self._frontier_keys:
            return
        self._frontier_keys.add(key)
        self.frontiers.append({"kind": kind, **detail})


class _Builder:
    def __init__(
        self,
        view: "GraphRecipeHealthView",
        proposal: dict[str, Any],
        *,
        max_depth: int,
        max_nodes: int,
    ) -> None:
        self.view = view
        self.proposal = proposal
        self.max_depth = max_depth
        self.budget = _Budget(max_nodes)
        self.unknowns: list[dict[str, Any]] = []
        self._unknown_keys: set[tuple[str, str | None]] = set()

    def unknown(self, code: str, message: str, subject: str | None = None) -> None:
        key = (code, subject)
        if key in self._unknown_keys:
            return
        self._unknown_keys.add(key)
        row: dict[str, Any] = {"code": code, "message": message}
        if subject is not None:
            row["subject"] = subject
        self.unknowns.append(row)

    def _nodes(self, sql: str, parameters: Sequence[object], *, phase: str) -> list[dict[str, Any]]:
        remaining = max(0, self.budget.maximum - len(self.budget.seen))
        if remaining == 0:
            self.budget.frontier("node-bound", phase=phase, omitted_at_least=1)
            return []
        rows = list(
            self.view.query.connection.execute(sql + " LIMIT ?", [*parameters, remaining + 1])
        )
        if len(rows) > remaining:
            self.budget.frontier("node-bound", phase=phase, omitted_at_least=1)
        result: list[dict[str, Any]] = []
        for row in rows[:remaining]:
            node = self.view.query._node_row(row)
            if self.budget.admit(node["id"], phase=phase):
                result.append(node)
        return result

    def _edges(
        self,
        node_id: str,
        relations: Iterable[str],
        *,
        direction: str,
        phase: str,
    ) -> list[dict[str, Any]]:
        ordered = sorted(relations)
        endpoint = "s.id=?" if direction == "from" else "t.id=?"
        order = "e.relation,t.id,e.edge_key" if direction == "from" else "e.relation,s.id,e.edge_key"
        rows = list(
            self.view.query.connection.execute(
                "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json "
                "FROM edges e JOIN nodes s ON s.node_key=e.source_node "
                "JOIN nodes t ON t.node_key=e.target_node WHERE "
                + endpoint
                + " AND e.relation IN ("
                + ",".join("?" for _ in ordered)
                + ") ORDER BY "
                + order
                + " LIMIT ?",
                [node_id, *ordered, self.budget.maximum + 1],
            )
        )
        if len(rows) > self.budget.maximum:
            self.budget.frontier(
                "relation-bound", phase=phase, from_node_id=node_id, omitted_at_least=1
            )
        return [self.view.query._edge_row(row) for row in rows[: self.budget.maximum]]

    def _node(self, node_id: str, *, phase: str, from_node_id: str | None = None) -> dict[str, Any] | None:
        if not self.budget.admit(node_id, phase=phase, from_node_id=from_node_id):
            return None
        return self.view._node(node_id)

    def _resolve_one(
        self,
        descriptor: dict[str, Any],
        *,
        family: str,
        ordinal: int,
    ) -> dict[str, Any]:
        phase = "resource-resolution"
        kind = descriptor.get("kind")
        if family.startswith("fluid-"):
            candidates = self._nodes(
                "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
                "WHERE kind=? AND semantic_key=? ORDER BY id",
                ("forge-fluid", descriptor["name"]),
                phase=phase,
            )
            identity_kind = "forge-fluid-name"
            exact_key = True
        elif kind == "ore":
            candidates = self._nodes(
                "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
                "WHERE kind=? AND semantic_key=? ORDER BY id",
                ("ore-dictionary-key", descriptor["name"]),
                phase=phase,
            )
            identity_kind = "ore-dictionary-key"
            exact_key = True
        elif kind == "metaitem":
            self.unknown(
                "symbolic-metaitem-runtime-binding-unavailable",
                "The graph does not retain a proved mapping from this source metaitem symbol to an exact runtime item variant.",
                f"{family}:{ordinal}",
            )
            return {
                "family": family,
                "ordinal": ordinal,
                "descriptor": descriptor,
                "identity_kind": "source-metaitem-symbol",
                "status": "unsupported-symbolic-binding",
                "candidate_count": None,
                "candidates": [],
                "reason": "no admitted source-symbol-to-runtime-item identity mapping",
            }
        else:
            clauses = [
                "kind='item-variant'",
                "json_extract(properties_json,'$.registry_name')=?",
            ]
            parameters: list[object] = [descriptor["name"]]
            if "metadata" in descriptor:
                clauses.append("json_extract(properties_json,'$.metadata')=?")
                parameters.append(descriptor["metadata"])
            clauses.append("json_extract(properties_json,'$.count_in_observation')=?")
            parameters.append(descriptor["amount"])
            candidates = self._nodes(
                "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes WHERE "
                + " AND ".join(clauses)
                + " ORDER BY semantic_key,id",
                parameters,
                phase=phase,
            )
            identity_kind = "item-registry-variant"
            exact_key = "metadata" in descriptor
        if not candidates:
            status = "unresolved"
            reason = "no exact observed graph key or item candidate matched"
        elif len(candidates) > 1 or not exact_key:
            status = "ambiguous-observed-candidates"
            reason = "the source descriptor does not select one exact observed runtime identity"
        else:
            status = "exact-observed-key"
            reason = "one exact observed graph key matched the validated source descriptor"
        if status != "exact-observed-key":
            self.unknown(
                "proposed-resource-not-exactly-resolved",
                reason,
                f"{family}:{ordinal}",
            )
        return {
            "family": family,
            "ordinal": ordinal,
            "descriptor": descriptor,
            "identity_kind": identity_kind,
            "status": status,
            "candidate_count": len(candidates),
            "candidates": [_node_summary(node) for node in candidates],
            "reason": reason,
        }

    def _resolution(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "item_inputs": [
                self._resolve_one(row, family="item-input", ordinal=index)
                for index, row in enumerate(self.proposal["item_inputs"])
            ],
            "fluid_inputs": [
                self._resolve_one(row, family="fluid-input", ordinal=index)
                for index, row in enumerate(self.proposal["fluid_inputs"])
            ],
            "item_outputs": [
                self._resolve_one(row, family="item-output", ordinal=index)
                for index, row in enumerate(self.proposal["item_outputs"])
            ],
            "fluid_outputs": [
                self._resolve_one(row, family="fluid-output", ordinal=index)
                for index, row in enumerate(self.proposal["fluid_outputs"])
            ],
        }

    @staticmethod
    def _one_candidate(row: Mapping[str, Any]) -> str | None:
        candidates = row.get("candidates")
        if row.get("status") != "exact-observed-key" or type(candidates) is not list or len(candidates) != 1:
            return None
        return candidates[0]["selection_id"]

    def _proposal_shape(
        self, resolution: Mapping[str, list[dict[str, Any]]]
    ) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]] | None:
        inputs: list[tuple[str, str, int]] = []
        outputs: list[tuple[str, str, int]] = []
        for key, relation in (
            ("item_inputs", "accepts-gt-item-alternative"),
            ("fluid_inputs", "accepts-gt-fluid-input"),
        ):
            for source, resolved in zip(self.proposal[key], resolution[key]):
                node_id = self._one_candidate(resolved)
                if node_id is None:
                    return None
                actual_relation = (
                    "accepts-ore-dictionary-class"
                    if source.get("kind") == "ore"
                    else relation
                )
                inputs.append((actual_relation, node_id, source["amount"]))
        for key, relation in (
            ("item_outputs", "produces-gt-item"),
            ("fluid_outputs", "produces-gt-fluid"),
        ):
            for source, resolved in zip(self.proposal[key], resolution[key]):
                node_id = self._one_candidate(resolved)
                if node_id is None:
                    return None
                outputs.append((relation, node_id, source["amount"]))
        return sorted(inputs), sorted(outputs)

    def _recipe_shape(
        self, recipe: dict[str, Any]
    ) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]] | None:
        inputs: list[tuple[str, str, int]] = []
        outputs: list[tuple[str, str, int]] = []
        selector_edges = self._edges(
            recipe["id"], _SELECTOR_RELATIONS, direction="from", phase="structural-collision"
        )
        for selector_edge in selector_edges:
            selector = self._node(
                selector_edge["target"], phase="structural-collision", from_node_id=recipe["id"]
            )
            if selector is None:
                return None
            if selector["properties"].get("acceptance_complete", True) is not True:
                self.unknown(
                    "selector-acceptance-incomplete",
                    "Captured input representatives do not establish an exact selector shape for collision comparison.",
                    selector["id"],
                )
                return None
            accepted = self._edges(
                selector["id"], _INPUT_RELATIONS, direction="from", phase="structural-collision"
            )
            ore = [edge for edge in accepted if edge["relation"] == "accepts-ore-dictionary-class"]
            selected = ore if ore else accepted
            if len(selected) != 1:
                return None
            edge = selected[0]
            amount = edge["properties"].get("amount")
            if edge["relation"] == "accepts-gt-item-alternative":
                target = self._node(
                    edge["target"],
                    phase="structural-collision",
                    from_node_id=selector["id"],
                )
                amount = (
                    None
                    if target is None
                    else target["properties"].get("count_in_observation")
                )
            if type(amount) is not int or type(amount) is bool:
                return None
            inputs.append((edge["relation"], edge["target"], amount))
        for edge in self._edges(
            recipe["id"], _OUTPUT_RELATIONS, direction="from", phase="structural-collision"
        ):
            amount = edge["properties"].get("amount")
            if edge["relation"] == "produces-gt-item":
                target = self._node(
                    edge["target"], phase="structural-collision", from_node_id=recipe["id"]
                )
                amount = None if target is None else target["properties"].get("count_in_observation")
            if type(amount) is not int or type(amount) is bool:
                return None
            outputs.append((edge["relation"], edge["target"], amount))
        return sorted(inputs), sorted(outputs)

    def _collisions(
        self,
        resolution: Mapping[str, list[dict[str, Any]]],
        recipe_maps: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        shape = self._proposal_shape(resolution)
        if len(recipe_maps) != 1:
            status = "evidence-unavailable"
            candidates: list[dict[str, Any]] = []
        elif shape is None:
            status = "resource-resolution-incomplete"
            candidates = []
        else:
            recipes = self._nodes(
                "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
                "WHERE kind=? AND json_extract(properties_json,'$.recipe_map')=? "
                "AND json_extract(properties_json,'$.duration')=? ORDER BY semantic_key,id",
                (_RECIPE_KIND, self.proposal["recipe_map"], self.proposal["duration"]),
                phase="structural-collision",
            )
            candidates = []
            for recipe in recipes:
                if self._recipe_shape(recipe) == shape:
                    candidates.append(
                        {
                            "recipe": _node_summary(recipe),
                            "duration_delta": recipe["properties"].get("duration")
                            - self.proposal["duration"],
                            "observed_eut": recipe["properties"].get("eut"),
                            "eut_comparison": "proposed-numeric-eut-unavailable",
                        }
                    )
            status = "candidates-observed" if candidates else "no-candidate-observed"
        return {
            "exact_runtime_signature": {
                "status": "unavailable-before-runtime-observation",
                "semantic_sha256": None,
                "collision_count": None,
                "candidates": [],
                "reason": "the source plan does not establish resolved runtime stacks or the runtime semantic recipe digest",
            },
            "resolved_request_structure": {
                "status": status,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "comparison_dimensions": [
                    "observed recipe-map registry name",
                    "resolved input relation, resource, and amount",
                    "resolved output relation, resource, and amount",
                    "duration",
                ],
                "excluded_dimensions": [
                    "numeric EUt",
                    "runtime-generated properties",
                    "lookup ordering and collision behavior",
                    "unresolved or ambiguous source identities",
                ],
                "interpretation": "a structural candidate is not an exact signature collision",
            },
        }

    def _output_flow_one(self, resolved: Mapping[str, Any]) -> dict[str, Any]:
        node_id = self._one_candidate(resolved)
        if node_id is None:
            return {
                "output": resolved,
                "status": "resource-not-exactly-resolved",
                "existing_producers": [],
                "existing_consumers": [],
            }
        producers: list[dict[str, Any]] = []
        for edge in self._edges(
            node_id, _OUTPUT_RELATIONS, direction="to", phase="output-flow"
        ):
            recipe = self._node(edge["source"], phase="output-flow", from_node_id=node_id)
            if recipe is not None and recipe["kind"] == _RECIPE_KIND:
                producers.append({"recipe": _node_summary(recipe), "edge": _edge_summary(edge)})
        consumers: list[dict[str, Any]] = []
        for accepted in self._edges(
            node_id, _INPUT_RELATIONS, direction="to", phase="output-flow"
        ):
            selector = self._node(accepted["source"], phase="output-flow", from_node_id=node_id)
            if selector is None:
                continue
            for selector_edge in self._edges(
                selector["id"], _SELECTOR_RELATIONS, direction="to", phase="output-flow"
            ):
                recipe = self._node(
                    selector_edge["source"], phase="output-flow", from_node_id=selector["id"]
                )
                if recipe is not None and recipe["kind"] == _RECIPE_KIND:
                    consumers.append(
                        {
                            "recipe": _node_summary(recipe),
                            "selector": _node_summary(selector),
                            "acceptance_edge": _edge_summary(accepted),
                        }
                    )
        producers.sort(key=lambda row: row["recipe"]["selection_id"])
        consumers.sort(key=lambda row: (row["recipe"]["selection_id"], row["selector"]["selection_id"]))
        return {
            "output": resolved,
            "status": "observed-finite-graph-neighborhood",
            "existing_producers": producers,
            "existing_consumers": consumers,
        }

    def _output_flow(
        self, resolution: Mapping[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        return [
            self._output_flow_one(row)
            for row in [*resolution["item_outputs"], *resolution["fluid_outputs"]]
        ]

    def _cycle_candidates(
        self, resolution: Mapping[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        input_rows = [*resolution["item_inputs"], *resolution["fluid_inputs"]]
        output_rows = [*resolution["item_outputs"], *resolution["fluid_outputs"]]
        inputs = {
            node_id: row
            for row in input_rows
            if (node_id := self._one_candidate(row)) is not None
        }
        signals: list[dict[str, Any]] = []
        signal_keys: set[tuple[str, str, tuple[str, ...]]] = set()
        for output in output_rows:
            output_id = self._one_candidate(output)
            if output_id is None:
                continue
            queue: deque[tuple[str, int, tuple[str, ...]]] = deque(
                [(output_id, 0, (output_id,))]
            )
            visited: set[tuple[str, int]] = set()
            while queue:
                resource_id, depth, path = queue.popleft()
                if (resource_id, depth) in visited:
                    continue
                visited.add((resource_id, depth))
                if resource_id in inputs:
                    key = (output_id, resource_id, path)
                    if key not in signal_keys:
                        signal_keys.add(key)
                        signals.append(
                            {
                                "proposed_output": output,
                                "proposed_input": inputs[resource_id],
                                "observed_output_to_input_path": list(path),
                                "proposed_closing_edge": {
                                    "input_resource_id": resource_id,
                                    "proposal_id": self.proposal["proposal_id"],
                                    "output_resource_id": output_id,
                                },
                                "interpretation": "an observed finite recipe path from a proposed output reaches a proposed input; adding the proposal would close a structural cycle",
                                "viability_effect": "unknown",
                            }
                        )
                    continue
                if depth >= self.max_depth:
                    if self._edges(
                        resource_id,
                        _INPUT_RELATIONS,
                        direction="to",
                        phase="output-input-cycle-scan",
                    ):
                        self.budget.frontier(
                            "depth-bound",
                            phase="output-input-cycle-scan",
                            from_node_id=resource_id,
                            depth=self.max_depth,
                            omitted_at_least=1,
                        )
                    continue
                for accepted in self._edges(
                    resource_id,
                    _INPUT_RELATIONS,
                    direction="to",
                    phase="output-input-cycle-scan",
                ):
                    selector = self._node(
                        accepted["source"],
                        phase="output-input-cycle-scan",
                        from_node_id=resource_id,
                    )
                    if selector is None:
                        continue
                    for selector_edge in self._edges(
                        selector["id"],
                        _SELECTOR_RELATIONS,
                        direction="to",
                        phase="output-input-cycle-scan",
                    ):
                        recipe = self._node(
                            selector_edge["source"],
                            phase="output-input-cycle-scan",
                            from_node_id=selector["id"],
                        )
                        if recipe is None or recipe["kind"] != _RECIPE_KIND:
                            continue
                        for produced in self._edges(
                            recipe["id"],
                            _OUTPUT_RELATIONS,
                            direction="from",
                            phase="output-input-cycle-scan",
                        ):
                            next_resource = self._node(
                                produced["target"],
                                phase="output-input-cycle-scan",
                                from_node_id=recipe["id"],
                            )
                            if next_resource is not None and next_resource["id"] not in path:
                                queue.append(
                                    (
                                        next_resource["id"],
                                        depth + 1,
                                        (*path, selector["id"], recipe["id"], next_resource["id"]),
                                    )
                                )
        return sorted(
            signals,
            key=lambda row: (
                row["proposed_output"]["ordinal"],
                row["proposed_input"]["ordinal"],
                row["observed_output_to_input_path"],
            ),
        )

    def _quest_requirements(
        self, resolution: Mapping[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for output in [*resolution["item_outputs"], *resolution["fluid_outputs"]]:
            resource_id = self._one_candidate(output)
            if resource_id is None:
                continue
            for occurrence_edge in self._edges(
                resource_id,
                _QUEST_RESOURCE_RELATIONS,
                direction="to",
                phase="quest-output-reference",
            ):
                occurrence = self._node(
                    occurrence_edge["source"],
                    phase="quest-output-reference",
                    from_node_id=resource_id,
                )
                if occurrence is None:
                    continue
                for task_edge in self._edges(
                    occurrence["id"],
                    _TASK_RESOURCE_RELATIONS,
                    direction="to",
                    phase="quest-output-reference",
                ):
                    task = self._node(
                        task_edge["source"],
                        phase="quest-output-reference",
                        from_node_id=occurrence["id"],
                    )
                    if task is None:
                        continue
                    for quest_edge in self._edges(
                        task["id"],
                        frozenset({"owns-progression-task"}),
                        direction="to",
                        phase="quest-output-reference",
                    ):
                        quest = self._node(
                            quest_edge["source"],
                            phase="quest-output-reference",
                            from_node_id=task["id"],
                        )
                        if quest is not None:
                            rows.append(
                                {
                                    "output": output,
                                    "occurrence": _node_summary(occurrence),
                                    "task": _node_summary(task),
                                    "quest": _node_summary(quest),
                                    "resource_edge": _edge_summary(occurrence_edge),
                                }
                            )
        rows.sort(
            key=lambda row: (
                row["output"]["family"],
                row["output"]["ordinal"],
                row["quest"]["selection_id"],
            )
        )
        if rows:
            status = "observed-definition-references"
        elif any(self.view.relations.get(relation, 0) for relation in _QUEST_RESOURCE_RELATIONS):
            status = "no-observed-reference"
        else:
            status = "evidence-unavailable"
        return {
            "status": status,
            "direct_output_requirements": rows,
            "interpretation": "quest definitions referencing an output are shortcut-review signals, not proof that a player can produce, submit, or use it",
        }

    def _recipe_maps(self) -> list[dict[str, Any]]:
        maps = self._nodes(
            "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
            "WHERE kind=? AND semantic_key=? ORDER BY id",
            ("gt-recipe-map", self.proposal["recipe_map"]),
            phase="numeric-signals",
        )
        if len(maps) != 1:
            self.unknown(
                "proposed-recipe-map-evidence-unavailable",
                "The graph does not contain one exact observed recipe-map node for the owner-bound proposed recipe map.",
                self.proposal["recipe_map"],
            )
        return maps

    def _numeric_signals(
        self, maps: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        machines: list[dict[str, Any]] = []
        if len(maps) == 1:
            for edge in self._edges(
                maps[0]["id"], _MACHINE_MAP_RELATIONS, direction="to", phase="numeric-signals"
            ):
                machine = self._node(edge["source"], phase="numeric-signals", from_node_id=maps[0]["id"])
                if machine is not None:
                    machines.append(_node_summary(machine))
        recipes = self._nodes(
            "SELECT id,kind,semantic_key,properties_json,evidence_json FROM nodes "
            "WHERE kind=? AND json_extract(properties_json,'$.recipe_map')=? "
            "ORDER BY semantic_key,id",
            (_RECIPE_KIND, self.proposal["recipe_map"]),
            phase="numeric-signals",
        )
        durations = sorted(
            {
                value
                for recipe in recipes
                if type(value := recipe["properties"].get("duration")) is int
                and type(value) is not bool
            }
        )
        euts = sorted(
            {
                value
                for recipe in recipes
                if type(value := recipe["properties"].get("eut")) is int
                and type(value) is not bool
            }
        )
        tiers = sorted(
            {
                value
                for machine in machines
                if type(value := machine["properties"].get("tier")) is int
                and type(value) is not bool
            }
        )
        return {
            "proposal": {
                "duration": self.proposal["duration"],
                "voltage_tier": self.proposal["voltage_tier"],
                "voltage_tier_ordinal": _VOLTAGE_TIERS.index(self.proposal["voltage_tier"]),
                "numeric_eut": None,
            },
            "recipe_map": None if len(maps) != 1 else _node_summary(maps[0]),
            "machines_observed_using_recipe_map": sorted(
                machines, key=lambda row: row["selection_id"]
            ),
            "machine_numeric_tiers_observed": tiers,
            "observed_recipe_count_inspected": len(recipes),
            "observed_durations": durations,
            "observed_euts": euts,
            "interpretation": "duration, observed recipe EUt, and machine tier properties are numeric review signals; the symbolic proposed voltage tier is not converted into a claimed runtime EUt or pack progression tier",
        }

    def build(self) -> dict[str, Any]:
        recipe_maps = self._recipe_maps()
        resolution = self._resolution()
        collisions = self._collisions(resolution, recipe_maps)
        flow = self._output_flow(resolution)
        cycles = self._cycle_candidates(resolution)
        quests = self._quest_requirements(resolution)
        numeric = self._numeric_signals(recipe_maps)
        exact = sum(
            row["status"] == "exact-observed-key"
            for rows in resolution.values()
            for row in rows
        )
        unresolved = sum(
            row["status"] != "exact-observed-key"
            for rows in resolution.values()
            for row in rows
        )
        report = {
            "format": PROPOSED_ASSESSMENT_FORMAT,
            "schema_version": 1,
            "context": self.view.describe(),
            "proposal": self.proposal,
            "scenario": {
                "kind": "add-owner-validated-proposed-recipe",
                "source_plan_validation": "required-upstream",
                "proposed_source_bytes_observed_runtime": False,
                "existing_graph_mutated": False,
                "interpretation": "the validated plan is compared with one earlier observed graph; it is not projected into that graph as runtime truth",
            },
            "analysis_model": {
                "kind": "bounded-proposed-recipe-structural-review",
                "dead_path_rule": "an ADD preserves existing finite graph edges in this model, so no dead path is classified",
                "shortcut_rule": "collisions, output consumers, quest references, and output-to-input cycles are review candidates only",
                "claim_boundary": "observed finite recipe and definition structure; not player reachability or runtime lookup behavior",
            },
            "bounds": {
                "max_depth": self.max_depth,
                "max_nodes": self.budget.maximum,
                "visited_node_count": len(self.budget.seen),
            },
            "resolution": resolution,
            "collision_assessment": collisions,
            "output_flow": flow,
            "dependency_cycle_candidates": cycles,
            "progression_signals": {
                "dead_path_assessment": {
                    "status": "no-removal-modeled",
                    "candidate_count": 0,
                    "interpretation": "the add-only structural model removes no observed producers or consumers; runtime collision or lookup effects remain unobserved",
                },
                "quest_signals": quests,
                "energy_and_machine_signals": numeric,
            },
            "frontiers": sorted(
                self.budget.frontiers,
                key=lambda row: (
                    row["kind"],
                    str(row.get("phase", "")),
                    str(row.get("from_node_id", "")),
                ),
            ),
            "unknowns": sorted(
                self.unknowns,
                key=lambda row: (row["code"], str(row.get("subject", ""))),
            ),
            "evidence_gaps": [
                {
                    "code": "proposed-runtime-not-observed",
                    "message": "The proposed source bytes have not been executed or admitted as runtime recipe evidence.",
                },
                {
                    "code": "graph-capture-workspace-revision-unbound",
                    "message": "The graph manifest and generic proposal do not bind a shared workspace revision, so capture freshness relative to the retained plan is unknown.",
                },
                {
                    "code": "exact-signature-not-derived",
                    "message": "The plan does not establish the runtime semantic digest, resolved stacks, lookup ordering, or collision behavior.",
                },
                {
                    "code": "non-recipe-acquisition-not-assessed",
                    "message": "World generation, loot, trade, inventory, commands, and other acquisition domains are outside this assessment.",
                },
                {
                    "code": "progression-reachability-not-proven",
                    "message": "Recipe edges, quest definitions, and machine signals do not prove a bypass, dead path, or broken progression.",
                },
                {
                    "code": "task-execution-not-invoked",
                    "message": "Quest task matching, completion, and reward execution were not invoked.",
                },
                {
                    "code": "stoichiometry-throughput-and-chance-not-proven",
                    "message": "Amounts are compared structurally; inventory balance, throughput, reusable inputs, and probability are not simulated.",
                },
                *(
                    gap for gap in self.view._evidence_gaps(role="recipe")
                    if gap["code"] == "graph-projection-limitations"
                ),
            ],
        }
        report["summary"] = {
            "exact_observed_resource_key_count": exact,
            "unresolved_or_ambiguous_resource_count": unresolved,
            "structural_collision_candidate_count": collisions[
                "resolved_request_structure"
            ]["candidate_count"],
            "output_with_existing_producer_count": sum(
                bool(row["existing_producers"]) for row in flow
            ),
            "output_with_existing_consumer_count": sum(
                bool(row["existing_consumers"]) for row in flow
            ),
            "quest_output_reference_count": len(quests["direct_output_requirements"]),
            "output_input_cycle_candidate_count": len(cycles),
            "dead_path_candidate_count": 0,
            "truncated": bool(report["frontiers"]),
        }
        return report


def validate_proposed_recipe_assessment(value: object) -> dict[str, Any]:
    """Validate the V1 adapter result shape and its bounded claim contract.

    This checks the returned interface, not evidence admission or a second
    assessment. Graph admission and the analysis remain with their owners.
    """
    from .view import RecipeHealthError

    def require(condition: bool, label: str) -> None:
        if not condition:
            raise RecipeHealthError(f"invalid proposed-recipe assessment: {label}")

    def object_section(parent: dict[str, Any], key: str) -> dict[str, Any]:
        row = parent.get(key)
        require(type(row) is dict, f"{key} must be an object")
        return cast(dict[str, Any], row)

    def object_rows(parent: dict[str, Any], key: str) -> list[dict[str, Any]]:
        rows = parent.get(key)
        require(type(rows) is list and all(type(row) is dict for row in rows), f"{key} must contain objects")
        return cast(list[dict[str, Any]], rows)

    require(type(value) is dict, "report must be an object")
    report = cast(dict[str, Any], value)
    require(report.get("format") == PROPOSED_ASSESSMENT_FORMAT, "format changed")
    require(type(report.get("schema_version")) is int and report["schema_version"] == 1, "schema version changed")
    sections = {
        key: object_section(report, key)
        for key in ("context", "proposal", "scenario", "analysis_model", "bounds", "resolution", "collision_assessment", "progression_signals", "summary")
    }
    rows = {
        key: object_rows(report, key)
        for key in ("output_flow", "dependency_cycle_candidates", "frontiers", "unknowns", "evidence_gaps")
    }
    context = sections["context"]
    require(context.get("context_type") == "categorical-graph-v2", "context is not an observed graph")
    for key in ("root", "graph_set_id"):
        _text(context.get(key), f"assessment context {key}")
    _validate_proposal(sections["proposal"])
    scenario = sections["scenario"]
    require(
        scenario.get("kind") == "add-owner-validated-proposed-recipe"
        and scenario.get("source_plan_validation") == "required-upstream"
        and scenario.get("proposed_source_bytes_observed_runtime") is False
        and scenario.get("existing_graph_mutated") is False,
        "scenario exceeds the owner-validated ADD claim boundary",
    )
    model = sections["analysis_model"]
    require(
        model.get("kind") == "bounded-proposed-recipe-structural-review"
        and model.get("claim_boundary") == "observed finite recipe and definition structure; not player reachability or runtime lookup behavior",
        "analysis claim boundary changed",
    )
    for key in ("dead_path_rule", "shortcut_rule"):
        _text(model.get(key), f"assessment {key}")
    bounds = sections["bounds"]
    _bound(bounds.get("max_depth"), "maximum depth", minimum=1, maximum=12)
    maximum = _bound(bounds.get("max_nodes"), "maximum nodes", minimum=10, maximum=2_000)
    _bound(bounds.get("visited_node_count"), "visited nodes", minimum=0, maximum=maximum)
    for key in ("item_inputs", "fluid_inputs", "item_outputs", "fluid_outputs"):
        object_rows(sections["resolution"], key)
    collision = sections["collision_assessment"]
    signature = object_section(collision, "exact_runtime_signature")
    require(signature.get("status") == "unavailable-before-runtime-observation" and signature.get("semantic_sha256") is None and signature.get("collision_count") is None, "runtime signature claim changed")
    require(object_rows(signature, "candidates") == [], "runtime signature candidates are not observed")
    structural = object_section(collision, "resolved_request_structure")
    object_rows(structural, "candidates")
    _bound(structural.get("candidate_count"), "structural candidates", minimum=0, maximum=2_147_483_647)
    for output in rows["output_flow"]:
        object_section(output, "output")
        for key in ("existing_producers", "existing_consumers"):
            object_rows(output, key)
    progression = sections["progression_signals"]
    dead_path = object_section(progression, "dead_path_assessment")
    require(dead_path.get("status") == "no-removal-modeled" and type(dead_path.get("candidate_count")) is int and dead_path["candidate_count"] == 0, "ADD assessment cannot claim dead paths")
    object_rows(object_section(progression, "quest_signals"), "direct_output_requirements")
    object_section(progression, "energy_and_machine_signals")
    summary = sections["summary"]
    for key in ("exact_observed_resource_key_count", "unresolved_or_ambiguous_resource_count", "structural_collision_candidate_count", "output_with_existing_producer_count", "output_with_existing_consumer_count", "quest_output_reference_count", "output_input_cycle_candidate_count", "dead_path_candidate_count"):
        _bound(summary.get(key), key, minimum=0, maximum=2_147_483_647)
    require(summary["dead_path_candidate_count"] == 0, "ADD summary cannot claim dead paths")
    require(type(summary.get("truncated")) is bool, "summary truncation must be explicit")
    for row in rows["frontiers"]:
        _text(row.get("kind"), "assessment frontier kind")
    for key in ("unknowns", "evidence_gaps"):
        for row in rows[key]:
            _text(row.get("code"), f"assessment {key} code")
            _text(row.get("message"), f"assessment {key} message")
    require({"proposed-runtime-not-observed", "progression-reachability-not-proven"} <= {row["code"] for row in rows["evidence_gaps"]}, "required evidence gaps missing")
    return report


def assess_proposed_recipe(
    view: "GraphRecipeHealthView",
    proposal: Mapping[str, Any],
    *,
    max_depth: int = 4,
    max_nodes: int = 500,
) -> dict[str, Any]:
    """Compare one owner-validated recipe-plan projection with a verified graph."""

    from .view import GraphRecipeHealthView, RecipeHealthError

    if not isinstance(view, GraphRecipeHealthView):
        raise RecipeHealthError(
            "proposed recipe assessment requires an explicit verified categorical V2 graph"
        )
    reviewed = _validate_proposal(proposal)
    max_depth = _bound(max_depth, "maximum depth", minimum=1, maximum=12)
    max_nodes = _bound(max_nodes, "maximum nodes", minimum=10, maximum=2_000)
    return _Builder(
        view,
        reviewed,
        max_depth=max_depth,
        max_nodes=max_nodes,
    ).build()


__all__ = ["PROPOSED_ASSESSMENT_FORMAT", "assess_proposed_recipe", "validate_proposed_recipe_assessment"]
