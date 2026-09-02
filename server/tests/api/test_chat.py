from app.services.actions import register_action


async def send(client, h, case_id, text):
    return await client.post(f"/cases/{case_id}/messages", json={"text": text}, headers=h)


async def test_send_validation(client, auth_headers, case_id):
    assert (await send(client, auth_headers, case_id, "")).status_code == 422
    assert (await send(client, auth_headers, case_id, "x" * 2001)).status_code == 422


async def test_message_before_video_gets_upload_cta(client, auth_headers, case_id, sse, settle):
    tap = await sse(case_id)
    res = await send(client, auth_headers, case_id, "어제 교차로에서 오토바이가 박았어요")
    assert res.status_code == 202
    body = res.json()
    assert body["assistantPending"] is True and body["message"]["role"] == "user" and body["message"]["payload"] == {"text": "어제 교차로에서 오토바이가 박았어요"}
    await settle()
    frames = await tap.take(2)
    assert '"role": "user"' in frames[0]
    assert '"cta": {"type": "upload_video", "label": "영상 올리기"}' in frames[1]


async def test_video_then_description_starts_analysis(client, auth_headers, case_id, upload, settle):
    await upload()
    await settle()
    await send(client, auth_headers, case_id, "교차로에서 오토바이가 박았어요")
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "needs_review" and detail["title"] == "교차로 직진 충돌 · 08-22"


async def test_full_conversation_to_verdict_and_rejudge(client, auth_headers, case_id, upload, settle):
    await send(client, auth_headers, case_id, "교차로에서 오토바이가 박았어요")
    await settle()
    await upload()
    await settle()

    r = await send(client, auth_headers, case_id, "우측 앞펜더요.")
    assert r.status_code == 202
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert "2/2" in msgs[-1]["payload"]["text"]

    await send(client, auth_headers, case_id, "초록불이었어요.")
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged" and detail["verdict"]["version"] == 1
    verdict = (await client.get(f"/cases/{case_id}/verdict", headers=auth_headers)).json()
    assert verdict["ratio"] == {"mine": 0, "other": 100} and verdict["basis"]["precedents"][0]["id"] == "2019-018856"

    await send(client, auth_headers, case_id, "다시 보니까 상대 신호가 황색이었던 것 같아요.")
    await settle()
    detail = (await client.get(f"/cases/{case_id}", headers=auth_headers)).json()
    assert detail["status"] == "judged" and detail["verdict"]["version"] == 2 and detail["verdict"]["ratio"] == {"mine": 20, "other": 80}
    msgs = (await client.get(f"/cases/{case_id}/messages", params={"limit": 50}, headers=auth_headers)).json()["items"]
    verdict_cards = [m for m in msgs if m["type"] == "verdict"]
    assert [v["payload"]["version"] for v in verdict_cards] == [1, 2]
    assert verdict_cards[1]["payload"]["changeReason"]


async def test_send_blocked_while_analysis_running(client, auth_headers, case_id, upload, settle, monkeypatch):
    import asyncio

    from app.agent.loader import get_agent

    gate = asyncio.Event()
    original = get_agent()._impl.analyze

    async def slow(inp):
        await gate.wait()
        return await original(inp)

    monkeypatch.setattr(get_agent()._impl, "analyze", slow)
    await send(client, auth_headers, case_id, "설명")
    await settle()
    await upload()
    res = await send(client, auth_headers, case_id, "또 보냄")
    assert res.status_code == 409 and res.json()["error"]["code"] == "JOB_ALREADY_RUNNING"
    gate.set()
    await settle()


async def test_unregistered_action_is_noop_and_api_error_becomes_text_card(client, auth_headers, case_id, settle, monkeypatch):
    # 영상을 올리지 않는다: 영상이 있고 분석이 없으면 chat 대신 분석 Job이 돌기 때문 (§1.1 ②′)
    from app.agent.base import ChatResult
    from app.agent.loader import get_agent
    from app.errors import ApiError

    async def chat(inp):
        return ChatResult(reply="경위서 만들게요", next_action="create_report")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)

    async def failing(db, case):
        raise ApiError("REPORT_VERDICT_REQUIRED")

    register_action("create_report", failing)
    await send(client, auth_headers, case_id, "경위서")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["text"] == "과실비율 판정이 끝나면 경위서를 만들 수 있어요."


