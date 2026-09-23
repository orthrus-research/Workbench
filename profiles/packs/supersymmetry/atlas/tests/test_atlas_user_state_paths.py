"""Supersymmetry Atlas tools keep user data outside the suite checkout."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench_atlas.atlas_provenance_normalizer import (
    default_static_input_path,
    parser as normalization_parser,
)
from workbench_atlas.layout import WORKBENCH_ROOT
from workbench_profile_supersymmetry import (
    pack_mutations, profile, provenance_primitives, source_span_index,
)


class AtlasUserStatePathTests(unittest.TestCase):
    def test_generated_defaults_follow_core_state_and_profile_owns_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "user state"
            with patch.dict(
                os.environ,
                {
                    "WORKBENCH_STATE_ROOT": str(state),
                    "WORKBENCH_CONFIG_HOME": str(Path(temporary) / "config"),
                },
                clear=True,
            ):
                knowledge = state / "atlas/knowledge" / source_span_index.SNAPSHOT_ID
                self.assertEqual(
                    knowledge / "atlas-source-spans.json",
                    source_span_index.default_output_path(),
                )
                self.assertEqual(
                    knowledge / "atlas-source-spans.json",
                    pack_mutations.default_source_index_path(),
                )
                self.assertEqual(
                    knowledge / "atlas-pack-mutations.json",
                    pack_mutations.default_output_path(),
                )
                self.assertEqual(
                    knowledge / "atlas-pack-mutations.json",
                    default_static_input_path("atlas-pack-mutations.json"),
                )
                resolver = source_span_index.SourceResolver()
                self.assertEqual(
                    state / "atlas/sources/legacy-forge/SRC-PACK" / resolver.pack_commit,
                    resolver._bindings["SRC-PACK"].git_root,
                )
                self.assertEqual(
                    profile().resource("source-lock"),
                    source_span_index.SOURCE_LOCK_PATH,
                )
                self.assertFalse(state.exists())
                self.assertNotEqual(WORKBENCH_ROOT / ".workbench/atlas", state / "atlas")

    def test_explicit_legacy_paths_remain_selectable(self) -> None:
        legacy = Path("/old/workbench/.workbench/atlas")
        source = legacy / "sources/legacy-forge"
        index = legacy / "knowledge/snapshot/atlas-source-spans.json"
        mutations = legacy / "knowledge/snapshot/atlas-pack-mutations.json"
        source_options = source_span_index.parser().parse_args(
            ["--check", "--source-root", str(source), "--output", str(index)]
        )
        self.assertEqual(source, source_options.source_root)
        self.assertEqual(index, source_options.output)
        mutation_options = pack_mutations.parser().parse_args(
            [
                "--check", "--source-root", str(source),
                "--source-index", str(index), "--output", str(mutations),
            ]
        )
        self.assertEqual(source, mutation_options.source_root)
        self.assertEqual(index, mutation_options.source_index)
        self.assertEqual(mutations, mutation_options.output)
        normalization_options = normalization_parser().parse_args(
            [
                "static", "--pack-profile", "supersymmetry",
                "--profile", "OFFLINE_ARTIFACT_STATE",
                "--physical-side", "OFFLINE",
                "--source-root", str(source),
                "--source-index", str(index),
                "--mutations", str(mutations),
                "--output", str(legacy / "knowledge/snapshot/normalized.json"),
            ]
        )
        self.assertEqual(source, normalization_options.source_root)
        resolver = provenance_primitives.source_resolver(source_root=source)
        self.assertEqual(
            source / "SRC-PACK" / resolver.pack_commit,
            resolver._bindings["SRC-PACK"].git_root,
        )


if __name__ == "__main__":
    unittest.main()
