from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class SendEmailCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["login", "register"]
    email: EmailStr
    invitation_code: str | None = Field(default=None, min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().casefold()

    @model_validator(mode="after")
    def validate_invitation_mode(self) -> SendEmailCodeRequest:
        if self.mode == "register" and not self.invitation_code:
            raise ValueError("registration requires an invitation")
        if self.mode == "login" and self.invitation_code is not None:
            raise ValueError("login must omit invitation_code")
        return self


class VerifyEmailCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=8, max_length=80)
    code: str = Field(pattern=r"^[0-9]{6,10}$")


class CurrentUserProjection(BaseModel):
    user_id: str
    display_name: str
    display_name_change_available_at: datetime | None
    masked_email: str | None


class CurrentUserQuotaProjection(BaseModel):
    policy: Literal["beta_lifetime"]
    limit: int
    reserved: int
    consumed: int
    remaining: int
    resets_at: datetime | None


class CurrentUserActiveTripProjection(BaseModel):
    trip_id: str
    job_id: str | None
    status: Literal["SUBMITTING", "PENDING", "RUNNING"]


class CurrentUserResponse(BaseModel):
    ok: Literal[True]
    user: CurrentUserProjection
    quota: CurrentUserQuotaProjection
    active_trip: CurrentUserActiveTripProjection | None
