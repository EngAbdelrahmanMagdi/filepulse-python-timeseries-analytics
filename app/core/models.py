from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.paths import normalize_relative


class FileEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    event_id: UUID
    timestamp: datetime
    event_type: Literal["created", "modified", "deleted", "moved"]
    relative_path: str
    destination_path: str | None = None
    extension: str | None = Field(default=None, max_length=255)
    is_directory: bool
    size_bytes: int | None = Field(default=None, ge=0, le=2**63 - 1)
    size_delta: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        return value.astimezone(UTC)

    @field_validator("relative_path", "destination_path")
    @classmethod
    def relative_paths(cls, value: str | None) -> str | None:
        return normalize_relative(value) if value is not None else None

    @model_validator(mode="after")
    def move_destination(self) -> Self:
        if (self.event_type == "moved") != (self.destination_path is not None):
            raise ValueError("destination_path is required only for moves")
        if self.is_directory and (self.size_bytes is not None or self.size_delta is not None):
            raise ValueError("directory sizes must be null")
        return self
