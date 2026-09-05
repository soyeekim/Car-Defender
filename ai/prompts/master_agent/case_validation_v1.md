[TASK: RETRIEVED CASE VALIDATION]

현재 사건과 검색된 심의사례를 비교하라.

<CASE_STATE>
{{case_state}}
</CASE_STATE>

<RETRIEVED_CASES>
{{retrieved_cases}}
</RETRIEVED_CASES>

각 사례에 대해 다음을 평가한다.
- 사고 장소 유형 일치 여부
- 차량 진행 방향 일치 여부
- 신호 조건 일치 여부
- 선진입 관계 일치 여부
- 충돌 관계(진입 방향, 충돌 부위) 일치 여부
- 기본과실 적용 가능성
- 수정요소 적용 가능성

규칙:
- 단순한 텍스트 유사도로 판단하지 않는다. 사고 구조 요소별로 비교한다.
- 심의사례의 사실을 현재 사건 사실로 혼동하지 않는다.
- 제공된 case_id만 사용한다. 목록에 없는 사례를 만들어내지 않는다.
- 현재 사건에서 UNKNOWN인 요소는 "일치"로 처리하지 않고 different_factors 또는 note에 "확인 불가"로 적는다.
- usable_as_primary_reference는 사고 장소·진행 방향·충돌 관계가 모두 일치할 때만 true.
- relevance는 0.0~1.0.

출력 JSON:
{
  "validations": [
    {
      "case_id": "2018-070162",
      "relevance": 0.0,
      "matched_factors": [],
      "different_factors": [],
      "usable_as_primary_reference": true,
      "basic_ratio_applicable": true,
      "adjustment_factor_notes": [],
      "note": ""
    }
  ]
}
