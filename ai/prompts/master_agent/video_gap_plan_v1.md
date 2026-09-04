[TASK: VIDEO GAP PLAN — 1차 영상 분석을 읽고 2차 분석 지시서를 쓴다]

Video Analysis Agent의 1차 분석 결과가 도착했다. 너는 이 사건의 과실비율을 판정하고 사건경위서를 쓰기 위해
아직 부족한 것이 무엇인지 스스로 판단하고, Video Agent에게 **같은 영상을 한 번 더 보게 할 지시서**를 작성하라.
이 지시서로 영상 분석은 딱 한 번 더 실행된다. 그 뒤에는 사용자 대화로 넘어가므로, 영상에서 얻을 수 있는 것은 여기서 최대한 얻어야 한다.

<USER_CASE_DESCRIPTION>
{{user_description}}
</USER_CASE_DESCRIPTION>

<VIDEO_RESULT>
{{video_result}}
</VIDEO_RESULT>

1차 분석이 CONFIRMED로 확정한 사실:
{{confirmed_facts}}

1차 분석이 미확인/추정으로 남긴 항목과 이유:
{{unknowns}}

추론 순서 (reasoning에 그대로 적는다):
1. 사고 구조(장소 유형, 양 차량의 진행 관계, 충돌 형태)를 정리하고, 이 구조에서 기본과실과 수정요소를 결정하는 요소들을 열거한다.
2. 각 요소가 1차 결과에서 CONFIRMED인지, INFERRED(추정)인지, UNKNOWN인지 대조한다.
3. 화면에 찍혀 있어 다시 보면 확인할 수 있는 것만 고른다.
   - 다시 볼 가치가 있는 것: 노면 차선 종류, 정지선·횡단보도, 신호등 색과 변경 시점, 각 차량의 차로 위치, 화면 안 차량의 방향지시등·제동등,
     제동·감속, 충돌 부위, 진입 순서(양 차량이 화면에 있을 때), 충돌 직전 차량 간 거리·속도 변화(정성), 회피 조향, 경적/급정거 흔적
   - 제외: 1차 분석이 "화각 밖 / 영상 시작 전·종료 후 / 가려짐"이라고 명시한 것, 정확한 km/h 수치, 운전자 의도
4. 과실비율에 영향이 큰 순서로 최대 {{max_items}}개를 고르고, 각 항목에 확인할 시간 구간(MM:SS~MM:SS)과 왜 필요한지를 적는다.
5. 사건경위서를 위해 충돌 직전 3~4초의 사건 서술(각 차량의 위치 변화, 상대 거리, 감속·조향, 접촉 순간, 접촉 후 움직임)을
   0.5초 단위 timeline으로 보완하도록 enrichment에 지시한다.
6. 다시 볼 것이 전혀 없으면 recheck_items를 빈 배열로 두고 reasoning에 이유를 적는다 (enrichment는 그래도 요청해도 된다).

작성 규칙:
- item: Video Agent가 그대로 실행할 수 있는 구체적 지시 한 문장 ("00:03.5~00:05.0 구간에서 vehicle_2가 차로를 옮기는 지점의 차선이 실선인지 점선인지 확인하고 timestamp를 기록하라")
- target_field: 결과가 들어갈 schema 필드 (예: road_environment.lane_marking_at_lane_change, vehicles[vehicle_2].turn_signal,
  road_environment.signal_observations, collision.participant_parts, vehicles[vehicle_1].braking, vehicles[...].entered_intersection_first)
- why: 이 사실이 결정하는 과실 요소
- skipped: 다시 봐도 소용없어 제외한 항목과 이유 (사용자에게 물어볼 후보가 된다)

출력 JSON:
{
  "reasoning": ["사고 구조: …", "판정 요소: …", "1차 결과 대조: … CONFIRMED / … UNKNOWN(이유)", "다시 볼 것: …", "제외: …"],
  "recheck_items": [
    {"item": "<구체적 지시 한 문장>", "time_window": "MM:SS~MM:SS", "target_field": "<schema 필드>", "why": "<과실 요소>"}
  ],
  "enrichment": "<충돌 직전 구간 서술 보완 지시 한두 문장>",
  "skipped": [
    {"item": "<항목>", "reason": "<화각 밖 등>"}
  ]
}
