# 카-디펜더 백엔드 설계서

> 2026-09-03 · 기준 문서: `20_API명세서_v2.md` (엔드포인트 36 · 카드 10 · SSE 5 · 에러 22)
> 이 문서는 **어떻게 만들 것인가**를 정한다. **무엇을 만들 것인가**는 API 명세서가 정본이며, 이 문서와 어긋나면 API 명세서를 따른다.

---

## 0. 확정 결정 요약

| 항목 | 결정 |
|---|---|
| 언어·프레임워크 | Python 3.11 · FastAPI · uvicorn (워커 1개) |
| DB | PostgreSQL 16 (Docker Compose, EC2 한 대에 앱과 함께) · SQLAlchemy 2 async + asyncpg · Alembic |
| 비동기 Job | **단일 프로세스 asyncio 태스크** (A안). Redis·별도 워커 없음 |
| SSE | 메모리 허브 (사건별 구독 큐 + 5분 링 버퍼) |
| Agent | **AI 담당이 만든 파이썬 패키지를 같은 프로세스에서 import해 호출**. 백엔드는 Protocol(인터페이스)과 Mock만 가짐 |
| 객체 스토리지 | `StorageBackend` 인터페이스. `local`(기본) / `s3` 환경 변수 전환 |
| 메일 | SMTP(aiosmtplib). 개발용 `mock` 백엔드 |
| PDF | fpdf2 + Pretendard TTF 내장 |
| 인증 | JWT(HS256) 30분 access · 불투명 refresh 토큰 14일(DB 해시 저장, httpOnly 쿠키) · bcrypt |
| 저장소 루트 | `C:\finance ai challenge\server` (= git 루트). 설계 문서 폴더(`금융 ai 디자인/`)는 저장소 밖 참조용 |
| 커밋 형식 | Conventional Commits · 타입 영어 + 요약 한국어 · **Claude 관련 트레일러 없음** |
| 데모 로그인 (A-9) | 명세대로 보류. 구현하지 않음 |

---

## 1. 디렉터리 구조

```
server/
  pyproject.toml            의존성·도구 설정 (uv 또는 pip)
  .env.example              환경 변수 목록과 예시값
  .gitignore · .gitattributes(* text=auto eol=lf)
  README.md                 로컬 실행·배포 절차
  Dockerfile · docker-compose.yml · deploy/nginx.conf
  alembic.ini · alembic/versions/
  docs/
    superpowers/specs/      이 문서
    superpowers/plans/      구현 계획
    agent-interface.md      AI 담당용 Agent 계약서
  app/
    main.py                 create_app() · lifespan(시작 시 미완료 Job 정리, 허브 초기화)
    config.py               Settings (pydantic-settings)
    db.py                   engine · session factory · Base
    ids.py                  ULID 생성
    clock.py                now_utc() · to_kst_iso()
    security.py             JWT 발급/검증 · 비밀번호 해시 · 스트림 토큰
    errors.py               ApiError · 에러 코드 카탈로그 22종 · 예외 핸들러
    deps.py                 get_db · current_user · owned_case
    ratelimit.py            메모리 토큰 버킷
    models/                 SQLAlchemy 테이블 (아래 §2)
    schemas/                Pydantic 요청·응답·카드 payload
    api/                    라우터 (auth · users · legal · cases · messages · videos · verdict · precedents · report · rebuttal · events · health)
    services/               auth · case · chat · video · verdict · report · rebuttal · presenters
    jobs/                   runner.py · analysis.py · verdict.py · report.py · rebuttal.py
    sse/hub.py              SSE 허브
    agent/                  base.py(Protocol + I/O 모델) · loader.py · mock.py
    storage/                base.py · local.py · s3.py
    mail/                   base.py · smtp.py · mock.py · templates.py
    pdf/                    report_pdf.py · fonts/Pretendard-Regular.ttf, Pretendard-Bold.ttf
    content/                guide 문구 · 약관 3종 markdown · 고정 안내문 · 라벨
    seed.py                 시드 데이터 (closed 사건 1건)
  tests/
    conftest.py             앱·DB·클라이언트 픽스처
    api/                    엔드포인트별 테스트
    services/ · jobs/ · sse/
```

**계층 규칙.** `api/`는 요청 파싱과 응답 직렬화만 한다. 규칙·상태 전이·이벤트 송출은 `services/`와 `jobs/`에 있다. `models/`는 다른 계층을 import하지 않는다. `agent/` `storage/` `mail/`은 인터페이스 뒤에 숨기고 `config`로 구현체를 고른다.

