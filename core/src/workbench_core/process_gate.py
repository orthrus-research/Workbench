"""Hold a Core-supervised child until its owner custody is durable."""

from __future__ import annotations

import os
import hashlib
import stat
import sys


RELEASE_TOKEN = b"WORKBENCH-GO\n"
EXIT_UNRELEASED = 125
EXIT_EXEC_FAILED = 126


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    input_path = None
    input_digest = None
    if arguments[:1] == ["--input-file"] and len(arguments) >= 4:
        input_path, input_digest = arguments[1:3]
        arguments = arguments[3:]
    if not arguments or arguments[0] != "--" or len(arguments) < 2:
        print("workbench process gate has no exact target argv", file=sys.stderr)
        return EXIT_EXEC_FAILED
    target = arguments[1:]
    token = sys.stdin.buffer.read(len(RELEASE_TOKEN))
    if token != RELEASE_TOKEN:
        # EOF means the spawning frontend died before it durably bound this
        # PID.  Never execute the target in that state.
        return EXIT_UNRELEASED
    try:
        descriptor = os.open(
            input_path if input_path is not None else os.devnull,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | (getattr(os, "O_NOFOLLOW", 0) if input_path is not None else 0),
        )
        try:
            if input_path is not None:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                ):
                    raise OSError("process input must be one independent file")
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    if (
                        hashlib.file_digest(stream, "sha256").hexdigest()
                        != input_digest
                    ):
                        raise OSError("process input changed before custody release")
                os.lseek(descriptor, 0, os.SEEK_SET)
            os.dup2(descriptor, 0)
        finally:
            if descriptor != 0:
                os.close(descriptor)
        if os.name == "nt":
            import subprocess

            # Windows CRT exec does not preserve the gate's process handle.
            # Keep the assigned job leader alive until the target exits, return
            # its actual status, and let the target inherit the owned job.
            return subprocess.call(target, stdin=0, stdout=1, stderr=2, shell=False)
        os.execvpe(target[0], target, os.environ)
    except OSError as exc:
        print(f"workbench process gate could not exec target: {exc}", file=sys.stderr)
        return EXIT_EXEC_FAILED
    return EXIT_EXEC_FAILED  # pragma: no cover - exec replaces this process


if __name__ == "__main__":
    raise SystemExit(main())
