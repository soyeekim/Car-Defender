# 카-디펜더 백엔드 4/4 — 사건경위서·PDF·반박의견서·발송 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사건경위서(F-1~F-6), PDF 생성, 반박의견서(G-1~G-5), 실제 메일 발송과 멱등키, 시드 스크립트를 만들어 명세 36개 엔드포인트를 완성한다. 계획 3이 끝난 상태에서 시작한다.

**Architecture:** `services/report.py`·`services/rebuttal.py`가 규칙(조건 검사·버전·잠금·canSend)을 갖고, Job 핸들러는 `jobs/report.py`·`jobs/rebuttal.py`. 두 서비스는 import 시 `actions` 레지스트리에 `create_report`·`create_rebuttal`을 등록한다. PDF는 `pdf/report_pdf.py` 순수 함수. 발송은 동기이며 `SendLog.idempotency_key`로 중복을 막는다.

**Tech Stack:** fpdf2 + NanumGothic TTF(OFL). 나머지는 이전 계획과 동일.

**Spec:** `docs/superpowers/specs/2026-09-03-backend-design.md` · `../금융 ai 디자인/20_API명세서_v2.md`

## Global Constraints

- 계획 1~3의 Global Constraints 전부 적용.
- 다시 쓰기는 UPDATE가 아니라 새 버전 INSERT. `report_draft` 카드는 버전이 올라도 새로 만들지 않고 `message.updated`로 갱신 (명세 §4.7).
- 반박의견서 잠금 판정은 서버가 한다: 판정 있음 + 경위서 있음 (명세 §4.8 · G-1).
- `canSend = is_email(recipient) and bool(claimNumber)`. `subjectAuto`면 제목은 `과실비율 재검토 요청 (접수번호 {claimNumber})`, 접수번호 없으면 `(접수번호는 아직 안 넣었어요)`.
- 발송은 동기. `Idempotency-Key` 없으면 400. 성공한 키로 재호출하면 최초 결과 반환, 메일은 다시 안 나간다.
- 첨부 합계 25MB 초과면 영상을 `included: false` + `note`로 내리고 메일 본문 말미에 안내 한 줄 (명세 §8.1).
- PDF 파일명 `사건경위서_{사건제목}_{YYYYMMDD}.pdf`. 제목의 `\ / : * ? " < > |`는 `_`로.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `app/pdf/__init__.py` · `report_pdf.py` · `fonts/NanumGothic-Regular.ttf` · `fonts/NanumGothic-Bold.ttf` | PDF 렌더링 |
| `app/services/report.py` · `app/jobs/report.py` · `app/api/report.py` · `app/schemas/report.py` | 경위서 |
| `app/services/rebuttal.py` · `app/jobs/rebuttal.py` · `app/api/rebuttal.py` · `app/schemas/rebuttal.py` | 반박의견서 · 발송 |
| `app/mail/templates.py` | 메일 본문 조립 |
| `app/seed.py` | 시드 |
| `tests/...` | 각 Task |

---

### Task 1: PDF 렌더러

**Files:**
- Create: `app/pdf/__init__.py`, `app/pdf/report_pdf.py`, `app/pdf/fonts/NanumGothic-Regular.ttf`, `app/pdf/fonts/NanumGothic-Bold.ttf`, `tests/test_pdf.py`

**Interfaces:**
- `render_report_pdf(*, case_title: str, date_label: str, version_label: str, sections: list[dict], disclaimer: str) -> tuple[bytes, int]` (pdf bytes, page_count)
- `safe_filename(title: str) -> str` · `report_pdf_filename(case_title, created_at) -> str`

- [ ] **Step 1: 폰트 내려받기**

```bash
mkdir -p app/pdf/fonts
curl -L -o app/pdf/fonts/NanumGothic-Regular.ttf https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf
curl -L -o app/pdf/fonts/NanumGothic-Bold.ttf https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Bold.ttf
ls -la app/pdf/fonts
```
Expected: 두 파일 각각 4MB 안팎. (내려받기가 막히면 `C:\Windows\Fonts\NanumGothic.ttf`가 있는지 확인해 복사한다. 없으면 OFL 폰트 아무거나 한글 지원 TTF를 같은 이름으로 넣는다.)

- [ ] **Step 2: 실패하는 테스트** — `tests/test_pdf.py`

```python
from datetime import datetime, timezone

from app.pdf.report_pdf import render_report_pdf, report_pdf_filename, safe_filename

SECTIONS = [
    {"index": 1, "title": "사고 일시 및 장소", "body": "2026년 8월 22일 14시경, 서울시 강남구 논현사거리 교차로에서 발생한 사고입니다."},
    {"index": 2, "title": "사고 경위", "body": "본인은 2차로에서 정상 신호에 따라 직진 중이었습니다. " * 20},
    {"index": 3, "title": "블랙박스 영상 분석 결과", "body": "영상에서 본인 차량의 2차로 직진이 확인됩니다."},
    {"index": 4, "title": "주장 요지", "body": "상대 차량의 일방과실 적용을 요청드립니다."},
]


def test_render_pdf_returns_bytes_and_pages():
    data, pages = render_report_pdf(case_title="교차로 직진 충돌 · 08-22", date_label="08-25", version_label="첫 번째 버전", sections=SECTIONS, disclaimer="본 결과는 참고용입니다.")
    assert data[:4] == b"%PDF" and pages >= 1


def test_long_body_spans_pages():
    long_sections = [{**s, "body": s["body"] * 30} for s in SECTIONS]
    _, pages = render_report_pdf(case_title="t", date_label="08-25", version_label="v", sections=long_sections, disclaimer="d")
    assert pages >= 2


def test_filename():
    assert safe_filename('a/b:c*d?e"f<g>h|i\\j') == "a_b_c_d_e_f_g_h_i_j"
    dt = datetime(2026, 8, 22, 9, 40, tzinfo=timezone.utc)
    assert report_pdf_filename("교차로 직진 충돌 · 08-22", dt) == "사건경위서_교차로 직진 충돌 · 08-22_20260822.pdf"
```

- [ ] **Step 3: 실패 확인** — Run: `.venv/Scripts/python -m pytest tests/test_pdf.py -v` → FAIL ImportError

- [ ] **Step 4: app/pdf/report_pdf.py**

```python
import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from app.clock import kst_yyyymmdd

FONT_DIR = Path(__file__).parent / "fonts"
FONT = "NanumGothic"
_BAD = re.compile(r'[\\/:*?"<>|]')


def safe_filename(title: str) -> str:
    return _BAD.sub("_", title).strip() or "사건"


def report_pdf_filename(case_title: str, created_at: datetime) -> str:
    return f"사건경위서_{safe_filename(case_title)}_{kst_yyyymmdd(created_at)}.pdf"


class _Doc(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.add_font(FONT, "", str(FONT_DIR / "NanumGothic-Regular.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "NanumGothic-Bold.ttf"))
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(20, 20, 20)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(FONT, "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"{self.page_no()} / {{nb}}", align="C")


def render_report_pdf(*, case_title: str, date_label: str, version_label: str, sections: list[dict], disclaimer: str) -> tuple[bytes, int]:
    pdf = _Doc()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_text_color(20, 20, 20)
    pdf.set_font(FONT, "B", 20)
    pdf.cell(0, 12, "사건경위서", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT, "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 7, f"{case_title} · {version_label} · {date_label}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for s in sorted(sections, key=lambda x: x.get("index", 0)):
        pdf.set_text_color(20, 20, 20)
        pdf.set_font(FONT, "B", 12)
        pdf.multi_cell(0, 8, f"{s.get('index')}. {s.get('title', '')}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(FONT, "", 10.5)
        pdf.multi_cell(0, 6.5, s.get("body", ""), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
    pdf.ln(4)
    pdf.set_font(FONT, "", 8.5)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 5, disclaimer, new_x="LMARGIN", new_y="NEXT")
    data = bytes(pdf.output())
    return data, pdf.pages_count
```

