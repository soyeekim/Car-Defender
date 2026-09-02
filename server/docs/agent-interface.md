# Agent 계약서 — AI 담당용

백엔드는 `AGENT_IMPL=패키지.모듈:클래스` 로 지정된 클래스를 import해서 인스턴스를 하나 만들고,
아래 5개 메서드를 호출한다. 입출력 타입은 `app/agent/base.py` 의 Pydantic 모델이 정본이다.

- 메서드는 `async def` 여도 되고 일반 `def` 여도 된다 (동기는 스레드에서 실행).
- 반환은 해당 Result 모델 인스턴스 또는 같은 필드의 `dict`. 백엔드가 검증한다. 검증 실패 → 해당 Job 실패(사용자에게는 로딩만 사라짐).
- 상태를 갖지 말 것. 필요한 맥락은 매 호출의 입력에 다 들어 있다.
- 영상은 로컬 파일 경로(`video_path`)로 온다. Gemini 등에 올리는 것은 Agent 몫.

| 메서드 | 언제 | 입력 | 출력 |
|---|---|---|---|
| `analyze(AnalyzeInput)` | 영상 + 설명이 모여 분석 Job이 돌 때 | `video_path` `video_mime` `description`(유저 텍스트 메시지 전부 합침) | `summary_text`(H18 본문) `facts`(자유 JSON, 이후 호출에 그대로 돌아옴) `questions`(첫 질문은 `questions[0]`을 백엔드가 그대로 카드로 보냄) `title`(사건 제목) `video_meta{speed_kph, impact_at_sec}` |
| `chat(ChatInput)` | 사용자가 입력창에 글을 칠 때마다 | `messages`(최근 40건 text) `new_message` `facts` `questions` `verdict`(활성 판정 스냅샷) `has_video` `has_report` | `reply` `next_action`(`none`·`verdict`·`rejudge`·`create_report`·`create_rebuttal`) `fact_updates`(facts에 병합됨) |
| `judge(JudgeInput)` | `next_action`이 `verdict`/`rejudge`일 때 | `messages` `facts` `previous_verdict` | `ratio_mine+ratio_other=100` `summary` `change_reason`(재판정 시) `opponent_claim` `basis{chart{name,note}, precedents[{id,title,body_text}]}` — `body_text`는 H37 팝업용 설명문 |
| `write(WriteInput)` | 경위서·반박의견서 초안/다시 쓰기 | `kind` `messages` `facts` `verdict` `revision_request` `previous_sections` `report_sections` | report: `sections[4]{index,title,body}` `caveat` `page_count` / rebuttal: `body` |
| `explain(ExplainInput)` | (선택) 백엔드는 현재 부르지 않음 | `precedent_id` `facts` | `body_text` |

참고 구현: `app/agent/mock.py` (시연 시나리오 고정 응답).
