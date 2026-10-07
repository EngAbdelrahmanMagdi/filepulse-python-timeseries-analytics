import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.analytics.anomalies import FEATURES, AnomalyService, feature_frame, minute
from app.api.anomaly_schemas import AnomalyQuery
from app.api.server import create_app

NOW = datetime(2026, 10, 7, 12, 0, 45, tzinfo=UTC)


def bucket(stamp, magnitude=1):
    return dict(
        timestamp=stamp,
        total=magnitude * 10,
        created=magnitude * 3,
        modified=magnitude * 4,
        deleted=magnitude * 2,
        moved=magnitude,
        unique_files=magnitude * 4,
        unique_extensions=min(magnitude, 3),
        bytes_changed=None,
        eligible_delta_events=magnitude * 4,
        known_delta_events=0,
    )


def history(count=40, spacing=1):
    return [
        bucket(minute(NOW) - timedelta(minutes=(count - i) * spacing), 1 + i % 4)
        for i in range(count)
    ]


def service(rows):
    result = AnomalyService(SimpleNamespace())
    result.buckets = lambda start, end: [dict(r) for r in rows if start <= r["timestamp"] < end]
    return result


def test_training_raw_statistics_reproducibility_and_future_burst():
    rows = history()
    a, b = service(rows), service(rows)
    assert a.train(NOW).state == b.train(NOW).state == "ready"
    assert a.status().training_cutoff == minute(NOW)
    assert a.status().sample_count == 40
    frame = feature_frame(rows)
    assert list(frame[FEATURES].dtypes.astype(str).unique()) == ["int64"]
    assert a._model.statistics.loc["total", "median"] == frame.total.median()
    future = [bucket(minute(NOW), 2), bucket(minute(NOW) + timedelta(minutes=1), 100)]
    scored = a.score(future, a._model)
    again = b.score(future, b._model)
    assert [r.anomaly_score for r in scored] == [r.anomaly_score for r in again]
    assert scored[1].anomaly_score > scored[0].anomaly_score and scored[1].flagged
    assert scored[1].bytes_changed is None
    assert next(c for c in scored[1].observed_context if c.feature == "total").observed == 1000
    assert all(c.p95 < 100 for c in scored[1].observed_context)
    assert np.isfinite(scored[1].anomaly_score)


@pytest.mark.parametrize(
    "rows", [[], history(29), history(30), [bucket(r["timestamp"]) for r in history(40)]]
)
def test_insufficient_active_elapsed_or_variation(rows):
    detector = service(rows)
    assert detector.train(NOW).state == "insufficient_data"
    assert detector.list(AnomalyQuery(), NOW).timeline == []


def test_nonconsecutive_buckets_idle_open_minute_and_cutoff():
    rows = history(30, spacing=2)
    rows += [bucket(minute(NOW), 100), bucket(minute(NOW) - timedelta(minutes=1), 0)]
    detector = service(rows)
    assert detector.train(NOW).sample_count == 30
    model = detector._model
    scored = detector.score(
        [
            bucket(minute(NOW) - timedelta(minutes=1)),
            bucket(minute(NOW)),
            bucket(minute(NOW) + timedelta(minutes=1), 0),
        ],
        model,
    )
    assert [r.state for r in scored] == ["outside_scoring_period", "scored", "inactive"]
    result = detector.list(AnomalyQuery(), NOW)
    assert all(r.timestamp < minute(NOW) for r in result.timeline)
    assert result.scored_count == 0
    assert AnomalyService(SimpleNamespace()).status().state == "not_trained"


def test_failed_retraining_preserves_model_and_concurrent_busy():
    detector = service(history())
    detector.train(NOW)
    previous = detector._model
    detector.buckets = lambda *args: []
    result = detector.train(NOW)
    assert result.state == "ready" and result.last_attempt == "insufficient_data"
    assert detector._model is previous
    entered, release = threading.Event(), threading.Event()

    def failing(*args):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("secret details")

    detector.buckets = failing
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(detector.train, NOW)
        assert entered.wait(5)
        assert detector.status().training
        assert detector.train(NOW).last_attempt == "busy"
        release.set()
        assert future.result().last_attempt == "failed"
    assert detector._model is previous and "secret" not in detector.status().message
    fresh = service([])
    fresh.buckets = failing
    assert fresh.train(NOW).state == "failed"


