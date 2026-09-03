# VSCode CLI 기반 교통사고 Intake Agent 개발 명세

## 1. 목적

본 문서는 교통사고 과실비율 분쟁 대응 AI Agent를 웹서비스로 구현하기 전에,
VSCode 터미널 환경에서 핵심 Agent 대화 기능을 먼저 검증하기 위한 개발 명세이다.

초기 개발 목표는 웹 UI 없이 Python CLI 환경에서 다음 흐름을 구현하는 것이다.

```text
사용자 자유 서술 입력
        ↓
LLM이 사고 정보 추출
        ↓
Agent State 업데이트
        ↓
사고 대상 / 사고 장소 / 사고 유형 추정
        ↓
사고 유형별 필요한 Slot 활성화
        ↓
Missing Slot 확인
        ↓
다음 질문 대상 결정
        ↓
LLM이 자연어 질문 생성
        ↓
사용자 답변 입력
        ↓
State 업데이트
        ↓
필요 정보가 충분할 때까지 반복
```

이 단계의 목적은 웹 화면을 만드는 것이 아니라,
Agent가 실제로 사용자와 티키타카하면서 사고 정보를 수집할 수 있는지 검증하는 것이다.

---

## 2. 1차 구현 범위

최초 버전에서는 다음 기능만 구현한다.

```text
1. 사용자 자유 서술 입력
2. 사용자 입력에서 사고 관련 Slot 추출
3. 사고 대상 분류
4. 사고 장소 분류
5. 사고 유형 후보 추정
6. 공통 Slot 활성화
7. 사고 유형별 조건부 Slot 활성화
8. Missing Slot 계산
9. 다음 질문 대상 Slot 선택
10. Agent 자연어 질문 생성
11. 사용자 답변을 반영하여 State 업데이트
12. 충분한 정보가 모이면 Intake 종료
```

초기 단계에서 다음 기능은 구현하지 않아도 된다.

```text
- 웹 UI
- React / Next.js
- FastAPI 서버
- 회원가입 / 로그인
- 보험사 이메일 전송
- PDF/HWP 문서 자동 생성
- 실제 RAG 검색
- 실제 블랙박스 Vision 분석
```

단, 이후 Vision과 RAG를 쉽게 붙일 수 있도록 모듈 구조는 분리한다.

---

## 3. 권장 개발 환경

```text
Python 3.11+
VSCode
venv 또는 conda
OpenAI API 또는 Gemini API
Pydantic
python-dotenv
```

예시 설치:

```bash
pip install openai pydantic python-dotenv
```

Gemini를 사용할 경우:

```bash
pip install google-genai pydantic python-dotenv
```

---

## 4. 프로젝트 디렉터리 구조

```text
traffic-accident-agent/
│
├─ main.py
├─ .env
├─ requirements.txt
│
├─ agent/
│  ├─ __init__.py
│  ├─ intake_agent.py
│  ├─ slot_extractor.py
│  ├─ question_generator.py
│  ├─ accident_classifier.py
│  └─ state_manager.py
│
├─ schemas/
│  ├─ __init__.py
│  ├─ accident_state.py
│  └─ slot_schema.py
│
├─ config/
│  ├─ accident_taxonomy.json
│  └─ slot_rules.json
│
├─ prompts/
│  ├─ extract_slots.txt
│  ├─ classify_accident.txt
│  └─ generate_question.txt
│
├─ tests/
│  ├─ test_cases.json
│  └─ manual_test.md
│
└─ data/
   └─ placeholder/
```

---

## 5. 핵심 모듈 역할

### `main.py`

CLI 실행 진입점.

역할:

```text
- 프로그램 시작
- Agent State 초기화
- 사용자 입력 받기
- Intake Agent 호출
- Agent 질문 출력
- Intake 완료 여부 판단
- 최종 State 출력
```

### `agent/intake_agent.py`

전체 Intake 흐름을 조정하는 Orchestrator.

```text
1. 사용자 입력 수신
2. Slot Extractor 호출
3. State 업데이트
4. Accident Classifier 호출
5. 조건부 Slot 활성화
6. Missing Slot 계산
7. 다음 질문 필요 여부 판단
8. Question Generator 호출
9. 결과 반환
```

