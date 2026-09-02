from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import to_kst_iso
from app.db import get_db
from app.deps import current_user, owned_case
from app.errors import ApiError
from app.models import Case, User, Video
from app.security import decode_stream_token
from app.services import video as video_service
from app.services.presenters import size_label
from app.storage import get_storage

router = APIRouter(tags=["videos"])


@router.post("/cases/{case_id}/videos", status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile = File(...), case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    video, job, needs_description = await video_service.upload_video(db, case, file)
    return {
        "video": {
            "id": video.id, "filename": video.filename, "sizeBytes": video.size_bytes,
            "sizeLabel": size_label(video.size_bytes), "durationSec": video.duration_sec,
            "mimeType": video.mime_type, "recordedAt": to_kst_iso(video.recorded_at),
        },
        "analysis": {"started": job is not None, "jobId": job.id if job else None},
        "needsDescription": needs_description,
    }


async def _owned_video(video_id: str, user: User, db: AsyncSession) -> Video:
    video = await db.get(Video, video_id)
    if video is None:
        raise ApiError("NOT_FOUND")
    case = await db.get(Case, video.case_id)
    if case is None or case.user_id != user.id:
        raise ApiError("FORBIDDEN")
    return video


@router.get("/videos/{video_id}")
async def video_meta(video_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    return video_service.video_detail(await _owned_video(video_id, user, db))


@router.get("/videos/{video_id}/stream")
async def stream(video_id: str, request: Request, t: str = Query(...), db: AsyncSession = Depends(get_db)):
    if decode_stream_token(t) != video_id:
        raise ApiError("FORBIDDEN")
    video = await db.get(Video, video_id)
    if video is None:
        raise ApiError("NOT_FOUND")
    storage = get_storage()
    try:
        size = await storage.size(video.storage_key)
    except FileNotFoundError as e:
        raise ApiError("NOT_FOUND") from e
    rng = video_service.parse_range(request.headers.get("range"), size)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
    if rng is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(storage.read_range(video.storage_key, 0, size - 1), media_type=video.mime_type, headers=headers)
    start, end = rng
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(storage.read_range(video.storage_key, start, end), status_code=206, media_type=video.mime_type, headers=headers)
