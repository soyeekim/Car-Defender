"""과실비율 인정기준·회전교차로 비정형기준 PDF 의 도표 페이지를 표 구조를 알고 다시 읽는다.

`pdftotext` 의 일반 출력은 도표 상자의 세로 라벨("과실비율 조정예시"의 과/실/비/율/조/정/예/시)과 오른쪽 여백의 장 표시,
머리글(러닝 타이틀·쪽수)을 본문 사이에 끼워 넣는다. 그래서 도표 180건 중 165건의 제목이 머리글이 되고, 수정요소 행에
"실 A 중대한 과실 +20" 처럼 세로 글자가 붙어 있었다. 여기서는 글자 좌표(pdftotext -bbox-layout)로 칸을 나눠 읽는다.

도표 상자 구조 (한 쪽에 두 상자가 있을 수도 있다: 보5·보6)
    ┌ [차3-2] │ 적색직진 대 적색좌회전            ← 배지 + 제목 띠 (배지는 왼쪽 칸의 세로 가운데)
    │         │ (A) 적색 직진 / (B) 적색 좌회전   ← A·B 역할 (회전교차로: 레드(A)/블루(B), 보행자: (보)/(차))
    │ [그림]  │ 기본 과실비율      A50  B50       ← 기본비율. 변형이 있으면 "(가) A40 B60 / (나) A30 B70" 여러 줄
    │         │ A 교차로 정체중 진입   +10        ← 수정요소 행: 쪽(A/B) · 항목 · ±값  (보행자 도표는 쪽 없이 보행자 기준)
    │         │ B 좌회전 완료직후          -10
    │ ※ 사고발생, 손해확대와의 인과관계를 감안하여 … (안내, 버림)
    │ ※舊 216, 330(가), 331(가) 기준              ← 옛 도표 번호 (심의사례의 참고기준 번호와 같은 체계)
    │ 사고 상황 / 기본 과실비율 해설 / 수정요소 해설 / 관련 법규 …  ← 한 단 본문 (다음 쪽까지 이어짐)

결과는 줄 단위 텍스트다 (rag/case_documents.parse_standard_parent 가 읽는다):
    차12-1 우측도로 직진 대 좌측도로 직진(동일폭)
    A: 우측도로에서 직진 (가)동시 (나)선진입 (다)후진입
    B: 좌측도로에서 직진 (가)동시 (나)후진입 (다)선진입
    기본 과실비율 (가) A 40 : B 60
    기본 과실비율 (나) A 30 : B 70
    수정요소 표
    - A 현저한 과실 +10
    舊 기준 205, 306, 307
    사고 상황
    ⊙ 신호기에 의해 … 사고이다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from common.korean_text import join_lines, repair_spacing
from rag.pdf_layout import _lines, _text
from rag.pdf_words import Word, page_count, page_words

_HEADER_Y = 40.0
_FOOTER_MARGIN = 22.0
_TAB_MARGIN = 30.0
_BADGE = re.compile(r"^(?:(보|거|차)(\d{1,3}(?:-\d{1,2})?)|(회전)-?(\d{1,2}))$")
_BADGE_MIN_HEIGHT = 14.0  # 배지 글자(17~18pt)는 본문(8~10pt)·목차(12.6pt)보다 크다
_ROLE = re.compile(
    r"^(?:\((A|B|보|차|자|거|이|보행자|이륜차|자전거|자동차|차량)\)|(?:레드|블루)\((A|B)\)|(?:자동차|차량)\s*(A|B))\s*:?\s*"
)
# 한 줄에 두 역할이 붙어 나오는 경우("(보행자) 횡단보도 횡단 (이륜차) 횡단보도 횡단") 역할 표시 앞에서 자른다
_ROLE_SPLIT = re.compile(r"(?<!레드)(?<!블루)(?=\((?:A|B|보|차|자|거|이|보행자|이륜차|자전거|자동차|차량)\)\s)|(?=(?:레드|블루)\((?:A|B)\))")
_SIDE = re.compile(r"^(?:(A|B)|(?:레드|블루)\((A|B)\))$")
_FUSED_SIDE = re.compile(r"^(A|B)([가-힣].*)$")
_VALUE = re.compile(r"^([+\-−±])\s?(\d{1,3})$")
_NON_NUMERIC_VALUE = {"비적용", "적용", "-", "–"}
_RATIO_TOKEN = re.compile(r"^(?:(A|B|레드|블루)\s*)?(\d{1,3})$")
_VARIANT = re.compile(r"^\(([가-힣])\)$")
_CIRCLED = re.compile(r"^[①-⑳]$")
_LEGACY_NUMBER = re.compile(r"\d{3}(?:\([가-힣]\))?")
SECTION_TITLES = (
    "사고 상황", "사고상황", "기본 과실비율 해설", "기본 과실비율", "수정요소", "활용시 참고 사항", "활용시 참고사항",
    "관련 법규", "참고 판례", "참고 사항", "참고사항", "적용 예시", "해설",
)
_ITEM_START = re.compile(r"^(?:[⊙●•▪◦※]|[①-⑳]|\(\d{1,2}\)|\d{1,2}\.|\d{1,2}\)|[가-하]\.|-\s)")
_GAP_NEW_ITEM = 7.0
_ROW_TOLERANCE = 3.0


def _body_words(width: float, height: float, words: list[Word]) -> list[Word]:
    return [
        w for w in words
        if w.y0 >= _HEADER_Y and w.y1 <= height - _FOOTER_MARGIN and w.x0 <= width - _TAB_MARGIN and w.text.strip()
    ]


def _rows(words: list[Word], tolerance: float) -> list[list[Word]]:
    ordered = sorted(words, key=lambda w: (w.yc, w.x0))
    lines: list[list[Word]] = []
    for word in ordered:
        if lines and abs(lines[-1][0].yc - word.yc) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def find_chart_badges(words: list[Word]) -> list[tuple[str, Word]]:
    """왼쪽 칸의 큰 글자 배지(차3-2 / 회전-3 / 보1)를 위에서부터 모두 찾는다.
    추출이 '차'+'3-2', '회전'+'-3' 으로 쪼개므로 같은 줄 이웃을 이어 본다."""
    left = sorted((w for w in words if w.x1 <= 105 and w.y0 >= _HEADER_Y and (w.y1 - w.y0) >= _BADGE_MIN_HEIGHT), key=lambda w: (w.y0, w.x0))
    found: list[tuple[str, Word]] = []
    used: set[int] = set()
    for start in left:
        if id(start) in used:
            continue
        acc, x0, y0, x1, y1 = start.text, start.x0, start.y0, start.x1, start.y1
        parts = [start]
        match = _BADGE.match(acc)
        if not match:
            neighbours = sorted((w for w in left if w is not start and abs(w.y0 - start.y0) <= 8 and w.x0 >= start.x1 - 3), key=lambda w: w.x0)
            for word in neighbours:
                acc += word.text
                parts.append(word)
                x1, y0, y1 = max(x1, word.x1), min(y0, word.y0), max(y1, word.y1)
                match = _BADGE.match(acc)
                if match or len(acc) > 8:
                    break
        if match:
            used.update(id(w) for w in parts)
            found.append((_badge_id(match), Word(text=acc, x0=x0, y0=y0, x1=x1, y1=y1)))
    return found


def _badge_id(match: re.Match) -> str:
    if match.group(3):
        return f"회전-{match.group(4)}"
    return f"{match.group(1)}{match.group(2)}"


def _role_side(marker: str) -> str:
    if marker in {"A", "B"}:
        return marker
    # 보행자·자전거·이륜차 도표: 약자(보/자/거/이)가 A, 자동차(차)가 B
    return "B" if marker in {"차", "자동차", "차량"} else "A"


def _vertical_label_ids(words: list[Word]) -> set[int]:
    """'과실비율 조정예시' 세로 라벨: 같은 x 에 한 글자씩 4개 이상 쌓인 낱자."""
    columns: dict[int, list[Word]] = defaultdict(list)
    for w in words:
        if len(w.text) == 1 and "가" <= w.text <= "힣" and (w.y1 - w.y0) <= 13:
            columns[int(round(w.x0 / 3))].append(w)
    ids: set[int] = set()
    for column in columns.values():
        if len(column) >= 4 and (max(w.y1 for w in column) - min(w.y0 for w in column)) >= 40:
            ids.update(id(w) for w in column)
    return ids


def _sections(lines: list[list[Word]]) -> list[str]:
    """한 단 본문: 절 제목은 한 줄, 항목(⊙ ① 1. …)은 이어지는 줄을 붙여 한 줄로."""
    out: list[str] = []
    item: list[str] = []
    prev: Optional[list[Word]] = None

    def flush():
        nonlocal item
        if item:
            out.append(repair_spacing(join_lines(item)))
            item = []

    for line in lines:
        text = _text(line)
        if not text:
            continue
        compact = text.replace(" ", "")
        title = next((t for t in SECTION_TITLES if compact.startswith(t.replace(" ", ""))), None)
        if title and line[0].x0 < 80 and len(text) <= 42:
            flush()
            out.append(text)
            prev = line
            continue
        gap = (line[0].y0 - max(w.y1 for w in prev)) if prev else 0.0
        if _ITEM_START.match(text) or gap > _GAP_NEW_ITEM or not item:
            flush()
        item.append(text)
        prev = line
    flush()
    return out


def _find_header(words: list[Word], badge: Word, limit_y: float) -> Optional[Word]:
    """표 머리 '기본 과실비율' 의 '과실비율' 낱말 (배지 오른쪽 칸, 배지 아래)."""
    candidates = []
    for w in words:
        if w.text == "과실비율" and w.x0 > 180 and badge.y0 - 5 < w.y0 < limit_y:
            if any(v.text == "기본" and abs(v.yc - w.yc) <= 4 and 0 <= w.x0 - v.x1 <= 8 for v in words):
                candidates.append(w)
    return min(candidates, key=lambda w: w.y0) if candidates else None


def _chart_block(badge_id: str, badge: Word, words: list[Word], header: Word, limit_y: float) -> tuple[list[str], float, float]:
    """도표 상자 하나를 읽는다. (줄 목록, 상자 위 y, 상자 아래 y)."""
    vertical = _vertical_label_ids(words)
    vertical_right = max((w.x1 for w in words if id(w) in vertical and w.y0 > badge.y0), default=None)
    words = [w for w in words if id(w) not in vertical and not _CIRCLED.match(w.text) and w.y0 < limit_y]

    # 기본비율: 표 머리 오른쪽, 머리와 같은 높이(변형이 있으면 위아래 줄까지)
    basic_words = [w for w in words if w.x0 > header.x1 + 2 and abs(w.yc - header.yc) <= 24]
    variants: list[tuple[str, tuple[int, int]]] = []
    for line in _rows(basic_words, 4.0):
        label = ""
        numbers: list[int] = []
        for w in line:
            v = _VARIANT.match(w.text)
            if v:
                label = f"({v.group(1)})"
                continue
            m = _RATIO_TOKEN.match(w.text)
            if m:
                numbers.append(int(m.group(2)))
        if len(numbers) >= 2:
            variants.append((label, (numbers[0], numbers[1])))
        elif len(numbers) == 1:
            variants.append((label, (numbers[0], 100 - numbers[0])))
    basic_lines = [w for w in basic_words if _RATIO_TOKEN.match(w.text) or _VARIANT.match(w.text)] + [header]
    basic_top = min(w.y0 for w in basic_lines)
    basic_bottom = max(w.y1 for w in basic_lines)
    # 표의 왼쪽 경계: 세로 라벨("과실비율 조정예시") 바로 오른쪽. 세로 라벨이 없으면 '기본 과실비율' 머리에서 짐작한다
    if vertical_right is not None:
        table_left = vertical_right + 1
    else:
        table_left = min(header.x0, *(v.x0 for v in words if v.text == "기본" and abs(v.yc - header.yc) <= 4)) - 12

    # 제목·역할 띠: 배지는 왼쪽 칸(제목 띠 + 역할 줄)의 세로 가운데 → 띠 위 = 2*배지중심 - 역할 아래
    roles_bottom = basic_top - 4
    title_top = 2 * badge.yc - roles_bottom
    band_words = [
        w for w in words
        if w.x0 > badge.x1 + 3 and w.y0 >= title_top - 8 and w.yc < basic_bottom
        and (w.y1 <= basic_top - 2 or w.x1 < header.x0 - 2)
        and not (abs(w.yc - header.yc) <= 5 and w.x0 >= table_left - 40)  # 표 머리 '보행자 기본 과실비율' 의 앞 낱말
    ]
    title_parts: list[str] = []
    roles: list[tuple[str, str]] = []
    top = title_top
    for line in _rows(band_words, 3.5):
        if line[0].x0 <= badge.x1 + 3:
            continue
        for text in (piece.strip() for piece in _ROLE_SPLIT.split(_text(line)) if piece.strip()):
            marker = _ROLE.match(text)
            if marker:
                side = _role_side(marker.group(1) or marker.group(2) or marker.group(3))
                roles.append((side, text[marker.end():].strip()))
            elif roles:
                roles[-1] = (roles[-1][0], join_lines([roles[-1][1], text]))
            else:
                title_parts.append(text)
                top = min(top, line[0].y0 - 4)

    # 안내줄(※) 과 옛 기준 번호
    notes = sorted((w for w in words if w.text.startswith("※") and w.y0 > basic_bottom and w.x0 < 120), key=lambda w: w.y0)
    note_y = notes[0].y0 - 2 if notes else min(limit_y, basic_bottom + 230)
    legacy: list[str] = []
    bottom = note_y
    for note in notes:
        if note.y0 - notes[0].y0 > 30:
            break
        line = [w for w in words if abs(w.yc - note.yc) <= 4 and w.x0 >= note.x0]
        text = _text(sorted(line, key=lambda w: w.x0))
        if "舊" in text:
            legacy = _LEGACY_NUMBER.findall(text)
        bottom = max(bottom, note.y1 + 2)

    # 수정요소 행
    region = [w for w in words if basic_bottom + 3 < w.yc < note_y and w.x0 >= table_left]
    lines = _rows(region, _ROW_TOLERANCE)
    has_sides = any(line and _SIDE.match(line[0].text) for line in lines)
    rows: list[dict] = []
    for line in lines:
        tokens = [w.text for w in line]
        side = None
        value: Optional[str] = None
        if tokens and _SIDE.match(tokens[0]):
            m = _SIDE.match(tokens[0])
            side = m.group(1) or m.group(2)
            tokens = tokens[1:]
        elif tokens and has_sides and _FUSED_SIDE.match(tokens[0]):
            m = _FUSED_SIDE.match(tokens[0])  # "B진로변경" 처럼 쪽 글자가 항목에 붙어 나온 것
            side, tokens[0] = m.group(1), m.group(2)
        if tokens and (_VALUE.match(tokens[-1]) or tokens[-1] in _NON_NUMERIC_VALUE):
            value = tokens[-1]
            tokens = tokens[:-1]
        label = repair_spacing(" ".join(tokens)).replace(" ·", "·").replace("· ", "·")
        if has_sides:
            if side is None:
                if rows and rows[-1]["value"] is None:
                    rows[-1]["label"] = join_lines([rows[-1]["label"], label]) if label else rows[-1]["label"]
                    rows[-1]["value"] = value
                elif rows and value is None:
                    rows[-1]["label"] = join_lines([rows[-1]["label"], label]) if label else rows[-1]["label"]
                continue
            rows.append({"side": side, "label": label, "value": value})
        else:
            if not label and value is not None and rows and rows[-1]["value"] is None:
                rows[-1]["value"] = value
                continue
            if label:
                rows.append({"side": "A", "label": label, "value": value})

    out: list[str] = [f"{badge_id} {' '.join(title_parts)}".strip()]
    for side, text in roles:
        out.append(f"{side}: {text}")
    for label, (a, b) in variants:
        out.append(f"기본 과실비율{(' ' + label) if label else ''} A {a} : B {b}")
    numeric = [row for row in rows if row["value"] is not None and _VALUE.match(row["value"]) and row["label"]]
    if numeric:
        out.append("수정요소 표")
        for row in numeric:
            value = row["value"].replace("−", "-").replace("±", "+").replace(" ", "")
            label = row["label"].replace(" ·", "·").replace("· ", "·")
            out.append(f"- {row['side']} {label} {value}")
    if legacy:
        out.append("舊 기준 " + ", ".join(legacy))
    return out, top, bottom


def build_chart_page_text(width: float, height: float, words: list[Word]) -> str:
    """한 쪽의 글자 좌표 → 줄 단위 텍스트. 도표 상자가 있으면 표 구조로, 나머지는 한 단 본문으로 읽는다."""
    body = _body_words(width, height, words)
    badges = find_chart_badges(body)
    boxes: list[tuple[str, Word, Word, float]] = []
    for index, (badge_id, badge) in enumerate(badges):
        limit_y = badges[index + 1][1].y0 - 40 if index + 1 < len(badges) else height
        header = _find_header(body, badge, min(limit_y, badge.y1 + 160))
        if header is not None:
            boxes.append((badge_id, badge, header, limit_y))
    if not boxes:
        return "\n".join(_sections(_lines(body)))
    out: list[str] = []
    cursor = 0.0
    for badge_id, badge, header, limit_y in boxes:
        block, top, bottom = _chart_block(badge_id, badge, body, header, limit_y)
        above = [w for w in body if cursor <= w.y0 and w.y1 <= top]
        out.extend(_sections(_lines(above)))
        out.extend(block)
        cursor = bottom
    below = [w for w in body if w.y0 >= cursor]
    out.extend(_sections(_lines(below)))
    return "\n".join(line for line in out if line.strip())


def extract_standard_pages(pdf_path: str | Path, *, words_of: Callable[[Path, int], tuple[float, float, list[Word]]] = page_words) -> list[str]:
    """인정기준·회전교차로 PDF 전체를 쪽 단위 텍스트 목록으로 (rag.pdf_index.extract_pdf_pages 와 같은 모양)."""
    pdf = Path(pdf_path)
    pages: list[str] = []
    for number in range(1, page_count(pdf) + 1):
        try:
            width, height, words = words_of(pdf, number)
        except Exception:  # noqa: BLE001 — 한 쪽이 깨져도 나머지는 살린다
            pages.append("")
            continue
        pages.append(build_chart_page_text(width, height, words))
    return pages
