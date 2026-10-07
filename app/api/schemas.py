from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import FileEvent

Window = Literal["1h", "6h", "24h", "7d"]
Bucket = Literal["1m", "5m", "15m", "1h"]
EventType = Literal["created", "modified", "deleted", "moved"]


class ActivityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: Window = "24h"
    event_type: EventType | None = None


class SeriesQuery(ActivityQuery):
    bucket: Bucket = "15m"


class EventsQuery(ActivityQuery):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=10000)


class ActivityTotals(BaseModel):
    total: int = 0
    created: int = 0
    modified: int = 0
    deleted: int = 0
    moved: int = 0
    unique_files: int = 0
    bytes_changed: int | None = 0
    eligible_delta_events: int = 0
    known_delta_events: int = 0


class ActivityBucket(ActivityTotals):
    timestamp: datetime


class SeriesResponse(BaseModel):
    start: datetime
    end: datetime
    bucket: Bucket
    buckets: list[ActivityBucket]


class EventsResponse(BaseModel):
    start: datetime
    end: datetime
    limit: int
    offset: int
    has_more: bool
    events: list[FileEvent]


class ExtensionSummary(BaseModel):
    extension: str | None
    files: int
    bytes: int


class ScannedFile(BaseModel):
    relative_path: str
    filename: str
    extension: str | None
    size_bytes: int
    directory_depth: int
    is_empty: bool
    modified_at: datetime
    created_at: datetime | None


class FileSummary(BaseModel):
    total_files: int
    total_bytes: int
    average_size: float
    median_size: float
    p95_size: float
    empty_count: int
    extensions: list[ExtensionSummary]
    depth_distribution: dict[str, int]
    size_distribution: dict[str, int]
    largest_files: list[ScannedFile]
    recent_files: list[ScannedFile]
    oldest_files: list[ScannedFile]
    empty_files: list[ScannedFile]


class ScanStatus(BaseModel):
    state: Literal["not_scanned", "ready", "stale", "failed"] = "not_scanned"
    scanned_at: datetime | None = None
    skipped_entries: int = 0
    message: str | None = None


class ScanResponse(ScanStatus):
    summary: FileSummary | None = None


class ExtensionsResponse(ScanStatus):
    extensions: list[ExtensionSummary]


class ActiveExtension(BaseModel):
    extension: str | None
    events: int


class OverviewResponse(BaseModel):
    start: datetime
    end: datetime
    activity_available: bool
    activity_message: str | None = None
    activity: ActivityTotals | None = None
    events_today: int | None = None
    events_per_minute: int | None = None
    latest_event: FileEvent | None = None
    active_extensions: list[ActiveExtension] = Field(default_factory=list)
    scan: ScanResponse


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    api: Literal["ready"] = "ready"
    postgres: Literal["ready", "unavailable"]
    timescaledb: Literal["ready", "unavailable"]
    mongodb: Literal["ready", "unavailable"]
    scan: ScanStatus
    monitored_directory: str = "watched_data/"
    access: str = "read-only"
    runtime_mode: str
    collector: str = "not reported"
    monitor: str = "not reported"
    latest_event_at: datetime | None = None


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
