[TASK: REBUTTAL_OPINION]

다음 사건 정보와 유사 심의사례를 이용하여 과실비율 반박의견서(검토 문서)를 작성하라.
이 문서는 사용자가 **본인이 가입한 보험사** 담당자에게 보내는 것이다. 사용자는 계약자로서
"상대 측이 제시한 과실비율에 동의하지 않으며, 본인은 나 A : 상대 B를 주장한다. 근거는 다음과 같다"는 입장을 전달한다.
(메일 본문은 시스템이 이 섹션들과 사건 데이터로 조립하므로, 여기서는 섹션만 충실히 쓴다.)

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
(상대 보험사 또는 협의 과정에서 제시된 과실비율. 사용자가 입력한 것만 있다.)

<INCIDENT_REPORT>
{{report_sections}}
</INCIDENT_REPORT>
(이미 작성된 사건경위서 본문. 있으면 사실관계 서술을 이와 일치시킨다. 없으면 "(없음)")

{{revision_block}}

문서 구조(sections 키 순서와 동일):
1. overview — 사건 개요 (일시·장소·양 차량의 진행 상황을 2~3문장)
2. opponent_claim — 제시된 과실비율 요약 (OPPONENT_CLAIM에 있는 것만)
3. objective_facts — 영상에서 확인되는 객관적 사실 (확정된 것만, 항목마다 한 문장)
4. key_issues — 본 사건의 핵심 과실 판단 쟁점
5. similar_cases — 유사 심의사례 요약. 사례마다 "심의번호 · 사고 구조 · 기본비율/결정비율 · 심의 이유"를 3~4줄로 정리한다.
   사례 번호는 SIMILAR_CASES에 있는 것만 쓰고, 각 사례가 본 사건에 어떻게 적용되는지 한 문장 덧붙인다.
6. commonalities — 현재 사건과 유사사례의 공통점
7. differences — 현재 사건과 유사사례의 차이점 (불리한 차이점 포함. 이 섹션은 내부 검토용이다)
8. basic_ratio_review — 기본 과실비율 검토
9. adjustment_factor_review — 수정요소 검토. 확인된 사실로 적용 가능한 것과, 확인되지 않아 적용하지 않은 것을 구분한다.
10. final_opinion — 최종 의견. "본인은 나 A : 상대 B를 주장하며, 그 근거는 ~이다"의 구조로 쓴다.
    받는 사람이 확인할 수 없는 사항을 확인해 달라고 요청하지 않는다 ("상대 차량 신호가 불명확하므로 추가 확인이 필요합니다" 금지).
    확인되지 않은 사실은 근거로 쓰지 않을 뿐, 이 섹션에 나열하지 않는다.

규칙:
- 제시된 과실비율이 입력되지 않았다면(OPPONENT_CLAIM이 비어 있음) 만들어내지 않는다.
  opponent_claim 섹션에 "상대방 주장은 현재 제공되지 않았습니다. 추후 확인 시 보완이 필요합니다."라고만 쓴다.
- 유사사례의 결론만 가져오지 말고 현재 사건에 적용 가능한 이유를 설명한다.
- 법적 확정판결처럼 표현하지 않는다. 과실비율은 예상임을 명시한다.
- 논리적이고 제출 문서에 적합한 문체를 사용한다. 공격적 표현 금지. 상대방의 의도를 추정하지 않는다.
- 확인되지 않은 법 위반을 단정하지 않는다. 영상 사실과 사용자 진술을 구분한다.
- 전체 sections 합계는 3,500자 이내.

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