`app/pdf/__init__.py`는 빈 파일. `.gitattributes`에 `*.ttf binary`가 이미 있다.

- [ ] **Step 5: 통과 확인** — Run: `.venv/Scripts/python -m pytest tests/test_pdf.py -v` → PASS (3)

- [ ] **Step 6: 커밋**

```bash
git add app/pdf tests/test_pdf.py
git commit -m "feat(pdf): 한글 폰트를 내장한 사건경위서 PDF 렌더러"
```

---

### Task 2: 사건경위서 (F-1 ~ F-6)

**Files:**
- Create: `app/services/report.py`, `app/jobs/report.py`, `app/api/report.py`, `app/schemas/report.py`, `tests/api/test_report.py`
- Modify: `app/main.py`, `tests/conftest.py` (`judged_case` 픽스처)

**Interfaces:**
- `services/report.py`:
  - `start_report(db, case, revision_request: str | None = None) -> tuple[Job, int, int | None]` (job, to_version, from_version). 판정 없으면 `REPORT_VERDICT_REQUIRED`. 다시 쓰기인데 경위서 없으면 `NOT_FOUND`
  - `report_by_version(db, case_id, version: str | int) -> Report` (`latest` 허용, 없으면 `NOT_FOUND`)
  - `draft_payload(report) -> dict` (§4.7) · `full_text(report) -> dict` (F-3) · `versions_list(db, case_id) -> dict` (F-2)
  - `ensure_pdf(db, case, report) -> tuple[ReportPdf, bool created]` (F-5)
  - `pdf_response_dict(case, pdf) -> dict`
  - import 시 `register_action("create_report", ...)`
- `jobs/report.py`: `make_report_handler(revision_request) -> JobHandler`
- 픽스처 `judged_case(client, auth_headers, case_id, upload, settle) -> str`: 설명 → 업로드 → 답변 2회 → judged 상태의 case_id

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_report.py`

```python
from app.mail import get_mailer  # noqa: F401  (픽스처 호환)


