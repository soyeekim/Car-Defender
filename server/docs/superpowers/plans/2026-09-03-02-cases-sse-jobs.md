# 카-디펜더 백엔드 2/4 — 데이터 모델·사건·SSE·Job·Agent 계약 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 나머지 도메인 테이블 전부, 사건 API 5개(B-1~B-5), 메시지 목록(C-1), SSE 채널(B-6), Job 실행기, Agent 계약과 MockAgent를 만든다. 계획 1이 끝난 상태에서 시작한다.

**Architecture:** `services/cases.py`가 사건 로딩·메시지 저장·`case.updated` 송출의 단일 창구다. `presenters.py`는 DB 행 → 응답 dict 순수 함수. `sse/hub.py`는 메모리 허브, `jobs/runner.py`는 asyncio 태스크 실행기. `agent/base.py`가 AI 담당과의 계약이다.

**Tech Stack:** 계획 1과 동일. 추가 없음.

**Spec:** `docs/superpowers/specs/2026-09-03-backend-design.md` · `../금융 ai 디자인/20_API명세서_v2.md`

## Global Constraints

- 계획 1의 Global Constraints 전부 적용.
- 카드 payload 계약은 명세 §4. 응답 dict의 키는 camelCase 문자열 리터럴로 직접 쓴다 (presenter는 Pydantic 없이 dict 반환).
- 사건당 동시 Job 1개. Job 종류 `analysis · verdict · report · rebuttal`. 상태 `queued · running · succeeded · failed`.
- SSE 이벤트 5종 `connected · message.created · message.updated · case.updated · rebuttal.sent`. 모든 이벤트에 ULID `id:`.
- 커밋 형식·트레일러 금지 규칙 동일.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `app/models/case.py` `message.py` `video.py` `analysis.py` `verdict.py` `report.py` `rebuttal.py` `send_log.py` `job.py` | 테이블 |
| `alembic/versions/0002_domain_tables.py` | 마이그레이션 |
| `app/content/texts.py` | guide 카드 · 고정 안내문 · 고지 문구 |
| `app/services/presenters.py` | 라벨·stages·응답 dict |
| `app/services/cases.py` | `CaseBundle` · 사건 CRUD · 메시지 저장 · `publish_case_updated` |
| `app/sse/hub.py` · `app/api/events.py` | SSE 허브 · 엔드포인트 |
| `app/jobs/runner.py` | `JobRunner` |
| `app/agent/base.py` · `loader.py` · `mock.py` | 계약 · 로더 · Mock |
| `app/api/cases.py` · `app/api/messages.py` | 사건 · 메시지 목록 라우터 |
| `docs/agent-interface.md` | AI 담당용 계약서 |
| `tests/...` | 각 Task의 테스트 |

---

### Task 1: 도메인 테이블 9개와 마이그레이션 0002

**Files:**
- Create: `app/models/case.py`, `app/models/message.py`, `app/models/video.py`, `app/models/analysis.py`, `app/models/verdict.py`, `app/models/report.py`, `app/models/rebuttal.py`, `app/models/send_log.py`, `app/models/job.py`, `alembic/versions/0002_domain_tables.py`, `tests/test_models.py`
- Modify: `app/models/__init__.py`

**Interfaces:**
- Produces 모델 (컬럼명은 아래 코드가 정본): `Case` · `Message` · `Video` · `Analysis` · `Verdict` · `Report` · `ReportPdf` · `Rebuttal` · `SendLog` · `Job`
- 상수: `CASE_STATUSES = ("intake","analyzing","needs_review","judged","sent","closed")` · `JOB_KINDS = ("analysis","verdict","report","rebuttal")` · `JOB_STATUSES = ("queued","running","succeeded","failed")`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_models.py`

```python
from sqlalchemy import select

from app.clock import now_utc
from app.db import session_scope
from app.ids import new_id
from app.models import Case, Job, Message, SendLog, User, Verdict


async def _user(db):
    u = User(id=new_id(), email=f"{new_id()}@t.co", password_hash="x", created_at=now_utc())
    db.add(u)
    await db.flush()
    return u


async def test_case_cascade_deletes_children_but_keeps_send_log(app):
    async with session_scope() as db:
        u = await _user(db)
        c = Case(id=new_id(), user_id=u.id, title="새 사건", status="intake", created_at=now_utc(), updated_at=now_utc())
        db.add(c)
        db.add(Message(id=new_id(), case_id=c.id, role="assistant", type="guide", payload={"text": "hi"}, created_at=now_utc()))
        db.add(Verdict(id=new_id(), case_id=c.id, version=1, ratio_mine=0, ratio_other=100, summary="s", basis={}, is_active=True, created_at=now_utc()))
        db.add(Job(id=new_id(), case_id=c.id, kind="analysis", status="succeeded", started_at=now_utc()))
        db.add(SendLog(id=new_id(), case_id=c.id, rebuttal_id="r", idempotency_key="k", sent_at=now_utc(), from_email="a@b.co", recipient="c@d.co", subject="s", attachment_names=[], result="sent"))
        await db.commit()
        case_id = c.id

    async with session_scope() as db:
        c = await db.get(Case, case_id)
        await db.delete(c)
        await db.commit()

    async with session_scope() as db:
        assert (await db.execute(select(Message))).scalars().all() == []
        assert (await db.execute(select(Verdict))).scalars().all() == []
        assert (await db.execute(select(Job))).scalars().all() == []
        assert len((await db.execute(select(SendLog))).scalars().all()) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 모델 작성**

`app/models/case.py`:

```python
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

CASE_STATUSES = ("intake", "analyzing", "needs_review", "judged", "sent", "closed")


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(26), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(60), nullable=False, default="새 사건")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="intake")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    messages = relationship("Message", cascade="all, delete-orphan", passive_deletes=True)
    videos = relationship("Video", cascade="all, delete-orphan", passive_deletes=True)
    analysis = relationship("Analysis", cascade="all, delete-orphan", passive_deletes=True, uselist=False)
    verdicts = relationship("Verdict", cascade="all, delete-orphan", passive_deletes=True)
    reports = relationship("Report", cascade="all, delete-orphan", passive_deletes=True)
    rebuttal = relationship("Rebuttal", cascade="all, delete-orphan", passive_deletes=True, uselist=False)
    jobs = relationship("Job", cascade="all, delete-orphan", passive_deletes=True)
```

`app/models/message.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user | assistant
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/video.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False, default="video/mp4")
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict | None] = mapped_column(JSON)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/analysis.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Analysis(Base):
    __tablename__ = "analyses"

    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    facts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/verdict.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Verdict(Base):
    __tablename__ = "verdicts"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    ratio_mine: Mapped[int] = mapped_column(Integer, nullable=False)
    ratio_other: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text)
    opponent_claim: Mapped[dict | None] = mapped_column(JSON)
    basis: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/report.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    caveat: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision_request: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pdf = relationship("ReportPdf", cascade="all, delete-orphan", passive_deletes=True, uselist=False)


class ReportPdf(Base):
    __tablename__ = "report_pdfs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(26), ForeignKey("reports.id", ondelete="CASCADE"), unique=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/rebuttal.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Rebuttal(Base):
    __tablename__ = "rebuttals"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), unique=True)
    recipient: Mapped[str | None] = mapped_column(String(320))
    claim_number: Mapped[str | None] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")  # draft | sent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`app/models/send_log.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SendLog(Base):
    __tablename__ = "send_logs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), index=True)  # FK 없음
    rebuttal_id: Mapped[str] = mapped_column(String(26))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    attachment_names: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result: Mapped[str] = mapped_column(String(10), nullable=False)  # sent | failed
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
```

`app/models/job.py`:

```python
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

JOB_KINDS = ("analysis", "verdict", "report", "rebuttal")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(26), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")
    error: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`app/models/__init__.py`:

```python
from app.models.analysis import Analysis
from app.models.auth_token import PasswordResetToken, RefreshToken
from app.models.case import CASE_STATUSES, Case
from app.models.job import JOB_KINDS, JOB_STATUSES, Job
from app.models.message import Message
from app.models.rebuttal import Rebuttal
from app.models.report import Report, ReportPdf
from app.models.send_log import SendLog
from app.models.user import User
from app.models.verdict import Verdict
from app.models.video import Video

__all__ = [
    "User", "RefreshToken", "PasswordResetToken", "Case", "CASE_STATUSES", "Message", "Video",
    "Analysis", "Verdict", "Report", "ReportPdf", "Rebuttal", "SendLog", "Job", "JOB_KINDS", "JOB_STATUSES",
]
```

SQLite에서 `ON DELETE CASCADE`가 동작하려면 외래키를 켜야 한다. `app/db.py`의 `configure_database` 끝에 추가:

```python
from sqlalchemy import event

    if url.startswith("sqlite"):
        @event.listens_for(_engine.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 마이그레이션 0002 생성**

```bash
DATABASE_URL=sqlite+aiosqlite:///./data/alembic-scratch.db .venv/Scripts/python -m alembic upgrade head
DATABASE_URL=sqlite+aiosqlite:///./data/alembic-scratch.db .venv/Scripts/python -m alembic revision --autogenerate -m "domain tables" --rev-id 0002
DATABASE_URL=sqlite+aiosqlite:///./data/alembic-scratch.db .venv/Scripts/python -m alembic upgrade head
rm data/alembic-scratch.db
```

`alembic/versions/0002_domain_tables.py`에 9개 테이블 `create_table`이 있는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add app/models app/db.py alembic/versions/0002_domain_tables.py tests/test_models.py
git commit -m "feat(db): 사건·메시지·영상·판정·서류·발송·Job 테이블과 마이그레이션"
```

