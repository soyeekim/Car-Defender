# VSCode CLI 기반 Vision-first 교통사고 Intake Agent 개발 명세 v3

## 1. 문서 목적

이 문서는 `vscode_cli_intake_agent_development_spec_v2.md`의 텍스트 중심 Intake를
Vision-first 사고 사실 수집 구조로 개정한 명세다.

핵심 목표는 사용자의 진술을 사고 사실로 그대로 채택하는 챗봇이 아니다.
블랙박스 영상에서 객관적으로 확인 가능한 정보를 먼저 수집하고, 영상으로 확인할 수
없거나 불확실한 정보만 Multi-turn 대화로 보완한다.

```text
블랙박스 영상
    ↓
1차 Vision Observer: 넓고 객관적인 사건·타임라인 관찰
    ↓
Accident Hypothesis: 가능한 사고 유형 가설(최대 3개)
    ↓
Fact Planning Agent: 이 사고의 과실 판단에 필요한 요소를 동적으로 계획
    ↓
2차 Targeted Video Recheck: 핵심·고우선 요소를 원본 영상에서 재확인
    ↓
Evidence Coverage 검사
    ├─ 영상으로 충분히 확인됨 → 질문하지 않음
    └─ 비가시/저신뢰/충돌/미확인 → 사용자에게 한 번에 하나씩 질문
    ↓
Evidence Fusion
    ↓
영상 분석 / 사용자 답변 / 최종 정리 사실을 각각 저장
    ↓ (critical/high factor 수집 완료 후에만)
객관적 사건경위서 생성
    ↓
로컬 과실비율 PDF RAG: 유사 심의사례·인정기준 검색 및 근거 제한 재평가
```

v2의 CLI와 기존 Slot은 하위 호환 및 실패 시 안전망으로 유지한다. v3의 Vision-first
경로에서 질문 대상과 완료 여부를 결정하는 주체는 고정 Slot 목록이 아니라
`FactPlan.required_factors`다. 영상 분석은 선택적 보조 기능이 아니라 Intake의 선행
단계다.

---

## 2. 핵심 원칙

### 2.1 Vision-first

- 영상에서 직접 확인된 정보는 사용자에게 다시 질문하지 않는다.
- 영상에서 확인되지 않는 정보만 질문한다.
- 영상 결과가 저신뢰 추론이면 사실로 확정하지 않고 사용자에게 확인한다.
- 사용자가 영상과 다른 말을 하더라도 영상 또는 사용자 값을 조용히 덮어쓰지 않는다.
- 최종 결과에는 값뿐 아니라 출처, 근거, 신뢰도, 판정 상태를 기록한다.

### 2.2 영상 AI도 절대적 진실이 아니다

영상 모델이 값을 반환했다는 이유만으로 확정하지 않는다.
자동 채택 가능한 영상 결과는 다음 조건을 만족해야 한다.

```text
1. observation_type이 direct_visual 또는 sensor_readout
2. confidence가 기준값 이상
3. evidence가 존재
4. timestamp가 존재
5. 해당 시점과 슬롯 의미가 일치
```

`inferred` 결과는 높은 confidence여도 단독으로 사용자 진술을 덮어쓰지 않는다.

### 2.3 Multi-turn의 역할

Multi-turn은 전체 사고 내용을 사용자에게 다시 조사하는 과정이 아니다.

```text
- 영상에서 보이지 않는 정보
- 영상 신뢰도가 낮은 정보
- 영상과 사용자 진술이 충돌한 정보
- 보험사 주장 등 영상에 존재할 수 없는 정보
```

위 항목을 보완하는 수단이다.

### 2.4 증거 보존

다음 세 결과를 반드시 별도 파일로 저장한다.

```text
vision_analysis.json  # 블랙박스 모델의 원본 분석 결과
user_answers.json     # 사용자 발화와 추출된 답변
final_facts.json      # Evidence Fusion 이후 정리된 사실
```

### 2.5 관찰과 판단의 분리

