from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/project-intelligence/src"
sys.path.insert(0, str(SOURCE))

from workbench_project_intelligence import (  # noqa: E402
    ArtifactScanError,
    ClassifiedBuildFileInput,
    ExactBuildFileInput,
    MIXIN_BUILD_POLICY_FORMAT,
    MixinCompilerAPInvocationInput,
    build_mixin_compiler_ap_build_receipt,
    canonical_json_bytes,
    validate_bound_mixin_compiler_ap_build_receipt,
    validate_mixin_compiler_ap_build_receipt,
)


SERVICE_PATH = "META-INF/services/javax.annotation.processing.Processor"
INJECTION = "org.spongepowered.tools.obfuscation.MixinObfuscationProcessorInjection"
TARGETS = "org.spongepowered.tools.obfuscation.MixinObfuscationProcessorTargets"
SCHEMA = json.loads(
    (
        ROOT
        / "modules/project-intelligence/schemas/"
        "mixin-compiler-ap-build-receipt-v1.schema.json"
    ).read_text(encoding="utf-8")
)
VALIDATOR = Draft202012Validator(SCHEMA)
TOOL = ROOT / "tools/assemble_mixin_compiler_ap_build_receipt.py"


def _archive(providers: list[str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        info = ZipInfo(SERVICE_PATH, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_STORED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, ("\n".join(providers) + "\n").encode("utf-8"))
    return output.getvalue()


def _policy(processor: bytes) -> bytes:
    value = {
        "cleanmix_compatibility_output_name": "cleanmix_version_compatibility.json",
        "component": {
            "distribution": "CleanMix",
            "distribution_version": "test",
            "source_revision": "0" * 40,
        },
        "format": MIXIN_BUILD_POLICY_FORMAT,
        "minimum_processor_artifact_count": 1,
        "minimum_runtime_artifact_count": 1,
        "minimum_source_count": 1,
        "policy_id": "workbench-test-cleanmix-compiler-ap-build-policy-v1",
        "processor_service_path": SERVICE_PATH,
        "required_output_kinds": [
            "cleanmix-compatibility",
            "mapping",
            "refmap",
        ],
        "required_processor_artifact_sha256s": [
            hashlib.sha256(processor).hexdigest()
        ],
        "required_processor_providers": [INJECTION, TARGETS],
        "schema_version": 1,
    }
    return canonical_json_bytes(value) + b"\n"


def _file(label: str, logical_name: str, data: bytes | None) -> ExactBuildFileInput:
    return ExactBuildFileInput(label=label, logical_name=logical_name, data=data)


def _classified(
    kind: str, label: str, logical_name: str, data: bytes | None
) -> ClassifiedBuildFileInput:
    return ClassifiedBuildFileInput(kind, _file(label, logical_name, data))


def _invocation(
    processor: bytes, *, mapping_output: bytes | None = b"CL: a b\n"
) -> MixinCompilerAPInvocationInput:
    return MixinCompilerAPInvocationInput(
        invocation_label="example-main-compile",
        compiler_executable=_file("javac", "jdk/bin/javac", b"javac-executable"),
        runtime_artifacts=[_file("java", "jdk/bin/java", b"java-executable")],
        compiler_identity_capture=_file(
            "javac-version", "captures/javac-version.txt", b"javac 1.8.0_412\n"
        ),
        runtime_identity_capture=_file(
            "java-version", "captures/java-version.txt", b"java version 1.8.0_412\n"
        ),
        processor_artifacts=[
            _file("cleanmix-ap", "processor-path/cleanmix.jar", processor)
        ],
        compiler_options=["-source", "8", "-target", "8"],
        annotation_processor_options=[
            "-AoutRefMapFile=example.refmap.json",
            "-AoutSrgFile=example.srg",
        ],
        diagnostics=_file("diagnostics", "captures/javac-diagnostics.bin", b""),
        sources=[
            _file(
                "example-mixin",
                "src/example/ExampleMixin.java",
                b"package example; final class ExampleMixin {}\n",
            )
        ],
        inputs=[
            _classified(
                "mapping-input", "mcp-input", "mappings/input.srg", b"CL: x y\n"
            )
        ],
        outputs=[
            _classified(
                "refmap", "refmap", "example.refmap.json", b'{"mappings":{}}\n'
            ),
            _classified("mapping", "mapping", "example.srg", mapping_output),
            _classified(
                "cleanmix-compatibility",
                "compatibility",
                "cleanmix_version_compatibility.json",
                b'{"example.ExampleMixin":"0.6.0"}\n',
            ),
        ],
    )


def _schema_errors(receipt: dict) -> list[str]:
    return [error.message for error in VALIDATOR.iter_errors(receipt)]


class MixinBuildProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = _archive([INJECTION, TARGETS])
        self.policy = _policy(self.processor)

    def test_complete_receipt_binds_exact_invocation_custody(self) -> None:
        invocation = _invocation(self.processor)
        receipt = build_mixin_compiler_ap_build_receipt(invocation, self.policy)

        validate_mixin_compiler_ap_build_receipt(receipt)
        validate_bound_mixin_compiler_ap_build_receipt(
            receipt, invocation, self.policy
        )
        self.assertEqual([], _schema_errors(receipt))
        self.assertEqual("complete", receipt["summary"]["custody_state"])
        self.assertEqual([], receipt["issues"])
        self.assertEqual(
            [INJECTION, TARGETS],
            receipt["processors"][0]["service_entry"]["providers"],
        )
        self.assertEqual(
            ["-source", "8", "-target", "8"],
            receipt["invocation"]["options"]["compiler_options"],
        )
        self.assertFalse(receipt["boundaries"]["reproducible_build_proved"])
        self.assertFalse(
            receipt["boundaries"]["source_to_output_causality_proved"]
        )

    def test_bound_validator_rejects_exact_output_substitution(self) -> None:
        invocation = _invocation(self.processor)
        receipt = build_mixin_compiler_ap_build_receipt(invocation, self.policy)
        substituted_outputs = list(invocation.outputs)
        substituted_outputs[0] = replace(
            substituted_outputs[0],
            file=replace(substituted_outputs[0].file, data=b'{"tampered":true}\n'),
        )
        substituted = replace(invocation, outputs=substituted_outputs)

        validate_mixin_compiler_ap_build_receipt(receipt)
        with self.assertRaisesRegex(ArtifactScanError, "bound exact inputs"):
            validate_bound_mixin_compiler_ap_build_receipt(
                receipt, substituted, self.policy
            )

    def test_reidentified_partial_issue_omission_is_rejected(self) -> None:
        receipt = build_mixin_compiler_ap_build_receipt(
            _invocation(self.processor, mapping_output=None), self.policy
        )
        forged = deepcopy(receipt)
        forged["issues"] = []
        forged["summary"]["custody_state"] = "complete"
        forged["summary"]["issue_count"] = 0
        forged["summary"]["issue_counts"] = {
            key: 0 for key in forged["summary"]["issue_counts"]
        }
        material = {key: value for key, value in forged.items() if key != "receipt_id"}
        forged["receipt_id"] = (
            "workbench-mixin-compiler-ap-build-receipt:sha256:"
            + hashlib.sha256(canonical_json_bytes(material)).hexdigest()
        )

        with self.assertRaisesRegex(ArtifactScanError, "issues are not reproducible"):
            validate_mixin_compiler_ap_build_receipt(forged)

    def test_missing_mapping_output_publishes_partial_exact_facts(self) -> None:
        receipt = build_mixin_compiler_ap_build_receipt(
            _invocation(self.processor, mapping_output=None), self.policy
        )
        kinds = [row["issue_kind"] for row in receipt["issues"]]

        self.assertEqual("partial", receipt["summary"]["custody_state"])
        self.assertIn("output-missing", kinds)
        self.assertIn("required-output-kind-missing", kinds)
        self.assertNotIn("diagnostics-capture-missing", kinds)
        self.assertEqual(1, receipt["summary"]["missing_file_count"])
        self.assertEqual([], _schema_errors(receipt))

    def test_processor_hash_and_provider_mismatch_fail_closed(self) -> None:
        substituted_processor = _archive([INJECTION])
        receipt = build_mixin_compiler_ap_build_receipt(
            _invocation(substituted_processor), self.policy
        )
        kinds = {row["issue_kind"] for row in receipt["issues"]}

        self.assertEqual("partial", receipt["summary"]["custody_state"])
        self.assertIn("required-processor-artifact-missing", kinds)
        self.assertIn("required-processor-provider-missing", kinds)

    def test_cli_publishes_partial_receipt_before_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {
                "javac.bin": b"javac",
                "java.bin": b"java",
                "javac-version.txt": b"javac 1.8\n",
                "java-version.txt": b"java 1.8\n",
                "processor.jar": self.processor,
                "diagnostics.bin": b"",
                "ExampleMixin.java": b"final class ExampleMixin {}\n",
                "input.srg": b"CL: a b\n",
                "refmap.json": b"{}\n",
                "compatibility.json": b"{}\n",
            }
            for name, payload in files.items():
                (root / name).write_bytes(payload)
            (root / "policy.json").write_bytes(self.policy)
            spec = {
                "annotation_processor_options": ["-AoutRefMapFile=refmap.json"],
                "compiler": {
                    "compiler_identity_capture": {
                        "label": "javac-version",
                        "logical_name": "captures/javac-version.txt",
                        "path": "javac-version.txt",
                    },
                    "executable": {
                        "label": "javac",
                        "logical_name": "jdk/bin/javac",
                        "path": "javac.bin",
                    },
                    "runtime_artifacts": [
                        {
                            "label": "java",
                            "logical_name": "jdk/bin/java",
                            "path": "java.bin",
                        }
                    ],
                    "runtime_identity_capture": {
                        "label": "java-version",
                        "logical_name": "captures/java-version.txt",
                        "path": "java-version.txt",
                    },
                },
                "compiler_options": ["-source", "8"],
                "diagnostics": {
                    "label": "diagnostics",
                    "logical_name": "captures/diagnostics.bin",
                    "path": "diagnostics.bin",
                },
                "format": "workbench-mixin-compiler-ap-build-input-v1",
                "inputs": [
                    {
                        "kind": "mapping-input",
                        "label": "input-mapping",
                        "logical_name": "mappings/input.srg",
                        "path": "input.srg",
                    }
                ],
                "invocation_label": "cli-test",
                "outputs": [
                    {
                        "kind": "refmap",
                        "label": "refmap",
                        "logical_name": "example.refmap.json",
                        "path": "refmap.json",
                    },
                    {
                        "kind": "mapping",
                        "label": "mapping",
                        "logical_name": "example.srg",
                        "path": "missing-output.srg",
                    },
                    {
                        "kind": "cleanmix-compatibility",
                        "label": "compatibility",
                        "logical_name": "cleanmix_version_compatibility.json",
                        "path": "compatibility.json",
                    },
                ],
                "processors": [
                    {
                        "label": "cleanmix",
                        "logical_name": "processor-path/cleanmix.jar",
                        "path": "processor.jar",
                    }
                ],
                "schema_version": 1,
                "sources": [
                    {
                        "label": "source",
                        "logical_name": "src/ExampleMixin.java",
                        "path": "ExampleMixin.java",
                    }
                ],
            }
            spec_path = root / "input.json"
            output_path = root / "receipt.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    str(spec_path),
                    "--policy",
                    str(root / "policy.json"),
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            published = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual("partial", published["summary"]["custody_state"])

    def test_exact_cleanroom_policy_binds_locked_cleanmix_artifact(self) -> None:
        policy = json.loads(
            (
                ROOT
                / "profiles/platforms/cleanroom/mixins/"
                "cleanmix-compiler-ap-build-policy-v1.json"
            ).read_text(encoding="utf-8")
        )
        lock = json.loads(
            (
                ROOT
                / "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
                "transformer-toolchain-lock-v1.json"
            ).read_text(encoding="utf-8")
        )
        cleanmix = next(
            row
            for row in lock["artifacts"]
            if row["coordinate"] == "com.cleanroommc:cleanmix:0.7.0"
        )

        self.assertEqual(
            [cleanmix["sha256"]], policy["required_processor_artifact_sha256s"]
        )
        self.assertEqual([INJECTION, TARGETS], policy["required_processor_providers"])


if __name__ == "__main__":
    unittest.main()
