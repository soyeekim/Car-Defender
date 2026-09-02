import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.errors import error_response

log = logging.getLogger(__name__)


class CatchAllErrorMiddleware(BaseHTTPMiddleware):
    """Catches exceptions that escape route handling so the response still
    passes through CORSMiddleware (which sits outside this middleware).

    Without this, an unhandled exception is caught by the outer
    ServerErrorMiddleware/`Exception` handler registered via FastAPI, which
    runs *outside* CORSMiddleware and therefore never gets CORS headers.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            log.exception("unhandled error", exc_info=exc)
            return error_response("INTERNAL_ERROR")
