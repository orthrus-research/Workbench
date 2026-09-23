"""Profile-free integrity for retained operation traces and their source links."""


def validate_lifecycle(value, capture):
    if not isinstance(value, dict) or set(value) != {"state", "reasons", "events", "links"}:
        raise ValueError("invalid lifecycle interpretation")
    if value["state"] not in {"complete", "incomplete", "unavailable"}:
        raise ValueError("invalid lifecycle coverage state")
    if not isinstance(value["reasons"], list) or len(value["reasons"]) > 33 or any(not isinstance(row, str) or len(row) > 2000 for row in value["reasons"]):
        raise ValueError("invalid lifecycle limitations")
    if value["state"] != "complete":
        if value["events"] or value["links"] or not value["reasons"]:
            raise ValueError("ineligible trace cannot assert operation/source links")
        return value
    if (not isinstance(capture, dict) or capture.get("state") != "complete" or capture.get("problems") != []
            or value["reasons"] or value["links"] != capture.get("links")
            or not isinstance(value["events"], list) or len(value["events"]) != len(capture.get("events", []))):
        raise ValueError("lifecycle interpretation differs from captured evidence")
    for interpreted, original in zip(value["events"], capture["events"]):
        if {key: row for key, row in interpreted.items() if key != "location"} != original:
            raise ValueError("lifecycle event differs from captured evidence")
        location, source = interpreted.get("location"), original.get("source")
        if (source is None) != (location is None):
            raise ValueError("lifecycle source attribution lacks an observed origin")
        if source is not None and (location["path"] != source["path"] or location["sha256"] != source["sha256"] or location["start"]["line"] != source["line"]):
            raise ValueError("lifecycle location differs from its compiler-bound source")
    return value
