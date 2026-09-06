"""poppler `pdftotext -bbox-layout` 로 페이지의 글자 좌표를 읽는다 (그림 잘라내기·2단 표 재구성이 함께 쓴다)."""

from __future__ import annotations

import html
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


_PAGE_RE = re.compile(r'<page\s+width="([\d.]+)"\s+height="([\d.]+)"')
_WORD_RE = re.compile(r'<word\s+xMin="([\d.]+)"\s+yMin="([\d.]+)"\s+xMax="([\d.]+)"\s+yMax="([\d.]+)"\s*>(.*?)</word>', re.S)


def parse_bbox_xml(xml_text: str) -> tuple[float, float, list[Word]]:
    """pdftotext -bbox-layout 출력은 특수문자 때문에 XML 파서가 깨질 수 있어 정규식으로 읽는다."""
    page = _PAGE_RE.search(xml_text)
    if page is None:
        raise ValueError("bbox 출력에 page 가 없다")
    width, height = float(page.group(1)), float(page.group(2))
    words = [
        Word(text=html.unescape(match.group(5)).strip(), x0=float(match.group(1)), y0=float(match.group(2)), x1=float(match.group(3)), y1=float(match.group(4)))
        for match in _WORD_RE.finditer(xml_text)
    ]
    return width, height, [w for w in words if w.text]


def page_words(pdf: Path, page: int) -> tuple[float, float, list[Word]]:
    result = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox-layout", str(pdf), "-"],
        capture_output=True, text=True, check=True,
    )
    return parse_bbox_xml(result.stdout)


def page_count(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    match = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(match.group(1)) if match else 0
