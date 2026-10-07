from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas import ActivityTotals, Window


class ModelStatus(BaseModel):
    state: Literal["not_trained", "ready", "insufficient_data", "failed"] = "not_trained"
    training: bool = False
    trained_at: datetime | None = None
    training_start: datetime | None = None
    training_cutoff: datetime | None = None
    sample_count: int = 0
    features: list[str] = Field(default_factory=list)
    last_attempt: Literal["never", "ready", "insufficient_data", "failed", "busy"] = "never"
    message: str | None = None


class AnomalyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: Window = "24h"
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10000)


class DetailQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def complete_utc_minute(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Use a timezone-aware minute boundary")
        value = value.astimezone(UTC)
        if value.second or value.microsecond:
            raise ValueError("Use a timezone-aware minute boundary")
        return value


class Comparison(BaseModel):
    feature: str
    observed: int
    median: float
    p95: float


class AnomalyBucket(ActivityTotals):
    timestamp: datetime
    unique_extensions: int = 0
    state: Literal["scored", "inactive", "outside_scoring_period"]
    anomaly_score: float | None = None
    flagged: bool = False
    observed_context: list[Comparison] = Field(default_factory=list)


class AnomaliesResponse(BaseModel):
    model: ModelStatus
    start: datetime
    end: datetime
    bucket: Literal["1m"] = "1m"
    scored_count: int = 0
    flagged_count: int = 0
    latest_flagged: datetime | None = None
    limit: int
    offset: int
    has_more: bool = False
    timeline: list[AnomalyBucket] = Field(default_factory=list)
    anomalies: list[AnomalyBucket] = Field(default_factory=list)


class AnomalyDetail(BaseModel):
    model: ModelStatus
    bucket: AnomalyBucket
    comparisons: list[Comparison]
    surrounding: list[AnomalyBucket]
