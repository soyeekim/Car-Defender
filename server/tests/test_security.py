import time

import jwt
import pytest

from app.errors import ApiError
from app.security import (
    create_access_token,
    create_stream_token,
    decode_access_token,
    decode_stream_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    is_email,
    password_policy_ok,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("carguard12")
    assert h != "carguard12"
    assert verify_password("carguard12", h)
    assert not verify_password("wrong", h)


def test_password_policy():
    assert password_policy_ok("carguard12")
    assert not password_policy_ok("short1")
    assert not password_policy_ok("nodigitsatall")


def test_is_email():
    assert is_email("hyun@example.com")
    assert not is_email("hyun@")
    assert not is_email("")


def test_access_token_roundtrip(test_env):
    token = create_access_token("01JU1A2B3C4D5E6F7G8H9I0JKL")
    assert decode_access_token(token) == "01JU1A2B3C4D5E6F7G8H9I0JKL"


def test_expired_access_token(test_env):
    from app.config import get_settings

    payload = {"sub": "u", "type": "access", "exp": int(time.time()) - 1}
    token = jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(ApiError) as ei:
        decode_access_token(token)
    assert ei.value.code == "TOKEN_EXPIRED"


def test_garbage_token(test_env):
    with pytest.raises(ApiError) as ei:
        decode_access_token("garbage")
    assert ei.value.code == "UNAUTHORIZED"


def test_stream_token_is_not_accepted_as_access(test_env):
    token = create_stream_token("vid1")
    assert decode_stream_token(token) == "vid1"
    with pytest.raises(ApiError):
        decode_access_token(token)


def test_opaque_token_hash():
    raw = generate_opaque_token()
    assert len(raw) >= 32
    assert hash_token(raw) == hash_token(raw)
    assert hash_token(raw) != hash_token(generate_opaque_token())
