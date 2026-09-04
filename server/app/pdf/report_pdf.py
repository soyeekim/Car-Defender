import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from app.clock import kst_yyyymmdd

FONT_DIR = Path(__file__).parent / "fonts"
FONT = "NanumGothic"
# 문서마다 다시 만들 이유가 없는 "경로 문자열"만 캐시한다. fpdf2가 읽어 들인 폰트 객체는
# 문서에 묶여 있어(서브셋·글리프 인덱스) 문서 간에 돌려 쓰면 안 되므로 캐시하지 않는다.
_FONT_REGULAR = str(FONT_DIR / "NanumGothic-Regular.ttf")
_FONT_BOLD = str(FONT_DIR / "NanumGothic-Bold.ttf")
_BAD = re.compile(r'[\\/:*?"<>|]')


def safe_filename(title: str) -> str:
    return _BAD.sub("_", title).strip() or "사건"


def report_pdf_filename(case_title: str, created_at: datetime) -> str:
    return f"사건경위서_{safe_filename(case_title)}_{kst_yyyymmdd(created_at)}.pdf"


class _Doc(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.add_font(FONT, "", _FONT_REGULAR)
        self.add_font(FONT, "B", _FONT_BOLD)
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(FONT, "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"{self.page_no()} / {{nb}}", align="C")


def render_report_pdf(*, case_title: str, date_label: str, version_label: str, sections: list[dict], disclaimer: str) -> tuple[bytes, int]:
    pdf = _Doc()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_text_color(20, 20, 20)
    pdf.set_font(FONT, "B", 20)
    pdf.cell(0, 12, "사건경위서", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT, "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 7, f"{case_title} · {version_label} · {date_label}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for s in sorted(sections, key=lambda x: x.get("index", 0)):
        pdf.set_text_color(20, 20, 20)
        pdf.set_font(FONT, "B", 12)
        pdf.multi_cell(0, 8, f"{s.get('index')}. {s.get('title', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(FONT, "", 10.5)
        pdf.multi_cell(0, 6.5, s.get("body", ""), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    pdf.ln(4)
    pdf.set_font(FONT, "", 8.5)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, disclaimer, new_x="LMARGIN", new_y="NEXT")
    data = bytes(pdf.output())
    return data, pdf.pages_count
