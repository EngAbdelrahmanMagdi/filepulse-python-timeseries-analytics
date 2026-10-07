from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.dashboard import client
from scripts.generate_activity import baseline


def test_baseline_uses_real_minute_spacing_and_seed(monkeypatch):
    import scripts.generate_activity as module

    clock, calls = [100.0], []
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def generated(profile, paths, interval, seed, count):
        calls.append((clock[0], seed, count))
        clock[0] += interval * 4
        return {"profile": profile, "created": count}

    monkeypatch.setattr(module, "generate", generated)
    baseline(None, 2.5, 42, 31)
    assert len(calls) == 31 and calls[-1][0] - calls[0][0] == 1800
    assert len({r[2] for r in calls}) >= 3
    assert clock[0] >= calls[-1][0] + 60
    with pytest.raises(ValueError):
        baseline(None, 0.1, 42, 31)
    with pytest.raises(ValueError):
        baseline(None, 2.5, 42, 0)


def test_training_invalidates_anomaly_caches():
    with (
        patch.object(client, "request", return_value={"last_attempt": "ready"}),
        patch.object(client, "live_data") as live,
    ):
        assert client.train_model()["last_attempt"] == "ready"
        live.clear.assert_called_once()
        live.assert_called_once_with("/api/v1/anomalies/model")


def test_only_scan_capable_requests_have_longer_timeout(monkeypatch):
    import httpx

    observed = []

    def respond(request):
        observed.append(request.extensions['timeout']['read'])
        return httpx.Response(200, json={})

    monkeypatch.setattr(client, 'client', lambda: httpx.Client(
        base_url='http://test', timeout=30, transport=httpx.MockTransport(respond)))
    for endpoint in ['/api/v1/files/scan', '/api/v1/files/summary', '/api/v1/overview',
                     '/api/v1/events', '/api/v1/anomalies']:
        client.request(endpoint)
    assert observed == [90, 90, 90, 30, 30]


@pytest.mark.parametrize("state", ["not_trained", "insufficient_data", "failed", "ready"])
def test_anomaly_page_states(state):
    def response(endpoint, params=()):
        if endpoint.endswith("/model"):
            return {
                "state": state,
                "sample_count": 40,
                "training_cutoff": "2026-10-07T12:00:00Z",
                "message": "Need more data" if state == "insufficient_data" else None,
            }
        return {
            "flagged_count": 0,
            "scored_count": 0,
            "latest_flagged": None,
            "timeline": [],
            "anomalies": [],
            "has_more": False,
        }

    with (
        patch("streamlit.navigation", return_value=SimpleNamespace(run=lambda: None)),
        patch.object(client, "live_data", side_effect=response),
    ):
        app = AppTest.from_string(
            "import importlib\nimport app.dashboard.main as page\n"
            "importlib.reload(page)\npage.anomalies()", default_timeout=20
        ).run()
    assert not app.exception and app.info
    if state == "ready":
        assert "No active complete minutes" in app.info[0].value
    else:
        assert "Collect at least 30" in app.info[0].value
