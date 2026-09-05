"""심의사례·도표 그림 잘라내기 — poppler 없이 좌표 계산 로직만 검증한다."""

import json

from rag.case_images import ImageUnit, Word, _find_badge, crop_box, image_path, load_units, parse_bbox_xml, sanitize_key


def _w(text, x0, y0, w=20.0, h=10.0):
    return Word(text=text, x0=x0, y0=y0, x1=x0 + w, y1=y0 + h)


def test_parse_bbox_xml_tolerates_unescaped_characters():
    xml = (
        '<?xml version="1.0"?><html><body><doc><page width="420.0" height="595.0">'
        '<word xMin="10.0" yMin="20.0" xMax="30.0" yMax="30.0">A&B</word>'
        '<word xMin="40.0" yMin="20.0" xMax="60.0" yMax="30.0">&#xC0AC;&#xB840;</word>'
        "</page></doc></body></html>"
    )
    width, height, words = parse_bbox_xml(xml)
    assert (width, height) == (420.0, 595.0)
    assert [w.text for w in words] == ["A&B", "사례"]


def test_badge_is_found_even_when_split_into_tokens():
    words = [_w("회전교차로", 30, 22), _w("-4", 60, 88, w=12), _w("회전", 36, 89, w=22), _w("기본", 200, 128)]
    badge = _find_badge(words, "회전-4")
    assert badge is not None and badge.text == "회전-4" and badge.y0 == 88
    words = [_w("1-1", 52, 184, w=14), _w("차", 40, 185, w=10)]
    assert _find_badge(words, "차1-1").x0 == 40
    assert _find_badge([_w("[차1]", 300, 146)], "차1").text == "[차1]"  # 헤딩 대괄호는 마지막 수단
    assert _find_badge([_w("차1-2", 40, 100)], "차1-1") is None


def test_crop_box_for_case_and_chart():
    case = ImageUnit(key="2019-036580", unit_type="case", source_type="deliberation_case", source_file="a.pdf", source_path="", page_start=1, page_end=2)
    words = [_w("사례", 40, 100), _w("개요", 62, 100), _w("심의번호", 40, 120), _w("주장", 40, 400), _w("내용", 62, 400)]
    x0, y0, x1, y1 = crop_box(case, 420.0, 595.0, words)
    assert (x0, x1) == (28.0, 392.0) and y0 == 94 and y1 == 390
    assert crop_box(case, 420.0, 595.0, [_w("주장", 40, 400)]) is None  # 제목 띠가 없는 페이지 → 다음 페이지 시도

    chart = ImageUnit(key="차1-1", unit_type="standard_chart", source_type="fault_standard", source_file="b.pdf", source_path="", page_start=148, page_end=151, chart_number="차1-1")
    words = [_w("1-1", 52, 184, w=14), _w("차", 40, 185, w=10), _w("※사고발생,", 40, 330), _w("※舊", 40, 342), _w("사고", 40, 380), _w("상황", 62, 380)]
    _, y0, _, y1 = crop_box(chart, 420.0, 595.0, words)
    assert y0 == 184 - 28 and y1 == 370  # '사고 상황' 헤딩 앞까지 (※ 두 줄 포함)
    without_header = [w for w in words if w.text not in {"사고", "상황"}]
    _, _, _, y1 = crop_box(chart, 420.0, 595.0, without_header)
    assert y1 == 342 + 10 + 6  # 마지막 ※ 줄 아래까지


def test_load_units_and_image_path(tmp_path):
    rows = [
        {"unit_type": "case", "unit_key": "deliberation_case:2019-036580", "case_number": "2019-036580", "source_type": "deliberation_case", "source_file": "a.pdf", "page_start": 208, "page_end": 209},
        {"unit_type": "standard_chart", "unit_key": "fault_standard:차1-1", "chart_number": "차1-1", "source_type": "fault_standard", "source_file": "b.pdf", "page_start": 148, "page_end": 151},
        {"unit_type": "reference_page", "unit_key": "fault_standard:page-10", "source_type": "fault_standard", "source_file": "b.pdf", "page_start": 10, "page_end": 10},
    ]
    (tmp_path / "parents.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    units = load_units(tmp_path)
    assert [u.key for u in units] == ["2019-036580", "차1-1"]  # reference_page 는 그림 대상이 아니다
    assert units[1].chart_number == "차1-1" and units[1].page_end == 151

    images = tmp_path / "images"
    images.mkdir()
    (images / "2019-036580.png").write_bytes(b"png")
    (images / "manifest.json").write_text(json.dumps({"version": 1, "images": {"2019-036580": {"file": "2019-036580.png"}}}), encoding="utf-8")
    assert image_path("2019-036580", tmp_path) == images / "2019-036580.png"
    assert image_path("차1-1", tmp_path) is None
    assert sanitize_key("회전-4") == "회전-4" and sanitize_key("213(나)") == "213_나_"