---

## 2. 데이터 모델

명세 §3의 12개 엔티티 + 인증용 2개. 공통 규칙:

- PK는 ULID 26자 `String(26)`. 외부 노출 ID와 동일.
- 시각은 `DateTime(timezone=True)`, UTC로 저장. 응답 직렬화에서 `+09:00`으로 변환.
- JSON 컬럼은 SQLAlchemy `JSON` 타입 (Postgres·SQLite 공통).
- `SendLog`만 FK 없음 (사건 삭제 후에도 남긴다).

| 테이블 | 컬럼 | 메모 |
|---|---|---|
| `users` | id, email(unique), password_hash, agreed_terms_at, agreed_privacy_at, agreed_video_at, onboarded_at, is_demo, created_at | 동의 3종은 시각 컬럼으로 |
| `refresh_tokens` | id, user_id FK, token_hash(unique), expires_at, revoked_at, created_at | 로그아웃 시 revoked |
| `password_reset_tokens` | id, user_id FK, token_hash(unique), expires_at, used_at, created_at | 30분 · 1회용 |
| `cases` | id, user_id FK, title, status, created_at, updated_at | `stages`는 저장하지 않고 계산 (§7.2) |
| `messages` | id, case_id FK, role, type, payload JSON, created_at | 카드 계약은 명세 §4 |
| `videos` | id, case_id FK(unique), filename, size_bytes, duration_sec, mime_type, recorded_at, meta JSON, storage_key | 사건당 1개 (교체 시 이전 객체 삭제) |
| `analyses` | case_id PK/FK, summary_text, facts JSON, questions JSON, created_at, updated_at | `facts`는 화면에 안 나감 |
| `verdicts` | id, case_id FK, version, ratio_mine, ratio_other, summary, change_reason, opponent_claim JSON, basis JSON, is_active, created_at | INSERT 누적 · 활성 1건 · `basis.precedents[].body_text` 보관 |
| `reports` | id, case_id FK, version, sections JSON, caveat, page_count, revision_request, created_at | INSERT 누적 |
| `report_pdfs` | id, report_id FK(unique), storage_key, filename, size_bytes, created_at | 버전당 1개, 재생성 없음 |
| `rebuttals` | id, case_id FK(unique), recipient, claim_number, subject, subject_auto, body, attachments JSON, status, created_at, updated_at | `sent`면 잠금 |
| `send_logs` | id, case_id, rebuttal_id, idempotency_key(unique), sent_at, from_email, recipient, subject, attachment_names JSON, result, provider_message_id, error | **FK 없음**. 멱등키는 여기 |
| `jobs` | id, case_id FK, kind, status, error JSON, started_at, ended_at | 명세의 `idempotencyKey`는 발송이 동기라 `send_logs`로 이동 |

**명세와 다른 점 두 가지**(응답 계약은 동일): `Case.stages`를 계산 필드로 두는 것, 멱등키를 `send_logs`에 두는 것. 둘 다 저장하면 어긋날 수 있는 값을 저장하지 않기 위해서다.

**Case 삭제.** ORM cascade로 messages · videos · analyses · verdicts · reports(+report_pdfs) · rebuttals · jobs 삭제. 서비스 계층에서 스토리지 객체(영상·PDF)도 함께 지운다. `send_logs`는 남긴다.

---

## 3. 인증·보안

- **Access 토큰**: HS256 JWT, `sub=user_id`, 30분. `Authorization: Bearer`. 만료면 `TOKEN_EXPIRED`, 그 외 위조·누락은 `UNAUTHORIZED`.
- **Refresh 토큰**: 32바이트 난수를 base64url로 발급, DB에는 SHA-256 해시만 저장. 14일. 쿠키 `refresh_token`, `HttpOnly; Secure(운영); SameSite=Lax; Path=/api/v1/auth`. `/auth/refresh`는 회전(rotation)하지 않고 만료까지 재사용한다. 로그아웃은 `revoked_at` 기록 + 쿠키 삭제.
- **비밀번호**: bcrypt(cost 12). 정책은 8자 이상 + 숫자 1자 이상.
- **재설정**: `/auth/password-reset`는 존재 여부와 무관하게 `202`. 토큰은 난수 해시 저장, 30분, 1회. 메일 본문에 `{FRONT_BASE_URL}/reset?token=...` 링크.
- **스트림 토큰**: `{"vid": videoId, "exp": +10분}` JWT를 쿼리 `t`로. `GET /videos/{id}/stream`은 Bearer 없이 이 토큰만 검사.
- **속도 제한**: `ratelimit.py` 메모리 토큰 버킷. 로그인·이메일 확인·재설정 메일(분당 3회)에 IP+이메일 기준. 초과 시 `RATE_LIMITED`.
- **권한**: `owned_case` 의존성이 `case.user_id != current_user.id`면 `FORBIDDEN`, 없으면 `NOT_FOUND`.
- **CORS**: `CORS_ORIGINS` 환경 변수 목록. `credentials=True`.

