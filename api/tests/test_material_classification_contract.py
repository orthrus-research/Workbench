from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from copy import deepcopy
import hashlib
import json
import unittest

from workbench_api.material_classification import (
    MaterialClassificationPolicy,
    MaterialPolicyValidationError,
    admit_material_classification_policy,
    material_classification_policy_from_v1,
)


class MaterialClassificationContractTests(unittest.TestCase):
    def policy(self, **changes: object) -> MaterialClassificationPolicy:
        fields = {
            "policy_id": "fixture:café",
            "platform_profile": "fixture:profile",
            "admitted_material_adapters": ("fixture",),
            "base_property_keys": ("dust",),
            "capability_property_keys": ("ore",),
            "property_dependencies": (("ore", ("dust",)),),
        }
        return MaterialClassificationPolicy(**{**fields, **changes})

    def test_preserves_existing_policy_bytes_and_digest(self) -> None:
        # Captured from the original Material Semantics contract before moving
        # the type. Non-ASCII text must remain literal UTF-8 in these V1 bytes.
        expected = (
            '{"admitted_material_adapters":["fixture"],"flag_categories":{},'
            '"flag_dependencies":{},"flag_property_requirements":{},'
            '"generation_constraint_kind":null,"platform_profile":"fixture:profile",'
            '"policy_id":"fixture:café","property_any_dependencies":{},'
            '"property_classes":{"base":["dust"],"capability":["ore"]},'
            '"property_dependencies":{"ore":["dust"]},'
            '"property_fallback_dependencies":{},"property_implied_flags":{},'
            '"property_incompatibilities":[]}'
        ).encode("utf-8")
        policy = self.policy()
        actual = json.dumps(
            policy.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(expected, actual)
        self.assertEqual(
            "7a0ec129b6a689a94d73b705fcf51548c163a1d37aa28db41908235c1400dc3f",
            policy.sha256,
        )
        self.assertNotIn("api_version", policy.to_dict())
        self.assertNotEqual(policy.sha256, replace(policy, policy_id="changed").sha256)

    def test_input_and_output_mutation_cannot_change_policy(self) -> None:
        adapters = ["fixture"]
        required = ["dust"]
        dependencies = [["ore", required]]
        policy = self.policy(
            admitted_material_adapters=adapters,
            property_dependencies=dependencies,
        )
        original_digest = policy.sha256
        adapters.append("later")
        required.append("later")
        dependencies.append(["later", []])
        exported = policy.to_dict()
        exported["property_dependencies"]["ore"].append("later")
        self.assertEqual(original_digest, policy.sha256)
        with self.assertRaises(FrozenInstanceError):
            policy.policy_id = "changed"

    def test_existing_structural_refusals_remain_explicit(self) -> None:
        cases = (
            ({"policy_id": ""}, "policy ID must be a non-empty string"),
            ({"platform_profile": ""}, "platform profile must be a non-empty string"),
            ({"admitted_material_adapters": "fixture"}, "sequence of names"),
            ({"base_property_keys": ("dust", "dust")}, "unique and canonically sorted"),
            ({"base_property_keys": ("ore",)}, "property keys overlap"),
            ({"property_dependencies": (("ore", ("",)),)}, "invalid name"),
            ({"property_dependencies": (("z", ()), ("a", ()))}, "unique, canonically sorted keys"),
            ({"property_dependencies": (("ore", ()), ("ore", ()))}, "unique, canonically sorted keys"),
            ({"property_fallback_dependencies": (("ore", ("dust",)),)}, "must select from the alternatives"),
            ({"property_incompatibilities": (("dust", "dust"),)}, "incompatible with itself"),
            ({"property_incompatibilities": (("dust", "ore"), ("dust", "ore"))}, "unique and sorted"),
            ({"generation_constraint_kind": ""}, "constraint kind must be a non-empty string"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(MaterialPolicyValidationError, message):
                    self.policy(**changes)

    def test_rejects_incompatible_api_version_without_changing_positional_inputs(self) -> None:
        policy = MaterialClassificationPolicy("id", "platform", (), (), ())
        self.assertEqual(1, policy.api_version)
        for version in (0, 2, True, "1"):
            with self.subTest(version=version):
                with self.assertRaisesRegex(MaterialPolicyValidationError, "API version"):
                    self.policy(api_version=version)

    def test_admission_revalidates_and_snapshots_producer_data(self) -> None:
        class ProducerPolicy(MaterialClassificationPolicy):
            def to_dict(self) -> dict:
                return {"unvalidated": True}

            @property
            def sha256(self) -> str:
                return "wrong"

        original = self.policy()
        producer = ProducerPolicy(
            original.policy_id,
            original.platform_profile,
            original.admitted_material_adapters,
            original.base_property_keys,
            original.capability_property_keys,
            property_dependencies=original.property_dependencies,
        )
        admitted = admit_material_classification_policy(producer)
        self.assertIs(type(admitted), MaterialClassificationPolicy)
        self.assertEqual(original.sha256, admitted.sha256)
        object.__setattr__(producer, "base_property_keys", ("ore",))
        with self.assertRaisesRegex(MaterialPolicyValidationError, "overlap"):
            admit_material_classification_policy(producer)
        self.assertEqual(original.sha256, admitted.sha256)

    def test_legacy_snapshot_reconstructs_validated_policy(self) -> None:
        original = self.policy()

        class LegacyPolicy:
            def to_dict(self):
                return original.to_dict()

            @property
            def sha256(self):
                return original.sha256

        admitted = admit_material_classification_policy(LegacyPolicy())
        self.assertIs(type(admitted), MaterialClassificationPolicy)
        self.assertEqual(original, admitted)
        self.assertEqual(
            original,
            material_classification_policy_from_v1(
                original.to_dict(), expected_sha256=original.sha256
            ),
        )

    def test_legacy_snapshot_refuses_changed_digest_version_shape_and_order(self) -> None:
        original = self.policy()
        payload = original.to_dict()

        def digest(value):
            return hashlib.sha256(json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()

        mutations = (
            (lambda p: p.update(api_version=1), "unexpected fields"),
            (lambda p: p["property_classes"].update(other=[]), "invalid property classes"),
            (lambda p: p.update(admitted_material_adapters="fixture"), "must be arrays"),
            (lambda p: p.update(flag_categories={"flag": "category"}), "must map to arrays"),
            (lambda p: p.update(property_incompatibilities=[["dust"]]), "must be pairs"),
            (lambda p: p.update(property_incompatibilities=[["ore", "dust"]]), "not canonical"),
            (lambda p: p.update(property_dependencies={"z": [], "a": []}), "canonically sorted"),
            (lambda p: p["property_classes"].update(base=["ore"]), "overlap"),
        )
        for mutate, message in mutations:
            changed = deepcopy(payload)
            mutate(changed)
            with self.subTest(message=message):
                with self.assertRaisesRegex(MaterialPolicyValidationError, message):
                    material_classification_policy_from_v1(changed, expected_sha256=digest(changed))
        with self.assertRaisesRegex(MaterialPolicyValidationError, "digest"):
            material_classification_policy_from_v1(payload, expected_sha256="0" * 64)

        class IncompatiblePolicy:
            api_version = 2

            def to_dict(self):
                raise AssertionError("must not invoke an incompatible provider")

        with self.assertRaisesRegex(MaterialPolicyValidationError, "API version"):
            admit_material_classification_policy(IncompatiblePolicy())

        class BrokenPolicy:
            def to_dict(self):
                raise RuntimeError("provider failed")

        with self.assertRaisesRegex(MaterialPolicyValidationError, "legacy V1 policy"):
            admit_material_classification_policy(BrokenPolicy())


if __name__ == "__main__":
    unittest.main()
