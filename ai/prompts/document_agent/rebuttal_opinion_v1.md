[TASK: REBUTTAL_OPINION]

다음 사건 정보와 유사 심의사례를 이용하여 과실비율에 대한 반박의견서 초안을 작성하라.

<VERIFIED_CASE_STATE>
{{verified_case_state}}
</VERIFIED_CASE_STATE>

<FAULT_ASSESSMENT>
{{fault_assessment}}
</FAULT_ASSESSMENT>

<SIMILAR_CASES>
{{similar_cases}}
</SIMILAR_CASES>

<OPPONENT_CLAIM>
{{opponent_claim}}
</OPPONENT_CLAIM>

문서 구조(sections 키 순서와 동일):
1. overview — 사건 개요
2. opponent_claim — 상대방 주장 요약
3. objective_facts — 영상에서 확인되는 객관적 사실
4. key_issues — 본 사건의 핵심 과실 판단 쟁점
5. similar_cases — 유사 심의사례 (심의번호, 사고 구조, 기본비율·결정비율)
6. commonalities — 현재 사건과 유사사례의 공통점
7. differences — 현재 사건과 유사사례의 차이점 (불리한 차이점 포함)
8. basic_ratio_review — 기본 과실비율 검토
9. adjustment_factor_review — 수정요소 검토 (확인된 사실로 적용 가능한 것과 확인 불가한 것 구분)
10. final_opinion — 최종 의견 (예상 과실비율과 근거, 확정이 아님을 명시)

규칙:
- 상대방 주장이 입력되지 않았다면(OPPONENT_CLAIM이 비어 있음) 만들어내지 않는다.
  opponent_claim 섹션에 "상대방 주장은 현재 제공되지 않았습니다. 추후 확인 시 보완이 필요합니다."라고만 쓴다.
- 유사사례의 결론만 가져오지 말고 현재 사건에 적용 가능한 이유를 설명한다.
- 불리한 차이점이 있어도 숨기지 않는다.
- 법적 확정판결처럼 표현하지 않는다.
- 논리적이고 제출 문서에 적합한 문체를 사용한다. 공격적 표현 금지.
- 상대방의 의도를 추정하지 않는다.
- 확인되지 않은 법 위반을 단정하지 않는다.
- 영상 사실과 사용자 진술을 구분한다.
- 유사 심의사례 번호는 SIMILAR_CASES에 있는 것만 인용한다.

출력 JSON:
{
  "title": "과실비율 반박의견서",
  "sections": {
    "overview": "",
    "opponent_claim": "",
    "objective_facts": "",
    "key_issues": "",
    "similar_cases": "",
    "commonalities": "",
    "differences": "",
    "basic_ratio_review": "",
    "adjustment_factor_review": "",
    "final_opinion": ""
  },
  "cited_case_ids": [],
  "text": "섹션을 순서대로 이어 붙인 전체 문서 본문 (섹션 제목 포함)"
}
