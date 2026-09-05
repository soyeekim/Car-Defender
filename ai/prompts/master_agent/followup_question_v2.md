[TASK: FOLLOW-UP QUESTION GENERATION]

현재 Case State에서 부족한 정보 중 사용자가 객관적으로 답할 수 있는 사실만 질문하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

질문 대상 항목(중요도 순):
{{missing_information}}

영상 분석 요약(질문 문맥으로 활용):
{{video_summary}}

규칙:
- 1회 최대 {{max_questions}}개. 질문 대상 항목 수가 그 이하이면 가능한 모든 항목을 질문한다.
- 가장 중요한 질문부터: 영상 소유 관계 → 영상이 확인하지 못한 과실 관련 사실(방향지시등, 진입 순서, 영상 시작 전 상황, 별도 충돌) → 사고 일시·장소
- 사용자의 주관적 과실 판단을 묻지 않음 ("잘못", "무리하게", "생각하시나요" 같은 표현 금지)
- 영상으로 다시 확인할 수 있는 것은 질문하지 않음. 단, 영상 분석이 "확인 불가"로 남긴 사실 중 사용자가 직접 보았거나 알 수 있는 것
  (상대 방향지시등, 어느 차량이 먼저 진입했는지, 영상 시작 전 상황, 영상 밖 별도 충돌, 실선/점선 구간)은 질문 대상이다.
- 이미 확인된 정보나 이미 질문한 항목(asked_fields)은 다시 질문하지 않음
- 각 질문은 한 문장, 존댓말, 구체적인 선택지를 제시할 수 있으면 제시 (예: "본인 차량 블랙박스인가요, 상대 차량 블랙박스인가요?")
- 영상에서 식별된 vehicle ID와 설명을 활용해 어떤 차량을 말하는지 명확히 지칭
- 질문 field는 질문 대상 항목의 field를 그대로 사용한다.
- intro에는 영상 분석에서 확인된 내용을 1~2문장으로 먼저 알려준 뒤 질문으로 이어지는 짧은 안내문을 작성

출력 JSON:
{
  "intro": "영상에서는 ... 장면이 확인됩니다. 먼저 몇 가지 확인하겠습니다.",
  "questions": [
    {"field": "video_source.vehicle_owner", "question": "업로드하신 영상은 본인 차량 블랙박스인가요, 상대 차량 블랙박스인가요?", "importance": "critical"},
    {"field": "other_vehicle.turn_signal", "question": "상대 차량이 차로를 바꿀 때 방향지시등을 켠 것을 보셨나요?", "importance": "high"},
    {"field": "accident_datetime.date", "question": "사고 발생 날짜는 언제인가요?", "importance": "medium"}
  ]
}
