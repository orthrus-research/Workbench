import contextlib
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from workbench_api.modules import ExecutionContext
from workbench_api.processes import ProcessResult, ProcessOutput, CapturedProcessResult
from workbench_api.sandboxes import WorkerSandboxSelection
from workbench_axiom.cli import ENGINE_NOTICES, distribution, installation, main, invoke, _response
from workbench_axiom.material_checks import program_snapshot, source_acknowledgement


class AxiomIntegrationTests(unittest.TestCase):
    def test_native_registration_exposes_only_implemented_operations(self):
        from workbench_axiom import module
        with patch("workbench_axiom.version", return_value="0.1.0"):
            descriptor = module()
        self.assertEqual("axiom", descriptor.id)
        self.assertEqual({"axiom.check", "axiom.query", "axiom.coverage", "axiom.target", "axiom.platform", "axiom.material-program"}, {capability.id for capability in descriptor.capabilities})
        self.assertTrue(all(isinstance(capability.handler, str) for capability in descriptor.capabilities))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="axiom-integration-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "lib").mkdir()
        self.jar = self.root / "lib/engine.jar"
        self.jar.write_bytes(b"test artifact, not executable Java")
        self.manifest = {"schema": "axiom.installation.v1", "component": "workbench-axiom-engine", "version": "0.1.0",
                         "mainClass": "research.orthrus.axiom.Main", "jars": {"engine.jar": sha256(self.jar.read_bytes()).hexdigest()}}
        self.write_manifest()

    def write_manifest(self):
        (self.root / "engine-manifest.json").write_text(json.dumps(self.manifest))

    def test_exact_installed_jar_inventory(self):
        manifest, jars, _, _ = installation(self.root)
        self.assertEqual(self.manifest, manifest)
        self.assertEqual([str(self.jar)], jars)
        self.jar.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "digest"):
            installation(self.root)

    def test_extra_jar_and_symlink_are_rejected(self):
        extra = self.root / "lib/extra.jar"
        extra.symlink_to(self.jar)
        with self.assertRaisesRegex(ValueError, "inventory"):
            installation(self.root)
        extra.unlink()
        self.jar.unlink()
        self.jar.symlink_to(self.root / "engine-manifest.json")
        with self.assertRaisesRegex(ValueError, "ordinary"):
            installation(self.root)

    def test_incompatible_engine_is_rejected(self):
        self.manifest["version"] = "1.0.0"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "incompatible"):
            installation(self.root)

    def test_distribution_retains_its_component_folder_and_original_notices(self):
        home = self.root / "workbench-axiom-engine-0.1.0"
        home.mkdir()
        (self.root / "lib").rename(home / "lib")
        (self.root / "engine-manifest.json").rename(home / "engine-manifest.json")
        for name in ENGINE_NOTICES:
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original notice fixture")
        result = distribution(home)
        self.assertEqual("workbench-axiom-engine", result["component"])
        self.assertEqual(set(ENGINE_NOTICES), set(result["notices"]))
        (home / ENGINE_NOTICES[-1]).unlink()
        with self.assertRaisesRegex(ValueError, "ordinary installed file"):
            distribution(home)

    def test_only_forwards_domain_response_through_host_port(self):
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "coverage", "status": "accepted", "result": {"wholePackParity": False}}
        result = ProcessResult(0, json.dumps(response).encode(), b"")
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        import sys
        with patch("workbench_axiom.cli.execute_process", return_value=result) as host, contextlib.redirect_stdout(io.StringIO()) as output:
            code = main("coverage", ["--engine-home", str(self.root), "--java", sys.executable], context)
        self.assertEqual(0, code)
        self.assertEqual(response["result"], json.loads(output.getvalue())["result"])
        self.assertEqual({"LANG": "C.UTF-8"}, host.call_args.kwargs["environment"])

    def test_source_evaluation_uses_core_selected_sandbox_and_records_policy(self):
        from types import SimpleNamespace
        import sys
        request = self.root / "request.json"
        request.write_text("{}")
        args = SimpleNamespace(engine_home=self.root, java=Path(sys.executable), request=request,
                               sandbox_backend="gvisor")
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0",
                    "operation": "check", "status": "accepted", "result": {}}
        selection = WorkerSandboxSelection("gvisor", "axiom.oci-worker.v1:test",
                                           ("-Daxiom.sandbox.backend=gvisor",), "session-id")

        @contextlib.contextmanager
        def selected(backend, **kwargs):
            self.assertEqual("gvisor", backend)
            yield selection

        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        with patch("workbench_axiom.cli.axiom_worker_sandbox", side_effect=selected), \
                patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(0, json.dumps(response).encode(), b"")) as host:
            value, code = invoke("check", args, context)
        self.assertEqual(0, code)
        self.assertEqual({"backend": "gvisor", "policy": selection.policy}, value["invocation"]["sandbox"])
        self.assertIn(selection.jvm_arguments[0], host.call_args.args[0])

    def test_exit_status_must_agree_with_domain_envelope(self):
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "coverage", "status": "unsupported"}
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        import sys
        with patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(0, json.dumps(response).encode(), b"")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(4, main("coverage", ["--engine-home", str(self.root), "--java", sys.executable], context))

    def test_file_capture_uses_verified_host_reads_and_preserves_protocol_checks(self):
        import sys
        from types import SimpleNamespace
        context = ExecutionContext(self.root, self.root / 'state', threading.Event())
        args = SimpleNamespace(engine_home=self.root, java=Path(sys.executable))
        body = {'schema': 'axiom.result.v1', 'engineVersion': '0.1.0', 'operation': 'coverage',
                'status': 'unsupported', 'result': {'wholePackParity': False}}
        payload = json.dumps(body).encode()
        stdout = ProcessOutput(self.root / 'capture/stdout.raw', len(payload), sha256(payload).hexdigest())
        stderr = ProcessOutput(self.root / 'capture/stderr.raw', 0, sha256(b'').hexdigest())
        captured = CapturedProcessResult(3, 'capture-fixture-id', 'request-fixture', stdout, stderr)
        with patch('workbench_axiom.cli.capture_process', return_value=captured) as host, \
                patch('workbench_axiom.cli.open_process_output', side_effect=lambda output: contextlib.closing(io.BytesIO(payload))) as opened, \
                patch('workbench_axiom.cli.execute_process', side_effect=AssertionError('no byte-buffer fallback')):
            response, code = invoke('coverage', args, context, capture_directory=self.root / 'capture', capture_binding='request-fixture')
        self.assertEqual(3, code)
        self.assertEqual(body['result'], response['result'])
        self.assertEqual(captured.reference, response['invocation']['capture'])
        self.assertEqual('request-fixture', host.call_args.kwargs['binding'])
        opened.assert_called_once_with(stdout)
        for raw, code in [(payload, 0), (b'{"schema":"a","schema":"b"}', 3), (b'{"value":NaN}', 3)]:
            wrong = CapturedProcessResult(code, captured.capture_id, captured.binding, stdout, stderr)
            with self.subTest(raw=raw, code=code), \
                    patch('workbench_axiom.cli.capture_process', return_value=wrong), \
                    patch('workbench_axiom.cli.open_process_output', side_effect=lambda output: contextlib.closing(io.BytesIO(raw))), \
                    self.assertRaises(ValueError):
                invoke('coverage', args, context, capture_directory=self.root / 'capture', capture_binding='request-fixture')

    def test_non_object_domain_envelopes_fail_closed(self):
        import sys
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        for raw in (b"null", b"[]", b'"not a result"'):
            with self.subTest(raw=raw), patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(0, raw, b"")), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(4, main("coverage", ["--engine-home", str(self.root), "--java", sys.executable], context))

    def test_target_is_bound_to_explicit_installed_profile_policy(self):
        import sys
        from types import SimpleNamespace
        target = self.root / "source-target.zip"
        target.write_bytes(b"invocation fixture, no source execution")
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        owner = {"profile": "supersymmetry", "sha256": "policy-a"}
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "target", "status": "accepted",
                    "result": {"profile": "supersymmetry", "policySha256": "policy-a"}}
        arguments = ["--engine-home", str(self.root), "--java", sys.executable, "--target", str(target), "--profile", "supersymmetry"]
        extension = SimpleNamespace(target_policy=lambda: owner)
        with patch("workbench_axiom.cli.profile_extension_identity", return_value={"profile": "supersymmetry"}), \
                patch("workbench_axiom.cli.require_profile_extension", return_value=extension), \
                patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(0, json.dumps(response).encode(), b"")) as host, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main("target", arguments, context))
            self.assertEqual(["--target", str(target)], host.call_args.args[0][-2:])
            artifacts = self.root / "artifacts.zip"
            artifacts.write_bytes(b"artifact invocation fixture")
            self.assertEqual(0, main("target", [*arguments, "--artifacts", str(artifacts)], context))
            self.assertEqual(["--target", str(target), "--artifacts", str(artifacts)], host.call_args.args[0][-4:])
        response["result"]["policySha256"] = "other-policy"
        with patch("workbench_axiom.cli.profile_extension_identity", return_value={"profile": "supersymmetry"}), \
                patch("workbench_axiom.cli.require_profile_extension", return_value=extension), \
                patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(0, json.dumps(response).encode(), b"")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(4, main("target", arguments, context))

    def test_platform_and_combined_target_bind_both_installed_owners(self):
        import sys
        from types import SimpleNamespace
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        source, platform = self.root / "source.zip", self.root / "platform.zip"
        source.write_bytes(b"source fixture")
        platform.write_bytes(b"platform fixture")
        owners = {name: {"profile": name, "sha256": "policy-" + name} for name in ("supersymmetry", "cleanroom")}
        def extension(group, name):
            return SimpleNamespace(target_policy=lambda: owners[name])
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "platform", "status": "accepted",
                    "result": {"profile": "cleanroom", "policySha256": "policy-cleanroom"}}
        base = ["--engine-home", str(self.root), "--java", sys.executable]
        def execute(*args, **kwargs):
            return ProcessResult(0, json.dumps(response).encode(), b"")
        with patch("workbench_axiom.cli.profile_extension_identity", side_effect=lambda group, name: {"profile": name}), \
                patch("workbench_axiom.cli.require_profile_extension", side_effect=extension), \
                patch("workbench_axiom.cli.execute_process", side_effect=execute) as host, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, main("platform", [*base, "--platform", str(platform), "--profile", "cleanroom"], context))
            self.assertEqual(["--platform", str(platform)], host.call_args.args[0][-2:])
            selected = response["result"]
            response.update(operation="target", result={"profile": "supersymmetry", "policySha256": "policy-supersymmetry", "platformInspection": selected})
            combined = [*base, "--target", str(source), "--profile", "supersymmetry", "--platform", str(platform)]
            self.assertEqual(4, main("target", combined, context))
            self.assertEqual(0, main("target", [*combined, "--platform-profile", "cleanroom"], context))
            self.assertEqual(["--target", str(source), "--platform", str(platform)], host.call_args.args[0][-4:])
            selected["policySha256"] = "wrong-policy"
            self.assertEqual(4, main("target", [*combined, "--platform-profile", "cleanroom"], context))

    def test_resource_policy_change_during_evaluation_is_rejected(self):
        import sys
        from types import SimpleNamespace
        platform = self.root / "platform.zip"
        platform.write_bytes(b"fixture")
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        calls = iter([{"profile": "cleanroom", "sha256": "before"}, {"profile": "cleanroom", "sha256": "after"}])
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "platform", "status": "accepted",
                    "result": {"profile": "cleanroom", "policySha256": "before"}}
        with patch("workbench_axiom.cli.profile_extension_identity", return_value={"profile": "cleanroom"}), \
                patch("workbench_axiom.cli.require_profile_extension", return_value=SimpleNamespace(target_policy=lambda: next(calls))), \
                patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(0, json.dumps(response).encode(), b"")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(4, main("platform", ["--engine-home", str(self.root), "--java", sys.executable,
                                                "--platform", str(platform), "--profile", "cleanroom"], context))

    def test_material_program_uses_installed_context_without_promoting_qualification(self):
        import sys
        from types import SimpleNamespace
        context = ExecutionContext(self.root, self.root / "state", threading.Event())
        archive, request = self.root / "program.zip", self.root / "request.json"
        raw, program = program_snapshot({"groovy/runConfig.json": b"{}", "groovy/material/Test.groovy": b"// test\r\n"}, ".")
        archive.write_bytes(raw)
        request.write_text(json.dumps({"observeMaterials": ["supersymmetry:example"]}))
        policy = {"profile": "supersymmetry", "context": "supersymmetry:material-authoring-gt-base",
                  "sha256": "admission-a", "contextPolicySha256": "context-a"}
        response = {"schema": "axiom.result.v1", "engineVersion": "0.1.0", "operation": "material-program", "status": "incomplete",
                    "result": {"context": {"id": policy["context"]}, "contextPolicySha256": "context-a",
                               "admissionPolicySha256": "admission-a", "nativeOutcome": "completed-without-observed-error",
                               "sourceProgram": source_acknowledgement(program)}}
        arguments = ["--engine-home", str(self.root), "--java", sys.executable, "--runtime-home", str(self.root),
                     "--program", str(archive), "--request", str(request), "--profile", "supersymmetry", "--context", policy["context"]]
        extension = SimpleNamespace(material_admission=lambda context_id: policy)
        with patch("workbench_axiom.cli.profile_extension_identity", return_value={"profile": "supersymmetry"}), \
                patch("workbench_axiom.cli.require_profile_extension", return_value=extension), \
                patch("workbench_axiom.cli.execute_process", return_value=ProcessResult(4, json.dumps(response).encode(), b"")) as host, \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(4, main("material-program", arguments, context))
            forwarded = json.loads(host.call_args.kwargs["stdin"])
            self.assertEqual(policy["context"], forwarded["context"])
            self.assertEqual("admission-a", forwarded["admissionPolicySha256"])
            self.assertEqual(response["result"], json.loads(output.getvalue())["result"])
            self.assertEqual(["--runtime-home", str(self.root), "--program", str(archive)], host.call_args.args[0][-4:])
            request.write_text(json.dumps({"expectations": [{"id": "registered", "material": "supersymmetry:example", "kind": "registration", "equals": True}]}))
            paired = {**response, "result": {"baseline": response, "candidate": response}}
            host.return_value = ProcessResult(4, json.dumps(paired).encode(), b"")
            self.assertEqual(4, main("material-program", [*arguments, "--baseline-program", str(archive)], context))
            self.assertEqual(["--baseline-program", str(archive)], host.call_args.args[0][-2:])
            self.assertIsNone(host.call_args.kwargs["timeout_seconds"])
            self.assertEqual("registration", json.loads(host.call_args.kwargs["stdin"])["expectations"][0]["kind"])
            host.return_value = ProcessResult(4, json.dumps(response).encode(), b"")
            diagnostics = [arg for arg in arguments if arg not in {"--request", str(request)}]
            self.assertEqual(4, main("material-program", diagnostics, context))
            self.assertEqual({"context": policy["context"], "contextPolicySha256": "context-a",
                              "admissionPolicySha256": "admission-a"}, json.loads(host.call_args.kwargs["stdin"]))
            self.assertIsNone(host.call_args.kwargs["timeout_seconds"])
            self.assertIsNone(host.call_args.kwargs["output_limit"])
            self.assertIsNone(host.call_args.kwargs["input_limit"])
            tampered = {**response, "result": {**response["result"], "sourceProgram": {**source_acknowledgement(program), "fileCount": 99}}}
            host.return_value = ProcessResult(4, json.dumps(tampered).encode(), b"")
            with contextlib.redirect_stdout(io.StringIO()) as invalid_output, contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(4, main("material-program", diagnostics, context))
            self.assertEqual("", invalid_output.getvalue())
            self.assertIn("source inventory acknowledgement differs", errors.getvalue())
            host.reset_mock()
            request.write_text(json.dumps({"observeMaterials": [], "admissionPolicySha256": "candidate-choice"}))
            self.assertEqual(4, main("material-program", arguments, context));host.assert_not_called()

    def test_installed_response_retains_full_single_and_pair_after_custody_projection(self):
        single = {"value": "é" * 700000}
        for value in (single, {"result": {"baseline": single, "candidate": single}}):
            output = _response(value)
            self.assertGreater(len(output.encode()), 1024**2)
            self.assertTrue(output.endswith("\n"))
            self.assertEqual(value, json.loads(output))
