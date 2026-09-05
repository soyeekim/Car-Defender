[TASK: FULL ANALYSIS FROM TIMESTAMPED FRAMES]

다음 이미지는 동일한 약 {{duration_sec}}초 교통사고 영상에서 시간 순서대로 추출한 프레임이다.
프레임 간격: {{interval_sec}}초, 총 {{frame_count}}장.
각 프레임 앞에 "Frame NN — SS.s sec" 형식의 timestamp가 붙어 있다.

각 프레임의 timestamp를 사용하여 하나의 연속된 사고 영상으로 해석하라.

분석 순서:
1. 모든 relevant vehicle 식별 (블랙박스 촬영 차량 포함, is_ego=true, ego_vehicle_id 기록)
2. 고정 vehicle ID 부여 (vehicle_1, vehicle_2, ...)
3. 시간 순서대로 각 차량의 위치 변화 추적 (tracking_timeline)
4. 충돌 후보 시간 구간 탐색 (collision_window)
5. 실제 충돌 pair 판단 (모든 pair를 pair_scores로 비교한 뒤 collision_pair 선택)
6. non-participant vehicle 구분
7. 과실비율 판단에 필요한 객관적 사실 추출 (fault_relevant_factors)
8. 사고 timeline 및 detailed_description 생성

규칙:
- 프레임 사이에 보이지 않는 사건을 임의로 만들어내지 않는다.
- 충돌 순간이 프레임 사이에 있을 가능성이 있으면 정확히 확정하지 말고
  recommended_dense_sampling에 재추출이 필요한 시간 구간(start_sec, end_sec)을 반환한다.
- 한 번 부여한 vehicle ID는 모든 프레임에서 동일하게 유지한다. 동일 차량을 다른 ID로 재부여하지 않는다.
- 시각은 프레임 timestamp를 기준으로 "MM:SS.s" 형식으로 기록한다.

추가 컨텍스트: {{extra_context}}
