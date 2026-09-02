from pathlib import Path

from app.config import get_settings
from app.storage.base import StorageBackend

_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            from app.storage.s3 import S3Storage

            _storage = S3Storage(s.s3_bucket or "", s.s3_region)
        else:
            from app.storage.local import LocalStorage

            _storage = LocalStorage(Path(s.storage_local_dir))
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None


__all__ = ["StorageBackend", "get_storage", "reset_storage"]
