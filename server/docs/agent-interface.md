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

---

## 어디에 무엇을 만드나

저장소 루트의 `ai/` 아래에 클래스를 하나 만들면 된다. 위치와 이름은 자유다.

```
Car-Defender/
├─ ai/                 ← 여기. AI 담당 영역
│  ├─ agent/
│  │  └─ real.py       ← 예: class RealAgent
│  └─ requirements.txt
└─ server/             ← 백엔드. 건드릴 일 없다
```

백엔드는 `.env` 의 `AGENT_IMPL` 한 줄로 이 클래스를 찾는다.

```
AGENT_IMPL=ai.agent.real:RealAgent
```

**별도 서버도, HTTP 호출도, 컨테이너 추가도 없다.** 백엔드 프로세스가 이 클래스를
`importlib` 로 가져와 인스턴스 하나를 만들고 메서드를 직접 부른다. Docker 이미지에는
`ai/` 가 통째로 들어가므로(`/srv/ai`) `import` 는 그냥 된다.

## 붙기 전에 스스로 확인하는 법

계약을 지켰는지 검사하는 테스트가 있다. 이걸 통과하면 백엔드에 붙는다.

```bash
cd server
AGENT_CONTRACT_IMPL=ai.agent.real:RealAgent \
AGENT_CONTRACT_VIDEO=/경로/블랙박스.mp4 \
.venv/Scripts/python -m pytest tests/test_agent_contract.py -v
```

`AGENT_CONTRACT_VIDEO` 를 주지 않으면 더미 파일이 들어가므로, 실제 영상으로 한 번은 돌려 본다.
실패 메시지가 무엇을 어떻게 고쳐야 하는지까지 알려준다.

## 지켜야 할 제약

**시간 — 한 번의 호출은 300초 안에 끝나야 한다.**
`analyze` / `judge` / `write` 는 각각 하나의 Job 으로 돌고, 300초(`JOB_TIMEOUT_SECONDS`)를 넘기면
실패 처리된다. 사용자 화면에서는 로딩이 사라지고 끝난다. 3분짜리 영상을 업로드해 분석하는
경로가 여기에 걸리기 쉬우니, 오래 걸리는 작업은 잘라서 하거나 모델을 바꾼다.

**인스턴스는 프로세스당 하나, 서버 기동 중에 만들어진다.**
`__init__` 은 즉시 끝나야 한다. 여기서 네트워크를 부르거나 `asyncio.run` 을 쓰면 기동이 막히거나
죽는다. 무거운 준비는 첫 호출 때 지연 초기화하거나 모듈 수준 상수로 둔다.

**동기 함수여도 된다.**
`def` 로 만들면 백엔드가 스레드에서 돌린다. `async def` 로 만들 거라면 이벤트 루프를 막지 말 것
(무거운 CPU 작업은 `asyncio.to_thread` 로 뺀다). 서버는 `--workers 1` 로 도는 단일 프로세스라
루프를 붙잡으면 다른 사용자 요청까지 멈춘다.

**상태를 갖지 말 것.**
필요한 맥락은 매 호출 입력에 다 들어 있다. 인스턴스 변수에 사건 정보를 쌓아 두면 사용자끼리 섞인다.

## API 키와 라이브러리

**환경변수**는 `.env` 에 넣으면 컨테이너 안 `os.environ` 으로 그대로 온다. 백엔드 설정은
모르는 키를 무시하므로 자유롭게 추가해도 된다.

```
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

읽는 쪽은 평범하게 쓴다. 키가 없을 때 무엇이 없는지 알 수 있게 메시지를 남긴다.

```python
key = os.environ["GEMINI_API_KEY"]
```

**라이브러리**는 `ai/requirements.txt` 에 추가한다. 이미지를 만들 때 함께 설치된다.
추가했다면 백엔드 담당에게 알려 이미지를 다시 빌드하게 한다.

**파일 쓰기**는 `/tmp` 만 쓴다. 그 밖은 컨테이너를 다시 띄우면 사라진다.

## `write` 반환 형태

`kind` 에 따라 채우는 필드가 다르다. 둘 다 `WriteResult` 한 모델을 쓰므로 스키마 검증만으로는
안 걸러진다. 잘못 채우면 문서가 빈 채로 만들어진다.

| `kind` | 채울 것 | 비고 |
|---|---|---|
| `report` (사건경위서) | `sections` **정확히 4개** (`index` 1~4), `caveat`, `page_count` | PDF 로 만들어진다 |
| `rebuttal` (반박의견서) | `body` | 메일 본문으로 그대로 나간다 |

`revision_request` 가 있으면 다시 쓰기다. `previous_sections` 에 이전 원고가 오니 그걸 고친다.
반박의견서를 쓸 때는 `report_sections` 로 경위서 본문이 함께 온다.
