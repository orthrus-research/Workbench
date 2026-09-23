"""Host-specific private-path and durability mechanics for Service V3.

These helpers are deliberately mechanical. They establish local filesystem
custody for the service runtime without interpreting any domain evidence or
authorizing a Workbench action.
"""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path
import re
import stat


from workbench_api.host_filesystem import HostFilesystemError
from .filesystem_paths import native_path, resolved_path


@contextmanager
def file_lease(descriptor: int, *, exclusive: bool):
    """Hold a nonblocking shared/exclusive lease without changing file bytes."""
    if os.name != "nt":
        import fcntl
        fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        return

    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel32.LockFileEx.restype = wintypes.BOOL
    kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                     wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    kernel32.UnlockFileEx.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(descriptor)
    offset = Overlapped()
    # LockFileEx supports shared leases, read-only handles and ranges beyond EOF.
    # https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex
    flags = 1 | (2 if exclusive else 0)  # FAIL_IMMEDIATELY | EXCLUSIVE_LOCK
    if not kernel32.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(offset)):
        if ctypes.get_last_error() == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError("another process holds the file lease")
        _raise_windows("LockFileEx")
    try:
        yield
    finally:
        if not kernel32.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(offset)):
            _raise_windows("UnlockFileEx")


def _raise_windows(label: str) -> None:
    error = ctypes.get_last_error()
    raise HostFilesystemError(error, f"{label} failed with Windows error {error}")


@lru_cache(maxsize=1)
def _windows_current_user_sid() -> str:
    token_query = 0x0008
    token_user = 1
    error_insufficient_buffer = 122

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]

    class TokenUser(ctypes.Structure):
        _fields_ = [("User", SidAndAttributes)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
    ):
        _raise_windows("OpenProcessToken")
    try:
        size = wintypes.DWORD()
        if advapi32.GetTokenInformation(
            token, token_user, None, 0, ctypes.byref(size)
        ) or ctypes.get_last_error() != error_insufficient_buffer:
            _raise_windows("GetTokenInformation(size)")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, token_user, buffer, size, ctypes.byref(size)
        ):
            _raise_windows("GetTokenInformation")
        user = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents
        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(
            user.User.Sid, ctypes.byref(sid_text)
        ):
            _raise_windows("ConvertSidToStringSidW")
        try:
            return sid_text.value
        finally:
            kernel32.LocalFree(sid_text)
    finally:
        kernel32.CloseHandle(token)


def _windows_security_descriptor(path: Path) -> str:
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    error_insufficient_buffer = 122
    sddl_revision_1 = 1
    information = owner_security_information | dacl_security_information
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetFileSecurityW.restype = wintypes.BOOL
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    size = wintypes.DWORD()
    if advapi32.GetFileSecurityW(str(native_path(path)), information, None, 0, ctypes.byref(size)):
        raise HostFilesystemError("GetFileSecurityW returned an empty descriptor")
    if ctypes.get_last_error() != error_insufficient_buffer:
        _raise_windows("GetFileSecurityW(size)")
    descriptor = ctypes.create_string_buffer(size.value)
    if not advapi32.GetFileSecurityW(
        str(native_path(path)), information, descriptor, size, ctypes.byref(size)
    ):
        _raise_windows("GetFileSecurityW")
    text = wintypes.LPWSTR()
    text_length = wintypes.DWORD()
    if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor,
        sddl_revision_1,
        information,
        ctypes.byref(text),
        ctypes.byref(text_length),
    ):
        _raise_windows("ConvertSecurityDescriptorToStringSecurityDescriptorW")
    try:
        return text.value
    finally:
        kernel32.LocalFree(text)


def _windows_private(path: Path, *, directory: bool) -> bool:
    try:
        descriptor = _windows_security_descriptor(path)
        user_sid = _windows_current_user_sid()
    except (HostFilesystemError, OSError):
        return False
    return _windows_private_descriptor(
        descriptor,
        user_sid=user_sid,
        directory=directory,
    )


