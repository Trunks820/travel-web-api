from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.db.session import get_db_session
from src.history.cursor import InvalidHistoryCursor
from src.history.service import archive_expired, own_history

router = APIRouter(prefix="/api/me")
CURRENT_AUTH = Depends(get_current_auth)
DB_SESSION = Depends(get_db_session)


@router.get("/trips")
async def trips(
    request: Request,
    cursor: Annotated[str | None, Query(min_length=8, max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    status: Literal["SUCCESS", "FAILED", "TIMEOUT", "REJECTED"] | None = None,
    auth: AuthContext = CURRENT_AUTH,
    db: AsyncSession = DB_SESSION,
) -> dict[str, object]:
    await archive_expired(request.app.state.session_factory)
    try:
        return await own_history(
            db,
            request.app.state.settings,
            user_id=auth.user.id,
            cursor=cursor,
            limit=limit,
            status=status,
        )
    except InvalidHistoryCursor as exc:
        raise ApiError(422, "VALIDATION_ERROR", "历史游标无效。") from exc
