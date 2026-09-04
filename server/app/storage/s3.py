import asyncio
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.storage.base import CHUNK, validate_key


class S3Storage:
    def __init__(self, bucket: str, region: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", region_name=region)

    async def put_file(self, key: str, src_path: Path) -> int:
        validate_key(key)
        await asyncio.to_thread(self.client.upload_file, str(src_path), self.bucket, key)
        return src_path.stat().st_size

    async def put_bytes(self, key: str, data: bytes) -> int:
        validate_key(key)
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data)
        return len(data)

    async def size(self, key: str) -> int:
        try:
            head = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as e:
            raise FileNotFoundError(key) from e
        return int(head["ContentLength"])

    async def read_range(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}")
        body = obj["Body"]
        while True:
            data = await asyncio.to_thread(body.read, CHUNK)
            if not data:
                break
            yield data

    async def read_bytes(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    @asynccontextmanager
    async def local_path(self, key: str):
        suffix = Path(key).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            await asyncio.to_thread(self.client.download_file, self.bucket, key, str(tmp_path))
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)

    def healthy(self) -> bool:
        return bool(self.bucket)
