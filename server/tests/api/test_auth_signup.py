VALID = {
    "email": "hyun@example.com",
    "password": "carguard12",
    "passwordConfirm": "carguard12",
    "agreements": {"termsOfService": True, "privacy": True, "videoConsent": True},
}


async def test_signup_success_sets_cookie_and_returns_token(client):
    res = await client.post("/auth/signup", json=VALID)
    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "hyun@example.com"
    assert body["user"]["isDemo"] is False
    assert body["user"]["onboardedAt"] is None
    assert len(body["user"]["id"]) == 26
    assert body["expiresIn"] == 1800
    assert body["accessToken"]
    cookie = res.headers["set-cookie"]
    assert "refresh_token=" in cookie and "HttpOnly" in cookie and "Path=/api/v1/auth" in cookie
    assert "samesite=lax" in cookie.lower()


async def test_signup_duplicate_and_bad_password_collects_all_fields(client):
    await client.post("/auth/signup", json=VALID)
    res = await client.post("/auth/signup", json={**VALID, "password": "short", "passwordConfirm": "short"})
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "AUTH_EMAIL_DUPLICATED"
    assert err["actions"] == [{"label": "이 이메일로 로그인하기", "type": "go_login"}]
    assert set(err["fields"]) == {"email", "password"}
    assert err["fields"]["password"].startswith("비밀번호가 짧아요")


async def test_signup_email_format_first_priority(client):
    res = await client.post("/auth/signup", json={**VALID, "email": "nope", "password": "short", "passwordConfirm": "x"})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "AUTH_EMAIL_FORMAT"
    assert set(err["fields"]) == {"email", "password", "passwordConfirm"}


async def test_signup_password_mismatch(client):
    res = await client.post("/auth/signup", json={**VALID, "passwordConfirm": "carguard13"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AUTH_PASSWORD_MISMATCH"
    assert res.json()["error"]["fields"] == {"passwordConfirm": "비밀번호 확인이 달라요. 한 번 더 확인해 주세요."}


async def test_signup_agreement_required(client):
    body = {**VALID, "agreements": {"termsOfService": True, "privacy": False, "videoConsent": True}}
    res = await client.post("/auth/signup", json=body)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AGREEMENT_REQUIRED"
    assert "agreements" in res.json()["error"]["fields"]


async def test_signup_email_is_case_insensitive(client):
    await client.post("/auth/signup", json=VALID)
    res = await client.post("/auth/signup", json={**VALID, "email": "HYUN@example.com"})
    assert res.json()["error"]["code"] == "AUTH_EMAIL_DUPLICATED"


async def test_email_available(client):
    res = await client.get("/auth/email-available", params={"email": "new@example.com"})
    assert res.status_code == 200
    assert res.json() == {"available": True, "reason": None}
    await client.post("/auth/signup", json=VALID)
    res = await client.get("/auth/email-available", params={"email": "hyun@example.com"})
    assert res.json() == {"available": False, "reason": "이미 가입된 이메일이에요. 로그인해 주세요."}


async def test_email_available_bad_format(client):
    res = await client.get("/auth/email-available", params={"email": "nope"})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "AUTH_EMAIL_FORMAT"
