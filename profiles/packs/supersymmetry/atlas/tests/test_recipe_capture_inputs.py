"""Developer source declarations remain separate from runtime qualification."""

from copy import deepcopy
import hashlib
import json
import unittest

from workbench_profile_supersymmetry import recipe_capture_inputs as inputs
from workbench_profile_supersymmetry import recipe_graphs
import test_recipe_graph_projection as projection_fixtures


def encoded(value, *, ascii_only=False):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii_only).encode()


def digest(value, *, ascii_only=False):
    return hashlib.sha256(encoded(value, ascii_only=ascii_only)).hexdigest()


def reseal(candidate):
    candidate["id"] = "candidate:sha256:" + digest(
        {key: value for key, value in candidate.items() if key != "id"}, ascii_only=True)
    return candidate


def candidate(*, root="file:///developer/pack", revision="1" * 40, deleted=()):
    # Two ordinary saved files include an untracked recipe on an unreleased branch.
    files = [{"path": path, "mode": 0o100644, "size": len(raw),
              "sha256": hashlib.sha256(raw).hexdigest()} for path, raw in (
        ("config/machines.cfg", b"new_saved_setting=true\n"),
        ("groovy/postInit/new_é_recipe.groovy", b"// locally saved untracked recipe\n"))]
    source = {"root_uri": root, "revision": revision, "dirty": True,
              "file_count": len(files) + len(deleted), "source_sha256": "2" * 64,
              "index_sha256": "3" * 64}
    return reseal({"format": "workbench-saved-candidate-v1", "source": source, "files": files})


def capture_input(source=None, **kwargs):
    return inputs.build_capture_input(source or candidate(),
        platform=deepcopy(inputs.QUALIFIED_PLATFORM),
        runtime_artifacts=deepcopy(inputs.QUALIFIED_ARTIFACTS),
        capture_id="fixture-capture", launch_id="fixture-launch",
        candidate_lock_sha256="c" * 64, adapter_profile_sha256="d" * 64, **kwargs)