async def test_report_requires_verdict(client, auth_headers, case_id):
    res = await client.post(f"/cases/{case_id}/report", headers=auth_headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "REPORT_VERDICT_REQUIRED"


async def test_report_draft_flow(client, auth_headers, judged_case, sse, settle):
    tap = await sse(judged_case)
    res = await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    assert res.status_code == 202
    body = res.json()
    assert body["kind"] == "report" and body["status"] == "running" and body["version"] == 1
    await settle()
    frames = await tap.take(3)
    assert '"kind": "report"' in frames[0]
    assert '"type": "report_draft"' in frames[1] and '"versionLabel": "첫 번째 버전"' in frames[1] and '"canCreateRebuttal": true' in frames[1]
    assert '"report": {"exists": true, "label": "첫 번째 버전 · 2장"' in frames[2]
    assert '"rebuttal": {"exists": false, "locked": false, "label": "이제 만들 수 있어요"}' in frames[2]

    detail = (await client.get(f"/cases/{judged_case}", headers=auth_headers)).json()
    assert detail["stages"]["report"] == {"state": "done"}

    full = (await client.get(f"/cases/{judged_case}/report/versions/latest", headers=auth_headers)).json()
    assert full["version"] == 1 and len(full["sections"]) == 4 and full["sections"][0]["index"] == 1
    assert full["intro"] == "채팅에서 나눈 대화와 영상 분석 결과를 바탕으로 쓴 첫 번째 버전이에요."
    assert full["revisionPlaceholder"] == "예: 2번을 더 간단하게" and full["disclaimer"]

    assert (await client.get(f"/cases/{judged_case}/report/versions/9", headers=auth_headers)).status_code == 404


async def test_report_revision_creates_new_version_and_updates_card(client, auth_headers, judged_case, settle, sse):
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    tap = await sse(judged_case)
    res = await client.post(f"/cases/{judged_case}/report/revisions", json={"request": "2번을 더 간단하게"}, headers=auth_headers)
    assert res.status_code == 202 and res.json()["fromVersion"] == 1 and res.json()["toVersion"] == 2
    await settle()
    frames = await tap.take(3)
    assert "event: message.updated" in frames[1] and '"version": 2' in frames[1]

    versions = (await client.get(f"/cases/{judged_case}/report/versions", headers=auth_headers)).json()
    assert versions["latestVersion"] == 2 and [v["version"] for v in versions["items"]] == [2, 1]
    assert versions["items"][0]["revisionRequest"] == "2번을 더 간단하게" and versions["items"][0]["hasPdf"] is False

    msgs = (await client.get(f"/cases/{judged_case}/messages", params={"limit": 50}, headers=auth_headers)).json()["items"]
    assert len([m for m in msgs if m["type"] == "report_draft"]) == 1


async def test_report_revision_validation(client, auth_headers, judged_case, settle):
    assert (await client.post(f"/cases/{judged_case}/report/revisions", json={"request": "x"}, headers=auth_headers)).status_code == 404
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    assert (await client.post(f"/cases/{judged_case}/report/revisions", json={"request": ""}, headers=auth_headers)).status_code == 422


async def test_pdf_create_idempotent_and_download(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    await settle()
    assert (await client.get(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)).status_code == 404

    res = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert res.status_code == 201
    body = res.json()
    assert body["filename"].startswith("사건경위서_교차로 직진 충돌 · 08-22_") and body["sizeBytes"] > 1000
    assert body["downloadUrl"] == f"/api/v1/cases/{judged_case}/report/versions/1/pdf"

    again = await client.post(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert again.status_code == 200 and again.json()["pdfId"] == body["pdfId"]

    dl = await client.get(f"/cases/{judged_case}/report/versions/1/pdf", headers=auth_headers)
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''%EC%82%AC%EA%B1%B4%EA%B2%BD%EC%9C%84%EC%84%9C_" in dl.headers["content-disposition"]
    assert dl.content[:4] == b"%PDF"

    versions = (await client.get(f"/cases/{judged_case}/report/versions", headers=auth_headers)).json()
    assert versions["items"][0]["hasPdf"] is True


async def test_chat_create_report_action(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/messages", json={"text": "사건경위서 만들어 주세요"}, headers=auth_headers)
    await settle()
    detail = (await client.get(f"/cases/{judged_case}", headers=auth_headers)).json()
    assert detail["documents"]["report"]["exists"] is True
```

`tests/conftest.py`에 추가:

```python
@pytest.fixture
async def judged_case(client, auth_headers, case_id, upload, settle):
    async def say(text):
        r = await client.post(f"/cases/{case_id}/messages", json={"text": text}, headers=auth_headers)
        assert r.status_code == 202, r.text
        await settle()

    await say("교차로에서 오토바이가 박았어요")
    await upload()
    await settle()
    await say("우측 앞펜더요.")
    await say("초록불이었어요.")
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged", detail
    return case_id
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_report.py -v` → FAIL

- [ ] **Step 3: 스키마** — `app/schemas/report.py`

```python
from pydantic import Field

from app.schemas.base import CamelModel


class RevisionRequest(CamelModel):
    request: str = Field(min_length=1, max_length=500)
```

- [ ] **Step 4: 서비스** — `app/services/report.py`

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import kst_date_label, now_utc, to_kst_iso
from app.content.texts import DISCLAIMER, REPORT_INTRO, REPORT_REVISION_PLACEHOLDER
from app.errors import ApiError
from app.ids import new_id
from app.models import Case, Job, Message, Report, ReportPdf
from app.pdf.report_pdf import render_report_pdf, report_pdf_filename
from app.services import cases as case_service
from app.services.actions import register_action
from app.services.presenters import ordinal_label
from app.storage import get_storage


def preview_lines(sections: list[dict], n: int = 2) -> list[str]:
    out = []
    for s in sections[:n]:
        body = s.get("body", "")
        out.append(f"{s.get('index')}. {s.get('title')} — {body[:40]}{' …' if len(body) > 40 else ''}")
    return out


def draft_payload(report: Report) -> dict:
    return {
        "reportId": report.id,
        "version": report.version,
        "versionLabel": ordinal_label(report.version),
        "pageCount": report.page_count,
        "preview": preview_lines(report.sections),
        "caveat": report.caveat,
        "canCreateRebuttal": True,
    }


def full_text(report: Report) -> dict:
    label = ordinal_label(report.version)
    return {
        "reportId": report.id,
        "version": report.version,
        "versionLabel": label,
        "dateLabel": kst_date_label(report.created_at),
        "pageCount": report.page_count,
        "intro": REPORT_INTRO.format(label=label),
        "sections": report.sections,
        "revisionPlaceholder": REPORT_REVISION_PLACEHOLDER,
        "disclaimer": DISCLAIMER,
    }


async def report_by_version(db: AsyncSession, case_id: str, version: str | int) -> Report:
    if version == "latest":
        r = await case_service.latest_report(db, case_id)
    else:
        try:
            v = int(version)
        except ValueError as e:
            raise ApiError("NOT_FOUND") from e
        r = (await db.execute(select(Report).where(Report.case_id == case_id, Report.version == v))).scalar_one_or_none()
    if r is None:
        raise ApiError("NOT_FOUND")
    return r


async def pdf_for(db: AsyncSession, report_id: str) -> ReportPdf | None:
    return (await db.execute(select(ReportPdf).where(ReportPdf.report_id == report_id))).scalar_one_or_none()


async def versions_list(db: AsyncSession, case_id: str) -> dict:
    stmt = select(Report).where(Report.case_id == case_id).order_by(Report.version.desc())
    reports = list((await db.execute(stmt)).scalars().all())
    items = []
    for r in reports:
        items.append({
            "version": r.version, "versionLabel": ordinal_label(r.version), "pageCount": r.page_count,
            "revisionRequest": r.revision_request, "hasPdf": await pdf_for(db, r.id) is not None,
            "createdAt": to_kst_iso(r.created_at),
        })
    return {"items": items, "latestVersion": reports[0].version if reports else None}


async def start_report(db: AsyncSession, case: Case, revision_request: str | None = None) -> tuple[Job, int, int | None]:
    if await case_service.active_verdict(db, case.id) is None:
        raise ApiError("REPORT_VERDICT_REQUIRED")
    latest = await case_service.latest_report(db, case.id)
    if revision_request is not None and latest is None:
        raise ApiError("NOT_FOUND")
    from app.jobs.report import make_report_handler
    from app.jobs.runner import runner

    job = await runner.start(db, case.id, "report", make_report_handler(revision_request))
    from_version = latest.version if latest else None
    return job, (from_version or 0) + 1, from_version


async def draft_card(db: AsyncSession, case_id: str) -> Message | None:
    stmt = select(Message).where(Message.case_id == case_id, Message.type == "report_draft").order_by(Message.id.desc())
    return (await db.execute(stmt)).scalars().first()


async def ensure_pdf(db: AsyncSession, case: Case, report: Report) -> tuple[ReportPdf, bool]:
    existing = await pdf_for(db, report.id)
    if existing is not None:
        return existing, False
    data, pages = render_report_pdf(
        case_title=case.title, date_label=kst_date_label(report.created_at),
        version_label=ordinal_label(report.version), sections=report.sections, disclaimer=DISCLAIMER,
    )
    key = f"pdfs/{case.id}/{report.id}.pdf"
    size = await get_storage().put_bytes(key, data)
    pdf = ReportPdf(id=new_id(), report_id=report.id, storage_key=key, filename=report_pdf_filename(case.title, report.created_at), size_bytes=size, created_at=now_utc())
    db.add(pdf)
    if report.page_count != pages:
        report.page_count = pages
    await db.commit()
    return pdf, True


def pdf_response_dict(case: Case, report: Report, pdf: ReportPdf) -> dict:
    return {
        "pdfId": pdf.id, "version": report.version, "filename": pdf.filename, "sizeBytes": pdf.size_bytes,
        "downloadUrl": f"/api/v1/cases/{case.id}/report/versions/{report.version}/pdf", "createdAt": to_kst_iso(pdf.created_at),
    }


async def _action_create_report(db: AsyncSession, case: Case) -> None:
    await start_report(db, case)


register_action("create_report", _action_create_report)
```

- [ ] **Step 5: Job 핸들러** — `app/jobs/report.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import Section, WriteInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.jobs.runner import JobHandler
from app.models import Case, Report
from app.services import cases as case_service
from app.services.report import draft_card, draft_payload
from app.services.verdict import recent_turns, snapshot


def make_report_handler(revision_request: str | None) -> JobHandler:
    async def run_report(db: AsyncSession, case_id: str, job_id: str) -> None:
        verdict = await case_service.active_verdict(db, case_id)
        analysis = await case_service.get_analysis(db, case_id)
        latest = await case_service.latest_report(db, case_id)
        result = await get_agent().write(WriteInput(
            kind="report",
            messages=await recent_turns(db, case_id),
            facts=analysis.facts if analysis else {},
            verdict=snapshot(verdict),
            revision_request=revision_request,
            previous_sections=[Section(**s) for s in latest.sections] if latest else None,
            report_sections=None,
        ))
        if not result.sections:
            raise RuntimeError("Agent가 경위서 sections를 돌려주지 않았어요")
        report = Report(
            id=new_id(), case_id=case_id, version=(latest.version + 1) if latest else 1,
            sections=[s.model_dump() for s in result.sections], caveat=result.caveat,
            page_count=result.page_count or 1, revision_request=revision_request, created_at=now_utc(),
        )
        db.add(report)
        case = await db.get(Case, case_id)
        case_service.touch(case)
        await db.commit()

        card = await draft_card(db, case_id)
        if card is None:
            await case_service.add_message(db, case_id, "assistant", "report_draft", draft_payload(report))
        else:
            await case_service.update_message(db, card, draft_payload(report))

    return run_report
```

- [ ] **Step 6: 라우터** — `app/api/report.py`

```python
from urllib.parse import quote

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import owned_case
from app.errors import ApiError
from app.models import Case
from app.schemas.report import RevisionRequest
from app.services import report as report_service
from app.storage import get_storage

router = APIRouter(prefix="/cases/{case_id}/report", tags=["report"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_report(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    job, to_version, _ = await report_service.start_report(db, case)
    return {"jobId": job.id, "kind": "report", "status": job.status, "version": to_version}


@router.post("/revisions", status_code=status.HTTP_202_ACCEPTED)
async def revise(body: RevisionRequest, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    job, to_version, from_version = await report_service.start_report(db, case, body.request)
    return {"jobId": job.id, "kind": "report", "status": job.status, "fromVersion": from_version, "toVersion": to_version}


@router.get("/versions")
async def versions(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return await report_service.versions_list(db, case.id)


@router.get("/versions/{version}")
async def full(version: str, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return report_service.full_text(await report_service.report_by_version(db, case.id, version))


@router.post("/versions/{version}/pdf")
async def create_pdf(version: str, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)):
    report = await report_service.report_by_version(db, case.id, version)
    pdf, created = await report_service.ensure_pdf(db, case, report)
    return JSONResponse(status_code=201 if created else 200, content=report_service.pdf_response_dict(case, report, pdf))


@router.get("/versions/{version}/pdf")
async def download_pdf(version: str, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)):
    report = await report_service.report_by_version(db, case.id, version)
    pdf = await report_service.pdf_for(db, report.id)
    if pdf is None:
        raise ApiError("NOT_FOUND")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(pdf.filename)}",
        "Content-Length": str(pdf.size_bytes),
    }
    return StreamingResponse(get_storage().read_range(pdf.storage_key, 0, pdf.size_bytes - 1), media_type="application/pdf", headers=headers)
```

`app/main.py`에 등록. `app/main.py`는 `app.api.report`를 import하므로 `services.report`의 `register_action`이 앱 생성 시 실행된다.

- [ ] **Step 7: 통과 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_report.py -v` → PASS (6). 이어서 `pytest -q` 전체.

- [ ] **Step 8: 커밋**

```bash
git add app/services/report.py app/jobs/report.py app/api/report.py app/schemas/report.py app/main.py tests/conftest.py tests/api/test_report.py
git commit -m "feat(report): 사건경위서 초안·다시 쓰기·버전·전문·PDF API"
```

---

### Task 3: 반박의견서 초안 · 조회 · 수정 (G-1 ~ G-3)

**Files:**
- Create: `app/services/rebuttal.py`, `app/jobs/rebuttal.py`, `app/api/rebuttal.py`, `app/schemas/rebuttal.py`, `tests/api/test_rebuttal.py`
- Modify: `app/main.py`, `tests/conftest.py` (`reported_case` 픽스처)

**Interfaces:**
- `services/rebuttal.py`:
  - `MAX_ATTACH_BYTES = 25 * 1024 * 1024`
  - `missing_for(db, case_id) -> list[str]` (`verdict` · `report`)
  - `start_rebuttal(db, case) -> Job` — missing 있으면 `REBUTTAL_LOCKED(fields={"missing": ",".join(missing)})`; 이미 `sent`면 `REBUTTAL_ALREADY_SENT`
  - `auto_subject(claim_number) -> str`
  - `default_attachments(db, case) -> list[dict]` (`{kind, refId, name, sizeBytes, included}`)
  - `resolve_attachments(db, rebuttal) -> list[dict]` (현재 크기 반영 · 25MB 규칙 → `note`)
  - `can_send(rebuttal) -> tuple[bool, list[str]]`
  - `view(db, case, rebuttal, user) -> dict` (G-2) · `draft_payload(rebuttal, attachments) -> dict` (§4.9)
  - `apply_patch(db, rebuttal, body: RebuttalPatch) -> Rebuttal`
  - import 시 `register_action("create_rebuttal", ...)`
- `jobs/rebuttal.py`: `run_rebuttal(db, case_id, job_id)`
- 픽스처 `reported_case`: judged_case + 경위서 1판

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_rebuttal.py`

```python
async def test_rebuttal_locked_without_verdict_and_report(client, auth_headers, case_id):
    res = await client.post(f"/cases/{case_id}/rebuttal", headers=auth_headers)
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "REBUTTAL_LOCKED" and err["fields"] == {"missing": "verdict,report"}
    assert err["actions"] == [{"label": "사건경위서 먼저 만들기", "type": "create_report"}]


async def test_rebuttal_locked_without_report(client, auth_headers, judged_case):
    res = await client.post(f"/cases/{judged_case}/rebuttal", headers=auth_headers)
    assert res.json()["error"]["fields"] == {"missing": "report"}
    assert (await client.get(f"/cases/{judged_case}/rebuttal", headers=auth_headers)).status_code == 404


async def test_rebuttal_draft_flow(client, auth_headers, reported_case, sse, settle):
    tap = await sse(reported_case)
    res = await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    assert res.status_code == 202 and res.json()["kind"] == "rebuttal"
    await settle()
    frames = await tap.take(3)
    assert '"type": "rebuttal_draft"' in frames[1]
    assert '"canSend": false' in frames[1] and '"blockedBy": ["recipient", "claimNumber"]' in frames[1]
    assert '"subject": "과실비율 재검토 요청 (접수번호는 아직 안 넣었어요)"' in frames[1]
    assert '"recipientPlaceholder": "아직 안 정했어요"' in frames[1]
    assert '"rebuttal": {"exists": true, "locked": false, "label": "작성 중"}' in frames[2]
    detail = (await client.get(f"/cases/{reported_case}", headers=auth_headers)).json()
    assert detail["stages"]["rebuttal"] == {"state": "in_progress"}

    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "draft" and g2["editable"] is True and g2["subjectAuto"] is True
    assert g2["fromEmail"] == "hyun@example.com"
    kinds = [a["kind"] for a in g2["attachments"]]
    assert kinds == ["report_pdf", "video"] and all(a["included"] for a in g2["attachments"])
    assert g2["attachments"][0]["name"] == "사건경위서.pdf" and g2["attachments"][1]["name"] == "blackbox_0822.mp4"
    assert g2["attachmentNotice"].startswith("영상에는 상대 차량 번호판")


async def test_rebuttal_patch_rules(client, auth_headers, reported_case, settle):
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    url = f"/cases/{reported_case}/rebuttal"

    res = await client.patch(url, json={"recipient": "not-an-email"}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "RECIPIENT_INVALID"

    res = await client.patch(url, json={"recipient": "kim@insu.co.kr"}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["canSend"] is False and res.json()["blockedBy"] == ["claimNumber"]

    res = await client.patch(url, json={"claimNumber": "2026-08-0000"}, headers=auth_headers)
    body = res.json()
    assert body["canSend"] is True and body["blockedBy"] == []
    assert body["subject"] == "과실비율 재검토 요청 (접수번호 2026-08-0000)" and body["subjectAuto"] is True

    res = await client.patch(url, json={"subject": "직접 쓴 제목"}, headers=auth_headers)
    assert res.json()["subjectAuto"] is False
    res = await client.patch(url, json={"claimNumber": "X-1"}, headers=auth_headers)
    assert res.json()["subject"] == "직접 쓴 제목"

    video_ref = body["attachments"][1]["refId"]
    res = await client.patch(url, json={"attachments": [{"refId": video_ref, "included": False}]}, headers=auth_headers)
    assert [a["included"] for a in res.json()["attachments"]] == [True, False]

    res = await client.patch(url, json={"body": ""}, headers=auth_headers)
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_large_video_excluded_from_attachments(client, auth_headers, reported_case, settle, monkeypatch):
    from app.services import rebuttal as rs

    monkeypatch.setattr(rs, "MAX_ATTACH_BYTES", 1000)
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    video = g2["attachments"][1]
    assert video["included"] is False and video["note"] == "용량이 커서 첨부할 수 없어요"


async def test_chat_create_rebuttal_action_locked_card(client, auth_headers, judged_case, settle):
    await client.post(f"/cases/{judged_case}/messages", json={"text": "바로 반박의견서 보낼 수 있어요?"}, headers=auth_headers)
    await settle()
    msgs = (await client.get(f"/cases/{judged_case}/messages", params={"limit": 50}, headers=auth_headers)).json()["items"]
    assert msgs[-1]["type"] == "rebuttal_locked" and msgs[-1]["payload"]["missing"] == ["report"]
```

`tests/conftest.py`에 추가:

```python
@pytest.fixture
async def reported_case(client, auth_headers, judged_case, settle):
    r = await client.post(f"/cases/{judged_case}/report", headers=auth_headers)
    assert r.status_code == 202, r.text
    await settle()
    return judged_case
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_rebuttal.py -v` → FAIL

- [ ] **Step 3: 스키마** — `app/schemas/rebuttal.py`

```python
from pydantic import Field

from app.schemas.base import CamelModel


class AttachmentPatch(CamelModel):
    ref_id: str
    included: bool


class RebuttalPatch(CamelModel):
    recipient: str | None = None
    claim_number: str | None = None
    subject: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    attachments: list[AttachmentPatch] | None = None
```

- [ ] **Step 4: 서비스** — `app/services/rebuttal.py`

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.content.texts import (
    ATTACHMENT_NOTICE_CARD, ATTACHMENT_NOTICE_FULL, ATTACHMENT_TOO_LARGE_NOTE,
    CLAIM_NUMBER_HINT, CLAIM_NUMBER_HINT_SHORT, RECIPIENT_PLACEHOLDER,
)
from app.errors import ApiError
from app.ids import new_id
from app.models import Case, Job, Message, Rebuttal, ReportPdf, User, Video
from app.schemas.rebuttal import RebuttalPatch
from app.security import is_email
from app.services import cases as case_service
from app.services.actions import register_action
from app.services.report import pdf_for

MAX_ATTACH_BYTES = 25 * 1024 * 1024


def auto_subject(claim_number: str | None) -> str:
    return f"과실비율 재검토 요청 (접수번호 {claim_number})" if claim_number else "과실비율 재검토 요청 (접수번호는 아직 안 넣었어요)"


async def missing_for(db: AsyncSession, case_id: str) -> list[str]:
    missing = []
    if await case_service.active_verdict(db, case_id) is None:
        missing.append("verdict")
    if await case_service.latest_report(db, case_id) is None:
        missing.append("report")
    return missing


async def start_rebuttal(db: AsyncSession, case: Case) -> Job:
    missing = await missing_for(db, case.id)
    if missing:
        raise ApiError("REBUTTAL_LOCKED", fields={"missing": ",".join(missing)})
    existing = await case_service.get_rebuttal(db, case.id)
    if existing is not None and existing.status == "sent":
        raise ApiError("REBUTTAL_ALREADY_SENT")
    from app.jobs.rebuttal import run_rebuttal
    from app.jobs.runner import runner

    return await runner.start(db, case.id, "rebuttal", run_rebuttal)


async def default_attachments(db: AsyncSession, case: Case) -> list[dict]:
    out = []
    report = await case_service.latest_report(db, case.id)
    if report is not None:
        pdf = await pdf_for(db, report.id)
        out.append({"kind": "report_pdf", "refId": report.id, "name": "사건경위서.pdf", "sizeBytes": pdf.size_bytes if pdf else None, "included": True})
    video = await case_service.get_video(db, case.id)
    if video is not None:
        out.append({"kind": "video", "refId": video.id, "name": video.filename, "sizeBytes": video.size_bytes, "included": True})
    return out


async def resolve_attachments(db: AsyncSession, rebuttal: Rebuttal) -> list[dict]:
    """저장된 첨부 목록에 현재 크기와 25MB 규칙을 반영한다."""
    items = [dict(a) for a in rebuttal.attachments]
    for a in items:
        a["note"] = None
        if a["kind"] == "report_pdf":
            pdf = await pdf_for(db, a["refId"])
            a["sizeBytes"] = pdf.size_bytes if pdf else a.get("sizeBytes")
        elif a["kind"] == "video":
            video = await db.get(Video, a["refId"])
            a["sizeBytes"] = video.size_bytes if video else a.get("sizeBytes")
    total = sum(a.get("sizeBytes") or 0 for a in items if a["included"])
    if total > MAX_ATTACH_BYTES:
        for a in items:
            if a["kind"] == "video" and a["included"]:
                a["included"] = False
                a["note"] = ATTACHMENT_TOO_LARGE_NOTE
    return items


def can_send(rebuttal: Rebuttal) -> tuple[bool, list[str]]:
    blocked = []
    if not is_email(rebuttal.recipient):
        blocked.append("recipient")
    if not (rebuttal.claim_number or "").strip():
        blocked.append("claimNumber")
    return not blocked, blocked


def draft_payload(rebuttal: Rebuttal, attachments: list[dict]) -> dict:
    ok, blocked = can_send(rebuttal)
    return {
        "rebuttalId": rebuttal.id,
        "recipient": rebuttal.recipient,
        "recipientPlaceholder": RECIPIENT_PLACEHOLDER,
        "claimNumber": rebuttal.claim_number,
        "claimNumberHint": CLAIM_NUMBER_HINT,
        "subject": rebuttal.subject,
        "bodyPreview": rebuttal.body[:80] + (" …" if len(rebuttal.body) > 80 else ""),
        "attachments": [{"kind": a["kind"], "name": a["name"], "included": a["included"]} for a in attachments],
        "attachmentNotice": ATTACHMENT_NOTICE_CARD,
        "canSend": ok,
        "blockedBy": blocked,
    }


async def view(db: AsyncSession, case: Case, rebuttal: Rebuttal, user: User) -> dict:
    attachments = await resolve_attachments(db, rebuttal)
    ok, blocked = can_send(rebuttal)
    return {
        "rebuttalId": rebuttal.id,
        "status": rebuttal.status,
        "recipient": rebuttal.recipient,
        "claimNumber": rebuttal.claim_number,
        "claimNumberHint": CLAIM_NUMBER_HINT_SHORT,
        "subject": rebuttal.subject,
        "subjectAuto": rebuttal.subject_auto,
        "body": rebuttal.body,
        "attachments": attachments,
        "attachmentNotice": ATTACHMENT_NOTICE_FULL,
        "fromEmail": user.email,
        "canSend": ok and rebuttal.status == "draft",
        "blockedBy": blocked,
        "editable": rebuttal.status == "draft",
    }


async def draft_card(db: AsyncSession, case_id: str) -> Message | None:
    stmt = select(Message).where(Message.case_id == case_id, Message.type == "rebuttal_draft").order_by(Message.id.desc())
    return (await db.execute(stmt)).scalars().first()


async def apply_patch(db: AsyncSession, rebuttal: Rebuttal, patch: RebuttalPatch) -> Rebuttal:
    if rebuttal.status == "sent":
        raise ApiError("REBUTTAL_ALREADY_SENT")
    data = patch.model_dump(exclude_unset=True)
    if "recipient" in data:
        r = (data["recipient"] or "").strip()
        if r and not is_email(r):
            raise ApiError("RECIPIENT_INVALID", fields={"recipient": "이메일 주소가 아니에요. name@company.co.kr 처럼 고치면 보내기가 열려요."})
        rebuttal.recipient = r or None
    if "claim_number" in data:
        rebuttal.claim_number = (data["claim_number"] or "").strip() or None
    if "subject" in data and data["subject"] is not None:
        rebuttal.subject = data["subject"].strip() or rebuttal.subject
        rebuttal.subject_auto = False
    if "body" in data and data["body"] is not None:
        rebuttal.body = data["body"]
    if "attachments" in data and data["attachments"] is not None:
        wanted = {a["ref_id"]: a["included"] for a in data["attachments"]}
        rebuttal.attachments = [{**a, "included": wanted.get(a["refId"], a["included"])} for a in rebuttal.attachments]
    if rebuttal.subject_auto:
        rebuttal.subject = auto_subject(rebuttal.claim_number)
    rebuttal.updated_at = now_utc()
    await db.commit()
    return rebuttal


async def _action_create_rebuttal(db: AsyncSession, case: Case) -> None:
    await start_rebuttal(db, case)


register_action("create_rebuttal", _action_create_rebuttal)
```

- [ ] **Step 5: Job 핸들러** — `app/jobs/rebuttal.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import Section, WriteInput
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.models import Case, Rebuttal
from app.services import cases as case_service
from app.services.rebuttal import auto_subject, default_attachments, draft_card, draft_payload, resolve_attachments
from app.services.verdict import recent_turns, snapshot


async def run_rebuttal(db: AsyncSession, case_id: str, job_id: str) -> None:
    case = await db.get(Case, case_id)
    verdict = await case_service.active_verdict(db, case_id)
    analysis = await case_service.get_analysis(db, case_id)
    report = await case_service.latest_report(db, case_id)
    result = await get_agent().write(WriteInput(
        kind="rebuttal", messages=await recent_turns(db, case_id), facts=analysis.facts if analysis else {},
        verdict=snapshot(verdict), revision_request=None, previous_sections=None,
        report_sections=[Section(**s) for s in report.sections] if report else None,
    ))
    if not result.body:
        raise RuntimeError("Agent가 반박의견서 body를 돌려주지 않았어요")

    rebuttal = await case_service.get_rebuttal(db, case_id)
    now = now_utc()
    if rebuttal is None:
        rebuttal = Rebuttal(
            id=new_id(), case_id=case_id, recipient=None, claim_number=None, subject=auto_subject(None), subject_auto=True,
            body=result.body, attachments=await default_attachments(db, case), status="draft", created_at=now, updated_at=now,
        )
        db.add(rebuttal)
    else:
        rebuttal.body = result.body
        rebuttal.attachments = await default_attachments(db, case)
        rebuttal.updated_at = now
    case_service.touch(case)
    await db.commit()

    payload = draft_payload(rebuttal, await resolve_attachments(db, rebuttal))
    card = await draft_card(db, case_id)
    if card is None:
        await case_service.add_message(db, case_id, "assistant", "rebuttal_draft", payload)
    else:
        await case_service.update_message(db, card, payload)
```

- [ ] **Step 6: 라우터 (G-1 ~ G-3)** — `app/api/rebuttal.py`

```python
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, owned_case
from app.errors import ApiError
from app.models import Case, User
from app.schemas.rebuttal import RebuttalPatch
from app.services import cases as case_service
from app.services import rebuttal as rebuttal_service

router = APIRouter(prefix="/cases/{case_id}/rebuttal", tags=["rebuttal"])


async def _rebuttal_or_404(db, case_id):
    r = await case_service.get_rebuttal(db, case_id)
    if r is None:
        raise ApiError("NOT_FOUND")
    return r


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    job = await rebuttal_service.start_rebuttal(db, case)
    return {"jobId": job.id, "kind": "rebuttal", "status": job.status}


@router.get("")
async def get(case: Case = Depends(owned_case), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return await rebuttal_service.view(db, case, await _rebuttal_or_404(db, case.id), user)


@router.patch("")
async def patch(body: RebuttalPatch, case: Case = Depends(owned_case), user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    rebuttal = await rebuttal_service.apply_patch(db, await _rebuttal_or_404(db, case.id), body)
    return await rebuttal_service.view(db, case, rebuttal, user)
```

`app/main.py`에 등록.

- [ ] **Step 7: 통과 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_rebuttal.py -v` → PASS (6)

- [ ] **Step 8: 커밋**

```bash
git add app/services/rebuttal.py app/jobs/rebuttal.py app/api/rebuttal.py app/schemas/rebuttal.py app/main.py tests/conftest.py tests/api/test_rebuttal.py
git commit -m "feat(rebuttal): 반박의견서 초안 Job과 조회·수정, 잠금·canSend 규칙"
```

---

### Task 4: 발송 (G-4) · 발송 기록 (G-5)

**Files:**
- Create: `app/mail/templates.py`, `tests/api/test_rebuttal_send.py`
- Modify: `app/services/rebuttal.py`, `app/api/rebuttal.py`

**Interfaces:**
- `mail/templates.py`: `rebuttal_body(body: str, user_email: str, video_dropped: bool) -> str` · `SERVICE_DOMAIN = "cardefender.kr"`
- `services/rebuttal.py`:
  - `send(db, case, rebuttal, user, idempotency_key) -> dict` — 성공 응답 dict. 실패 시 `MAIL_SEND_FAILED`
  - `send_logs(db, case_id) -> dict` (G-5)
- `POST /cases/{caseId}/rebuttal/send` (헤더 `Idempotency-Key`) · `GET /cases/{caseId}/rebuttal/sends`

- [ ] **Step 1: 실패하는 테스트** — `tests/api/test_rebuttal_send.py`

```python
import uuid

from app.mail import get_mailer


async def ready(client, h, case_id, settle):
    await client.post(f"/cases/{case_id}/rebuttal", headers=h)
    await settle()
    await client.patch(f"/cases/{case_id}/rebuttal", json={"recipient": "kim@insu.co.kr", "claimNumber": "2026-08-0000"}, headers=h)


async def test_send_requires_idempotency_key(client, auth_headers, reported_case, settle):
    await ready(client, auth_headers, reported_case, settle)
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=auth_headers)
    assert res.status_code == 400 and res.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_send_validation(client, auth_headers, reported_case, settle):
    await client.post(f"/cases/{reported_case}/rebuttal", headers=auth_headers)
    await settle()
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 422 and res.json()["error"]["code"] == "RECIPIENT_INVALID"
    await client.patch(f"/cases/{reported_case}/rebuttal", json={"recipient": "kim@insu.co.kr"}, headers=auth_headers)
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 422 and res.json()["error"]["code"] == "CLAIM_NUMBER_REQUIRED"


async def test_send_success_flow(client, auth_headers, reported_case, settle, sse):
    await ready(client, auth_headers, reported_case, settle)
    tap = await sse(reported_case)
    key = str(uuid.uuid4())
    h = {**auth_headers, "Idempotency-Key": key}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["recipient"] == "kim@insu.co.kr" and body["fromEmail"] == "hyun@example.com" and body["attachmentCount"] == 2
    assert body["sentAt"].endswith("+09:00") and body["sendLogId"]

    sent = get_mailer().sent[-1]
    assert sent.to == "kim@insu.co.kr" and sent.reply_to == "hyun@example.com" and sent.sender_email == "hyun@example.com"
    assert sent.display_name == "카-디펜더 (hyun@example.com)"
    assert sent.subject == "과실비율 재검토 요청 (접수번호 2026-08-0000)"
    assert [a.filename for a in sent.attachments][0].startswith("사건경위서_") and sent.attachments[1].filename == "blackbox_0822.mp4"
    assert sent.attachments[0].content[:4] == b"%PDF"
    assert "이 메일은 카-디펜더(cardefender.kr)를 통해 hyun@example.com 님이 보냈습니다." in sent.body_text

    frames = await tap.take(3)
    assert '"type": "sent"' in frames[0] and '"attachmentCount": 2' in frames[0] and '"nextSteps"' in frames[0]
    assert "event: rebuttal.sent" in frames[1]
    assert '"status": "sent"' in frames[2] and '"rebuttal": {"exists": true, "locked": false, "label": "발송 완료 · ' in frames[2]

    detail = (await client.get(f"/cases/{reported_case}", headers=auth_headers)).json()
    assert detail["stages"]["rebuttal"] == {"state": "done"}

    # 같은 키 재호출: 메일 안 나가고 같은 결과
    again = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert again.status_code == 200 and again.json()["sendLogId"] == body["sendLogId"]
    assert len(get_mailer().sent) == 1

    # 새 키로 재호출: 이미 보냄
    h2 = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    assert (await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h2)).json()["error"]["code"] == "REBUTTAL_ALREADY_SENT"
    assert (await client.patch(f"/cases/{reported_case}/rebuttal", json={"body": "x"}, headers=auth_headers)).json()["error"]["code"] == "REBUTTAL_ALREADY_SENT"
    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "sent" and g2["editable"] is False and g2["canSend"] is False

    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()
    assert len(logs["items"]) == 1 and logs["items"][0]["result"] == "sent" and logs["items"][0]["attachmentNames"][1] == "blackbox_0822.mp4"


async def test_send_failure_keeps_draft_and_logs_failed(client, auth_headers, reported_case, settle, monkeypatch):
    from app.mail.base import MailSendError

    await ready(client, auth_headers, reported_case, settle)

    async def boom(msg):
        raise MailSendError("smtp down")

    monkeypatch.setattr(get_mailer(), "send", boom)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 502
    err = res.json()["error"]
    assert err["code"] == "MAIL_SEND_FAILED" and err["retryable"] is True and err["actions"] == [{"label": "다시 시도", "type": "retry_send"}]
    g2 = (await client.get(f"/cases/{reported_case}/rebuttal", headers=auth_headers)).json()
    assert g2["status"] == "draft" and g2["editable"] is True
    logs = (await client.get(f"/cases/{reported_case}/rebuttal/sends", headers=auth_headers)).json()
    assert logs["items"][0]["result"] == "failed"


async def test_send_drops_video_when_too_large(client, auth_headers, reported_case, settle, monkeypatch):
    from app.services import rebuttal as rs

    monkeypatch.setattr(rs, "MAX_ATTACH_BYTES", 1000)
    await ready(client, auth_headers, reported_case, settle)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    res = await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    assert res.status_code == 200 and res.json()["attachmentCount"] == 1
    sent = get_mailer().sent[-1]
    assert "블랙박스 영상은 용량 제한으로 첨부하지 못했습니다." in sent.body_text


async def test_send_log_survives_case_delete(client, auth_headers, reported_case, settle):
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import SendLog

    await ready(client, auth_headers, reported_case, settle)
    h = {**auth_headers, "Idempotency-Key": str(uuid.uuid4())}
    await client.post(f"/cases/{reported_case}/rebuttal/send", headers=h)
    await client.delete(f"/cases/{reported_case}", headers=auth_headers)
    async with session_scope() as db:
        assert len((await db.execute(select(SendLog))).scalars().all()) == 1
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_rebuttal_send.py -v` → FAIL

- [ ] **Step 3: 메일 템플릿** — `app/mail/templates.py`

```python
SERVICE_DOMAIN = "cardefender.kr"


def rebuttal_body(body: str, user_email: str, video_dropped: bool) -> str:
    parts = [body.rstrip(), ""]
    if video_dropped:
        parts.append("블랙박스 영상은 용량 제한으로 첨부하지 못했습니다.")
        parts.append("")
    parts.append(f"이 메일은 카-디펜더({SERVICE_DOMAIN})를 통해 {user_email} 님이 보냈습니다.")
    parts.append("회신은 위 주소로 전달됩니다.")
    return "\n".join(parts)
```

- [ ] **Step 4: 서비스에 발송 추가** — `app/services/rebuttal.py` 끝에

```python
from app.clock import to_kst_iso
from app.content.texts import SENT_NEXT_STEPS, SENT_NOTICE
from app.mail import MailAttachment, MailMessage, MailSendError, get_mailer
from app.mail.templates import rebuttal_body
from app.models import SendLog
from app.services.report import ensure_pdf, report_by_version
from app.sse.hub import hub
from app.storage import get_storage


def _send_result(log: SendLog, attachment_count: int) -> dict:
    return {"sendLogId": log.id, "sentAt": to_kst_iso(log.sent_at), "fromEmail": log.from_email, "recipient": log.recipient, "attachmentCount": attachment_count}


async def send(db: AsyncSession, case: Case, rebuttal: Rebuttal, user: User, idempotency_key: str | None) -> dict:
    if not idempotency_key:
        raise ApiError("IDEMPOTENCY_KEY_REQUIRED")
    prior = (await db.execute(select(SendLog).where(SendLog.idempotency_key == idempotency_key))).scalar_one_or_none()
    if prior is not None:
        if prior.result == "sent":
            return _send_result(prior, len(prior.attachment_names))
        raise ApiError("MAIL_SEND_FAILED")
    if rebuttal.status == "sent":
        raise ApiError("REBUTTAL_ALREADY_SENT")
    if not is_email(rebuttal.recipient):
        raise ApiError("RECIPIENT_INVALID", fields={"recipient": "이메일 주소가 아니에요. name@company.co.kr 처럼 고치면 보내기가 열려요."})
    if not (rebuttal.claim_number or "").strip():
        raise ApiError("CLAIM_NUMBER_REQUIRED", fields={"claimNumber": "접수번호를 넣어야 보험사가 사건을 찾을 수 있어요. 보험사 접수 문자나 메일에 있어요."})

    # 첨부 준비 (PDF는 없으면 지금 만든다)
    for a in rebuttal.attachments:
        if a["kind"] == "report_pdf" and a["included"]:
            report = await report_by_version(db, case.id, "latest")
            await ensure_pdf(db, case, report)
            a["refId"] = report.id
    rebuttal.attachments = [dict(a) for a in rebuttal.attachments]
    attachments = await resolve_attachments(db, rebuttal)
    video_dropped = any(a["kind"] == "video" and a.get("note") for a in attachments)
    files: list[MailAttachment] = []
    storage = get_storage()
    for a in attachments:
        if not a["included"]:
            continue
        if a["kind"] == "report_pdf":
            pdf = await pdf_for(db, a["refId"])
            files.append(MailAttachment(filename=pdf.filename, content=await storage.read_bytes(pdf.storage_key), mime_type="application/pdf"))
        elif a["kind"] == "video":
            video = await db.get(Video, a["refId"])
            if video is not None:
                files.append(MailAttachment(filename=video.filename, content=await storage.read_bytes(video.storage_key), mime_type=video.mime_type))

    msg = MailMessage(
        to=rebuttal.recipient, subject=rebuttal.subject,
        body_text=rebuttal_body(rebuttal.body, user.email, video_dropped),
        reply_to=user.email, sender_email=user.email, display_name=f"카-디펜더 ({user.email})", attachments=files,
    )
    now = now_utc()
    log = SendLog(
        id=new_id(), case_id=case.id, rebuttal_id=rebuttal.id, idempotency_key=idempotency_key, sent_at=now,
        from_email=user.email, recipient=rebuttal.recipient, subject=rebuttal.subject,
        attachment_names=[f.filename for f in files], result="failed", provider_message_id=None, error=None,
    )
    try:
        log.provider_message_id = await get_mailer().send(msg)
        log.result = "sent"
    except MailSendError as e:
        log.error = str(e)
        db.add(log)
        await db.commit()
        raise ApiError("MAIL_SEND_FAILED") from e

    rebuttal.status = "sent"
    rebuttal.updated_at = now
    db.add(log)
    case_service.set_status(case, "sent")
    await db.commit()

    await case_service.add_message(db, case.id, "assistant", "sent", {
        "sendLogId": log.id, "sentAt": to_kst_iso(now), "recipient": rebuttal.recipient,
        "attachmentCount": len(files), "notice": SENT_NOTICE, "nextSteps": list(SENT_NEXT_STEPS),
    })
    hub.publish(case.id, "rebuttal.sent", {"sendLogId": log.id, "sentAt": to_kst_iso(now), "recipient": rebuttal.recipient})
    await case_service.publish_case_updated(db, case.id)
    return _send_result(log, len(files))


async def send_logs(db: AsyncSession, case_id: str) -> dict:
    stmt = select(SendLog).where(SendLog.case_id == case_id).order_by(SendLog.sent_at.desc(), SendLog.id.desc())
    items = [{
        "sendLogId": s.id, "sentAt": to_kst_iso(s.sent_at), "fromEmail": s.from_email, "recipient": s.recipient,
        "subject": s.subject, "attachmentCount": len(s.attachment_names), "attachmentNames": s.attachment_names, "result": s.result,
    } for s in (await db.execute(stmt)).scalars().all()]
    return {"items": items}
```

- [ ] **Step 5: 라우터 추가** — `app/api/rebuttal.py`

```python
from fastapi import Header

from app.services import rebuttal as rebuttal_service


@router.post("/send")
async def send(
    case: Case = Depends(owned_case), user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    rebuttal = await _rebuttal_or_404(db, case.id)
    return await rebuttal_service.send(db, case, rebuttal, user, idempotency_key)


@router.get("/sends")
async def sends(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return await rebuttal_service.send_logs(db, case.id)
```

- [ ] **Step 6: 통과 확인** — Run: `.venv/Scripts/python -m pytest tests/api/test_rebuttal_send.py -v` → PASS (6). 이어서 `pytest -q` 전체.

- [ ] **Step 7: 커밋**

```bash
git add app/mail/templates.py app/services/rebuttal.py app/api/rebuttal.py tests/api/test_rebuttal_send.py
git commit -m "feat(rebuttal): 멱등키 기반 메일 발송과 발송 기록 API"
```

---

### Task 5: 시드 · 스모크 점검 · 문서 마무리

**Files:**
- Create: `app/seed.py`, `tests/test_seed.py`
- Modify: `README.md`, `docs/superpowers/specs/2026-09-03-backend-design.md` (PDF 폰트를 NanumGothic으로 정정)

**Interfaces:**
- `python -m app.seed` → 데모 사용자 `demo@cardefender.kr / demo1234` 와 `closed` 사건 1건 (`주차장 후진 접촉 · 07-14`) 생성. 이미 있으면 건너뜀. `seed(db) -> None`

- [ ] **Step 1: 실패하는 테스트** — `tests/test_seed.py`

```python
from sqlalchemy import select

from app.db import session_scope
from app.models import Case, User
from app.seed import seed


async def test_seed_is_idempotent(app):
    async with session_scope() as db:
        await seed(db)
        await seed(db)
        users = (await db.execute(select(User).where(User.email == "demo@cardefender.kr"))).scalars().all()
        assert len(users) == 1
        cases = (await db.execute(select(Case).where(Case.user_id == users[0].id))).scalars().all()
        assert len(cases) == 1 and cases[0].status == "closed" and cases[0].title == "주차장 후진 접촉 · 07-14"


async def test_seeded_case_visible_in_list(client, app):
    async with session_scope() as db:
        await seed(db)
    login = await client.post("/auth/login", json={"email": "demo@cardefender.kr", "password": "demo1234"})
    h = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    items = (await client.get("/cases", headers=h)).json()["items"]
    assert items[0]["statusLabel"] == "종결"
    detail = (await client.get(f"/cases/{items[0]['id']}", headers=h)).json()
    assert detail["stages"]["rebuttal"] == {"state": "done"}
```

- [ ] **Step 2: app/seed.py**

```python
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as database
from app.config import get_settings
from app.content.texts import GUIDE_CARD
from app.ids import new_id
from app.models import Case, Message, Report, Rebuttal, User, Verdict
from app.security import hash_password

DEMO_EMAIL = "demo@cardefender.kr"
DEMO_PASSWORD = "demo1234"


async def seed(db: AsyncSession) -> None:
    user = (await db.execute(select(User).where(User.email == DEMO_EMAIL))).scalar_one_or_none()
    t0 = datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc)
    if user is None:
        user = User(id=new_id(), email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), agreed_terms_at=t0, agreed_privacy_at=t0, agreed_video_at=t0, onboarded_at=t0, is_demo=True, created_at=t0)
        db.add(user)
        await db.flush()
    existing = (await db.execute(select(Case).where(Case.user_id == user.id))).scalars().first()
    if existing is not None:
        return
    t1 = datetime(2026, 7, 20, 2, 2, tzinfo=timezone.utc)
    case = Case(id=new_id(), user_id=user.id, title="주차장 후진 접촉 · 07-14", status="closed", created_at=t0, updated_at=t1)
    db.add(case)
    db.add(Message(id=new_id(), case_id=case.id, role="assistant", type="guide", payload=dict(GUIDE_CARD), created_at=t0))
    db.add(Verdict(id=new_id(), case_id=case.id, version=1, ratio_mine=0, ratio_other=100, summary="상대 차량 후진 중 접촉으로 상대 일방과실이에요.", basis={"chart": {"name": "주차장 · 후진 중 접촉", "note": "주차장 사고 유형별 기본 비율"}, "precedents": []}, is_active=True, created_at=t0))
    db.add(Report(id=new_id(), case_id=case.id, version=1, sections=[{"index": 1, "title": "사고 일시 및 장소", "body": "2026년 7월 14일 11시경, 서울시 마포구 지하주차장."}], caveat=None, page_count=1, created_at=t0))
    db.add(Rebuttal(id=new_id(), case_id=case.id, recipient="claims@insu.co.kr", claim_number="2026-07-0001", subject="과실비율 재검토 요청 (접수번호 2026-07-0001)", subject_auto=True, body="…", attachments=[], status="sent", created_at=t0, updated_at=t1))
    await db.commit()


async def main() -> None:
    settings = get_settings()
    database.configure_database(settings.database_url)
    async with database.session_scope() as db:
        await seed(db)
    await database.dispose()
    print("seed 완료")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 통과 확인** — Run: `.venv/Scripts/python -m pytest tests/test_seed.py -v` → PASS. `pytest -q` 전체 PASS.

- [ ] **Step 4: 문서 정리**

- `README.md`의 "로컬 실행" 아래에 `.venv/Scripts/python -m app.seed` 줄과 데모 계정을 추가한다.
- `docs/superpowers/specs/2026-09-03-backend-design.md`의 §0 표와 §8에서 `Pretendard`를 `NanumGothic(OFL)`로 바꾼다.
- `docs/agent-interface.md`에 "판정 `basis.precedents[].body_text`는 E-2에서 그대로 노출된다"는 문장이 있는지 확인.

- [ ] **Step 5: 스모크 점검 (수동)**

```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

다른 터미널에서:

```bash
curl -s localhost:8000/api/v1/health
curl -s -X POST localhost:8000/api/v1/auth/signup -H "Content-Type: application/json" -d "{\"email\":\"a@b.co\",\"password\":\"carguard12\",\"passwordConfirm\":\"carguard12\",\"agreements\":{\"termsOfService\":true,\"privacy\":true,\"videoConsent\":true}}"
```

토큰으로 사건 생성 → `curl -N .../events` 로 SSE 연결 → 메시지·업로드(실제 mp4)·판정·경위서·PDF·반박·발송(mock)까지 한 번 돈다. `ffprobe`가 실제 mp4에서 길이를 뽑는지 확인한다.

- [ ] **Step 6: 커밋**

```bash
git add app/seed.py tests/test_seed.py README.md docs
git commit -m "feat: 시드 스크립트와 문서 마무리"
```

---

## 계획 4 완료 기준 (= 전체 완료)

- 명세 36개 엔드포인트 전부 존재 (A-9 데모 로그인은 명세대로 보류).
- `pytest -q` 전부 통과.
- 스모크 점검 시나리오가 끝까지 돈다.
- `docker compose up -d --build` 후 `GET /api/v1/health`가 `ok` (Docker Desktop을 켠 상태에서 확인).