def test_api_validation_not_trained_training_detail_and_paging():
    app = create_app()
    detector = service(history())
    app.state.anomalies = detector
    client = TestClient(app)  # No lifespan: fixture owns the service.
    assert client.get("/api/v1/anomalies/model").json()["state"] == "not_trained"
    assert client.get("/api/v1/anomalies").json()["timeline"] == []
    for query in ["window=invalid", "limit=201", "offset=10001", "event_type=created"]:
        assert client.get("/api/v1/anomalies?" + query).status_code == 422
    assert client.post("/api/v1/anomalies/train", json={"root": "/"}).status_code == 422
    # Historical fixture is outside real clock cutoff; explicitly train at its clock.
    assert detector.train(NOW).state == "ready"
    for stamp in ["2026-10-07T12:00:01Z", "2026-10-07T12:00:00"]:
        assert (
            client.get("/api/v1/anomalies/detail", params={"timestamp": stamp}).status_code == 422
        )
    assert (
        client.get(
            "/api/v1/anomalies/detail", params={"timestamp": "2099-01-01T00:00:00Z"}
        ).status_code
        == 404
    )
    cutoff = minute(NOW)
    detector.buckets = lambda start, end: [
        bucket(cutoff + timedelta(minutes=i), 100)
        for i in range(3)
        if start <= cutoff + timedelta(minutes=i) < end
    ]
    response = detector.list(AnomalyQuery(limit=2), NOW + timedelta(minutes=3))
    assert response.flagged_count == 3 and response.has_more
    assert response.anomalies[0].timestamp > response.anomalies[1].timestamp
    detail = detector.detail(cutoff, NOW + timedelta(minutes=3))
    assert detail.bucket.state == "scored" and len(detail.comparisons) == 7


def test_real_query_shape_gaps_and_unknown_bytes():
    recorded = []

    def rows(sql, params):
        recorded.append((sql, params))
        return [
            dict(
                timestamp=minute(NOW),
                total=1,
                created=0,
                modified=1,
                deleted=0,
                moved=0,
                unique_files=1,
                unique_extensions=0,
                eligible_delta_events=1,
                known_delta_events=0,
                observed_bytes=None,
            )
        ]

    detector = AnomalyService(SimpleNamespace(rows=rows))
    result = detector.buckets(minute(NOW), minute(NOW) + timedelta(minutes=2))
    assert result[0]["bytes_changed"] is None and result[1]["bytes_changed"] == 0
    assert (
        "time_bucket" in recorded[0][0] and "timestamp >= %s AND timestamp < %s" in recorded[0][0]
    )
    with pytest.raises(ValueError):
        detector.buckets(minute(NOW) - timedelta(days=8), minute(NOW))


def test_training_http_outcomes_and_safe_scoring_failure():
    from psycopg_pool import PoolTimeout

    detector = service([])
    app = create_app()
    app.state.anomalies = detector
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/api/v1/anomalies/train", json={}).status_code == 422
    original = detector.train
    detector.buckets = service(history()).buckets
    detector.train = lambda: original(NOW)
    assert client.post("/api/v1/anomalies/train", json={}).json()["state"] == "ready"
    detector._training.acquire()
    try:
        assert client.post("/api/v1/anomalies/train", json={}).status_code == 409
    finally:
        detector._training.release()

    def unavailable(*args):
        raise PoolTimeout("password secret")

    detector.buckets = unavailable
    failed = client.post("/api/v1/anomalies/train", json={})
    assert failed.status_code == 503 and failed.json()["state"] == "ready"
    assert "secret" not in failed.text
    scored = client.get("/api/v1/anomalies")
    assert scored.status_code == 503 and "secret" not in scored.text