---

### Task 2: 고정 문구와 presenter

**Files:**
- Create: `app/content/texts.py`, `app/services/presenters.py`, `tests/test_presenters.py`

**Interfaces:**
- Produces `texts.py`: `DISCLAIMER` · `GUIDE_CARD` (dict) · `VIDEO_NOTICE` · `LIMITS_LABEL` · `NEED_DESCRIPTION_TEXT` · `VERDICT_PLACEHOLDER` · `STATUS_LABELS` · `ANALYZING_TEXT`
- Produces `presenters.py`: `compute_stages(status, active_job_kind, has_report, rebuttal_status) -> dict` · `size_label(n) -> str` · `ordinal_label(n) -> str` · `report_doc(latest_report) -> dict` · `rebuttal_doc(has_verdict, has_report, rebuttal, sent_at) -> dict` · `case_detail(bundle) -> dict` · `case_list_item(case) -> dict` · `message_dict(msg) -> dict` · `verdict_summary(verdict) -> dict | None` · `verdict_payload(verdict) -> dict`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_presenters.py`

```python
from datetime import datetime, timezone

from app.services.presenters import compute_stages, ordinal_label, rebuttal_doc, report_doc, size_label


def s(status, job=None, report=False, reb=None):
    return {k: v["state"] for k, v in compute_stages(status, job, report, reb).items()}


def test_stages_table_from_spec_7_2():
    assert s("intake") == {"analysis": "pending", "fault_ratio": "pending", "report": "pending", "rebuttal": "pending"}
    assert s("analyzing", "analysis")["analysis"] == "in_progress"
    assert s("needs_review") == {"analysis": "in_progress", "fault_ratio": "pending", "report": "pending", "rebuttal": "pending"}
    assert s("needs_review", "verdict") == {"analysis": "done", "fault_ratio": "in_progress", "report": "pending", "rebuttal": "pending"}
    assert s("judged") == {"analysis": "done", "fault_ratio": "done", "report": "pending", "rebuttal": "pending"}
    assert s("judged", report=True) == {"analysis": "done", "fault_ratio": "done", "report": "done", "rebuttal": "pending"}
    assert s("judged", report=True, reb="draft")["rebuttal"] == "in_progress"
    assert s("judged", "verdict", report=True, reb="draft") == {"analysis": "done", "fault_ratio": "done", "report": "done", "rebuttal": "in_progress"}
    assert s("sent", report=True, reb="sent") == {"analysis": "done", "fault_ratio": "done", "report": "done", "rebuttal": "done"}
    assert s("closed", report=True, reb="sent")["rebuttal"] == "done"


def test_size_label():
    assert size_label(18874368) == "18MB"
    assert size_label(184320) == "180KB"
    assert size_label(0) == "0KB"


def test_ordinal_label():
    assert ordinal_label(1) == "첫 번째 버전"
    assert ordinal_label(2) == "두 번째 버전"
    assert ordinal_label(5) == "다섯 번째 버전"
    assert ordinal_label(6) == "6번째 버전"


class R:
    version = 2
    page_count = 2


def test_report_doc():
    assert report_doc(None) == {"exists": False, "label": "아직 없음", "version": None, "pageCount": None}
    assert report_doc(R()) == {"exists": True, "label": "두 번째 버전 · 2장", "version": 2, "pageCount": 2}


class Reb:
    def __init__(self, status):
        self.status = status


def test_rebuttal_doc_labels():
    assert rebuttal_doc(False, False, None, None) == {"exists": False, "locked": True, "label": "잠김 · 판정과 경위서가 먼저예요"}
    assert rebuttal_doc(True, False, None, None)["label"] == "잠김 · 경위서를 만들면 열려요"
    assert rebuttal_doc(True, True, None, None) == {"exists": False, "locked": False, "label": "이제 만들 수 있어요"}
    assert rebuttal_doc(True, True, Reb("draft"), None) == {"exists": True, "locked": False, "label": "작성 중"}
    sent_at = datetime(2026, 8, 25, 5, 32, tzinfo=timezone.utc)
    assert rebuttal_doc(True, True, Reb("sent"), sent_at)["label"] == "발송 완료 · 08-25 14:32"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_presenters.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: app/content/texts.py**

```python
DISCLAIMER = "본 결과는 참고용이며, 최종 과실비율은 보험사·분쟁심의위원회 결정에 따릅니다."
VIDEO_NOTICE = "영상은 이 사건 처리에만 쓰이며, 사건을 지우면 함께 지워집니다."
LIMITS_LABEL = "mp4 권장 · 최대 200MB · 3분 이내"
VERDICT_PLACEHOLDER = "아직 판정 전이에요."
NEED_DESCRIPTION_TEXT = "영상 잘 받았어요. 사고 상황을 한두 문장으로 알려 주시면 바로 분석을 시작할게요."
UPLOAD_CTA = {"type": "upload_video", "label": "영상 올리기"}

GUIDE_CARD = {
    "text": "안녕하세요, 카-디펜더예요. 사고 상황을 말로 설명하고, 블랙박스 영상을 올려 주세요. 둘이 모이면 분석이 자동으로 시작돼요.",
    "notice": VIDEO_NOTICE,
    "limitsLabel": LIMITS_LABEL,
}

STATUS_LABELS = {
    "intake": "접수중",
    "analyzing": "분석중",
    "needs_review": "확인 필요",
    "judged": "판정 완료",
    "sent": "발송 완료",
    "closed": "종결",
}

REBUTTAL_LOCKED_CARD = {
    "text": "반박의견서에는 사건경위서가 첨부돼요. 먼저 경위서를 만들면 보낼 수 있어요.",
    "buttonLabel": "반박의견서 보내기",
    "buttonHint": "경위서를 만들면 열려요",
}

SENT_NOTICE = "보낸 문서는 그대로 보관되고 수정할 수 없어요. 다시 보내려면 새 문서로 만들어요."
SENT_NEXT_STEPS = [
    "보험사 회신을 기다려요 (보통 3~7일)",
    "회신이 오면 채팅에 붙여넣어 주세요 — 함께 따져 볼게요",
    "받아들여지지 않으면 내 보험사에 분쟁심의 청구를 요청하는 방법을 안내해 드려요",
]

CLAIM_NUMBER_HINT = "보험사 접수 문자나 메일에 있어요 · 이걸 넣어야 보험사가 사건을 찾을 수 있어요"
CLAIM_NUMBER_HINT_SHORT = "보험사 접수 문자나 메일에 있어요"
RECIPIENT_PLACEHOLDER = "아직 안 정했어요"
ATTACHMENT_NOTICE_CARD = "영상에는 다른 차량 번호판이 담길 수 있어요 · 다음 창에서 ×로 뺄 수 있어요"
ATTACHMENT_NOTICE_FULL = "영상에는 상대 차량 번호판 등 다른 사람의 정보가 담길 수 있어요. 보험사 담당자에게만 보내 주세요. ×를 누르면 빼고 보낼 수 있어요."
ATTACHMENT_TOO_LARGE_NOTE = "용량이 커서 첨부할 수 없어요"
REPORT_INTRO = "채팅에서 나눈 대화와 영상 분석 결과를 바탕으로 쓴 {label}이에요."
REPORT_REVISION_PLACEHOLDER = "예: 2번을 더 간단하게"
```

- [ ] **Step 4: app/services/presenters.py**

