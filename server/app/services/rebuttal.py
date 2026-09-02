from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.content.texts import (
    ATTACHMENT_NOTICE_CARD,
    ATTACHMENT_NOTICE_FULL,
    ATTACHMENT_TOO_LARGE_NOTE,
    CLAIM_NUMBER_HINT,
    CLAIM_NUMBER_HINT_SHORT,
    RECIPIENT_PLACEHOLDER,
)
from app.errors import ApiError
from app.models import Case, Job, Message, Rebuttal, User, Video
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
        out.append({
            "kind": "report_pdf", "refId": report.id, "name": "사건경위서.pdf",
            "sizeBytes": pdf.size_bytes if pdf else None, "included": True,
        })
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
        rebuttal.subject = data["subject"].strip()[:200] or rebuttal.subject
        rebuttal.subject_auto = False
    if "body" in data and data["body"] is not None:
        rebuttal.body = data["body"]
    if "attachments" in data and data["attachments"] is not None:
        wanted = {a["ref_id"]: a["included"] for a in data["attachments"]}
        rebuttal.attachments = [{**a, "included": wanted.get(a["refId"], a["included"])} for a in rebuttal.attachments]
    if rebuttal.subject_auto:
        rebuttal.subject = auto_subject(rebuttal.claim_number)[:200]
    rebuttal.updated_at = now_utc()
    await db.commit()
    return rebuttal


async def _action_create_rebuttal(db: AsyncSession, case: Case) -> None:
    await start_rebuttal(db, case)


register_action("create_rebuttal", _action_create_rebuttal)
