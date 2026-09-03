"""Slot name constants and static slot metadata shared across the agent modules."""

# Slots that are always relevant regardless of accident family/subtype.
COMMON_SLOTS = [
    "accident_target",
    "accident_place",
    "ego_maneuver",
    "opponent_maneuver",
    "ego_collision_area",
    "opponent_claimed_fault_ratio",
]

# All slot names known to the schema (used for extraction/validation).
ALL_SLOTS = [
    "accident_target",
    "accident_place",
    "place_signal_presence",
    "ego_maneuver",
    "opponent_maneuver",
    "ego_lane",
    "opponent_lane",
    "ego_signal",
    "opponent_signal",
    "pedestrian_signal",
    "ego_speed",
    "opponent_speed",
    "ego_collision_area",
    "opponent_collision_area",
    "pre_collision_sudden_braking",
    "post_collision_stop",
    "speed_change_before_collision",
    "braking_evidence",
    "braking_reason",
    "turn_signal",
    "lane_change_direction",
    "left_turn_type",
    "intersection_entry_order",
    "pedestrian_crossing_state",
    "collision_type",
    "collision_timestamp",
    "opponent_claimed_fault_ratio",
]

# Priority order used when several slots are missing at once (section 17).
# Higher number = asked sooner. Slots not listed default to 1.
SLOT_PRIORITY = {
    "ego_signal": 10,
    "opponent_signal": 10,
    "pedestrian_signal": 10,
    "ego_maneuver": 9,
    "opponent_maneuver": 9,
    "accident_place": 8,
    "pre_collision_sudden_braking": 8,
    "post_collision_stop": 4,
    "speed_change_before_collision": 7,
    "braking_evidence": 7,
    "braking_reason": 8,
    "turn_signal": 7,
    "lane_change_direction": 7,
    "left_turn_type": 9,
    "intersection_entry_order": 6,
    "pedestrian_crossing_state": 8,
    "collision_type": 5,
    "collision_timestamp": 4,
    "ego_collision_area": 7,
    "opponent_collision_area": 6,
    "ego_speed": 5,
    "opponent_speed": 4,
    "ego_lane": 4,
    "opponent_lane": 4,
    "accident_target": 3,
    "place_signal_presence": 10,
    "opponent_claimed_fault_ratio": 2,
}


def slot_priority(slot_name: str) -> int:
    return SLOT_PRIORITY.get(slot_name, 1)


# Human-readable meaning of each slot, used to keep the LLM from confusing
# similarly-named slots (e.g. ego_signal = 신호등 색, turn_signal = 방향지시등).
SLOT_DESCRIPTIONS = {
    "accident_target": "사고 당사자 유형 (차대차/차대보행자/차대자전거/차대이륜차)",
    "accident_place": "사고 발생 장소 (교차로/직선도로/횡단보도 등, 신호등 유무 포함)",
    "place_signal_presence": "사고 장소에 신호등이 있는지 여부",
    "ego_maneuver": "내(자차) 차량이 사고 당시 취한 진행 동작 (직진/좌회전/우회전/정차/차선변경 등)",
    "opponent_maneuver": "상대 차량이 사고 당시 취한 진행 동작",
    "ego_lane": "내 차량이 주행하던 차로 위치",
    "opponent_lane": "상대 차량이 주행하던 차로 위치",
    "ego_signal": "내 차량 진행 방향의 신호등 색 (녹색/적색/황색 등) - 방향지시등이 아님",
    "opponent_signal": "상대 차량 진행 방향의 신호등 색 - 방향지시등이 아님",
    "pedestrian_signal": "보행자 신호등 색 (녹색/적색)",
    "ego_speed": "내 차량의 사고 당시 속도 또는 속도 수준",
    "opponent_speed": "상대 차량의 사고 당시 속도 또는 속도 수준",
    "ego_collision_area": "내 차량에서 충돌이 발생한 부위 (전면/후면/좌측면/우측면 등)",
    "opponent_collision_area": "상대 차량에서 충돌이 발생한 부위",
    "pre_collision_sudden_braking": "최초 충돌이 발생하기 전 자차의 급제동 여부",
    "post_collision_stop": "최초 충돌 이후 자차가 정지했는지 여부",
    "speed_change_before_collision": "최초 충돌 직전 자차의 속도 변화",
    "braking_evidence": "충돌 전 급제동을 판단할 수 있는 속도계/G센서/영상 근거",
    "braking_reason": "정차 또는 급정거를 한 이유",
    "turn_signal": "차선 변경 시 방향지시등(깜빡이) 점등 여부 - 신호등이 아님",
    "lane_change_direction": "차선을 변경한 방향 (왼쪽/오른쪽)과 어느 차량이 변경했는지",
    "left_turn_type": "좌회전이 보호 좌회전 신호였는지 비보호 좌회전이었는지",
    "intersection_entry_order": "두 당사자 중 교차로에 먼저 진입한 쪽",
    "pedestrian_crossing_state": "보행자가 횡단 중이었는지와 횡단 방향/위치",
    "collision_type": "충돌 형태 (정면/측면/후방 접촉 등)",
    "collision_timestamp": "영상에서 최초 접촉이 발생한 시각",
    "opponent_claimed_fault_ratio": "상대 보험사 또는 상대방이 주장하는 과실비율",
}


def slot_description(slot_name: str) -> str:
    return SLOT_DESCRIPTIONS.get(slot_name, "")
