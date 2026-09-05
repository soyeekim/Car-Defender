[TASK: CASE REVIEW QUESTIONS]

유사 심의사례를 찾았다. 판정 전에 현재 사건과 심의사례의 차이, 그리고 심의사례의 수정요소가
현재 사건에 적용되는지를 결정할 수 있는 객관적 사실을 사용자에게 확인하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

<RETRIEVED_CASES>
{{retrieved_cases}}
</RETRIEVED_CASES>

영상만으로 확인되지 않은 사항:
{{uncertain_facts}}

이미 질문했거나 확인된 항목(다시 묻지 않음):
{{asked_fields}}

규칙:
- 최대 {{max_questions}}개. 가장 유사한 사례(usable_as_primary_reference)의 결정비율에 영향을 준 수정요소부터.
- 사용자가 객관적으로 알 수 있는 사실만 묻는다 (예: 방향지시등을 켠 것을 보았는지, 어느 차량이 먼저 진입했는지, 차로 변경 지점이 실선 구간이었는지, 정지선 앞에서 멈췄는지).
- 주관적 과실 판단("잘못", "무리하게", "생각하시나요")은 묻지 않는다.
- 영상에서 이미 VIDEO_CONFIRMED로 확인된 사실은 묻지 않는다.
- 각 질문의 field는 아래 슬롯 경로 중 하나를 쓰고, 해당 슬롯이 없으면 `review.<snake_case>` 형식의 custom field를 만든다.
  슬롯 경로: ego_vehicle.turn_signal, ego_vehicle.entered_first, ego_vehicle.lane_change, ego_vehicle.braking, ego_vehicle.signal,
  other_vehicle.turn_signal, other_vehicle.entered_first, other_vehicle.lane_change, other_vehicle.braking, other_vehicle.signal,
  road.signal_state, road.signal_present, collision.type
  custom 예: review.solid_line_lane_change, review.secondary_collision, review.speed_limit_sign
- why에는 이 답이 어떤 수정요소(예: "실선구간 진로변경 +10")의 적용 여부를 결정하는지 적는다.
- summary에는 심의사례 대비 현재 사건에서 아직 확인되지 않은 쟁점을 1~2문장으로 적는다.
- 확인할 것이 없으면 questions를 빈 배열로 둔다.

출력 JSON:
{
  "summary": "가장 유사한 2018-047765 사례는 실선구간 진로변경으로 청구 측 과실이 10% 가산되었으나 본 사건에서는 해당 여부가 확인되지 않았습니다.",
  "questions": [
    {"field": "review.solid_line_lane_change", "question": "상대 차량이 차로를 바꾼 지점이 실선 구간이었나요, 점선 구간이었나요?", "importance": "high", "why": "실선구간 진로변경 수정요소 적용 여부", "related_case_ids": ["2018-047765"]},
    {"field": "other_vehicle.turn_signal", "question": "상대 차량이 차로를 바꿀 때 방향지시등을 켠 것을 보셨나요?", "importance": "high", "why": "방향지시등 미점등 수정요소", "related_case_ids": ["2019-023315"]}
  ]
}