```python
from dataclasses import dataclass
from datetime import datetime

from app.clock import kst_date_label, kst_datetime_label, to_kst_iso
from app.content.texts import DISCLAIMER, STATUS_LABELS, VERDICT_PLACEHOLDER
from app.models import Case, Job, Message, Rebuttal, Report, Verdict, Video

ORDINALS = {1: "첫", 2: "두", 3: "세", 4: "네", 5: "다섯"}


def ordinal_label(n: int) -> str:
    return f"{ORDINALS[n]} 번째 버전" if n in ORDINALS else f"{n}번째 버전"


def size_label(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{round(n / (1024 * 1024))}MB"
    return f"{round(n / 1024)}KB"


def compute_stages(status: str, active_job_kind: str | None, has_report: bool, rebuttal_status: str | None) -> dict:
    analysis = fault = report = rebuttal = "pending"
    if status == "analyzing":
        analysis = "in_progress"
    elif status == "needs_review":
        if active_job_kind == "verdict":
            analysis, fault = "done", "in_progress"
        else:
            analysis = "in_progress"
    elif status in ("judged", "sent", "closed"):
        analysis = fault = "done"
        report = "done" if has_report else "pending"
        if status in ("sent", "closed"):
            report, rebuttal = "done", "done"
        elif rebuttal_status == "draft":
            rebuttal = "in_progress"
    return {
        "analysis": {"state": analysis},
        "fault_ratio": {"state": fault},
        "report": {"state": report},
        "rebuttal": {"state": rebuttal},
    }


def report_doc(latest: Report | None) -> dict:
    if latest is None:
        return {"exists": False, "label": "아직 없음", "version": None, "pageCount": None}
    return {
        "exists": True,
        "label": f"{ordinal_label(latest.version)} · {latest.page_count}장",
        "version": latest.version,
        "pageCount": latest.page_count,
    }


def rebuttal_doc(has_verdict: bool, has_report: bool, rebuttal: Rebuttal | None, sent_at: datetime | None) -> dict:
    if rebuttal is not None and rebuttal.status == "sent":
        label = "발송 완료" + (f" · {kst_datetime_label(sent_at)}" if sent_at else "")
        return {"exists": True, "locked": False, "label": label}
    if rebuttal is not None:
        return {"exists": True, "locked": False, "label": "작성 중"}
    if not has_verdict:
        return {"exists": False, "locked": True, "label": "잠김 · 판정과 경위서가 먼저예요"}
    if not has_report:
        return {"exists": False, "locked": True, "label": "잠김 · 경위서를 만들면 열려요"}
    return {"exists": False, "locked": False, "label": "이제 만들 수 있어요"}


def verdict_summary(v: Verdict | None) -> dict | None:
    if v is None:
        return None
    return {"verdictId": v.id, "version": v.version, "ratio": {"mine": v.ratio_mine, "other": v.ratio_other}}


def verdict_payload(v: Verdict) -> dict:
    basis = dict(v.basis or {})
    precedents = [{"id": p.get("id"), "title": p.get("title")} for p in basis.get("precedents", [])]
    return {
        "verdictId": v.id,
        "version": v.version,
        "changeReason": v.change_reason,
        "ratio": {"mine": v.ratio_mine, "other": v.ratio_other},
        "summary": v.summary,
        "opponentClaim": v.opponent_claim,
        "basis": {"chart": basis.get("chart"), "precedents": precedents},
        "canCreateReport": True,
        "disclaimer": DISCLAIMER,
    }


def video_summary(video: Video | None) -> dict | None:
    if video is None:
        return None
    return {"id": video.id, "filename": video.filename, "durationSec": video.duration_sec, "sizeLabel": size_label(video.size_bytes)}


def job_summary(job: Job | None) -> dict | None:
    if job is None:
        return None
    return {"jobId": job.id, "kind": job.kind, "status": job.status}


@dataclass
class CaseBundle:
    case: Case
    video: Video | None
    verdict: Verdict | None
    latest_report: Report | None
    rebuttal: Rebuttal | None
    active_job: Job | None
    sent_at: datetime | None


def case_detail(b: CaseBundle) -> dict:
    c = b.case
    subtitle = f"접수 {kst_date_label(c.created_at)}" + (" · 블랙박스 1건" if b.video else "")
    has_report = b.latest_report is not None
    return {
        "id": c.id,
        "title": c.title,
        "status": c.status,
        "statusLabel": STATUS_LABELS[c.status],
        "subtitle": subtitle,
        "stages": compute_stages(c.status, b.active_job.kind if b.active_job else None, has_report, b.rebuttal.status if b.rebuttal else None),
        "verdict": verdict_summary(b.verdict),
        "verdictPlaceholder": None if b.verdict else VERDICT_PLACEHOLDER,
        "documents": {
            "report": report_doc(b.latest_report),
            "rebuttal": rebuttal_doc(b.verdict is not None, has_report, b.rebuttal, b.sent_at),
        },
        "video": video_summary(b.video),
        "activeJob": job_summary(b.active_job),
        "disclaimer": DISCLAIMER,
        "createdAt": to_kst_iso(c.created_at),
        "updatedAt": to_kst_iso(c.updated_at),
    }


def case_list_item(c: Case) -> dict:
    return {"id": c.id, "title": c.title, "status": c.status, "statusLabel": STATUS_LABELS[c.status], "updatedAt": to_kst_iso(c.updated_at)}


def message_dict(m: Message) -> dict:
    return {"id": m.id, "caseId": m.case_id, "role": m.role, "type": m.type, "payload": m.payload, "createdAt": to_kst_iso(m.created_at)}
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_presenters.py -v`
Expected: PASS (6)

- [ ] **Step 6: 커밋**

```bash
git add app/content/texts.py app/services/presenters.py tests/test_presenters.py
git commit -m "feat(cases): 고정 문구와 현황판 응답 presenter"
```

---

### Task 3: SSE 허브

**Files:**
- Create: `app/sse/__init__.py`, `app/sse/hub.py`, `tests/test_sse_hub.py`

**Interfaces:**
- Produces: `Hub.publish(case_id, event, data: dict) -> str(event_id)` · `Hub.subscribe(case_id, last_event_id=None) -> AsyncIterator[str]` (SSE 프레임 문자열; 첫 프레임은 `connected`) · `Hub.subscriber_count(case_id)` · `Hub.reset()` · 전역 `hub` · `format_frame(event_id, event, data) -> str` · `KEEPALIVE_SECONDS = 15` · `BUFFER_SECONDS = 300`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_sse_hub.py`

```python
import asyncio
import json

from app.sse.hub import Hub, format_frame


def parse(frame: str) -> tuple[str, str, dict]:
    lines = [ln for ln in frame.strip().split("\n") if ln]
    d = {}
    for ln in lines:
        k, _, v = ln.partition(": ")
        d[k] = v
    return d["id"], d["event"], json.loads(d["data"])


def test_format_frame():
    f = format_frame("01A", "message.created", {"a": 1})
    assert f == 'id: 01A\nevent: message.created\ndata: {"a": 1}\n\n'


async def test_subscribe_gets_connected_then_live_events():
    hub = Hub()
    it = hub.subscribe("c1")
    first = await it.__anext__()
    _, ev, data = parse(first)
    assert ev == "connected" and data["caseId"] == "c1" and "serverTime" in data

    hub.publish("c1", "case.updated", {"id": "c1"})
    hub.publish("c2", "case.updated", {"id": "c2"})  # 다른 사건
    _, ev, data = parse(await it.__anext__())
    assert ev == "case.updated" and data["id"] == "c1"
    await it.aclose()
    assert hub.subscriber_count("c1") == 0


async def test_replay_after_last_event_id():
    hub = Hub()
    a = hub.publish("c1", "message.created", {"n": 1})
    b = hub.publish("c1", "message.created", {"n": 2})
    c = hub.publish("c1", "message.created", {"n": 3})
    assert a < b < c
    it = hub.subscribe("c1", last_event_id=a)
    await it.__anext__()  # connected
    _, _, d2 = parse(await it.__anext__())
    _, _, d3 = parse(await it.__anext__())
    assert (d2["n"], d3["n"]) == (2, 3)
    await it.aclose()


async def test_buffer_prunes_old_events():
    now = [1000.0]
    hub = Hub(clock=lambda: now[0])
    old = hub.publish("c1", "x", {"n": 1})
    now[0] += 301
    hub.publish("c1", "x", {"n": 2})
    it = hub.subscribe("c1", last_event_id=old)
    await it.__anext__()
    _, _, d = parse(await it.__anext__())
    assert d["n"] == 2
    await it.aclose()


async def test_keepalive_when_idle():
    hub = Hub(keepalive_seconds=0.01)
    it = hub.subscribe("c1")
    await it.__anext__()
    frame = await asyncio.wait_for(it.__anext__(), timeout=1)
    assert frame == ": keepalive\n\n"
    await it.aclose()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_sse_hub.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: app/sse/hub.py**

```python
import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable

from app.clock import now_utc, to_kst_iso
from app.ids import new_id

KEEPALIVE_SECONDS = 15
BUFFER_SECONDS = 300


def format_frame(event_id: str, event: str, data: dict) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Hub:
    def __init__(self, clock: Callable[[], float] = time.monotonic, keepalive_seconds: float = KEEPALIVE_SECONDS):
        self._clock = clock
        self._keepalive = keepalive_seconds
        self._buffers: dict[str, deque[tuple[str, float, str]]] = defaultdict(deque)
        self._subs: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    def _prune(self, case_id: str) -> None:
        buf = self._buffers[case_id]
        cutoff = self._clock() - BUFFER_SECONDS
        while buf and buf[0][1] < cutoff:
            buf.popleft()

    def publish(self, case_id: str, event: str, data: dict) -> str:
        event_id = new_id()
        frame = format_frame(event_id, event, data)
        self._prune(case_id)
        self._buffers[case_id].append((event_id, self._clock(), frame))
        for q in list(self._subs.get(case_id, ())):
            q.put_nowait(frame)
        return event_id

    def subscriber_count(self, case_id: str) -> int:
        return len(self._subs.get(case_id, ()))

    def reset(self) -> None:
        self._buffers.clear()
        self._subs.clear()

    async def subscribe(self, case_id: str, last_event_id: str | None = None) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._subs[case_id].add(q)
        try:
            yield format_frame(new_id(), "connected", {"caseId": case_id, "serverTime": to_kst_iso(now_utc())})
            if last_event_id:
                self._prune(case_id)
                for event_id, _, frame in list(self._buffers[case_id]):
                    if event_id > last_event_id:
                        yield frame
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=self._keepalive)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subs[case_id].discard(q)
            if not self._subs[case_id]:
                del self._subs[case_id]


hub = Hub()
```

