from __future__ import annotations

from typing import Iterable

import plotly.graph_objects as go


PBIX_COLORWAY = [
    "#2563eb",
    "#f97316",
    "#14b8a6",
    "#a855f7",
    "#ef4444",
    "#eab308",
    "#0f766e",
    "#ec4899",
]


def _as_strings(values: Iterable) -> list[str]:
    return [str(value) for value in values or []]


def _as_floats(values: Iterable) -> list[float]:
    converted = []
    for value in values or []:
        try:
            converted.append(float(value))
        except Exception:
            continue
    return converted


def _looks_dense(labels: list[str]) -> bool:
    if not labels:
        return False
    return len(labels) > 6 or max(len(label) for label in labels) > 13


def _trim_single_series(x_values: list[str], y_values: list[float], limit: int = 12) -> tuple[list[str], list[float]]:
    pairs = list(zip(x_values, y_values))
    pairs = [pair for pair in pairs if pair[0] not in {None, ""}]
    pairs.sort(key=lambda item: float(item[1]), reverse=True)
    trimmed = pairs[:limit]
    return [str(item[0]) for item in trimmed], [float(item[1]) for item in trimmed]


def _trim_multi_series(x_values: list[str], series_payload: list[dict], limit: int = 12) -> tuple[list[str], list[dict]]:
    totals = []
    for index, x_value in enumerate(x_values):
        total = 0.0
        for series_item in series_payload:
            y_values = series_item.get("y", [])
            if index < len(y_values):
                try:
                    total += float(y_values[index])
                except Exception:
                    continue
        totals.append((str(x_value), total, index))

    totals.sort(key=lambda item: item[1], reverse=True)
    keep = totals[:limit]
    keep_indices = [item[2] for item in keep]
    trimmed_x = [totals_item[0] for totals_item in keep]
    trimmed_series = []
    for series_item in series_payload:
        y_values = series_item.get("y", [])
        trimmed_series.append({
            "name": series_item.get("name", "Series"),
            "y": [float(y_values[index]) if index < len(y_values) else 0.0 for index in keep_indices],
        })
    return trimmed_x, trimmed_series


