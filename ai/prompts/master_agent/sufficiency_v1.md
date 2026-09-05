[TASK: INFORMATION SUFFICIENCY]

현재 Case State를 검토하여 예상 과실비율 판단을 시작할 수 있을 만큼
정보가 충분한지 평가하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

코드 레벨 사전 점검 결과(반드시 존중한다. 여기서 부족하다고 판단된 항목을 충분하다고 바꾸지 않는다):
{{deterministic_check}}

다음을 점검한다.
- 영상 기준 차량이 누구인지 (업로드 영상이 사용자 차량 블랙박스인지)
- 사고 장소 유형
- 차량별 진행 방향
- 충돌 관계 (충돌 당사 차량, 충돌 형태, 충돌 부위)
- 과실 판단에 중요한 신호/차선 조건 (또는 불확실성이 명시되었는지)
- 영상에서 직접 확인 가능한 핵심 사실
- 유사 심의사례 검색 query 생성 가능 여부

규칙:
- missing_information의 각 항목은 field 경로(Case State의 키)와 importance를 가진다.
- user_answerable에는 영상으로 확인할 수 없고 사용자가 객관적으로 알 수 있는 항목만 넣는다.
  (예: 영상 소유 관계, 사고 일시, 영상 밖의 별도 충돌 여부, 상대방 주장)
- video_reanalysis_targets에는 영상으로 다시 확인할 수 있는 쟁점을 focus 문장으로 넣는다.
  (예: "충돌 직전 상대 차량 진행 방향 신호등 색상과 정지선 통과 시점")
- 이미 asked_fields에 있거나 값이 확인된 항목은 다시 넣지 않는다.
- 정확한 속도처럼 영상으로 확정하기 어려운 항목은 재분석 대상으로 넣지 않는다.

출력 JSON:
{
  "ready_for_rag": true,
  "ready_for_assessment": false,
  "missing_information": [
    {"field": "video_source.vehicle_owner", "importance": "critical", "reason": "", "user_answerable": true, "video_recheckable": false}
  ],
  "user_answerable": ["video_source.vehicle_owner"],
  "video_reanalysis_targets": [],
  "reasons": []
}
