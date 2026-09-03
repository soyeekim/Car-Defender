import logging
import re

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc, to_kst_iso
from app.content.texts import (
    ATTACHMENT_NOTICE_CARD,
    ATTACHMENT_NOTICE_FULL,
    ATTACHMENT_TOO_LARGE_NOTE,
    CLAIM_NUMBER_HINT,
    CLAIM_NUMBER_HINT_SHORT,
    RECIPIENT_PLACEHOLDER,
    SENT_NEXT_STEPS,
    SENT_NOTICE,
)
from app.errors import ApiError
from app.ids import new_id
from app.mail import MailAttachment, MailMessage, get_mailer
from app.mail.templates import rebuttal_body
from app.models import Case, Job, Message, Rebuttal, SendLog, User, Video
from app.schemas.rebuttal import RebuttalPatch
from app.security import is_email
from app.services import cases as case_service
from app.services.actions import register_action
from app.services.report import ensure_pdf, pdf_for, report_by_version
from app.sse.hub import hub
from app.storage import get_storage

logger = logging.getLogger(__name__)

MAX_ATTACH_BYTES = 25 * 1024 * 1024
ALREADY_SENT_STATUSES = ("sent", "sending")


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
    if rebuttal.status in ALREADY_SENT_STATUSES:
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
        normalized = re.sub(r"[\r\n]+", " ", data["subject"]).strip()
        rebuttal.subject = normalized[:200] or rebuttal.subject
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


def _send_result(log: SendLog, attachment_count: int) -> dict:
    return {"sendLogId": log.id, "sentAt": to_kst_iso(log.sent_at), "fromEmail": log.from_email, "recipient": log.recipient, "attachmentCount": attachment_count}