def build_pbix_figure(chart: dict) -> go.Figure | None:
    if not chart or not chart.get("data_available", True):
        return None

    raw_x_values = list(chart.get("x", []) or [])
    x_values = _as_strings(raw_x_values)
    if not x_values:
        return None

    chart_type = str(chart.get("chart_type", "")).lower()
    measure_label = chart.get("measure_used", "Value")
    title = chart.get("title", "Chart")
    display_title = title

    if chart.get("series"):
        series_payload = [
            {"name": series_item.get("name", "Series"), "y": list(series_item.get("y", []))}
            for series_item in chart.get("series", [])
        ]
        if len(x_values) > 14 and any(
            token in chart_type for token in ("bar", "column", "stacked", "clustered", "pie", "donut")
        ):
            x_values, series_payload = _trim_multi_series(x_values, series_payload, limit=12)
            display_title = f"{title} (top view)"
            if "pie" in chart_type or "donut" in chart_type:
                chart_type = "clusteredBarChart"
    else:
        y_values = [float(value) for value in chart.get("y", [])]
        if len(x_values) > 14 and any(
            token in chart_type for token in ("bar", "column", "stacked", "clustered", "pie", "donut")
        ):
            x_values, y_values = _trim_single_series(x_values, y_values, limit=12)
            display_title = f"{title} (top view)"
            if "pie" in chart_type or "donut" in chart_type:
                chart_type = "clusteredBarChart"
    dense_labels = _looks_dense(x_values)

    fig = go.Figure()

    def style_trace(trace: go.BaseTraceType, index: int = 0) -> None:
        color = PBIX_COLORWAY[index % len(PBIX_COLORWAY)]
        if isinstance(trace, go.Scatter):
            trace.line = dict(color=color, width=3)
            trace.marker = dict(color=color, size=8, line=dict(color="#ffffff", width=1.2))
        elif isinstance(trace, go.Bar):
            trace.marker = dict(
                color=color,
                line=dict(color="rgba(15, 23, 42, 0.18)", width=0.8),
            )
        elif isinstance(trace, go.Funnel):
            trace.marker = dict(
                color=[PBIX_COLORWAY[i % len(PBIX_COLORWAY)] for i, _ in enumerate(trace.x or [])]
            )
        elif isinstance(trace, go.Pie):
            trace.marker = dict(
                colors=[PBIX_COLORWAY[i % len(PBIX_COLORWAY)] for i, _ in enumerate(trace.labels or [])],
                line=dict(color="#ffffff", width=1.5),
            )
        elif isinstance(trace, go.Treemap):
            trace.marker = dict(
                colors=[PBIX_COLORWAY[i % len(PBIX_COLORWAY)] for i, _ in enumerate(trace.labels or [])],
                line=dict(color="#ffffff", width=1.2),
            )

    def apply_common_layout(height: int = 430, width: int = 980) -> None:
        fig.update_layout(
            title={"text": display_title, "x": 0.02, "xanchor": "left"},
            template="plotly_white",
            height=height,
            width=width,
            margin=dict(l=88, r=40, t=82, b=92),
            colorway=PBIX_COLORWAY,
            paper_bgcolor="#ffffff",
            plot_bgcolor="#fcfdff",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="left",
                x=0,
            ),
            font=dict(color="#1f3557"),
        )
        fig.update_xaxes(
            showgrid=True,
            gridcolor="rgba(37, 99, 235, 0.08)",
            zeroline=False,
            automargin=True,
            tickfont=dict(color="#3b4f6a", size=12),
            title_font=dict(color="#1f3557", size=14),
        )
        fig.update_yaxes(
            showgrid=True,
            gridcolor="rgba(37, 99, 235, 0.08)",
            zeroline=False,
            automargin=True,
            tickfont=dict(color="#3b4f6a", size=12),
            title_font=dict(color="#1f3557", size=14),
        )

    if chart.get("series"):
        use_horizontal = dense_labels and any(
            token in chart_type for token in ("bar", "column", "stacked", "clustered", "pie", "donut")
        )

        for series_item in series_payload:
            y_values = series_item.get("y", [])
            name = series_item.get("name", "Series")

            if "line" in chart_type:
                fig.add_trace(
                    go.Scatter(
                        x=x_values,
                        y=y_values,
                        mode="lines+markers",
                        name=name,
                    )
                )
            elif "scatter" in chart_type:
                fig.add_trace(
                    go.Scatter(
                        x=x_values,
                        y=y_values,
                        mode="markers",
                        name=name,
                    )
                )
            elif "area" in chart_type:
                fig.add_trace(
                    go.Scatter(
                        x=x_values,
                        y=y_values,
                        mode="lines",
                        fill="tozeroy" if not fig.data else "tonexty",
                        stackgroup="stack" if len(series_payload) > 1 else None,
                        name=name,
                    )
                )
            elif "funnel" in chart_type:
                fig.add_trace(go.Funnel(y=x_values, x=y_values, name=name))
            elif "treemap" in chart_type:
                fig.add_trace(
                    go.Treemap(
                        labels=x_values,
                        parents=[""] * len(x_values),
                        values=y_values,
                        name=name,
                        textinfo="label+value",
                    )
                )
            elif "pie" in chart_type or "donut" in chart_type:
                fig.add_trace(go.Bar(x=y_values, y=x_values, orientation="h", name=name))
            elif "histogram" in chart_type:
                fig.add_trace(go.Histogram(x=_as_floats(y_values), name=name, nbinsx=18))
            elif "box" in chart_type:
                fig.add_trace(go.Box(x=x_values, y=y_values, name=name, boxmean=True))
            else:
                bar_kwargs = (
                    {
                        "x": y_values,
                        "y": x_values,
                        "orientation": "h",
                    }
                    if use_horizontal
                    else {
                        "x": x_values,
                        "y": y_values,
                        "orientation": "v",
                    }
                )
                fig.add_trace(
                    go.Bar(
                        name=name,
                        **bar_kwargs,
                    )
                )

        if "bar" in chart_type or "column" in chart_type or "stacked" in chart_type:
            fig.update_layout(barmode="group")
        for index, trace in enumerate(fig.data):
            style_trace(trace, index)
        apply_common_layout(
            height=500 if dense_labels else 450,
            width=1160 if dense_labels or "treemap" in chart_type or "funnel" in chart_type else 980,
        )

        if use_horizontal:
            fig.update_xaxes(title_text=measure_label, automargin=True)
            fig.update_yaxes(title_text=chart.get("dimension", "Category"), automargin=True)
        elif "pie" not in chart_type and "donut" not in chart_type and "treemap" not in chart_type:
            fig.update_xaxes(title_text=chart.get("dimension", "Category"), automargin=True)
            fig.update_yaxes(title_text=measure_label, automargin=True)
            if dense_labels:
                fig.update_xaxes(tickangle=-32)
        return fig

    y_values = y_values if 'y_values' in locals() else chart.get("y", [])
    if not y_values:
        return None

    use_horizontal = dense_labels and any(
        token in chart_type for token in ("bar", "column", "stacked", "clustered", "pie", "donut")
    )
    if "pie" in chart_type or "donut" in chart_type:
        use_horizontal = True

    if "line" in chart_type:
        fig.add_trace(go.Scatter(x=x_values, y=y_values, mode="lines+markers"))
    elif "area" in chart_type:
        fig.add_trace(go.Scatter(x=x_values, y=y_values, mode="lines", fill="tozeroy"))
    elif "scatter" in chart_type:
        scatter_x = _as_floats(raw_x_values)
        if len(scatter_x) == len(y_values):
            fig.add_trace(go.Scatter(x=scatter_x, y=y_values, mode="markers"))
        else:
            fig.add_trace(go.Scatter(x=x_values, y=y_values, mode="markers"))
    elif "treemap" in chart_type:
        fig.add_trace(
            go.Treemap(
                labels=x_values,
                parents=[""] * len(x_values),
                values=y_values,
                textinfo="label+value",
            )
        )
    elif "funnel" in chart_type:
        fig.add_trace(go.Funnel(y=x_values, x=y_values))
    elif "pie" in chart_type or "donut" in chart_type:
        if len(x_values) <= 8:
            fig.add_trace(
                go.Pie(
                    labels=x_values,
                    values=y_values,
                    hole=0.45 if "donut" in chart_type else 0.0,
                    textinfo="label+percent",
                )
            )
        else:
            fig.add_trace(
                go.Bar(
                    x=y_values,
                    y=x_values,
                    orientation="h",
                )
            )
    elif "histogram" in chart_type:
        hist_x = _as_floats(raw_x_values)
        if not hist_x:
            return None
        fig.add_trace(go.Histogram(x=hist_x, nbinsx=18))
    elif "box" in chart_type:
        fig.add_trace(go.Box(x=x_values, y=y_values, boxmean=True))
    else:
        bar_kwargs = (
            {
                "x": y_values,
                "y": x_values,
                "orientation": "h",
            }
            if use_horizontal
            else {
                "x": x_values,
                "y": y_values,
                "orientation": "v",
            }
        )
        fig.add_trace(
            go.Bar(
                **bar_kwargs,
            )
        )

    for index, trace in enumerate(fig.data):
        style_trace(trace, index)

    apply_common_layout(
        height=500 if dense_labels else 450,
        width=1160 if dense_labels or "treemap" in chart_type or "funnel" in chart_type else 980,
    )

    if use_horizontal:
        fig.update_xaxes(title_text=measure_label, automargin=True)
        fig.update_yaxes(title_text=chart.get("dimension", "Category"), automargin=True)
    elif "pie" not in chart_type and "donut" not in chart_type and "treemap" not in chart_type:
        fig.update_xaxes(title_text=chart.get("dimension", "Category"), automargin=True)
        fig.update_yaxes(title_text=measure_label, automargin=True)
        if dense_labels:
            fig.update_xaxes(tickangle=-32)

    if "histogram" in chart_type:
        fig.update_xaxes(title_text=chart.get("dimension", measure_label), automargin=True)
        fig.update_yaxes(title_text="Frequency", automargin=True)

    return fig
