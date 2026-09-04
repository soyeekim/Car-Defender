from datetime import UTC, datetime

from app.pdf.report_pdf import render_report_pdf, report_pdf_filename, safe_filename

SECTIONS = [
    {"index": 1, "title": "사고 일시 및 장소", "body": "2026년 8월 22일 14시경, 서울시 강남구 논현사거리 교차로에서 발생한 사고입니다."},
    {"index": 2, "title": "사고 경위", "body": "본인은 2차로에서 정상 신호에 따라 직진 중이었습니다. " * 20},
    {"index": 3, "title": "블랙박스 영상 분석 결과", "body": "영상에서 본인 차량의 2차로 직진이 확인됩니다."},
    {"index": 4, "title": "주장 요지", "body": "상대 차량의 일방과실 적용을 요청드립니다."},
]


def test_render_pdf_returns_bytes_and_pages():
    data, pages = render_report_pdf(case_title="교차로 직진 충돌 · 08-22", date_label="08-25", version_label="첫 번째 버전", sections=SECTIONS, disclaimer="본 결과는 참고용입니다.")
    assert data[:4] == b"%PDF" and pages >= 1


def test_long_body_spans_pages():
    long_sections = [{**s, "body": s["body"] * 30} for s in SECTIONS]
    _, pages = render_report_pdf(case_title="t", date_label="08-25", version_label="v", sections=long_sections, disclaimer="d")
    assert pages >= 2


def test_filename():
    assert safe_filename('a/b:c*d?e"f<g>h|i\\j') == "a_b_c_d_e_f_g_h_i_j"
    dt = datetime(2026, 8, 22, 9, 40, tzinfo=UTC)
    assert report_pdf_filename("교차로 직진 충돌 · 08-22", dt) == "사건경위서_교차로 직진 충돌 · 08-22_20260822.pdf"
