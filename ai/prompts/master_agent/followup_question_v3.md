[TASK: NEXT QUESTION SELECTION]

현재 사건에서 과실비율 판정에 영향을 주는 사실 가운데, 영상으로 확인되지 않았고
사용자가 객관적으로 답할 수 있는 것을 네가 직접 골라 질문하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

영상 분석이 확인하지 못했거나 추정만 한 항목:
{{video_unknowns}}

이 사고 유형({{accident_profile}})에서 과실비율에 영향을 주는 요소 체크리스트:
{{factor_checklist}}

코드 체크리스트가 제안한 후보(참고용이며 반드시 따를 필요 없음):
{{missing_information}}

반드시 먼저 물어야 하는 항목(있으면 이 항목을 첫 질문으로 한다):
{{forced_field}}

이미 질문했거나 확인된 항목(다시 묻지 않음):
{{asked_fields}}

영상 분석 요약(질문 문맥으로 활용):
{{video_summary}}

선택 기준:
1. 답에 따라 과실비율(기본과실 또는 수정요소)이 실제로 달라지는 사실을 우선한다.
2. 영상으로 다시 확인할 수 있는 것은 묻지 않는다. 영상이 "확인 불가"로 남긴 것 중 사용자가 직접 보았거나 알 수 있는 것
   (상대 방향지시등, 어느 차량이 먼저 진입했는지, 실선/점선 구간, 영상 시작 전 상황, 영상 밖 별도 충돌, 상대 차량 신호)만 묻는다.
3. 사용자의 주관적 과실 판단("잘못", "무리하게", "생각하시나요")은 묻지 않는다.
4. 사고 일시·장소명처럼 판정에 영향이 없는 정보는 판정 관련 질문이 모두 끝난 뒤에만 묻는다.
5. 최대 {{max_questions}}개. 이미 질문한 항목은 제외한다.

작성 규칙:
- field는 Case State의 슬롯 경로(예: other_vehicle.turn_signal, ego_vehicle.entered_first, road.signal_state, video_source.vehicle_owner,
  accident_datetime.date)를 쓰고, 맞는 슬롯이 없으면 `review.<snake_case>` custom field를 만든다 (예: review.solid_line_lane_change, review.pre_video_situation).
- question은 한 문장, 존댓말, 선택지를 제시할 수 있으면 제시. 영상에서 식별된 vehicle ID와 설명을 활용해 어떤 차량인지 명확히 지칭.
- why에는 이 답이 어떤 과실 요소(기본과실 유형 또는 수정요소)의 적용을 결정하는지 한 문장으로 쓴다. 사용자에게 그대로 보여준다.
- intro에는 영상 분석에서 확인된 내용을 1~2문장으로 먼저 알려준 뒤 질문으로 이어지는 짧은 안내문을 쓴다. 이미 대화 중이면 빈 문자열.

출력 JSON:
{
  "intro": "영상에서는 ... 장면이 확인됩니다. 먼저 한 가지 확인하겠습니다.",
  "questions": [
    {"field": "other_vehicle.turn_signal", "question": "상대 차량(vehicle_2)이 차로를 바꿀 때 방향지시등을 켠 것을 보셨나요?", "importance": "high", "why": "방향지시등 미점등은 차로 변경 차량 과실을 가산하는 수정요소입니다."}
  ]
}
