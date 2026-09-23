"""Retained decision binding requires no profile import or game runtime."""
from copy import deepcopy
import unittest

from workbench_crucible.check_decisions import validate_decisions


class CheckDecisionTests(unittest.TestCase):
    def test_unavailable_cannot_gain_decision_links(self):
        value = dict(state="unavailable", reasons=["No decision capture"], steps=[], queries=[], links={})
        self.assertEqual(validate_decisions(value, None), value)
        for key, changed in (("steps", [{}]), ("queries", [{}]), ("links", {"r0": "o0"}), ("state", "complete"), ("reasons", [])):
            with self.assertRaises(ValueError):
                validate_decisions({**value, key: changed}, None)

    def test_derived_decisions_must_equal_the_retained_original(self):
        capture = dict(state="complete", problems=[], steps=[{"id": "d0", "outcome": "observed"}],
                       queries=[{"id": "q0", "selected": "o0"}], links={"r0": "o0"})
        value = {key: deepcopy(capture[key]) for key in ("state", "steps", "queries", "links")} | {"reasons": []}
        self.assertEqual(validate_decisions(value, capture), value)
        for key in ("steps", "queries", "links"):
            changed = deepcopy(value); changed[key] = [] if key != "links" else {}
            with self.assertRaisesRegex(ValueError, "differs from captured"):
                validate_decisions(changed, capture)
