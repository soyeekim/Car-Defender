"""judge 결과 임시 보관 (/tmp).

백엔드 계약의 `judge(JudgeInput) -> JudgeResult` 는 facts 를 갱신할 수 없다. 그래서 판정 시점의 Case State
(판정 상세·검색된 심의사례)를 case_id + 판정 버전으로 /tmp 에 남겨 두고, 다음 chat/write 호출이
같은 버전의 판정 스냅샷을 받으면 그 상세를 되살린다. 없으면(컨테이너 재시작 등) 스냅샷만으로 복원한다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.codec import pack_state
from state.case_state import CaseState

DEFAULT_TMP_DIR = "/tmp/car_defender_agent"


def agent_tmp_dir() -> Path:
    return Path(os.environ.get("CAR_DEFENDER_AGENT_TMP", DEFAULT_TMP_DIR))


class JudgeCache:
    def __init__(self, base_dir: Optional[str | Path] = None):
        self.base_dir = Path(base_dir) if base_dir else agent_tmp_dir() / "judgements"

    def _path(self, case_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in case_id)
        return self.base_dir / f"{safe}.json"

    def save(self, state: CaseState, version: int) -> Optional[Path]:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(state.case_id)
            payload = {"version": int(version), "saved_at": datetime.now(timezone.utc).isoformat(), "state": pack_state(state)}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            return path
        except OSError:
            return None

    def load(self, case_id: str, version: int) -> Optional[dict[str, Any]]:
        path = self._path(case_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if int(payload.get("version") or 0) != int(version):
            return None
        state = payload.get("state")
        return state if isinstance(state, dict) else None
