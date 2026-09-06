"""심의사례 PDF 페이지를 표·2단 구조를 알고 다시 읽는다.

`pdftotext` 의 일반 출력은 "사례 개요" 표의 왼쪽 라벨(심의번호·사고내용·참고·인정기준·도표번호)을 오른쪽 칸 문장 중간에
끼워 넣고, "주장 내용"·"입증 자료"의 두 단을 한 줄씩 섞어 낸다. 그래서 226건 중 209건의 사고내용이 앞부분이 잘리거나
낱말이 갈라진 채 인덱스에 들어갔다. 여기서는 글자 좌표(pdftotext -bbox-layout)로 칸을 나눠 읽는다.

페이지 구조 (사례 첫 쪽)
    ┌ 제목 / 참고기준 상자(도표번호) ─────────────────────┐
    │ 사례 개요                                          │
    │ 심의번호 | 2018-047765   결정비율 A : B = 40 : 60   │
    │ 사고내용 | • 회전교차로에서 … 사고임                 │
    │ 참고     | [그림]      회전교차로의 경우 … 정한다.   │
    │ 인정기준 |             기본비율 A : B = 30 : 70     │
    │ 266      |                                         │
    │ 주장 내용                                          │
    │ 청구인 (왼쪽 단)        │ 피청구인 (오른쪽 단)       │
    └────────────────────────────────────────────────────┘
둘째 쪽: 입증 자료(2단) → 주요 쟁점 → 결정 근거 → 결정 이유 → (수정요소·관련 사례) — 한 단.

결과는 기존 파서(rag/case_documents.py)가 그대로 읽을 수 있는 줄 단위 텍스트다: 섹션 제목이 한 줄, 그 아래 내용.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from common.korean_text import join_lines, repair_spacing
from rag.pdf_words import Word, page_count, page_words

_HEADER_Y = 40.0  # 머리글(목차보기·쪽수·러닝타이틀)
_FOOTER_MARGIN = 22.0
_TAB_MARGIN = 30.0  # 오른쪽 여백의 세로 장 표시("1. 자동차와 자동차의 사고")
_LINE_TOLERANCE = 3.0
_OVERVIEW_LABELS = {"심의번호", "사고내용", "참고", "인정기준", "사고", "내용"}
_SECTION_TITLES = ("사례 개요", "주장 내용", "입증 자료", "주요 쟁점", "결정 근거", "결정 이유", "수정 요소", "수정요소", "관련 심의사례", "관련 사례", "참고 사항", "참고사항")
_TWO_COLUMN = {"주장 내용", "입증 자료"}
_BULLET = re.compile(r"^[•●▪◦]")


def _lines(words: list[Word]) -> list[list[Word]]:
    """y 로 묶어 줄을 만든다 (같은 줄의 글자는 y 중심이 거의 같다)."""
    ordered = sorted(words, key=lambda w: (w.yc, w.x0))
    lines: list[list[Word]] = []
    for word in ordered:
        if lines and abs(lines[-1][0].yc - word.yc) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _text(line: list[Word]) -> str:
    return " ".join(w.text for w in line)


def _section_title(line: list[Word]) -> Optional[str]:
    if not line or line[0].x0 > 90:
        return None
    text = _text(line[:2])
    for title in _SECTION_TITLES:
        if text.startswith(title) or text.replace(" ", "").startswith(title.replace(" ", "")):
            return title
    return None


def _items(lines: list[list[Word]]) -> list[str]:
    """불릿으로 시작하는 줄을 항목으로, 이어지는 줄은 앞 항목에 잇는다 (단어 중간 줄바꿈은 사전으로 붙인다)."""
    items: list[str] = []
    for line in lines:
        text = _text(line)
        if not text:
            continue
        if _BULLET.match(text) or not items:
            items.append(text)
        else:
            items[-1] = join_lines([items[-1], text])
    return [repair_spacing(item) for item in items]


def _two_columns(lines: list[list[Word]], width: float) -> tuple[list[list[Word]], list[list[Word]]]:
    mid = width / 2
    left: list[Word] = []
    right: list[Word] = []
    for line in lines:
        for word in line:
            (left if word.x1 <= mid + 4 else right).append(word)
    return _lines(left), _lines(right)


def _overview(lines: list[list[Word]], width: float) -> Optional[list[str]]:
    """'사례 개요' 표를 칸 단위로 읽는다. 라벨을 못 찾으면 None (호출자가 일반 방식으로 처리)."""
    words = [w for line in lines for w in line]
    labels = {w.text: w for w in words if w.text in _OVERVIEW_LABELS and w.x0 < 110}
    number = labels.get("심의번호")
    accident = labels.get("사고내용") or labels.get("사고")
    if number is None or accident is None:
        return None
    # 라벨 칸의 오른쪽 끝: 라벨 글자(심의번호·사고내용·참고·인정기준)만으로 잡는다 — 내용 칸 첫 글자(•)를 섞으면 첫 낱말이 잘린다
    label_right = max(w.x1 for w in labels.values()) + 3
    content = [w for w in words if w.x0 > label_right]

    row1 = [w for w in content if abs(w.yc - number.yc) <= 9]
    row1_bottom = max((w.y1 for w in row1), default=number.y1)
    row2 = [w for w in content if row1_bottom + 1 < w.yc <= accident.yc + 26 and w not in row1]
    row2_bottom = max((w.y1 for w in row2), default=accident.y1 + 12)
    text_column = width * 0.45
    row3 = [w for w in content if w.yc > row2_bottom + 1 and w.x0 >= text_column]

    out = ["사례 개요"]
    out.append(repair_spacing("심의번호 " + _text(sorted(row1, key=lambda w: w.x0))))
    out.append("사고내용")
    if row2:
        accident_text = join_lines([_text(line) for line in _lines(row2)])
        accident_text = repair_spacing(accident_text)
        out.append(accident_text if _BULLET.match(accident_text) else "• " + accident_text)
    # 도표 번호 배지는 상단 '참고기준' 상자에서 따로 읽으므로(rag.pdf_index._extract_chart_number) 여기서는 본문에 섞지 않는다
    out.append("참고 인정기준")
    paragraph: list[str] = []
    for line in _lines(row3):
        text = _text(line)
        if text.startswith("기본비율") or text.startswith("기본 비율"):
            if paragraph:
                out.append(repair_spacing(join_lines(paragraph)))
                paragraph = []
            out.append(repair_spacing(text))
        else:
            paragraph.append(text)
    if paragraph:
        out.append(repair_spacing(join_lines(paragraph)))
    return out


def build_page_text(width: float, height: float, words: list[Word], *, carry: Optional[str] = None) -> tuple[str, Optional[str]]:
    """한 쪽의 글자 좌표 → 줄 단위 텍스트. carry 는 앞 쪽에서 이어지는 섹션 제목(2단 섹션이 쪽을 넘길 때)."""
    body = [
        w for w in words
        if w.y0 >= _HEADER_Y and w.y1 <= height - _FOOTER_MARGIN and w.x0 <= width - _TAB_MARGIN and w.text != "목차보기"
    ]
    lines = _lines(body)
    # 섹션 경계
    boundaries: list[tuple[int, Optional[str]]] = [(0, carry)]
    for index, line in enumerate(lines):
        title = _section_title(line)
        if title:
            boundaries.append((index, title))
    boundaries.append((len(lines), None))

    out: list[str] = []
    current: Optional[str] = carry
    for (start, title), (end, _) in zip(boundaries, boundaries[1:]):
        section_lines = lines[start:end]
        if not section_lines:
            current = title
            continue
        current = title
        if title is not None and section_lines and _section_title(section_lines[0]) == title:
            header_line = section_lines[0]
            rest_words = header_line[2:] if len(header_line) > 2 else []
            section_lines = ([rest_words] if rest_words else []) + section_lines[1:]
            if title != "사례 개요":
                out.append(title)
        if title == "사례 개요":
            table = _overview(section_lines, width)
            if table is not None:
                out.extend(table)
                continue
            out.append("사례 개요")
            out.extend(repair_spacing(_text(line)) for line in section_lines)
        elif title in _TWO_COLUMN:
            left, right = _two_columns(section_lines, width)
            if title == "주장 내용":
                left = [line for line in left if _text(line) not in {"청구인", "피청구인"}]
                right = [line for line in right if _text(line) not in {"청구인", "피청구인"}]
                out.extend(f"[청구인] {item}" for item in _items(left))
                out.extend(f"[피청구인] {item}" for item in _items(right))
            else:
                out.extend(_items(left) + _items(right))
        elif title is None and current is None:
            # 사례 첫 쪽 상단: 제목 + 오른쪽 '참고기준' 상자(도표 번호 배지 "213" + "(나)" 가 두 줄로 나온다 → 한 줄로 붙인다)
            box = [w for line in section_lines for w in line if w.x0 >= width * 0.76 and w.y0 >= _HEADER_Y + 30]
            badge = "".join(w.text for w in sorted(box, key=lambda w: (w.y0, w.x0)) if w.text != "참고기준")
            if any(w.text == "참고기준" for w in box):
                out.append(("참고기준 " + badge).strip())
            box_ids = {id(w) for w in box}
            for line in section_lines:
                rest = [w for w in line if id(w) not in box_ids]
                if rest:
                    out.append(_text(rest))
        else:
            out.extend(_items(section_lines))
    next_carry = current if current in _TWO_COLUMN else None
    return "\n".join(line for line in out if line.strip()), next_carry


def extract_case_pages(pdf_path: str | Path, *, words_of: Callable[[Path, int], tuple[float, float, list[Word]]] = page_words) -> list[str]:
    """심의사례 PDF 전체를 쪽 단위 텍스트 목록으로 (rag.pdf_index.extract_pdf_pages 와 같은 모양)."""
    pdf = Path(pdf_path)
    total = page_count(pdf)
    pages: list[str] = []
    carry: Optional[str] = None
    for number in range(1, total + 1):
        try:
            width, height, words = words_of(pdf, number)
        except Exception:  # noqa: BLE001 — 한 쪽이 깨져도 나머지는 살린다
            pages.append("")
            carry = None
            continue
        if any(w.text == "심의번호" for w in words):
            carry = None  # 새 사례 시작
        text, carry = build_page_text(width, height, words, carry=carry)
        pages.append(text)
    return pages