### `agent/slot_extractor.py`

사용자의 자유 서술에서 사고 정보를 구조화하여 추출한다.

입력:

```text
교차로에서 직진하고 있었는데 오른쪽에서 차가 와서 박았어요.
저는 녹색 신호였습니다.
```

출력:

```json
{
  "accident_target": "차대차",
  "accident_place": "사거리교차로",
  "ego_maneuver": "직진",
  "opponent_maneuver": "우측 진입",
  "ego_signal": "녹색",
  "opponent_signal": null
}
```

LLM Structured Output 또는 JSON Schema를 사용한다.

### `agent/accident_classifier.py`

현재 State를 기반으로 사고 유형을 추정한다.

```json
{
  "family": "intersection_collision",
  "subtype": "straight_vs_entry",
  "status": "partially_confirmed",
  "candidates": [
    "intersection_collision",
    "road_entry"
  ]
}
```

사고유형이 불확실한 경우 하나로 강제 확정하지 않는다.

### `agent/question_generator.py`

현재 State와 Missing Slot을 보고 사용자에게 물어볼 다음 질문을 생성한다.

핵심 원칙:

```text
질문 리스트 전체를 하드코딩하지 않는다.
```

개발자가 정의하는 것:

```text
- 어떤 Slot이 필요한가
- 사고 유형별로 어떤 Slot이 활성화되는가
- 어떤 Slot이 더 중요한가
```

Agent가 생성하는 것:

```text
- 실제 사용자에게 보여줄 자연어 질문 문장
```

### `agent/state_manager.py`

대화 중 누적되는 사고 State를 관리한다.

```text
- 기존 State와 새 추출 결과 Merge
- None 값 처리
- source 기록
- confidence 기록
- 기존 값과 충돌하는 값 관리
- Missing Slot 계산
```

---

## 6. 기본 State Schema

예시:

```python
from pydantic import BaseModel
from typing import Optional, List

class SlotValue(BaseModel):
    value: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None

class AccidentType(BaseModel):
    family: Optional[str] = None
    subtype: Optional[str] = None
    status: str = "uncertain"
    candidates: List[str] = []

class AccidentState(BaseModel):
    accident_target: SlotValue = SlotValue()
    accident_place: SlotValue = SlotValue()
    place_signal_presence: SlotValue = SlotValue()

    ego_maneuver: SlotValue = SlotValue()
    opponent_maneuver: SlotValue = SlotValue()

    ego_lane: SlotValue = SlotValue()
    opponent_lane: SlotValue = SlotValue()

    ego_signal: SlotValue = SlotValue()
    opponent_signal: SlotValue = SlotValue()

    ego_speed: SlotValue = SlotValue()
    opponent_speed: SlotValue = SlotValue()

    ego_collision_area: SlotValue = SlotValue()
    opponent_collision_area: SlotValue = SlotValue()

    sudden_braking: SlotValue = SlotValue()
    braking_reason: SlotValue = SlotValue()
    turn_signal: SlotValue = SlotValue()

    opponent_claimed_fault_ratio: SlotValue = SlotValue()

    accident_type: AccidentType = AccidentType()

    active_slots: List[str] = []
    missing_slots: List[str] = []
```

---

## 7. 사고 대상 Taxonomy

```json
[
  "차대차",
  "차대보행자",
  "차대자전거",
  "차대이륜차"
]
```

예:

```text
오토바이랑 부딪혔어요 → 차대이륜차
사람을 쳤어요 → 차대보행자
자전거가 갑자기 나왔어요 → 차대자전거
```

---

## 8. 사고 장소 Taxonomy

예시 `config/accident_taxonomy.json`:

```json
{
  "차대차": [
    "직선 도로",
    "사거리교차로(신호등 없음)",
    "사거리교차로(신호등 있음)",
    "고속도로/자동차전용도로",
    "T자형 교차로",
    "차도와 차도가 아닌 장소",
    "주차장 또는 차도가 아닌 장소",
    "회전교차로"
  ],
  "차대보행자": [
    "횡단보도(신호등 없음)",
    "횡단보도(신호등 있음)",
    "횡단보도(신호등 없음) 부근",
    "횡단보도(신호등 있음) 부근",
    "횡단보도 없음",
    "육교 및 지하도 부근"
  ],
  "차대자전거": [
    "사거리교차로(신호등 있음)",
    "사거리교차로(신호등 없음)",
    "자전거도로",
    "직선 도로"
  ],
  "차대이륜차": [
    "직선 도로",
    "사거리교차로(신호등 없음)",
    "사거리교차로(신호등 있음)",
    "회전교차로",
    "T자형 교차로",
    "차도와 차도가 아닌 장소"
  ]
}
```

