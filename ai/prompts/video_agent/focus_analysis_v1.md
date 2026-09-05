[TASK: FOCUS RE-ANALYSIS]

기존 전체 영상 분석 결과를 참고하되, 이번 분석에서는 아래 쟁점만 집중적으로 재검토하라.

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
- 기존 분석 결과와 다른 결론이 나오면 changes_from_previous에 무엇이 변경되었는지 명시한다.
- 확인할 수 없다면 UNKNOWN으로 유지한다. 억지로 확정하지 않는다.
- 출력은 전체 schema를 채우되, focus 쟁점과 무관한 항목은 기존 결과를 그대로 유지한다.
