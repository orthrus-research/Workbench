import unittest

from workbench_api.checks import observation, validate_observation


class CheckObservationTests(unittest.TestCase):
    def test_owner_neutral_terminal_observations_require_evidence(self):
        value = observation("failure", "example-failure", "Owner observed a failure", [{"log": "reports/example.txt", "line": 2}])
        self.assertEqual(validate_observation(value), value)
        with self.assertRaises(ValueError):
            observation("checkpoint", "done", "Not evidenced")

    def test_malformed_or_escaping_observations_are_rejected(self):
        value = observation("running", "starting", "Waiting")
        for patch in (
            {"state": "passed"}, {"stage": "../escape"}, {"summary": "a\nb"},
            {"evidence": [{"log": "../secret.txt", "line": 1}]},
            {"evidence": [{"log": "logs/one.txt", "line": True}]},
            {"evidence": [{"log": "/logs/one.txt", "line": 1}]},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_observation({**value, **patch})
