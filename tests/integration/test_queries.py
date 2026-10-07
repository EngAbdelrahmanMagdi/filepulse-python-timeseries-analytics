from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.analytics.queries import EventQueries
from app.api.schemas import ActivityQuery, EventsQuery, SeriesQuery
from app.core.config import Settings
from app.core.models import FileEvent
from app.database.postgres import PostgresStore

pytestmark = pytest.mark.integration


def test_real_bounded_queries_coverage_gaps_order_and_rate():
    settings = Settings()
    if not settings.postgres_password or not settings.mongo_uri:
        pytest.skip("requires running databases")
    store = PostgresStore(settings)
    queries = EventQueries(store)
    end = datetime(2040, 1, 2, 12, 0, tzinfo=UTC)
    base = end - timedelta(minutes=3)
    events = []

    def add(kind, seconds, delta=None, directory=False, path="demo/a.txt"):
        event = FileEvent(
            event_id=uuid4(),
            timestamp=base + timedelta(seconds=seconds),
            event_type=kind,
            relative_path=path,
            destination_path="demo/b.txt" if kind == "moved" else None,
            is_directory=directory,
            extension=None if directory else ".txt",
            size_bytes=None if directory or kind == "deleted" else 100,
            size_delta=delta,
        )
        store.insert(event)
        events.append(event)

    try:
        add("created", 1)
        add("modified", 2, directory=True, path="demo")
        add("modified", 3, 100)
        add("modified", 3, -90)
        add("moved", 4, 0)
        add("modified", 61)  # Eligible but unknown: null bytes.
        add("moved", 62, 7)  # Actual observed size change: eligible.
        add("modified", 121)  # Unknown-only bucket.
        series = queries.timeseries(SeriesQuery(window="1h", bucket="1m"), end)
        assert len(series.buckets) == 60
        assert all(row.timestamp.tzinfo == UTC for row in series.buckets)
        assert all(row.total == 0 and row.bytes_changed == 0 for row in series.buckets[:-3])
        first, second, third = series.buckets[-3:]
        assert first.total == 5 and first.unique_files == 1
        assert first.bytes_changed == 190 and first.eligible_delta_events == 2
        assert first.known_delta_events == 2
        assert second.bytes_changed == 7 and second.eligible_delta_events == 2
        assert second.known_delta_events == 1
        assert third.bytes_changed is None and third.eligible_delta_events == 1
        filtered = queries.timeseries(
            SeriesQuery(window="1h", bucket="1m", event_type="created"), end
        )
        assert filtered.buckets[-3].total == 1 and filtered.buckets[-3].bytes_changed == 0
        overview = queries.overview(ActivityQuery(window="1h"), end)
        assert overview["activity"].total == 8
        assert overview["activity"].bytes_changed == 197
        assert overview["events_per_minute"] == 1
        assert overview["events_today"] == 8
        expected = sorted(events, key=lambda event: (event.timestamp, event.event_id), reverse=True)
        page = queries.events(EventsQuery(window="1h", limit=3), end)
        assert page.has_more and [e.event_id for e in page.events] == [
            e.event_id for e in expected[:3]
        ]
        last = queries.events(EventsQuery(window="1h", limit=3, offset=6), end)
        assert not last.has_more and len(last.events) == 2
        with store.pool.connection() as connection:
            count = connection.execute(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name='file_events'"
            ).fetchone()[0]
            assert count == 1
    finally:
        with store.pool.connection() as connection:
            connection.execute(
                "DELETE FROM file_events WHERE timestamp >= %s AND timestamp < %s "
                "AND event_id = ANY(%s)",
                (base, end, [event.event_id for event in events]),
            )
        store.close()
