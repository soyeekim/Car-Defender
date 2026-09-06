[TASK: CHART ASSESSMENT]

현재 사건에 딱 맞는 심의사례가 없어서 과실비율 인정기준 도표로 계산한다.
너는 "어느 도표의 어느 변형이 맞는지, 사건의 나·상대가 도표의 A·B 중 누구인지, 도표의 수정요소 행 가운데 확인된 사실로
적용되는 것이 무엇인지"만 판단한다. 비율 숫자의 덧셈·뺄셈은 코드가 한다. 숫자를 직접 계산해 적지 않는다.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

<CHARTS>
{{charts}}
</CHARTS>

각 도표는 role_a / role_b (A·B 차량의 진행 상황), situation (사고 상황), variants (변형별 기본비율), modifiers (수정요소 행, id 로 지칭)를 갖는다.

반드시 다음 순서로 판단한다.
1. 현재 사건의 확인된 사실(VIDEO_CONFIRMED / USER_CONFIRMED)만 정리한다. UNKNOWN 인 것은 사실이 아니다.
2. 사고 상황(situation)과 A·B 역할이 현재 사건의 사용자 차량·상대 차량 진행과 대응되는 도표를 고른다 → chart_id.
   사고의 종류가 정확히 같아야 한다: 회전 중 진로변경 사고 ≠ 진입부·진출부 사고, 점멸 신호 ≠ 일반 신호, 직진 대 직진 ≠ 직진 대 좌회전.
   도표 중 어느 것도 사용자·상대 진행에 대응되지 않으면 chart_id 는 그대로 두고 user_is 를 null 로 하고 이유를 적는다.
   역할이 맞지 않는데 억지로 A 나 B 를 고르지 않는다 — 방향이 뒤집힌 비율은 틀린 비율보다 나쁘다.
3. 사용자 차량이 그 도표의 A 인지 B 인지 정한다 → user_is. 역할 정의(role_a/role_b)의 진행 방향·신호·차로가 사용자 차량의 확인된 사실과 맞아야 한다.
   방향을 뒤집어 읽지 않도록 orientation_reason 에 "사용자 차량은 …이므로 A(…)" 처럼 근거를 적는다.
4. variants 가 둘 이상이면 어느 변형인지 확인된 사실로만 정한다 (예: 선진입이 VIDEO_CONFIRMED 이면 그 변형). 확인되지 않으면 variant 를 null 로 두고 uncertainties 에 적는다.
5. modifiers 가운데 현재 사건에서 확인된 사실로 뒷받침되는 행만 applied_modifiers 에 넣는다 (id 와 source=video|user, evidence 에 어떤 사실인지).
   - "확인 불가", "불명확", "가능성" 은 적용 근거가 아니다. 그런 행은 rejected_modifiers 에 reason 과 함께 둔다.
   - 현저한 과실·중대한 과실 같은 평가적 항목은 영상에서 그 행위(급제동 없는 과속, 음주, 신호위반 등)가 직접 확인됐을 때만 적용한다.
   - 서로 배타적인 행(현저한 과실 vs 중대한 과실)은 하나만 적용한다.
6. 확신도(confidence, 0~1)와 무엇이 확인되면 결과가 바뀌는지(ratio_dependencies)를 적는다.
   다른 도표가 적용될 여지가 있으면(예: 진출부 사고인지 회전 중 진로변경인지가 불확실) ratio_dependencies 에
   "…으로 확인되면 도표 회전-3(기본 A 70 : B 30)이 적용돼요" 처럼 그 도표와 기본비율을 함께 적고 confidence 를 낮춘다.

중요:
- 도표의 사실을 현재 사건 사실처럼 섞지 않는다. 사용자의 희망 비율은 고려하지 않는다.
- explanation_facts 는 사용자에게 보이는 글이다: 해요체로, 왜 이 도표와 역할이 맞는지 핵심 사실 1~2문장. 숫자 비율은 쓰지 않는다.
- uncertainties, ratio_dependencies 도 사용자에게 보이므로 전문 용어는 풀어 쓴다.
- id 는 CHARTS 에 있는 것만 쓴다. 없는 id 를 만들지 않는다.

출력 JSON:
{
  "chart_id": "",
  "variant": null,
  "user_is": "A | B | null",
  "orientation_reason": "",
  "applied_modifiers": [
    {"id": "m1", "source": "video | user", "evidence": ""}
  ],
  "rejected_modifiers": [
    {"id": "m2", "reason": ""}
  ],
  "confidence": 0.0,
  "uncertainties": [],
  "ratio_dependencies": [],
  "explanation_facts": []
}