Vision 모델에게 처음부터 과실비율이나 책임 결론을 요구하지 않는다. 1차 분석은 화면에서
관찰되는 행위, 위치, 신호, 궤적, 접촉 시점과 한계를 기록한다. Fact Planning Agent는 그
관찰을 바탕으로 가능한 사고 유형과 과실 판단에 필요한 사실을 계획하지만 과실비율을
확정하지 않는다. 실제 비율 판단은 향후 판례·과실비율 기준 RAG 단계가 담당한다.

### 2.6 JSON 설계 원칙

사고 종류마다 필요한 정보가 다르므로 모든 사고를 하나의 고정된 평면 JSON에 끼워 맞추지
않는다. 다만 기계 검증, 출처 추적, 재현성을 위해 JSON 자체는 유지한다.

```text
공통 계층: 자주 쓰이는 사고 사실 + 증거 메타데이터
동적 계층: 사고별 required_factors
시간 계층: 범용 observed_events 타임라인
원문 계층: summary, evidence, 사용자 발화
```

새로운 사고 요소는 먼저 동적 factor 또는 `additional_observations`로 저장한다. 반복적으로
사용되고 의미가 안정된 항목만 추후 공통 Slot으로 승격한다.

---

## 3. 구현 범위

### 이번 단계에 포함

```text
1. Gemini 1차 블랙박스 영상 분석
2. 영상 Structured JSON 및 범용 사건 타임라인 반환
3. 영상 관찰 유형·신뢰도·근거·시각 기록
4. 사고 유형 가설 생성
5. 사고별 과실 판단 필요 요소(required factors) 동적 계획
6. 핵심·고우선 요소의 targeted 영상 재확인
7. Evidence Coverage에 따라 필요한 요소만 Multi-turn 질문
8. 사용자 발화와 factor 답변 독립 저장
9. 영상과 사용자 진술 비교
10. 공통 Evidence Fusion 정책 적용
11. 최종 사실, 계획, 충돌 및 미해결 정보 독립 저장
12. Intake 완료 후 사건경위서 생성
13. 심의사례·과실비율 기준 PDF 임베딩 인덱스와 출처 기반 유사자료 검색
14. CLI에서 전체 흐름 검증
```

### 이번 단계에 포함하지 않음

```text
- 실제 과실비율 확정
- 법률적 책임 판단
- 분쟁조정 문서 생성
- FastAPI
- Web UI
- 로그인 및 데이터베이스
```

---

## 4. 프로젝트 구조

```text
traffic-accident-agent/
├─ main.py                         # 텍스트 전용 Intake
├─ analyze_case.py                 # 영상/진술 단회 비교
├─ vision_intake.py                # Vision-first Multi-turn CLI
├─ build_rag_index.py              # 로컬 PDF 임베딩 인덱스 사전 생성
├─ agent/
│  ├─ intake_agent.py
│  ├─ vision_first_intake_agent.py
│  ├─ evidence_fusion.py
│  ├─ fact_planner.py
│  ├─ slot_extractor.py
│  ├─ accident_classifier.py
│  ├─ question_generator.py
│  └─ state_manager.py
├─ models/
│  └─ gemini_video.py
├─ schemas/
│  ├─ accident_state.py
│  ├─ slot_schema.py
│  ├─ video_analysis.py
│  ├─ evidence_state.py
│  └─ fact_plan.py
├─ storage/
│  └─ evidence_store.py
├─ rag/
│  ├─ pdf_index.py                 # 사례 Parent/의미 Child 청킹·하이브리드 검색
│  └─ pipeline.py                  # 경위서 생성·검색·근거 제한 재평가
├─ prompts/
│  ├─ analyze_video.txt
│  ├─ plan_factors.txt
│  ├─ generate_factor_question.txt
│  ├─ extract_factor_answer.txt
│  ├─ extract_slots.txt
│  ├─ classify_accident.txt
│  └─ generate_question.txt
└─ logs/
   └─ evidence_session_<timestamp>/
      ├─ vision_analysis.json
      ├─ user_answers.json
      ├─ final_facts.json
      ├─ incident_report.json
      └─ rag_results.json
```

---

## 5. 영상 분석 스키마

각 영상 Slot은 단순 값이 아니라 증거 정보를 포함한다.
이 구조는 급정거뿐 아니라 사고 장소, 신호, 차로, 진행행동, 방향지시등, 진입 순서,
충돌 부위, 충돌 형태, 보행자 행동 등 모든 사고 사실에 동일하게 적용한다.

