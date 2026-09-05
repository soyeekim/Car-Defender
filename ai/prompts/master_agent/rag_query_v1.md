[TASK: RAG QUERY GENERATION]

현재 사건의 Case State를 이용하여 과실비율 심의사례 검색을 위한 query를 작성하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

영상 상세 설명:
<VIDEO_DESCRIPTION>
{{video_description}}
</VIDEO_DESCRIPTION>

검색 query에는 가능한 경우 다음 요소를 포함한다.
- 사고 장소 유형 (사거리 신호교차로, 무신호 삼거리, 회전교차로, 직선도로, 주차장 등)
- A(사용자)/B(상대) 차량의 진행 방향
- 직진/좌회전/우회전/유턴
- 차선 변경 여부와 주체
- 선진입 관계
- 신호 조건
- 상대 차량 진입 방향 (측면, 맞은편, 후방 등)
- 충돌 위치 및 형태 (예: A 전면과 B 좌측면 충돌)
- 핵심 수정요소 후보 (방향지시등 미점등, 급차로변경, 서행 여부 등)

규칙:
- 감정 표현이나 사용자 주장 대신 객관적인 사고 구조만 사용한다.
- 심의사례 문서에서 쓰는 용어(청구차량/피청구차량, 직진 대 직진, 사거리 교차로, 차로변경 등)를 활용한다.
- structured_query는 검색에 적합한 짧은 키워드 나열(한 줄), detailed_query는 dense retrieval용 3~6문장 설명이다.
- filters에는 확실한 조건만 넣는다. 불확실하면 null로 둔다.

출력 JSON:
{
  "structured_query": "사거리 신호교차로 직진 대 직진 측면 진입 선진입 A전면 B좌측면 충돌",
  "detailed_query": "신호기가 있는 사거리 교차로에서 A 차량이 녹색 신호에 직진하던 중 ...",
  "key_factors": ["사거리 교차로", "직진 대 직진", "측면 진입"],
  "filters": {
    "road_type": null,
    "intersection_type": null,
    "signal_present": null,
    "accident_target": "차대차",
    "movement_a": null,
    "movement_b": null,
    "lane_change": null,
    "keywords": []
  }
}
