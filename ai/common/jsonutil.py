"""LLM 응답에서 JSON 객체를 안전하게 추출하는 유틸리티."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# 문자열 안의 "http://" 같은 것을 건드리지 않도록 구분자(,{[ 공백) 뒤에 오는 // 주석만 제거한다
_LINE_COMMENT = re.compile(r"(?:(?<=[,{\[\s])|^)//[^\n]*", re.MULTILINE)


# 오류 지점이 본문 끝에서 이만큼 안쪽이면 "잘린 출력"이 아니라 "중간에 깨진 출력"이다
_TAIL_TOLERANCE = 8


class MidDocumentJSONError(ValueError):
    """JSON 이 본문 중간에서 깨졌다 — 잘린 꼬리를 닫는 식으로 살리면 그 뒤의 필드가 통째로 사라진다."""


def _container_stack(text: str, end: int) -> tuple[list[str], bool]:
    """text[:end] 까지 열려 있는 컨테이너 스택과, 그 지점이 문자열 안인지."""
    stack: list[str] = []
    in_string = escape = False
    for char in text[:end]:
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
        elif char in "[{":
            stack.append(char)
        elif char in "]}" and stack:
            stack.pop()
    return stack, in_string


def repair_mismatched_closers(text: str, max_fixes: int = 6) -> Any:
    """모델이 객체를 닫아야 할 자리에 `]` 를 쓰거나(`{...],`) 값 사이 콤마를 빠뜨린 응답을 오류 지점에서 국소 수정해 파싱한다.

    Gemini 는 온도 0 에서 같은 실수를 반복하므로(예: vehicles 배열의 첫 객체를 `}` 대신 `]` 로 닫음) 다시 물어도 소용이 없었다.
    오류 지점의 닫는 괄호가 가장 안쪽 컨테이너와 맞지 않으면 기대되는 괄호로 바꾸고, 값 앞에서 구분자를 기다리면 콤마를 넣는다.
    본문 뒤쪽 필드를 하나도 잃지 않는다. 고칠 수 없으면 마지막 JSONDecodeError 를 낸다."""
    fixed = text
    for _ in range(max_fixes):
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as exc:
            stack, in_string = _container_stack(fixed, exc.pos)
            if in_string or not stack:
                raise
            index = exc.pos
            while index < len(fixed) and fixed[index] in " \t\r\n":
                index += 1
            following = fixed[index] if index < len(fixed) else ""
            expected = "}" if stack[-1] == "{" else "]"
            if following in "]}" and following != expected:
                fixed = fixed[:index] + expected + fixed[index + 1 :]
            elif following in '{["' and "delimiter" in exc.msg:
                fixed = fixed[:index] + "," + fixed[index:]
            else:
                raise
    return json.loads(fixed)


def _lenient_loads(candidate: str, *, truncated_only: bool = False) -> Any:
    """흔한 LLM JSON 오류(코드펜스, 후행 콤마, // 주석, 잘린 꼬리)를 보정하며 파싱한다.

    truncated_only=True 면 오류가 본문 끝(진짜 잘림)일 때만 살리고, 중간에서 깨진 경우는 MidDocumentJSONError 를 낸다.
    (Gemini 가 vehicles 배열 중간에 `]` 를 잘못 넣은 응답을 '잘린 출력'으로 살려 상대 차량·충돌 정보가 조용히 사라진 일이 있었다.
    호출자는 이 오류를 받아 repair 호출로 보낸다.)"""
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    repaired = _LINE_COMMENT.sub("", candidate)
    repaired = _TRAILING_COMMA.sub(r"\1", repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        if exc.pos < len(repaired) - _TAIL_TOLERANCE:
            # 본문 중간의 괄호·콤마 실수는 국소 수정으로 뒤쪽 필드까지 전부 살린다
            try:
                return repair_mismatched_closers(repaired)
            except json.JSONDecodeError:
                pass
        if truncated_only and exc.pos < len(repaired) - _TAIL_TOLERANCE:
            raise MidDocumentJSONError(f"JSON 이 {exc.pos}/{len(repaired)} 위치에서 깨졌습니다: {exc.msg}") from exc
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


def parse_json_object(text: str, *, truncated_only: bool = False) -> dict[str, Any]:
    """코드펜스, 앞뒤 설명문이 섞여 있어도 첫 번째 JSON 객체를 파싱한다. truncated_only 는 _lenient_loads 참고."""
    if text is None:
        raise ValueError("응답이 비어 있습니다.")
    candidate = text.strip()
    if not candidate:
        raise ValueError("응답이 비어 있습니다.")

    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    try:
        parsed = _lenient_loads(candidate, truncated_only=truncated_only)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("응답에서 JSON 객체를 찾지 못했습니다.")
    parsed = _lenient_loads(candidate[start : end + 1], truncated_only=truncated_only)
    if not isinstance(parsed, dict):
        raise ValueError("JSON 응답이 객체가 아닙니다.")
    return parsed


def compact_json(data: Any, *, indent: int | None = None, max_chars: int | None = None) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=indent, default=str)
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "…(truncated)"
    return text
