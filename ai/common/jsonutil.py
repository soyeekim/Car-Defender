"""LLM 응답에서 JSON 객체를 안전하게 추출하는 유틸리티."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# 문자열 안의 "http://" 같은 것을 건드리지 않도록 구분자(,{[ 공백) 뒤에 오는 // 주석만 제거한다
_LINE_COMMENT = re.compile(r"(?:(?<=[,{\[\s])|^)//[^\n]*", re.MULTILINE)


def _lenient_loads(candidate: str) -> Any:
    """흔한 LLM JSON 오류(코드펜스, 후행 콤마, // 주석, 잘린 꼬리)를 보정하며 파싱한다."""
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    repaired = _LINE_COMMENT.sub("", candidate)
    repaired = _TRAILING_COMMA.sub(r"\1", repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        # 잘린 출력: 오류 지점 이전까지의 마지막 완전한 값에서 열린 괄호를 닫아 본다
        head = repaired[: exc.pos]
        cut = max(head.rfind(","), head.rfind("}"), head.rfind("]"))
        if cut > 0:
            trimmed = head[: cut + 1].rstrip().rstrip(",")
            depth = []
            in_string = False
            escape = False
            for char in trimmed:
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char in "{[":
                    depth.append("}" if char == "{" else "]")
                elif char in "}]" and depth:
                    depth.pop()
            if not in_string:
                closed = trimmed + "".join(reversed(depth))
                try:
                    return json.loads(_TRAILING_COMMA.sub(r"\1", closed))
                except json.JSONDecodeError:
                    pass
        raise


def parse_json_object(text: str) -> dict[str, Any]:
    """코드펜스, 앞뒤 설명문이 섞여 있어도 첫 번째 JSON 객체를 파싱한다."""
    if text is None:
        raise ValueError("응답이 비어 있습니다.")
    candidate = text.strip()
    if not candidate:
        raise ValueError("응답이 비어 있습니다.")

    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = _lenient_loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("응답에서 JSON 객체를 찾지 못했습니다.")
    parsed = _lenient_loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON 응답이 객체가 아닙니다.")
    return parsed


def compact_json(data: Any, *, indent: int | None = None, max_chars: int | None = None) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=indent, default=str)
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "…(truncated)"
    return text
