"""Agent 실행 로그 (가이드 113·114절).

각 LLM 호출과 Agent 액션을 JSONL로 남긴다. 저장 항목:
agent, task, model, prompt_version, case_id, input_hash,
output_schema_version, latency, token_usage, extra.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from settings import get_settings

OUTPUT_SCHEMA_VERSION = "2026-09-v1"


@dataclass
class CallMetrics:
    model: str = ""
    latency_sec: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    attempts: int = 1
    structured_mode: str = ""
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def input_hash(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (dict, list)):
            part = json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


class RunLogger:
    def __init__(self, path: Optional[Path] = None, enabled: bool = True):
        self.path = Path(path) if path else get_settings().run_log_path
        self.enabled = enabled
        self._lock = threading.Lock()
        self.records: list[dict[str, Any]] = []

    def log(
        self,
        *,
        agent: str,
        task: str,
        case_id: Optional[str] = None,
        model: str = "",
        prompt_version: str = "",
        metrics: Optional[CallMetrics] = None,
        input_digest: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "case_id": case_id,
            "agent": agent,
            "task": task,
            "model": model or (metrics.model if metrics else ""),
            "prompt_version": prompt_version,
            "input_hash": input_digest,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "latency_sec": round(metrics.latency_sec, 3) if metrics else None,
            "token_usage": {
                "input": metrics.input_tokens,
                "output": metrics.output_tokens,
                "total": metrics.total_tokens,
            }
            if metrics
            else None,
            "extra": extra or {},
        }
        self.records.append(record)
        if self.enabled:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record


_default_logger: Optional[RunLogger] = None


def get_run_logger() -> RunLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = RunLogger()
    return _default_logger