`app/sse/__init__.py`는 빈 파일.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_sse_hub.py -v`
Expected: PASS (5)

- [ ] **Step 5: 커밋**

```bash
git add app/sse tests/test_sse_hub.py
git commit -m "feat(sse): 사건별 구독 큐와 5분 링 버퍼를 가진 SSE 허브"
```

---

### Task 4: 사건 서비스와 사건 API (B-1 ~ B-5)

**Files:**
- Create: `app/services/cases.py`, `app/api/cases.py`, `app/schemas/cases.py`, `tests/api/test_cases.py`
- Modify: `app/main.py`, `tests/conftest.py`

**Interfaces:**
- Produces 서비스 (`app/services/cases.py`):
  - `load_bundle(db, case_id) -> CaseBundle`
  - `get_owned_case(db, user, case_id) -> Case` (없으면 `NOT_FOUND`, 남의 것이면 `FORBIDDEN`)
  - `create_case(db, user) -> Case` (guide 카드 삽입, 이벤트 송출 없음)
  - `list_cases(db, user) -> list[Case]` (updated_at desc)
  - `rename_case(db, case, title) -> Case`
  - `delete_case(db, case) -> None` (스토리지 삭제는 계획 3에서 `storage.delete` 호출 추가)
  - `touch(case)` (updated_at 갱신)
  - `set_status(case, status)`
  - `add_message(db, case_id, role, type_, payload, *, publish=True) -> Message` (commit + `message.created`)
  - `update_message(db, message, payload) -> Message` (commit + `message.updated`)
  - `publish_case_updated(db, case_id) -> dict` (bundle 로드 → `case.updated` 송출 → dict 반환)
  - `active_job(db, case_id) -> Job | None`
  - `active_verdict(db, case_id) -> Verdict | None` · `latest_report(db, case_id) -> Report | None` · `get_video(db, case_id) -> Video | None` · `get_rebuttal(db, case_id) -> Rebuttal | None` · `get_analysis(db, case_id) -> Analysis | None`
- Produces 의존성 `owned_case(case_id, user=Depends(current_user), db=Depends(get_db)) -> Case` in `app/deps.py`
- 픽스처 `case_id(client, auth_headers) -> str`

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_cases.py`

```python
from app.sse.hub import hub


async def test_create_case_returns_detail_and_inserts_guide(client, auth_headers):
    res = await client.post("/cases", headers=auth_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["title"] == "새 사건" and body["status"] == "intake" and body["statusLabel"] == "접수중"
    assert body["subtitle"].startswith("접수 ")
    assert body["stages"]["analysis"] == {"state": "pending"}
    assert body["verdict"] is None and body["verdictPlaceholder"] == "아직 판정 전이에요."
    assert body["documents"]["report"]["label"] == "아직 없음"
    assert body["documents"]["rebuttal"]["label"] == "잠김 · 판정과 경위서가 먼저예요"
    assert body["video"] is None and body["activeJob"] is None

    msgs = (await client.get(f"/cases/{body['id']}/messages", headers=auth_headers)).json()
    assert len(msgs["items"]) == 1
    assert msgs["items"][0]["type"] == "guide" and msgs["items"][0]["role"] == "assistant"
    assert msgs["items"][0]["payload"]["limitsLabel"] == "mp4 권장 · 최대 200MB · 3분 이내"


async def test_list_cases_newest_first(client, auth_headers):
    a = (await client.post("/cases", headers=auth_headers)).json()["id"]
    b = (await client.post("/cases", headers=auth_headers)).json()["id"]
    await client.patch(f"/cases/{a}", json={"title": "나중에 고침"}, headers=auth_headers)
    res = await client.get("/cases", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert [i["id"] for i in body["items"]] == [a, b]
    assert body["hasMore"] is False and body["nextCursor"] is None
    assert set(body["items"][0]) == {"id", "title", "status", "statusLabel", "updatedAt"}


async def test_list_empty(client, auth_headers):
    assert (await client.get("/cases", headers=auth_headers)).json()["items"] == []


async def test_get_case_forbidden_for_other_user(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    other = await client.post("/auth/signup", json={
        "email": "other@example.com", "password": "carguard12", "passwordConfirm": "carguard12",
        "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True}})
    h = {"Authorization": f"Bearer {other.json()['accessToken']}"}
    res = await client.get(f"/cases/{case_id}", headers=h)
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"


async def test_get_case_not_found(client, auth_headers):
    res = await client.get("/cases/01JNOPE00000000000000000000", headers=auth_headers)
    assert res.status_code == 404


async def test_rename_validation_and_success(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    res = await client.patch(f"/cases/{case_id}", json={"title": "   "}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_FAILED"
    res = await client.patch(f"/cases/{case_id}", json={"title": "x" * 61}, headers=auth_headers)
    assert res.status_code == 422
    res = await client.patch(f"/cases/{case_id}", json={"title": "논현사거리 이륜차 충돌"}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["title"] == "논현사거리 이륜차 충돌"


async def test_delete_case(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    res = await client.delete(f"/cases/{case_id}", headers=auth_headers)
    assert res.status_code == 204
    assert (await client.get(f"/cases/{case_id}", headers=auth_headers)).status_code == 404


async def test_rename_publishes_case_updated(client, auth_headers):
    case_id = (await client.post("/cases", headers=auth_headers)).json()["id"]
    it = hub.subscribe(case_id)
    await it.__anext__()
    await client.patch(f"/cases/{case_id}", json={"title": "바뀜"}, headers=auth_headers)
    frame = await it.__anext__()
    assert "event: case.updated" in frame and '"title": "바뀜"' in frame
    await it.aclose()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_cases.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 스키마** — `app/schemas/cases.py`

```python
from pydantic import field_validator

from app.schemas.base import CamelModel


class RenameCaseRequest(CamelModel):
    title: str

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("제목은 1~60자예요.")
        return v
```

- [ ] **Step 4: 서비스** — `app/services/cases.py`

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.content.texts import GUIDE_CARD
from app.errors import ApiError
from app.ids import new_id
from app.models import Analysis, Case, Job, Message, Rebuttal, Report, SendLog, User, Verdict, Video
from app.services.presenters import CaseBundle, case_detail, message_dict
from app.sse.hub import hub


async def get_owned_case(db: AsyncSession, user: User, case_id: str) -> Case:
    case = await db.get(Case, case_id)
    if case is None:
        raise ApiError("NOT_FOUND")
    if case.user_id != user.id:
        raise ApiError("FORBIDDEN")
    return case


def touch(case: Case) -> None:
    case.updated_at = now_utc()


def set_status(case: Case, status: str) -> None:
    case.status = status
    touch(case)


async def create_case(db: AsyncSession, user: User) -> Case:
    now = now_utc()
    case = Case(id=new_id(), user_id=user.id, title="새 사건", status="intake", created_at=now, updated_at=now)
    db.add(case)
    db.add(Message(id=new_id(), case_id=case.id, role="assistant", type="guide", payload=dict(GUIDE_CARD), created_at=now))
    await db.commit()
    return case


async def list_cases(db: AsyncSession, user: User) -> list[Case]:
    stmt = select(Case).where(Case.user_id == user.id).order_by(Case.updated_at.desc(), Case.id.desc())
    return list((await db.execute(stmt)).scalars().all())


async def rename_case(db: AsyncSession, case: Case, title: str) -> Case:
    case.title = title
    touch(case)
    await db.commit()
    return case


async def delete_case(db: AsyncSession, case: Case) -> None:
    await db.delete(case)
    await db.commit()


async def get_video(db: AsyncSession, case_id: str) -> Video | None:
    return (await db.execute(select(Video).where(Video.case_id == case_id))).scalar_one_or_none()


async def get_analysis(db: AsyncSession, case_id: str) -> Analysis | None:
    return await db.get(Analysis, case_id)


async def active_verdict(db: AsyncSession, case_id: str) -> Verdict | None:
    stmt = select(Verdict).where(Verdict.case_id == case_id, Verdict.is_active.is_(True)).order_by(Verdict.version.desc())
    return (await db.execute(stmt)).scalars().first()


async def latest_report(db: AsyncSession, case_id: str) -> Report | None:
    stmt = select(Report).where(Report.case_id == case_id).order_by(Report.version.desc())
    return (await db.execute(stmt)).scalars().first()


async def get_rebuttal(db: AsyncSession, case_id: str) -> Rebuttal | None:
    return (await db.execute(select(Rebuttal).where(Rebuttal.case_id == case_id))).scalar_one_or_none()


async def active_job(db: AsyncSession, case_id: str) -> Job | None:
    stmt = select(Job).where(Job.case_id == case_id, Job.status.in_(("queued", "running"))).order_by(Job.started_at.desc())
    return (await db.execute(stmt)).scalars().first()


async def last_sent_at(db: AsyncSession, case_id: str):
    stmt = select(SendLog.sent_at).where(SendLog.case_id == case_id, SendLog.result == "sent").order_by(SendLog.sent_at.desc())
    return (await db.execute(stmt)).scalars().first()


async def load_bundle(db: AsyncSession, case_id: str) -> CaseBundle:
    case = await db.get(Case, case_id)
    if case is None:
        raise ApiError("NOT_FOUND")
    return CaseBundle(
        case=case,
        video=await get_video(db, case_id),
        verdict=await active_verdict(db, case_id),
        latest_report=await latest_report(db, case_id),
        rebuttal=await get_rebuttal(db, case_id),
        active_job=await active_job(db, case_id),
        sent_at=await last_sent_at(db, case_id),
    )


async def publish_case_updated(db: AsyncSession, case_id: str) -> dict:
    body = case_detail(await load_bundle(db, case_id))
    hub.publish(case_id, "case.updated", body)
    return body


async def add_message(db: AsyncSession, case_id: str, role: str, type_: str, payload: dict, *, publish: bool = True) -> Message:
    msg = Message(id=new_id(), case_id=case_id, role=role, type=type_, payload=payload, created_at=now_utc())
    db.add(msg)
    case = await db.get(Case, case_id)
    if case is not None:
        touch(case)
    await db.commit()
    if publish:
        hub.publish(case_id, "message.created", message_dict(msg))
    return msg


async def update_message(db: AsyncSession, message: Message, payload: dict) -> Message:
    message.payload = payload
    await db.commit()
    hub.publish(message.case_id, "message.updated", message_dict(message))
    return message
```

