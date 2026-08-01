from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.auth.router import _mask_email
from src.db.models import UserIdentity
from src.profile.display_names import DisplayNameError
from src.profile.schemas import DisplayNameUpdateRequest, ProfileUpdateResponse
from src.profile.service import (
    DisplayNameMutationError,
    change_available_at,
    rename_display_name,
)

router = APIRouter(prefix="/api/me")
CURRENT_AUTH = Depends(get_current_auth)

_ERRORS = {
    "DISPLAY_NAME_UNAVAILABLE": (409, "该显示名称暂不可用。"),
    "DISPLAY_NAME_INVALID": (422, "显示名称格式无效。"),
    "DISPLAY_NAME_RESERVED": (422, "该显示名称为系统保留名称。"),
    "DISPLAY_NAME_CHANGE_COOLDOWN": (429, "显示名称修改仍在冷却期。"),
    "AUTH_REQUIRED": (401, "请先登录。"),
}


@router.patch("/profile", response_model=ProfileUpdateResponse)
async def update_profile(
    body: DisplayNameUpdateRequest,
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
) -> dict:
    try:
        mutation = await rename_display_name(
            request.app.state.session_factory,
            request.app.state.settings,
            user_id=auth.user.id,
            requested_name=body.display_name,
        )
    except (DisplayNameError, DisplayNameMutationError) as error:
        status_code, message = _ERRORS[error.code]
        raise ApiError(status_code, error.code, message) from error

    async with request.app.state.session_factory() as session:
        email = await session.scalar(
            select(UserIdentity.verified_email).where(
                UserIdentity.user_id == auth.user.id,
                UserIdentity.provider == "email_otp",
            )
        )
    return {
        "ok": True,
        "user": {
            "user_id": mutation.public_user_id,
            "display_name": mutation.display_name,
            "display_name_change_available_at": change_available_at(mutation.changed_at),
            "masked_email": _mask_email(email) if email else None,
        },
    }
