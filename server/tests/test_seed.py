from sqlalchemy import select

from app.db import session_scope
from app.models import Case, User
from app.seed import seed


async def test_seed_is_idempotent(app):
    async with session_scope() as db:
        await seed(db)
        await seed(db)
        users = (await db.execute(select(User).where(User.email == "demo@fairway.click"))).scalars().all()
        assert len(users) == 1
        cases = (await db.execute(select(Case).where(Case.user_id == users[0].id))).scalars().all()
        assert len(cases) == 1 and cases[0].status == "closed" and cases[0].title == "주차장 후진 접촉 · 07-14"


async def test_seeded_case_visible_in_list(client, app):
    async with session_scope() as db:
        await seed(db)
    login = await client.post("/auth/login", json={"email": "demo@fairway.click", "password": "demo1234"})
    h = {"Authorization": f"Bearer {login.json()['accessToken']}"}
    items = (await client.get("/cases", headers=h)).json()["items"]
    assert items[0]["statusLabel"] == "종결"
    detail = (await client.get(f"/cases/{items[0]['id']}", headers=h)).json()
    assert detail["stages"]["rebuttal"] == {"state": "done"}
