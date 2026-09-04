import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as database
from app.config import get_settings
from app.content.texts import GUIDE_CARD
from app.ids import new_id
from app.models import Case, Message, Rebuttal, Report, User, Verdict
from app.security import hash_password

DEMO_EMAIL = "demo@fairway.click"
DEMO_PASSWORD = "demo1234"


async def seed(db: AsyncSession) -> None:
    user = (await db.execute(select(User).where(User.email == DEMO_EMAIL))).scalar_one_or_none()
    t0 = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
    if user is None:
        user = User(id=new_id(), email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD), agreed_terms_at=t0, agreed_privacy_at=t0, agreed_video_at=t0, onboarded_at=t0, is_demo=True, created_at=t0)
        db.add(user)
        await db.flush()
    existing = (await db.execute(select(Case).where(Case.user_id == user.id))).scalars().first()
    if existing is not None:
        return
    t1 = datetime(2026, 7, 20, 2, 2, tzinfo=UTC)
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