---

## 9. 공통 Slot

```json
[
  "accident_target",
  "accident_place",
  "ego_maneuver",
  "opponent_maneuver",
  "ego_collision_area",
  "opponent_claimed_fault_ratio"
]
```

모든 사고에서 반드시 전부 질문해야 한다는 의미는 아니다.
사용자의 초기 서술 또는 이후 Vision 분석으로 확보된 값은 다시 질문하지 않는다.

---

## 10. 사고유형별 조건부 Slot

예시 `config/slot_rules.json`:

```json
{
  "rear_end": {
    "required_slots": [
      "ego_maneuver",
      "opponent_maneuver",
      "sudden_braking",
      "braking_reason",
      "ego_collision_area"
    ]
  },
  "intersection_collision": {
    "required_slots": [
      "ego_maneuver",
      "opponent_maneuver",
      "ego_signal",
      "opponent_signal",
      "ego_collision_area"
    ]
  },
  "lane_change": {
    "required_slots": [
      "ego_maneuver",
      "opponent_maneuver",
      "turn_signal",
      "ego_lane",
      "opponent_lane",
      "ego_collision_area"
    ]
  },
  "vehicle_pedestrian": {
    "required_slots": [
      "accident_place",
      "ego_signal",
      "pedestrian_signal",
      "ego_speed",
      "ego_collision_area"
    ]
  }
}
```

---

## 11. 동적 Slot 활성화

예:

```text
사용자:
교차로에서 좌회전하다가 맞은편에서 오던 차와 부딪혔어요.
```

초기 추출:

```json
{
  "accident_target": "차대차",
  "accident_place": "사거리교차로",
  "ego_maneuver": "좌회전",
  "opponent_maneuver": "직진"
}
```

사고 유형:

```json
{
  "family": "intersection_collision",
  "subtype": "left_turn_vs_straight"
}
```

활성 Slot 예:

```text
- ego_signal
- opponent_signal
- protected_left_turn
- unprotected_left_turn
- intersection_entry_order
- ego_collision_area
```

---

## 12. 사고유형이 불확실할 때

사용자:

```text
옆차가 와서 박았어요.
```

Agent State:

```json
{
  "accident_type": {
    "family": null,
    "status": "uncertain",
    "candidates": [
      "lane_change",
      "intersection_collision",
      "road_entry"
    ]
  }
}
```

이 경우 세부 과실 질문보다 사고 유형을 좁힐 질문을 먼저 생성한다.

예:

```text
사고가 교차로 안에서 발생했나요,
아니면 같은 방향으로 주행 중 상대 차량이 차선을 변경하면서 발생했나요?
```

---

## 13. LLM 역할 분리

초기 구현은 최소 3가지 역할로 분리한다.

```text
1. Slot Extraction
2. Accident Classification
3. Question Generation
```

한 번의 LLM 호출에 모든 판단과 질문 생성을 몰아넣지 않는다.

---

## 14. Slot Extraction Prompt 역할

입력:

```text
현재 State
+ 사용자 최신 메시지
```

목표:

```text
사용자 최신 메시지에서 명시적으로 확인 가능한 사고 정보만 추출한다.
```

규칙:

```text
- 사용자가 말하지 않은 내용을 추측하지 않는다.
- 불확실하면 null로 반환한다.
- 기존 State를 참고한다.
- 새로운 정보가 있으면 갱신한다.
- 표준 Taxonomy 값으로 정규화한다.
```

출력 예:

```json
{
  "accident_target": "차대차",
  "accident_place": "사거리교차로(신호등 있음)",
  "ego_maneuver": "직진",
  "opponent_maneuver": null,
  "ego_signal": "녹색",
  "opponent_signal": null
}
```

