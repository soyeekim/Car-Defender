import hashlib
import re
import secrets
from datetime import timedelta

import bcrypt
import jwt

from app.clock import now_utc
from app.config import get_settings
from app.errors import ApiError

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 320  # users.email · rebuttals.recipient · send_logs.recipient 칼럼 길이와 같다


def is_email(value: str | None) -> bool:
    # 길이도 함께 본다: Postgres는 varchar 상한을 넘는 값을 자르지 않고 write 자체를 실패시킨다.
    return bool(value) and len(value) <= MAX_EMAIL_LENGTH and EMAIL_RE.match(value) is not None


def password_policy_ok(raw: str | None) -> bool:
    return bool(raw) and len(raw) >= 8 and any(c.isdigit() for c in raw)


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def _encode(payload: dict, minutes: int) -> str:
    settings = get_settings()
    now = now_utc()
    payload = {**payload, "iat": int(now.timestamp()), "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode(token: str, expected_type: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as e:
        raise ApiError("TOKEN_EXPIRED") from e
    except jwt.PyJWTError as e:
        raise ApiError("UNAUTHORIZED") from e
    if payload.get("type") != expected_type:
        raise ApiError("UNAUTHORIZED")
    return payload


def create_access_token(user_id: str) -> str:
    return _encode({"sub": user_id, "type": "access"}, get_settings().access_token_minutes)


def decode_access_token(token: str) -> str:
    return _decode(token, "access")["sub"]


def create_stream_token(video_id: str) -> str:
    return _encode({"vid": video_id, "type": "stream"}, get_settings().stream_token_minutes)


def decode_stream_token(token: str) -> str:
    return _decode(token, "stream")["vid"]


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
