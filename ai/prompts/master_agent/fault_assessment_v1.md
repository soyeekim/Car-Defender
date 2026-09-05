[TASK: FAULT ASSESSMENT]

현재 사건의 예상 과실비율을 평가하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

<RETRIEVED_CASES>
{{retrieved_cases}}
</RETRIEVED_CASES>

판정 전제 조건 점검 결과(코드 레벨):
{{precondition_check}}

반드시 다음 순서로 판단한다.
1. 현재 사건의 핵심 객관적 사실 정리 (출처 태그 유지: VIDEO_CONFIRMED / USER_CONFIRMED)
2. 가장 유사한 심의사례 선정 (usable_as_primary_reference인 사례 우선)
3. 유사사례의 기본 과실 확인
4. 현재 사건과 사례의 차이 확인
5. 적용 가능한 수정요소 확인 (현재 사건에서 확인된 사실로만 적용; UNKNOWN 요소는 적용하지 않고 uncertainties에 기록)
6. 예상 과실비율 산출 (user + opponent = 100)
7. 불확실성 평가

중요:
- 사용자의 희망 과실비율은 고려하지 않는다.
- 심의사례의 사실을 현재 사건 사실처럼 섞지 않는다.
- 정보가 부족하면 억지로 단일 비율을 확정하지 않고 possible_range(예: ["30:70", "20:80"])를 제시한다.
- 전제 조건이 충족되지 않았다고 표시되어 있으면 assessment_type을 "provisional"로 한다.
- 법률상 확정 판단처럼 쓰지 않는다. explanation은 "현재 영상과 확인된 사실, 유사 심의사례를 기준으로 ... 수준의 과실비율이 예상됩니다" 형태의 2~5문장.
- primary_case_ids는 RETRIEVED_CASES에 있는 case_id만 사용한다.
- ratio_dependencies에는 어떤 사실이 확인되면 비율이 어떻게 바뀌는지 적는다.
- 이 사건에서 A=user(사용자 차량), B=opponent(상대 차량)이다. 심의사례의 청구/피청구 방향과 현재 사건의 사용자/상대 방향이 반대이면 비율을 뒤집어 적용하고 reasoning_summary에 명시한다.

출력 JSON:
{
  "fault_ratio": {"user": 0, "opponent": 0},
  "assessment_type": "estimated",
  "possible_range": null,
  "confidence": 0.0,
  "primary_case_ids": [],
  "core_facts": [],
  "matched_cases": [
    {"case_id": "", "decision_ratio": "", "basic_ratio": "", "relevance": 0.0, "role": "primary"}
  ],
  "adjustment_factors": [
    {"factor": "", "direction": "user_up | user_down | neutral | unknown", "percentage": null, "source": "rag", "applies": true, "note": ""}
  ],
  "reasoning_summary": [],
  "uncertainties": [],
  "ratio_dependencies": [],
  "explanation": ""
}