```json
{
  "value": "회전교차로 외측 차로",
  "confidence": 0.91,
  "evidence": "차로 유도선과 중앙 교통섬을 기준으로 외측 차로 주행",
  "timestamp": "00:03.200",
  "observation_type": "direct_visual",
  "temporal_scope": "pre_collision"
}
```

### observation_type

```text
direct_visual   화면에서 직접 관찰
sensor_readout  속도계, GPS, G-sensor 등 계측값
inferred        움직임이나 정황으로 추론
not_observable  영상으로 확인 불가
```

### temporal_scope

```text
pre_collision   충돌 전
at_collision    충돌 순간
post_collision  충돌 후
general         영상 전반
unknown         시점 불명
```

### 범용 ObservedEvent

고정 Slot만으로 영상의 모든 돌발 상황을 미리 정의할 수 없으므로 영상 모델은 별도의
범용 사건 타임라인을 함께 생성한다.

```json
{
  "event_id": "event_003",
  "description": "상대 차량이 내측 차로에서 외측 차로 방향으로 접근",
  "actors": ["opponent"],
  "start_timestamp": "00:03.200",
  "end_timestamp": "00:04.100",
  "temporal_scope": "pre_collision",
  "observation_type": "direct_visual",
  "confidence": 0.88,
  "evidence": "상대 차량과 외측 유도선 사이 간격이 연속적으로 감소",
  "attributes": {
    "road_structure": "2차로형 회전교차로",
    "movement": "내측에서 외측 방향 접근"
  }
}
```

고정 Slot에 없는 사실은 `additional_observations`에 `fact_key`, `fact_label`, 값과
동일한 증거 메타데이터를 기록한다. 따라서 새로운 사고 정황이 생길 때마다 전용 필드를
추가하지 않아도 원본 관찰과 최종 사실에 보존할 수 있다.

---

## 6. Fact Planning과 Targeted Recheck

### 6.1 사고 가설

1차 영상 분석 결과로 최대 3개의 사고 가설을 만든다. 가설은 확정된 법률 판단이 아니며
`family`, `subtype`, `confidence`, `reason`을 가진다. 불명확한 영상을 하나의 유형으로
억지 확정하지 않는다.

### 6.2 동적 필요 요소

Fact Planning Agent는 가설과 영상 증거를 보고 해당 사고에서 필요한 요소를 최대 12개
계획한다.

```json
{
  "factor_id": "lane_boundary_crossing_vehicle",
  "description": "충돌 직전 차로 경계를 먼저 넘은 차량",
  "category": "spatial_relationship",
  "importance": "critical",
  "reason": "진로 변경 주체와 선행 침범 여부를 판단하기 위해 필요",
  "preferred_source": "vision_then_user",
  "related_fact_keys": ["lane_change_direction", "ego_lane", "opponent_lane"],
  "video_recheck_instruction": "충돌 전 3초간 차로선과 양 차량 바퀴 궤적을 확인",
  "question_hint": "영상으로 안 보이는 경우 어느 차량이 차선을 넘어왔는지 질문",
  "status": "unresolved"
}
```

`importance`가 `critical` 또는 `high`인 요소가 Intake 완료 여부를 결정한다. 요소의
`preferred_source`가 `vision` 또는 `vision_then_user`이고 재확인 지시가 있으면 사용자에게
묻기 전에 같은 원본 영상으로 targeted pass를 수행한다.

### 6.3 Targeted pass 원칙

- 1차 pass는 전체 장면과 예상 밖 사건을 폭넓게 관찰한다.
- 2차 pass는 Fact Planner가 지정한 요소만 시간축과 장면 근거 중심으로 다시 본다.
- 2차 결과가 직접 관찰이고 근거가 더 강할 때만 기존 관찰을 대체한다.
- 두 pass의 타임라인과 추가 관찰은 합쳐 보존한다.
- 재확인 뒤 사고 가설과 Fact Plan을 한 번 갱신한다.
- targeted pass 실패는 영상 원본 분석을 폐기하지 않으며 사용자 질문으로 계속한다.

