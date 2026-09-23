"""Bounded, retained diagnostic presentation; no profile import on reopen.

Owners supply structured differences, source candidates and evidence links.
The shared renderer is sealed with that structure so clients need no domain logic.
"""
import json
import re

from .developer_checks import content_id

FORMAT = "workbench-check-explanation-v1"
MAX_BYTES = 2 * 1024 * 1024


def _text(value, limit=4096):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("invalid bounded explanation text")


def section_text(section):
    lines = [section["title"], *section["notes"]]
    lines.extend(f"{row['label']}: expected {row['expected']}; observed {row['observed']}" for row in section["properties"])
    lines.extend(f"Source ({row['basis']}): {row['label']} — {row['location']['path']}:{row['location']['start']['line']}" for row in section["sources"])
    lines.extend(f"Evidence: {row['log']}:{row['line']}" for row in section["evidence"])
    return "\n".join(lines)


def seal_explanation(summary, sections, limitations):
    sections = [{**row, "text": section_text(row)} for row in sections]
    text = "\n\n".join([summary, *(row["text"] for row in sections), "Limits\n" + "\n".join(limitations)])
    body = {"format": FORMAT, "summary": summary, "sections": sections, "limitations": limitations, "text": text}
    return {**body, "id": content_id("check-explanation", body)}


def validate_explanation(value, expectation, evidence):
    if (not isinstance(value, dict) or set(value) != {"format", "summary", "sections", "limitations", "text", "id"}
            or value["format"] != FORMAT or len(json.dumps(value, allow_nan=False)) > MAX_BYTES
            or not isinstance(value["sections"], list) or not 1 <= len(value["sections"]) <= 322
            or not isinstance(value["limitations"], list) or not 1 <= len(value["limitations"]) <= 16):
        raise ValueError("invalid bounded check explanation")
    _text(value["summary"])
    for note in value["limitations"]:
        _text(note)
    ids = set()
    candidates = {expectation["candidate_id"], expectation["source"]["candidate_id"]}
    for row in value["sections"]:
        if (not isinstance(row, dict) or set(row) != {"id", "title", "notes", "properties", "sources", "evidence", "text"}
                or not isinstance(row["id"], str) or re.fullmatch(r"[a-z0-9-]{1,64}", row["id"]) is None or row["id"] in ids):
            raise ValueError("invalid explanation section")
        ids.add(row["id"])
        _text(row["title"])
        for key, bound in (("notes", 128), ("properties", 512), ("sources", 64), ("evidence", 32)):
            if not isinstance(row[key], list) or len(row[key]) > bound:
                raise ValueError("explanation section exceeds bounds")
        for note in row["notes"]:
            _text(note)
        for field in row["properties"]:
            if not isinstance(field, dict) or set(field) != {"label", "expected", "observed"}:
                raise ValueError("invalid explanation property")
            for text in field.values():
                _text(text)
        if any(ref not in evidence for ref in row["evidence"]):
            raise ValueError("explanation lacks its captured evidence binding")
        for source in row["sources"]:
            if (not isinstance(source, dict) or set(source) != {"label", "basis", "candidate_id", "location"}
                    or source["candidate_id"] not in candidates or not isinstance(source["location"], dict)):
                raise ValueError("explanation source belongs to another candidate")
            _text(source["label"])
            _text(source["basis"])
            location = source["location"]
            if (not isinstance(location.get("path"), str) or not isinstance(location.get("start"), dict)
                    or type(location["start"].get("line")) is not int or location["start"]["line"] < 1):
                raise ValueError("explanation source lacks a readable location")
        # Re-rendering checks both section text and the report, without domain code.
        if row["text"] != section_text(row):
            raise ValueError("explanation text disagrees with its structure")
    if seal_explanation(value["summary"], value["sections"], value["limitations"]) != value:
        raise ValueError("explanation text or identity changed")
    return value