---

## 15. Accident Classification Prompt 역할

입력:

```text
현재까지 수집된 State
```

출력:

```json
{
  "family": "intersection_collision",
  "subtype": "unknown",
  "status": "partially_confirmed",
  "candidates": [
    "intersection_collision"
  ]
}
```

규칙:

```text
- 정보가 부족하면 확정하지 않는다.
- 후보를 최대 3개까지 반환 가능하다.
- 사고 대상과 사고 장소를 우선 활용한다.
- 자차/상대방 진행행동을 함께 사용한다.
```

---

## 16. Question Generation Prompt 역할

입력:

```text
- 현재 사고 State
- 사고 유형
- 활성화된 Slot
- Missing Slot
- 기존 대화 내용
```

목표:

```text
현재 과실 판단에 필요한 가장 중요한 정보 1개를 선택하고
사용자가 이해하기 쉬운 자연어 질문으로 생성한다.
```

규칙:

```text
- 이미 확보한 정보는 다시 묻지 않는다.
- 한 번에 1개, 최대 2개까지만 질문한다.
- 법률 용어를 과도하게 사용하지 않는다.
- 사고유형이 불확실하면 사고 분류 질문을 우선한다.
- 사용자가 모를 수 있는 정보는 확인 가능 여부까지 고려한다.
```

예:

```text
당시 자차가 보고 있던 차량용 신호등은 어떤 색이었나요?
```

---

## 17. 질문 우선순위

초기 MVP에서는 Slot마다 우선순위를 둘 수 있다.

```json
{
  "ego_signal": 10,
  "opponent_signal": 10,
  "ego_maneuver": 9,
  "opponent_maneuver": 9,
  "accident_place": 8,
  "sudden_braking": 8,
  "braking_reason": 8,
  "turn_signal": 7,
  "ego_collision_area": 7,
  "ego_speed": 5
}
```

단, 단순 숫자 우선순위만으로 질문을 정하지 않고 현재 사고유형을 함께 고려한다.

---

## 18. State 업데이트 규칙

기존 State:

```json
{
  "ego_signal": null
}
```

사용자:

```text
저는 녹색이었어요.
```

업데이트:

```json
{
  "ego_signal": {
    "value": "녹색",
    "source": "user",
    "confidence": 0.98
  }
}
```

---

## 19. 서로 충돌하는 정보

예:

```text
기존 진술: 녹색
후속 진술: 생각해보니 황색이었던 것 같아요.
```

즉시 조용히 덮어쓰기보다 conflict를 남길 수 있도록 한다.

```json
{
  "ego_signal": {
    "value": "황색",
    "source": "user",
    "confidence": 0.6,
    "conflict": true,
    "previous_value": "녹색"
  }
}
```

초기 MVP에서 복잡하면 로그만 남겨도 된다.

---

## 20. Intake 종료 조건

모든 Slot이 채워질 때까지 질문하지 않는다.

다음 조건 중 하나를 만족하면 Intake 종료 가능:

```text
1. 현재 사고유형을 판단할 수 있음
2. 해당 사고유형의 핵심 required_slot이 충분히 채워짐
3. 남은 정보가 Vision 분석으로 확인 가능한 정보임
4. 사용자가 해당 정보를 모른다고 명시함
```

예:

```json
{
  "intake_complete": true,
  "reason": "required_slots_sufficient"
}
```

---

## 21. CLI 실행 예시

```text
$ python main.py

========================================
교통사고 과실비율 분쟁 대응 Agent
========================================

Agent:
사고 상황을 자유롭게 설명해주세요.

User:
교차로에서 직진 중인데 오른쪽에서 차가 와서 박았어요.
상대 보험사는 7:3이라고 합니다.

[DEBUG]
accident_target = 차대차
accident_place = 사거리교차로
ego_maneuver = 직진
opponent_maneuver = 우측 진입
opponent_claimed_fault_ratio = 7:3

Agent:
사고 당시 자차가 보고 있던 차량용 신호등은 어떤 색이었나요?

User:
녹색이었어요.

[DEBUG]
ego_signal = 녹색

Agent:
상대 차량이 보고 있던 신호 상태도 블랙박스나 현장에서 확인할 수 있었나요?

User:
영상에서는 빨간불로 보여요.

[DEBUG]
opponent_signal = 적색

Agent:
자차의 어느 부위와 상대 차량의 어느 부위가 충돌했나요?

User:
제 차 오른쪽 문 쪽을 상대차 앞부분이 박았습니다.

[DEBUG]
ego_collision_area = 우측 측면
opponent_collision_area = 전면

Agent:
필요한 사고 정보가 충분히 수집되었습니다.
```