async def test_rebuttal_locked_error_becomes_locked_card(client, auth_headers, case_id, settle, monkeypatch):
    from app.agent.base import ChatResult
    from app.agent.loader import get_agent
    from app.errors import ApiError

    async def chat(inp):
        return ChatResult(reply="반박 준비", next_action="create_rebuttal")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)

    async def locked(db, case):
        raise ApiError("REBUTTAL_LOCKED", fields={"missing": "verdict,report"})

    register_action("create_rebuttal", locked)
    await send(client, auth_headers, case_id, "반박")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    card = msgs[-1]
    assert card["type"] == "rebuttal_locked" and card["payload"]["missing"] == ["verdict", "report"]
    assert card["payload"]["buttonHint"] == "경위서를 만들면 열려요"


async def test_agent_crash_sends_internal_error_text(client, auth_headers, case_id, settle, monkeypatch):
    from app.agent.loader import get_agent

    async def chat(inp):
        raise RuntimeError("llm down")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)
    await send(client, auth_headers, case_id, "hi")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["text"].startswith("잠시 후 다시 시도해 주세요")


async def test_flush_failure_rolls_back_and_still_sends_error_card(client, auth_headers, case_id, upload, settle, monkeypatch):
    from sqlalchemy import insert

    import app.services.chat as chat_module
    from app.agent.base import ChatResult
    from app.agent.loader import get_agent
    from app.clock import now_utc
    from app.errors import ERROR_CATALOG
    from app.models import Analysis

    # 영상만 올리면 analysis Job이 아직 안 돈다: Analysis가 생기려면 설명 메시지가 먼저 필요하다.
    await send(client, auth_headers, case_id, "교차로에서 오토바이가 박았어요")
    await settle()
    await upload()
    await settle()  # 분석 Job이 끝나 Analysis 행이 이미 존재한다

    async def chat(inp):
        return ChatResult(reply="네, 확인했어요.")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)

    async def broken_merge_facts(db, cid, updates):
        # case_id가 이미 있는 Analysis에 같은 PK로 하나 더 INSERT해 flush에서 터뜨린다.
        # ORM add()는 세션 identity map과 충돌해 SAWarning을 내므로 Core insert로 우회한다.
        now = now_utc()
        await db.execute(insert(Analysis).values(case_id=cid, summary_text="dup", facts={}, questions=[], created_at=now, updated_at=now))
        await db.flush()

    monkeypatch.setattr(chat_module, "merge_facts", broken_merge_facts)

    await send(client, auth_headers, case_id, "우측 앞펜더요.")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["text"] == ERROR_CATALOG["INTERNAL_ERROR"].message


async def test_chat_timeout_sends_internal_error_card(client, auth_headers, case_id, upload, settle, monkeypatch):
    import asyncio
    import time

    from app.agent.loader import get_agent
    from app.config import get_settings
    from app.errors import ERROR_CATALOG

    # 영상만 올리면 analysis Job이 아직 안 돈다: Analysis가 생기려면 설명 메시지가 먼저 필요하다.
    await send(client, auth_headers, case_id, "교차로에서 오토바이가 박았어요")
    await settle()
    await upload()
    await settle()

    monkeypatch.setattr(get_settings(), "job_timeout_seconds", 0.05)

    cancelled = []

    async def slow_chat(inp):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(get_agent()._impl, "chat", slow_chat)

    started = time.monotonic()
    await send(client, auth_headers, case_id, "우측 앞펜더요.")
    await settle()
    elapsed = time.monotonic() - started

    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["text"] == ERROR_CATALOG["INTERNAL_ERROR"].message
    assert cancelled == [True]
    assert elapsed < 2


async def test_chat_wait_all_with_timeout_cancels_stuck_task():
    import asyncio

    from app.services import chat as chat_module

    async def stuck():
        await asyncio.sleep(10)

    task = asyncio.create_task(stuck())
    chat_module._tasks.add(task)
    task.add_done_callback(chat_module._tasks.discard)

    await chat_module.wait_all(timeout=0.05)

    assert task.cancelled()
    assert task not in chat_module._tasks


async def test_rebuttal_locked_missing_entries_are_stripped(client, auth_headers, case_id, settle, monkeypatch):
    from app.agent.base import ChatResult
    from app.agent.loader import get_agent
    from app.errors import ApiError

    async def chat(inp):
        return ChatResult(reply="반박 준비", next_action="create_rebuttal")

    monkeypatch.setattr(get_agent()._impl, "chat", chat)

    async def locked(db, case):
        raise ApiError("REBUTTAL_LOCKED", fields={"missing": " verdict , report "})

    register_action("create_rebuttal", locked)
    await send(client, auth_headers, case_id, "반박")
    await settle()
    msgs = (await client.get(f"/cases/{case_id}/messages", headers=auth_headers)).json()["items"]
    assert msgs[-1]["payload"]["missing"] == ["verdict", "report"]
