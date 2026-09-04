from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.clock import now_utc
from app.db import session_scope
from app.ids import new_id
from app.models import Analysis, Case, Job, Message, SendLog, User, Verdict, Video


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


async def test_json_columns_track_in_place_mutation(app):
    async with session_scope() as db:
        u = await _user(db)
        c = Case(id=new_id(), user_id=u.id, title="새 사건", status="intake", created_at=now_utc(), updated_at=now_utc())
        db.add(c)
        db.add(Analysis(case_id=c.id, summary_text="s", facts={"a": 1}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        case_id = c.id

    async with session_scope() as db:
        a = await db.get(Analysis, case_id)
        a.facts["k"] = "v"  # 통째로 대입하지 않고 제자리 수정
        a.questions.append("q1")
        await db.commit()

    async with session_scope() as db:
        a = await db.get(Analysis, case_id)
        assert a.facts == {"a": 1, "k": "v"}
        assert a.questions == ["q1"]


async def test_case_video_relationship_is_single(app):
    async with session_scope() as db:
        u = await _user(db)
        c = Case(id=new_id(), user_id=u.id, title="새 사건", status="intake", created_at=now_utc(), updated_at=now_utc())
        db.add(c)
        db.add(Video(id=new_id(), case_id=c.id, filename="a.mp4", size_bytes=1, storage_key="k", created_at=now_utc()))
        await db.commit()
        case_id = c.id

    async with session_scope() as db:
        c = (await db.execute(select(Case).where(Case.id == case_id).options(selectinload(Case.video)))).scalar_one()
        assert c.video is not None and c.video.filename == "a.mp4"
