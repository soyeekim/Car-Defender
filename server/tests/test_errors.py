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
