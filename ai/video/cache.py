"""Video 분석 결과 캐시 (가이드 61·115절).

cache key = video_hash + backend + model + prompt_version (+ focus key)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from video.schemas import VideoResult


class VideoResultCache:
    def __init__(self, directory: Path, enabled: bool = True):
        self.directory = Path(directory)
        self.enabled = enabled

    @staticmethod
    def make_key(
        *,
        video_hash: str,
        backend: str,
        model: str,
        prompt_version: str,
        focus_key: str = "",
        extra: str = "",
    ) -> str:
        raw = "|".join([video_hash, backend, model, prompt_version, focus_key, extra])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Optional[VideoResult]:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            result = VideoResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        result.cached = True
        return result

    def put(self, key: str, result: VideoResult) -> Optional[Path]:
        if not self.enabled:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    def clear(self) -> int:
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.glob("*.json"):
            path.unlink()
            removed += 1
        return removed
