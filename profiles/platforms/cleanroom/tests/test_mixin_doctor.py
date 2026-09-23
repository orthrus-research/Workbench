from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[4]
PROJECT_INTELLIGENCE_SOURCE = ROOT / "modules/project-intelligence/src"
CLEANROOM_SOURCE = ROOT / "profiles/platforms/cleanroom/src"
for source in (PROJECT_INTELLIGENCE_SOURCE, CLEANROOM_SOURCE):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_cleanroom_mixin_doctor import (  # noqa: E402
    DoctorError,
    evaluate_scans,
    inspect_artifact_paths,
    validate_bound_report,
    validate_report,
)
from workbench_project_intelligence import (  # noqa: E402
    ArtifactInput,
    build_topology_receipt,
    canonical_json_bytes,
    scan_artifact_bytes,
)


POLICY_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-policy-v1.json"
)
SCHEMA_PATH = (
    ROOT
    / "profiles/platforms/cleanroom/mixins/cleanroom-mixin-doctor-report-v1.schema.json"
)
TOOL_PATH = ROOT / "profiles/platforms/cleanroom/tools/run_mixin_doctor.py"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def _archive(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _class_with_interface(class_name: str, interface_name: str) -> bytes:
    def utf8(value: str) -> bytes:
        encoded = value.encode("ascii")
        return b"\x01" + struct.pack(">H", len(encoded)) + encoded

    def class_ref(index: int) -> bytes:
        return b"\x07" + struct.pack(">H", index)

    constant_pool = b"".join(
        [
            utf8(class_name),
            class_ref(1),
            utf8("java/lang/Object"),
            class_ref(3),
            utf8(interface_name),
            class_ref(5),
        ]
    )
    return b"".join(
        [
            b"\xca\xfe\xba\xbe",
            struct.pack(">HHH", 0, 52, 7),
            constant_pool,
            struct.pack(">HHHH", 0x0021, 2, 4, 1),
            struct.pack(">H", 6),
            struct.pack(">HHH", 0, 0, 0),
        ]
    )


def _fixture(
    *,
    owner: str = "example_mod",
    target: str = "@env(DEFAULT)",
    package: str | None = "example.mixin",
    min_version: str = "0.8.7",
    compatibility_level: str = "JAVA_8",
    compatibility: dict[str, str] | None = None,
    connector: str | None = None,
    extra: dict[str, bytes] | None = None,
    plugin: str | None = None,
    required_features: list[str] | None = None,
) -> bytes:
    config = {
        "compatibilityLevel": compatibility_level,
        "minVersion": min_version,
        "mixins": ["MixinWorld"],
        "refmap": "mixins.example.refmap.json",
        "required": True,
        "target": target,
    }
    if package is not None:
        config["package"] = package
    if plugin is not None:
        config["plugin"] = plugin
    if required_features is not None:
        config["requiredFeatures"] = required_features
    manifest = [
        "Manifest-Version: 1.0",
        "MixinConfigs: mixins.example.json",
    ]
    if connector is not None:
        manifest.append(f"MixinConnector: {connector}")
    entries = {
        "META-INF/MANIFEST.MF": ("\r\n".join(manifest) + "\r\n\r\n").encode(
            "ascii"
        ),
        "cleanmix_version_compatibility.json": canonical_json_bytes(
            compatibility
            if compatibility is not None
            else {"example.mixin.MixinWorld": "0.6.0"}
        ),
        "example/mixin/MixinWorld.class": b"bounded-mixin-fixture",
        "mcmod.info": canonical_json_bytes([{"modid": owner}]),
        "mixins.example.json": canonical_json_bytes(config),
        "mixins.example.refmap.json": canonical_json_bytes(
            {"data": {"searge": {}}, "mappings": {}}
        ),
    }
    entries.update(extra or {})
    return _archive(entries)


def _assert_schema(test: unittest.TestCase, report: dict) -> None:
    errors = sorted(VALIDATOR.iter_errors(report), key=lambda error: list(error.path))
    test.assertEqual([], [(list(error.path), error.message) for error in errors])


def _bound_fixture(payload: bytes) -> tuple[dict, dict, str, dict]:
    policy_bytes = POLICY_PATH.read_bytes()
    policy = json.loads(policy_bytes)
    policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
    artifact = ArtifactInput(label="example.jar", data=payload)
    topology_receipt = build_topology_receipt([artifact])
    report = evaluate_scans(
        policy=policy,
        policy_sha256=policy_sha256,
        scans=[scan_artifact_bytes(payload, label=artifact.label)],
        topology_receipt=topology_receipt,
    )
    return report, policy, policy_sha256, topology_receipt


def _reidentify_internal_report(report: dict) -> None:
    for finding in report["findings"]:
        material = {
            key: value for key, value in finding.items() if key != "finding_id"
        }
        finding["finding_id"] = (
            "workbench-cleanroom-mixin-doctor-finding:sha256:"
            + hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        )
    report["findings"].sort(key=lambda row: row["finding_id"])
    report["coverage"].sort(key=lambda row: row["rule_id"])
    coverage = report["coverage"]
    findings = report["findings"]
    summary = report["summary"]
    summary.update(
        {
            "coverage_state": (
                "complete"
                if all(row["state"] == "evaluated" for row in coverage)
                else "partial"
            ),
            "evaluated_rule_count": sum(
                row["state"] == "evaluated" for row in coverage
            ),
            "finding_count": len(findings),
            "partial_rule_count": sum(
                row["state"] == "partially-evaluated" for row in coverage
            ),
            "reject_finding_count": sum(
                row["disposition"] == "reject" for row in findings
            ),
            "review_finding_count": sum(
                row["disposition"] == "review" for row in findings
            ),
            "rule_count": len(coverage),
            "unevaluated_rule_count": sum(
                row["state"] == "not-evaluated" for row in coverage
            ),
        }
    )
    dispositions = {row["disposition"] for row in findings}
    if "reject" in dispositions:
        summary["disposition"] = "reject"
    elif "review" in dispositions or summary["coverage_state"] == "partial":
        summary["disposition"] = "review"
    else:
        summary["disposition"] = "accept"
    material = {key: value for key, value in report.items() if key != "report_id"}
    report["report_id"] = (
        "workbench-cleanroom-mixin-doctor-report:sha256:"
        + hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    )


class CleanroomMixinDoctorTests(unittest.TestCase):
    def _inspect(self, *artifacts: tuple[str, bytes]) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths: list[Path] = []
            for name, payload in artifacts:
                path = root / name
                path.write_bytes(payload)
                paths.append(path)
            return inspect_artifact_paths(paths, policy_path=POLICY_PATH)

    def test_bounded_fixture_is_schema_valid_and_review_not_accept(self) -> None:
        report = self._inspect(("example.jar", _fixture()))
        validate_report(report)
        _assert_schema(self, report)
        self.assertEqual(41, report["summary"]["rule_count"])
        self.assertEqual("partial", report["summary"]["coverage_state"])
        self.assertEqual("review", report["summary"]["disposition"])
        self.assertEqual(0, report["summary"]["reject_finding_count"])
        self.assertEqual(7, report["summary"]["unevaluated_rule_count"])
        self.assertEqual(
            "out-of-scope-no-adapter-introduced",
            report["boundaries"]["recurrent_complex_integration_scope"],
        )
        self.assertNotIn(
            "recurrent_complex_adapter_present", report["boundaries"]
        )
        self.assertIn(
            "Recurrent Complex integration and presence are out of scope; "
            "this Doctor introduces no adapter.",
            report["limitations"],
        )
        self.assertIn(
            "cleanroom.mixin.registration.configs",
            {row["rule_id"] for row in report["findings"]},
        )

    def test_forbidden_native_package_is_rejected(self) -> None:
        report = self._inspect(
            (
                "embedded-mixin.jar",
                _fixture(extra={"org/spongepowered/asm/Fake.class": b"duplicate"}),
            )
        )
        self.assertEqual("reject", report["summary"]["disposition"])
        finding = next(
            row
            for row in report["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.embedded.native-toolchain-package"
        )
        self.assertEqual("mixin", finding["observed"]["component"])
        self.assertEqual("reject", finding["disposition"])

    def test_multi_release_native_package_is_also_rejected(self) -> None:
        report = self._inspect(
            (
                "multi-release.jar",
                _fixture(
                    extra={
                        "META-INF/versions/9/org/spongepowered/asm/Fake.class": b"duplicate"
                    }
                ),
            )
        )
        finding = next(
            row
            for row in report["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.embedded.native-toolchain-package"
        )
        self.assertEqual("reject", report["summary"]["disposition"])
        self.assertEqual(
            ["META-INF/versions/9/org/spongepowered/asm/Fake.class"],
            finding["observed"]["sample_paths"],
        )

    def test_owner_collision_and_unknown_selector_are_rejected(self) -> None:
        report = self._inspect(
            ("first.jar", _fixture(owner="a-b", target="@env(NOT_A_PHASE)")),
            ("second.jar", _fixture(owner="a_b")),
        )
        rules = {row["rule_id"] for row in report["findings"]}
        self.assertIn("cleanroom.mixin.owner-id.collision", rules)
        self.assertIn("cleanroom.mixin.config.unknown-environment-selector", rules)
        self.assertEqual("reject", report["summary"]["disposition"])

    def test_missing_mixin_package_is_rejected_as_runtime_orphan(self) -> None:
        report = self._inspect(
            ("orphaned.jar", _fixture(package=None))
        )
        finding = next(
            row
            for row in report["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.class.outside-config-package"
        )
        self.assertEqual("orphaned", finding["observed"]["runtime_resolution"])
        self.assertEqual("reject", report["summary"]["disposition"])

    def test_compatibility_member_key_is_partial_not_false_orphan(self) -> None:
        report = self._inspect(
            (
                "member-metadata.jar",
                _fixture(
                    compatibility={
                        "example.mixin.MixinWorld::handler()V": "0.6.0"
                    }
                ),
            )
        )
        orphan_coverage = next(
            row
            for row in report["coverage"]
            if row["rule_id"] == "cleanroom.mixin.compatibility.orphan-key"
        )
        self.assertEqual("partially-evaluated", orphan_coverage["state"])
        self.assertNotIn(
            "cleanroom.mixin.compatibility.orphan-key",
            {row["rule_id"] for row in report["findings"]},
        )

    def test_invalid_compatibility_is_a_structured_reject_finding(self) -> None:
        report = self._inspect(
            (
                "invalid-compatibility.jar",
                _fixture(
                    compatibility={"example.mixin.MixinWorld": "0.1000.0"}
                ),
            )
        )
        _assert_schema(self, report)
        finding = next(
            row
            for row in report["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.compatibility.invalid-version"
        )
        self.assertEqual("reject", finding["disposition"])
        self.assertEqual("reject", report["summary"]["disposition"])

    def test_cleanmix_numeric_parts_may_have_leading_zeroes(self) -> None:
        report = self._inspect(
            (
                "leading-zero-compatibility.jar",
                _fixture(
                    compatibility={"example.mixin.MixinWorld": "00.7.0"}
                ),
            )
        )
        rules = {row["rule_id"] for row in report["findings"]}
        self.assertIn("cleanroom.mixin.compatibility.above-known-boundary", rules)
        self.assertNotIn("cleanroom.mixin.compatibility.invalid-version", rules)

    def test_runtime_version_number_exact_admission_and_comparison(self) -> None:
        cases = {
            "0.8.7.1": {
                "expected": "cleanroom.mixin.config.unsupported-min-version",
                "absent": "cleanroom.mixin.config.invalid-min-version",
            },
            "0.8.8-BETA": {
                "expected": "cleanroom.mixin.config.unsupported-min-version",
                "absent": "cleanroom.mixin.config.invalid-min-version",
            },
            "000.008.007": {"expected": None, "absent": None},
            "0.8.7-BETA": {"expected": None, "absent": None},
        }
        checked = {
            "cleanroom.mixin.config.invalid-min-version",
            "cleanroom.mixin.config.unsupported-min-version",
        }
        for value, expectation in cases.items():
            with self.subTest(min_version=value):
                report = self._inspect(
                    ("version.jar", _fixture(min_version=value))
                )
                rules = {row["rule_id"] for row in report["findings"]}
                expected = expectation["expected"]
                if expected is None:
                    self.assertTrue(checked.isdisjoint(rules))
                else:
                    self.assertIn(expected, rules)
                    self.assertNotIn(expectation["absent"], rules)

    def test_runtime_version_number_invalid_outcomes_are_explicit(self) -> None:
        cases = {
            "0.8.7.32768": "IllegalArgumentException",
            "not-a-version": "VersionNumber.NONE",
        }
        for value, runtime_equivalent in cases.items():
            with self.subTest(min_version=value):
                report = self._inspect(
                    ("invalid-version.jar", _fixture(min_version=value))
                )
                finding = next(
                    row
                    for row in report["findings"]
                    if row["rule_id"]
                    == "cleanroom.mixin.config.invalid-min-version"
                )
                self.assertEqual("reject", finding["disposition"])
                self.assertEqual(
                    runtime_equivalent,
                    finding["observed"]["runtime_equivalent"],
                )
                self.assertNotIn(
                    "cleanroom.mixin.config.unsupported-min-version",
                    {row["rule_id"] for row in report["findings"]},
                )

    def test_compatibility_level_uses_exact_uppercased_enum_names(self) -> None:
        for value in ("JAVA_1_25", "JAVA_1_8", "JAVA_5", "JAVA_26"):
            with self.subTest(compatibility_level=value):
                report = self._inspect(
                    (
                        "invalid-level.jar",
                        _fixture(compatibility_level=value),
                    )
                )
                finding = next(
                    row
                    for row in report["findings"]
                    if row["rule_id"]
                    == "cleanroom.mixin.config.unsupported-java-level"
                )
                self.assertEqual("reject", finding["disposition"])
                self.assertEqual(
                    "MixinInitialisationError",
                    finding["observed"]["runtime_equivalent"],
                )

        lowercase = self._inspect(
            ("lowercase-level.jar", _fixture(compatibility_level="java_8"))
        )
        self.assertNotIn(
            "cleanroom.mixin.config.unsupported-java-level",
            {row["rule_id"] for row in lowercase["findings"]},
        )

    def test_java_25_is_admitted_but_effective_support_requires_review(self) -> None:
        report = self._inspect(
            ("java-25.jar", _fixture(compatibility_level="JAVA_25"))
        )
        rules = {row["rule_id"] for row in report["findings"]}
        self.assertNotIn("cleanroom.mixin.config.unsupported-java-level", rules)
        self.assertIn(
            "cleanroom.mixin.config.java-level-effective-support", rules
        )
        finding = next(
            row
            for row in report["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.config.java-level-effective-support"
        )
        self.assertEqual("review", finding["disposition"])
        self.assertEqual(
            "requires-jre-and-asm-observation",
            finding["observed"]["runtime_support"],
        )

    def test_required_features_follow_exact_runtime_normalization_and_states(self) -> None:
        admitted = self._inspect(
            (
                "active-feature.jar",
                _fixture(required_features=[" unsafe_injection "]),
            )
        )
        self.assertFalse(
            {
                "cleanroom.mixin.config.required-feature-unavailable",
                "cleanroom.mixin.config.required-feature-runtime-state",
            }
            & {row["rule_id"] for row in admitted["findings"]}
        )

        unknown = self._inspect(
            ("unknown-feature.jar", _fixture(required_features=[" not_a_feature "]))
        )
        finding = next(
            row
            for row in unknown["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.config.required-feature-unavailable"
        )
        self.assertEqual("NOT_A_FEATURE", finding["observed"]["normalized_feature_id"])
        self.assertEqual("reject", unknown["summary"]["disposition"])

        conditional = self._inspect(
            (
                "conditional-feature.jar",
                _fixture(required_features=["injectors_in_interface_mixins"]),
            )
        )
        finding = next(
            row
            for row in conditional["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.config.required-feature-runtime-state"
        )
        coverage = next(
            row
            for row in conditional["coverage"]
            if row["rule_id"] == finding["rule_id"]
        )
        self.assertEqual("partially-evaluated", coverage["state"])
        self.assertEqual("review", finding["disposition"])

    def test_plugin_dependency_and_direct_interface_truth_remain_bounded(self) -> None:
        missing = self._inspect(
            ("missing-plugin.jar", _fixture(plugin="example.Plugin"))
        )
        missing_finding = next(
            row
            for row in missing["findings"]
            if row["rule_id"] == "cleanroom.mixin.plugin.class-missing"
        )
        self.assertEqual("not-observed", missing_finding["observed"]["dependency_closure"])
        self.assertEqual("review", missing_finding["disposition"])
        self.assertEqual(0, missing["summary"]["reject_finding_count"])

        valid = self._inspect(
            (
                "valid-plugin.jar",
                _fixture(
                    plugin="example.Plugin",
                    extra={
                        "example/Plugin.class": _class_with_interface(
                            "example/Plugin",
                            "org/spongepowered/asm/mixin/extensibility/IMixinConfigPlugin",
                        )
                    },
                ),
            )
        )
        self.assertNotIn(
            "cleanroom.mixin.plugin.wrong-interface",
            {row["rule_id"] for row in valid["findings"]},
        )

        wrong_direct = self._inspect(
            (
                "wrong-direct-plugin.jar",
                _fixture(
                    plugin="example.Plugin",
                    extra={
                        "example/Plugin.class": _class_with_interface(
                            "example/Plugin", "java/io/Serializable"
                        )
                    },
                ),
            )
        )
        wrong_finding = next(
            row
            for row in wrong_direct["findings"]
            if row["rule_id"] == "cleanroom.mixin.plugin.wrong-interface"
        )
        self.assertEqual("review", wrong_finding["disposition"])
        self.assertEqual(
            "superclass-and-dependency-closure-required",
            wrong_finding["observed"]["resolution"],
        )

    def test_connector_missing_and_interface_retention_are_explicit(self) -> None:
        missing = self._inspect(
            ("missing-connector.jar", _fixture(connector="example.Connector"))
        )
        missing_finding = next(
            row
            for row in missing["findings"]
            if row["rule_id"]
            == "cleanroom.mixin.registration.connector-class-missing"
        )
        self.assertEqual("review", missing_finding["disposition"])
        self.assertEqual("not-observed", missing_finding["observed"]["dependency_closure"])

        for interface_name in (
            "org/spongepowered/asm/mixin/connect/IMixinConnector",
            "java/io/Serializable",
        ):
            with self.subTest(interface=interface_name):
                report = self._inspect(
                    (
                        "connector.jar",
                        _fixture(
                            connector="example.Connector",
                            extra={
                                "example/Connector.class": _class_with_interface(
                                    "example/Connector", interface_name
                                )
                            },
                        ),
                    )
                )
                finding = next(
                    row
                    for row in report["findings"]
                    if row["rule_id"] == "cleanroom.mixin.registration.connector"
                )
                self.assertEqual(
                    "not-retained-by-v1-scanner",
                    finding["observed"]["interface_resolution"],
                )

    def test_target_selector_runtime_tokenization_and_case_are_exact(self) -> None:
        invalid = {
            "@environment(BOGUS)": ("default-from-null-phase", "BOGUS"),
            "@env(BOGUS)|@env(PREINIT)": (
                "default-from-null-phase",
                "BOGUS",
            ),
            "BOGUS": ("fallback-environment", None),
            "@env(preinit)": ("fallback-environment", None),
        }
        for target, (resolution, payload) in invalid.items():
            with self.subTest(target=target, expected="reject"):
                report = self._inspect(
                    ("invalid-selector.jar", _fixture(target=target))
                )
                finding = next(
                    row
                    for row in report["findings"]
                    if row["rule_id"]
                    == "cleanroom.mixin.config.unknown-environment-selector"
                )
                self.assertEqual("reject", finding["disposition"])
                self.assertEqual(
                    resolution, finding["observed"]["runtime_resolution"]
                )
                self.assertEqual(
                    payload,
                    finding["observed"]["first_environment_payload"],
                )

        valid = (
            "@env(PREINIT)|@env(BOGUS)",
            "@environment(PREINIT)",
            "PREINIT",
        )
        for target in valid:
            with self.subTest(target=target, expected="admitted"):
                report = self._inspect(
                    ("valid-selector.jar", _fixture(target=target))
                )
                self.assertNotIn(
                    "cleanroom.mixin.config.unknown-environment-selector",
                    {row["rule_id"] for row in report["findings"]},
                )

    def test_first_environment_selector_controls_mod_alias_semantics(self) -> None:
        mod_first = self._inspect(
            (
                "mod-first.jar",
                _fixture(target="@environment(MOD)|@env(PREINIT)"),
            )
        )
        mod_first_rules = {row["rule_id"] for row in mod_first["findings"]}
        self.assertIn(
            "cleanroom.mixin.config.mod-selector-is-default-alias",
            mod_first_rules,
        )
        self.assertIn(
            "cleanroom.mixin.plugin.mod-selector-without-plugin",
            mod_first_rules,
        )
        self.assertNotIn(
            "cleanroom.mixin.config.unknown-environment-selector",
            mod_first_rules,
        )

        phase_first = self._inspect(
            (
                "phase-first.jar",
                _fixture(target="@env(PREINIT)|@env(MOD)"),
            )
        )
        phase_first_rules = {row["rule_id"] for row in phase_first["findings"]}
        self.assertNotIn(
            "cleanroom.mixin.config.mod-selector-is-default-alias",
            phase_first_rules,
        )
        self.assertNotIn(
            "cleanroom.mixin.plugin.mod-selector-without-plugin",
            phase_first_rules,
        )

    def test_report_identity_and_summary_tampering_fail(self) -> None:
        report = self._inspect(("example.jar", _fixture()))
        tampered = deepcopy(report)
        tampered["summary"]["finding_count"] += 1
        with self.assertRaisesRegex(DoctorError, "content ID"):
            validate_report(tampered)

        tampered = deepcopy(report)
        tampered["report_id"] = report["report_id"]
        tampered["findings"][0]["finding_id"] = (
            "workbench-cleanroom-mixin-doctor-finding:sha256:" + "0" * 64
        )
        with self.assertRaisesRegex(DoctorError, "content ID"):
            validate_report(tampered)

    def test_bound_validation_rejects_omitted_policy_rule_after_reidentification(
        self,
    ) -> None:
        report, policy, policy_sha256, receipt = _bound_fixture(_fixture())
        reject_rule_ids = {
            rule["finding_id"]
            for group in policy["doctor_rules"].values()
            for rule in group["rules"]
            if rule["result"]["disposition"] == "reject"
        }
        removed = next(
            row
            for row in report["coverage"]
            if row["rule_id"] in reject_rule_ids and row["match_count"] == 0
        )
        forged = deepcopy(report)
        forged["coverage"] = [
            row for row in forged["coverage"] if row["rule_id"] != removed["rule_id"]
        ]
        _reidentify_internal_report(forged)

        validate_report(forged)
        with self.assertRaisesRegex(DoctorError, "coverage does not exactly match"):
            validate_bound_report(
                forged,
                policy=policy,
                policy_sha256=policy_sha256,
                topology_receipt=receipt,
            )

    def test_bound_validation_rejects_forged_finding_rule_semantics(self) -> None:
        report, policy, policy_sha256, receipt = _bound_fixture(_fixture())
        forged = deepcopy(report)
        forged["findings"][0]["rationale"] = "forged replacement rationale"
        _reidentify_internal_report(forged)

        validate_report(forged)
        with self.assertRaisesRegex(DoctorError, "finding rationale"):
            validate_bound_report(
                forged,
                policy=policy,
                policy_sha256=policy_sha256,
                topology_receipt=receipt,
            )

    def test_bound_validation_rejects_forged_artifact_identity(self) -> None:
        report, policy, policy_sha256, receipt = _bound_fixture(_fixture())
        forged = deepcopy(report)
        forged["findings"][0]["artifact_label"] = "different.jar"
        _reidentify_internal_report(forged)

        validate_report(forged)
        with self.assertRaisesRegex(DoctorError, "artifact identity"):
            validate_bound_report(
                forged,
                policy=policy,
                policy_sha256=policy_sha256,
                topology_receipt=receipt,
            )

    def test_scan_backed_validation_rejects_reidentified_zero_match_forgery(
        self,
    ) -> None:
        payload = _fixture()
        report, policy, policy_sha256, receipt = _bound_fixture(payload)
        rule_id = "cleanroom.mixin.registration.configs"
        self.assertTrue(
            any(row["rule_id"] == rule_id for row in report["findings"])
        )

        forged = deepcopy(report)
        forged["findings"] = [
            row for row in forged["findings"] if row["rule_id"] != rule_id
        ]
        coverage = next(
            row for row in forged["coverage"] if row["rule_id"] == rule_id
        )
        coverage["match_count"] = 0
        _reidentify_internal_report(forged)

        validate_report(forged)
        validate_bound_report(
            forged,
            policy=policy,
            policy_sha256=policy_sha256,
            topology_receipt=receipt,
        )
        with self.assertRaisesRegex(
            DoctorError, "does not match deterministic evaluation"
        ):
            validate_bound_report(
                forged,
                policy=policy,
                policy_sha256=policy_sha256,
                topology_receipt=receipt,
                scans=[scan_artifact_bytes(payload, label="example.jar")],
            )

    def test_evaluation_rejects_scans_from_a_different_topology(self) -> None:
        first = _fixture(owner="first_owner")
        second = _fixture(owner="second_owner")
        policy_bytes = POLICY_PATH.read_bytes()
        policy = json.loads(policy_bytes)
        receipt = build_topology_receipt(
            [ArtifactInput(label="first.jar", data=first)]
        )
        mismatched_scan = scan_artifact_bytes(second, label="second.jar")
        with self.assertRaisesRegex(
            DoctorError, "do not deterministically rebuild"
        ):
            evaluate_scans(
                policy=policy,
                policy_sha256=hashlib.sha256(policy_bytes).hexdigest(),
                scans=[mismatched_scan],
                topology_receipt=receipt,
            )

    def test_cli_writes_atomic_schema_valid_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "example.jar"
            output = root / "report.json"
            artifact.write_bytes(_fixture())
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--compact",
                    "--output",
                    str(output),
                    str(artifact),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            validate_report(report)
            _assert_schema(self, report)

    def test_malformed_archive_fails_closed_without_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "broken.jar"
            output = root / "report.json"
            artifact.write_bytes(b"not a zip")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--output",
                    str(output),
                    str(artifact),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(output.exists())
            self.assertIn("artifact scan rejected", completed.stderr)

    def test_policy_with_duplicate_json_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "example.jar"
            policy_path = root / "duplicate-policy.json"
            artifact.write_bytes(_fixture())
            policy_text = POLICY_PATH.read_text(encoding="utf-8")
            policy_path.write_text(
                policy_text.replace(
                    "{\n  \"applicability\"",
                    "{\n  \"format\": \"shadowed-format\",\n  \"applicability\"",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(DoctorError, "repeats JSON object key"):
                inspect_artifact_paths([artifact], policy_path=policy_path)

    def test_cli_disposition_threshold_fails_after_publishing_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "embedded-mixin.jar"
            output = root / "report.json"
            artifact.write_bytes(
                _fixture(extra={"org/spongepowered/asm/Fake.class": b"duplicate"})
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL_PATH),
                    "--fail-on",
                    "reject",
                    "--output",
                    str(output),
                    str(artifact),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(1, completed.returncode, completed.stderr)
            self.assertTrue(output.is_file())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("reject", report["summary"]["disposition"])

    def test_real_retained_fixture_when_available(self) -> None:
        artifact = (
            ROOT
            / ".workbench/targets/cleanroom-0.6.8-alpha-worldgen-observatory/"
            "artifacts/worldgen-observatory-fixture-0.1.0.jar"
        )
        if not artifact.is_file():
            self.skipTest("retained exact runtime fixture is unavailable")
        report = inspect_artifact_paths([artifact], policy_path=POLICY_PATH)
        validate_report(report)
        _assert_schema(self, report)
        self.assertEqual("review", report["summary"]["disposition"])
        self.assertEqual(0, report["summary"]["reject_finding_count"])
        self.assertIn(
            "cleanroom.mixin.plugin.programmatic-gating",
            {row["rule_id"] for row in report["findings"]},
        )


if __name__ == "__main__":
    unittest.main()
