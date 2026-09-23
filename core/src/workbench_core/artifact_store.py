"""Content-addressed downloads shared by Workbench runtime provisioners."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class ArtifactStoreError(ValueError):
    """Raised when an immutable artifact cannot be retained exactly."""


def sha256_file(path: Path) -> tuple[str, int]:
    """Return the lowercase SHA-256 and byte size of one regular file."""

    digest = sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _verify_cached_artifact(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> None:
    actual_sha256, actual_size = sha256_file(path)
    if actual_size != expected_size:
        raise ArtifactStoreError(
            f"cached {label} size mismatch: expected {expected_size}, "
            f"found {actual_size}"
        )
    if actual_sha256 != expected_sha256:
        raise ArtifactStoreError(
            f"cached {label} SHA-256 mismatch: expected "
            f"{expected_sha256}, found {actual_sha256}"
        )


def fetch_verified_artifact(
    *,
    url: str,
    expected_sha256: str,
    expected_size: int,
    state_root: Path | str,
    label: str,
    timeout_seconds: float = 30.0,
    user_agent: str = "Workbench-Artifact-Store/0.1",
) -> tuple[Path, str]:
    """Fetch one exact artifact or reuse its verified cache entry."""

    if type(expected_size) is not int or expected_size <= 0:
        raise ArtifactStoreError(
            f"{label} size must be a positive integer"
        )
    if (
        len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ArtifactStoreError(
            f"{label} SHA-256 must be 64 lowercase hexadecimal characters"
        )
    if timeout_seconds <= 0:
        raise ArtifactStoreError("download timeout must be positive")
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "file"}:
        raise ArtifactStoreError(
            f"{label} URL must use HTTPS or a local file URI"
        )

    cache_root = (
        Path(state_root).expanduser().resolve()
        / "artifacts"
        / "sha256"
    )
    cache_path = cache_root / expected_sha256
    if cache_path.exists():
        if not cache_path.is_file() or cache_path.is_symlink():
            raise ArtifactStoreError(
                f"cached {label} is not a regular file"
            )
        _verify_cached_artifact(
            cache_path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=label,
        )
        return cache_path, "reused"

    cache_root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".download-",
        dir=cache_root,
    )
    temporary_path = Path(temporary_name)
    digest = sha256()
    size = 0
    try:
        request = Request(
            url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": user_agent,
            },
        )
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            with urlopen(request, timeout=timeout_seconds) as response:
                while chunk := response.read(DOWNLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    if size > expected_size:
                        raise ArtifactStoreError(
                            f"downloaded {label} exceeds its locked size"
                        )
                    digest.update(chunk)
                    destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        actual_sha256 = digest.hexdigest()
        if size != expected_size:
            raise ArtifactStoreError(
                f"downloaded {label} size mismatch: expected "
                f"{expected_size}, found {size}"
            )
        if actual_sha256 != expected_sha256:
            raise ArtifactStoreError(
                f"downloaded {label} SHA-256 mismatch: expected "
                f"{expected_sha256}, found {actual_sha256}"
            )
        try:
            os.link(temporary_path, cache_path)
            temporary_path.unlink()
        except FileExistsError:
            _verify_cached_artifact(
                cache_path,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                label=label,
            )
            temporary_path.unlink(missing_ok=True)
        return cache_path, "downloaded"
    except ArtifactStoreError:
        temporary_path.unlink(missing_ok=True)
        raise
    except (HTTPError, URLError, OSError) as exc:
        temporary_path.unlink(missing_ok=True)
        raise ArtifactStoreError(
            f"cannot download {label}: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
