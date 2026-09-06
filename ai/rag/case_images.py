"""심의사례·인정기준 도표를 원본 PDF에서 잘라 PNG 로 만든다 (판정 카드 → 심의사례 팝업 그림).

세 PDF 는 구조가 다르므로 잘라내는 규칙도 다르다.

| source_type                 | unit_type        | 위(top) 기준          | 아래(bottom) 기준 | 잘리는 것 |
|-----------------------------|------------------|-----------------------|-------------------|-----------|
| deliberation_case (심의사례) | case             | "사례 개요" 제목 띠    | "주장 내용" 제목  | 심의번호·결정비율·사고내용·참고 인정기준 그림 표 |
| fault_standard (인정기준)    | standard_chart   | 도표 번호 배지(예 차1-1) | "사고 상황" 제목  | 도표 제목 띠·그림·기본 과실비율 표·(※ 조정 안내) |
| roundabout_special_standard | roundabout_chart | 도표 번호 배지(예 회전-4) | "사고 상황" 제목  | 위와 같음 |

글자 좌표는 poppler `pdftotext -bbox-layout`, 렌더링은 `pdftoppm` 으로 한다 (RAG 인덱스 빌드가 이미 poppler 를 쓴다).
파이썬 추가 의존성은 없다. 결과는 `<index_dir>/images/<id>.png` 와 `manifest.json` (id → 파일명·페이지) 이며,
백엔드는 manifest 만 읽어 `GET /api/v1/precedents/{id}/image` 로 내려준다.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from rag.pdf_index import DEFAULT_INDEX_DIR

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF_DIR = _ROOT / "data" / "text"
IMAGE_DIR_NAME = "images"
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
DEFAULT_DPI = 120
_IMAGE_UNIT_TYPES = {"case", "standard_chart", "roundabout_chart"}


from rag.pdf_words import Word, page_words, parse_bbox_xml  # noqa: E402  (그림·표 재구성이 같은 좌표 읽기를 쓴다)


@dataclass
class ImageUnit:
    key: str  # RetrievedCase.case_id 와 같은 값: 심의번호 또는 도표 번호
    unit_type: str
    source_type: str
    source_file: str
    source_path: str
    page_start: int
    page_end: int
    chart_number: Optional[str] = None


@dataclass
class BuildSummary:
    total: int = 0
    created: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"total": self.total, "created": self.created, "skipped": self.skipped, "failed": list(self.failed)}


def sanitize_key(key: str) -> str:
    """파일명으로 안전한 형태. 한글·숫자·영문·-_ 만 남긴다 (예: '회전-4' → '회전-4', '보27-1' → '보27-1')."""
    return re.sub(r"[^0-9A-Za-z가-힣_\-]+", "_", key.strip()) or "unknown"


def poppler_available() -> bool:
    return bool(shutil.which("pdftotext") and shutil.which("pdftoppm"))


# ----------------------------------------------------------------------------- 인덱스 → 단위 목록


def load_units(index_dir: Path = DEFAULT_INDEX_DIR) -> list[ImageUnit]:
    """parents.jsonl 에서 그림을 만들 단위(심의사례·도표)만 고른다. reference_page(서문·해설)는 제외."""
    path = Path(index_dir) / "parents.jsonl"
    units: list[ImageUnit] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        unit_type = row.get("unit_type") or ""
        if unit_type not in _IMAGE_UNIT_TYPES:
            continue
        unit_key = str(row.get("unit_key") or "")
        key = str(row.get("case_number") or row.get("chart_number") or unit_key.split(":", 1)[-1]).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        units.append(
            ImageUnit(
                key=key,
                unit_type=unit_type,
                source_type=str(row.get("source_type") or ""),
                source_file=str(row.get("source_file") or ""),
                source_path=str(row.get("source_path") or ""),
                page_start=int(row.get("page_start") or 1),
                page_end=int(row.get("page_end") or row.get("page_start") or 1),
                chart_number=(str(row["chart_number"]) if row.get("chart_number") else None),
            )
        )
    return units


def resolve_pdf(unit: ImageUnit, pdf_dir: Path = DEFAULT_PDF_DIR) -> Optional[Path]:
    """인덱스를 만든 PC 의 절대 경로(source_path)보다 현재 PC 의 pdf_dir/<파일명> 을 먼저 본다."""
    candidates = [Path(pdf_dir) / unit.source_file, Path(unit.source_path)]
    for candidate in candidates:
        if unit.source_file and candidate.is_file():
            return candidate
    return None


# ----------------------------------------------------------------------------- 좌표 찾기


def _label(words: list[Word], first: str, second: Optional[str] = None, *, after_y: float = -1.0) -> Optional[Word]:
    """'사례' 바로 뒤 같은 줄에 '개요' 가 오는 자리(제목 띠)를 찾는다. second 가 없으면 단어 하나만 본다."""
    for index, word in enumerate(words):
        if word.text != first or word.y0 <= after_y:
            continue
        if second is None:
            return word
        for follower in words[index + 1 : index + 3]:
            if follower.text == second and abs(follower.y0 - word.y0) < 3:
                return word
    return None


def _find_badge(words: list[Word], chart: str) -> Optional[Word]:
    """도표 번호 배지를 찾는다. PDF 글자 추출이 '회전-4' 를 '회전'+'-4', '차1-1' 을 '차'+'1-1' 로 쪼개므로
    같은 줄(y 차이 8pt 이내)의 이웃 토큰을 이어 붙여 비교한다. 헤딩의 '[차1]' 은 대괄호 때문에 잡히지 않는다."""
    target = chart.replace(" ", "")
    ordered = sorted(words, key=lambda w: (w.y0, w.x0))
    # 1) 쪼개진 배지: 시작 토큰 오른쪽(같은 줄)의 토큰을 x 순서로 이어 붙여 본다 (추출 순서는 믿지 않는다)
    for start in ordered:
        if not start.text or not target.startswith(start.text) or start.text == target:
            continue
        acc, x0, y0, x1, y1 = start.text, start.x0, start.y0, start.x1, start.y1
        neighbours = sorted((w for w in words if w is not start and abs(w.y0 - start.y0) <= 8 and w.x0 >= start.x1 - 3), key=lambda w: w.x0)
        for word in neighbours:
            acc += word.text
            x1, y0, y1 = max(x1, word.x1), min(y0, word.y0), max(y1, word.y1)
            if acc == target:
                return Word(text=target, x0=x0, y0=y0, x1=x1, y1=y1)
            if not target.startswith(acc):
                break
    # 2) 한 토큰으로 나온 배지
    exact = [w for w in ordered if w.text == target]
    if exact:
        return exact[0]
    # 3) 헤딩 "[차1]" 처럼 대괄호로 싸인 것 (배지 글자를 못 뽑는 페이지의 마지막 수단)
    heading = [w for w in ordered if w.text.strip("[]") == target]
    return heading[0] if heading else None


def _note_below(words: list[Word], after_y: float) -> Optional[Word]:
    """표 바로 아래 '※ 사고발생, 손해확대와의 인과관계…' 안내줄. '※舊 201 기준' 처럼 두 줄이면 마지막 줄을 돌려준다."""
    notes = sorted((w for w in words if w.text.startswith("※") and w.y0 > after_y), key=lambda w: w.y0)
    if not notes:
        return None
    last = notes[0]
    for note in notes[1:]:
        if note.y0 - last.y1 > 14:  # 붙어 있는 줄만 (멀리 떨어진 ※ 는 다른 절)
            break
        last = note
    return last


def crop_box(unit: ImageUnit, width: float, height: float, words: list[Word]) -> Optional[tuple[float, float, float, float]]:
    """(x0, y0, x1, y1) in PDF points. 못 찾으면 None (다른 페이지를 시도한다)."""
    if unit.unit_type == "case":
        top = _label(words, "사례", "개요")
        if top is None:
            return None
        bottom = _label(words, "주장", "내용", after_y=top.y1)
        y0 = max(0.0, top.y0 - 6)
        y1 = (bottom.y0 - 10) if bottom else min(height - 30, y0 + 330)
    else:
        chart = (unit.chart_number or unit.key).strip()
        badge = _find_badge(words, chart)
        if badge is None:
            return None
        # 배지는 도표 왼쪽 칸의 세로 가운데에 있다. 제목 띠(두 줄이면 더 높다)는 배지보다 14~22pt 위에서 시작한다.
        y0 = max(0.0, badge.y0 - 28)
        # 그 위에 절 제목("3) 도로의 사고 [보27~보28]")이 바짝 붙어 있으면 잘려 들어오므로 그 아래로 내린다
        above = [w.y1 for w in words if y0 < w.y1 <= badge.y0 - 18]
        if above:
            y0 = max(above) + 2
        header = _label(words, "사고", "상황", after_y=badge.y1)
        note = _note_below(words, badge.y1)
        if header is not None and (note is None or header.y0 > note.y0):
            y1 = header.y0 - 10
        elif note is not None:
            y1 = note.y1 + 6
        else:
            y1 = min(height - 30, y0 + 330)
    if y1 - y0 < 60:  # 제목만 잡힌 경우: 표가 없다고 보고 실패 처리
        return None
    return (28.0, y0, width - 28.0, y1)


def render_crop(pdf: Path, page: int, box: tuple[float, float, float, float], out_png: Path, *, dpi: int = DEFAULT_DPI) -> None:
    scale = dpi / 72.0
    x0, y0, x1, y1 = box
    args = [
        "pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile",
        "-x", str(int(x0 * scale)), "-y", str(int(y0 * scale)), "-W", str(int((x1 - x0) * scale)), "-H", str(int((y1 - y0) * scale)),
        str(pdf), str(out_png.with_suffix("")),
    ]
    subprocess.run(args, check=True, capture_output=True)


# ----------------------------------------------------------------------------- 빌드


def images_dir(index_dir: Path = DEFAULT_INDEX_DIR) -> Path:
    return Path(index_dir) / IMAGE_DIR_NAME


def build_case_images(
    *,
    index_dir: Path = DEFAULT_INDEX_DIR,
    pdf_dir: Path = DEFAULT_PDF_DIR,
    out_dir: Optional[Path] = None,
    dpi: int = DEFAULT_DPI,
    force: bool = False,
    only: Optional[Iterable[str]] = None,
    log: Callable[[str], None] = print,
) -> BuildSummary:
    if not poppler_available():
        raise RuntimeError("poppler(pdftotext, pdftoppm) 가 필요하다. Ubuntu: sudo apt install poppler-utils")
    out_dir = Path(out_dir) if out_dir else images_dir(index_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    manifest: dict[str, dict] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")).get("images", {})
        except (OSError, ValueError):
            manifest = {}

    wanted = set(only) if only else None
    units = [unit for unit in load_units(index_dir) if wanted is None or unit.key in wanted]
    summary = BuildSummary(total=len(units))
    for unit in units:
        out_png = out_dir / f"{sanitize_key(unit.key)}.png"
        if out_png.is_file() and not force and unit.key in manifest:
            summary.skipped += 1
            continue
        pdf = resolve_pdf(unit, pdf_dir)
        if pdf is None:
            summary.failed.append(f"{unit.key}: PDF 없음 ({unit.source_file})")
            continue
        done = False
        for page in range(unit.page_start, unit.page_end + 1):
            try:
                width, height, words = page_words(pdf, page)
                box = crop_box(unit, width, height, words)
                if box is None:
                    continue
                render_crop(pdf, page, box, out_png, dpi=dpi)
            except (subprocess.CalledProcessError, ValueError, OSError) as exc:
                summary.failed.append(f"{unit.key}: {page}쪽 처리 실패 ({str(exc)[:80]})")
                break
            manifest[unit.key] = {
                "file": out_png.name, "unit_type": unit.unit_type, "source_type": unit.source_type,
                "source_file": unit.source_file, "page": page,
            }
            summary.created += 1
            done = True
            log(f"  {unit.key}: {unit.source_file} {page}쪽 → {out_png.name}")
            break
        if not done and not any(item.startswith(f"{unit.key}:") for item in summary.failed):
            summary.failed.append(f"{unit.key}: {unit.page_start}~{unit.page_end}쪽에서 표 제목을 못 찾음")

    manifest_path.write_text(
        json.dumps({"version": MANIFEST_VERSION, "dpi": dpi, "images": manifest}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return summary


def image_path(case_id: str, index_dir: Path = DEFAULT_INDEX_DIR) -> Optional[Path]:
    """심의번호/도표 번호 → 만들어 둔 PNG 경로. 없으면 None."""
    folder = images_dir(index_dir)
    manifest_path = folder / MANIFEST_NAME
    name = None
    if manifest_path.is_file():
        try:
            name = (json.loads(manifest_path.read_text(encoding="utf-8")).get("images", {}).get(case_id) or {}).get("file")
        except (OSError, ValueError):
            name = None
    candidate = folder / (name or f"{sanitize_key(case_id)}.png")
    return candidate if candidate.is_file() else None
