"""Pack-owned startup semantics; fixtures do not qualify a real game run."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from workbench_profile_supersymmetry import developer_checks as policy
from workbench_profile_supersymmetry.check_diagnostics import findings, locations
from workbench_pack_program_studio.source_locations import verify_source_location


def logs(latest="", groovy="", **extra):
    return {name: {"state": "captured", "text": text} for name, text in {"logs/latest.log": latest, "logs/groovy.log": groovy, **extra}.items()}


def checkpoint(nonce="nonce"):
    return "Forge Mod Loader has successfully loaded 2 mods\n" + policy.PREFIX + json.dumps(dict(nonce=nonce, side="CLIENT", items=7, fluids=8)) + "\n"


class SavedCheckPolicyTests(unittest.TestCase):
    def setUp(self):
        self.inputs = SimpleNamespace(sources={"groovy/preInit/Probe.groovy": b"def x = (\n"})

    def interpret(self, records):
        return policy.interpret(self.inputs, records, "nonce", runtime_root=Path("/private/run"))

    def test_microblock_materials_are_identified_without_gregtech_or_causal_guessing(self):
        event = "[12:00:00] [Client thread/ERROR] [ForgeMicroBlockCBE]: Material with id example:block[prop=x] is already registered.\n"
        result = self.interpret(logs(checkpoint() + event * 2))
        self.assertEqual(result["unclassified_findings_count"], 2)
        self.assertEqual(result["findings"][0]["diagnostic"]["family"], "microblock-material-registration")
        self.assertEqual(result["findings"][0]["diagnostic"]["origin"]["kind"], "unknown")
        self.assertIn("not evidence of duplicate GregTech", result["findings"][0]["diagnostic"]["guidance"])
        other = self.interpret(logs(checkpoint() + event.replace("ForgeMicroBlockCBE", "other")))
        self.assertNotEqual(other["findings"][0]["diagnostic"]["family"], "microblock-material-registration")

    def test_recipe_json_identity_keeps_subject_and_exception_changes_distinct(self):
        event = "[12:00:00] [Client thread/ERROR] [FML]: Parsing error loading recipe example:missing\ncom.google.gson.JsonSyntaxException: Unknown item 'example:item'\n"
        result = self.interpret(logs(checkpoint() + event))
        metadata = result["findings"][0]["diagnostic"]
        self.assertEqual(metadata["family"], "recipe-json-loading")
        self.assertEqual(metadata["subject"], "example:missing")
        self.assertIn("assets/example/recipes/missing.json", metadata["guidance"])
        self.assertEqual(result["outcome"], "inconclusive")
        changed = self.interpret(logs(checkpoint() + event.replace("example:item", "example:other")))
        self.assertNotEqual(metadata["key"], changed["findings"][0]["diagnostic"]["key"])

    def test_only_disposable_root_is_normalized_and_graphics_overflow_is_explicit(self):
        event = "[CLIENT/ERROR] [unknown]: Failure in /private/run/file with value 123\n"
        first = self.interpret(logs(event))["findings"][0]["diagnostic"]["key"]
        moved = policy.interpret(self.inputs, logs(event.replace("/private/run", "/other/run")), "nonce", runtime_root=Path("/other/run"))
        self.assertEqual(first, moved["findings"][0]["diagnostic"]["key"])
        changed = self.interpret(logs(event.replace("123", "124")))
        self.assertNotEqual(first, changed["findings"][0]["diagnostic"]["key"])
        observed = self.interpret(logs(checkpoint() + "".join(f"  GL info: GPU {index}\n" for index in range(9))))
        self.assertFalse(observed["runtime_observations"]["complete"])

    def test_probe_uses_platform_json_serializer_without_optional_groovy_module(self):
        original = b'{"loaders":{"postInit":["postInit/"]}}'
        inputs = SimpleNamespace(sources={"groovy/runConfig.json": original})
        overlays = policy.overlays(inputs, "nonce")
        script = overlays["groovy/workbenchChecks/Observe.groovy"].decode()
        self.assertIn("import com.google.gson.Gson", script)
        self.assertIn("new Gson().toJson(observation)", script)
        self.assertNotIn("groovy.json", script)
        self.assertEqual(inputs.sources["groovy/runConfig.json"], original)
        self.assertEqual(
            json.loads(overlays["groovy/runConfig.json"])["loaders"]["postInit"],
            ["postInit/", "workbenchChecks/"],
        )

    def test_unknown_errors_block_acceptance_without_becoming_compiler_failures(self):
        result = self.interpret(logs(checkpoint() + "[12:00:00] [Client thread/ERROR] [example]: Unknown runtime issue\n"))
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["observation"]["state"], "checkpoint")
        self.assertEqual(result["checks"][1]["state"], "no-errors-observed")
        self.assertEqual(result["unclassified_findings_count"], 1)

    def test_mislevelled_initialization_has_a_narrow_explained_rule(self):
        message = "[12:00:00] [Client thread/FATAL] [mousetweaks]: Mouse Tweaks has been initialized.\n"
        result = self.interpret(logs(checkpoint() + message))
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["findings"][0]["category"], "informational")
        self.assertTrue(result["findings"][0]["reason"])
        for changed in (message.replace("initialized.", "broken."), message.replace("mousetweaks", "other")):
            self.assertEqual(self.interpret(logs(checkpoint() + changed))["outcome"], "inconclusive")

    def test_mirrors_merge_but_repeated_occurrences_and_evidence_survive(self):
        event = "[12:00:00] [CLIENT/ERROR] [supersymmetry]: MultipleCompilationErrorsException: startup failed\nfile:/private/run/groovy/preInit/Probe.groovy: 2: Unexpected input @ line 2, column 1.\n"
        result = self.interpret(logs(groovy=event * 2, **{"logs/process-stdout.raw": event * 2}))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["findings_count"], 2)
        for finding in result["findings"]:
            self.assertEqual(len(finding["evidence"]), 2)
            self.assertEqual(finding["location"]["byte_start"], len(self.inputs.sources["groovy/preInit/Probe.groovy"]))
            self.assertEqual(finding["location"]["start"], {"line": 2, "column": 1})

    def test_real_groovy_preface_and_throwing_events_merge_across_streams(self):
        preface = "An error occurred while trying to compile script class preInit/Probe.groovy Look at latest.log for a full stacktrace:"
        detail = "org.codehaus.groovy.control.MultipleCompilationErrorsException: startup failed\nfile:/private/run/groovy/preInit/Probe.groovy: 2: Unexpected input @ line 2, column 1.\n"
        groovy = "[12:00:00] [CLIENT/ERROR] [supersymmetry]: " + preface + "\n\t" + detail + "\t\tat java.base/Example.run(Example.java:1)\n"
        latest = "[12:00:00] [Client thread/ERROR] [GroovyLog]: " + preface + "\n[12:00:00] [Client thread/ERROR] [groovyscript]: Throwing\n" + detail + "\tat Example.run(Example.java:1)\n"
        result = self.interpret(logs(latest, groovy, **{"logs/process-stdout.raw": latest}))
        self.assertEqual(result["findings_count"], 1)
        self.assertEqual(len(result["findings"][0]["evidence"]), 5)
        self.assertNotIn("Example.run", result["findings"][0]["message"])

    def test_unclassified_native_stderr_error_is_not_silently_accepted(self):
        result = self.interpret(logs(checkpoint(), **{"logs/process-stderr.raw": "MESA: error: ZINK: failed to choose pdev\n"}))
        self.assertEqual(result["outcome"], "inconclusive")
        self.assertEqual(result["findings"][0]["category"], "unclassified")

    def test_exact_source_paths_support_spaces_unicode_blank_lines_and_eof(self):
        name = "groovy/preInit/水 probe.groovy"
        raw = "// 😀\r\n\r\ndef x = (\r\n".encode()
        for line, column in ((2, 1), (4, 1)):
            text = f"file:///private/run/groovy/preInit/%E6%B0%B4%20probe.groovy: {line}: Unexpected input @ line {line}, column {column}."
            found = locations({name: raw}, [text], "/private/run")
            self.assertEqual(len(found), 1)
            location = found[0]["location"]
            self.assertEqual(location["start"], {"line": line, "column": 1})
            self.assertEqual(location, verify_source_location(raw, location))
        self.assertEqual(locations({name: raw}, ["file:/outside/" + name + ": 2: error"], "/private/run"), [])
        self.assertEqual(locations({name: raw}, ["probe.groovy: 2: error"], "/private/run"), [])
        self.assertEqual(locations({name: raw}, [name + ": 900: error"], "/private/run"), [])

    def test_empty_file_and_implicit_final_line_have_explicit_point_anchors(self):
        name = "groovy/empty.groovy"
        for raw, line, kind in ((b"", 1, "insertion-point"), (b"(", 2, "end-of-file-anchor")):
            row = locations({name: raw}, [f"{name}: {line}: error"], "/private/run")[0]
            self.assertEqual(row["location_kind"], kind)
            self.assertEqual(row["reported_position"]["line"], line)
            self.assertEqual(row["location"]["byte_start"], len(raw))
            self.assertEqual(row["location"]["byte_end"], len(raw))

    def test_nonce_duplicates_wrong_side_and_incomplete_logs_never_pass(self):
        for latest in (checkpoint("wrong"), checkpoint() * 2, checkpoint().replace('"CLIENT"', '"SERVER"')):
            self.assertEqual(self.interpret(logs(latest))["outcome"], "inconclusive")
        records = logs(checkpoint())
        records["logs/groovy.log"] = {"state": "over-bound", "text": None}
        self.assertEqual(self.interpret(records)["outcome"], "inconclusive")

    def test_startup_crash_report_stops_a_live_error_screen(self):
        report = "---- Minecraft Crash Report ----\nDescription: Initializing game\nLoaderExceptionModCrash: failed\nCaused by: InjectionError: Critical injection failure\n"
        result = self.interpret(logs(**{"crash-reports/crash-client.txt": report}))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["observation"]["stage"], "client-startup-crash")
        self.assertEqual(result["checks"][1]["state"], "inconclusive")
        self.assertIn("InjectionError", result["findings"][0]["message"])

    def test_jvm_failure_on_stderr_is_environment_not_compilation(self):
        result = self.interpret(logs(**{"logs/process-stderr.raw": "Error: Could not find or load main class example.Main\n"}))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["findings"][0]["category"], "environment")

    def test_current_groovyscript_runtime_exception_messages_are_terminal(self):
        # Messages from GroovyScript 1.4.3's GroovyScriptSandbox.run/runClosure.
        for message in (
            "Script 'preInit.Probe' ran into an issue while executing.",
            "An exception occurred while trying to run groovy code! This is might be a internal groovy issue.",
            "An unknown error occurred while running scripts",
            "An exception occurred while running a closure at least once!",
        ):
            with self.subTest(message=message):
                result = self.interpret(logs(groovy="[12:00:00] [CLIENT/ERROR] [supersymmetry]: " + message + "\n"))
                self.assertEqual(result["outcome"], "failed")
                self.assertEqual(result["observation"]["stage"], "groovy-execution-failed")
                self.assertEqual(result["findings"][0]["category"], "runtime")

    def test_runtime_exception_wrappers_merge_without_losing_repeated_failures(self):
        preface = "Script 'preInit.Probe' ran into an issue while executing. Look at latest.log for a full stacktrace:"
        cause = "java.lang.IllegalStateException: deliberate failure"
        groovy = "[12:00:00] [CLIENT/ERROR] [supersymmetry]: " + preface + "\n\t" + cause + "\n\t\tat preInit.Probe.run(Probe.groovy:2)\n"
        latest = "[12:00:00] [Client thread/ERROR] [GroovyLog]: " + preface + "\n[12:00:00] [Client thread/ERROR] [groovyscript]: Throwing\n" + cause + "\n\tat preInit.Probe.run(Probe.groovy:2)\n"
        result = self.interpret(logs(latest * 2, groovy * 2, **{"logs/process-stdout.raw": latest * 2}))
        self.assertEqual(result["findings_count"], 2)
        for row in result["findings"]:
            self.assertEqual(row["category"], "runtime")
            self.assertEqual(len(row["evidence"]), 5)
            self.assertIn(cause, row["message"])
            self.assertIsNone(row["location"])  # A basename stack frame is not an exact source path.

    def test_interleaved_thread_exception_is_not_absorbed_as_the_script_failure(self):
        text = "[12:00:00] [Client thread/ERROR] [GroovyLog]: Script 'preInit.Probe' ran into an issue while executing.\n[12:00:00] [Other thread/ERROR] [other]: Throwing\njava.lang.IllegalStateException: unrelated\n"
        result = self.interpret(logs(text))
        self.assertEqual(result["findings_count"], 2)
        self.assertEqual(result["blocking_findings_count"], 1)
        self.assertEqual(result["unclassified_findings_count"], 1)

    def test_loader_entry_reports_progress_before_compilation_completes(self):
        for loader, stage in (("preInit", "groovy-preinit"), ("init", "client-init"), ("postInit", "groovy-postinit")):
            records = logs(groovy=f"[12:00:00] [CLIENT/INFO] [supersymmetry]: Running scripts in loader '{loader}'\n")
            self.assertEqual(policy.observe(self.inputs, records, "nonce", runtime_root=Path("/private/run"))["stage"], stage)

    def test_oversized_finding_list_is_visible_and_never_clean(self):
        event = "[12:00:00] [Client thread/FATAL] [mousetweaks]: Mouse Tweaks has been initialized.\n"
        result = self.interpret(logs(checkpoint() + event * 1001))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["findings_count"], 1001)
        self.assertEqual(len(result["findings"]), 1000)
        self.assertEqual(result["outcome"], "inconclusive")
