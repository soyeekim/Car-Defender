import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now_utc
from app.content.texts import GUIDE_CARD
from app.errors import ApiError
from app.ids import new_id
from app.models import (
    Analysis,
    Case,
    Job,
    Message,
    Rebuttal,
    Report,
    ReportPdf,
    SendLog,
    User,
    Verdict,
    Video,
)
from app.services.presenters import CaseBundle, case_detail, message_dict
from app.sse.hub import hub

log = logging.getLogger(__name__)


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
    from app.storage import get_storage

    case_id = case.id
    storage = get_storage()
    keys: list[str] = []
    video = await get_video(db, case_id)
    if video is not None:
        keys.append(video.storage_key)
    for report in (await db.execute(select(Report).where(Report.case_id == case_id))).scalars().all():
        pdf = (await db.execute(select(ReportPdf).where(ReportPdf.report_id == report.id))).scalar_one_or_none()
        if pdf is not None:
            keys.append(pdf.storage_key)

    await db.delete(case)
    await db.commit()

    for key in keys:
        try:
            await storage.delete(key)
        except Exception:
            log.exception("사건 삭제 후 스토리지 정리 실패: %s (case %s)", key, case_id)

    hub.drop(case_id)  # 남은 버퍼와 구독자 정리


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
