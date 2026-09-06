"""판정 카드의 '상대 보험사 주장 vs 카-디펜더 판정' 비교."""

from sqlalchemy import select

from app.clock import now_utc
from app.db import session_scope
from app.models import Analysis, Message
from app.services.presenters import NO_OPPONENT_CLAIM_NOTE, opponent_claim_note
from app.services.verdict import perform_verdict, sync_opponent_claim


def test_opponent_claim_note_wording():
    assert opponent_claim_note(0, None) == NO_OPPONENT_CLAIM_NOTE
    assert opponent_claim_note(0, {"mine": 30, "other": 70}) == "상대 보험사 주장보다 내 과실이 30%p 낮게 나왔어요"
    assert opponent_claim_note(40, {"mine": 30, "other": 70}) == "상대 보험사 주장보다 내 과실이 10%p 높게 나왔어요"
    assert opponent_claim_note(30, {"mine": 30, "other": 70}) == "상대 보험사 주장과 같은 비율이에요"


async def test_verdict_payload_carries_claim_and_note(client, auth_headers, case_id):
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        await perform_verdict(db, case_id)
    res = await client.get(f"/cases/{case_id}/messages", headers=auth_headers)
    card = next(m for m in res.json()["items"] if m["type"] == "verdict")
    # MockAgent 는 상대 주장을 돌려주지 않는다 → null + '아직 없어요' 안내
    assert card["payload"]["opponentClaim"] is None
    assert card["payload"]["opponentClaimNote"] == NO_OPPONENT_CLAIM_NOTE


async def test_claim_given_after_verdict_updates_verdict_and_card(client, auth_headers, case_id):
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
        verdict = await perform_verdict(db, case_id)
        mine = verdict.ratio_mine

        # 판정 뒤 대화에서 "상대 보험사가 나 30 : 상대 70 이래요" → 판정 행 + 카드 갱신, 재판정은 없음
        updated = await sync_opponent_claim(db, case_id, {"mine": 30, "other": 70})
        assert updated is not None and updated.id == verdict.id and updated.opponent_claim == {"mine": 30, "other": 70}

        cards = list((await db.execute(select(Message).where(Message.case_id == case_id, Message.type == "verdict"))).scalars().all())
        assert len(cards) == 1  # 새 카드가 생기지 않고 기존 카드가 바뀐다
        payload = cards[0].payload
        assert payload["opponentClaim"] == {"mine": 30, "other": 70}
        assert payload["opponentClaimNote"] == opponent_claim_note(mine, {"mine": 30, "other": 70})

        # 같은 값이면 아무것도 바꾸지 않고, 주장이 없거나 판정이 없으면 None
        assert (await sync_opponent_claim(db, case_id, {"mine": 30, "other": 70})).id == verdict.id
        assert await sync_opponent_claim(db, case_id, None) is None
        assert await sync_opponent_claim(db, "no-such-case", {"mine": 1, "other": 99}) is None

    res = await client.get(f"/cases/{case_id}/verdict", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["opponentClaim"] == {"mine": 30, "other": 70}  # 판정 조회도 같은 payload 를 돌려준다
    assert res.json()["opponentClaimNote"].endswith("낮게 나왔어요") or res.json()["opponentClaimNote"].endswith("높게 나왔어요") or "같은 비율" in res.json()["opponentClaimNote"]


# --------------------------------------------------------------------------- 사건 현황판 '확인된 사실'


async def test_case_detail_exposes_fact_chips(client, auth_headers, case_id):
    res = await client.get(f"/cases/{case_id}", headers=auth_headers)
    assert res.json()["facts"] is None  # 분석 전

    chips = {"confirmed": 2, "total": 3, "items": [
        {"label": "2차로 직진", "source": "video", "field": "ego_vehicle.movement"},
        {"label": "상대 우측 진입", "source": "video", "field": "other_vehicle.entry_direction"},
        {"label": "정지선 통과 확인 필요", "source": "pending", "field": ""},
    ]}
    async with session_scope() as db:
        db.add(Analysis(case_id=case_id, summary_text="s", facts={"fact_chips": chips}, questions=[], created_at=now_utc(), updated_at=now_utc()))
        await db.commit()
    res = await client.get(f"/cases/{case_id}", headers=auth_headers)
    facts = res.json()["facts"]
    assert facts["confirmed"] == 2 and facts["total"] == 3
    assert facts["label"] == "확인된 사실 2 / 3 · 남은 1개는 쟁점이에요"
    assert [item["label"] for item in facts["items"]] == ["2차로 직진", "상대 우측 진입", "정지선 통과 확인 필요"]
    assert facts["items"][2]["source"] == "pending" and facts["items"][2]["field"] is None
