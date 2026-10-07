from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row

from app.api.schemas import (
    ActivityBucket,
    ActivityQuery,
    ActivityTotals,
    EventsQuery,
    EventsResponse,
    SeriesQuery,
    SeriesResponse,
)
from app.database.postgres import PostgresStore

WINDOWS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
BUCKETS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
ELIGIBLE = (
    "(NOT is_directory AND (event_type='modified' OR "
    "(event_type='moved' AND size_delta IS NOT NULL AND size_delta<>0)))"
)
AGGREGATES = f"""
count(*) AS total,
count(*) FILTER (WHERE event_type='created') AS created,
count(*) FILTER (WHERE event_type='modified') AS modified,
count(*) FILTER (WHERE event_type='deleted') AS deleted,
count(*) FILTER (WHERE event_type='moved') AS moved,
count(DISTINCT relative_path) FILTER (WHERE NOT is_directory) AS unique_files,
count(*) FILTER (WHERE {ELIGIBLE}) AS eligible_delta_events,
count(*) FILTER (WHERE {ELIGIBLE} AND size_delta IS NOT NULL) AS known_delta_events,
sum(abs(size_delta::numeric)) FILTER (WHERE NOT is_directory) AS observed_bytes
"""
BOUNDED = "timestamp >= %s AND timestamp < %s AND (%s::text IS NULL OR event_type=%s)"


def bounds(query: ActivityQuery, end: datetime | None = None) -> tuple[datetime, datetime]:
    end = (end or datetime.now(UTC)).astimezone(UTC)
    return end - timedelta(seconds=WINDOWS[query.window]), end


def totals(row: dict[str, Any]) -> ActivityTotals:
    data = dict(row)
    observed = data.pop("observed_bytes", None)
    # Unknown eligible measurements are not silently presented as zero.
    data["bytes_changed"] = (
        0
        if data["eligible_delta_events"] == 0
        else None
        if data["known_delta_events"] == 0
        else int(observed or 0)
    )
    return ActivityTotals.model_validate(data)


class EventQueries:
    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    def rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.store.pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

    def timeseries(self, query: SeriesQuery, end: datetime | None = None) -> SeriesResponse:
        start, end = bounds(query, end)
        seconds = BUCKETS[query.bucket]
        rows = self.rows(
            f"SELECT time_bucket(%s::interval, timestamp) AS timestamp, {AGGREGATES} "
            f"FROM file_events WHERE {BOUNDED} GROUP BY 1 ORDER BY 1",
            (f"{seconds} seconds", start, end, query.event_type, query.event_type),
        )
        measured = {
            row["timestamp"].astimezone(UTC): totals(
                {key: value for key, value in row.items() if key != "timestamp"}
            )
            for row in rows
        }
        # Supported minute/hour buckets share PostgreSQL's UTC epoch alignment.
        stamp = datetime.fromtimestamp(int(start.timestamp()) // seconds * seconds, UTC)
        buckets = []
        while stamp < end:
            buckets.append(
                ActivityBucket(
                    timestamp=stamp, **measured.get(stamp, ActivityTotals()).model_dump()
                )
            )
            stamp += timedelta(seconds=seconds)
        return SeriesResponse(start=start, end=end, bucket=query.bucket, buckets=buckets)

    def events(self, query: EventsQuery, end: datetime | None = None) -> EventsResponse:
        start, end = bounds(query, end)
        rows = self.rows(
            f"SELECT * FROM file_events WHERE {BOUNDED} "
            "ORDER BY timestamp DESC, event_id DESC LIMIT %s OFFSET %s",
            (start, end, query.event_type, query.event_type, query.limit + 1, query.offset),
        )
        return EventsResponse(
            start=start,
            end=end,
            limit=query.limit,
            offset=query.offset,
            has_more=len(rows) > query.limit,
            events=rows[: query.limit],
        )

    def overview(self, query: ActivityQuery, end: datetime) -> dict[str, Any]:
        start, end = bounds(query, end)
        params = (start, end, query.event_type, query.event_type)
        activity = totals(
            self.rows(
                f"SELECT {AGGREGATES} FROM file_events WHERE {BOUNDED}",
                params,
            )[0]
        )
        today = end.replace(hour=0, minute=0, second=0, microsecond=0)
        rates = self.rows(
            "SELECT count(*) FILTER (WHERE timestamp >= %s) AS events_today, "
            "count(*) FILTER (WHERE timestamp >= %s) AS events_per_minute "
            f"FROM file_events WHERE {BOUNDED}",
            (
                today,
                end - timedelta(seconds=60),
                min(today, end - timedelta(seconds=60)),
                end,
                query.event_type,
                query.event_type,
            ),
        )[0]
        latest = self.rows(
            f"SELECT * FROM file_events WHERE {BOUNDED} "
            "ORDER BY timestamp DESC, event_id DESC LIMIT 1",
            params,
        )
        extensions = self.rows(
            f"SELECT extension, count(*) AS events FROM file_events WHERE {BOUNDED} "
            "AND NOT is_directory GROUP BY extension ORDER BY events DESC, extension LIMIT 10",
            params,
        )
        return {
            "activity": activity,
            **rates,
            "latest_event": latest[0] if latest else None,
            "active_extensions": extensions,
        }
