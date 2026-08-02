import asyncio
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO, Self

from fastapi import UploadFile

from app.core.common.exceptions import BadRequestError

CHUNK_SIZE = 1024 * 1024  # 1 MiB chunks for fast buffered writes without thread overhead


class ProcessingFile:
    """A temporary file moving through the upload pipeline."""

    def __init__(self, path: Path, size: int, hash: str | None = None) -> None:
        self.path = path
        self.size = size
        self.hash = hash

    @classmethod
    async def from_upload(cls, upload: UploadFile, max_bytes: int) -> Self:
        """Spool UploadFile to a named temp file with size enforcement and hashing."""
        temp = NamedTemporaryFile(delete=False)
        temp_path = Path(temp.name)
        hasher = hashlib.sha256()

        try:
            total_size = 0
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break

                total_size += len(chunk)
                if total_size > max_bytes:
                    temp.close()
                    temp_path.unlink()
                    raise BadRequestError(
                        f"File size exceeds maximum of {max_bytes // (1024 * 1024)} MiB"
                    )
                hasher.update(chunk)
                await asyncio.to_thread(temp.write, chunk)

            temp.close()
            return cls(temp_path, total_size, hash=hasher.hexdigest())
        except BaseException:
            temp.close()
            temp_path.unlink(missing_ok=True)
            raise

    async def replace_with(self, new_path: Path) -> None:
        """Delete the old file and invalidate metadata derived from its bytes."""
        if self.path != new_path:
            for _ in range(5):
                if new_path.exists():
                    break
                await asyncio.sleep(0.05)
            if not new_path.exists():
                raise FileNotFoundError(f"Replacement file was not created: {new_path}")

            new_size = new_path.stat().st_size
            self.path.unlink(missing_ok=True)
            self.path = new_path
            self.size = new_size
            self.hash = None

    async def sha256(self) -> str:
        """Compute SHA-256 by chunked reading in a separate thread."""
        if self.hash:
            return self.hash

        def _hash_file() -> str:
            hasher = hashlib.sha256()
            buffer = bytearray(CHUNK_SIZE)
            with open(self.path, "rb") as f:
                while True:
                    n = f.readinto(buffer)
                    if n == 0:
                        break
                    hasher.update(buffer[:n])
            return hasher.hexdigest()

        digest = await asyncio.to_thread(_hash_file)
        self.hash = digest
        return digest

    def read_bytes(self) -> bytes:
        """Read full file for small files."""
        return self.path.read_bytes()

    def open(self, mode: str = "rb") -> IO[bytes]:
        """Context manager returning a file handle."""
        return open(self.path, mode)  # noqa: SIM115

    def cleanup(self) -> None:
        """Remove temp file."""
        self.path.unlink(missing_ok=True)
