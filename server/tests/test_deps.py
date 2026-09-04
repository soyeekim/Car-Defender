from starlette.requests import Request

from app.deps import client_ip


def _request(headers: dict[str, str], client_host: str | None = "203.0.113.9") -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "headers": raw_headers,
        "client": (client_host, 12345) if client_host else None,
    }
    return Request(scope)


def test_client_ip_uses_last_forwarded_for_entry():
    # 첫 번째 항목은 클라이언트가 스스로 주장할 수 있으므로 신뢰하지 않는다.
    # 우리 nginx가 마지막에 덧붙인 값만 신뢰한다.
    req = _request({"x-forwarded-for": "1.2.3.4, 203.0.113.9"})
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_single_forwarded_for_entry():
    req = _request({"x-forwarded-for": "203.0.113.9"})
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_falls_back_to_client_host_without_header():
    req = _request({})
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_unknown_without_client_or_header():
    req = _request({}, client_host=None)
    assert client_ip(req) == "unknown"
