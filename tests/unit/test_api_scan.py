import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from app.analytics.scan_cache import ScanCache
from app.api.server import create_app
from app.core.config import Settings


def test_one_lazy_scan_concurrent_failure_and_refresh(paths, monkeypatch):
    import app.analytics.scan_cache as module

    original = module.scan
    calls = []
    started, release = threading.Event(), threading.Event()

    def scanning(root):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return original(root)

    monkeypatch.setattr(module, "scan", scanning)
    (paths.root / "fixture.txt").write_text("metadata")
    cache = ScanCache(paths)
    assert cache.status().state == "not_scanned"
    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = [workers.submit(cache.get) for _ in range(8)]
        assert started.wait(5)
        assert cache.status().state == "not_scanned"  # Nonblocking; no extra scan.
        release.set()
        results = [future.result() for future in futures]
    assert len(calls) == 1
    assert all(result.summary.total_files == 1 for result in results)
    stamp = results[0].scanned_at
    (paths.root / "another.txt").write_text("second")
    assert cache.get().summary.total_files == 1
    assert cache.get(refresh=True).summary.total_files == 2

    def failed(root):
        calls.append(1)
        raise OSError("secret failure")

    monkeypatch.setattr(module, "scan", failed)
    stale = cache.get(refresh=True)
    assert stale.state == "stale" and stale.summary.total_files == 2
    assert stale.scanned_at >= stamp and "secret" not in stale.message
    count = len(calls)
    cache.get()
    assert len(calls) == count
    fresh = ScanCache(paths)
    assert fresh.get().state == "failed"
    count = len(calls)
    fresh.get()
    assert len(calls) == count


def test_api_health_no_scan_validation_and_safe_partial_failure(paths):
    class Queries:
        def rows(self, *args):
            raise RuntimeError("password=secret")

        def overview(self, *args):
            raise RuntimeError("password=secret")

        def timeseries(self, *args):
            raise PoolTimeout("password=secret")

    app = create_app()
    app.state.scans = ScanCache(paths)
    app.state.settings = Settings(_env_file=None)
    app.state.queries = Queries()
    app.state.mongo = SimpleNamespace(
        client=SimpleNamespace(admin=SimpleNamespace(command=lambda *args: {"ok": 1}))
    )
    client = TestClient(app, raise_server_exceptions=False)
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["mongodb"] == "ready"
    assert health.json()["scan"]["state"] == "not_scanned"
    assert app.state.scans.status().state == "not_scanned"
    for endpoint, params in [
        ("events", {"limit": 201}),
        ("events", {"offset": 10001}),
        ("events", {"limit": 0}),
        ("events", {"event_type": "injected"}),
        ("activity/timeseries", {"bucket": "1m; DROP TABLE file_events"}),
        ("overview", {"window": "all"}),
        ("events", {"unexpected": "x"}),
    ]:
        assert client.get(f"/api/v1/{endpoint}", params=params).status_code == 422
    assert client.post("/api/v1/files/scan", json={"root": "../"}).status_code == 422
    result = client.get("/api/v1/overview").json()
    assert not result["activity_available"] and result["scan"]["state"] == "ready"
    assert result["scan"]["summary"]["total_files"] == 0
    assert client.get("/api/v1/files/extensions").json()["extensions"] == []
    failure = client.get("/api/v1/activity/timeseries")
    assert failure.status_code == 503 and "secret" not in failure.text
    assert client.get("/api/v1/files/summary").status_code == 200
    assert client.post("/api/v1/files/scan", json={}).json()["scanned_at"]
