# Agent 계약서 — AI 담당용

백엔드는 `AGENT_IMPL=패키지.모듈:클래스` 로 지정된 클래스를 import해서 인스턴스를 하나 만들고,
아래 5개 메서드를 호출한다. 입출력 타입은 `app/agent/base.py` 의 Pydantic 모델이 정본이다.

- 메서드는 `async def` 여도 되고 일반 `def` 여도 된다 (동기는 스레드에서 실행).
- 반환은 해당 Result 모델 인스턴스 또는 같은 필드의 `dict`. 백엔드가 검증한다. 검증 실패 → 해당 Job 실패(사용자에게는 로딩만 사라짐).
- 상태를 갖지 말 것. 필요한 맥락은 매 호출의 입력에 다 들어 있다.
- 영상은 로컬 파일 경로(`video_path`)로 온다. Gemini 등에 올리는 것은 Agent 몫.

| 메서드 | 언제 | 입력 | 출력 |
|---|---|---|---|
| `analyze(AnalyzeInput)` | 영상 + 설명이 모여 분석 Job이 돌 때 | `video_path` `video_mime` `description`(유저 텍스트 메시지 전부 합침) | `summary_text`(H18 본문) `facts`(자유 JSON, 이후 호출에 그대로 돌아옴) `questions`(아래 "questions" 절 참고, 빈 리스트 = 질문 없이 바로 판정) `title`(사건 제목) `video_meta{speed_kph, impact_at_sec}` |
| `chat(ChatInput)` | 사용자가 입력창에 글을 칠 때마다 | `messages`(최근 40건 text) `new_message` `facts` `questions` `verdict`(활성 판정 스냅샷) `has_video` `has_report` | `reply` `next_action`(`none`·`verdict`·`rejudge`·`create_report`·`create_rebuttal`) `fact_updates`(facts에 병합됨) |
| `judge(JudgeInput)` | `next_action`이 `verdict`/`rejudge`일 때 | `messages` `facts` `previous_verdict` | `ratio_mine+ratio_other=100` `summary` `change_reason`(재판정 시) `opponent_claim` `basis{chart{name,note}, precedents[{id,title,body_text}]}` — **`body_text`(H37 팝업 본문)는 `judge` 가 채워 보내야 한다** |
| `write(WriteInput)` | 경위서·반박의견서 초안/다시 쓰기 | `kind` `messages` `facts` `verdict` `revision_request` `previous_sections` `report_sections` | report: `sections[4]{index,title,body}` `caveat` `page_count` / rebuttal: `body` |
| `explain(ExplainInput)` | **선택 — 백엔드는 부르지 않는다** (구현하지 않아도 된다) | `precedent_id` `facts` | `body_text` |

## `questions` — 분석이 돌려주는 확인 질문

- `questions[0]` 은 백엔드가 그대로 질문 카드 메시지로 사용자에게 보낸다.
- `questions[1:]` 는 저장해 두었다가 이후 `chat(ChatInput)` 의 `questions` 로 매번 되돌려준다.
  남은 질문을 언제 어떻게 물을지는 Agent 가 대화 흐름에서 정한다.
- **빈 리스트는 "물어볼 것이 없다"는 뜻이다.** 질문 카드를 보내지 않고 바로 판정 단계로 넘어간다.
  질문을 없애고 싶으면 `questions: []` 로 두면 된다.

## `facts` — 사건 사실 누적본

- JSON 직렬화 가능한 값만 담는다(dict / list / str / int / float / bool / null).
  datetime, set, 커스텀 객체는 안 된다 — 저장 시 실패해 Job 이 실패한다.
- `chat` 의 `fact_updates` 는 **얕게(shallow) 병합**된다: 최상위 키 단위로 덮어쓰고,
  키 삭제는 없다. 중첩 dict 안의 일부만 바꾸려면 그 최상위 키의 전체 값을 다시 보내야 한다.
- 매 호출마다 통째로 오가므로 작게 유지한다(요약된 사실 위주, 원문 전체 붙여넣기 금지).

## `basis.precedents[].body_text`

H37 판례 팝업(E-2 `GET /api/v1/precedents/{precedentId}`)에 그대로 보여 주는 설명문이다.

- **`judge` 가 판례마다 `body_text` 를 채워서 돌려주는 것이 계약이다.** 백엔드는 이 값을 판정에 그대로 저장하고,
  팝업 요청이 오면 저장값을 읽어 보여 준다. **`explain(ExplainInput)` 은 선택 구현이며 백엔드는 절대 부르지 않는다** —
  즉 판정 시점에 비워 두면 나중에 채울 방법이 없고, 팝업은 제목만 남는다.
- 스키마 기본값이 빈 문자열이라 검증에서 막히지는 않지만, 빈 `body_text` 는 계약 위반으로 본다.
- `basis.chart.note` 는 생략 가능하다.

참고 구현: `app/agent/mock.py` (시연 시나리오 고정 응답).