`app/deps.py`에 추가:

```python
async def owned_case(case_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.services.cases import get_owned_case

    return await get_owned_case(db, user, case_id)
```

- [ ] **Step 5: 라우터** — `app/api/cases.py`

```python
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, owned_case
from app.models import Case, User
from app.schemas.cases import RenameCaseRequest
from app.services import cases as case_service
from app.services.presenters import case_detail, case_list_item

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    case = await case_service.create_case(db, user)
    return case_detail(await case_service.load_bundle(db, case.id))


@router.get("")
async def list_cases(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    items = [case_list_item(c) for c in await case_service.list_cases(db, user)]
    return {"items": items, "hasMore": False, "nextCursor": None}


@router.get("/{case_id}")
async def get_case(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return case_detail(await case_service.load_bundle(db, case.id))


@router.patch("/{case_id}")
async def rename_case(body: RenameCaseRequest, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    await case_service.rename_case(db, case, body.title)
    return await case_service.publish_case_updated(db, case.id)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)):
    await case_service.delete_case(db, case)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

`app/main.py`에 `cases.router` 등록. 테스트가 `GET /cases/{id}/messages`를 부르므로 Task 5의 메시지 목록도 필요하다 — 이 Task에서는 `test_create_case_returns_detail_and_inserts_guide`만 Task 5 뒤에 통과한다. 나머지 7개가 먼저 통과하는지 확인한다.

`tests/conftest.py`에 픽스처와 허브 초기화 추가:

```python
@pytest.fixture(autouse=True)
def _reset_hub():
    from app.sse.hub import hub

    hub.reset()
    yield
    hub.reset()


@pytest.fixture
async def case_id(client, auth_headers):
    res = await client.post("/cases", headers=auth_headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_cases.py -v`
Expected: 7 PASS, `test_create_case_returns_detail_and_inserts_guide` 1 FAIL (메시지 목록 404) — Task 5에서 통과

- [ ] **Step 7: 커밋**

```bash
git add app/services/cases.py app/api/cases.py app/schemas/cases.py app/deps.py app/main.py tests/conftest.py tests/api/test_cases.py
git commit -m "feat(cases): 사건 생성·목록·조회·이름 변경·삭제 API"
```

---

### Task 5: 메시지 목록 (C-1) · SSE 엔드포인트 (B-6)

**Files:**
- Create: `app/api/messages.py`, `app/api/events.py`, `tests/api/test_messages_list.py`, `tests/api/test_events.py`
- Modify: `app/main.py`

**Interfaces:**
- `GET /cases/{caseId}/messages?before=&limit=20` — 오래된 → 최신 순, `nextCursor`는 반환된 것 중 가장 오래된 ID (더 있으면), `hasMore`
- `GET /cases/{caseId}/events` — `Authorization: Bearer` **또는** 쿼리 `access_token` (EventSource는 헤더를 못 붙인다). `Last-Event-ID` 헤더 지원

- [ ] **Step 1: 실패하는 테스트**

`tests/api/test_messages_list.py`:

```python
from app.db import session_scope
from app.services.cases import add_message


async def test_messages_pagination_oldest_to_newest(client, auth_headers, case_id):
    async with session_scope() as db:
        for i in range(25):
            await add_message(db, case_id, "user", "text", {"text": f"m{i}"}, publish=False)
    res = await client.get(f"/cases/{case_id}/messages", headers=auth_headers)
    body = res.json()
    assert len(body["items"]) == 20 and body["hasMore"] is True
    texts = [m["payload"].get("text") for m in body["items"]]
    assert texts[-1] == "m24" and texts[0] == "m5"
    assert body["nextCursor"] == body["items"][0]["id"]

    res2 = await client.get(f"/cases/{case_id}/messages", params={"before": body["nextCursor"], "limit": 10}, headers=auth_headers)
    b2 = res2.json()
    assert [m["payload"].get("text") for m in b2["items"]][-1] == "m4"
    assert b2["items"][0]["type"] == "guide"
    assert b2["hasMore"] is False and b2["nextCursor"] is None


async def test_messages_limit_capped_at_50(client, auth_headers, case_id):
    res = await client.get(f"/cases/{case_id}/messages", params={"limit": 500}, headers=auth_headers)
    assert res.status_code == 200


async def test_messages_forbidden(client, auth_headers, case_id):
    res = await client.get(f"/cases/{case_id}/messages")
    assert res.status_code == 401
```

`tests/api/test_events.py` — httpx의 `ASGITransport`는 응답 본문을 전부 모은 뒤 돌려주므로 끝나지 않는 SSE 스트림에는 쓸 수 없다. ASGI 앱을 직접 호출해 청크를 하나씩 받는 프로브를 쓴다:

```python
import asyncio

from app.sse.hub import hub


class SseProbe:
    """앱을 ASGI로 직접 호출해 SSE 청크를 하나씩 받는다."""

    def __init__(self, app, path: str, headers: dict | None = None, query: str = ""):
        self.app, self.path, self.query = app, path, query
        self.headers = headers or {}
        self.status = None
        self.response_headers = {}
        self.chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._task = None

    async def _receive(self):
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.response_headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body" and message.get("body"):
            await self.chunks.put(message["body"])

    async def start(self):
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": self.path, "raw_path": self.path.encode(), "query_string": self.query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            "server": ("test", 80), "client": ("127.0.0.1", 1),
        }
        self._task = asyncio.create_task(self.app(scope, self._receive, self._send))
        return self

    async def next_frame(self, timeout: float = 5.0) -> str:
        return (await asyncio.wait_for(self.chunks.get(), timeout)).decode()

    async def close(self):
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass


async def test_events_stream_connected_then_event(app, auth_headers, case_id):
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", auth_headers).start()
    first = await probe.next_frame()
    assert probe.status == 200 and probe.response_headers["content-type"].startswith("text/event-stream")
    assert "event: connected" in first
    hub.publish(case_id, "case.updated", {"id": case_id})
    assert "event: case.updated" in await probe.next_frame()
    await probe.close()
    assert hub.subscriber_count(case_id) == 0


async def test_events_accepts_query_token(app, auth_headers, case_id):
    token = auth_headers["Authorization"].split()[1]
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", query=f"access_token={token}").start()
    assert (await probe.next_frame()).startswith("id: ")
    await probe.close()


async def test_events_replays_after_last_event_id(app, auth_headers, case_id):
    first_id = hub.publish(case_id, "case.updated", {"n": 1})
    hub.publish(case_id, "case.updated", {"n": 2})
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", {**auth_headers, "Last-Event-ID": first_id}).start()
    await probe.next_frame()  # connected
    assert '"n": 2' in await probe.next_frame()
    await probe.close()


async def test_events_requires_auth(client, case_id):
    res = await client.get(f"/cases/{case_id}/events")
    assert res.status_code == 401
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_messages_list.py tests/api/test_events.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 메시지 목록 라우터** — `app/api/messages.py`

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import owned_case
from app.models import Case, Message
from app.services.presenters import message_dict

router = APIRouter(prefix="/cases/{case_id}/messages", tags=["chat"])