class DeveloperRecipeCaptureInputTests(unittest.TestCase):
    def test_unreleased_dirty_source_and_deleted_file_are_bound(self):
        deleted = ["groovy/postInit/removed.groovy"]
        source = candidate(revision="9" * 40, deleted=deleted)
        before = deepcopy(source)
        value = capture_input(source, deleted_paths=deleted)
        self.assertEqual((value["pack_binding_id"], value["platform_binding_id"]),
                         inputs.validate_capture_input(value))
        self.assertEqual(before, value["pack_source"]["candidate"])
        self.assertEqual(deleted, value["pack_source"]["deleted_paths"])
        self.assertEqual(before, source)

    def test_portable_content_identity_retains_original_custody_roots(self):
        linux = capture_input(candidate(root="file:///home/dev/pack"))
        windows = capture_input(candidate(root="file:///C:/Dev%20pack"))
        self.assertNotEqual(linux["pack_source"]["candidate"]["id"],
                            windows["pack_source"]["candidate"]["id"])
        self.assertEqual(linux["pack_binding_id"], windows["pack_binding_id"])
        self.assertEqual(linux["pack_source"]["source_content_sha256"],
                         windows["pack_source"]["source_content_sha256"])
        self.assertEqual("file:///C:/Dev%20pack", windows["pack_source"]["candidate"]["source"]["root_uri"])

    def test_next_saved_change_mode_or_revision_gets_a_new_binding(self):
        baseline = capture_input()
        for change in ("hash", "mode", "revision", "untracked", "deletion"):
            with self.subTest(change=change):
                source = candidate()
                deleted = []
                if change == "hash":
                    source["files"][0]["sha256"] = "f" * 64
                elif change == "mode":
                    source["files"][0]["mode"] = 0o100755
                elif change == "revision":
                    source["source"]["revision"] = "a" * 64
                elif change == "untracked":
                    source["files"].append({"path": "scripts/new.zs", "mode": 0o100644,
                                            "size": 0, "sha256": hashlib.sha256(b"").hexdigest()})
                    source["source"]["file_count"] += 1
                else:
                    removed = source["files"].pop(0)
                    deleted.append(removed["path"])
                value = capture_input(reseal(source), deleted_paths=deleted)
                self.assertNotEqual(baseline["pack_binding_id"], value["pack_binding_id"])
                if change == "revision":
                    self.assertEqual(baseline["pack_source"]["source_content_sha256"], value["pack_source"]["source_content_sha256"])
                else:
                    self.assertNotEqual(baseline["pack_source"]["source_content_sha256"], value["pack_source"]["source_content_sha256"])

    def test_local_commit_candidate_shape_is_supported_without_a_dirty_claim(self):
        source = candidate()
        source["source"].pop("index_sha256")
        source["source"].update(kind="local-git-commit", dirty=False)
        self.assertTrue(capture_input(reseal(source))["pack_binding_id"])

    def test_original_candidate_checksum_and_both_derived_identities_are_checked(self):
        for field in ("candidate", "content", "source_id", "pack_id", "platform_id"):
            with self.subTest(field=field):
                value = capture_input()
                if field == "candidate":
                    value["pack_source"]["candidate"]["files"][0]["sha256"] = "0" * 64
                elif field == "content":
                    value["pack_source"]["source_content_sha256"] = "0" * 64
                elif field == "source_id":
                    value["pack_source"]["id"] = "forged"
                else:
                    value["pack_binding_id" if field == "pack_id" else "platform_binding_id"] = "forged"
                with self.assertRaises(inputs.RecipeCaptureInputError):
                    inputs.validate_capture_input(value)

    def test_unsafe_or_ambiguous_source_paths_are_refused_even_when_resealed(self):
        for path in ("../outside", "/absolute", "C:/drive", "C:drive", "a\\b", "a//b", ".", "a/../b",
                     "a/./b", "a/.Git/config", "a/name.", "a/name ", "a/CON.txt", "a/Lpt9", "a/COM¹", "CONOUT$",
                     "a/file:stream", "a/q?", "a/line\nname"):
            with self.subTest(path=path):
                source = candidate()
                source["files"][0]["path"] = path
                source["files"].sort(key=lambda row: row["path"])
                with self.assertRaises(inputs.RecipeCaptureInputError):
                    capture_input(reseal(source))

    def test_duplicate_prefix_unsorted_and_missing_deletion_paths_are_refused(self):
        for change in ("duplicate", "case_collision", "prefix", "unsorted", "missing", "deleted_collision"):
            with self.subTest(change=change):
                source = candidate()
                deleted = []
                if change == "duplicate":
                    source["files"][1] = deepcopy(source["files"][0])
                elif change == "case_collision":
                    source["files"][1]["path"] = source["files"][0]["path"].upper()
                    source["files"].sort(key=lambda row: row["path"])
                elif change == "prefix":
                    source["files"][1]["path"] = source["files"][0]["path"] + "/child"
                elif change == "unsorted":
                    source["files"].reverse()
                elif change == "missing":
                    source["source"]["file_count"] += 1
                else:
                    source["source"]["file_count"] += 1
                    deleted.append(source["files"][0]["path"])
                with self.assertRaises(inputs.RecipeCaptureInputError):
                    capture_input(reseal(source), deleted_paths=deleted)

    def test_shapes_types_and_source_bounds_are_strict(self):
        for change in ("extra", "bad_hash", "bad_mode", "bool_size", "bad_revision", "bad_uri", "oversized"):
            with self.subTest(change=change):
                source = candidate()
                if change == "extra":
                    source["files"][0]["compatibility"] = True
                elif change == "bad_hash":
                    source["files"][0]["sha256"] = "G" * 64
                elif change == "bad_mode":
                    source["files"][0]["mode"] = 0o120000
                elif change == "bool_size":
                    source["files"][0]["size"] = True
                elif change == "bad_revision":
                    source["source"]["revision"] = "advanced-branch"
                elif change == "bad_uri":
                    source["source"]["root_uri"] = "https://example.invalid/pack"
                else:
                    source["files"][0]["size"] = 64 * 1024**2 + 1
                with self.assertRaises(inputs.RecipeCaptureInputError):
                    capture_input(reseal(source))

    def test_directory_component_collisions_include_live_and_deleted_paths(self):
        for first, second in (("Dir/a", "dir/b"), ("root/Dir/a", "root/dir/b"),
                              ("Dír/a", "Di\u0301r/b"), ("Dir", "dir/a"), ("dir/a", "dir")):
            for deleted_count in (0, 1, 2):
                with self.subTest(first=first, second=second, deleted_count=deleted_count):
                    source = candidate()
                    source["files"][0]["path"] = first
                    source["files"][1]["path"] = second
                    deleted = sorted(row["path"] for row in source["files"][:deleted_count])
                    source["files"] = sorted(source["files"][deleted_count:], key=lambda row: row["path"])
                    with self.assertRaises(inputs.RecipeCaptureInputError):
                        capture_input(reseal(source), deleted_paths=deleted)

    def test_consistent_directory_spelling_accepts_live_and_deleted_siblings(self):
        source = candidate()
        source["files"][0]["path"] = "Dír/a"
        source["files"][1]["path"] = "Dír/b"
        source["source"]["file_count"] += 1
        value = capture_input(reseal(source), deleted_paths=["Dír/c"])
        self.assertEqual(value["pack_binding_id"], inputs.validate_source_binding(value["pack_source"]))

    def test_changed_runtime_java_side_and_claimed_compatibility_are_refused(self):
        for change in ("java", "java_float", "loader", "artifact", "side", "compatibility", "preparation"):
            with self.subTest(change=change):
                value = capture_input()
                if change.startswith("java"):
                    value["platform"]["java_major"] = 21 if change == "java" else 8.0
                elif change == "loader":
                    value["platform"]["loader"] = "cleanroom"
                elif change == "artifact":
                    value["runtime_artifacts"]["susy_core_sha256"] = "f" * 64
                elif change == "side":
                    value["physical_side"] = "client"
                elif change == "compatibility":
                    value["compatibility"] = True
                else:
                    value["observation_preparation"] = {"policy": "compatible", "phase": "any"}
                with self.assertRaises(inputs.RecipeCaptureInputError):
                    inputs.validate_capture_input(value)

    def test_modifying_a_caller_template_cannot_change_the_qualified_policy(self):
        original = deepcopy(inputs.QUALIFIED_PLATFORM)
        try:
            inputs.QUALIFIED_PLATFORM["java_major"] = 25
            with self.assertRaises(inputs.RecipeCaptureInputError):
                capture_input()
        finally:
            inputs.QUALIFIED_PLATFORM.clear()
            inputs.QUALIFIED_PLATFORM.update(original)


class DeveloperRecipeCaptureProjectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = projection_fixtures.FiniteRecipeProjectionTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_new_input_projects_and_retains_custody_and_admission_source(self):
        self.fixture.input_value = capture_input(candidate(revision="f" * 40))
        self.fixture.publish()
        self.fixture.project()
        manifest, _, _ = self.fixture.rows()
        self.assertEqual(self.fixture.input_value, manifest["evidence_binding"]["input_manifest"])
        self.assertEqual(self.fixture.input_value["pack_binding_id"], manifest["scope"]["pack_binding_id"])
        self.assertIn("recipe_capture_inputs.py", manifest["evidence_binding"]["projection_sources"])

    def test_capture_protocol_mismatch_is_refused_after_sealed_capture_read(self):
        self.fixture.input_value = capture_input()
        self.fixture.input_value["candidate_lock_sha256"] = "e" * 64
        self.fixture.publish()
        with self.assertRaisesRegex(recipe_graphs.RecipeGraphProjectionError, "protocol binding"):
            self.fixture.project()
        self.assertFalse(self.fixture.output.exists())

    def test_preparation_declaration_does_not_bypass_required_payload(self):
        self.fixture.input_value = capture_input(observation_preparation={
            "policy": "forge-loli-original-capability-materialization-v1",
            "phase": "before-two-effective-samples"})
        self.fixture.publish()
        with self.assertRaisesRegex(recipe_graphs.RecipeGraphProjectionError, "preparation"):
            self.fixture.project()
        self.assertFalse(self.fixture.output.exists())

    def test_legacy_branch_and_foundation_admission_remain_separate(self):
        self.fixture.publish()
        self.fixture.project()
        manifest, _, _ = self.fixture.rows()
        self.assertNotIn("recipe_capture_inputs.py", manifest["evidence_binding"]["projection_sources"])
        source = deepcopy(recipe_graphs._BRANCH_PACK)
        platform = deepcopy(recipe_graphs._BRANCH_PLATFORM)
        artifacts = deepcopy(recipe_graphs._BRANCH_ARTIFACTS)
        self.assertEqual(inputs.QUALIFIED_PLATFORM, platform)
        self.assertEqual(inputs.QUALIFIED_ARTIFACTS, artifacts)
        branch = {"format": recipe_graphs._BRANCH_INPUT_FORMAT, "pack_source": source,
                  "platform": platform, "runtime_artifacts": artifacts, "physical_side": "dedicated_server",
                  "candidate_lock_sha256": "c" * 64, "adapter_profile_sha256": "d" * 64,
                  "pack_binding_id": "supersymmetry-branch-observation:sha256:" + digest(source),
                  "platform_binding_id": "forge-branch-observation:sha256:" + digest({
                      "platform": platform, "runtime_artifacts": artifacts})}
        self.assertEqual((branch["pack_binding_id"], branch["platform_binding_id"]),
                         recipe_graphs._admit_input(branch, branch))
        branch["pack_source"]["revision"] = "f" * 40
        with self.assertRaises(recipe_graphs.RecipeGraphProjectionError):
            recipe_graphs._admit_input(branch, branch)


if __name__ == "__main__":
    unittest.main()