### 6.4 Factor 상태

```text
confirmed_by_vision       강한 영상 근거로 확인
corroborated              영상과 사용자 진술 일치
user_claimed              사용자 진술만 존재
disputed_vision_preferred 강한 영상과 진술 충돌, 영상 우선
inferred_by_vision        영상 추론뿐이라 추가 확인 필요
not_visible               영상에서 보이지 않음
disputed_unresolved       증거가 약한 충돌
unresolved                아직 미확인
external_required         보험사 문서 등 외부 자료 필요
```

---

## 7. 급정거 정보 분리

급정거는 범용 모델의 유일한 대상이 아니라 시간축 혼동을 설명하는 대표 사례다.
기존 `sudden_braking`은 의미 자체가 시점에 따라 달라 다음처럼 분리한다.

```text
pre_collision_sudden_braking  충돌 전 급제동 여부
post_collision_stop           충돌 충격 또는 사고 후 정지 여부
speed_change_before_collision 충돌 전 속도 변화
braking_evidence              급제동 판단 근거
```

후방추돌 과실 판단용 Slot은 `pre_collision_sudden_braking`이다.
충돌 후 정차를 충돌 전 급정거로 분류해서는 안 된다.
다른 모든 사실도 `temporal_scope`로 충돌 전·순간·후를 구분하며, 새로운 상황은
ObservedEvent로 기록한다. 사고 상황별 전용 우선순위 코드를 계속 추가하지 않는다.

---

## 8. 사용자 답변 저장

사용자의 답변은 영상 State에 직접 덮어쓰지 않는다.

```json
{
  "conversation": [
    {
      "role": "assistant",
      "content": "충돌 전에 급제동하셨나요?",
      "target_factor": "pre_collision_braking_behavior",
      "target_slot": "pre_collision_sudden_braking"
    },
    {
      "role": "user",
      "content": "아니요, 계속 천천히 가고 있었습니다."
    }
  ],
  "factor_answers": {
    "pre_collision_braking_behavior": {
      "factor_id": "pre_collision_braking_behavior",
      "value": "아니오",
      "confidence": 0.98,
      "related_fact_updates": {
        "pre_collision_sudden_braking": "아니오"
      }
    }
  },
  "extracted_answers": {
    "pre_collision_sudden_braking": {
      "value": "아니오",
      "source": "user",
      "confidence": 0.98
    }
  }
}
```

사용자 답변은 `claimed evidence`이며, 영상 사실과 독립적으로 보존한다.

---

## 9. Evidence Fusion 공통 정책

개별 사고 유형마다 충돌 우선순위를 하드코딩하지 않는다.
모든 Slot에 다음 공통 정책을 적용한다.

```text
accident_place
ego/opponent_maneuver
ego/opponent_lane
ego/opponent/pedestrian_signal
turn_signal
collision_area/type/timestamp
speed 및 braking
pedestrian_crossing_state
그 외 additional_observations
```

### 9.1 양쪽 값이 일치

```text
status = corroborated
resolved_value = 일치 값
```

### 9.2 영상만 존재하며 직접 관찰 또는 센서 근거가 충분

```text
status = verified_by_vision
resolved_value = 영상 값
```

### 9.3 사용자만 존재

```text
status = user_claimed
resolved_value = 사용자 값
```

영상에서 원래 관찰하기 어려운 보험사 주장 등의 정보가 이에 해당한다.

### 9.4 영상과 사용자 값이 다르고 영상 근거가 강함

```text
status = disputed_vision_preferred
resolved_value = 영상 값
```

조건:

```text
observation_type ∈ {direct_visual, sensor_readout}
confidence >= VISION_VERIFIED_THRESHOLD
evidence 존재
timestamp 존재
```

사용자 값은 삭제하지 않고 함께 기록한다.

### 9.5 영상과 사용자 값이 다르지만 영상이 추론 또는 저신뢰

```text
status = disputed_unresolved
resolved_value = null
```

추가 질문 또는 사람 검토 대상으로 남긴다.

### 9.6 영상의 추론값만 존재

```text
status = vision_inferred
resolved_value = null
```

Multi-turn 질문 대상으로 유지한다.

