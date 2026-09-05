[TASK: FULL ANALYSIS]

업로드된 교통사고 영상을 처음부터 끝까지 분석하라.

목표는 단순 영상 요약이 아니라, 향후 교통사고 과실비율 판단에 사용할 수 있도록
사고 관련 객관적 사실을 최대한 상세히 추출하는 것이다.

분석은 내부적으로 반드시 다음 순서를 따른다.
1. 도로 및 사고 환경 파악
2. 영상에 등장하는 모든 relevant vehicle 식별
3. 각 차량에 고정 vehicle ID 부여 (블랙박스 촬영 차량은 반드시 포함하고 is_ego=true, ego_vehicle_id에 기록)
4. 각 vehicle ID의 움직임을 시간 순서로 추적 (tracking_timeline)
5. 실제 충돌이 발생한 시간 구간 탐색 (collision_window)
6. 가능한 vehicle pair를 비교하여 실제 충돌 차량 2대를 판단 (pair_scores → collision_pair)
7. 충돌하지 않은 차량을 non_participants로 구분
8. 과실비율 판단에 필요한 객관적 사실 추출 (fault_relevant_factors: 신호, 선진입, 차선 변경, 방향지시등, 제동, 충돌 부위 등)
9. 마지막에 전체 사고 timeline, confirmed_facts / inferred_facts / unknown_or_unobservable, detailed_description 생성

특히 사고 발생 전 → 충돌 직전 → 충돌 순간 → 충돌 이후의 시간 흐름을 timeline에 명확하게 작성하라.

System Prompt의 REQUIRED OBSERVATIONS를 모두 점검하고,
보이지 않거나 불확실한 정보는 UNKNOWN(또는 낮은 confidence)으로 기록하라.

신호교차로 사고라면 신호등 유무와 관찰 가능한 신호 상태를 반드시 fault_relevant_factors에 포함하고,
차선 변경 사고라면 변경 주체·시작 시점·방향지시등을, 회전교차로 사고라면 회전 차량과 진입 차량 및 진입 시점을 반드시 점검하라.

충돌 pair 판단이 불확실하면 alternative_pairs와 recommended_reanalysis_targets에 재검토가 필요한 쟁점을 적어라.
충돌 순간이 명확하지 않거나 프레임 사이에 있을 가능성이 있으면 recommended_dense_sampling에 재검토 구간(초 단위)을 적어라.

영상 길이(초): {{duration_sec}}
추가 컨텍스트: {{extra_context}}
