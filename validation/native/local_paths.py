"""Round-trip native local file URIs through installed workflow consumers."""
from importlib import import_module
from pathlib import Path
import tempfile


def main():
    from workbench_pack_program_studio.model import PackProgramError
    from workbench_shell.cleanroom_dev_loop import CleanroomDevLoopError
    from workbench_shell.diagnose_reproduce_cli import DiagnoseReproduceCliV2Error
    from workbench_shell.susy_mod_run import SusyModRunError
    from workbench_shell.susy_mod_check import SusyModCheckError
    from workbench_shell.susy_server_materialize import SusyServerMaterializationError
    refusals = (ValueError, PackProgramError, CleanroomDevLoopError,
                DiagnoseReproduceCliV2Error, SusyModRunError, SusyModCheckError,
                SusyServerMaterializationError)
    cases = (
        ("workbench_cleanroom_new_project.construction", "_target_from_uri", False),
        ("workbench_pack_program_studio.managed_session", "_file_uri_path", True),
        ("workbench_crucible_mixins.discovery_trace", "_artifact_path_from_uri", True),
        ("workbench_profile_supersymmetry.material_fluid_runtime_pair", "_local_uri", True),
        ("workbench_shell.blueprint_stage", "_local_retained_path", True),
        ("workbench_shell.cleanroom_dev_loop", "_local_uri", True),
        ("workbench_shell.diagnose_reproduce_cli", "_local_file_uri", True),
        ("workbench_shell.feature_change_workspace", "_local_uri", True),
        ("workbench_shell.feature_studio", "_request_file_path", True),
        ("workbench_shell.susy_mod_run", "_file_uri", True),
        ("workbench_shell.susy_mod_check", "_file_uri", True),
        ("workbench_shell.susy_server_materialize", "_local_uri", True),
    )
    with tempfile.TemporaryDirectory(prefix="wb-uri-") as temporary:
        directory = Path(temporary).resolve() / "Project 資料 100%"
        directory.mkdir()
        path = directory / "File with spaces 資料 100%.txt"
        path.write_bytes(b"exact local bytes\n")
        for package, name, labelled in cases:
            decoder = getattr(import_module(package), name)
            for uri in (path.as_uri(), path.as_uri().replace("file:///", "file://localhost/", 1)):
                arguments = (uri, "path conformance") if labelled else (uri,)
                assert decoder(*arguments) == path, (package, name, uri)
            for uri in ("https://example.invalid/file", "file://remote.invalid/file"):
                arguments = (uri, "path conformance") if labelled else (uri,)
                try:
                    decoder(*arguments)
                except refusals:
                    pass
                else:
                    raise AssertionError((package, name, "accepted nonlocal URI"))
        from workbench_shell.feature_studio import _workspace_from_uri, _read_bytes_uri
        from workbench_shell.feature_studio_snapshot import _is_local_file_uri
        assert _workspace_from_uri(directory.as_uri()) == directory
        assert _read_bytes_uri(path.as_uri(), "fixture", maximum_bytes=1024) == (path, path.read_bytes())
        assert _is_local_file_uri(path.as_uri())
    print("PASS installed local file URI round trips with drive/root, localhost, spaces, Unicode and percent signs; nonlocal schemes/authorities refused")


if __name__ == "__main__":
    main()
