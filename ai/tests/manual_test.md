# 수동 테스트 가이드

## 준비

```bash
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 입력
python main.py
```

## 평가 항목 (spec 29절 기준)

1. Slot Extraction 성공 여부
2. 사고 대상 분류 성공 여부
3. 사고 장소 분류 성공 여부
4. 사고 유형 분류 성공 여부
5. 필요한 조건부 Slot 활성화 여부
6. 불필요한 질문 생성 여부 (이미 채워진 슬롯 재질문 X)
7. 이미 답한 질문 반복 여부
8. 질문 순서의 적절성 (우선순위 높은 슬롯부터)
9. 사용자 답변이 State에 정확히 반영되는지
10. 정상적으로 Intake가 종료되는지

## 시나리오

`tests/test_cases.json`의 각 `initial_input`으로 대화를 시작하고,
`check_points`에 나열된 정보가 모두 채워질 때까지 대화를 이어간다.
매 turn마다 `[DEBUG]` 출력으로 `active_slots` / `missing_slots` /
`accident_type`이 기대와 일치하는지 확인한다.

### Case 1. 후방추돌

```text
빨간불이라 정차했는데 뒤차가 받았습니다.
```

### Case 2. 교차로 직진-좌회전

```text
교차로에서 직진하는데 맞은편 차가 좌회전하다가 받았습니다.
```

### Case 3. 차선 변경

```text
상대차가 갑자기 제 차선으로 들어오면서 부딪혔습니다.
```

### Case 4. 차대보행자

```text
횡단보도에서 사람이랑 사고가 났습니다.
```

### Case 5. 차대이륜차

```text
교차로에서 오토바이와 부딪혔습니다.
```

## 완료 후 확인

- `logs/session_*.json`에 대화 로그와 최종 state가 저장되었는지 확인
- `python -m pytest -q` 자동 회귀 테스트가 통과하는지 확인
- Intake 종료 시 핵심 required slot이 모두 값 또는 "모름"으로 해소되었는지 확인
- `missing_slots`에는 종료에 필수가 아닌 공통 보조 slot이 남을 수 있음

## 블랙박스 영상과 사용자 진술 비교

```bash
python analyze_case.py \
  --video "data/mp4/bb_1_220804_vehicle_116_067.mp4" \
  --description "회전교차로에서 주행 중 옆 차량과 부딪혔습니다."
```

확인 항목:

- `video_analysis`: 영상에서 직접 관찰한 사실과 근거 시각
- `user_statement_state`: 사용자 서술에서 추출한 Intake State
- `comparison.matches`: 양쪽이 일치하는 Slot
- `comparison.differences_requiring_review`: 값이 달라 사람이 확인해야 하는 Slot
- `comparison.user_only`: 사용자만 진술한 정보
- `comparison.vision_only`: 영상에서만 확인된 정보

블랙박스처럼 짧은 충돌 장면은 낮은 프레임 샘플링에서 누락될 수 있으므로
기본 `VIDEO_FPS=5`를 사용한다. 비용이나 영상 길이 때문에 변경할 때는 `.env`에서 조정한다.

### Vision-first Multi-turn 실행

```bash
python vision_intake.py \
  --video "data/mp4/bb_1_220804_vehicle_116_067.mp4"
```

이 실행 경로는 다음 순서로 동작한다.

```text
1차 전체 영상 관찰
→ 사고 유형 가설 및 동적 required factor 계획
→ critical/high factor targeted 영상 재확인
→ 그래도 미해결인 factor만 Multi-turn 질문
→ 영상/사용자 증거 병합
→ Intake 완료 후 사건경위서 생성
→ 유사 심의사례·인정기준 RAG
```

고정 Slot은 하위 호환과 빈 계획에 대한 안전망이고, Vision-first 대화의 질문 및 종료
기준은 `fact_plan.required_factors`다.
실행 결과는 `logs/evidence_session_<timestamp>/` 아래에 다음처럼 분리 저장된다.

```text
vision_analysis.json
user_answers.json
final_facts.json
incident_report.json
rag_results.json
```

확인 항목:

- `vision_analysis.json.analysis_passes`에 `broad_observation`과 필요한 경우
  `targeted_recheck`가 구분되어 있는가
- `user_answers.json.factor_answers`에 질문별 답변이 남는가
- `final_facts.json.fact_plan`에 사고 가설과 사고별 필요 요소가 남는가
- 강한 직접 영상 근거와 사용자 답변이 충돌하면 영상값이 선택되고 사용자 답변도
  삭제되지 않는가
- 추론 또는 저신뢰 영상과 사용자 답변의 충돌은 `disputed_unresolved`로 남는가
- 고정 Slot에 없는 영상 사실도 `additional_observations`와 최종 `facts`에 보존되는가
- `incident_report.json`이 영상 확정 사실, 사용자 진술, 충돌, 미확인을 구분하는가
- `rag_results.json`의 각 결과에 원본 PDF 파일명과 페이지가 있는가
- 여러 페이지인 사례는 `page_start`, `page_end` 범위와 동일한 `parent_id`로 반환되는가
- `matched_sections`에 사고내용·주요쟁점·결정이유 등 실제 매칭 섹션이 기록되는가
- 판례의 A/B·청구/피청구를 블랙박스 차량과 근거 없이 자동 대응하지 않는가
- 검색된 사건번호·도표·비율이 원문 발췌와 일치하는가

### RAG 인덱스 사전 생성

첫 완료 대화에서 인덱스를 만들면 대기시간이 길어질 수 있으므로 시연 전 한 번 실행한다.

```bash
python build_rag_index.py
```

정상적으로 생성되면 `data/rag_index/manifest.json`의 `chunk_count`와
`embedding_model`을 확인한다. PDF 또는 임베딩 모델이 바뀌지 않으면 이후 실행은 기존
인덱스를 재사용한다. PDF 텍스트 추출에는 시스템의 `pdftotext`(poppler-utils)가 필요하다.
