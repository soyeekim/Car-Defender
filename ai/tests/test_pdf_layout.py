"""심의사례 PDF 의 2단 표를 좌표로 다시 읽는 추출기 — poppler 없이 좌표 목록으로 검증한다."""

from common.korean_text import join_lines, join_wrapped, repair_spacing
from rag.case_documents import parse_case_parent
from rag.pdf_layout import build_page_text
from rag.pdf_words import Word


def _w(text, x0, y0, width=None):
    width = width or 7 * len(text)
    return Word(text=text, x0=x0, y0=y0, x1=x0 + width, y1=y0 + 10)


def test_join_wrapped_uses_vocabulary_for_mid_word_breaks():
    assert join_wrapped("2차로로 진", "로변경을 하던 중") == "2차로로 진로변경을 하던 중"  # '진로' 가 사전 낱말
    assert join_wrapped("차량 또는 진", "행하는 차량이") == "차량 또는 진행하는 차량이"
    assert join_wrapped("오른쪽 도로에서 2", "차로로 진입하던") == "오른쪽 도로에서 2차로로 진입하던"
    assert join_wrapped("있는지 주의하", "여 진로를") == "있는지 주의하여 진로를"
    assert join_wrapped("금지된 구간에서 무리하게 진입", "하면서 사고가") == "금지된 구간에서 무리하게 진입하면서 사고가"
    assert join_wrapped("진로변경을 하였", "으므로, 청구차량의") == "진로변경을 하였으므로, 청구차량의"
    assert join_wrapped("비록 직진 운행이지", "만 도로교통법에") == "비록 직진 운행이지만 도로교통법에"
    assert join_wrapped("정상적인 경", "로로 진로변경을") == "정상적인 경로로 진로변경을"
    assert join_wrapped("운전자가 신", "뢰하는 것으로") == "운전자가 신뢰하는 것으로"
    assert join_wrapped("1차로를 직진 중이었", "고, 피청구차량은") == "1차로를 직진 중이었고, 피청구차량은"
    # 낱말 경계에서 끊긴 줄은 공백을 둔다
    assert join_wrapped("충돌한 사고임", "회전교차로의 경우") == "충돌한 사고임 회전교차로의 경우"
    assert join_wrapped("사고인지", "여부") == "사고인지 여부"
    assert join_wrapped("하던 중", "회전교차로로 진입하던") == "하던 중 회전교차로로 진입하던"
    assert join_lines(["a", "", "b"]) == "a b"


def test_repair_spacing_joins_split_words_and_particles():
    assert repair_spacing("양보 의무 가 명시적으로") == "양보 의무가 명시적으로"
    assert repair_spacing("도 표 266을 기초로 사 고로서") == "도표 266을 기초로 사고로서"
    assert repair_spacing("우 측도로에서 진 로를 변경") == "우측도로에서 진로를 변경"