---

## 4. 에러 처리

`errors.py`에 `ERROR_CATALOG: dict[code, ErrorSpec(http, title, message, retryable, actions)]` 22종을 명세 §2.3 표 그대로 둔다.

```python
raise ApiError("REBUTTAL_LOCKED", fields={"missing": "report"})
```

핸들러가 카탈로그를 합쳐 여섯 키(`code title message retryable actions fields`)를 항상 내보낸다. `actions`는 카탈로그 기본값을 쓰되 호출부에서 덮어쓸 수 있다.

- Pydantic `RequestValidationError` → `VALIDATION_FAILED` 422, `fields`에 필드별 한국어 문구.
- 처리되지 않은 예외 → `INTERNAL_ERROR` 500 + 로그.
- 회원가입은 라우터에서 항목을 전부 검사해 `fields`를 모으고, `code`는 우선순위 `email → password → passwordConfirm → agreements`의 첫 항목.
- **채팅에서 시작된 동작은 HTTP 에러 대신 카드**(명세 §1.1). `chat` 서비스가 `ApiError`를 잡아 `text` 카드(`REPORT_VERDICT_REQUIRED`의 message) 또는 `rebuttal_locked` 카드로 바꿔 송출한다.

---

## 5. Job 실행기와 SSE

### 5.1 JobRunner (`jobs/runner.py`)

```python
async def start(case_id, kind, handler) -> Job
```

1. 같은 사건에 `queued|running` Job이 있으면 `ApiError("JOB_ALREADY_RUNNING")`.
2. `jobs` INSERT(status=running) · commit.
3. `case.updated` 송출 (activeJob 채움, 상태 변경이 있으면 함께).
4. `asyncio.create_task(_run(job_id, handler))`. 태스크 참조를 set에 보관해 GC를 막는다.
5. `_run`: 새 DB 세션을 열어 `handler(session, case_id, job_id)` 실행 → `succeeded`. 예외면 `failed` + `error` JSON + 로그, 그리고 `case.updated`(activeJob null)로 로딩만 지운다. 핸들러 자신이 성공 경로의 마지막 `case.updated`를 보낸다.
6. 앱 시작 시 `queued|running` 전부 `failed`로 정리 (재시작 복구).

Job 핸들러 4종:

| kind | 입력 | 하는 일 | 끝의 이벤트 순서 |
|---|---|---|---|
| `analysis` | 영상 + 사용자 text 메시지들 | Agent `analyze` → `analyses` upsert · `cases.title` · `videos.meta` → text 카드(summary) → text 카드(questions[0], 있으면) | `message.created`×(1~2) → `message.updated`(video_attachment.meta) → `case.updated`(needs_review 또는 질문 없으면 곧바로 verdict Job 시작) |
| `verdict` | 대화 + facts + 이전 활성 판정 | Agent `judge` → 이전 `is_active=false` · 새 버전 INSERT → verdict 카드 | `message.created`(verdict) → `case.updated`(judged · 새 비율) |
| `report` | 대화 + 판정 (+ 수정 요청) | Agent `write(report)` → 새 버전 INSERT → 첫 버전이면 `report_draft` 카드 생성, 이후 버전이면 기존 카드 payload 갱신 | `message.created` 또는 `message.updated` → `case.updated` |
| `rebuttal` | 대화 + 판정 + 최신 경위서 | Agent `write(rebuttal)` → `rebuttals` upsert(draft) · 첨부 기본값(경위서 PDF 최신 + 영상) → `rebuttal_draft` 카드 | `message.created` → `case.updated` |

`report` Job은 PDF를 만들지 않는다. PDF는 F-5에서 동기로 생성한다(보통 1초 미만).

### 5.2 채팅 처리 (`services/chat.py`)

`POST /messages`는 Job이 아니다. 순서(명세 §1.1):