---

## 22. `main.py` 기본 흐름

```python
from agent.intake_agent import IntakeAgent


def main():
    agent = IntakeAgent()

    print("교통사고 과실비율 분쟁 대응 Agent")
    print("사고 상황을 자유롭게 설명해주세요.")

    while True:
        user_input = input("\nUser: ").strip()

        if user_input.lower() in ["exit", "quit"]:
            break

        result = agent.process(user_input)

        if result.get("debug"):
            print("\n[DEBUG]")
            print(result["debug"])

        if result["intake_complete"]:
            print("\nAgent:")
            print("필요한 사고 정보가 충분히 수집되었습니다.")
            print("\n최종 사고 State:")
            print(result["state"])
            break

        print("\nAgent:")
        print(result["next_question"])


if __name__ == "__main__":
    main()
```

---

## 23. `IntakeAgent` 인터페이스

```python
class IntakeAgent:

    def __init__(self):
        self.state = initialize_state()
        self.history = []

    def process(self, user_input: str) -> dict:

        self.history.append({
            "role": "user",
            "content": user_input
        })

        extracted = extract_slots(
            state=self.state,
            user_input=user_input
        )

        self.state = update_state(
            self.state,
            extracted
        )

        accident_type = classify_accident(
            self.state
        )

        self.state.accident_type = accident_type

        active_slots = activate_slots(
            state=self.state,
            accident_type=accident_type
        )

        missing_slots = get_missing_slots(
            state=self.state,
            active_slots=active_slots
        )

        complete = check_intake_complete(
            state=self.state,
            missing_slots=missing_slots
        )

        if complete:
            return {
                "intake_complete": True,
                "state": self.state.model_dump()
            }

        next_question = generate_next_question(
            state=self.state,
            missing_slots=missing_slots,
            history=self.history
        )

        self.history.append({
            "role": "assistant",
            "content": next_question
        })

        return {
            "intake_complete": False,
            "next_question": next_question,
            "state": self.state.model_dump()
        }
```

---

## 24. API Key 관리

`.env`:

```text
OPENAI_API_KEY=...
```

또는:

```text
GEMINI_API_KEY=...
```

Python:

```python
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
```

API Key를 코드에 직접 작성하지 않는다.

---

## 25. 개발 단계

### Phase 1 — CLI + State

```text
input()
while loop
state 출력
```

### Phase 2 — Slot Extraction 연결

```text
자유로운 사용자 문장
→ JSON Slot 추출
```

테스트 입력:

```text
직진하다가 뒤에서 박혔어요.
주차장에서 후진하다가 차랑 부딪혔어요.
횡단보도에서 사람이 갑자기 나왔어요.
```

### Phase 3 — 사고유형 Classification

```text
Slot State
→ 사고 유형 후보
```

정보가 부족하면 `uncertain`이 정상적으로 나와야 한다.

### Phase 4 — Dynamic Question Generation

```text
Missing Slot
→ Agent 자연어 질문
```

확인할 항목:

```text
- 같은 질문 반복 여부
- 불필요한 질문 여부
- 사고 유형에 따라 질문이 달라지는지
- 중요한 질문부터 하는지
```

### Phase 5 — Stateful Intake Agent 완성

```text
User ↔ Agent
```

여러 Turn의 대화가 지속되고 마지막에 구조화된 사고 State를 출력해야 한다.

---

## 26. 최소 테스트 시나리오

### Case 1. 후방추돌

초기 입력:

```text
빨간불이라 정차했는데 뒤차가 박았습니다.
```

주요 확인:

```text
- 사고 장소
- 신호
- 정차 이유
- 급정거 여부
- 충돌 부위
```

### Case 2. 교차로 직진-좌회전

