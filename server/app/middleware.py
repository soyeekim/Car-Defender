import logging

from app.errors import error_response

log = logging.getLogger(__name__)


class CatchAllErrorMiddleware:
    """Catches exceptions that escape route handling so the response still
    passes through CORSMiddleware (which sits outside this middleware).

    Without this, an unhandled exception is caught by the outer
    ServerErrorMiddleware/`Exception` handler registered via FastAPI, which
    runs *outside* CORSMiddleware and therefore never gets CORS headers.

    Implemented as a pure ASGI middleware (not `BaseHTTPMiddleware`) because
    `BaseHTTPMiddleware` buffers streaming responses and only catches
    exceptions raised before the response starts; SSE responses need to
    stream through this middleware untouched, and an exception raised mid-
    stream after `http.response.start` has already been sent can no longer
    be turned into a JSON error response.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if started:
                raise  # 이미 응답이 시작됐으면 손댈 수 없음
            log.exception("unhandled error")
            response = error_response("INTERNAL_ERROR")
            await response(scope, receive, send)