@router.get("")
async def list_messages(
    before: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1),
    case: Case = Depends(owned_case),
    db: AsyncSession = Depends(get_db),
) -> dict:
    limit = min(limit, 50)
    stmt = select(Message).where(Message.case_id == case.id)
    if before:
        stmt = stmt.where(Message.id < before)
    stmt = stmt.order_by(Message.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return {
        "items": [message_dict(m) for m in rows],
        "hasMore": has_more,
        "nextCursor": rows[0].id if has_more and rows else None,
    }
```

(ULID는 시간순 정렬이 되므로 `id`로 커서 비교한다.)

- [ ] **Step 4: SSE 엔드포인트** — `app/api/events.py`

```python
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import bearer_token
from app.errors import ApiError
from app.models import User
from app.security import decode_access_token
from app.services.cases import get_owned_case
from app.sse.hub import hub

router = APIRouter(tags=["events"])


async def _user_from_header_or_query(
    token: str | None = Depends(bearer_token),
    access_token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    raw = token or access_token
    if not raw:
        raise ApiError("UNAUTHORIZED")
    user = await db.get(User, decode_access_token(raw))
    if user is None:
        raise ApiError("UNAUTHORIZED")
    return user


@router.get("/cases/{case_id}/events")
async def events(
    case_id: str,
    request: Request,
    user: User = Depends(_user_from_header_or_query),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_case(db, user, case_id)
    last_event_id = request.headers.get("last-event-id")
    stream = hub.subscribe(case_id, last_event_id)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
```

`app/main.py`에 `messages.router` · `events.router` 등록.

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/api/test_messages_list.py tests/api/test_events.py tests/api/test_cases.py -v`
Expected: 전부 PASS (Task 4의 남은 1건 포함)

- [ ] **Step 6: 커밋**

```bash
git add app/api/messages.py app/api/events.py app/main.py tests/api/test_messages_list.py tests/api/test_events.py
git commit -m "feat(chat): 메시지 목록 커서 페이지네이션과 SSE 이벤트 채널"
```

---

### Task 6: Agent 계약 · 로더 · MockAgent

**Files:**
- Create: `app/agent/base.py`, `app/agent/mock.py`, `docs/agent-interface.md`, `tests/test_agent_mock.py`
- Modify: `app/agent/loader.py`, `tests/conftest.py` (`AGENT_IMPL`을 `app.agent.mock:MockAgent`로)

**Interfaces:**
- Produces `base.py` (Pydantic, snake_case):
  - `ChatTurn(role: Literal["user","assistant"], text: str)`
  - `VerdictSnapshot(version, ratio_mine, ratio_other, summary, opponent_claim: dict|None, basis: dict)`
  - `AnalyzeInput(video_path: str, video_mime: str, description: str)` → `AnalyzeResult(summary_text, facts: dict, questions: list[str], title: str, video_meta: VideoMeta|None)` · `VideoMeta(speed_kph: int|None, impact_at_sec: int|None)`
  - `ChatInput(messages: list[ChatTurn], new_message: str, facts: dict, questions: list[str], verdict: VerdictSnapshot|None, has_video: bool, has_report: bool)` → `ChatResult(reply: str, next_action: Literal["none","verdict","rejudge","create_report","create_rebuttal"]="none", fact_updates: dict = {})`
  - `JudgeInput(messages, facts, previous_verdict: VerdictSnapshot|None)` → `JudgeResult(ratio_mine, ratio_other, summary, change_reason: str|None, opponent_claim: Ratio|None, basis: Basis)` · `Ratio(mine, other)` · `Basis(chart: Chart, precedents: list[Precedent])` · `Chart(name, note)` · `Precedent(id, title, body_text)`
  - `WriteInput(kind: Literal["report","rebuttal"], messages, facts, verdict: VerdictSnapshot, revision_request: str|None, previous_sections: list[Section]|None, report_sections: list[Section]|None)` → `WriteResult(sections: list[Section]|None, caveat: str|None, page_count: int|None, body: str|None)` · `Section(index, title, body)`
  - `ExplainInput(precedent_id, facts)` → `ExplainResult(body_text)`
  - `class Agent(Protocol)` 5 메서드
- Produces `loader.py`: `get_agent() -> Agent` (싱글턴, 동기 메서드는 `asyncio.to_thread`로 감싼 어댑터) · `reset_agent()`
- `JudgeResult` validator: `ratio_mine + ratio_other == 100`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_agent_mock.py`

```python
import pytest
from pydantic import ValidationError

from app.agent.base import AnalyzeInput, ChatInput, ChatTurn, JudgeInput, JudgeResult, VerdictSnapshot, WriteInput
from app.agent.loader import get_agent, reset_agent
from app.agent.mock import MockAgent


def test_judge_result_requires_ratio_sum_100():
    with pytest.raises(ValidationError):
        JudgeResult(ratio_mine=10, ratio_other=80, summary="s", basis={"chart": {"name": "n", "note": "x"}, "precedents": []})


async def test_mock_full_scenario():
    agent = MockAgent()
    a = await agent.analyze(AnalyzeInput(video_path="x.mp4", video_mime="video/mp4", description="교차로에서 오토바이가 박았어요"))
    assert a.title == "교차로 직진 충돌 · 08-22"
    assert a.video_meta.speed_kph == 48 and a.video_meta.impact_at_sec == 31
    assert len(a.questions) == 1 and "1/2" in a.questions[0]

    facts = dict(a.facts)
    c1 = await agent.chat(ChatInput(messages=[], new_message="우측 앞펜더요.", facts=facts, questions=a.questions, verdict=None, has_video=True, has_report=False))
    assert c1.next_action == "none" and "2/2" in c1.reply
    facts.update(c1.fact_updates)

    c2 = await agent.chat(ChatInput(messages=[], new_message="초록불이었어요.", facts=facts, questions=a.questions, verdict=None, has_video=True, has_report=False))
    assert c2.next_action == "verdict"
    facts.update(c2.fact_updates)

    j = await agent.judge(JudgeInput(messages=[], facts=facts, previous_verdict=None))
    assert (j.ratio_mine, j.ratio_other) == (0, 100) and j.change_reason is None
    assert len(j.basis.precedents) == 2 and j.basis.precedents[0].body_text

    snap = VerdictSnapshot(version=1, ratio_mine=0, ratio_other=100, summary=j.summary, opponent_claim=None, basis=j.basis.model_dump())
    c3 = await agent.chat(ChatInput(messages=[], new_message="다시 보니까 상대 신호가 황색이었던 것 같아요.", facts=facts, questions=[], verdict=snap, has_video=True, has_report=False))
    assert c3.next_action == "rejudge" and c3.fact_updates == {"opponent_signal": "yellow"}
    facts.update(c3.fact_updates)
    j2 = await agent.judge(JudgeInput(messages=[], facts=facts, previous_verdict=snap))
    assert (j2.ratio_mine, j2.ratio_other) == (20, 80) and j2.change_reason

    c4 = await agent.chat(ChatInput(messages=[], new_message="상대 보험사는 30:70이라고 해요", facts=facts, questions=[], verdict=snap, has_video=True, has_report=False))
    assert c4.fact_updates == {"opponent_claim": {"mine": 30, "other": 70}} and c4.next_action == "none"

    c5 = await agent.chat(ChatInput(messages=[], new_message="사건경위서 만들어 주세요", facts=facts, questions=[], verdict=snap, has_video=True, has_report=False))
    assert c5.next_action == "create_report"
    c6 = await agent.chat(ChatInput(messages=[], new_message="바로 반박의견서 보낼 수 있어요?", facts=facts, questions=[], verdict=snap, has_video=True, has_report=True))
    assert c6.next_action == "create_rebuttal"

    w = await agent.write(WriteInput(kind="report", messages=[], facts=facts, verdict=snap, revision_request=None, previous_sections=None, report_sections=None))
    assert [s.index for s in w.sections] == [1, 2, 3, 4] and w.page_count == 2
    w2 = await agent.write(WriteInput(kind="rebuttal", messages=[], facts=facts, verdict=snap, revision_request=None, previous_sections=None, report_sections=w.sections))
    assert w2.body and w2.sections is None


async def test_mock_chat_without_video_asks_for_upload():
    agent = MockAgent()
    c = await agent.chat(ChatInput(messages=[], new_message="어제 사고났어요", facts={}, questions=[], verdict=None, has_video=False, has_report=False))
    assert "영상" in c.reply and c.next_action == "none"


async def test_get_agent_wraps_sync_impl(test_env, monkeypatch):
    monkeypatch.setenv("AGENT_IMPL", "tests.test_agent_mock:SyncAgent")
    from app.config import get_settings

    get_settings.cache_clear()
    reset_agent()
    agent = get_agent()
    r = await agent.chat(ChatInput(messages=[], new_message="x", facts={}, questions=[], verdict=None, has_video=True, has_report=False))
    assert r.reply == "sync"
    reset_agent()


class SyncAgent:
    def chat(self, inp):
        return {"reply": "sync", "next_action": "none", "fact_updates": {}}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_agent_mock.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: app/agent/base.py**

```python
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class Ratio(BaseModel):
    mine: int = Field(ge=0, le=100)
    other: int = Field(ge=0, le=100)


class VerdictSnapshot(BaseModel):
    version: int
    ratio_mine: int
    ratio_other: int
    summary: str
    opponent_claim: dict | None = None
    basis: dict = Field(default_factory=dict)


class VideoMeta(BaseModel):
    speed_kph: int | None = None
    impact_at_sec: int | None = None


class AnalyzeInput(BaseModel):
    video_path: str
    video_mime: str
    description: str


class AnalyzeResult(BaseModel):
    summary_text: str
    facts: dict = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    title: str
    video_meta: VideoMeta | None = None


NextAction = Literal["none", "verdict", "rejudge", "create_report", "create_rebuttal"]


class ChatInput(BaseModel):
    messages: list[ChatTurn]
    new_message: str
    facts: dict
    questions: list[str]
    verdict: VerdictSnapshot | None
    has_video: bool
    has_report: bool


class ChatResult(BaseModel):
    reply: str
    next_action: NextAction = "none"
    fact_updates: dict = Field(default_factory=dict)


class Chart(BaseModel):
    name: str
    note: str


class Precedent(BaseModel):
    id: str
    title: str
    body_text: str


class Basis(BaseModel):
    chart: Chart
    precedents: list[Precedent] = Field(default_factory=list)


class JudgeInput(BaseModel):
    messages: list[ChatTurn]
    facts: dict
    previous_verdict: VerdictSnapshot | None


class JudgeResult(BaseModel):
    ratio_mine: int = Field(ge=0, le=100)
    ratio_other: int = Field(ge=0, le=100)
    summary: str
    change_reason: str | None = None
    opponent_claim: Ratio | None = None
    basis: Basis

    @model_validator(mode="after")
    def _sum(self):
        if self.ratio_mine + self.ratio_other != 100:
            raise ValueError("ratio_mine + ratio_other must be 100")
        return self


class Section(BaseModel):
    index: int
    title: str
    body: str


class WriteInput(BaseModel):
    kind: Literal["report", "rebuttal"]
    messages: list[ChatTurn]
    facts: dict
    verdict: VerdictSnapshot
    revision_request: str | None = None
    previous_sections: list[Section] | None = None
    report_sections: list[Section] | None = None


class WriteResult(BaseModel):
    sections: list[Section] | None = None
    caveat: str | None = None
    page_count: int | None = None
    body: str | None = None


class ExplainInput(BaseModel):
    precedent_id: str
    facts: dict


class ExplainResult(BaseModel):
    body_text: str


class Agent(Protocol):
    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult: ...
    async def chat(self, inp: ChatInput) -> ChatResult: ...
    async def judge(self, inp: JudgeInput) -> JudgeResult: ...
    async def write(self, inp: WriteInput) -> WriteResult: ...
    async def explain(self, inp: ExplainInput) -> ExplainResult: ...
```

- [ ] **Step 4: app/agent/loader.py에 get_agent 추가**

```python
import asyncio
import inspect

from pydantic import BaseModel

from app.agent.base import (
    Agent, AnalyzeInput, AnalyzeResult, ChatInput, ChatResult, ExplainInput, ExplainResult,
    JudgeInput, JudgeResult, WriteInput, WriteResult,
)
from app.config import get_settings

_RESULT_TYPES = {"analyze": AnalyzeResult, "chat": ChatResult, "judge": JudgeResult, "write": WriteResult, "explain": ExplainResult}


class AgentAdapter:
    """AI 담당 구현체를 감싼다. 동기 함수면 스레드에서 돌리고, 반환값을 Pydantic으로 검증한다."""

    def __init__(self, impl) -> None:
        self._impl = impl

    async def _call(self, name: str, inp: BaseModel):
        fn = getattr(self._impl, name, None)
        if fn is None:
            raise NotImplementedError(f"Agent에 {name}() 이 없어요")
        if inspect.iscoroutinefunction(fn):
            raw = await fn(inp)
        else:
            raw = await asyncio.to_thread(fn, inp)
        result_type = _RESULT_TYPES[name]
        if isinstance(raw, result_type):
            return raw
        if isinstance(raw, BaseModel):
            raw = raw.model_dump()
        return result_type.model_validate(raw)

    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult:
        return await self._call("analyze", inp)

    async def chat(self, inp: ChatInput) -> ChatResult:
        return await self._call("chat", inp)

    async def judge(self, inp: JudgeInput) -> JudgeResult:
        return await self._call("judge", inp)

    async def write(self, inp: WriteInput) -> WriteResult:
        return await self._call("write", inp)

    async def explain(self, inp: ExplainInput) -> ExplainResult:
        return await self._call("explain", inp)


_agent: Agent | None = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        cls = load_agent_class(get_settings().agent_impl)
        _agent = AgentAdapter(cls())
    return _agent


def reset_agent() -> None:
    global _agent
    _agent = None
```

- [ ] **Step 5: app/agent/mock.py**

```python
import re

from app.agent.base import (
    AnalyzeInput, AnalyzeResult, Basis, Chart, ChatInput, ChatResult, ExplainInput, ExplainResult,
    JudgeInput, JudgeResult, Precedent, Ratio, Section, VideoMeta, WriteInput, WriteResult,
)

RATIO_RE = re.compile(r"(\d{1,3})\s*[:대]\s*(\d{1,3})")

PRECEDENTS = [
    Precedent(
        id="2019-018856",
        title="신호위반 직진 충돌",
        body_text=(
            "신호기 있는 교차로에서 직진 차량과 적색 신호에 진입한 이륜차가 충돌한 사례예요. 보험사는 직진차 30 : 이륜차 70을 주장했지만, "
            "블랙박스로 상대 신호위반이 입증되어 직진차 0 : 이륜차 100으로 뒤집혔어요.\n\n"
            "내 사건과 신호 상태·진입 방향·충돌 형태가 같고, 상대가 이륜차라는 점까지 동일해요."
        ),
    ),
    Precedent(
        id="2021-004312",
        title="이륜차 교차로 진입",
        body_text=(
            "교차로에 먼저 진입한 승용차와 우측에서 진입한 이륜차의 충돌 사례예요. 이륜차의 신호위반이 확인되어 승용차 0 : 이륜차 100이 인정됐어요.\n\n"
            "내 사건처럼 상대가 우측에서 진입했고 충돌 부위가 앞펜더라는 점이 같아요."
        ),
    ),
]

SUMMARY = (
    "영상을 분석했어요. 내 차는 2차로에서 직진 중이었고, 상대는 우측에서 적색 신호에 진입했어요. "
    "내 속도는 약 48km/h예요. 충돌 부위와 정지선 통과 시점은 영상만으로는 확인이 어려워요."
)
Q1 = "판정까지 두 가지만 더 물어볼게요. 차량 어느 부분에 충돌했나요? 1/2"
Q2 = "마지막 하나만 더 여쭤볼게요. 정지선을 지날 때 내 신호는 무엇이었나요? 2/2"


class MockAgent:
    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult:
        return AnalyzeResult(
            summary_text=SUMMARY,
            facts={"my_lane": "2차로 직진", "opponent_entry": "우측", "opponent_signal": "red", "my_speed_kph": 48},
            questions=[Q1],
            title="교차로 직진 충돌 · 08-22",
            video_meta=VideoMeta(speed_kph=48, impact_at_sec=31),
        )

    async def chat(self, inp: ChatInput) -> ChatResult:
        msg = inp.new_message
        if not inp.has_video:
            return ChatResult(reply=(
                "많이 놀라셨겠어요. 지금 설명만으로는 정확한 비율을 말씀드리기 어려워요 — "
                "블랙박스 영상을 올려 주시면 1~2분 안에 예상 과실비율을 근거와 함께 보여 드릴게요."
            ))
        if inp.verdict is None:
            if "impact_part" not in inp.facts:
                return ChatResult(reply=f"{msg.rstrip('.요')}로 적어 뒀어요. {Q2}", fact_updates={"impact_part": msg})
            if "my_signal" not in inp.facts:
                return ChatResult(reply="알겠어요. 과실비율을 계산할게요.", next_action="verdict", fact_updates={"my_signal": msg})
            return ChatResult(reply="네, 확인했어요.")
        if "경위서" in msg:
            return ChatResult(reply="사건경위서를 만들게요.", next_action="create_report")
        if "반박" in msg:
            return ChatResult(reply="반박의견서를 준비할게요.", next_action="create_rebuttal")
        if "황색" in msg or "노란" in msg:
            return ChatResult(
                reply="상대 신호를 황색으로 반영할게요. 신호위반 일방과실이 아니게 되어 비율을 다시 계산해요.",
                next_action="rejudge",
                fact_updates={"opponent_signal": "yellow"},
            )
        m = RATIO_RE.search(msg)
        if m:
            mine, other = int(m.group(1)), int(m.group(2))
            return ChatResult(reply=f"상대 보험사 주장 나 {mine} : 상대 {other}으로 적어 뒀어요. 판정 카드에서 비교해 보세요.", fact_updates={"opponent_claim": {"mine": mine, "other": other}})
        return ChatResult(reply="네, 확인했어요. 더 궁금한 점이 있으면 말씀해 주세요.")

    async def judge(self, inp: JudgeInput) -> JudgeResult:
        claim = inp.facts.get("opponent_claim")
        opponent_claim = Ratio(**claim) if claim else None
        chart = Chart(name="신호기 있는 교차로 · 신호위반", note="사고 유형별 기본 비율을 정해 둔 표 · 차대이륜차 편")
        if inp.facts.get("opponent_signal") == "yellow":
            return JudgeResult(
                ratio_mine=20, ratio_other=80,
                summary="상대 황색 신호 진입으로 기본 과실이 적용돼요. 내 차의 전방 주시 의무가 일부 반영됐어요.",
                change_reason="상대 신호가 황색으로 바뀌어 신호위반 일방과실 대신 기본 과실이 적용됐어요.",
                opponent_claim=opponent_claim,
                basis=Basis(chart=Chart(name="신호기 있는 교차로 · 황색 신호 진입", note=chart.note), precedents=PRECEDENTS),
            )
        return JudgeResult(
            ratio_mine=0, ratio_other=100,
            summary="상대 신호위반 일방과실이에요. 내 차가 미리 알아차리거나 피할 수 없었던 것으로 판단돼요.",
            change_reason="상대 신호를 다시 적색으로 반영해 신호위반 일방과실로 돌아왔어요." if inp.previous_verdict else None,
            opponent_claim=opponent_claim,
            basis=Basis(chart=chart, precedents=PRECEDENTS),
        )

    async def write(self, inp: WriteInput) -> WriteResult:
        v = inp.verdict
        if inp.kind == "report":
            sections = [
                Section(index=1, title="사고 일시 및 장소", body="2026년 8월 22일 14시경, 서울시 강남구 논현사거리 교차로에서 발생한 사고입니다."),
                Section(index=2, title="사고 경위", body="본인은 2차로에서 정상 신호에 따라 직진 중이었습니다. 우측에서 교차로에 진입한 이륜차가 본인 차량의 우측 앞펜더를 충격하였습니다."),
                Section(index=3, title="블랙박스 영상 분석 결과", body="영상에서 본인 차량의 2차로 직진, 상대 차량의 교차로 진입, 주행 속도 약 48km/h가 확인됩니다."),
                Section(index=4, title="주장 요지", body=f"상대 차량의 진입으로 생긴 사고이므로, 나 {v.ratio_mine} : 상대 {v.ratio_other}의 과실비율 적용을 요청드립니다."),
            ]
            if inp.revision_request:
                sections[1] = Section(index=2, title="사고 경위", body="본인은 2차로 직진 중 우측에서 진입한 이륜차와 충돌하였습니다.")
            caveat = None if inp.facts.get("my_signal") else "정지선 통과 시점 한 가지는 아직 확인 중이에요."
            return WriteResult(sections=sections, caveat=caveat, page_count=2)
        claim = v.opponent_claim or {"mine": 30, "other": 70}
        body = (
            f"귀사는 나 {claim['mine']} : 상대 {claim['other']}을 제시하셨습니다. 블랙박스 영상에서 상대 차량의 교차로 진입 상황이 확인됩니다. "
            f"인정기준 도표({v.basis.get('chart', {}).get('name', '')})와 심의사례 2019-018856 · 2021-004312에 비추어 "
            f"나 {v.ratio_mine} : 상대 {v.ratio_other}이 타당합니다."
        )
        return WriteResult(body=body)

    async def explain(self, inp: ExplainInput) -> ExplainResult:
        for p in PRECEDENTS:
            if p.id == inp.precedent_id:
                return ExplainResult(body_text=p.body_text)
        return ExplainResult(body_text="해당 심의사례 설명을 찾지 못했어요.")
```

- [ ] **Step 6: conftest의 AGENT_IMPL을 Mock으로 변경**

`tests/conftest.py`의 `test_env`에서 `monkeypatch.setenv("AGENT_IMPL", "app.agent.mock:MockAgent")`로 바꾸고, yield 앞뒤에 `from app.agent.loader import reset_agent; reset_agent()` 호출을 추가한다. `tests/api/test_health.py`의 degraded 테스트는 그대로 동작한다.

- [ ] **Step 7: docs/agent-interface.md 작성**

```markdown
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
```

- [ ] **Step 8: 통과 확인**

Run: `.venv/Scripts/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 9: 커밋**

```bash
git add app/agent docs/agent-interface.md tests/conftest.py tests/test_agent_mock.py
git commit -m "feat(agent): Agent 계약·어댑터 로더와 시연용 MockAgent"
```

---

### Task 7: Job 실행기

**Files:**
- Create: `app/jobs/__init__.py`, `app/jobs/runner.py`, `tests/test_job_runner.py`
- Modify: `app/main.py` (시작 시 `cleanup_stale`, 종료 시 `wait_all`)

**Interfaces:**
- Produces: `JobHandler = Callable[[AsyncSession, str, str], Awaitable[None]]` (db, case_id, job_id)
- `JobRunner.start(db, case_id, kind, handler) -> Job` — `db`는 호출자의 세션(Job INSERT·commit에 사용). 이미 실행 중이면 `JOB_ALREADY_RUNNING`. 태스크 생성 후 `case.updated` 송출.
- `JobRunner.wait_all()` — 테스트용. 모든 활성 태스크 완료 대기.
- `JobRunner.cleanup_stale()` — `queued|running` → `failed`
- 전역 `runner`
- 실패 시: `Job.status=failed`, `error={"type":..., "message":...}`, `case.updated` 송출

- [ ] **Step 1: 실패하는 테스트** — `tests/test_job_runner.py`

```python
import asyncio

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.errors import ApiError
from app.jobs.runner import JobRunner
from app.models import Job
from app.sse.hub import hub


async def test_runner_runs_handler_and_marks_succeeded(app, case_id):
    runner = JobRunner()
    seen = {}

    async def handler(db, cid, job_id):
        seen["case"] = cid
        seen["job"] = job_id

    it = hub.subscribe(case_id)
    await it.__anext__()
    async with session_scope() as db:
        job = await runner.start(db, case_id, "analysis", handler)
        assert job.status == "running"
    frame = await it.__anext__()
    assert "event: case.updated" in frame and '"kind": "analysis"' in frame
    await runner.wait_all()
    assert seen == {"case": case_id, "job": job.id}
    async with session_scope() as db:
        assert (await db.get(Job, job.id)).status == "succeeded"
    await it.aclose()


async def test_runner_rejects_second_job_while_running(app, case_id):
    runner = JobRunner()
    gate = asyncio.Event()

    async def handler(db, cid, job_id):
        await gate.wait()

    async with session_scope() as db:
        await runner.start(db, case_id, "analysis", handler)
        with pytest.raises(ApiError) as ei:
            await runner.start(db, case_id, "verdict", handler)
        assert ei.value.code == "JOB_ALREADY_RUNNING"
    gate.set()
    await runner.wait_all()


async def test_runner_marks_failed_and_publishes_case_updated(app, case_id):
    runner = JobRunner()

    async def handler(db, cid, job_id):
        raise RuntimeError("boom")

    it = hub.subscribe(case_id)
    await it.__anext__()
    async with session_scope() as db:
        job = await runner.start(db, case_id, "verdict", handler)
    await it.__anext__()  # start
    await runner.wait_all()
    frame = await it.__anext__()
    assert '"activeJob": null' in frame
    async with session_scope() as db:
        j = await db.get(Job, job.id)
        assert j.status == "failed" and j.error["message"] == "boom" and j.ended_at is not None
    await it.aclose()


async def test_cleanup_stale(app, case_id):
    runner = JobRunner()
    gate = asyncio.Event()

    async def handler(db, cid, job_id):
        await gate.wait()

    async with session_scope() as db:
        await runner.start(db, case_id, "report", handler)
    await JobRunner().cleanup_stale()
    async with session_scope() as db:
        rows = (await db.execute(select(Job))).scalars().all()
        assert all(r.status == "failed" for r in rows)
    gate.set()
    await runner.wait_all()
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_job_runner.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: app/jobs/runner.py**

```python
import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.db import session_scope
from app.errors import ApiError
from app.ids import new_id
from app.models import Job
from app.services.cases import active_job, publish_case_updated

log = logging.getLogger(__name__)

JobHandler = Callable[[AsyncSession, str, str], Awaitable[None]]


class JobRunner:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    async def start(self, db: AsyncSession, case_id: str, kind: str, handler: JobHandler) -> Job:
        if await active_job(db, case_id) is not None:
            raise ApiError("JOB_ALREADY_RUNNING")
        job = Job(id=new_id(), case_id=case_id, kind=kind, status="running", started_at=now_utc())
        db.add(job)
        await db.commit()
        await publish_case_updated(db, case_id)
        task = asyncio.create_task(self._run(job.id, case_id, kind, handler), name=f"job:{kind}:{job.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job_id: str, case_id: str, kind: str, handler: JobHandler) -> None:
        try:
            async with session_scope() as db:
                await handler(db, case_id, job_id)
            async with session_scope() as db:
                await db.execute(update(Job).where(Job.id == job_id).values(status="succeeded", ended_at=now_utc()))
                await db.commit()
        except Exception as e:  # noqa: BLE001
            log.exception("job %s (%s) failed for case %s", job_id, kind, case_id)
            async with session_scope() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(status="failed", ended_at=now_utc(), error={"type": type(e).__name__, "message": str(e)})
                )
                await db.commit()
                try:
                    await publish_case_updated(db, case_id)
                except ApiError:
                    pass  # 사건이 삭제된 경우

    async def wait_all(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def cleanup_stale(self) -> None:
        async with session_scope() as db:
            stmt = select(Job).where(Job.status.in_(("queued", "running")))
            for job in (await db.execute(stmt)).scalars().all():
                job.status = "failed"
                job.ended_at = now_utc()
                job.error = {"type": "Restart", "message": "서버 재시작으로 중단됐어요"}
            await db.commit()


runner = JobRunner()
```

`app/main.py`의 lifespan: `db.configure_database(...)`와 `create_all` 뒤에 `await runner.cleanup_stale()`, `yield` 뒤에 `await runner.wait_all()`을 넣는다. (`from app.jobs.runner import runner`)

`tests/conftest.py`에 autouse 픽스처 추가:

```python
@pytest.fixture(autouse=True)
async def _drain_jobs():
    yield
    from app.jobs.runner import runner

    await runner.wait_all()
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_job_runner.py -v`
Expected: PASS (4)

- [ ] **Step 5: 커밋**

```bash
git add app/jobs app/main.py tests/conftest.py tests/test_job_runner.py
git commit -m "feat(jobs): 사건당 1개 제한과 실패 정리를 갖춘 asyncio Job 실행기"
```

---

## 계획 2 완료 기준

- `pytest -q` 전부 통과.
- 사건 생성 → 목록 → 조회 → 이름 변경 → SSE로 `case.updated` 수신 → 삭제가 curl로 동작.
- `docs/agent-interface.md`를 AI 담당에게 전달 가능.
- 계획 3(`2026-09-03-03-video-chat-verdict.md`)으로 이어진다.
