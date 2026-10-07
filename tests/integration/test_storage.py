import socket
import time
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.database.mongo import MongoStore
from app.database.postgres import PostgresStore

pytestmark = pytest.mark.integration


@pytest.fixture
def stores():
    settings = Settings()
    if not settings.postgres_password or not settings.mongo_uri:
        pytest.skip("integration requires configured running databases")
    postgres, mongo = PostgresStore(settings), MongoStore(settings)
    mongo.initialize()
    try:
        yield postgres, mongo
    finally:
        postgres.close()
        mongo.close()


def test_real_storage_duplicates_hypertable_and_bounded_bucket(stores, event):
    postgres, mongo = stores
    for _ in range(2):
        postgres.insert(event)
        mongo.insert(event)
    with postgres.pool.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM file_events WHERE timestamp=%s AND event_id=%s",
            (event.timestamp, event.event_id),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'file_events'"
        ).fetchone()[0] == 1
        indexes = connection.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'file_events'"
        ).fetchall()
        assert {row[0] for row in indexes} == {"file_events_pkey", "file_events_type_time_idx"}
        buckets = connection.execute(
            "SELECT time_bucket('1 minute', timestamp), count(*) FROM file_events "
            "WHERE timestamp >= %s AND timestamp < %s AND event_id=%s GROUP BY 1",
            (event.timestamp - timedelta(seconds=1), event.timestamp + timedelta(seconds=1),
             event.event_id),
        ).fetchall()
        assert len(buckets) == 1 and buckets[0][1] == 1
    assert mongo.collection.count_documents({"_id": str(event.event_id)}) == 1
    document = mongo.collection.find_one({"_id": str(event.event_id)})
    assert document["event_id"] == str(event.event_id)


def test_live_collector_malformed_then_valid(stores, event):
    postgres, mongo = stores
    settings = Settings()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        for payload in (b"\xff", b"{bad", b"[]", b"x" * 8193):
            sender.sendto(payload, ("collector", settings.udp_port))
        sender.sendto(event.model_dump_json().encode(), ("collector", settings.udp_port))
    deadline = time.monotonic() + 15
    found = False
    while time.monotonic() < deadline:
        with postgres.pool.connection() as connection:
            found = connection.execute(
                "SELECT count(*) FROM file_events WHERE timestamp=%s AND event_id=%s",
                (event.timestamp, event.event_id),
            ).fetchone()[0] == 1
        if found and mongo.collection.find_one({"_id": str(event.event_id)}):
            break
        time.sleep(0.2)
    assert found and mongo.collection.find_one({"_id": str(event.event_id)})