async def _prior_send_log(db: AsyncSession, case_id: str, idempotency_key: str) -> SendLog | None:
    stmt = select(SendLog).where(SendLog.idempotency_key == idempotency_key, SendLog.case_id == case_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def send(db: AsyncSession, case: Case, rebuttal: Rebuttal, user: User, idempotency_key: str | None) -> dict:
    if not idempotency_key:
        raise ApiError("IDEMPOTENCY_KEY_REQUIRED")
    # rollback은 세션의 객체를 모두 expire시킨다. rollback 이후 case.id/rebuttal.id에
    # 접근하면 동기 지연로딩이 걸려 MissingGreenlet이 나므로, id는 미리 뽑아 둔다.
    case_id = case.id
    rebuttal_id = rebuttal.id
    prior = await _prior_send_log(db, case_id, idempotency_key)
    if prior is not None:
        if prior.result == "sent":
            return _send_result(prior, len(prior.attachment_names))
        raise ApiError("MAIL_SEND_FAILED")
    if rebuttal.status in ALREADY_SENT_STATUSES:
        raise ApiError("REBUTTAL_ALREADY_SENT")
    if not is_email(rebuttal.recipient):
        raise ApiError("RECIPIENT_INVALID", fields={"recipient": "이메일 주소가 아니에요. name@company.co.kr 처럼 고치면 보내기가 열려요."})
    if not (rebuttal.claim_number or "").strip():
        raise ApiError("CLAIM_NUMBER_REQUIRED", fields={"claimNumber": "접수번호를 넣어야 보험사가 사건을 찾을 수 있어요. 보험사 접수 문자나 메일에 있어요."})

    # 첨부 준비 (PDF는 없으면 지금 만든다). 실제 파일 바이트는 아직 읽지 않는다 —
    # 메타데이터(파일명·크기)만으로 SendLog를 먼저 기록해 멱등키를 선점한다.
    for a in rebuttal.attachments:
        if a["kind"] == "report_pdf" and a["included"]:
            report = await report_by_version(db, case_id, "latest")
            await ensure_pdf(db, case, report)
            a["refId"] = report.id
    rebuttal.attachments = [dict(a) for a in rebuttal.attachments]
    attachments = await resolve_attachments(db, rebuttal)
    video_dropped = any(a["kind"] == "video" and a.get("note") for a in attachments)

    attachment_names: list[str] = []
    for a in attachments:
        if not a["included"]:
            continue
        if a["kind"] == "report_pdf":
            pdf = await pdf_for(db, a["refId"])
            attachment_names.append(pdf.filename if pdf else a["name"])
        elif a["kind"] == "video":
            video = await db.get(Video, a["refId"])
            if video is not None:
                attachment_names.append(video.filename)

    now = now_utc()
    log = SendLog(
        id=new_id(), case_id=case_id, rebuttal_id=rebuttal_id, idempotency_key=idempotency_key, sent_at=now,
        from_email=user.email, recipient=rebuttal.recipient, subject=rebuttal.subject,
        attachment_names=attachment_names, result="failed", provider_message_id=None, error="in_progress",
    )
    db.add(log)
    try:
        await db.commit()
    except IntegrityError as e:
        # 같은 idempotency_key로 동시에 들어온 다른 요청이 먼저 기록을 남겼다.
        # 이 요청은 진 쪽이니, 승자의 결과를 멱등 응답으로 돌려준다 (아직 진행 중이면 실패로 본다).
        await db.rollback()
        winner = await _prior_send_log(db, case_id, idempotency_key)
        if winner is not None and winner.result == "sent":
            return _send_result(winner, len(winner.attachment_names))
        raise ApiError("MAIL_SEND_FAILED") from e

    # 초안을 선점한다: draft → sending. 다른 요청이 이미 선점/발송했다면 rowcount가 0이다.
    claim = await db.execute(
        update(Rebuttal).where(Rebuttal.id == rebuttal_id, Rebuttal.status == "draft").values(status="sending")
    )
    if claim.rowcount == 0:
        # 진 쪽: 이 요청이 미리 기록해 둔 플레이스홀더 로그를 지운다 — 이 키는 아직 "쓰이지" 않았으니
        # 재시도할 때 다시 검증(REBUTTAL_ALREADY_SENT)을 받게 하고, G-5 목록에 유령 실패 행을 남기지 않는다.
        await db.delete(log)
        await db.commit()
        raise ApiError("REBUTTAL_ALREADY_SENT")
    await db.commit()
    rebuttal.status = "sending"

    # 여기서부터 실제 발송까지: 어떤 이유로 빠져나가도(예외는 물론, 태스크 취소·프로세스 종료 신호
    # 같은 BaseException 포함) sending에 갇힌 초안을 draft로 되돌린다. 되돌리는 커밋마저 실패하면
    # 그 예외를 바깥으로 새어 나가게 두지 않고 MAIL_SEND_FAILED로 응답한다.
    try:
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
        log.provider_message_id = await get_mailer().send(msg)
        log.result = "sent"
        log.error = None
    except BaseException as e:
        is_ordinary = isinstance(e, Exception)
        if is_ordinary:
            logger.exception("반박의견서 발송 실패: case=%s rebuttal=%s", case_id, rebuttal_id)
        else:
            logger.warning("반박의견서 발송이 중단됐어요(취소 등): case=%s rebuttal=%s (%r)", case_id, rebuttal_id, e)
        log.error = str(e) if is_ordinary else f"{type(e).__name__}: {e}"
        log.result = "failed"
        rebuttal.status = "draft"
        rebuttal.updated_at = now_utc()
        try:
            await db.commit()
        except Exception:
            logger.exception("발송 실패를 되돌리는 커밋도 실패했어요: case=%s rebuttal=%s", case_id, rebuttal_id)
            raise ApiError("MAIL_SEND_FAILED") from e
        if is_ordinary:
            raise ApiError("MAIL_SEND_FAILED") from e
        raise  # 취소 등 BaseException은 되돌린 뒤 그대로 다시 던진다

    rebuttal.status = "sent"
    rebuttal.updated_at = now
    case_service.set_status(case, "sent")
    await db.commit()

    await case_service.add_message(db, case_id, "assistant", "sent", {
        "sendLogId": log.id, "sentAt": to_kst_iso(now), "recipient": rebuttal.recipient,
        "attachmentCount": len(files), "notice": SENT_NOTICE, "nextSteps": list(SENT_NEXT_STEPS),
    })
    hub.publish(case_id, "rebuttal.sent", {"sendLogId": log.id, "sentAt": to_kst_iso(now), "recipient": rebuttal.recipient})
    await case_service.publish_case_updated(db, case_id)
    return _send_result(log, len(files))


async def send_logs(db: AsyncSession, case_id: str) -> dict:
    stmt = select(SendLog).where(SendLog.case_id == case_id).order_by(SendLog.sent_at.desc(), SendLog.id.desc())
    items = [{
        "sendLogId": s.id, "sentAt": to_kst_iso(s.sent_at), "fromEmail": s.from_email, "recipient": s.recipient,
        "subject": s.subject, "attachmentCount": len(s.attachment_names), "attachmentNames": s.attachment_names, "result": s.result,
    } for s in (await db.execute(stmt)).scalars().all()]
    return {"items": items}


async def reset_stuck_sending(db: AsyncSession) -> int:
    """서버가 발송 도중 죽거나 재시작되면 sending에 갇힌 초안이 남을 수 있다.
    기동 시 한 번 모두 draft로 되돌린다 (§8 문서화된 복구 규칙)."""
    result = await db.execute(update(Rebuttal).where(Rebuttal.status == "sending").values(status="draft"))
    await db.commit()
    return result.rowcount or 0
