"""Atlas mutable defaults follow Core's selected physical state."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from workbench_atlas.layout import (
    HISTORICAL_SOURCE_LOCK_PATH,
    MODULE_ROOT,
    WORKBENCH_ROOT,
    atlas_knowledge_root,
    atlas_source_root,
    atlas_state_root,
)
from workbench_atlas.atlas_provenance_normalizer import (
    AtlasProvenanceNormalizationError,
    _selected_source_resolver,
)


class AtlasStateLayoutTests(unittest.TestCase):
    def test_source_checkout_uses_external_core_state_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            environment = {"WORKBENCH_CONFIG_HOME": str(base / "configuration")}
            if os.name == "nt":
                environment["LOCALAPPDATA"] = str(base / "state")
                selected = base / "state/Workbench/runtime/atlas"
            else:
                environment["XDG_STATE_HOME"] = str(base / "state")
                selected = base / "state/workbench/runtime/atlas"
            self.assertEqual(selected, atlas_state_root(environment=environment))
            self.assertEqual(
                selected / "sources/legacy-forge",
                atlas_source_root(environment=environment),
            )
            self.assertEqual(
                selected / "knowledge",
                atlas_knowledge_root(environment=environment),
            )
            self.assertNotEqual(WORKBENCH_ROOT / ".workbench/atlas", selected)
            self.assertFalse((base / "state").exists())
            self.assertFalse((base / "configuration").exists())

    def test_explicit_state_root_preserves_one_selected_user_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "user state"
            self.assertEqual(state / "atlas", atlas_state_root(state_root=state))
            self.assertEqual(
                state / "atlas/sources/legacy-forge",
                atlas_source_root(state_root=state),
            )
            self.assertEqual(
                state / "atlas/knowledge",
                atlas_knowledge_root(state_root=state),
            )
            self.assertFalse(state.exists())

    def test_historical_lock_is_a_packaged_atlas_resource(self) -> None:
        self.assertTrue(HISTORICAL_SOURCE_LOCK_PATH.is_relative_to(MODULE_ROOT))
        self.assertNotIn("profiles", HISTORICAL_SOURCE_LOCK_PATH.parts)
        self.assertTrue(HISTORICAL_SOURCE_LOCK_PATH.is_file())

    def test_explicit_source_paths_require_a_selected_profile_resolver(self) -> None:
        self.assertIsNone(
            _selected_source_resolver(object(), source_root=None, pack_root=None)
        )
        with self.assertRaisesRegex(
            AtlasProvenanceNormalizationError,
            "cannot resolve explicit source paths",
        ):
            _selected_source_resolver(
                object(), source_root=Path("/old/sources"), pack_root=None
            )

        class Adapter:
            def source_resolver(self, *, source_root, pack_root):
                return source_root, pack_root

        self.assertEqual(
            (Path("/old/sources"), Path("/old/pack")),
            _selected_source_resolver(
                Adapter(),
                source_root=Path("/old/sources"),
                pack_root=Path("/old/pack"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
