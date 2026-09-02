from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.base import ChatTurn, JudgeInput, VerdictSnapshot
from app.agent.loader import get_agent
from app.clock import now_utc
from app.ids import new_id
from app.models import Analysis, Case, Message, Verdict
from app.services import cases as case_service
from app.services.presenters import verdict_payload


async def recent_turns(db: AsyncSession, case_id: str, limit: int = 40, exclude_id: str | None = None) -> list[ChatTurn]:
    stmt = select(Message).where(Message.case_id == case_id, Message.type == "text").order_by(Message.id.desc()).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    rows = [m for m in rows if m.id != exclude_id][:limit]
    rows.reverse()
    return [ChatTurn(role=m.role, text=m.payload.get("text", "")) for m in rows]


def snapshot(v: Verdict | None) -> VerdictSnapshot | None:
    if v is None:
        return None
    return VerdictSnapshot(version=v.version, ratio_mine=v.ratio_mine, ratio_other=v.ratio_other, summary=v.summary, opponent_claim=v.opponent_claim, basis=v.basis or {})


async def merge_facts(db: AsyncSession, case_id: str, updates: dict) -> None:
    if not updates:
        return
    analysis = await db.get(Analysis, case_id)
    if analysis is None:
        now = now_utc()
        analysis = Analysis(case_id=case_id, summary_text="", facts={}, questions=[], created_at=now, updated_at=now)
        db.add(analysis)
    analysis.facts = {**(analysis.facts or {}), **updates}
    analysis.updated_at = now_utc()
    await db.commit()


async def perform_verdict(db: AsyncSession, case_id: str) -> Verdict:
    case = await db.get(Case, case_id)
    analysis = await db.get(Analysis, case_id)
    previous = await case_service.active_verdict(db, case_id)
    result = await get_agent().judge(JudgeInput(
        messages=await recent_turns(db, case_id),
        facts=(analysis.facts if analysis else {}),
        previous_verdict=snapshot(previous),
    ))
    await db.execute(update(Verdict).where(Verdict.case_id == case_id).values(is_active=False))
    verdict = Verdict(
        id=new_id(), case_id=case_id, version=(previous.version + 1) if previous else 1,
        ratio_mine=result.ratio_mine, ratio_other=result.ratio_other, summary=result.summary,
        change_reason=result.change_reason if previous else None,
        opponent_claim=result.opponent_claim.model_dump() if result.opponent_claim else None,
        basis=result.basis.model_dump(), is_active=True, created_at=now_utc(),
    )
    db.add(verdict)
    if case.status in ("sent", "closed"):
        case_service.touch(case)  # 이미 보낸/종결된 사건은 재판정해도 상태를 되돌리지 않는다
    else:
        case_service.set_status(case, "judged")
    await db.commit()
    await case_service.add_message(db, case_id, "assistant", "verdict", verdict_payload(verdict))
    await case_service.publish_case_updated(db, case_id)
    return verdict
