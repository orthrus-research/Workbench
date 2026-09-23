"""Profile-free binding of a decision interpretation to retained original evidence."""


def validate_decisions(value, capture):
    if (not isinstance(value, dict) or set(value) != {"state", "reasons", "steps", "queries", "links"}
            or value["state"] not in {"complete", "incomplete", "unavailable"}
            or not isinstance(value["steps"], list) or len(value["steps"]) > 40000
            or not isinstance(value["queries"], list) or len(value["queries"]) > 64
            or not isinstance(value["links"], dict) or len(value["links"]) > 4096
            or not isinstance(value["reasons"], list) or len(value["reasons"]) > 33
            or any(not isinstance(reason, str) or len(reason) > 2000 for reason in value["reasons"])):
        raise ValueError("invalid decision interpretation")
    if value["state"] != "complete":
        if value["steps"] or value["queries"] or value["links"] or not value["reasons"]:
            raise ValueError("ineligible decisions cannot assert causal links")
        return value
    if (not isinstance(capture, dict) or capture.get("state") != "complete" or capture.get("problems") != []
            or value["reasons"] or any(value[key] != capture.get(key) for key in ("steps", "queries", "links"))):
        raise ValueError("decision interpretation differs from captured evidence")
    return value
