"""File-backed native-tool evidence inside an owner's existing private attempt."""

from contextlib import ExitStack, contextmanager
from hashlib import sha256
import os
from pathlib import Path
import re

from workbench_api.processes import CapturedProcessResult, ProcessError, ProcessOutput
from . import check_storage as storage
from .host_filesystem import fsync_directory, private_path, secure_private_path
from .sessions import EphemeralSession

FORMAT = "workbench-process-capture-v1"
STREAMS = ("stdout", "stderr")


class FileCapture(EphemeralSession):
    def __init__(self, directory, binding, limit):
        super().__init__()
        self.directory = Path(directory)
        if not self.directory.is_absolute() or not isinstance(binding, str) or not binding.strip():
            raise ProcessError("file capture requires an absolute destination and owner binding")
        parent = storage.ordinary(self.directory.parent, directory=True)
        if not private_path(parent, directory=True):
            raise ProcessError("file capture requires owner-private parent storage")
        self.directory.mkdir(mode=0o700)  # Never reuse or replace a prior capture.
        secure_private_path(self.directory, directory=True)
        fsync_directory(parent)
        self.binding, self.limit = binding, limit
        self.stack = ExitStack()
        self.counts = dict.fromkeys(STREAMS, 0)
        self.hashes = {name: sha256() for name in STREAMS}
        try:
            storage.write_json(self.directory / "started.json", {"format": FORMAT, "binding": binding})
            self.streams = {}
            for name in STREAMS:
                path = self.directory / (name + ".raw")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
                self.streams[name] = self.stack.enter_context(os.fdopen(fd, "wb"))
                secure_private_path(path, directory=False)
            fsync_directory(self.directory)
        except BaseException:
            self.stack.close()
            raise

    def write_raw(self, stream, data):
        if stream in self.streams:
            if self.limit is not None and self.counts[stream] + len(data) > self.limit:
                raise ProcessError("native-tool output exceeded its byte bound")
            if self.streams[stream].write(data) != len(data):
                raise OSError("native-tool output could not be completely written")
            self.hashes[stream].update(data)
            self.counts[stream] += len(data)
        return super().write_raw(stream, data)

    def commit(self, *, exit_code=None, failure=None):
        try:
            for stream in self.streams.values():
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            self.stack.close()
        streams = {}
        for name in STREAMS:
            path = storage.ordinary(self.directory / (name + ".raw"))
            if path.stat().st_size != self.counts[name]:
                raise ProcessError("captured output size changed during execution")
            streams[name] = {"path": path.name, "bytes": self.counts[name], "sha256": self.hashes[name].hexdigest()}
        record = storage.seal("process-capture", {
            "format": FORMAT, "binding": self.binding,
            "state": "complete" if failure is None else "incomplete",
            "exit_code": exit_code, "failure": failure, "streams": streams,
        })
        storage.write_json(self.directory / "capture.json", record)
        return record


def load(directory, *, binding, expected_id=None):
    """Validate a capture's owner and metadata; reads verify stream bytes separately."""
    directory = storage.ordinary(directory, directory=True)
    if storage.read_json(directory / "started.json") != {"format": FORMAT, "binding": binding}:
        raise ProcessError("native capture belongs to another owner request")
    record = storage.read_json(directory / "capture.json")
    if (set(record) != {"format", "binding", "state", "exit_code", "failure", "streams", "id"}
            or record["format"] != FORMAT or record["binding"] != binding
            or storage.seal("process-capture", {k: v for k, v in record.items() if k != "id"}) != record
            or record["state"] not in {"complete", "incomplete"}
            or set(record["streams"]) != set(STREAMS)
            or expected_id is not None and record["id"] != expected_id):
        raise ProcessError("native capture metadata changed or is unsupported")
    if record["state"] == "complete" and (type(record["exit_code"]) is not int or record["failure"] is not None):
        raise ProcessError("native capture has no complete process outcome")
    for name, row in record["streams"].items():
        if (set(row) != {"path", "bytes", "sha256"} or row["path"] != name + ".raw"
                or type(row["bytes"]) is not int or row["bytes"] < 0
                or not re.fullmatch("[0-9a-f]{64}", str(row["sha256"]))
                or storage.ordinary(directory / row["path"]).stat().st_size != row["bytes"]):
            raise ProcessError("native capture stream descriptor changed")
    return record


def result(directory, record):
    if record["state"] != "complete":
        raise ProcessError("native capture is incomplete")
    return CapturedProcessResult(record["exit_code"], record["id"], record["binding"],
        *(ProcessOutput(Path(directory) / record["streams"][name]["path"],
                        record["streams"][name]["bytes"], record["streams"][name]["sha256"]) for name in STREAMS))


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class _Reader:
    def __init__(self, stream):
        self.stream, self.digest, self.size = stream, sha256(), 0

    def read(self, size=-1):
        raw = self.stream.read(size)
        self.digest.update(raw)
        self.size += len(raw)
        return raw


@contextmanager
def open_output(output):
    """Hash while reading; reject replacement, truncation or changed bytes."""
    if not isinstance(output, ProcessOutput):
        raise ProcessError("expected a Core process output reference")
    path = storage.ordinary(output.path)
    before = path.stat()
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                          | getattr(os, "O_BINARY", 0)), "rb") as stream:
        opened = os.fstat(stream.fileno())
        # Native Windows stat/fstat can report different ctime meanings. Bind
        # their common file identity, then compare each clock to its own initial
        # observation below; neither content nor replacement checks are skipped.
        common = slice(None, 4 if os.name == "nt" else None)
        if _identity(opened)[common] != _identity(before)[common]:
            raise ProcessError("captured output changed before reading")
        reader = _Reader(stream)
        yield reader
        while reader.read(1024 * 1024):
            pass
        if (reader.size != output.size or reader.digest.hexdigest() != output.sha256
                or _identity(os.fstat(stream.fileno())) != _identity(opened)
                or _identity(storage.ordinary(path).stat()) != _identity(before)):
            raise ProcessError("captured output changed while reading")


def retained_files(directory, *, binding, expected_id=None, verify=False):
    """Include complete or interrupted bytes in the owner's existing evidence closure."""
    directory = storage.ordinary(directory, directory=True)
    if storage.read_json(directory / "started.json") != {"format": FORMAT, "binding": binding}:
        raise ProcessError("native capture belongs to another owner request")
    names = {path.name for path in directory.iterdir()}
    if not names <= {"started.json", "stdout.raw", "stderr.raw", "capture.json"}:
        raise ProcessError("native capture contains unrecognized evidence")
    if "capture.json" in names:
        record = load(directory, binding=binding, expected_id=expected_id)
        if expected_id is not None and record["state"] != "complete":
            raise ProcessError("native response refers to an incomplete capture")
        if verify:
            for row in record["streams"].values():
                with open_output(ProcessOutput(directory / row["path"], row["bytes"], row["sha256"])):
                    pass
    elif expected_id is not None:
        raise ProcessError("native response capture was not committed")
    return {"native-process-" + name: storage.ordinary(directory / name) for name in sorted(names)}