1. 진행 중 Job이 `analysis|verdict`면 `JOB_ALREADY_RUNNING`.
2. 유저 `text` 저장 → `message.created` 송출 → `202 {message, assistantPending}` 즉시 반환.
3. 백그라운드 태스크(사건별 `asyncio.Lock`으로 직렬화):
   - 영상은 있는데 `analyses`가 없으면 → `analysis` Job 시작, 끝.
   - 아니면 컨텍스트 구성 → Agent `chat` → `factUpdates`를 `analyses.facts`에 병합 → assistant `text` 저장(영상 없으면 `cta=upload_video`) → `message.created`.
   - `nextAction`: `verdict|rejudge` → verdict Job · `create_report` → report 서비스(판정 없으면 text 카드) · `create_rebuttal` → rebuttal 서비스(잠기면 `rebuttal_locked` 카드) · `none` → 끝.
   - 예외는 로그만 남기고 `INTERNAL_ERROR` message를 text 카드로 보낸다 (사용자가 멈춘 것처럼 보이지 않게).

### 5.3 SSE 허브 (`sse/hub.py`)

- `publish(case_id, event, data)`: ULID `id` 부여 → 링 버퍼 `deque[(id, ts, event, json)]`에 append(5분 지난 항목 제거) → 그 사건의 모든 구독자 `asyncio.Queue`에 put.
- `subscribe(case_id, last_event_id)`: 큐 등록 → `connected` 이벤트 → 버퍼에서 `last_event_id` 이후 항목 재전송(ULID는 시간순 정렬 가능) → 큐 소비 루프. 15초 무이벤트면 `: keepalive`. 연결 종료 시 큐 제거.
- 이벤트 5종: `connected` `message.created` `message.updated` `case.updated` `rebuttal.sent`. `case.updated`의 data는 B-3 응답 본문 전체(presenter 재사용).
- 버퍼 밖 `Last-Event-ID`면 그냥 실시간부터 보낸다. 프론트가 B-3 · C-1을 다시 부른다(명세 §6).

---

## 6. Agent 계약 (`app/agent/base.py` · `docs/agent-interface.md`)

Agent는 AI 담당이 구현하는 **무상태 파이썬 클래스**다. 백엔드는 인터페이스만 정의하고 `AGENT_IMPL=패키지.모듈:클래스`로 import한다.

```python
class Agent(Protocol):
    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult: ...
    async def chat(self, inp: ChatInput) -> ChatResult: ...
    async def judge(self, inp: JudgeInput) -> JudgeResult: ...
    async def write(self, inp: WriteInput) -> WriteResult: ...
    async def explain(self, inp: ExplainInput) -> ExplainResult: ...   # 선택
```

| 호출 | 입력 | 출력 |
|---|---|---|
| `analyze` | `video_path: Path` · `video_mime` · `description: str`(유저 text 메시지들을 합친 것) | `summary_text` · `facts: dict` · `questions: list[str]` · `title` · `video_meta{speed_kph, impact_at_sec}` |
| `chat` | `messages: list[{role,text}]`(최근 40건, text 카드만) · `new_message` · `facts` · `questions` · `verdict: VerdictSnapshot|None` · `has_video` · `has_report` | `reply` · `next_action: none|verdict|rejudge|create_report|create_rebuttal` · `fact_updates: dict` |
| `judge` | `messages` · `facts` · `previous_verdict: VerdictSnapshot|None` | `ratio{mine,other}` · `summary` · `change_reason` · `opponent_claim|None` · `basis{chart{name,note}, precedents[{id,title,body_text}]}` |
| `write` | `kind: report|rebuttal` · `messages` · `facts` · `verdict` · `revision_request|None` · `previous_sections|None` · `report_sections`(rebuttal일 때 최신 경위서) | report: `sections[{index,title,body}]` · `caveat` · `page_count` / rebuttal: `body` |
| `explain` | `precedent_id` · `facts` | `body_text` |

규칙:

- 모든 I/O는 Pydantic 모델. 반환값이 검증에 실패하면 Job은 `failed`. 백엔드는 재요청하지 않는다.
- 동기 함수로 구현돼 있으면 `loader.py`가 `asyncio.to_thread`로 감싼다.
- `judge`는 `precedents[].body_text`(H37 팝업 본문)를 **반드시** 채워 돌려준다. 백엔드는 `explain`을 부르지 않으므로(선택 구현), 판정 시점에 비면 E-2 팝업은 제목만 남는다 — E-2는 저장값만 읽는다.
- `MockAgent`(`app/agent/mock.py`)는 교차로 이륜차 신호위반 시연 사례의 고정 응답. 질문 2개 → 판정 0:100 → 정정 시 재판정 20:80. 기본값이며 테스트에 쓴다.
- 영상은 로컬 경로로만 넘긴다. S3 백엔드일 때는 백엔드가 임시 파일로 내려받고 호출 후 삭제한다.
- 나중에 AI 담당이 별도 프로세스가 되면 같은 Protocol을 구현한 `HttpAgent` 어댑터를 추가한다.

