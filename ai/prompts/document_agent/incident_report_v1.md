[TASK: INCIDENT_REPORT]

다음 검증된 사건 정보를 이용하여 사건경위서 초안을 작성하라.

<VERIFIED_CASE_STATE>
{{verified_case_state}}
</VERIFIED_CASE_STATE>

작성 순서(sections 키 순서와 동일):
1. date_time — 사고 일시
2. location — 사고 장소
3. vehicles — 차량 및 영상 기준 (어느 차량이 사용자 차량인지, 영상 출처)
4. pre_collision — 사고 직전 진행 상황
5. collision_process — 사고 발생 과정
6. collision — 충돌 내용 (충돌 형태, 부위)
7. post_collision — 사고 직후 상황
8. objective_evidence — 영상에서 확인되는 핵심 객관적 사실
9. notes — 참고사항 (확인되지 않은 사항, 사용자 진술로만 기록된 사항)

작성 규칙:
- 시간 순서대로 작성
- 1인칭("본 차량", "본인") 또는 제출 문서에 적합한 중립 문체 사용
- 확인되지 않은 사실은 넣지 않음. 값이 UNKNOWN인 항목은 "확인되지 않음"으로 표기하거나 생략
- 상대방의 고의 또는 의도를 추정하지 않음
- 사용자 감정 표현 삭제
- 영상에서 명확하지 않은 속도 수치를 임의 생성하지 않음
- 사용자가 말한 표현을 그대로 복사하지 않고 객관적 사실 중심으로 재작성
- 영상 확인 사실과 사용자 진술을 구분 ("영상에서 확인된다", "당사자 진술에 따르면")
- 심의사례나 과실비율은 사건경위서에 포함하지 않음 (사실 기술 문서)

출력 JSON:
{
  "title": "교통사고 사건경위서",
  "sections": {
    "date_time": "",
    "location": "",
    "vehicles": "",
    "pre_collision": "",
    "collision_process": "",
    "collision": "",
    "post_collision": "",
    "objective_evidence": "",
    "notes": ""
  },
  "text": "섹션을 순서대로 이어 붙인 전체 문서 본문 (섹션 제목 포함)"
}