def _sample_first_page():
    """2018-047765 첫 쪽의 실제 좌표를 단순화한 것 (515 x 728)."""
    words = [
        _w("목차보기", 498, 16), _w("284", 446, 26), _w("자동차사고", 56, 29), _w("과실비율분쟁", 84, 29),
        _w("차대차", 63, 62), _w("회전교차로", 109, 62), _w("사고", 176, 62), _w("-", 206, 62), _w("회전차로", 218, 62), _w("2차로형", 272, 62),
        _w("참고기준", 415, 89), _w("266", 411, 110),
        _w("1.", 498, 132), _w("자동차와", 498, 140),  # 오른쪽 여백 세로 탭
        _w("사례", 61, 177), _w("개요", 82, 177),
        _w("심의번호", 62, 194), _w("2018-047765", 142, 194), _w("결정비율", 250, 194), _w("A", 316, 194), _w("(청구)", 323, 196), _w(":", 348, 196),
        _w("B", 353, 194), _w("(피청구)", 360, 196), _w("=", 394, 196), _w("40", 402, 194), _w(":", 416, 196), _w("60", 422, 194),
        _w("•", 108, 219, 4), _w("회전교차로에서", 115, 219), _w("청구차량이", 175, 219), _w("2차로로", 412, 219), _w("진", 445, 219, 8),
        _w("사고내용", 62, 224),
        _w("로변경을", 115, 234), _w("하던", 150, 234), _w("중", 169, 234), _w("충돌한", 317, 234), _w("사고임", 343, 234),
        _w("(가)", 150, 260), _w("좌회전신호", 170, 260),  # 그림 안 설명 글자 → 버린다
        _w("회전교차로의", 253, 267), _w("경우", 306, 267), _w("양보", 415, 282), _w("의무", 434, 282),
        _w("가", 253, 298), _w("명시적으로", 265, 298), _w("규정되어", 310, 298), _w("있다.", 347, 298),
        _w("참고", 71, 299), _w("인정기준", 62, 313), _w("266", 71, 328),
        _w("기본비율", 253, 361), _w("A", 293, 361), _w(":", 302, 361), _w("B", 307, 361), _w("=", 316, 361), _w("30", 324, 361), _w(":", 338, 361), _w("70", 344, 361),
        _w("주장", 61, 418), _w("내용", 82, 418),
        _w("청구인", 100, 440), _w("피청구인", 330, 440),
        _w("•", 62, 460, 4), _w("피청구차량이", 68, 460), _w("급진입하여", 120, 460), _w("•", 264, 460, 4), _w("청구차량이", 269, 460), _w("실선구간에서", 320, 460),
        _w("일방과실", 68, 475), _w("타당함", 120, 475), _w("차로변경", 269, 475), _w("타당함", 330, 475),
    ]
    return 515.9, 728.5, words


def test_build_page_text_reads_overview_table_by_cells():
    width, height, words = _sample_first_page()
    text, carry = build_page_text(width, height, words)
    lines = text.splitlines()
    assert "목차보기" not in text and "1." not in lines and "자동차와" not in lines  # 머리글·세로 탭 제거
    assert lines[0] == "참고기준 266"  # 오른쪽 상자의 도표 번호를 한 줄로 (213 + (나) 처럼 갈라진 것도 붙인다)
    assert lines[1] == "차대차 회전교차로 사고 - 회전차로 2차로형"
    assert "심의번호 2018-047765 결정비율 A (청구) : B (피청구) = 40 : 60" in lines
    i = lines.index("사고내용")
    assert lines[i + 1] == "• 회전교차로에서 청구차량이 2차로로 진로변경을 하던 중 충돌한 사고임"  # 라벨이 문장을 자르지 않는다
    assert "참고 인정기준" in lines and "266" not in lines[2:]  # 표 안의 배지 번호는 본문에 섞지 않는다
    assert "회전교차로의 경우 양보 의무가 명시적으로 규정되어 있다." in lines  # 그림 설명 글자(좌회전신호)는 빠진다
    assert "좌회전신호" not in text
    assert "기본비율 A : B = 30 : 70" in lines
    assert "[청구인] • 피청구차량이 급진입하여 일방과실 타당함" in lines
    assert "[피청구인] • 청구차량이 실선구간에서 차로변경 타당함" in lines
    assert carry == "주장 내용"  # 두 단 섹션이 쪽을 넘긴다


def test_reconstructed_text_parses_into_clean_case_document():
    width, height, words = _sample_first_page()
    text, _ = build_page_text(width, height, words)
    parent = {"parent_id": "p0", "unit_key": "deliberation_case:2018-047765", "unit_type": "case", "source_type": "deliberation_case",
              "source_file": "a.pdf", "page_start": 282, "page_end": 283, "full_text": "[PDF 282쪽]\n" + text,
              "case_number": "2018-047765", "chart_number": "266", "decision_ratio": "40:60"}
    doc = parse_case_parent(parent)
    assert doc.accident_description.startswith("회전교차로에서 청구차량이 2차로로 진로변경을 하던 중")
    assert doc.decision_ratio == "40:60" and doc.basic_ratio == "30:70" and doc.chart_number == "266"
    assert doc.claimant_argument.startswith("피청구차량이 급진입하여") and doc.respondent_argument.startswith("청구차량이 실선구간에서")
    assert doc.reference_standard.startswith("회전교차로의 경우")  # 상단 '참고기준' 상자·제목이 섞이지 않는다
    assert doc.title == "차대차 회전교차로 사고 - 회전차로 2차로형"