---

## 7. 스토리지·영상

- `StorageBackend`: `put(key, fileobj|path) → size` · `open_range(key, start, end) → async iterator` · `size(key)` · `delete(key)` · `local_path(key) → contextmanager[Path]`(로컬은 그대로, S3는 임시 다운로드).
- `local`: `STORAGE_LOCAL_DIR` 아래 `videos/{caseId}/{videoId}.mp4`, `pdfs/{caseId}/{reportId}.pdf`.
- `s3`: boto3, 같은 키 구조. Range는 `GetObject(Range=...)`로 프록시. 프리사인드 URL은 쓰지 않는다(명세 §8.2).
- 업로드: `UploadFile`을 청크로 스토리지에 저장 → `ffprobe -show_format -show_streams`로 `duration`·`creation_time` 추출(없으면 `recorded_at=null`) → 기존 영상 있으면 교체 → `video_attachment`(role user) 카드 → 유저 text가 1건 이상이면 analysis Job, 아니면 고정 문구 text 카드.
- 스트리밍: `Range` 헤더 파싱 → `206` + `Content-Range` + `Accept-Ranges` + `Cache-Control: private, no-store`. Range 없으면 `200` 전체.
- 서버 본문 제한은 nginx `client_max_body_size 256m`. 앱은 거절하지 않는다.

---

## 8. 문서 생성·메일

- **PDF** (`pdf/report_pdf.py`): fpdf2. Pretendard Regular/Bold 등록. 제목 `사건경위서`, 사건 제목·날짜, 4개 절, 하단 고지 문구. `page_count`는 생성 후 실제 페이지 수로 `reports.page_count`를 덮어쓴다. 파일명 `사건경위서_{사건제목}_{YYYYMMDD}.pdf`(제목의 파일 금지 문자는 `_`로).
- **메일** (`mail/`): `Mailer.send(MailMessage) → provider_message_id`. `smtp`는 aiosmtplib(STARTTLS/SSL 환경 변수), `mock`은 로그 출력 + 가짜 ID. 헤더는 명세 §8.1 그대로(`From: "카-디펜더 ({email})" <MAIL_FROM>`, `Sender`·`Reply-To`=가입 이메일). 본문 말미 고정 문구 추가. 첨부 합계 25MB 초과면 영상 제외 + 안내 한 줄.
- **발송 (G-4)**: `Idempotency-Key` 없으면 400 → 같은 키의 `send_logs` 있으면 그 결과 반환 → 검증(recipient·claimNumber·status) → 메일 동기 발송 → 성공: `rebuttals.status=sent` · `send_logs` INSERT · `sent` 카드 · `rebuttal.sent` · `case.updated`(sent). 실패: `send_logs`에 `result=failed` 기록 후 `MAIL_SEND_FAILED` 502. 실패 기록은 G-5 목록에서 `result: failed`로 보인다.

---

## 9. 응답 조립 (`services/presenters.py`)

라벨·계산 필드는 한 곳에서 만든다.

- `status_label`: intake 접수중 · analyzing 분석중 · needs_review 확인 필요 · judged 판정 완료 · sent 발송 완료 · closed 종결.
- `stages`: 명세 §7.2 표를 `(status, active_job.kind, report_exists, rebuttal_status)`로 계산.
- `subtitle`: `접수 MM-DD` + 영상 있으면 ` · 블랙박스 1건`.
- `documents.report.label`: 없음 `아직 없음` / `{서수} 버전 · {n}장`. 서수는 첫·두·세·네·다섯 번째 … (6 이상은 `{n}번째`).
- `documents.rebuttal.label`: 판정 없음 `잠김 · 판정과 경위서가 먼저예요` · 경위서 없음 `잠김 · 경위서를 만들면 열려요` · 둘 다 있고 초안 없음 `이제 만들 수 있어요` · draft `작성 중` · sent `발송 완료 · MM-DD HH:mm`.
- `size_label`: MB 반올림 정수 + `MB` (1MB 미만은 `KB`).
- `verdict` 카드: `verdicts` 행 → payload. `basis.precedents[]`에서 `body_text` 제거, `canCreateReport=true`, `disclaimer` 고정.
- `rebuttal_draft`/G-2: `can_send = is_email(recipient) and bool(claim_number)`, `blocked_by`는 부족한 필드명. `subject_auto`면 `과실비율 재검토 요청 (접수번호 {claimNumber})`, 접수번호 없으면 `(접수번호는 아직 안 넣었어요)`.

