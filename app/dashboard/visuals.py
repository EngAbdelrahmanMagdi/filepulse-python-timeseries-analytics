from html import escape

import plotly.graph_objects as go
import streamlit as st

TOKENS = {
    "canvas": "#F3F5F7",
    "surface": "#FFFFFF",
    "navy": "#11243A",
    "muted": "#526477",
    "border": "#DCE3E8",
    "accent": "#007F7A",
    "warning": "#95621B",
    "critical": "#B84949",
    "radius": "10px",
    "shadow": "0 3px 14px rgba(17,36,58,.045)",
    "transition": "160ms",
}
EVENT_COLORS = {
    "created": "#007F7A",
    "modified": "#4479AE",
    "deleted": "#B84949",
    "moved": "#77659C",
}


def styles() -> None:
    t = TOKENS
    st.html(f"""<style>
    .fp-header {{background:{t["navy"]};color:white;border-radius:{t["radius"]};
      padding:22px 26px;display:flex;justify-content:space-between;align-items:center;
      gap:16px;flex-wrap:wrap;margin-bottom:12px;}}
    .fp-header h1 {{font-size:26px;letter-spacing:-.6px;margin:0;color:white;}}
    .fp-header p {{color:#C2D0DC;margin:4px 0 0;font-size:13px;}}
    .fp-tag {{font-size:12px;letter-spacing:.05em;color:#B9E7DF;}}
    .fp-dot {{display:inline-block;width:7px;height:7px;border-radius:50%;background:#73C8BB;
      margin-right:7px;animation:fp-pulse 2.4s ease-in-out infinite;}}
    .fp-card {{background:{t["surface"]};border:1px solid {t["border"]};
      border-radius:{t["radius"]};padding:18px 18px 16px;box-shadow:{t["shadow"]};
      min-height:132px;transition:box-shadow {t["transition"]} ease;}}
    .fp-card:hover {{box-shadow:0 5px 18px rgba(17,36,58,.09);}}
    .fp-label {{color:{t["muted"]};font-size:12px;font-weight:600;letter-spacing:.02em;}}
    .fp-value {{color:{t["navy"]};font-size:30px;font-weight:650;letter-spacing:-1px;
      font-variant-numeric:tabular-nums;margin:8px 0 5px;line-height:1.15;}}
    .fp-context {{color:{t["muted"]};font-size:12px;}}
    .fp-brand {{font-size:24px;font-weight:650;letter-spacing:-.6px;}}
    .fp-brand span {{color:#76CEC2;}}
    @keyframes fp-pulse {{50% {{opacity:.45;}}}}
    @media(prefers-reduced-motion:reduce) {{.fp-dot{{animation:none;}}
      .fp-card{{transition:none;}}}}
    </style>""")


def header(title: str, subtitle: str, live: bool = False) -> None:
    indicator = '<span class="fp-dot"></span>' if live else ""
    st.html(
        f'<div class="fp-header"><div><h1>{escape(title)}</h1>'
        f'<p>{escape(subtitle)}</p></div><div class="fp-tag">{indicator}'
        f"{'AUTO REFRESH · 10 SEC' if live else 'LOCAL OBSERVABILITY'}</div></div>"
    )


def metric(label: str, value: str, context: str) -> None:
    st.html(
        f'<div class="fp-card"><div class="fp-label">{escape(label)}</div>'
        f'<div class="fp-value">{escape(value)}</div>'
        f'<div class="fp-context">{escape(context)}</div></div>'
    )


def byte_label(value: float | int | None) -> str:
    if value is None:
        return "Unavailable"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{size:,.0f} B"
        size /= 1024
    return "Unavailable"


def coverage(activity: dict) -> str:
    eligible = activity.get("eligible_delta_events", 0)
    known = activity.get("known_delta_events", 0)
    return (
        f"{known / eligible:.0%} coverage · {known}/{eligible} eligible events"
        if eligible
        else "Measurement coverage: not applicable"
    )


def theme(figure: go.Figure, height: int = 310) -> go.Figure:
    figure.update_layout(
        template="plotly_white",
        height=height,
        paper_bgcolor="white",
        plot_bgcolor="white",
        font={"family": "Arial, sans-serif", "size": 12, "color": TOKENS["navy"]},
        margin={"l": 8, "r": 8, "t": 15, "b": 15},
        legend={"orientation": "h", "y": 1.13},
        hoverlabel={"bgcolor": TOKENS["navy"], "font_color": "white"},
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    figure.update_yaxes(gridcolor="#EDF1F4", zeroline=False, rangemode="tozero")
    return figure


def bars(labels: list, values: list, *, unit: str = "Count", colors=None) -> go.Figure:
    return theme(
        go.Figure(
            go.Bar(
                x=values,
                y=labels,
                orientation="h",
                marker_color=colors or TOKENS["accent"],
                hovertemplate=f"%{{y}}<br>{unit}: %{{x:,.0f}}<extra></extra>",
            )
        ).update_yaxes(autorange="reversed"),
        270,
    )


def activity_chart(buckets: list[dict]) -> go.Figure:
    figure = go.Figure()
    for name, color in EVENT_COLORS.items():
        figure.add_trace(
            go.Scatter(
                x=[row["timestamp"] for row in buckets],
                y=[row[name] for row in buckets],
                name=name.title(),
                mode="lines",
                line={"color": color, "width": 2},
                hovertemplate=f"%{{x|%d %b %H:%M}} UTC<br>{name.title()}: %{{y}}<extra></extra>",
            )
        )
    figure.update_xaxes(tickformat="%d %b\n%H:%M", title="Time (UTC)")
    figure.update_yaxes(title="Events · files and directories")
    return theme(figure)


def anomaly_chart(buckets: list[dict]) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=[row["timestamp"] for row in buckets],
            y=[row["anomaly_score"] for row in buckets],
            name="Anomaly score",
            mode="lines+markers",
            connectgaps=False,
            line={"color": TOKENS["accent"], "width": 2},
            marker={"size": 4},
            hovertemplate="%{x|%d %b %H:%M} UTC<br>Score: %{y:.3f}<extra></extra>",
        )
    )
    flagged = [row for row in buckets if row["flagged"]]
    figure.add_trace(
        go.Scatter(
            x=[row["timestamp"] for row in flagged],
            y=[row["anomaly_score"] for row in flagged],
            name="Flagged",
            mode="markers",
            marker={"color": EVENT_COLORS["deleted"], "size": 9},
            hovertemplate="%{x|%d %b %H:%M} UTC<br>Flagged score: %{y:.3f}<extra></extra>",
        )
    )
    figure.add_hline(y=0, line_dash="dot", line_color=TOKENS["muted"])
    figure.update_xaxes(title="Complete minute (UTC)", tickformat="%d %b\n%H:%M")
    figure.update_yaxes(title="Anomaly score · positive means flagged")
    return theme(figure)
