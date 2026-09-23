"""Current-only construction boundary for the Supersymmetry source index."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[5]
PROFILE_SOURCE = ROOT / "profiles/packs/supersymmetry/atlas/src"
ATLAS_SOURCE = ROOT / "modules/atlas/src"
sys.path[:0] = [str(PROFILE_SOURCE), str(ATLAS_SOURCE)]

import atlas_source_index  # noqa: E402
from workbench_api.profile_extensions import require_profile_extension, ProfileExtensionError
from workbench_api.profiles import profile_scope
from workbench_profile_supersymmetry import provenance_primitives


class AtlasSourceIndexBoundaryTests(unittest.TestCase):
    def test_native_primitive_adapter_preserves_original_validation(self) -> None:
        adapter = require_profile_extension("workbench.provenance_primitives", "supersymmetry")
        self.assertIs(provenance_primitives, adapter)
        with self.assertRaisesRegex(atlas_source_index.AtlasSourceIndexError, "schema failed"):
            adapter.validate_static_primitives({}, {}, resolver=object())
        source, mutations, resolver = {}, {}, object()
        with patch.object(adapter, "validate_index") as validate_index, patch.object(
            adapter, "validate_extraction"
        ) as validate_mutations:
            adapter.validate_static_primitives(source, mutations, resolver=resolver)
            validate_index.assert_called_once_with(source, resolver)
            validate_mutations.assert_called_once_with(mutations, source, resolver)
        with profile_scope(disabled=("supersymmetry",)):
            with self.assertRaises(ProfileExtensionError):
                require_profile_extension("workbench.provenance_primitives", "supersymmetry")

    def test_resolver_uses_the_single_v3_source_lock(self) -> None:
        resolver = atlas_source_index.SourceResolver(
            pack_root=Path("/nonexistent-pack-source"),
            source_root=Path("/nonexistent-source-cache"),
        )
        self.assertEqual("supersymmetry-legacy-forge", resolver.source_lock_id)
        self.assertEqual(
            {
                "SRC-FORGE",
                "SRC-GREGICALITY-MULTIBLOCKS",
                "SRC-GROOVYSCRIPT",
                "SRC-GTCEU",
                "SRC-PACK",
                "SRC-SUPERCRITICAL",
                "SRC-SUSYCORE",
            },
            set(resolver.bindings),
        )

    def test_generation_is_pack_source_only(self) -> None:
        self.assertEqual(
            {"config_paths", "pack_root", "source_root"},
            set(inspect.signature(atlas_source_index.generate).parameters),
        )
        options = atlas_source_index.parser().parse_args(["--write"])
        self.assertFalse(hasattr(options, "java_index"))
        self.assertFalse(hasattr(options, "pack_only"))
        self.assertFalse(hasattr(options, "sources"))

    def test_v1_java_rows_remain_reopenable_without_a_private_generator(self) -> None:
        self.assertTrue(callable(atlas_source_index.index_java_rows))
        source = Path(atlas_source_index.__file__).read_text(encoding="utf-8")
        self.assertNotIn("index_upstream_java", source)

    def test_line_config_entry_retains_escaped_key_and_exact_utf8_span(self) -> None:
        content = "group {\nS:key~/part= Ω\n}\n".encode("utf-8")
        binding = atlas_source_index.SourceBinding(
            source_lock_id="test-lock",
            source_id="SRC-PACK",
            repository="test-repository",
            revision="a" * 40,
            tree="b" * 40,
            git_root=Path("/unused"),
        )

        class Resolver:
            def read_bytes(self, source_id: str, path: str) -> bytes:
                observed = (source_id, path)
                if observed != ("SRC-PACK", "config/example.cfg"):
                    raise AssertionError(observed)
                return content

            def binding(self, source_id: str):
                if source_id != "SRC-PACK":
                    raise AssertionError(source_id)
                return binding

        spans, selections = atlas_source_index.line_config_entries(
            Resolver(), "SRC-PACK", "config/example.cfg"
        )
        self.assertEqual(1, len(spans))
        self.assertEqual("config-key:/group/key~0~1part", spans[0]["identity"]["symbol"])
        identity = spans[0]["identity"]
        self.assertEqual(2, identity["line_start"])
        self.assertEqual(b"\xce\xa9", content[identity["byte_start"] : identity["byte_end"] + 1])
        self.assertEqual("/group/key~0~1part", selections[0]["key_path"])
        self.assertEqual("Ω", selections[0]["selected_value"])

if __name__ == "__main__":
    unittest.main()
