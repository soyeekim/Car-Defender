import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.storage.base import CHUNK, validate_key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    async def put_file(self, key: str, src_path: Path) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, src_path, dst)
        return dst.stat().st_size

    async def put_bytes(self, key: str, data: bytes) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dst.write_bytes, data)
        return len(data)

    async def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        remaining = end - start + 1
        with open(self._path(key), "rb") as f:
            f.seek(start)
            while remaining > 0:
                data = await asyncio.to_thread(f.read, min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    async def read_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            await asyncio.to_thread(p.unlink)

    @asynccontextmanager
    async def local_path(self, key: str):
        yield self._path(key)

    def healthy(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False
