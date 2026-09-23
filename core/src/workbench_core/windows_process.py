"""Windows process-tree custody and anonymous-pipe transport for Core.

The launch gate is assigned to a non-inheritable kill-on-close job before Core
releases it. Its target and descendants inherit that job. Pipe readers only move
bytes into a bounded queue; the supervisor remains the sole evidence writer.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import queue
import selectors
import threading


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in (
        "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
        "ThisPeriodTotalKernelTime",
    )] + [(name, wintypes.DWORD) for name in (
        "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses",
        "TotalTerminatedProcesses",
    )]


class ProcessJob:
    """Own exactly one blocked gate and every process subsequently in its job."""

    def __init__(self, process):
        if os.name != "nt":
            raise OSError("Windows process jobs require the native Windows host")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def alive(self):
        counts = _Accounting()
        if not self.kernel.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(counts), ctypes.sizeof(counts), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return counts.ActiveProcesses != 0

    def terminate(self):
        # Windows has no equivalent of POSIX signals to an arbitrary process
        # group without a shared console. Terminate the exact owned job instead.
        if not self.kernel.TerminateJobObject(self.handle, 130):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


class PipeSelector:
    """Selector-shaped transport for Windows pipes, which select cannot read."""

    def __init__(self):
        self._keys = {}
        self._queue = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._threads = []
        self._ready = None

    def register(self, pipe, events, data):
        key = selectors.SelectorKey(pipe, pipe.fileno(), events, data)
        self._keys[key.fd] = key
        thread = threading.Thread(
            target=self._read_pipe, args=(key,),
            name="workbench-pipe-" + str(data), daemon=True,
        )
        self._threads.append(thread)
        thread.start()
        return key

    def _put(self, key, value):
        while not self._stop.is_set():
            try:
                self._queue.put((key, value), timeout=0.1)
                return
            except queue.Full:
                pass

    def _read_pipe(self, key):
        try:
            while not self._stop.is_set():
                chunk = os.read(key.fd, 64 * 1024)
                self._put(key, chunk)
                if not chunk:
                    return
        except OSError as exc:
            self._put(key, exc)

    def select(self, timeout=None):
        try:
            key, value = self._queue.get(timeout=timeout)
        except queue.Empty:
            return []
        if isinstance(value, BaseException):
            raise value
        self._ready = (key, value)
        return [(key, selectors.EVENT_READ)]

    def read(self, key):
        ready, self._ready = self._ready, None
        if ready is None or ready[0] != key:
            raise RuntimeError("pipe read does not match its ready event")
        return ready[1]

    def unregister(self, pipe):
        return self._keys.pop(pipe.fileno())

    def get_map(self):
        return self._keys

    def close(self):
        self._stop.set()
        # Core closes/terminates its job before this operation, so no owned
        # descendant can retain a writer and keep a pipe read blocked.
        for thread in self._threads:
            thread.join(timeout=1)
        for key in tuple(self._keys.values()):
            key.fileobj.close()
        self._keys.clear()
        if any(thread.is_alive() for thread in self._threads):
            raise OSError("owned pipe reader remained alive after process closure")
