from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from workbench_api.profile_extensions import ProfileExtension
from workbench_atlas import atlas_provenance_normalizer as normalizer
from test_atlas_provenance_normalizer import static_inputs, SNAPSHOT, SOURCE_LOCK, SCOPE


class ProvenanceProfileAdapterTests(unittest.TestCase):
    def inputs(self):
        source, extraction = static_inputs()
        bundle = normalizer.empty_runtime_bundle(
            snapshot_id=SNAPSHOT, source_lock_id=SOURCE_LOCK, scopes=[SCOPE]
        )
        return source, extraction, bundle

    def test_explicit_adapter_validates_original_inputs_before_normalization(self):
        source, extraction, bundle = self.inputs()
        validate = Mock()
        adapter = SimpleNamespace(
            PROVENANCE_PRIMITIVES_API_VERSION=1, validate_static_primitives=validate
        )
        resolver = object()
        result = normalizer.normalize(
            source, extraction, bundle, resolver, primitive_adapter=adapter
        )
        validate.assert_called_once_with(source, extraction, resolver=resolver)
        self.assertEqual(result, normalizer._normalize_validated(source, extraction, bundle))
        validate.side_effect = ValueError("original input digest mismatch")
        with patch.object(normalizer, "_normalize_validated") as derive:
            with self.assertRaisesRegex(normalizer.AtlasProvenanceNormalizationError, "digest mismatch"):
                normalizer.normalize(source, extraction, bundle, primitive_adapter=adapter)
            derive.assert_not_called()

    def test_no_implicit_profile_or_incompatible_adapter(self):
        inputs = self.inputs()
        with self.assertRaisesRegex(normalizer.AtlasProvenanceNormalizationError, "explicit pack profile"):
            normalizer.normalize(*inputs)
        for adapter in (SimpleNamespace(), SimpleNamespace(PROVENANCE_PRIMITIVES_API_VERSION=True),
                        SimpleNamespace(PROVENANCE_PRIMITIVES_API_VERSION=2),
                        SimpleNamespace(PROVENANCE_PRIMITIVES_API_VERSION=1)):
            with self.subTest(adapter=adapter):
                with self.assertRaisesRegex(normalizer.AtlasProvenanceNormalizationError, "API 1"):
                    normalizer.normalize(*inputs, primitive_adapter=adapter)

    def test_profile_admission_cannot_be_bypassed_by_absent_or_broken_owner(self):
        for rows in ((), (ProfileExtension("example", "unavailable", reason="disabled"),),
                     (ProfileExtension("example", "available"), ProfileExtension("example", "available")),
                     (ProfileExtension("example", "available", SimpleNamespace(PROFILE_API_VERSION=2)),)):
            with self.subTest(rows=rows), patch(
                "workbench_api.profile_extensions.profile_extensions", return_value=rows
            ):
                with self.assertRaisesRegex(normalizer.AtlasProvenanceNormalizationError, "install and enable"):
                    normalizer.normalize(*self.inputs(), pack_profile="example")

    def test_static_cli_separates_pack_selection_from_evidence_audience(self):
        options = normalizer.parser().parse_args([
            "static", "--pack-profile", "supersymmetry", "--profile", "COMMON_FINAL_STATE",
            "--physical-side", "DEDICATED_SERVER", "--output", "normalized.json",
        ])
        self.assertEqual("supersymmetry", options.pack_profile)
        self.assertEqual("COMMON_FINAL_STATE", options.profile)


if __name__ == "__main__":
    unittest.main()
