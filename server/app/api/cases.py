from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import current_user, owned_case
from app.models import Case, User
from app.schemas.cases import RenameCaseRequest
from app.services import cases as case_service
from app.services.presenters import case_detail, case_list_item

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    case = await case_service.create_case(db, user)
    return case_detail(await case_service.load_bundle(db, case.id))


@router.get("")
async def list_cases(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    items = [case_list_item(c) for c in await case_service.list_cases(db, user)]
    return {"items": items, "hasMore": False, "nextCursor": None}


@router.get("/{case_id}")
async def get_case(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    return case_detail(await case_service.load_bundle(db, case.id))


@router.patch("/{case_id}")
async def rename_case(body: RenameCaseRequest, case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)) -> dict:
    await case_service.rename_case(db, case, body.title)
    return await case_service.publish_case_updated(db, case.id)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case: Case = Depends(owned_case), db: AsyncSession = Depends(get_db)):
    await case_service.delete_case(db, case)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
