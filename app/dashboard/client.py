import httpx
import streamlit as st

from app.core.config import Settings


@st.cache_resource
def client() -> httpx.Client:
    settings = Settings()
    return httpx.Client(
        base_url=settings.api_base_url,
        timeout=settings.api_timeout_seconds,
        transport=httpx.HTTPTransport(retries=0),
    )


def request(endpoint: str, params: tuple = (), *, method: str = "GET") -> dict:
    try:
        response = client().request(
            method, endpoint, params=dict(params), json={} if method == "POST" else None
        )
        if response.status_code == 503 and endpoint == "/health":
            return response.json()
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Unexpected response")
        return result
    except (httpx.HTTPError, ValueError):
        return {"error": "Connection unavailable. Check the API service and try again."}


@st.cache_data(ttl=10, max_entries=128, show_spinner=False)
def live_data(endpoint: str, params: tuple = ()) -> dict:
    return request(endpoint, params)


@st.cache_data(ttl=45, max_entries=4, show_spinner=False)
def static_data(endpoint: str) -> dict:
    return request(endpoint)


def refresh_scan(params: tuple = ()) -> dict:
    result = request("/api/v1/files/scan", method="POST")
    if result.get("state") == "ready" and not result.get("error"):
        # Overview includes static KPIs, so its live cache must also be invalidated.
        static_data.clear()
        live_data.clear()
        static_data("/api/v1/files/summary")
        static_data("/api/v1/files/extensions")
        live_data("/api/v1/overview", params)
    return result