---

## 10. 최종 사실 스키마

```json
{
  "pre_collision_sudden_braking": {
    "resolved_value": "예",
    "status": "disputed_vision_preferred",
    "selected_source": "vision",
    "vision": {
      "value": "예",
      "confidence": 0.94,
      "observation_type": "sensor_readout",
      "evidence": "충돌 1.2초 전 속도 급감",
      "timestamp": "00:03.400"
    },
    "user": {
      "value": "아니오",
      "source": "user"
    },
    "resolution_reason": "충돌 전 센서 기반 급감이 직접 확인됨"
  }
}
```

최종 사실은 `verified`, `claimed`, `disputed`, `unresolved` 상태를 구분할 수 있어야 한다.

---

## 11. Vision-first 질문 생성

영상 분석 직후 다음 과정을 수행한다.

```text
1. 1차 영상 관찰과 타임라인 생성
2. 사고 유형 가설 및 동적 required factor 계획
3. critical/high factor 중 영상 재확인 가능한 요소 선택
4. targeted video pass 및 증거 병합
5. 새 증거로 가설과 계획 갱신
6. unresolved critical/high factor 중 가장 중요한 요소 하나 질문
7. 사용자 답변을 factor와 연관된 공통 Slot에 함께 연결
```

다음 정보는 다시 묻지 않는다.

```text
- direct_visual 또는 sensor_readout
- confidence 기준 이상
- evidence가 있는 값
```

다음 정보는 질문할 수 있다.

```text
- null
- not_observable
- inferred
- confidence 기준 미만
- disputed_unresolved
```

사용자에게는 “누가 더 잘못했습니까?”처럼 결론이나 과실비율을 묻지 않는다. 영상 밖의
사실을 묻되, 사용자가 기억하지 못하는 경우 `모름`도 유효한 claimed evidence로 저장한다.
`critical/high` 요소가 영상, 사용자, 또는 외부자료 필요 상태로 모두 해소되면 Intake를
종료한다. `medium/low` 요소는 정보 과수집을 막기 위해 종료를 차단하지 않는다.

---

## 12. 사고 정보 시간축

충돌 관련 Slot은 가능한 경우 다음 시간축을 사용한다.

```text
T-5s ~ T-1s  사고 직전 주행 및 제동
T            최초 접촉
T+1s ~       충돌 후 정지 및 2차 행동
```

모델은 충돌 후 화면 흔들림을 충돌 전 급제동으로 해석해서는 안 된다.

---

## 13. Intake 완료 후 사건경위서와 RAG

RAG는 Multi-turn 도중 실행하지 않는다. `critical/high` factor가 수집 완료 상태가 된
뒤 Evidence Fusion 결과로 객관적인 사건경위서를 먼저 생성하고, 그 경위서의
`retrieval_query`를 검색 질의로 사용한다.

```text
final_facts + observed_events + fact_plan + conversation
    ↓
IncidentReport
    ├─ confirmed_facts
    ├─ user_claims
    ├─ disputed_facts
    ├─ unknown_facts
    ├─ retrieval_factors
    ├─ objective_narrative
    └─ retrieval_query
    ↓
Child 임베딩 + 키워드 하이브리드 검색
    ↓
Child 점수를 사례 Parent로 합산
    ↓
전체 Parent 원문을 이용한 LLM 관련성 재평가
```

검색 원본:

```text
(최종)과실비율심의사례_(54MB).pdf
230630_자동차사고 과실비율 인정기준_최종.pdf
250624_2차로형 회전교차로사고 과실비율 비정형기준.pdf
```

인덱스는 페이지 자체가 아니라 문서의 논리 단위를 Parent로 사용한다. 심의사례는 `심의번호`부터
다음 `심의번호` 직전까지, 인정기준은 `보/거/차` 도표번호부터 다음 도표번호 직전까지,
회전교차로 비정형기준은 `회전-N`부터 다음 `회전-N` 직전까지를 하나의 Parent로 묶는다.
도표가 아닌 총설·일반 해설 페이지만 독립 Parent로 유지한다.

