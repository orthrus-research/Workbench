"""Feature export requests require an explicit Core host."""

import unittest
from unittest.mock import patch

from workbench_api import feature_exports as port


class FeatureExportPortTests(unittest.TestCase):
    def test_unbound_host_fails_closed(self) -> None:
        with patch.object(port, "_host", None):
            with self.assertRaises(port.FeatureExportError) as caught:
                port.feature_exports()
        self.assertEqual("feature-export.host", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
