"""사용자 지적(2026-09-06) 회귀 테스트: 사고 날짜 지어내기, 기준값 우회, 항목 기호 줄바꿈, 반박 메일 구조."""

from pathlib import Path

from agent.presenters import readable_lines
from assessment.fault_ratio import _validate
from case.extractor import case_today, extract_case_facts, ground_date_facts, rule_based_extraction
from fakes import FakeTextClient, quiet_logger
from state.case_state import AdjustmentFactor, CaseRelevance, CaseState, FaultAssessment, FaultRatio, RetrievedCase
from state.updater import ExtractedFact, UserFactExtraction


def _state(created_at: str = "2026-09-06T03:16:53+00:00") -> CaseState:
    state = CaseState()
    state.created_at = created_at
    return state


def test_relative_dates_resolve_against_case_registration_day():
    state = _state()
    assert case_today(state).isoformat() == "2026-09-06"  # UTC 03:16 → KST 12:16 같은 날
    extraction = rule_based_extraction(state, "어제 다른 차량과 충돌 사고가 발생했어요.")
    dates = [item for item in extraction.new_facts if item.field == "accident_datetime.date"]
    assert [item.value for item in dates] == ["2026-09-05"] and "어제" in dates[0].fact
    assert rule_based_extraction(state, "3일 전 사고예요").new_facts[0].value == "2026-09-03"
    assert rule_based_extraction(state, "2024년 4월 25일 사고").new_facts[0].value == "2024-04-25"
    # '어제'가 사고와 무관한 문장이면 날짜로 쓰지 않는다
    assert not [item for item in rule_based_extraction(state, "어제 보험사에서 전화가 왔어요").new_facts if item.field == "accident_datetime.date"]


def test_llm_extracted_date_is_dropped_unless_the_user_said_it():
    state = _state()
    # 모델이 '어제'를 제멋대로 2024-04-25 로 바꾼 경우 → 등록일 기준으로 고친다
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="accident_datetime.date", value="2024-04-25", fact="사고가 어제 발생했다는 사용자 진술")])
    fixed = ground_date_facts(state, "어제 사고가 났어요", extraction)
    assert fixed.new_facts[0].value == "2026-09-05"
    # 날짜를 말하지 않았는데 뽑아낸 값은 버린다
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="accident_datetime.date", value="2024-04-25", fact="사고 날짜"), ExtractedFact(field="road.road_type", value="roundabout", fact="회전교차로")])
    fixed = ground_date_facts(state, "회전교차로에서 부딪혔어요", extraction)
    assert [item.field for item in fixed.new_facts] == ["road.road_type"] and any("근거 없는 사고 날짜" in item for item in fixed.ignored_opinions)
    # 명시한 날짜는 그대로, '모른다'도 그대로
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="accident_datetime.date", value="2026-08-22", fact="날짜")])
    assert ground_date_facts(state, "8월 22일이요", extraction).new_facts[0].value == "2026-08-22"
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="accident_datetime.date", value="unknown", fact="모름", answers_field="accident_datetime.date")])
    assert ground_date_facts(state, "기억 안 나요", extraction).new_facts[0].value == "unknown"


def test_extract_case_facts_passes_today_and_grounds_dates(tmp_path: Path):
    state = _state()
    client = FakeTextClient()
    extract_case_facts(client, state, "어제 사고 났어요", run_logger=quiet_logger(tmp_path))
    prompt = [user for task, user in client.calls if task == "master_fact_extraction"][-1]
    assert "오늘 날짜(사건 등록일, KST): 2026-09-06" in prompt


