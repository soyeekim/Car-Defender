[TRACKING CONTEXT]

아래는 별도의 CV Detection/Tracking 모듈이 생성한 차량 track 정보다.
이 모듈은 사고 과실을 판단하지 않으며, 프레임별 차량 bounding box와 persistent track ID만 제공한다.

<TRACKED_VEHICLES>
{{tracked_vehicles}}
</TRACKED_VEHICLES>

규칙:
- 각 track ID를 vehicle ID에 1:1로 매핑하고, 매핑을 track_to_vehicle 형식으로 detailed_description 끝에 명시하라.
  (예: track_7 → vehicle_1, track_12 → vehicle_2)
- 매핑은 이 분석 안에서 고정한다.
- 이후 모든 설명은 vehicle ID 기준으로 작성한다.
- track이 끊기거나(occlusion) 다시 나타나는 구간은 identity 혼동 가능성으로 uncertain_facts에 기록한다.
- tracking 정보는 차량 identity와 위치 근거로만 사용하고, 신호·의도·과실 판단에 사용하지 않는다.
