"""영상 타임스탬프 문자열 <-> 초 변환."""

from __future__ import annotations

import re
from typing import Optional

_TS = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$")
_SEC = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:s|sec|초)?\s*$", re.IGNORECASE)


def parse_timestamp(value) -> Optional[float]:
    """'00:05.8', '5.8', '5.8s', '0:05', 5.8 → 초. 해석 불가하면 None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = _TS.match(text)
    if match:
        hours = float(match.group(1) or 0)
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    match = _SEC.match(text)
    if match:
        return float(match.group(1))
    range_match = re.match(r"^\s*([^-~]+)[-~]", text)
    if range_match:
        return parse_timestamp(range_match.group(1))
    return None


def format_timestamp(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:04.1f}"