각 Parent는 사고개요, 사고내용, 주장, 입증자료, 주요쟁점, 결정근거, 결정이유, 사고상황,
기본 과실비율, 수정요소, 관련 법규, 참고 판례 등의 의미 섹션을 Child로 나눈다. 임베딩과
키워드 검색은 Child에 수행하고, 같은 Parent에 속한 상위 Child 점수를 합산한다. 최종 관련성
재평가에는 여러 페이지에 걸친 Parent 원문을 전달한다. 이 방식으로 사고내용이 첫 페이지에,
결정근거가 다음 페이지에 있는 사례도 하나의 근거로 취급한다.

`EMBEDDING_MODEL` 임베딩은 최초 한 번 생성해 `data/rag_index/`의 `parents.jsonl`,
`children.jsonl`, `embeddings.npy`에 캐시한다. PDF, 임베딩 모델, 청킹 설정이 바뀌면
fingerprint가 달라져 자동 재생성한다. 검색 결과에는 반드시 원본 파일명, PDF 시작·끝 페이지,
사건번호 또는 도표번호, 매칭 Child 섹션, Parent 원문 발췌, 의미·키워드 유사도와 적용상 차이를
포함한다. 생성 모델은 검색 후보에 없는 `source_id`를 결과로 채택할 수 없다.

PDF 속 사고 그림은 별도 이미지 설명으로 변환하지 않는다. 현재 RAG의 검색 근거는 PDF에
포함된 사고내용·사고상황·주요쟁점·결정근거 텍스트다. 텍스트 설명만으로 사고구조를 식별할 수
없는 자료는 유사성 판단에서 확정 근거로 사용하지 않는다.

사건경위서의 `retrieval_factors`는 사고유형, 도로환경, 당사자 행동, 신호, 공간관계,
충돌형태, 시간순서로 확인 사실을 분류한다. 외부에서 주장한 과실비율은 검색 질의에서 제외해
결과가 원하는 비율 쪽으로 편향되지 않게 한다. 판례의 A/B 또는 청구/피청구는 현재 사고의
블랙박스 차량과 곧바로 대응하지 않고 선진입/후진입·직진/회전·신호 상태 등 행동 역할을
먼저 비교한다.

“유사 판례”라는 UI 표현을 쓰더라도 데이터에서는 `deliberation_case`, `fault_standard`,
`roundabout_special_standard`를 구분한다. 검색된 비율은 유사자료의 값이지 현재 사건의
최종 과실비율이 아니다.

사전 인덱스 생성:

```bash
python build_rag_index.py
```

---

## 14. 저장 구조

### vision_analysis.json

```json
{
  "video_path": "...",
  "video_model": "gemini-3.7-flash",
  "video_fps": 5,
  "analysis": {},
  "analysis_passes": [
    {"pass": "broad_observation", "result": {}},
    {
      "pass": "targeted_recheck",
      "requested_factors": [],
      "result": {}
    }
  ]
}
```

### user_answers.json

```json
{
  "conversation": [],
  "extracted_answers": {},
  "factor_answers": {},
  "pending_factor_id": null,
  "pending_question_slot": null
}
```

### final_facts.json

```json
{
  "facts": {},
  "observed_events": [],
  "accident_type": {},
  "fact_plan": {
    "plan_summary": "",
    "accident_hypotheses": [],
    "required_factors": []
  },
  "active_slots": [],
  "missing_slots": [],
  "intake_complete": false
}
```

### incident_report.json

```json
{
  "incident_report": {
    "title": "",
    "objective_narrative": "",
    "confirmed_facts": [],
    "user_claims": [],
    "disputed_facts": [],
    "unknown_facts": [],
    "retrieval_query": ""
  }
}
```

### rag_results.json

```json
{
  "rag": {
    "retrieval_query": "",
    "embedding_model": "text-embedding-3-small",
    "candidates": [],
    "similar_cases": []
  }
}
```

각 Turn이 끝날 때 세션 디렉터리의 파일을 갱신한다.
파일 저장은 임시 파일 작성 후 원자적 교체 방식으로 수행한다.

---

## 15. CLI 목표

```bash
python vision_intake.py \
  --video "data/mp4/bb_1_220804_vehicle_116_067.mp4"
```

예상 흐름:

