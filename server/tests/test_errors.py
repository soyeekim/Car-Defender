from fastapi import APIRouter
from pydantic import BaseModel

from app.errors import ERROR_CATALOG, ApiError


def test_catalog_has_22_codes():
    assert len(ERROR_CATALOG) == 22


async def test_api_error_response_has_six_keys(app, client):
    router = APIRouter()

    @router.get("/boom")
    async def boom():
        raise ApiError("REBUTTAL_LOCKED", fields={"missing": "report"})

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/boom")
    assert res.status_code == 409
    err = res.json()["error"]
    assert set(err) == {"code", "title", "message", "retryable", "actions", "fields"}
    assert err["code"] == "REBUTTAL_LOCKED"
    assert err["title"] == "아직 보낼 수 없어요"
    assert err["retryable"] is False
    assert err["actions"] == [{"label": "사건경위서 먼저 만들기", "type": "create_report"}]
    assert err["fields"] == {"missing": "report"}


async def test_validation_error_maps_to_validation_failed(app, client):
    router = APIRouter()

    class Body(BaseModel):
        title: str

    @router.post("/echo")
    async def echo(body: Body):
        return body

    app.include_router(router, prefix="/api/v1")
    res = await client.post("/echo", json={})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["code"] == "VALIDATION_FAILED"
    assert "title" in err["fields"]


async def test_unknown_exception_maps_to_internal_error(app, client):
    router = APIRouter()

    @router.get("/crash")
    async def crash():
        raise RuntimeError("x")

    app.include_router(router, prefix="/api/v1")
    client._transport.raise_app_exceptions = False
    res = await client.get("/crash")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"
    assert res.json()["error"]["retryable"] is True


async def test_404_route_maps_to_not_found(client):
    res = await client.get("/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


async def test_http_400_maps_to_validation_failed(app, client):
    from starlette.exceptions import HTTPException as StarletteHTTPException

    router = APIRouter()

    @router.get("/http400")
    async def http400():
        raise StarletteHTTPException(status_code=400)

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/http400")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_http_413_maps_to_validation_failed(app, client):
    from starlette.exceptions import HTTPException as StarletteHTTPException

    router = APIRouter()

    @router.get("/http413")
    async def http413():
        raise StarletteHTTPException(status_code=413)

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/http413")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_FAILED"


async def test_http_429_maps_to_rate_limited(app, client):
    from starlette.exceptions import HTTPException as StarletteHTTPException

    router = APIRouter()

    @router.get("/http429")
    async def http429():
        raise StarletteHTTPException(status_code=429)

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/http429")
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"


async def test_http_409_maps_to_job_already_running(app, client):
    from starlette.exceptions import HTTPException as StarletteHTTPException

    router = APIRouter()

    @router.get("/http409")
    async def http409():
        raise StarletteHTTPException(status_code=409)

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/http409")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "JOB_ALREADY_RUNNING"


async def test_500_response_carries_cors_headers(app, client):
    router = APIRouter()

    @router.get("/crash-with-origin")
    async def crash_with_origin():
        raise RuntimeError("boom")

    app.include_router(router, prefix="/api/v1")
    client._transport.raise_app_exceptions = False
    res = await client.get("/crash-with-origin", headers={"Origin": "http://test"})
    assert res.status_code == 500
    assert res.headers.get("access-control-allow-origin") == "http://test"
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_streaming_response_raising_after_start_does_not_hang(app, client):
    from starlette.responses import StreamingResponse

    router = APIRouter()

    @router.get("/stream-crash")
    async def stream_crash():
        async def gen():
            yield b"chunk-1"
            raise RuntimeError("boom mid-stream")

        return StreamingResponse(gen(), media_type="text/plain")

    app.include_router(router, prefix="/api/v1")
    client._transport.raise_app_exceptions = False
    res = await client.get("/stream-crash")
    assert res.status_code == 200
    assert res.content == b"chunk-1"


async def test_http_unknown_5xx_maps_to_internal_error(app, client):
    from starlette.exceptions import HTTPException as StarletteHTTPException

    router = APIRouter()

    @router.get("/http503")
    async def http503():
        raise StarletteHTTPException(status_code=503)

    app.include_router(router, prefix="/api/v1")
    res = await client.get("/http503")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "INTERNAL_ERROR"
