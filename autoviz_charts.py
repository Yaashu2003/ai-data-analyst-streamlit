from pathlib import Path
from typing import List, Optional, Set

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from chart_assets import normalize_chart_stem


_BASE_DIR = Path(__file__).resolve().parent
CHARTS_DIR = _BASE_DIR / "charts"
CHARTS_HTML_DIR = _BASE_DIR / "charts_html"

CHARTS_DIR.mkdir(exist_ok=True)
CHARTS_HTML_DIR.mkdir(exist_ok=True)


class AutovizChartGenerator:
    """Generate a small, non-overlapping AutoViz overview pack."""

    _MAX_CATEGORY_LABEL = 24
    _TOP_CATEGORY_COUNT = 6
    _MIN_TIME_POINTS = 3

    def _save_chart(self, fig, name: str, saved_paths: List[str]) -> None:
        html_path = CHARTS_HTML_DIR / f"{name}.html"
        png_path = CHARTS_DIR / f"{name}.png"

        fig.update_annotations(font=dict(size=14))
        fig.update_layout(
            autosize=True,
            height=max(int(fig.layout.height or 0), 760),
            margin=dict(l=125, r=52, t=100, b=105),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        )
        fig.update_xaxes(automargin=True, tickfont=dict(size=11))
        fig.update_yaxes(automargin=True, tickfont=dict(size=11))
        fig.write_html(str(html_path), full_html=True, include_plotlyjs="cdn")

        representative_path = str(html_path.resolve())
        try:
            fig.write_image(str(png_path), width=1600, height=1100, scale=2)
            representative_path = str(png_path.resolve())
        except Exception as error:
            print(f"  PNG save failed for {name}: {error}")

        saved_paths.append(representative_path)
        print(f"[saved] {name}")

    def _truncate_label(self, value: object, max_length: Optional[int] = None) -> str:
        text = str(value)
        limit = max_length or self._MAX_CATEGORY_LABEL
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    def _normalize_existing_stems(self, existing_chart_stems) -> Set[str]:
        return {
            normalize_chart_stem(stem).lower()
            for stem in (existing_chart_stems or set())
            if stem
        }

    def _can_emit_chart(self, stem: str, existing_chart_stems: Set[str], generated_stems: Set[str]) -> bool:
        normalized = normalize_chart_stem(stem).lower()
        return normalized not in existing_chart_stems and normalized not in generated_stems

    def _coerce_numeric_series(self, series: pd.Series) -> pd.Series:
        if pd.api.types.is_numeric_dtype(series):
            return pd.to_numeric(series, errors="coerce")
        cleaned = (
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.extract(r"([-+]?\d*\.?\d+)", expand=False)
        )
        return pd.to_numeric(cleaned, errors="coerce")

    def _looks_numeric_like(self, series: pd.Series) -> bool:
        non_null = series.dropna()
        if non_null.empty:
            return False
        coerced = self._coerce_numeric_series(non_null)
        return coerced.notna().mean() >= 0.85 and coerced.nunique(dropna=True) > 10

    def _select_columns(self, df: pd.DataFrame):
        num_cols = df.select_dtypes(include="number").columns.tolist()
        cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()

        num_cols = [
            column for column in num_cols
            if df[column].nunique() > 10
            and not any(token in column.lower() for token in ["id", "postal", "zip", "index", "pincode", "code"])
        ]
        for column in cat_cols:
            if any(token in column.lower() for token in ["id", "postal", "zip", "index", "pincode", "code"]):
                continue
            if self._looks_numeric_like(df[column]):
                num_cols.append(column)
        cat_cols = [
            column for column in cat_cols
            if 2 <= df[column].nunique() <= 12
            and not any(token in column.lower() for token in ["id", "name", "address", "code"])
        ]
        deduped_num_cols = []
        for column in num_cols:
            if column not in deduped_num_cols:
                deduped_num_cols.append(column)
        return cat_cols, deduped_num_cols

    def _pick_kpi(self, df: pd.DataFrame, num_cols: List[str]) -> str:
        priority = ["sales", "profit", "revenue", "amount", "price"]
        for token in priority:
            for column in num_cols:
                if token in column.lower():
                    return column
        return max(num_cols, key=lambda column: df[column].std())

    def _find_date_column(self, df: pd.DataFrame):
        for column in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[column]):
                return column
        for column in df.columns:
            if any(term in column.lower() for term in ["date", "time", "scraped", "created", "updated", "timestamp", "at"]):
                sample = pd.to_datetime(df[column], errors="coerce")
                if sample.notna().sum() >= max(10, int(len(df) * 0.2)):
                    return column
        return None

    def _parse_date_column(self, series: pd.Series) -> pd.Series:
        if pd.api.types.is_datetime64_any_dtype(series):
            return pd.to_datetime(series, errors="coerce")
        if pd.api.types.is_numeric_dtype(series):
            parsed_numeric = pd.to_datetime(series, errors="coerce", unit="s")
            if parsed_numeric.notna().sum() >= self._MIN_TIME_POINTS:
                return parsed_numeric
        as_text = series.astype(str).str.strip()
        parsed_text = pd.to_datetime(as_text, errors="coerce")
        if parsed_text.notna().sum() >= self._MIN_TIME_POINTS:
            return parsed_text
        return pd.to_datetime(series, errors="coerce")

    def _top_categories(self, df: pd.DataFrame, cat_cols: List[str]) -> List[str]:
        scored = []
        for column in cat_cols:
            nunique = df[column].nunique()
            score = 0
            if 3 <= nunique <= 8:
                score += 2
            elif nunique <= 12:
                score += 1
            if any(token in column.lower() for token in ["segment", "category", "region", "type", "brand"]):
                score += 3
            scored.append((column, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return [item[0] for item in scored[:4]]

    def _top_category_counts(self, df: pd.DataFrame, cat_col: str) -> pd.Series:
        counts = df[cat_col].astype(str).value_counts().head(self._TOP_CATEGORY_COUNT)
        counts.index = [self._truncate_label(item) for item in counts.index]
        return counts.sort_values(ascending=True)

    def _top_grouped_metric(self, df: pd.DataFrame, cat_col: str, num_col: str) -> pd.DataFrame:
        grouped = (
            df.groupby(cat_col, dropna=False)[num_col]
            .sum()
            .sort_values(ascending=False)
            .head(self._TOP_CATEGORY_COUNT)
            .reset_index()
        )
        grouped[cat_col] = grouped[cat_col].astype(str).apply(self._truncate_label)
        return grouped.sort_values(num_col, ascending=True)

    def _time_series_summary(self, df: pd.DataFrame, date_col: str, num_col: str) -> Optional[pd.DataFrame]:
        working = df[[date_col, num_col]].copy()
        working[date_col] = self._parse_date_column(working[date_col])
        working = working.dropna(subset=[date_col, num_col]).sort_values(date_col)
        if working.empty:
            return None
        date_range_days = max((working[date_col].max() - working[date_col].min()).days, 0)
        if date_range_days <= 2:
            working["time_bucket"] = working[date_col].dt.floor("h")
        elif date_range_days <= 60:
            working["time_bucket"] = working[date_col].dt.floor("D")
        else:
            working["time_bucket"] = working[date_col].dt.to_period("W").dt.start_time
        summary = working.groupby("time_bucket", as_index=False)[num_col].sum().sort_values("time_bucket")
        if len(summary) < self._MIN_TIME_POINTS:
            return None
        return summary

    def _format_kpi_value(self, value: float) -> str:
        if pd.isna(value):
            return "0"
        return f"{float(value):,.0f}"

    def _collect_kpi_metrics(self, df: pd.DataFrame, num_col: str):
        numeric_series = pd.to_numeric(df[num_col], errors="coerce").fillna(0)
        return [
            (f"Total {num_col.title()}", self._format_kpi_value(numeric_series.sum())),
            (f"Average {num_col.title()}", self._format_kpi_value(numeric_series.mean() or 0)),
            ("Records", f"{int(len(df)):,}"),
        ]

    def _add_kpi_indicators(self, fig, df: pd.DataFrame, num_col: str) -> None:
        numeric_series = pd.to_numeric(df[num_col], errors="coerce").fillna(0)
        total_value = float(numeric_series.sum())
        avg_value = float(numeric_series.mean() or 0)
        record_count = int(len(df))
        fig.add_trace(go.Indicator(
            mode="number",
            title={"text": f"Total {num_col.title()}"},
            value=total_value,
            number={"valueformat": ",.0f"},
            domain={"x": [0.0, 0.26], "y": [0.79, 0.98]},
        ))
        fig.add_trace(go.Indicator(
            mode="number",
            title={"text": f"Average {num_col.title()}"},
            value=avg_value,
            number={"valueformat": ",.0f"},
            domain={"x": [0.37, 0.63], "y": [0.79, 0.98]},
        ))
        fig.add_trace(go.Indicator(
            mode="number",
            title={"text": "Records"},
            value=record_count,
            number={"valueformat": ",.0f"},
            domain={"x": [0.74, 1.0], "y": [0.79, 0.98]},
        ))

    def generate_autoviz_charts(self, df, dataset_name="Dataset", existing_chart_stems=None, filename_prefix=""):
        print("Generating Smart Business Dashboard...")

        saved_paths: List[str] = []
        generated_stems: Set[str] = set()
        existing_chart_stems = self._normalize_existing_stems(existing_chart_stems)

        def chart_name(name: str) -> str:
            return f"{filename_prefix}{name}" if filename_prefix else name

        try:
            cat_cols, num_cols = self._select_columns(df)
            if not num_cols:
                print("[ERROR] No numeric columns")
                return []

            df = df.copy()
            for column in df.select_dtypes(include=["object", "category"]).columns:
                df[column] = df[column].astype(str).apply(self._truncate_label)
            for column in num_cols:
                df[column] = self._coerce_numeric_series(df[column])

            num = self._pick_kpi(df, num_cols)
            date_col = self._find_date_column(df)
            best_cats = self._top_categories(df, cat_cols)

            if self._can_emit_chart("Dashboard", existing_chart_stems, generated_stems):
                dashboard_fig = make_subplots(
                    rows=2,
                    cols=2,
                    subplot_titles=(
                        "KPI Trend",
                        "Distribution",
                        self._truncate_label(best_cats[0].title(), 22) if len(best_cats) > 0 else "Category 1",
                        self._truncate_label(best_cats[1].title(), 22) if len(best_cats) > 1 else "Category 2",
                    ),
                    vertical_spacing=0.22,
                    horizontal_spacing=0.16,
                )

                if date_col:
                    ts = self._time_series_summary(df, date_col, num)
                    if ts is not None:
                        dashboard_fig.add_trace(
                            go.Scatter(
                                x=ts["time_bucket"],
                                y=ts[num],
                                mode="lines+markers",
                                line=dict(color="#0f766e", width=2),
                                marker=dict(size=7),
                                showlegend=False,
                            ),
                            row=1,
                            col=1,
                        )
                    elif best_cats:
                        fallback = self._top_grouped_metric(df, best_cats[0], num)
                        if not fallback.empty:
                            dashboard_fig.add_trace(
                                go.Bar(
                                    x=fallback[best_cats[0]],
                                    y=fallback[num],
                                    marker_color="#0f766e",
                                    showlegend=False,
                                ),
                                row=1,
                                col=1,
                            )

                num_data = df[num].dropna()
                if not num_data.empty:
                    dashboard_fig.add_trace(
                        go.Histogram(
                            x=num_data,
                            marker_color="#7dd3c7",
                            showlegend=False,
                        ),
                        row=1,
                        col=2,
                    )

                palette = ["#2563eb", "#14b8a6"]
                for idx, cat_name in enumerate(best_cats[:2]):
                    counts = self._top_category_counts(df, cat_name)
                    if counts.empty:
                        continue
                    dashboard_fig.add_trace(
                        go.Bar(
                            x=counts.values,
                            y=counts.index,
                            orientation="h",
                            marker_color=palette[idx % len(palette)],
                            showlegend=False,
                        ),
                        row=2,
                        col=1 if idx == 0 else 2,
                    )

                dashboard_fig.update_layout(
                    title=dict(text="Business Overview Dashboard", x=0.03, xanchor="left"),
                    template="plotly_white",
                    height=900,
                    margin=dict(l=105, r=52, t=110, b=82),
                    showlegend=False,
                    title_font=dict(size=22, color="#0f2852"),
                )
                dashboard_fig.update_annotations(font=dict(size=16, color="#16396b"))
                dashboard_fig.update_xaxes(automargin=True, tickangle=-24, tickfont=dict(size=11), row=1, col=1)
                dashboard_fig.update_yaxes(automargin=True, tickfont=dict(size=11), row=1, col=1)
                dashboard_fig.update_xaxes(automargin=True, tickfont=dict(size=11), row=1, col=2)
                dashboard_fig.update_yaxes(automargin=True, tickfont=dict(size=11), row=1, col=2)
                dashboard_fig.update_xaxes(automargin=True, tickfont=dict(size=11), row=2, col=1)
                dashboard_fig.update_yaxes(automargin=True, tickfont=dict(size=11), row=2, col=1)
                dashboard_fig.update_xaxes(automargin=True, tickfont=dict(size=11), row=2, col=2)
                dashboard_fig.update_yaxes(automargin=True, tickfont=dict(size=11), row=2, col=2)

                self._save_chart(dashboard_fig, chart_name("Dashboard"), saved_paths)
                generated_stems.add("dashboard")

            if best_cats and self._can_emit_chart("Category_Subplots", existing_chart_stems, generated_stems):
                fig_multi = make_subplots(
                    rows=2,
                    cols=2,
                    subplot_titles=[self._truncate_label(cat, 30) for cat in best_cats[:4]],
                )

                row_index = 1
                col_index = 1
                for cat_col in best_cats[:4]:
                    counts = self._top_category_counts(df, cat_col)
                    fig_multi.add_trace(
                        go.Bar(
                            x=counts.values,
                            y=counts.index,
                            orientation="h",
                            marker_color="#38bdf8",
                            showlegend=False,
                        ),
                        row=row_index,
                        col=col_index,
                    )
                    col_index += 1
                    if col_index > 2:
                        col_index = 1
                        row_index += 1

                fig_multi.update_layout(
                    height=820,
                    title="Category Comparison",
                    template="plotly_white",
                    showlegend=False,
                    margin=dict(l=90, r=42, t=90, b=60),
                )
                fig_multi.update_xaxes(automargin=True)
                fig_multi.update_yaxes(automargin=True)
                self._save_chart(fig_multi, chart_name("Category_Subplots"), saved_paths)
                generated_stems.add("category_subplots")

            if best_cats and num and self._can_emit_chart("Top_10_Category_Bar", existing_chart_stems, generated_stems):
                top_data = self._top_grouped_metric(df, best_cats[0], num)
                if not top_data.empty:
                    fig = px.bar(
                        top_data,
                        x=num,
                        y=best_cats[0],
                        orientation="h",
                        title=f"Top {len(top_data)} {best_cats[0].title()} by Total {num.title()}",
                        color=num,
                        color_continuous_scale="Blues",
                    )
                    fig.update_layout(
                        template="plotly_white",
                        showlegend=False,
                        coloraxis_showscale=False,
                        margin=dict(l=110, r=40, t=90, b=50),
                    )
                    self._save_chart(fig, chart_name("Top_10_Category_Bar"), saved_paths)
                    generated_stems.add("top_10_category_bar")

            if best_cats and num and self._can_emit_chart("Market_Composition_Treemap", existing_chart_stems, generated_stems):
                treemap_source = best_cats[0]
                treemap_data = (
                    df.groupby(treemap_source, dropna=False)[num]
                    .sum()
                    .sort_values(ascending=False)
                    .head(12)
                    .reset_index()
                )
                if not treemap_data.empty:
                    treemap_data[treemap_source] = treemap_data[treemap_source].astype(str).apply(self._truncate_label)
                    fig = px.treemap(
                        treemap_data,
                        path=[treemap_source],
                        values=num,
                        color=num,
                        color_continuous_scale="Tealgrn",
                        title=f"Market Composition Treemap by {treemap_source.title()}",
                    )
                    fig.update_traces(
                        texttemplate="%{label}<br>%{percentEntry:.1%}",
                        textfont_size=15,
                        hovertemplate=f"{treemap_source}: %{{label}}<br>{num}: %{{value:,.0f}}<extra></extra>",
                    )
                    fig.update_layout(
                        template="plotly_white",
                        margin=dict(l=25, r=25, t=90, b=25),
                        height=760,
                        coloraxis_showscale=False,
                    )
                    self._save_chart(fig, chart_name("Market_Composition_Treemap"), saved_paths)
                    generated_stems.add("market_composition_treemap")

            if len(num_cols) >= 2 and self._can_emit_chart("Numeric_Relationship_Scatter", existing_chart_stems, generated_stems):
                scatter_df = df[[num_cols[0], num_cols[1]]].copy().dropna().head(1500)
                if not scatter_df.empty:
                    fig = px.scatter(
                        scatter_df,
                        x=num_cols[0],
                        y=num_cols[1],
                        title=f"Numeric Relationship: {num_cols[0].title()} vs {num_cols[1].title()}",
                        opacity=0.55,
                        trendline="ols" if len(scatter_df) >= 30 else None,
                    )
                    fig.update_layout(
                        template="plotly_white",
                        showlegend=False,
                        margin=dict(l=80, r=40, t=90, b=70),
                        title_font=dict(size=22),
                    )
                    fig.update_xaxes(automargin=True, tickangle=-18)
                    fig.update_yaxes(automargin=True)
                    self._save_chart(fig, chart_name("Numeric_Relationship_Scatter"), saved_paths)
                    generated_stems.add("numeric_relationship_scatter")

            print(f"\nCharts Generated: {len(saved_paths)}")
            return saved_paths
        except Exception as error:
            print(f"[ERROR] Failed: {error}")
            return []
