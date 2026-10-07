from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from app.dashboard.client import live_data, refresh_scan, static_data
from app.dashboard.visuals import (
    EVENT_COLORS,
    activity_chart,
    bars,
    byte_label,
    coverage,
    header,
    metric,
    styles,
)

st.set_page_config(
    page_title="FilePulse | Filesystem Intelligence",
    page_icon="assets/icon.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)
styles()


def get_data(endpoint: str, params: tuple = (), *, static: bool = False) -> dict | None:
    with st.spinner("Loading filesystem intelligence…"):
        result = static_data(endpoint) if static else live_data(endpoint, params)
    if result.get("error"):
        st.warning(result["error"], icon=":material/cloud_off:")
        st.caption("This panel is temporarily unavailable. Other available panels remain usable.")
        return None
    return result


def table(rows: list[dict], key: str) -> None:
    if not rows:
        st.info("No matching records in this view.", icon=":material/info:")
        return
    frame = pd.DataFrame(rows)
    for name in ("timestamp", "modified_at", "created_at"):
        if name in frame:
            frame[name] = pd.to_datetime(frame[name], utc=True).dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        key=key,
        column_config={
            "relative_path": st.column_config.TextColumn("Relative path"),
            "event_id": st.column_config.TextColumn("Event ID", width="small"),
        },
    )


def scan_notice(scan: dict) -> dict | None:
    if scan.get("state") in {"stale", "failed"}:
        st.warning(scan.get("message") or "Scan unavailable. Use Scan to retry.")
    if scan.get("scanned_at"):
        st.caption(
            f"Last successful scan: {scan['scanned_at']} · "
            f"Skipped entries: {scan.get('skipped_entries', 0)}"
        )
    summary = scan.get("summary")
    if summary is None:
        st.info("File metrics are unavailable until a scan succeeds. Open File Analysis to retry.")
    return summary


def remember_filter(widget: str, saved: str) -> None:
    st.session_state[saved] = st.session_state[widget]


def filters() -> tuple[tuple, str]:
    columns = st.columns([1, 1, 1, 2])
    windows = ["1h", "6h", "24h", "7d"]
    buckets = ["1m", "5m", "15m", "1h"]
    types = ["All", *EVENT_COLORS]
    with columns[0]:
        window = st.selectbox(
            "Time window",
            windows,
            index=windows.index(st.session_state.get("activity_window", "24h")),
            key="_window",
            on_change=remember_filter,
            args=("_window", "activity_window"),
        )
    with columns[1]:
        bucket = st.selectbox(
            "Bucket",
            buckets,
            index=buckets.index(st.session_state.get("activity_bucket", "15m")),
            key="_bucket",
            on_change=remember_filter,
            args=("_bucket", "activity_bucket"),
        )
    with columns[2]:
        event_type = st.selectbox(
            "Event type",
            types,
            index=types.index(st.session_state.get("activity_event_type", "All")),
            key="_event_filter",
            on_change=remember_filter,
            args=("_event_filter", "activity_event_type"),
        )
    params = (("window", window),) + ((("event_type", event_type),) if event_type != "All" else ())
    return params, bucket


def chart(figure, key: str) -> None:
    st.plotly_chart(
        figure, width="stretch", key=key, config={"displayModeBar": False, "responsive": True}
    )


