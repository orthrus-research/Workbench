"""Exact package and fixture input candidates do not rewrite V3 shares."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest import skipIf
from unittest.mock import patch
from zipfile import ZipFile

from workbench_api.profiles import Profile, ProfileStatus

from workbench_core.environment_input_candidates import (
    build_input_candidate, recheck_input_candidate, validate_input_candidate,
)
from workbench_core.environment_reconstruction import (
    ReconstructionError, build_share,
)
from workbench_core.user_preferences import register_workspace

from test_environment_reconstruction import SOURCE_SUITE, _environment, _suite


def _wheel(path: Path) -> Path:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "workbench_demo-0.1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: workbench-demo\nVersion: 0.1.0\n"
            "Requires-Python: >=3.12\n",
        )
        archive.writestr(
            "workbench_demo-0.1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(
            "workbench_demo-0.1.0.dist-info/entry_points.txt",
            "[workbench.modules]\ndemo = demo:module\n",
        )
    return path


class _FixtureOwner:
    def __init__(self, root: Path):
        self.root = root
        digest = "sha256:" + sha256(b"fixture tree\n").hexdigest()
        self.lock = {
            "declaration_id": "fixture.demo",
            "declared_values": {"tree_digest": digest, "identity": {"digest": digest}},
        }
        self.inputs = []
        for kind, name in (
            ("fixture-owner-lock", "fixture-lock.json"),
            ("fixture-owner-schema", "fixture-schema.json"),
            ("profile-preflight-tool", "fixture-tool.py"),
        ):
            path = root / "profiles/platforms/cleanroom" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((kind + "\n").encode("utf-8"))
            self.inputs.append({
                "kind": kind, "path": path,
                "display_path": path.relative_to(root).as_posix(),
            })

    def read_owner_lock(self):
        return deepcopy(self.lock)

    def validate_owner_lock(self, value):
        if value != self.lock:
            raise ValueError("owner fixture lock changed")
        return value

    def source_inputs(self):
        return tuple(self.inputs)


class EnvironmentInputCandidateTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        suite = _suite(self.root / "suite")
        profile = suite / "profiles/packs/supersymmetry/profile.yaml"
        original = profile.read_text(encoding="utf-8")
        changed = original.replace(
            "    maturity: experimental\n",
            "    source_lock: source-locks/legacy-forge/source-lock.json\n"
            "    maturity: experimental\n", 1,
        )
        self.assertNotEqual(original, changed)
        profile.write_text(changed, encoding="utf-8")
        source = SOURCE_SUITE / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
        destination = suite / "profiles/packs/supersymmetry/source-locks/legacy-forge/source-lock.json"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(source.read_bytes())
        workspace = self.root / "workspace"
        workspace.mkdir()
        environment = _environment(self.root / "user")
        register_workspace("pack", str(workspace), environment=environment)
        self.share = build_share(
            suite, "pack", environment=environment,
            bind_project_source_lock=True, bind_managed_tools=True,
        )
        self.wheel = _wheel(self.root / "workbench_demo-0.1.0-py3-none-any.whl")
        self.owner = _FixtureOwner(self.root)
        self.identity = {
            "profile_id": "cleanroom", "group": "workbench.workspace_home_fixtures",
            "module": "fixture_owner", "distribution": "workbench-profile-cleanroom",
            "version": "0.1.1", "api_version": 1,
            "sha256": "a" * 64, "size": 100, "package_source_sha256": "b" * 64,
        }
        profile_owner = ProfileStatus(
            "cleanroom", "workbench-profile-cleanroom", "0.1.1", "available",
            profile=Profile(
                "cleanroom", "platform", suite / "profiles/platforms/cleanroom",
                {"profile": "provisional.yaml"},
            ),
        )
        admitted = patch(
            "workbench_core.environment_input_candidates.profile_status",
            return_value=(profile_owner,),
        )
        provider = patch(
            "workbench_core.environment_input_candidates.require_profile_extension",
            return_value=self.owner,
        )
        identity = patch(
            "workbench_core.environment_input_candidates.profile_extension_identity",
            return_value=self.identity,
        )
        provider.start()
        identity.start()
        admitted.start()
        self.addCleanup(provider.stop)
        self.addCleanup(identity.stop)
        self.addCleanup(admitted.stop)

    def test_candidate_binds_exact_share_wheel_and_owner_fixture_without_local_paths(self) -> None:
        original = deepcopy(self.share)
        candidate = build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="cleanroom")
        self.assertEqual(original, self.share)
        self.assertEqual("workbench-environment-input-candidate-v1", candidate["format"])
        self.assertEqual(self.share["share_id"], candidate["share_id"])
        self.assertEqual(["demo"], candidate["packages"][0]["module_ids"])
        self.assertEqual(self.owner.lock["declared_values"]["tree_digest"],
                         candidate["profile_fixture"]["tree_digest"])
        self.assertNotIn(str(self.root), json.dumps(candidate))
        self.assertEqual(candidate, validate_input_candidate(self.share, candidate))
        self.assertEqual(candidate, recheck_input_candidate(
            self.share, candidate, wheels=(self.wheel,), profile_owner_id="cleanroom",
        ))

    def test_recheck_refuses_wheel_and_fixture_drift(self) -> None:
        candidate = build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="cleanroom")
        with ZipFile(self.wheel, "a") as archive:
            archive.writestr("demo/changed.py", b"changed\n")
        with self.assertRaisesRegex(ReconstructionError, "changed"):
            recheck_input_candidate(self.share, candidate, wheels=(self.wheel,), profile_owner_id="cleanroom")
        self.wheel.unlink()
        _wheel(self.wheel)
        fixture_source = self.owner.inputs[0]["path"]
        fixture_source.write_bytes(b"changed owner source\n")
        with self.assertRaisesRegex(ReconstructionError, "changed"):
            recheck_input_candidate(self.share, candidate, wheels=(self.wheel,), profile_owner_id="cleanroom")

    def test_candidate_refuses_other_share_and_repeated_owner(self) -> None:
        candidate = build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="cleanroom")
        changed = deepcopy(candidate)
        changed["share_id"] = "workbench-environment-share:sha256:" + "0" * 64
        with self.assertRaisesRegex(ReconstructionError, "binding"):
            validate_input_candidate(self.share, changed)
        with self.assertRaisesRegex(ReconstructionError, "repeat"):
            build_input_candidate(self.share, wheels=(self.wheel, self.wheel), profile_owner_id="cleanroom")
        altered = deepcopy(candidate)
        altered["packages"][0]["sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ReconstructionError, "identity changed"):
            validate_input_candidate(self.share, altered)

    def test_fixture_owner_failure_and_v2_share_do_not_create_candidate(self) -> None:
        with self.assertRaisesRegex(ReconstructionError, "not admitted"):
            build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="other")
        with patch.object(self.owner, "validate_owner_lock", side_effect=ValueError("invalid tree")):
            with self.assertRaisesRegex(ReconstructionError, "fixture owner"):
                build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="cleanroom")
        older = deepcopy(self.share)
        older["format"] = "workbench-environment-share-v2"
        with self.assertRaises(ReconstructionError):
            build_input_candidate(older, wheels=(self.wheel,), profile_owner_id="cleanroom")

    @skipIf(os.name == "nt", "symlink creation requires a separate Windows host fixture")
    def test_redirected_input_and_changed_platform_document_are_refused(self) -> None:
        linked_wheel = self.root / "linked.whl"
        linked_wheel.symlink_to(self.wheel)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            build_input_candidate(self.share, wheels=(linked_wheel,), profile_owner_id="cleanroom")
        source = self.owner.inputs[0]["path"]
        original = source.with_name("original-lock.json")
        source.rename(original)
        source.symlink_to(original)
        with self.assertRaisesRegex(ReconstructionError, "redirect"):
            build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="cleanroom")
        source.unlink()
        original.rename(source)
        profile = self.root / "suite/profiles/platforms/cleanroom/provisional.yaml"
        profile.write_bytes(profile.read_bytes() + b"# changed\n")
        with self.assertRaisesRegex(ReconstructionError, "platform document differs"):
            build_input_candidate(self.share, wheels=(self.wheel,), profile_owner_id="cleanroom")


if __name__ == "__main__":
    import unittest
    unittest.main()
