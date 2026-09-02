import time

import jwt

VALID = {
    "email": "hyun@example.com",
    "password": "carguard12",
    "passwordConfirm": "carguard12",
    "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
}


async def signup(client):
    return await client.post("/auth/signup", json=VALID)


async def test_login_success(client):
    await signup(client)
    res = await client.post("/auth/login", json={"email": "Hyun@Example.com", "password": "carguard12"})
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "hyun@example.com"
    assert "refresh_token=" in res.headers["set-cookie"]


async def test_login_wrong_password_and_unknown_email_look_same(client):
    await signup(client)
    a = await client.post("/auth/login", json={"email": "hyun@example.com", "password": "nope1234"})
    b = await client.post("/auth/login", json={"email": "ghost@example.com", "password": "nope1234"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert a.json()["error"]["fields"] == {"password": "이메일 또는 비밀번호가 맞지 않아요. 다시 입력해 주세요."}


async def test_login_unknown_email_still_verifies_a_password_hash(client, monkeypatch):
    import app.services.auth as auth_service

    await signup(client)
    calls = []
    original = auth_service.verify_password

    def counting_verify(raw, hashed):
        calls.append((raw, hashed))
        return original(raw, hashed)

    monkeypatch.setattr(auth_service, "verify_password", counting_verify)
    res = await client.post("/auth/login", json={"email": "ghost@example.com", "password": "nope1234"})
    assert res.status_code == 401
    assert len(calls) == 1


async def test_me_requires_bearer(client):
    res = await client.get("/auth/me")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_returns_agreements(client):
    token = (await signup(client)).json()["accessToken"]
    res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "hyun@example.com"
    assert body["agreements"]["termsOfService"]["agreed"] is True
    assert body["agreements"]["videoConsent"]["agreedAt"].endswith("+09:00")


async def test_expired_token_gives_token_expired(client, test_env):
    from app.config import get_settings

    await signup(client)
    token = jwt.encode({"sub": "x", "type": "access", "exp": int(time.time()) - 5}, get_settings().jwt_secret, algorithm="HS256")
    res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_refresh_returns_new_access_token(client):
    await signup(client)  # httpx 클라이언트가 쿠키를 보관한다
    res = await client.post("/auth/refresh")
    assert res.status_code == 200
    assert res.json()["expiresIn"] == 1800
    assert res.json()["accessToken"]


async def test_refresh_without_cookie(client):
    res = await client.post("/auth/refresh")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


async def test_logout_revokes_refresh(client):
    signup_res = await signup(client)
    token = signup_res.json()["accessToken"]
    old_refresh_cookie = client.cookies.get("refresh_token")
    res = await client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 204
    set_cookie = res.headers["set-cookie"]
    assert "refresh_token=" in set_cookie
    assert "max-age=0" in set_cookie.lower()

    # httpx가 방금 지운 쿠키를 더 이상 보관하지 않으므로 쿠키 없이 요청하면 UNAUTHORIZED
    res = await client.post("/auth/refresh")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"

    # 지워지기 전 쿠키 값을 재사용해도 이미 revoke된 토큰이라 TOKEN_EXPIRED
    res = await client.post("/auth/refresh", cookies={"refresh_token": old_refresh_cookie})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_login_rate_limited(client):
    await signup(client)
    for _ in range(10):
        await client.post("/auth/login", json={"email": "hyun@example.com", "password": "bad00000"})
    res = await client.post("/auth/login", json={"email": "hyun@example.com", "password": "bad00000"})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"


async def test_login_rate_limited_per_ip_across_different_emails(client):
    # 이메일별 버킷(분당 10회)만 있으면 공격자가 이메일을 바꿔가며 무한정
    # 시도할 수 있으므로, 같은 IP를 기준으로 한 더 넓은 버킷도 필요하다.
    for i in range(30):
        await client.post(
            "/auth/login", json={"email": f"guess{i}@example.com", "password": "bad00000"}
        )
    res = await client.post(
        "/auth/login", json={"email": "guess-final@example.com", "password": "bad00000"}
    )
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"