def overview() -> None:
    header("Filesystem overview", "One monitored source. Clear activity and storage intelligence.")
    params, bucket = filters()
    result = get_data("/api/v1/overview", params)
    if not result:
        return
    summary = scan_notice(result["scan"])
    if not result["activity_available"]:
        st.warning(result["activity_message"])
    activity = result.get("activity") or {}
    top = result.get("active_extensions") or []
    values = [
        (
            "Total Files",
            f"{summary['total_files']:,}" if summary else "Unavailable",
            "Latest scan · files only",
        ),
        (
            "Storage Used",
            byte_label(summary["total_bytes"]) if summary else "Unavailable",
            "Latest scan · files only",
        ),
        (
            "Events Today",
            str(result["events_today"]) if result["events_today"] is not None else "Unavailable",
            "Today (UTC) · files + directories",
        ),
        (
            "Events / min",
            str(result["events_per_minute"])
            if result["events_per_minute"] is not None
            else "Unavailable",
            "Last 60 seconds · files + directories",
        ),
        (
            "Most Active Extension",
            (top[0]["extension"] or "No extension") if top else "—",
            "Selected window · file events",
        ),
    ]
    for col, value in zip(st.columns(5), values, strict=True):
        with col:
            metric(*value)
    with st.container(border=True):
        st.subheader("Activity over time")
        st.caption("Complete event stream · files and directories · UTC")
        series = get_data("/api/v1/activity/timeseries", params + (("bucket", bucket),))
        if series:
            if not any(row["total"] for row in series["buckets"]):
                st.info("No activity in this window. Modify a file or run the demo generator.")
            chart(activity_chart(series["buckets"]), "overview_series")
    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("Events by type")
        if result["activity_available"]:
            chart(
                bars(
                    list(EVENT_COLORS),
                    [activity.get(name, 0) for name in EVENT_COLORS],
                    colors=list(EVENT_COLORS.values()),
                ),
                "event_types",
            )
        else:
            st.info("Event counts temporarily unavailable.")
    with right, st.container(border=True):
        st.subheader("Active extensions")
        st.caption("File events in the selected window")
        if top:
            chart(
                bars(
                    [row["extension"] or "No extension" for row in top],
                    [row["events"] for row in top],
                ),
                "active_extensions",
            )
        else:
            st.info("No extension activity available in this window.")
    with st.container(border=True):
        st.subheader("Recent events")
        events = get_data("/api/v1/events", params + (("limit", 10),))
        if events:
            table(events["events"], "overview_events")
    if result["activity_available"]:
        st.caption(
            f"Bytes Changed: {byte_label(activity.get('bytes_changed'))} · {coverage(activity)}"
        )


def live_activity() -> None:
    header("Live activity", "A bounded view of the complete filesystem event stream.", live=True)
    params, bucket = filters()
    signature = (params, bucket)
    if st.session_state.get("paging_filters") != signature:
        st.session_state["offset"] = 0
        st.session_state["paging_filters"] = signature
    offset = st.session_state.get("offset", 0)
    if offset:
        st.caption("Automatic table updates paused while viewing older events.")

    @st.fragment(run_every=None if offset else "10s")
    def feed():
        result = get_data("/api/v1/overview", params)
        if result and result["activity_available"]:
            activity = result["activity"]
            for col, value in zip(
                st.columns(4),
                [
                    (
                        "Total Events",
                        f"{activity['total']:,}",
                        "Selected window · files + directories",
                    ),
                    ("Events / min", str(result["events_per_minute"]), "Last 60 seconds"),
                    ("Unique File Paths", str(activity["unique_files"]), "Directories excluded"),
                    ("Bytes Changed", byte_label(activity["bytes_changed"]), coverage(activity)),
                ],
                strict=True,
            ):
                with col:
                    metric(*value)
        elif result:
            st.warning(result["activity_message"])
        with st.container(border=True):
            st.subheader("Event timeline")
            series = get_data("/api/v1/activity/timeseries", params + (("bucket", bucket),))
            if series:
                chart(activity_chart(series["buckets"]), "live_series")
        with st.container(border=True):
            st.subheader("Event feed")
            st.caption(
                "Newest first · full relative paths · offset pages can shift as events arrive"
            )
            events = get_data("/api/v1/events", params + (("limit", 50), ("offset", offset)))
            if events:
                table(events["events"], "live_events")
                previous, following, _ = st.columns([1, 1, 4])
                if previous.button("Previous", disabled=offset == 0):
                    st.session_state["offset"] = max(0, offset - 50)
                    st.rerun()
                if following.button("Next", disabled=not events["has_more"] or offset >= 10000):
                    st.session_state["offset"] = offset + 50
                    st.rerun()
        st.caption(
            f"Last refresh attempt: {datetime.now(UTC):%H:%M:%S} UTC · "
            "Auto refresh describes this view, not collector or monitor health."
        )

    feed()


