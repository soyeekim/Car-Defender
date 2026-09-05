기존 분석에서 다음 차량이 확인되었다: {{vehicle_ids}}
충돌 후보 시간 구간은 약 {{collision_window}}이다.

이번 분석에서는 해당 시간 구간 전후만 집중적으로 검토하여
실제 충돌(접촉)한 vehicle pair를 재검증하라.

기존 vehicle ID를 변경하지 않는다.

특히 다음 pair를 모두 비교하고 각 pair의 collision_probability와 근거를 pair_scores에 작성하라.
{{pair_list}}

확인 항목:
- 차량 간 거리 변화와 차체 접촉 가능성
- 급격한 방향 변화, 카메라 충격(흔들림), 충돌 직후 차량 움직임 변화
- 각 participant의 충돌 부위
- 충돌 시각(most_likely_timestamp)

불확실하면 alternative_pairs에 대안 pair를 적고 confidence를 낮게 유지하라.