```text
교차로에서 직진하는데 맞은편 차가 좌회전하다가 박았습니다.
```

주요 확인:

```text
- 신호등 유무
- 자차 신호
- 상대차 신호
- 좌회전 신호 / 비보호 여부
- 충돌 부위
```

### Case 3. 차선변경

```text
옆차가 갑자기 제 차선으로 들어오면서 부딪혔습니다.
```

주요 확인:

```text
- 사고 장소
- 방향지시등
- 차선변경 방향
- 충돌 부위
- 양 차량 진행 상태
```

### Case 4. 차대보행자

```text
횡단보도에서 사람이랑 사고가 났습니다.
```

주요 확인:

```text
- 횡단보도 신호등
- 차량 신호
- 보행자 신호
- 보행자 횡단 상태
- 차량 속도
```

### Case 5. 차대이륜차

```text
교차로에서 오토바이와 부딪혔습니다.
```

주요 확인:

```text
- 신호등 유무
- 각 당사자 진행 방향
- 각 당사자 신호
- 차선 위치
- 충돌 부위
```

---

## 27. 디버그 모드

CLI 테스트에서는 매 Turn마다 내부 State를 확인할 수 있도록 한다.

```text
[DEBUG STATE]

accident_target:
  value: 차대차
  source: user

accident_place:
  value: 사거리교차로
  source: user

ego_signal:
  value: null

missing_slots:
  - ego_signal
  - opponent_signal
  - ego_collision_area

next_question_target:
  ego_signal
```

웹서비스에서는 숨기더라도 개발 중에는 반드시 확인 가능해야 한다.

---

## 28. 로그 저장

대화 테스트 결과를 JSON으로 저장한다.

```text
logs/
└─ session_20260825_153000.json
```

예:

```json
{
  "conversation": [
    {
      "role": "user",
      "content": "교차로에서 직진하다가 사고났어요."
    },
    {
      "role": "assistant",
      "content": "당시 차량용 신호는 어떤 색이었나요?"
    }
  ],
  "final_state": {},
  "accident_type": {},
  "missing_slots": []
}
```

---

## 29. 초기 기술 테스트 평가 항목

```text
1. Slot Extraction 성공 여부
2. 사고 대상 분류 성공 여부
3. 사고 장소 분류 성공 여부
4. 사고 유형 분류 성공 여부
5. 필요한 조건부 Slot 활성화 여부
6. 불필요한 질문 생성 여부
7. 이미 답한 질문 반복 여부
8. 질문 순서의 적절성
9. 사용자 답변이 State에 정상 반영되는지
10. 정상적으로 Intake가 종료되는지
```

---

## 30. 이후 Vision 연결 방식

CLI Intake Agent가 정상 동작하면 Vision 모듈을 추가한다.

```python
vision_result = analyze_video(
    video_path="./data/test.mp4"
)
```

출력 예:

```json
{
  "accident_target": "차대차",
  "accident_place": "사거리교차로(신호등 있음)",
  "ego_maneuver": "직진",
  "opponent_maneuver": "좌회전",
  "ego_signal": "녹색",
  "collision_type": "측면충돌"
}
```

State Manager에 다음 source로 병합한다.

```text
source = vision
```

---

## 31. 이후 RAG 연결 방식

Intake + Vision으로 사고 State가 완성되면 RAG에 전달한다.

```python
retrieved_cases = search_similar_cases(
    accident_state=state
)
```

출력 예:

```json
[
  {
    "case_id": "case_001",
    "fault_ratio": "0:100",
    "content": "..."
  },
  {
    "case_id": "case_002",
    "fault_ratio": "10:90",
    "content": "..."
  }
]
```

---

## 32. 전체 기술 확장 순서

```text
Phase 1
CLI + State
        ↓
Phase 2
Slot Extraction
        ↓
Phase 3
Accident Classification
        ↓
Phase 4
Dynamic Question Generation
        ↓
Phase 5
Stateful Intake Agent 완성
        ↓
Phase 6
Vision API 연결
        ↓
Phase 7
과실비율심의사례 RAG 연결
        ↓
Phase 8
Reasoning Agent
        ↓
Phase 9
사건경위서 / 분쟁조정신청서 생성
        ↓
Phase 10
FastAPI Backend
        ↓
Phase 11
Web Chat UI
```