---

## 10. 설정 (`.env.example`)

```
APP_ENV=dev                      dev|prod
DATABASE_URL=postgresql+asyncpg://cardefender:cardefender@localhost:5432/cardefender
JWT_SECRET=change-me
CORS_ORIGINS=http://localhost:5173
FRONT_BASE_URL=http://localhost:5173
AGENT_IMPL=app.agent.mock:MockAgent
STORAGE_BACKEND=local            local|s3
STORAGE_LOCAL_DIR=./data
S3_BUCKET= · S3_REGION= · AWS 자격은 표준 환경 변수/인스턴스 롤
MAIL_BACKEND=mock                mock|smtp
SMTP_HOST= · SMTP_PORT=587 · SMTP_USER= · SMTP_PASSWORD= · SMTP_STARTTLS=true
MAIL_FROM=no-reply@cardefender.kr
FFPROBE_BIN=ffprobe
```

---

## 11. 테스트 전략

- pytest + pytest-asyncio + httpx `ASGITransport`. 
- 테스트 DB는 SQLite(aiosqlite) 파일 하나를 테스트마다 새로 만든다. 모델은 두 방언에서 같은 타입만 쓴다. Postgres 특정 기능(배열·JSONB 연산자)은 쓰지 않는다.
- 픽스처: `app`(Mock agent · mock mailer · tmp 스토리지) · `client` · `user`(가입+토큰) · `case` · `case_with_video` · `judged_case`.
- Job 테스트는 `runner`를 직접 await하거나 `asyncio.sleep(0)`로 태스크를 진행시킨다. 헬퍼 `await wait_jobs()`가 활성 태스크를 모두 기다린다.
- SSE 테스트는 허브를 직접 구독해 이벤트 순서를 검증한다. 엔드포인트 테스트는 첫 이벤트(`connected`)와 재전송만 확인한다.
- 각 기능은 실패 테스트 → 구현 → 통과 순서(TDD). 커밋 단위는 통과하는 기능 하나.
- Postgres 통합 확인은 `docker compose up`으로 실행해 Alembic 마이그레이션과 시드가 도는지 수동 검증.

---

## 12. 배포

- `Dockerfile`: `python:3.11-slim` + `ffmpeg` 설치 + 의존성 + `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`. 시작 전 `alembic upgrade head`.
- `docker-compose.yml`: `app`(8000) · `db`(postgres:16, 볼륨) · `nginx`(80/443, `client_max_body_size 256m`, `/api/` → app, SSE용 `proxy_buffering off`). 로컬 개발은 `docker compose up db`만 띄우고 앱은 venv에서 실행.
- 영상·PDF 로컬 스토리지는 호스트 볼륨(`./data`)에 마운트. S3로 바꿀 땐 환경 변수만.
- `GET /health`는 DB `SELECT 1` · 스토리지 쓰기 가능 여부 · Agent import 성공 · 메일 백엔드 설정 여부를 확인해 하나라도 실패면 `503 degraded`.

---

## 13. 구현 순서 (계획서에서 세분화)

1. 골격: pyproject · 설정 · DB · 에러 카탈로그 · 헬스체크 · Alembic 초기 마이그레이션 · Docker
2. 인증: 회원가입 · 로그인 · refresh · 로그아웃 · me · 이메일 확인 · 재설정 · 온보딩 · 약관
3. 사건: 생성(guide 카드) · 목록 · 조회(presenter) · 이름 변경 · 삭제
4. SSE 허브 + events 엔드포인트
5. Job 실행기 + Agent 계약 + MockAgent
6. 영상: 업로드 · 메타 · 스트리밍 · analysis Job
7. 채팅: 메시지 목록 · 보내기 · chat 흐름 · verdict Job · 판정 조회 · 심의사례
8. 경위서: 초안 Job · 버전 · 전문 · 다시 쓰기 · PDF 생성/다운로드
9. 반박의견서: 초안 Job · 조회 · 수정 · 발송(SMTP·멱등) · 발송 기록
10. 시드 · README · 배포 점검
