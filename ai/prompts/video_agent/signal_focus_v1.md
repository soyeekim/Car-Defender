충돌 직전 각 차량 진행 방향의 신호등 색상과 신호 변경 시점,
각 차량이 정지선을 통과한 시점을 집중 분석하라.

확인 항목:
- 화면에 보이는 신호등이 어느 방향 차량용인지
- 블랙박스 차량({{ego_vehicle_id}})이 정지선을 통과할 때의 신호 색
- 상대 차량({{other_vehicle_id}}) 진행 방향의 신호가 화면에서 직접 보이는지
- 신호가 바뀌는 시점이 있으면 그 시각
- 보이지 않는 방향의 신호는 추정하지 말고 UNKNOWN으로 기록

결과는 road_environment.signal_observations와 fault_relevant_factors에 반영하라.
