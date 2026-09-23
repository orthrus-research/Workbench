"""Reuse one verified recipe graph during a serial interactive query session."""
from pathlib import Path
import sys

from workbench_atlas_observations._custody import GraphWitness
from workbench_atlas_observations.session import _frames, _decode, _request_id, _write, MAX_REQUEST_BYTES
from .view import GraphRecipeHealthView, RecipeHealthError
from .browse import browse_recipe_evidence

PREFIX = "workbench-atlas-recipe-session-"


def serve_recipe_session(path, *, input=None, output=None, check_cancelled=None):
    """Core owns the process lifetime; no daemon, persistent worker or new store."""
    cancel = check_cancelled or (lambda: None)
    source = input if input is not None else sys.stdin.buffer
    target = output if output is not None else sys.stdout
    witness = GraphWitness(Path(path))
    with GraphRecipeHealthView(Path(path), check_cancelled=cancel) as view:
        witness.check()
        if witness.manifest != view.manifest:
            raise RecipeHealthError("recipe graph changed during verification")
        graph = view.manifest["graph_set_id"]
        _write(target, {"format": PREFIX + "ready-v1", "schema_version": 1, "state": "ready",
                        "graph_set_id": graph, "context": view.describe(),
                        "operations": ["search", "browse", "close"], "maximum_request_bytes": MAX_REQUEST_BYTES}, cancel, witness.check)
        frames = _frames(source, cancel)
        try:
            for frame in frames:
                value = _decode(frame)
                cancel(); witness.check()
                response = {"format": PREFIX + "response-v1", "schema_version": 1,
                            "request_id": _request_id(value), "graph_set_id": graph}
                try:
                    if (type(value) is not dict or set(value) != {"format", "schema_version", "request_id", "graph_set_id", "operation", "arguments"}
                            or value["format"] != PREFIX + "request-v1" or type(value["schema_version"]) is not int
                            or value["schema_version"] != 1 or _request_id(value) is None or value["graph_set_id"] != graph
                            or type(value["arguments"]) is not dict or value["operation"] not in ("search", "browse", "close")):
                        raise RecipeHealthError("request must bind this exact recipe session and graph")
                    operation, arguments = value["operation"], value["arguments"]
                    required = {"search": {"query"}, "browse": {"selection_id"}, "close": set()}[operation]
                    optional = {"search": {"limit"}, "browse": {"offset", "limit"}, "close": set()}[operation]
                    if not required <= set(arguments) <= required | optional:
                        raise RecipeHealthError("invalid recipe session arguments")
                    if operation == "close":
                        _write(target, {**response, "state": "closed"}, cancel, witness.check)
                        return 0
                    if operation == "search":
                        result = view.search(arguments["query"], limit=arguments.get("limit", 50))
                    else:
                        result = browse_recipe_evidence(view, **arguments, expected_graph=graph)
                    witness.check()
                except (ValueError, TypeError) as error:
                    witness.check()
                    _write(target, {**response, "state": "error", "error": str(error)}, cancel, witness.check)
                    continue
                _write(target, {**response, "state": "complete", "result": result}, cancel, witness.check)
        finally:
            frames.close()
    return 0
