[TASK: USER FACT EXTRACTION]

아래 사용자 메시지에서 현재 교통사고 사건에 새롭게 추가할 수 있는
객관적 사실만 추출하라.

{{user_message}}

직전 턴에 Agent가 사용자에게 한 질문(있는 경우):
{{pending_questions}}

기존 Case State:
<CASE_STATE>
{{case_state}}
</CASE_STATE>

영상에서 식별된 차량 목록(vehicle ID → 설명):
{{video_vehicles}}

규칙:
- 감정 표현은 제외
- 과실에 대한 사용자 의견("상대가 100% 잘못", "나는 잘못 없음")은 제외하고 ignored_opinions에만 기록
- 상대방 의도 추정("일부러", "무리하게")은 사실로 추출하지 않음
- 날짜, 시간, 장소, 차량 소유 관계(영상이 본인 차량 블랙박스인지), 영상 출처, 진행 방향,
  차선, 신호 상태에 대한 진술, 충돌 부위, 사고 전후 상황, 별도 충돌 여부 등 객관적 정보만 추출
- 사용자가 "모른다", "기억 안 난다"고 답하면 value를 "unknown"으로 하고 answers_field에 해당 field를 적는다.
- 사용자의 진술이 영상에서 확인 가능한 항목이면 verification을 판단하라:
  visible_in_video(영상과 일치), contradicts_video(영상과 다름), not_visible_in_video(영상에서 확인 불가), unverified
- 기존 정보와 충돌하면 conflicts에 표시하고 기존 값을 덮어쓰지 않는다.
- 상대방(또는 상대 보험사)의 주장이 포함되어 있으면 opponent_claim에 요약한다.
- field는 아래 경로 중 하나를 사용하고, 해당하는 경로가 없으면 null로 둔다.

허용 field 경로:
video_source.type (dashcam | cctv | third_party)
video_source.vehicle_owner (user | opponent)
accident_datetime.date (YYYY-MM-DD)
accident_datetime.time (HH:MM 또는 "오후 3시경")
road.location_name
road.road_type
road.intersection_type
road.lane_count
road.signal_present (true | false)
road.signal_state
road.road_surface
road.weather
ego_vehicle.vehicle_id (영상의 vehicle ID)
ego_vehicle.movement
ego_vehicle.entry_direction
ego_vehicle.lane
ego_vehicle.estimated_speed
ego_vehicle.signal
ego_vehicle.turn_signal
ego_vehicle.lane_change
ego_vehicle.braking
ego_vehicle.entered_first
other_vehicle.vehicle_id
other_vehicle.movement
other_vehicle.entry_direction
other_vehicle.lane
other_vehicle.estimated_speed
other_vehicle.signal
other_vehicle.turn_signal
other_vehicle.lane_change
other_vehicle.braking
other_vehicle.entered_first
collision.type
collision.ego_collision_part
collision.other_collision_part
collision.relative_direction
collision.timestamp
collision.participants_confirmed (true | false)

여기서 ego_vehicle은 항상 '사용자 차량', other_vehicle은 '상대 차량'이다.
사용자가 "내 차 블랙박스야"라고 하면 video_source.vehicle_owner=user, video_source.type=dashcam이다.

출력 JSON:
{
  "new_facts": [
    {
      "field": "video_source.vehicle_owner",
      "value": "user",
      "fact": "업로드 영상은 사용자 차량의 블랙박스 영상이다.",
      "confidence": 0.95,
      "verification": "not_visible_in_video",
      "answers_field": "video_source.vehicle_owner"
    }
  ],
  "conflicts": [
    {
      "field": "",
      "existing_value": "",
      "new_value": "",
      "description": ""
    }
  ],
  "opponent_claim": null,
  "ignored_opinions": [],
  "no_new_facts": false
}
