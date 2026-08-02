from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from src.admin.audit import append_admin_audit
from src.api.errors import ApiError
from src.auth.dependencies import AuthContext, get_current_auth
from src.config import Settings

CURRENT_AUTH = Depends(get_current_auth)

OWNER_CAPABILITIES = frozenset(
    {
        "admin.read",
        "audit.read",
        "artifact.read",
        "invitation.manage",
        "quota.manage_all",
        "report.read",
        "role.manage",
        "trip.read",
        "user.manage_all",
    }
)
ADMIN_CAPABILITIES = frozenset(
    {
        "admin.read",
        "audit.read",
        "artifact.read",
        "invitation.manage",
        "quota.manage_user",
        "report.read",
        "trip.read",
        "user.manage_user",
    }
)


@dataclass(frozen=True)
class AdminContext:
    auth: AuthContext
    product_identity: str
    capabilities: frozenset[str]

    @property
    def user(self):
        return self.auth.user

    @property
    def session(self):
        return self.auth.session

    @property
    def is_owner(self) -> bool:
        return self.product_identity == "OWNER"


def _store_admin_context(request: Request, context: AdminContext) -> None:
    state = getattr(request, "state", None)
    if state is not None:
        state.admin_context = context


def resolve_admin_context(
    auth: AuthContext,
    settings: Settings,
) -> AdminContext:
    if settings.admin_owner_user_id is not None and auth.user.id == settings.admin_owner_user_id:
        return AdminContext(
            auth=auth,
            product_identity="OWNER",
            capabilities=OWNER_CAPABILITIES,
        )
    if auth.user.role == "ADMIN":
        return AdminContext(
            auth=auth,
            product_identity="ADMIN",
            capabilities=ADMIN_CAPABILITIES,
        )
    return AdminContext(auth=auth, product_identity="USER", capabilities=frozenset())


async def get_current_admin(
    request: Request,
    auth: AuthContext = CURRENT_AUTH,
) -> AdminContext:
    context = resolve_admin_context(auth, request.app.state.settings)
    if context.is_owner:
        _store_admin_context(request, context)
        return context
    if context.product_identity == "USER":
        session_factory = getattr(request.app.state, "session_factory", None)
        if session_factory is not None:
            async with session_factory() as session, session.begin():
                await append_admin_audit(
                    session,
                    request.app.state.settings,
                    actor_user_id=auth.user.id,
                    actor_identity="USER",
                    action="ADMIN_ACCESS_DENIED",
                    target_type="ENDPOINT",
                    target_id=request.url.path,
                    result="FAILURE",
                    error_code="ADMIN_REQUIRED",
                    request_id=request.state.correlation_id,
                    source_ip=request.client.host if request.client else "unknown",
                )
        raise ApiError(403, "ADMIN_REQUIRED", "需要管理员权限。")
    _store_admin_context(request, context)
    return context


def require_capability(context: AdminContext, capability: str) -> None:
    if capability not in context.capabilities:
        code = "OWNER_REQUIRED" if capability == "role.manage" else "ADMIN_FORBIDDEN"
        raise ApiError(403, code, "当前管理员无权执行此操作。")
