from app.mail import get_mailer
from tests.conftest import SIGNUP_BODY


async def test_reset_request_sends_mail_with_link(client):
    await client.post("/auth/signup", json=SIGNUP_BODY)
    res = await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    assert res.status_code == 202
    assert res.json() == {"message": "비밀번호 재설정 링크를 보냈어요. 메일함을 확인해 주세요."}
    sent = get_mailer().sent
    assert len(sent) == 1 and "reset?token=" in sent[0].body_text


async def test_reset_request_unknown_email_still_202_and_no_mail(client):
    res = await client.post("/auth/password-reset", json={"email": "ghost@example.com"})
    assert res.status_code == 202
    assert get_mailer().sent == []


async def test_reset_request_bad_format(client):
    res = await client.post("/auth/password-reset", json={"email": "nope"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AUTH_EMAIL_FORMAT"


async def test_reset_request_rate_limited_3_per_minute(client):
    for _ in range(3):
        await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    res = await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    assert res.status_code == 429


async def test_reset_confirm_changes_password_once(client):
    await client.post("/auth/signup", json=SIGNUP_BODY)
    await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    body = get_mailer().sent[0].body_text
    token = body.split("reset?token=")[1].split()[0]

    res = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "newpass2026", "passwordConfirm": "newpass2026"})
    assert res.status_code == 200
    assert res.json() == {"message": "비밀번호를 바꿨어요. 새 비밀번호로 로그인해 주세요."}

    login = await client.post("/auth/login", json={"email": "hyun@example.com", "password": "newpass2026"})
    assert login.status_code == 200

    again = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "another2026", "passwordConfirm": "another2026"})
    assert again.status_code == 410
    assert again.json()["error"]["code"] == "RESET_TOKEN_INVALID"


async def test_reset_confirm_policy_and_mismatch(client):
    await client.post("/auth/signup", json=SIGNUP_BODY)
    await client.post("/auth/password-reset", json={"email": "hyun@example.com"})
    token = get_mailer().sent[0].body_text.split("reset?token=")[1].split()[0]
    res = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "short", "passwordConfirm": "short"})
    assert res.json()["error"]["code"] == "AUTH_PASSWORD_POLICY"
    res = await client.post("/auth/password-reset/confirm", json={"token": token, "password": "newpass2026", "passwordConfirm": "x"})
    assert res.json()["error"]["code"] == "AUTH_PASSWORD_MISMATCH"
