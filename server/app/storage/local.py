import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.storage.base import CHUNK, validate_key


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        validate_key(key)
        root = os.path.normpath(os.path.abspath(self.root))
        p = os.path.normpath(os.path.join(root, key))
        if not (p == root or p.startswith(root + os.sep)):
            raise ValueError(f"잘못된 스토리지 키: {key!r}")
        return Path(p)

    async def put_file(self, key: str, src_path: Path) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, src_path, dst)
        st = await asyncio.to_thread(dst.stat)
        return st.st_size

    async def put_bytes(self, key: str, data: bytes) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dst.write_bytes, data)
        return len(data)

    async def size(self, key: str) -> int:
        st = await asyncio.to_thread(self._path(key).stat)
        return st.st_size

    @staticmethod
    def _open_at(path: Path, start: int):
        f = open(path, "rb")
        f.seek(start)
        return f

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        if start > end:
            return
        remaining = end - start + 1
        f = await asyncio.to_thread(self._open_at, self._path(key), start)
        try:
            while remaining > 0:
                data = await asyncio.to_thread(f.read, min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            await asyncio.to_thread(f.close)

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
        probe = self.root / ".health"
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(probe, "w") as f:
                f.write("ok")
            with open(probe) as f:
                f.read()
            return True
        except OSError:
            return False
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