def test_model_reasoned_adjustment_keeps_deviation_and_explains_it():
    """모델이 영상 근거로 적용한 수정요소는 기준 사례에서 벗어나는 근거가 된다. 대신 왜 다르게 봤는지 첫 문장에 명시한다."""
    anchor = RetrievedCase(case_id="2018-071745", decision_ratio="50:50", relevance=CaseRelevance(case_id="2018-071745", relevance=0.9, usable_as_primary_reference=True))
    assessment = FaultAssessment(
        fault_ratio=FaultRatio(user=40, opponent=60),
        more_at_fault="opponent",
        primary_case_ids=["2018-071745"],
        adjustment_factors=[AdjustmentFactor(factor="상대 차량의 회전교차로 진입 시 양보 의무 위반 가능성", direction="user_down", percentage=10, source="video", applies=True)],
        explanation="가장 비슷한 심의사례 2018-071745의 결정비율 50:50를 기준으로 보면, 이번 사고는 나 40 : 상대 60 정도의 과실비율이 예상돼요. 사용자 차량은 선진입 상태였어요.",
    )
    validated = _validate(assessment, [anchor], provisional=False)
    assert (validated.fault_ratio.user, validated.fault_ratio.opponent) == (40, 60)
    assert not validated.anchor_enforced and validated.adjustment_factors[0].applies
    assert validated.explanation.startswith(
        "가장 비슷한 심의사례 2018-071745의 결정비율 50:50을 기준으로 삼되, 상대 차량의 회전교차로 진입 시 양보 의무 위반 가능성 때문에 내 과실을 낮춰 나 40 : 상대 60로 봤어요."
    )
    assert validated.explanation.endswith("사용자 차량은 선진입 상태였어요.") and validated.explanation.count("가장 비슷한 심의사례") == 1
    assert validated.reasoning_summary[0].startswith("[기준 사례와 조정]")

    # 출처 없는(rag) '가능성' 항목은 조정 근거가 아니다 → 기준값으로 돌아간다
    weak = FaultAssessment(
        fault_ratio=FaultRatio(user=40, opponent=60), more_at_fault="opponent", primary_case_ids=["2018-071745"],
        adjustment_factors=[AdjustmentFactor(factor="양보 의무 위반 가능성", direction="user_down", percentage=10, source="rag", applies=True)],
    )
    validated = _validate(weak, [anchor], provisional=False)
    assert (validated.fault_ratio.user, validated.fault_ratio.opponent) == (50, 50) and validated.anchor_enforced
    assert not validated.adjustment_factors[0].applies and "40:60" in (validated.possible_range or [])


def test_enumerator_periods_do_not_break_lines():
    text = "가. 블랙박스 영상에서 확인된 사실 나. 유사 심의사례 A. 항목입니다. 다음 문장이에요."
    assert readable_lines(text) == "가. 블랙박스 영상에서 확인된 사실 나. 유사 심의사례 A. 항목입니다.\n다음 문장이에요."


def test_mail_body_has_no_statement_section_and_keeps_sentences_whole(tmp_path: Path):
    from assessment.fault_ratio import assess_fault_ratio
    from document.package import build_verified_package
    from document.rebuttal import build_mail_body
    from test_assessment_and_documents import _ready_state

    state = _ready_state()
    state.fault_assessment = assess_fault_ratio(FakeTextClient(), state, state.retrieved_cases, run_logger=quiet_logger(tmp_path))
    long_reason = "양 차량이 회전교차로로 진입하다가 발생한 사고로, 피청구차량이 2차로에서 진입하였으나 사고 후 정차 위치를 보면 선행상태에서 진입하였던 것으로 확인되어 과실비율을 결정함 도표 264는 양 차량이 동시진입하는 상황으로서 서로 양보할 의무를 위반한 과실이 있고, 왼쪽에 있는 차량은 오른쪽에 있는 차량에 좀 더 주의하여 회전교차로에 진입하여야 하므로 기본과실을 60:40으로 정한다"
    state.retrieved_cases[0].decision_reasons = [long_reason]
    state.retrieved_cases[0].accident_description = "청구차량이 3차로에서 회전교차로로 진입 중 2차로에서 회전교차로에 선진입하여 진행하는 피청구차량과 충돌한 사고임 " + "부가 설명이 이어진다. " * 20
    package = build_verified_package(state)
    body = build_mail_body(package)
    lines = body.split("\n")
    assert "나. 당사자 진술" not in body and "라. " not in body
    assert "가. 블랙박스 영상에서 확인된 사실" in lines and "나. 유사 심의사례" in lines and "다. 과실 수정요소" in lines
    for heading in ("2. 주장 근거", "나. 유사 심의사례", "다. 과실 수정요소", "3. 결론"):
        assert lines[lines.index(heading) - 1] == ""  # 항목이 바뀔 때마다 빈 줄
    reason_line = next(line for line in lines if line.startswith("  심의 이유:"))
    assert reason_line.endswith(("결정함", "정한다", ".", "…"))  # 글자 수가 아니라 문장 단위로 끝난다 (전에는 '…주의하여 회' 로 잘렸다)
    overview = next(line for line in lines if line.startswith("  사고 개요:"))
    assert overview.endswith(("사고임", ".", "…")) and len(overview) < 320
    assert len(body) <= 5000