def file_analysis() -> None:
    header("File analysis", "Metadata-only inventory. Scan when you need a fresh view.")
    if st.button("Scan monitored directory", icon=":material/refresh:", type="primary"):
        params = (("window", st.session_state.get("activity_window", "24h")),)
        event_type = st.session_state.get("activity_event_type", "All")
        if event_type != "All":
            params += (("event_type", event_type),)
        with st.spinner("Scanning metadata and refreshing dashboard data…"):
            result = refresh_scan(params)
        if result.get("error") or result.get("state") != "ready":
            st.warning("Scan could not be completed. The previous successful result is preserved.")
        else:
            st.success(f"Scan complete · {result['scanned_at']}")
    result = get_data("/api/v1/files/summary", static=True)
    if not result:
        return
    summary = scan_notice(result)
    if not summary:
        return
    if summary["total_files"] == 0:
        st.info("No regular files found. Add files to the monitored directory and run Scan again.")
    for col, value in zip(
        st.columns(5),
        [
            ("Files", f"{summary['total_files']:,}", "Regular files only"),
            ("Storage", byte_label(summary["total_bytes"]), "Metadata sizes"),
            (
                "Mean / Median",
                byte_label(summary["average_size"]),
                f"Median {byte_label(summary['median_size'])}",
            ),
            ("95th Percentile", byte_label(summary["p95_size"]), "File size"),
            ("Empty Files", str(summary["empty_count"]), "Zero bytes"),
        ],
        strict=True,
    ):
        with col:
            metric(*value)
    left, right = st.columns(2)
    extensions = get_data("/api/v1/files/extensions", static=True)
    with left, st.container(border=True):
        st.subheader("Storage by extension")
        rows = (extensions or {}).get("extensions", [])
        if rows:
            chart(
                bars(
                    [row["extension"] or "No extension" for row in rows[:10]],
                    [row["bytes"] for row in rows[:10]],
                    unit="Bytes",
                ),
                "storage_extensions",
            )
        else:
            st.info("No extension data available.")
    with right, st.container(border=True):
        st.subheader("File-size distribution")
        distribution = summary["size_distribution"]
        chart(bars(list(distribution), list(distribution.values())), "size_distribution")
    with st.container(border=True):
        st.subheader("Directory depth")
        st.caption("Root-level files have depth zero.")
        depth = summary["depth_distribution"]
        if depth:
            chart(bars(list(depth), list(depth.values())), "depth_distribution")
        else:
            st.info("No file-depth observations available.")
    tabs = st.tabs(["Largest", "Recent", "Oldest", "Empty", "Extensions"])
    for tab, field in zip(
        tabs[:4], ["largest_files", "recent_files", "oldest_files", "empty_files"], strict=True
    ):
        with tab:
            st.caption(
                "Top 10 · oldest/recent use modification time; creation time only where reliable."
            )
            table(summary[field], field)
    with tabs[4]:
        table((extensions or {}).get("extensions", []), "extension_table")


def anomalies() -> None:
    header("Anomalies", "A dedicated workspace for future statistical analysis.")
    with st.container(border=True):
        st.subheader("Detection is not enabled")
        st.write(
            "Activity and file analytics are available today. Statistical detection is not "
            "implemented, so this workspace does not display scores or findings."
        )


def system() -> None:
    header("System", "Dependency health and current monitoring context.")
    health = get_data("/health")
    if not health:
        return
    if health["status"] != "healthy":
        st.warning("Some dependencies are unavailable. Independent data panels may still work.")
    for col, name in zip(st.columns(4), ["api", "postgres", "timescaledb", "mongodb"], strict=True):
        with col:
            metric(
                name.upper() if name == "api" else name.title(),
                health[name].title(),
                "Current health probe",
            )
    with st.container(border=True):
        st.subheader("Monitoring context")
        table(
            [
                {"Component": "Monitored directory", "State": health["monitored_directory"]},
                {"Component": "Access", "State": health["access"]},
                {"Component": "Observer", "State": health["runtime_mode"]},
                {"Component": "Collector", "State": health["collector"]},
                {"Component": "Monitor", "State": health["monitor"]},
                {
                    "Component": "Latest persisted event (last 7 days)",
                    "State": health["latest_event_at"] or "No recent events",
                },
                {"Component": "Scan state", "State": health["scan"]["state"]},
                {
                    "Component": "Last successful scan",
                    "State": health["scan"]["scanned_at"] or "Not scanned",
                },
            ],
            "system_context",
        )
        st.caption(
            "No process heartbeat is collected. Recent events do not establish process liveness. "
            "Health inspection never scans the directory."
        )


with st.sidebar:
    st.html('<div class="fp-brand">File<span>Pulse</span></div>')
    st.caption("FILESYSTEM INTELLIGENCE")
    st.divider()
navigation = st.navigation(
    [
        st.Page(overview, title="Overview", icon=":material/dashboard:", default=True),
        st.Page(live_activity, title="Live Activity", icon=":material/timeline:"),
        st.Page(file_analysis, title="File Analysis", icon=":material/folder_open:"),
        st.Page(anomalies, title="Anomalies", icon=":material/query_stats:"),
        st.Page(system, title="System", icon=":material/dns:"),
    ]
)
with st.sidebar:
    st.divider()
    st.caption("watched_data/ · read-only")
    st.caption("Polling + UDP · best-effort capture")
navigation.run()
