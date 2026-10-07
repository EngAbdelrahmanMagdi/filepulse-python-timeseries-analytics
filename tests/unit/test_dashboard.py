from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.dashboard import client


def test_client_timeout_safe_and_health_degraded(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("credentials secret", request=request)

    monkeypatch.setattr(
        client,
        "client",
        lambda: httpx.Client(base_url="http://test", transport=httpx.MockTransport(timeout)),
    )
    result = client.request("/api/v1/events")
    assert "error" in result and "secret" not in str(result)
    monkeypatch.setattr(
        client,
        "client",
        lambda: httpx.Client(
            base_url="http://test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"status": "degraded"})
            ),
        ),
    )
    assert client.request("/health")["status"] == "degraded"


def test_scan_invalidates_and_refetches_all_static_dependents():
    with (
        patch.object(client, "request", return_value={"state": "ready", "scanned_at": "UTC"}),
        patch.object(client, "static_data") as static,
        patch.object(client, "live_data") as live,
    ):
        assert client.refresh_scan((("window", "6h"),))["scanned_at"] == "UTC"
        static.clear.assert_called_once()
        live.clear.assert_called_once()
        assert [call.args[0] for call in static.call_args_list] == [
            "/api/v1/files/summary",
            "/api/v1/files/extensions",
        ]
        live.assert_called_once_with("/api/v1/overview", (("window", "6h"),))
    with (
        patch.object(client, "request", return_value={"error": "unavailable"}),
        patch.object(client, "static_data") as static,
    ):
        client.refresh_scan()
        static.clear.assert_not_called()


def test_dashboard_renders_recoverable_failure():
    with patch.object(client, "live_data", return_value={"error": "Connection unavailable."}):
        app = AppTest.from_file("app/dashboard/main.py", default_timeout=15).run()
    assert not app.exception
    assert app.warning and "Connection unavailable" in app.warning[0].value


@pytest.mark.parametrize("page", ["live_activity", "file_analysis", "system", "anomalies"])
def test_pages_render_without_raw_errors_when_api_unavailable(page):
    error = {"error": "Connection unavailable."}
    with (
        patch("streamlit.navigation", return_value=SimpleNamespace(run=lambda: None)),
        patch.object(client, "live_data", return_value=error),
        patch.object(client, "static_data", return_value=error),
    ):
        app = AppTest.from_string(
            f"from app.dashboard.main import {page}\n{page}()", default_timeout=15
        ).run()
    assert not app.exception
    if page != "anomalies":
        assert app.warning
