"""Native filesystem spellings, kept separate from retained path identities.

Windows extended-length prefixes are an IO detail. They must never be persisted
as provenance, exposed as file URIs, or used to bypass ordinary-path admission.
"""

from workbench_api.filesystem_paths import _windows_access_path, native_path, resolved_path


__all__ = ("_windows_access_path", "native_path", "resolved_path")