---

## 33. 최종 CLI 목표

웹 개발 이전에 VSCode 터미널에서 최소 다음 시나리오가 가능해야 한다.

```text
$ python main.py

Agent:
사고 상황을 자유롭게 설명해주세요.

User:
교차로에서 직진 중 오른쪽에서 차량이 와서 박았습니다.

Agent:
당시 신호등이 있었나요?

User:
네.

Agent:
자차가 보고 있던 신호는 어떤 색이었나요?

User:
녹색이었습니다.

Agent:
상대 차량은 어느 방향으로 진행하고 있었나요?

User:
오른쪽에서 직진해서 들어왔습니다.

Agent:
충돌 부위는 어디인가요?

User:
제 차 오른쪽 문을 상대 차량 앞부분이 박았습니다.

Agent:
필요한 사고 정보 수집이 완료되었습니다.

Final State:
{
  "accident_target": "차대차",
  "accident_place": "사거리교차로(신호등 있음)",
  "ego_maneuver": "직진",
  "opponent_maneuver": "우측 직진 진입",
  "ego_signal": "녹색",
  "ego_collision_area": "우측 측면",
  "opponent_collision_area": "전면"
}
```

이 단계가 정상 동작하면 해당 Agent 로직을 FastAPI API로 감싸고 웹 챗봇 UI에 연결한다.

---

# 사용 모델 및 역할 분담

## 1. 기본 모델 구성

본 프로젝트의 MVP에서는 모델 역할을 다음과 같이 분리한다.

```text
1. 블랙박스 동영상 분석
   → Gemini 3.7 Flash

2. Intake Agent
   → GPT 계열 모델

3. Reasoning Agent
   → GPT 계열 모델

4. 사건경위서 / 분쟁조정신청서 / 보험사 이메일 작성
   → GPT-4o
```

핵심 원칙:

```text
Gemini = 동영상 이해 및 객관적 사고 사실 추출
GPT = 사용자와의 대화, 사고정보 보완, 논리 생성
GPT-4o = 최종 문서 및 이메일 작성
```

## 2. Video Analysis Model

기본 모델:

```text
gemini-3.7-flash
```

역할:

```text
- 사고 대상
- 사고 장소
- 자차 진행 행동
- 상대방 진행 행동
- 신호등 유무
- 자차 신호
- 상대방 신호
- 차선
- 차선변경 여부
- 방향지시등 여부
- 급정거 여부
- 제동 상황
- 충돌 시점
- 충돌 형태
- 자차 충돌 부위
- 상대방 충돌 부위
- 보행자 / 자전거 / 이륜차 존재 여부
```

Vision 분석 결과는 자연어 설명뿐 아니라 Structured JSON으로 반환한다.

```json
{
  "accident_target": "차대차",
  "accident_place": "사거리교차로(신호등 있음)",
  "ego_maneuver": "직진",
  "opponent_maneuver": "좌회전",
  "ego_signal": "녹색",
  "opponent_signal": "적색",
  "lane_change": false,
  "sudden_braking": false,
  "collision_type": "측면 충돌",
  "ego_collision_area": "우측 측면",
  "opponent_collision_area": "전면",
  "collision_timestamp": "00:08"
}
```

환경변수:

```text
GEMINI_API_KEY=...
VIDEO_MODEL=gemini-3.7-flash
```

모델명은 코드에 하드코딩하지 않는다.

## 3. Video Model 비교 테스트

영상 분석은 서비스 핵심 기술이므로 Gemini 계열의 다른 video-capable 모델과 비교 가능하도록 구현한다.

기본 모델:

```text
gemini-3.7-flash
```

비교 시에는 환경변수만 변경한다.

```text
VIDEO_MODEL=<비교할 Gemini 모델 ID>
```

동일 AI-Hub 영상에서 다음 항목을 비교한다.

```text
- 사고 대상
- 사고 장소
- 자차 진행 행동
- 상대방 진행 행동
- 신호 상태
- 충돌 형태
- 충돌 부위
- 급정거 여부
```

## 4. Intake Agent Model

역할:

