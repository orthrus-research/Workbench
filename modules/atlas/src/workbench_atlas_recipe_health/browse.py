"""Paged, exact neighbors for interactive recipe evidence navigation."""

from .view import GraphRecipeHealthView, RecipeHealthError, _node_summary, _text, _limit


def browse_recipe_evidence(view, selection_id, *, offset=0, limit=50, expected_graph=None):
    """Read one neighborhood without expanding a prerequisite closure.

    Input selectors remain nodes. Accepted alternatives and observed
    representatives keep their distinct relations and captured properties.
    Paging limits the presentation, never the underlying graph or route model.
    """
    if not isinstance(view, GraphRecipeHealthView):
        raise RecipeHealthError("relationship browsing requires a captured graph; source search has no observed relationships")
    if expected_graph is not None and expected_graph != view.manifest["graph_set_id"]:
        raise RecipeHealthError("Atlas graph changed; reopen the graph and search again")
    if type(offset) is not int or offset < 0:
        raise RecipeHealthError("browse offset must be a nonnegative integer")
    limit = _limit(limit)
    selected = view._node(_text(selection_id, "browse selection"))
    if selected is None:
        raise RecipeHealthError("browse selection does not exist in this graph")
    connection = view.query.connection
    node_key = connection.execute("SELECT node_key FROM nodes WHERE id=?", (selection_id,)).fetchone()[0]
    # A self-reference is displayed once in each direction, with separate page
    # identities. Stable edge keys preserve repeated captured relationships.
    joined = (
        "SELECT e.edge_key,0 AS direction FROM edges e WHERE e.source_node=? "
        "UNION ALL SELECT e.edge_key,1 AS direction FROM edges e WHERE e.target_node=?"
    )
    total = connection.execute("SELECT count(*) FROM (" + joined + ")", (node_key, node_key)).fetchone()[0]
    if offset > total:
        raise RecipeHealthError("browse offset is beyond the observed neighborhood")
    rows = connection.execute(
        "SELECT e.relation,s.id,t.id,e.semantic_key,e.properties_json,e.evidence_json,p.direction "
        "FROM (" + joined + ") p JOIN edges e ON e.edge_key=p.edge_key "
        "JOIN nodes s ON s.node_key=e.source_node JOIN nodes t ON t.node_key=e.target_node "
        "ORDER BY p.direction,e.relation,e.edge_key LIMIT ? OFFSET ?",
        (node_key, node_key, limit, offset),
    )
    links = []
    for row in rows:
        edge = view.query._edge_row(row[:6])
        direction = "outgoing" if row[6] == 0 else "incoming"
        target = view._node(edge["target"] if row[6] == 0 else edge["source"])
        if target is None:
            raise RecipeHealthError("relationship refers to a missing node")
        links.append({"direction": direction, "relationship": edge, "node": _node_summary(target)})
    end = offset + len(links)
    return {"format": "workbench-atlas-recipe-browse-v1", "schema_version": 1,
            "context": view.describe(), "selection": _node_summary(selected),
            "page": {"offset": offset, "limit": limit, "total": total,
                     "next_offset": end if end < total else None}, "links": links,
            "evidence_gaps": view._evidence_gaps(role="recipe" if selected["kind"] == "gt-recipe" else "target"),
            "scope": "One observed neighborhood. This is not a complete prerequisite route. Craftability, machine access and starting inventory are unknown."}
