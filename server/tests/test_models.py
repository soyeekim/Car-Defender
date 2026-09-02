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
