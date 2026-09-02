import logging
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ErrorSpec:
    http: int
    title: str
    message: str
    retryable: bool = False
    actions: list[dict] = field(default_factory=list)


def _a(label: str, type_: str) -> dict:
    return {"label": label, "type": type_}


ERROR_CATALOG: dict[str, ErrorSpec] = {
    "INTERNAL_ERROR": ErrorSpec(500, "문제가 생겼어요", "잠시 후 다시 시도해 주세요. 계속 안 되면 화면을 새로고침해 주세요.", True),
    "UNAUTHORIZED": ErrorSpec(401, "로그인이 필요해요", "다시 로그인해 주세요.", False, [_a("로그인하기", "go_login")]),
    "TOKEN_EXPIRED": ErrorSpec(401, "로그인이 만료됐어요", "다시 로그인해 주세요.", False, [_a("로그인하기", "go_login")]),
    "FORBIDDEN": ErrorSpec(403, "볼 수 없는 사건이에요", "내 사건 목록에서 다시 선택해 주세요.", False, [_a("내 사건 목록으로", "go_case_list")]),
    "NOT_FOUND": ErrorSpec(404, "찾을 수 없어요", "삭제됐거나 주소가 잘못됐어요.", False, [_a("내 사건 목록으로", "go_case_list")]),
    "VALIDATION_FAILED": ErrorSpec(422, "입력을 확인해 주세요", "입력한 내용을 다시 확인해 주세요."),
    "IDEMPOTENCY_KEY_REQUIRED": ErrorSpec(400, "요청을 처리하지 못했어요", "잠시 후 다시 시도해 주세요."),
    "AUTH_INVALID_CREDENTIALS": ErrorSpec(401, "로그인하지 못했어요", "이메일 또는 비밀번호가 맞지 않아요. 다시 입력해 주세요."),
    "AUTH_EMAIL_DUPLICATED": ErrorSpec(409, "가입하지 못했어요", "이미 가입된 이메일이에요. 로그인해 주세요.", False, [_a("이 이메일로 로그인하기", "go_login")]),
    "AUTH_PASSWORD_POLICY": ErrorSpec(422, "가입하지 못했어요", "비밀번호가 짧아요. 8자 이상, 숫자를 섞어 다시 입력해 주세요."),
    "AUTH_PASSWORD_MISMATCH": ErrorSpec(422, "가입하지 못했어요", "비밀번호 확인이 달라요. 한 번 더 확인해 주세요."),
    "AUTH_EMAIL_FORMAT": ErrorSpec(422, "가입하지 못했어요", "이메일 주소가 아니에요. name@company.co.kr 처럼 입력해 주세요."),
    "AGREEMENT_REQUIRED": ErrorSpec(422, "가입하지 못했어요", "필수 3가지에 모두 체크하면 가입할 수 있어요."),
    "RESET_TOKEN_INVALID": ErrorSpec(410, "링크가 만료됐어요", "비밀번호 찾기를 다시 시작해 주세요.", False, [_a("비밀번호 찾기 다시 하기", "go_password_reset")]),
    "REPORT_VERDICT_REQUIRED": ErrorSpec(409, "아직 만들 수 없어요", "과실비율 판정이 끝나면 경위서를 만들 수 있어요."),
    "REBUTTAL_LOCKED": ErrorSpec(409, "아직 보낼 수 없어요", "반박의견서에는 사건경위서가 첨부돼요. 먼저 경위서를 만들면 보낼 수 있어요.", False, [_a("사건경위서 먼저 만들기", "create_report")]),
    "RECIPIENT_INVALID": ErrorSpec(422, "보내지 못했어요", "이메일 주소가 아니에요. name@company.co.kr 처럼 고치면 보내기가 열려요."),
    "CLAIM_NUMBER_REQUIRED": ErrorSpec(422, "보내지 못했어요", "접수번호를 넣어야 보험사가 사건을 찾을 수 있어요. 보험사 접수 문자나 메일에 있어요."),
    "MAIL_SEND_FAILED": ErrorSpec(502, "보내지 못했어요", "메일 서버가 응답하지 않았어요. 작성한 내용과 첨부는 그대로 있으니, 잠시 후 다시 시도해 주세요.", True, [_a("다시 시도", "retry_send")]),
    "REBUTTAL_ALREADY_SENT": ErrorSpec(409, "이미 보낸 문서예요", "보낸 문서는 수정할 수 없어요. 다시 보내려면 새 문서로 만들어 주세요."),
    "JOB_ALREADY_RUNNING": ErrorSpec(409, "이미 진행 중이에요", "지금 하던 작업이 끝나면 다시 할 수 있어요."),
    "RATE_LIMITED": ErrorSpec(429, "잠시만요", "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.", True),
}


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        *,
        fields: dict | None = None,
        actions: list[dict] | None = None,
        message: str | None = None,
    ):
        if code not in ERROR_CATALOG:
            raise ValueError(f"unknown error code {code}")
        self.code = code
        self.fields = fields
        self.actions = actions
        self.message = message
        super().__init__(code)


def error_body(code: str, *, fields=None, actions=None, message=None) -> dict:
    spec = ERROR_CATALOG[code]
    return {
        "error": {
            "code": code,
            "title": spec.title,
            "message": message or spec.message,
            "retryable": spec.retryable,
            "actions": spec.actions if actions is None else actions,
            "fields": fields,
        }
    }


def error_response(code: str, **kw) -> JSONResponse:
    return JSONResponse(status_code=ERROR_CATALOG[code].http, content=error_body(code, **kw))


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.code, fields=exc.fields, actions=exc.actions, message=exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields: dict[str, str] = {}
        for e in exc.errors():
            loc = [str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")]
            name = ".".join(loc) or "body"
            fields.setdefault(name, "입력을 확인해 주세요.")
        return error_response("VALIDATION_FAILED", fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        mapping = {404: "NOT_FOUND", 405: "NOT_FOUND", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}
        code = mapping.get(exc.status_code)
        if code is None:
            log.warning("unmapped HTTPException %s: %s", exc.status_code, exc.detail)
            code = "INTERNAL_ERROR"
        return error_response(code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return error_response("INTERNAL_ERROR")
