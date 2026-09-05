[TASK: FAULT ASSESSMENT]

현재 사건의 예상 과실비율을 평가하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

<RETRIEVED_CASES>
{{retrieved_cases}}
</RETRIEVED_CASES>

기준값(anchor) — 가장 유사한 심의사례:
{{anchor_reference}}

심의사례 검토 단계에서 사용자가 확인해 준 사실:
{{review_answers}}

판정 전제 조건 점검 결과(코드 레벨):
{{precondition_check}}

반드시 다음 순서로 판단한다.
1. 현재 사건의 핵심 객관적 사실 정리 (출처 태그 유지: VIDEO_CONFIRMED / USER_CONFIRMED)
2. 가장 유사한 심의사례(anchor) 선정 — usable_as_primary_reference인 사례 우선
3. anchor 사례의 결정비율을 기준값으로 둔다. (결정비율이 없으면 기본비율)
4. 현재 사건과 anchor 사례의 차이를 확인한다.
5. 기준값에서 벗어나려면 현재 사건에서 VIDEO_CONFIRMED 또는 USER_CONFIRMED로 확인된 차이(수정요소)가 있어야 한다.
   - 확인된 수정요소는 adjustment_factors에 applies=true, source=video|user 로 기재하고 percentage를 적는다.
   - "확인 불가", "불명확"인 차이는 기준값에서 벗어날 근거가 아니다. 이 경우 기준값을 fault_ratio로 두고,
     그 사실이 확인되면 바뀔 수 있는 비율을 possible_range와 ratio_dependencies에 적는다.
   - anchor 사례에서 가산/감산된 수정요소가 현재 사건에서 "적용되지 않음"으로 확인된 경우(예: 실선 아님이 USER_CONFIRMED)에만 그 만큼 되돌린다.
6. 누가 더 책임이 큰지 먼저 정한다 → more_at_fault ("user" | "opponent" | "equal").
   그 다음 예상 과실비율을 산출한다 (user + opponent = 100). fault_ratio는 more_at_fault와 반드시 일치해야 한다:
   more_at_fault가 "opponent"이면 fault_ratio.user < fault_ratio.opponent (예: 나 20 : 상대 80).
   숫자는 항상 "나(user)의 과실 : 상대(opponent)의 과실" 순서다. 심의사례의 청구:피청구 순서를 그대로 옮기지 않는다.
7. 불확실성 평가

중요:
- 사용자의 희망 과실비율은 고려하지 않는다.
- 심의사례의 사실을 현재 사건 사실처럼 섞지 않는다.
- 전제 조건이 충족되지 않았다고 표시되어 있으면 assessment_type을 "provisional"로 한다.
- 법률상 확정 판단처럼 쓰지 않는다. explanation은 사용자에게 그대로 보이는 글이다: 해요체로,
  "가장 비슷한 심의사례 {anchor}의 결정비율 {ratio}를 기준으로 보면 나 A : 상대 B 정도의 과실비율이 예상돼요" 형태의 2~5문장.
  왜 그 비율인지(핵심 사실 1~2개), 무엇이 확인되면 바뀔 수 있는지 쉽게 풀어 쓴다.
- reasoning_summary, uncertainties, ratio_dependencies도 사용자에게 보이므로 전문 용어는 풀어 쓴다.
- primary_case_ids는 RETRIEVED_CASES에 있는 case_id만 사용하고 첫 번째가 anchor다.
- reasoning_summary 첫 줄에 기준값과 그 사례 번호를 명시하고, 기준값과 다르게 판단했다면 어떤 확인된 사실 때문인지 적는다.
- 이 사건에서 A=user(사용자 차량), B=opponent(상대 차량)이다. 심의사례의 청구/피청구 방향과 현재 사건의 사용자/상대 방향이 반대이면 비율을 뒤집어 적용하고 reasoning_summary에 명시한다.

출력 JSON:
{
  "more_at_fault": "user | opponent | equal",
  "fault_ratio": {"user": 0, "opponent": 0},
  "assessment_type": "estimated",
  "possible_range": null,
  "confidence": 0.0,
  "anchor_case_id": "",
  "anchor_ratio": "40:60",
  "primary_case_ids": [],
  "core_facts": [],
  "matched_cases": [
    {"case_id": "", "decision_ratio": "", "basic_ratio": "", "relevance": 0.0, "role": "primary"}
  ],
  "adjustment_factors": [
    {"factor": "", "direction": "user_up | user_down | neutral | unknown", "percentage": null, "source": "video | user | rag", "applies": true, "note": ""}
  ],
  "reasoning_summary": [],
  "uncertainties": [],
  "ratio_dependencies": [],
  "explanation": ""
}
