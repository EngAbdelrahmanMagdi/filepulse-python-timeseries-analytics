from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.analytics.anomalies import AnomalyService, minute
from app.analytics.queries import EventQueries
from app.core.config import Settings
from app.core.models import FileEvent
from app.database.postgres import PostgresStore

pytestmark = pytest.mark.integration


def test_real_anomaly_buckets_training_cutoff_and_scoring():
    settings = Settings()
    if not settings.postgres_password or not settings.mongo_uri:
        pytest.skip("requires running databases")
    store = PostgresStore(settings)
    detector = AnomalyService(EventQueries(store))
    cutoff = datetime(2042, 1, 2, 12, tzinfo=UTC)
    events = []
    try:
        for index in range(35):
            stamp = cutoff - timedelta(minutes=35 - index)
            for number in range(2 + index % 4):
                event = FileEvent(
                    event_id=uuid4(),
                    timestamp=stamp + timedelta(seconds=number),
                    event_type="modified",
                    relative_path=f"demo/ml-{number}.txt",
                    is_directory=number == 0,
                    extension=None if number == 0 else ".txt",
                    size_bytes=None if number == 0 else 100,
                    size_delta=None,
                )
                store.insert(event)
                events.append(event)
        status = detector.train(cutoff + timedelta(seconds=59))
        assert status.state == "ready" and status.sample_count == 35
        assert status.training_cutoff == cutoff
        for number in range(60):
            event = FileEvent(
                event_id=uuid4(),
                timestamp=cutoff + timedelta(seconds=number % 50),
                event_type="modified",
                relative_path=f"demo/ml-burst-{number}.csv",
                is_directory=False,
                extension=".csv",
                size_bytes=100,
                size_delta=None,
            )
            store.insert(event)
            events.append(event)
        assert detector.buckets(cutoff - timedelta(minutes=1), cutoff)[0]["total"] == 4
        from app.api.anomaly_schemas import AnomalyQuery

        response = detector.list(AnomalyQuery(window="1h"), cutoff + timedelta(minutes=1))
        assert response.scored_count == 1 and response.flagged_count == 1
        burst = response.anomalies[0]
        assert burst.timestamp == cutoff and burst.bytes_changed is None
        detail = detector.detail(cutoff, cutoff + timedelta(minutes=1))
        assert detail.bucket.unique_files == 60 and detail.bucket.unique_extensions == 1
        assert detail.bucket.total == 60 and minute(status.training_cutoff) == cutoff
    finally:
        with store.pool.connection() as connection:
            connection.execute(
                "DELETE FROM file_events WHERE event_id=ANY(%s)", ([e.event_id for e in events],)
            )
        store.close()
