[TASK: FOCUS RE-ANALYSIS]

기존 전체 영상 분석 결과를 참고하되, 이번 분석에서는 아래 쟁점을 집중적으로 재검토하라.

<FOCUS>
{{focus_question}}
</FOCUS>

집중 검토 시간 구간: {{time_window}}

기존 분석 결과(요약):
<PREVIOUS_RESULT>
{{previous_result}}
</PREVIOUS_RESULT>

규칙:
- 기존 vehicle ID를 변경하지 않는다. 새로운 차량이 실제로 확인되는 경우에만 새 ID를 추가하고 changes_from_previous에 명시한다.
- 가능하면 해당 장면의 시간 구간을 특정하고, 프레임 전후의 변화를 함께 검토한다.
- 확인할 수 없다면 UNKNOWN으로 유지하고 unknown_or_unobservable에 이유(화각 밖/화질/가려짐/영상 시작 전)를 적는다. 억지로 확정하지 않는다.
- 출력은 전체 schema를 채운다. 비워서 보내면 기존 결과가 사라진 것으로 오해될 수 있으므로:
  * vehicles: 기존 차량을 모두 포함하고, 이번에 확인한 필드(turn_signal, braking, lane_change, entered_intersection_first 등)를 갱신한다.
  * timeline: 기존 이벤트를 모두 유지하고, 이번에 새로 확인한 이벤트(특히 충돌 직전 구간의 0.5초 단위 위치·거리·감속·조향)를 추가한다. 삭제하지 않는다.
  * confirmed_facts / inferred_facts: 기존 항목 + 이번에 새로 확인한 항목.
  * detailed_description: 이번 재검토로 확인한 내용을 시간 순으로 상세히 서술한다 (사건경위서의 재료가 된다).
  * changes_from_previous: 이번 분석으로 새로 확정·수정·추가된 내용을 항목별로 반드시 나열한다. 변경이 없으면 "변경 없음: <이유>"라고 적는다.
- FOCUS에 지정 필드가 적혀 있으면 그 필드에 결과를 기록한다.