```text
Agent:
블랙박스 분석을 시작합니다.

[VISION]
회전교차로, 차대차, 측면 접촉 확인

Agent:
과실 판단의 핵심 요소를 원본 영상에서 다시 확인합니다.

Agent:
영상으로 차로 경계가 가려져 확인되지 않습니다. 충돌 직전 어느 차량이 먼저 차로 경계를 넘었나요?

User:
보지 못했습니다.

Agent:
추가로 확인할 핵심 정보가 충분히 수집되었습니다.

Agent:
수집된 증거로 사건경위서를 생성하고 유사 심의사례·인정기준을 검색합니다.
```

---

## 16. 환경변수

```text
GEMINI_API_KEY=...
OPENAI_API_KEY=...
VIDEO_MODEL=gemini-3.7-flash
INTAKE_MODEL=<GPT model ID>
DOCUMENT_MODEL=<GPT model ID>
REASONING_MODEL=<GPT model ID>
EMBEDDING_MODEL=text-embedding-3-small
VIDEO_FPS=5
VIDEO_PROCESSING_TIMEOUT=300
VIDEO_ANALYSIS_RETRIES=3
VISION_VERIFIED_THRESHOLD=0.8
```

모델명과 임계값은 코드에 하드코딩하지 않는다.

---

## 17. 테스트 기준

```text
1. 영상 직접 관찰값이 질문 State에 먼저 반영되는가
2. 사고 가설별 required factor가 동적으로 생성되는가
3. 핵심 factor가 사용자 질문 전에 targeted pass로 재확인되는가
4. 영상에서 확인된 factor를 다시 질문하지 않는가
5. 영상에 없거나 저신뢰인 factor만 질문하는가
6. 사용자 답변이 영상 원본을 덮어쓰지 않는가
7. 직접 영상 근거와 사용자 답변 충돌 시 영상값을 선택하는가
8. 추론 영상값과 사용자 답변 충돌 시 미해결로 남기는가
9. 충돌 전 급제동과 충돌 후 정지가 분리되는가
10. 고정 Slot 밖의 관찰과 factor도 보존되는가
11. 세 JSON 파일이 독립적으로 저장되는가
12. broad/targeted 분석 pass가 구분되어 저장되는가
13. 각 파일에 source/confidence/evidence/timestamp가 유지되는가
14. critical/high factor가 해소되면 Intake가 정상 종료되는가
15. Intake 완료 전에는 사건경위서와 RAG가 실행되지 않는가
16. 사건경위서가 confirmed/user/disputed/unknown 증거를 분리하는가
17. PDF 시작·끝 페이지·사건번호·도표번호가 검색 결과까지 유지되는가
18. 여러 페이지의 동일 심의번호가 하나의 Parent로 묶이고 다음 심의번호에서 분리되는가
19. Child 검색 결과가 Parent로 합산되고 결정근거까지 재평가 입력에 포함되는가
20. 검색 후보에 없는 source_id가 최종 유사사례에서 배제되는가
21. 심의사례와 인정기준이 함께 비교 후보에 포함되는가
22. 임베딩 인덱스가 fingerprint 기반으로 재사용되는가
```

---

## 18. 현재 단계의 완료 정의

다음이 모두 동작하면 Vision-first Intake MVP가 완료된 것으로 본다.

```text
- 블랙박스 영상 Structured JSON 분석
- 범용 사건 타임라인과 고정 Slot 밖 관찰 보존
- 사고 가설 및 동적 Fact Plan 생성
- 핵심 요소 targeted 영상 재확인
- 고신뢰 직접 관찰값으로 Evidence State 초기화
- 미해결 critical/high factor만 Multi-turn 질문
- 사용자 답변 독립 보존
- 공통 Evidence Fusion 수행
- 최종 사실 및 충돌 상태 생성
- vision_analysis.json / user_answers.json / final_facts.json 분리 저장
- Intake 완료 후 객관적 incident_report.json 생성
- 출처와 PDF 페이지를 보존한 유사 심의사례·인정기준 RAG
- incident_report.json / rag_results.json 분리 저장
```

RAG 결과를 이용한 현재 사건의 과실비율 산정과 피해·가해 역할 평가는 다음 단계다.
