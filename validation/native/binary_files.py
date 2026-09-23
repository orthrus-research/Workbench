"""Keep byte custody exact on hosts whose low-level files default to text mode."""
from importlib import import_module
from pathlib import Path, PurePosixPath
import tempfile
import os
from types import SimpleNamespace
from unittest.mock import patch


def main():
    from workbench_core.host_services import install_local_host_services
    install_local_host_services()
    raw = b"line one\r\nline two\n\x1aafter control-Z\x00\xff"
    with tempfile.TemporaryDirectory(prefix="wb-bytes-") as temporary:
        root = Path(temporary)
        path = root / "evidence.bin"
        path.write_bytes(raw)
        cases = (
            ("workbench_atlas.material_census", "_read_regular"),
            ("workbench_blueprints.application_transaction", "_read_regular"),
            ("workbench_blueprints.interface", "_read_regular"),
            ("workbench_blueprints.lifecycle", "_read_regular"),
            ("workbench_blueprints.simulation", "_read_regular"),
            ("workbench_relay.cli", "_read_regular"),
            ("workbench_shell.feature_change_workspace", "_ordinary_bytes"),
            ("workbench_cleanroom_new_project.construction", "_read_regular"),
        )
        for package, name in cases:
            assert getattr(import_module(package), name)(path, "byte conformance") == raw, package
        from workbench_blueprints import fresh_project, convention_patch
        assert fresh_project._read_regular(path, "bytes", 4096) == raw
        assert convention_patch._read_regular(path, 4096, "bytes") == raw
        from workbench_atlas.experimental_anvil_worldgen_fingerprint import _read_stable_file
        assert _read_stable_file(path, 4096) == raw
        from workbench_runtime_explorer.providers import _safe_bytes
        assert _safe_bytes(path)[0] == raw
        for package in ("workbench_subsurface_studio.model", "workbench_worldgen_cockpit.model"):
            model = import_module(package)
            assert model.read_regular_file(path, context="bytes", maximum_bytes=4096)[0] == raw
            original_fstat = os.fstat
            def handle_view(descriptor):
                observed = original_fstat(descriptor)
                fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                values = {key: getattr(observed, key) for key in fields}
                values["st_ctime_ns"] += 1_000
                return SimpleNamespace(**values)
            # NTFS can return a stable handle ctime different from pathname stat.
            with patch("os.fstat", side_effect=handle_view):
                assert model.read_regular_file(path, context="bytes", maximum_bytes=4096)[0] == raw
            replacement = root / "replacement.bin"
            replacement.write_bytes(raw)
            original_close = os.close
            replaced = False
            def replace_after_close(descriptor):
                nonlocal replaced
                original_close(descriptor)
                if not replaced:
                    replaced = True
                    os.replace(replacement, path)
            error_type = getattr(model, "SubsurfaceStudioError", getattr(model, "CockpitError", ValueError))
            with patch("os.close", side_effect=replace_after_close):
                try:
                    model.read_regular_file(path, context="bytes", maximum_bytes=4096)
                except error_type as error:
                    assert "replaced" in str(error), str(error)
                else:
                    raise AssertionError(f"{package} accepted a replaced file")
        from workbench_shell.developer_feature import _read_workspace_file
        from workbench_profile_supersymmetry.quest_for_process import _regular_bytes
        relative = PurePosixPath(path.name)
        assert _read_workspace_file(root, relative, "bytes") == raw
        assert _regular_bytes(root, relative, "bytes") == raw
        created = root / "fresh.bin"
        fresh_project._atomic_new(created, raw)
        assert created.read_bytes() == raw
        assert fresh_project._read_regular(created, "created", 4096) == raw
        from workbench_pack_program_studio.managed_session import _write_fresh_bytes
        created = root / "managed.bin"
        _write_fresh_bytes(created, raw, mode=0o600)
        assert created.read_bytes() == raw
    print("PASS installed exact byte reads/writes: CRLF, LF, control-Z, NUL and non-UTF8 bytes")


if __name__ == "__main__":
    main()
