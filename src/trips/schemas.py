from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_HHMM = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class MustIncludeItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=120)
    place_id: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class AccommodationInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=160)
    place_id: int | None = Field(default=None, ge=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class RestWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start: str
    end: str
    label: str | None = Field(default=None, max_length=80)

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        cleaned = value.strip()
        if not _HHMM.fullmatch(cleaned):
            raise ValueError("time must use HH:MM")
        return cleaned

    @model_validator(mode="after")
    def validate_order(self) -> RestWindow:
        if self.start >= self.end:
            raise ValueError("rest window start must be before end")
        return self


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_city: str = Field(default="", max_length=120)
    to_city: str = Field(min_length=1, max_length=120)
    start_date: date | None = None
    end_date: date | None = None
    days: int = Field(ge=1, le=30)
    people_count: int = Field(default=1, ge=1, le=30)
    preferences: list[str] = Field(default_factory=list, max_length=20)
    avoid: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2_000)
    budget: int | None = Field(default=None, ge=0, le=10_000_000)
    must_include: list[MustIncludeItem] = Field(default_factory=list, max_length=5)
    commute_mode: Literal["driving", "transit", "cycling"] = "driving"
    daily_start: str | None = None
    daily_end: str | None = None
    rest_windows: list[RestWindow] = Field(default_factory=list, max_length=2)
    accommodation: AccommodationInput | None = None

    @field_validator("from_city", "to_city", "notes")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("preferences", "avoid")
    @classmethod
    def clean_string_list(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or len(item) > 80:
                if len(item) > 80:
                    raise ValueError("list item is too long")
                continue
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(item)
        return cleaned

    @field_validator("daily_start", "daily_end")
    @classmethod
    def validate_optional_time(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        cleaned = value.strip()
        if not _HHMM.fullmatch(cleaned):
            raise ValueError("time must use HH:MM")
        return cleaned

    @model_validator(mode="after")
    def validate_ranges(self) -> TripRequest:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.daily_start and self.daily_end and self.daily_start >= self.daily_end:
            raise ValueError("daily_start must be before daily_end")
        return self

    def normalized(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


class TripSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trip_request: TripRequest
    request_id: str

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not _REQUEST_ID.fullmatch(cleaned):
            raise ValueError("request_id is invalid")
        return cleaned


def normalized_request_hash(trip_request: dict[str, object]) -> str:
    canonical = json.dumps(
        trip_request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
