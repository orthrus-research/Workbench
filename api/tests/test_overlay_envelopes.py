"""Public overlay envelope host binding behavior."""

from __future__ import annotations

import unittest

from workbench_api.overlay_envelopes import overlay_envelopes, overlay_envelopes_scope


class OverlayEnvelopePortTests(unittest.TestCase):
    def test_unbound_host_refuses_use_and_scope_restores_it(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "no overlay envelope host is bound"):
            overlay_envelopes()
        host = object()
        with overlay_envelopes_scope(host):
            self.assertIs(host, overlay_envelopes())
        with self.assertRaisesRegex(RuntimeError, "no overlay envelope host is bound"):
            overlay_envelopes()


if __name__ == "__main__":
    unittest.main()