def _windows_private_descriptor(
    descriptor: str,
    *,
    user_sid: str,
    directory: bool,
) -> bool:
    """Validate the meaning of a private DACL without depending on SDDL order.

    Windows documents the SDDL tokens but does not make their serialized order
    an identity boundary.  In particular, different host builds may render the
    object/container inheritance flags in either order, use the SYSTEM SID
    instead of its ``SY`` alias, or render full access as its numeric mask.
    Those forms describe the same protected two-principal DACL.
    """

    if (
        type(descriptor) is not str
        or type(user_sid) is not str
        or "D:" not in descriptor
    ):
        return False
    dacl = descriptor.split("D:", 1)[1]
    if "P" not in dacl.split("(", 1)[0]:
        return False
    aces = re.findall(r"\(([^()]*)\)", dacl)
    if len(aces) != 2:
        return False
    trustees: set[str] = set()
    expected_flags = {"OI", "CI"} if directory else set()
    for ace in aces:
        fields = ace.split(";")
        flags = fields[1] if len(fields) == 6 else ""
        flag_tokens = re.findall(r"[A-Z]{2}", flags)
        rights = fields[2] if len(fields) == 6 else ""
        full_access = rights.lower() == "fa"
        if rights.lower().startswith("0x"):
            try:
                full_access = int(rights, 16) == 0x1F01FF
            except ValueError:
                full_access = False
        if (
            len(fields) != 6
            or fields[0] != "A"
            or "".join(flag_tokens) != flags
            or set(flag_tokens) != expected_flags
            or len(flag_tokens) != len(expected_flags)
            or not full_access
            or fields[3]
            or fields[4]
        ):
            return False
        trustees.add(fields[5])
    return trustees in ({"SY", user_sid}, {"S-1-5-18", user_sid})


def _secure_windows(path: Path, *, directory: bool) -> None:
    dacl_security_information = 0x00000004
    protected_dacl_security_information = 0x80000000
    sddl_revision_1 = 1
    user_sid = _windows_current_user_sid()
    flags = "OICI" if directory else ""
    sddl = f"D:P(A;{flags};FA;;;SY)(A;{flags};FA;;;{user_sid})"
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    advapi32.SetFileSecurityW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    descriptor = wintypes.LPVOID()
    size = wintypes.DWORD()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        sddl_revision_1,
        ctypes.byref(descriptor),
        ctypes.byref(size),
    ):
        _raise_windows("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        if not advapi32.SetFileSecurityW(
            str(native_path(path)),
            dacl_security_information | protected_dacl_security_information,
            descriptor,
        ):
            _raise_windows("SetFileSecurityW")
    finally:
        kernel32.LocalFree(descriptor)
    if not _windows_private(path, directory=directory):
        observed = _windows_security_descriptor(path)
        raise HostFilesystemError(
            "Windows path did not retain its private DACL: " + observed
        )


def private_path(path: Path, *, directory: bool) -> bool:
    """Return whether one ordinary path is accessible only to its owner/system."""

    if not isinstance(path, Path) or not path.is_absolute() or native_path(path).is_symlink():
        return False
    try:
        metadata = native_path(path).stat()
    except OSError:
        return False
    if directory != stat.S_ISDIR(metadata.st_mode):
        return False
    if not directory and not stat.S_ISREG(metadata.st_mode):
        return False
    if os.name == "nt":
        return _windows_private(path, directory=directory)
    return metadata.st_mode & 0o077 == 0 and (
        not hasattr(os, "geteuid") or metadata.st_uid == os.geteuid()
    )


def secure_private_path(path: Path, *, directory: bool) -> Path:
    """Apply and verify the exact host-private protection for one path."""

    if not isinstance(path, Path) or not path.is_absolute() or native_path(path).is_symlink():
        raise HostFilesystemError("private path must be absolute and non-symbolic")
    if directory:
        native_path(path).mkdir(parents=True, mode=0o700, exist_ok=True)
    if not native_path(path).exists():
        raise HostFilesystemError("private path does not exist")
    if os.name == "nt":
        _secure_windows(path, directory=directory)
    else:
        os.chmod(path, 0o700 if directory else 0o600)
    if not private_path(path, directory=directory):
        raise HostFilesystemError("path is not owner-private")
    return resolved_path(path, strict=True)


def secure_private_endpoint(path: Path) -> None:
    """Protect one already-bound local endpoint without assuming file kind."""

    if not isinstance(path, Path) or not path.is_absolute() or native_path(path).is_symlink():
        raise HostFilesystemError("endpoint path must be absolute and non-symbolic")
    if not native_path(path).exists():
        raise HostFilesystemError("endpoint path does not exist")
    if os.name == "nt":
        _secure_windows(path, directory=False)
    else:
        os.chmod(path, 0o600)
        if native_path(path).stat().st_mode & 0o077:
            raise HostFilesystemError("endpoint path is not owner-private")


def fsync_directory(path: Path) -> None:
    """Flush one directory using the host's supported directory handle."""

    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    invalid_handle = wintypes.HANDLE(-1).value
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(
        str(native_path(path)),
        generic_read | generic_write,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_flag_backup_semantics,
        None,
    )
    if handle == invalid_handle:
        _raise_windows("CreateFileW(directory)")
    try:
        if not kernel32.FlushFileBuffers(handle):
            _raise_windows("FlushFileBuffers(directory)")
    finally:
        kernel32.CloseHandle(handle)


__all__ = [
    "HostFilesystemError",
    "fsync_directory",
    "private_path",
    "secure_private_endpoint",
    "secure_private_path",
]
