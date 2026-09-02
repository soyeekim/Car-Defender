from sqlalchemy import select

from app.db import session_scope
from app.ids import new_id
from app.models import User
from app.clock import now_utc


async def test_can_insert_and_read_user(app):
    async with session_scope() as db:
        user = User(id=new_id(), email="a@b.co", password_hash="x", created_at=now_utc())
        db.add(user)
        await db.commit()
    async with session_scope() as db:
        got = (await db.execute(select(User).where(User.email == "a@b.co"))).scalar_one()
        assert got.id == user.id
        assert got.is_demo is False
        assert got.onboarded_at is None