```text
- Slot Extraction
- 사고 대상 분류
- 사고 장소 분류
- 사고 유형 후보 추정
- Missing Slot 탐색
- 다음 질문 대상 결정
- 자연어 꼬리 질문 생성
- Agent State 업데이트
```

환경변수:

```text
OPENAI_API_KEY=...
INTAKE_MODEL=<사용할 GPT 모델 ID>
```

Intake 모델은 코드 내부에 고정하지 않는다.

## 5. Reasoning Agent Model

입력:

```text
1. Intake Agent가 수집한 사용자 진술
2. Gemini가 추출한 Vision JSON
3. 자동차사고 과실비율 인정기준 검색 결과
4. 과실비율심의사례 RAG 검색 결과
```

출력:

```text
- 예상 과실비율
- 상대 보험사 주장에 대한 반박 포인트
- 사용자가 주장할 과실비율
- 유사 심의사례 활용 이유
- 최종 주장 논리
```

환경변수:

```text
REASONING_MODEL=<사용할 GPT 모델 ID>
```

## 6. Document Generation Model

기본 모델:

```text
gpt-4o
```

역할:

```text
- 사건경위서
- 사고내용 진술서
- 분쟁조정신청서
- 당사자 교섭 및 조정 전 합의 경위서
- 보험사 제출용 이메일
```

문서 생성 규칙:

```text
- 제공된 사고 사실과 검색 근거만 사용한다.
- 확인되지 않은 사실을 추가하지 않는다.
- 객관적 사실과 사용자 의견을 구분한다.
- 보험사 제출용 문서 형식에 맞게 작성한다.
```

환경변수:

```text
DOCUMENT_MODEL=gpt-4o
```

## 7. 모델 설정 예시

`.env`

```text
OPENAI_API_KEY=...
GEMINI_API_KEY=...

VIDEO_MODEL=gemini-3.7-flash
INTAKE_MODEL=<GPT model ID>
REASONING_MODEL=<GPT model ID>
DOCUMENT_MODEL=gpt-4o
```

## 8. 모델 호출 모듈 구조

```text
models/
├─ __init__.py
├─ gemini_video.py
├─ openai_intake.py
├─ openai_reasoning.py
└─ openai_document.py
```

역할:

```text
gemini_video.py
→ 블랙박스 영상 분석

openai_intake.py
→ Slot Extraction / Dynamic Question

openai_reasoning.py
→ RAG 결과 및 사고정보 종합 추론

openai_document.py
→ 사건경위서 / 신청서 / 이메일 작성
```

## 9. 최종 모델 파이프라인

```text
[User]
사고 상황 설명
블랙박스 업로드
        │
        ├─────────────────────────────┐
        ↓                             ↓
[Intake Agent]                  [Gemini Video]
GPT 계열                       gemini-3.7-flash
        │                             │
        ↓                             ↓
사용자 사고 State              Vision Structured JSON
        │                             │
        └──────────────┬──────────────┘
                       ↓
                  [RAG Search]
                       ↓
             과실비율심의사례 Top-K
                       ↓
               [Reasoning Agent]
                    GPT 계열
                       ↓
            예상 과실 + 주장 논리
                       ↓
              [Document Generator]
                     GPT-4o
                       ↓
          사건경위서 / 분쟁조정신청서
               / 보험사 이메일
```

## 10. 개발 우선순위

```text
1순위. Gemini 블랙박스 영상 분석
2순위. Vision Structured JSON 안정화
3순위. Intake Agent CLI
4순위. 사용자 State + Vision State 통합
5순위. 심의사례 RAG
6순위. Reasoning Agent
7순위. GPT-4o 문서 생성
8순위. FastAPI
9순위. Web Chat UI
```

## 11. 모델별 역할 요약

| Component | Model | 주요 역할 |
|---|---|---|
| Video Analysis | Gemini 3.7 Flash | 블랙박스 영상 이해 및 Structured JSON 추출 |
| Intake Agent | GPT 계열 | Slot Filling, 사고분류, 동적 꼬리질문 |
| Reasoning Agent | GPT 계열 | 사용자 진술 + Vision + RAG 종합 및 주장 논리 |
| Document Generation | GPT-4o | 사건경위서, 분쟁조정신청서, 보험사 이메일 |
