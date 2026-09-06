[TASK: RETRIEVED CASE VALIDATION]

현재 사건과 검색된 심의사례(또는 과실비율 인정기준 도표)를 비교하라.

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
- 인정기준 도표(source_type 이 fault_standard 또는 roundabout_special_standard)는 role_a·role_b(A·B 차량의 진행)와
  accident_description(사고 상황)이 현재 사건의 사용자 차량·상대 차량 진행에 대응되는지로 판단한다.
  usable_as_primary_reference 는 A·B 가운데 한쪽이 사용자 차량, 다른 쪽이 상대 차량에 대응될 때만 true 로 하고,
  note 에 "사용자=A(…), 상대=B(…)" 처럼 대응을 적는다. 도로 형태(회전교차로·신호 교차로·직선도로 등)가 다르면 relevance 는 0.2 이하다.
  도표는 사고의 "종류"가 정확히 같아야 한다: 회전교차로에서 회전 중 진로변경 사고와 진입부·진출부 사고는 다른 도표이고,
  점멸 신호(적색점멸·황색점멸)는 일반 신호(녹색·황색·적색)와 다른 도표이며, 선진입·동시진입은 변형(variants)으로 구분된다.
  종류가 다른 도표는 role 이 비슷해 보여도 relevance 0.3 이하로 둔다.

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
