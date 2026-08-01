from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DisplayNameUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str


class ProfileUserProjection(BaseModel):
    user_id: str
    display_name: str
    display_name_change_available_at: datetime | None
    masked_email: str | None


class ProfileUpdateResponse(BaseModel):
    ok: bool = True
    user: ProfileUserProjection
