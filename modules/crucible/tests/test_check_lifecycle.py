"""Retained lifecycle integrity needs no installed game/profile implementation."""
from copy import deepcopy
import unittest

from workbench_crucible.check_lifecycle import validate_lifecycle


class CheckLifecycleTests(unittest.TestCase):
    def test_unavailable_cannot_gain_links_or_success_without_raw_evidence(self):
        value = dict(state="unavailable", reasons=["No capture"], events=[], links={})
        self.assertEqual(validate_lifecycle(value, None), value)
        for field, changed in (("links", {"r0": "o0"}), ("events", [{}]), ("state", "complete")):
            with self.assertRaises(ValueError): validate_lifecycle({**value, field: changed}, None)

    def test_observed_source_and_derived_location_must_agree(self):
        source = dict(path="groovy/Source.groovy", sha256="a"*64, line=3)
        event = dict(id="e0", source=source, returned=False)
        capture = dict(state="complete", problems=[], events=[event], links={})
        location = dict(path=source["path"], sha256=source["sha256"], start=dict(line=3))
        value = dict(state="complete", reasons=[], events=[dict(event, location=location)], links={})
        self.assertEqual(validate_lifecycle(value, capture), value)
        for kind in ("path", "hash", "line", "outcome", "origin"):
            broken = deepcopy(value)
            if kind == "path": broken["events"][0]["location"]["path"] = "other.groovy"
            if kind == "hash": broken["events"][0]["location"]["sha256"] = "b"*64
            if kind == "line": broken["events"][0]["location"]["start"]["line"] = 4
            if kind == "outcome": broken["events"][0]["returned"] = True
            if kind == "origin": broken["events"][0]["location"] = None
            with self.assertRaises(ValueError, msg=kind): validate_lifecycle(broken, capture)
