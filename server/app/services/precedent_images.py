"""심의사례·인정기준 도표 그림(PNG) 찾기.

AI 쪽 `ai/build_case_images.py` 가 원본 PDF 에서 표를 잘라 `<rag_index>/images/<id>.png` 와 `manifest.json`
(id → 파일명) 을 만들어 둔다. 여기서는 그 폴더를 찾아 파일을 돌려줄 뿐, PDF 나 AI 코드는 건드리지 않는다.
그림이 없는 사례는 None → 응답의 imageUrl 이 null 이고 팝업은 글만 보여 준다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from app.config import get_settings
from app.security import create_precedent_image_token

MANIFEST_NAME = "manifest.json"
IMAGE_CAPTION = "출처: 손해보험협회 자동차사고 과실비율 인정기준 · 과실비율 심의사례"

_CANDIDATE_DIRS = (
    Path("/srv/ai/data/rag_index/images"),  # Docker: WORKDIR /srv + COPY ai ./ai
    Path(__file__).resolve().parents[3] / "ai" / "data" / "rag_index" / "images",  # 로컬 체크아웃
)
_cache: dict[str, object] = {"dir": None, "mtime": None, "images": {}}


def reset_precedent_images() -> None:
    _cache.update({"dir": None, "mtime": None, "images": {}})


def images_dir() -> Path | None:
    configured = get_settings().precedent_image_dir
    if configured:
        path = Path(configured)
        return path if path.is_dir() else None
    rag_index = os.environ.get("RAG_INDEX_DIR")
    if rag_index and (Path(rag_index) / "images").is_dir():
        return Path(rag_index) / "images"
    for candidate in _CANDIDATE_DIRS:
        if candidate.is_dir():
            return candidate
    return None


def _sanitize(precedent_id: str) -> str:
    # ai/rag/case_images.py::sanitize_key 와 같은 규칙 (manifest 가 없을 때의 대비책)
    return re.sub(r"[^0-9A-Za-z가-힣_\-]+", "_", precedent_id.strip()) or "unknown"


def _manifest(folder: Path) -> dict[str, dict]:
    path = folder / MANIFEST_NAME
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache["dir"] == folder and _cache["mtime"] == mtime:
        return _cache["images"]  # type: ignore[return-value]
    try:
        images = json.loads(path.read_text(encoding="utf-8")).get("images", {})
    except (OSError, ValueError):
        images = {}
    _cache.update({"dir": folder, "mtime": mtime, "images": images})
    return images


def image_file(precedent_id: str) -> Path | None:
    folder = images_dir()
    if folder is None or not precedent_id:
        return None
    entry = _manifest(folder).get(precedent_id) or {}
    name = str(entry.get("file") or f"{_sanitize(precedent_id)}.png")
    path = (folder / name).resolve()
    # manifest 가 조작돼도 폴더 밖 파일은 내보내지 않는다
    if folder.resolve() not in path.parents or not path.is_file():
        return None
    return path


def image_url(precedent_id: str) -> str | None:
    """프론트가 <img src> 에 그대로 넣는 서명된 상대 URL. 그림이 없으면 None."""
    if image_file(precedent_id) is None:
        return None
    return f"/api/v1/precedents/{quote(precedent_id, safe='')}/image?t={create_precedent_image_token(precedent_id)}"
