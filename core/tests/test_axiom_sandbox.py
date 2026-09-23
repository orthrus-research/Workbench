from pathlib import Path
from subprocess import CompletedProcess
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

from workbench_core import axiom_sandbox


def result(output=b"", code=0):
    return CompletedProcess([], code, output, b"")


class AxiomSandboxTests(unittest.TestCase):
    def test_core_selects_pinned_docker_worker_and_cleans_the_session(self):
        calls = []

        def docker(executable, host, *arguments, **options):
            calls.append(arguments)
            if arguments[:1] == ("info",):
                return result(b'{"runc":{},"runsc":{}}')
            if arguments[:2] == ("image", "inspect"):
                return result(b"linux/amd64\n")
            if arguments[:3] == ("container", "ls", "-aq"):
                return result(b"worker-id\n" if sum(c[:3] == ("container", "ls", "-aq") for c in calls) == 1 else b"")
            return result()

        with patch.object(axiom_sandbox, "_control", return_value=("/usr/bin/docker", "unix:///var/run/docker.sock")), \
                patch.object(axiom_sandbox, "_docker", side_effect=docker):
            selected = axiom_sandbox.open_axiom("gvisor", state_root=Path("/tmp"), cancelled=Event())
            self.assertEqual("gvisor", selected.backend)
            self.assertIn("-Daxiom.sandbox.backend=gvisor", selected.jvm_arguments)
            self.assertIn("-Daxiom.sandbox.image=" + axiom_sandbox.AXIOM_OCI_IMAGE,
                          selected.jvm_arguments)
            self.assertIn("-Daxiom.sandbox.user=", " ".join(selected.jvm_arguments))
            self.assertIn("runsc", selected.policy)
            axiom_sandbox.close_axiom(selected)
        self.assertTrue(any(call[:3] == ("container", "rm", "-f") for call in calls))
        self.assertNotIn(selected.session_id, axiom_sandbox._sessions)

    def test_gvisor_refuses_unavailable_runtime_without_docker_fallback(self):
        with patch.object(axiom_sandbox, "_control", return_value=("/usr/bin/docker", "unix:///var/run/docker.sock")), \
                patch.object(axiom_sandbox, "_docker", return_value=result(b'{"runc":{}}')):
            with self.assertRaisesRegex(ValueError, "runsc is unavailable"):
                axiom_sandbox.open_axiom("gvisor", state_root=Path("/tmp"), cancelled=Event())

    def test_next_run_recovers_abandoned_container_from_dead_owner(self):
        calls = []

        def docker(executable, host, *arguments, **options):
            calls.append(arguments)
            if arguments[:1] == ("info",):
                return result(b'{"runc":{}}')
            if arguments[:2] == ("image", "inspect"):
                return result(b"linux/amd64\n")
            if arguments[:3] == ("container", "ls", "-aq"):
                return result(b"abandoned-id\n" if sum(c[:3] == ("container", "ls", "-aq") for c in calls) == 1 else b"")
            return result()

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(axiom_sandbox, "_control", return_value=("/usr/bin/docker", "unix:///var/run/docker.sock")), \
                patch.object(axiom_sandbox, "_docker", side_effect=docker):
            root = Path(directory)
            abandoned = axiom_sandbox.open_axiom("docker", state_root=root, cancelled=Event())
            axiom_sandbox._sessions.pop(abandoned.session_id)
            with patch.object(axiom_sandbox, "_owner_alive", return_value=False):
                active = axiom_sandbox.open_axiom("docker", state_root=root, cancelled=Event())
            self.assertTrue(any(call[:4] == ("container", "rm", "-f", "abandoned-id") for call in calls))
            self.assertFalse((root / "sandboxes" / "axiom" / (abandoned.session_id + ".json")).exists())
            axiom_sandbox.close_axiom(active)

    def test_remote_or_non_socket_daemon_is_not_admitted(self):
        with patch.dict("os.environ", {"DOCKER_HOST": "tcp://example.invalid:2375"}), \
                patch("shutil.which", return_value="/usr/bin/docker"):
            with self.assertRaisesRegex(ValueError, "local Unix Docker socket"):
                axiom_sandbox._control()
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "docker.sock"
            fake.write_text("not a socket")
            with patch.dict("os.environ", {"DOCKER_HOST": "unix://" + str(fake)}), \
                    patch("shutil.which", return_value="/usr/bin/docker"):
                with self.assertRaisesRegex(ValueError, "not a local socket"):
                    axiom_sandbox._control()


if __name__ == "__main__":
    unittest.main()
