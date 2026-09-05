import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import ensure_aware, now_utc
from app.content.texts import GUIDE_CARD, UPLOAD_CTA
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


_last_stamp: datetime | None = None


def next_stamp(previous: datetime | None = None) -> datetime:
    """사건 목록 정렬에 쓰는, 프로세스 안에서 같은 값을 두 번 주지 않는 UTC 시각.

    목록은 `updated_at DESC, id DESC`라서 "방금 만진 사건이 맨 위"가 계약이다. 그런데 시계 해상도가
    거칠면(윈도우는 종종 ~15ms) 서로 다른 두 사건의 시각이 같은 값으로 찍히고, 그러면 두 번째
    정렬 키(id 내림차순)가 이겨서 "나중에 만든 사건"이 "방금 만진 사건"보다 위로 올라간다.
    그래서 이 함수가 발급하는 값은 (1) 직전에 발급한 값보다 크고 (2) 그 행의 기존 값보다 크다.
    같거나 뒤로 가면 1마이크로초를 더한다 — 실시간과의 오차는 발급 횟수 × 1µs로 사실상 없다.

    프로세스 단위 상태다. 레이트리밋·SSE 허브와 마찬가지로 `uvicorn --workers 1` 전제 위에 있다."""
    global _last_stamp
    now = now_utc()
    floor = _last_stamp
    if previous is not None:
        prev = ensure_aware(previous)
        floor = prev if floor is None or prev > floor else floor
    if floor is not None and now <= floor:
        now = floor + timedelta(microseconds=1)
    _last_stamp = now
    return now


def touch(case: Case) -> None:
    case.updated_at = next_stamp(case.updated_at)


def set_status(case: Case, status: str) -> None:
    case.status = status
    touch(case)


async def create_case(db: AsyncSession, user: User) -> Case:
    # 생성 시각도 같은 발급기를 쓴다. 안 그러면 "직전에 만든 사건"과 "방금 만진 사건"의
    # updated_at이 같은 값으로 찍혀(거친 시계) 목록 순서가 뒤집힌다.
    now = next_stamp()
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
        keys.extend(k for k in (video.storage_key, video.playback_key) if k)
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


async def assistant_text(db: AsyncSession, case_id: str, text: str) -> Message:
    """assistant text 카드 한 장. 영상이 아직 없으면 업로드 CTA를 붙인다."""
    has_video = await get_video(db, case_id) is not None
    payload = {"text": text, "cta": None if has_video else dict(UPLOAD_CTA)}
    return await add_message(db, case_id, "assistant", "text", payload)


async def update_message(db: AsyncSession, message: Message, payload: dict) -> Message:
    message.payload = payload
    await db.commit()
    hub.publish(message.case_id, "message.updated", message_dict(message))
    return message
