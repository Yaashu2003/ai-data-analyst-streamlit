import gemini_patch
import os
import re
from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")


def _chart_priority(chart):
    chart_type = str(chart.get("chart_type", "")).lower()
    score = 0
    if chart.get("data_available", True):
        score += 30
    if "line" in chart_type or "combo" in chart_type:
        score += 18
    if "scatter" in chart_type:
        score += 16
    if "bar" in chart_type or "column" in chart_type or "ribbon" in chart_type:
        score += 14
    if "treemap" in chart_type or "funnel" in chart_type or "donut" in chart_type or "pie" in chart_type:
        score += 10
    if "card" in chart_type or "gauge" in chart_type:
        score -= 4
    if chart.get("series"):
        score += 4
    if chart.get("dimension"):
        score += 3
    x_count = len(chart.get("x", []) or [])
    if x_count > 60:
        score -= 12
    elif x_count > 20:
        score -= 6
    return score


def _normalized_chart_title(title):
    return re.sub(r"[^a-z0-9]+", "", str(title or "").lower())


def _chart_topic(chart):
    title_blob = " ".join(
        [
            str(chart.get("title", "")),
            str(chart.get("dimension", "")),
            str(chart.get("measure_used", "")),
            str(chart.get("chart_type", "")),
        ]
    ).lower()
    if any(token in title_blob for token in ["availability", "stock", "oos"]):
        return "availability"
    if "discount" in title_blob:
        return "discount"
    if "brand" in title_blob:
        return "brand"
    if "category" in title_blob:
        return "category"
    if any(token in title_blob for token in ["city", "latitude", "longitude", "pincode", "geo"]):
        return "geography"
    if any(token in title_blob for token in ["trend", "time", "date", "month", "year", "day"]):
        return "trend"
    if any(token in title_blob for token in ["source", "platform", "channel"]):
        return "platform"
    if "price" in title_blob:
        return "price"
    return str(chart.get("chart_type", "other")).lower() or "other"


def _select_diverse_charts(charts, limit=8, max_per_topic=2):
    selected = []
    topic_counts = {}
    seen_titles = set()
    for chart in sorted(charts, key=_chart_priority, reverse=True):
        normalized_title = _normalized_chart_title(chart.get("title"))
        if not normalized_title or normalized_title in seen_titles:
            continue
        topic = _chart_topic(chart)
        if topic_counts.get(topic, 0) >= max_per_topic:
            continue
        selected.append(chart)
        seen_titles.add(normalized_title)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        if len(selected) >= limit:
            return selected

    if len(selected) < limit:
        for chart in sorted(charts, key=_chart_priority, reverse=True):
            normalized_title = _normalized_chart_title(chart.get("title"))
            if not normalized_title or normalized_title in seen_titles:
                continue
            selected.append(chart)
            seen_titles.add(normalized_title)
            if len(selected) >= limit:
                break
    return selected


def _select_analytic_charts(charts, limit=6):
    return _select_diverse_charts(charts, limit=limit, max_per_topic=2)


def _is_reportable_chart(chart):
    if not chart or not chart.get("data_available", True):
        return False

    x_count = len(chart.get("x", []) or [])
    chart_type = str(chart.get("chart_type", "")).lower()
    if not x_count:
        return False
    if "scatter" in chart_type:
        return x_count >= 2
    if "pie" in chart_type or "donut" in chart_type:
        return x_count <= 16
    return True


def _prepare_report_charts(charts, minimum_count=7):
    data_charts = [chart for chart in charts if _is_reportable_chart(chart)]
    limit = max(minimum_count, 8) if minimum_count else 8
    ordered = _select_diverse_charts(data_charts, limit=limit, max_per_topic=2)
    return ordered


def _format_metric(value):
    try:
        numeric = float(value)
    except Exception:
        return str(value)

    abs_value = abs(numeric)
    if abs_value >= 1_000_000:
        return f"{numeric / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{numeric / 1_000:.1f}K"
    if abs_value >= 100:
        return f"{numeric:.0f}"
    if abs_value >= 10:
        return f"{numeric:.1f}"
    return f"{numeric:.2f}"


def _build_numeric_insight(chart):
    if not chart.get("data_available", True):
        return ""

    x_values = [str(item) for item in chart.get("x", [])]
    if chart.get("series"):
        series_totals = []
        for series in chart.get("series", []):
            try:
                total = sum(float(value) for value in series.get("y", []))
                series_totals.append((series.get("name", "Series"), total))
            except Exception:
                continue
        if series_totals:
            top_name, top_total = max(series_totals, key=lambda item: item[1])
            return f"The strongest series is {top_name} with an aggregate value of {_format_metric(top_total)}."
        return ""

    y_values = chart.get("y", [])
    if not x_values or not y_values:
        return ""

    try:
        numeric_y = [float(value) for value in y_values]
    except Exception:
        return ""

    top_index = max(range(len(numeric_y)), key=lambda idx: numeric_y[idx])
    bottom_index = min(range(len(numeric_y)), key=lambda idx: numeric_y[idx])
    return (
        f"The highest point is {x_values[top_index]} at {_format_metric(numeric_y[top_index])}, "
        f"while the lowest is {x_values[bottom_index]} at {_format_metric(numeric_y[bottom_index])}."
    )


def _build_chart_reason(chart):
    title = chart.get("title", "Chart")
    dimension = chart.get("dimension") or "category"
    measure = chart.get("measure_used") or "value"
    chart_type = chart.get("chart_type", "visual")
    if chart.get("data_available", True):
        if chart.get("series"):
            series_names = ", ".join(series.get("name", "Series") for series in chart.get("series", [])[:3])
            base = (
                f"{title} compares {measure} across {dimension} with segmented series ({series_names}), "
                f"which makes it useful for identifying performance gaps, mix shifts, and action priorities."
            )
            numeric_note = _build_numeric_insight(chart)
            return f"{base} {numeric_note}".strip()
        base = (
            f"{title} shows how {measure} changes by {dimension}, which makes it a strong chart for "
            f"performance review, driver analysis, and recommendation building."
        )
        numeric_note = _build_numeric_insight(chart)
        return f"{base} {numeric_note}".strip()
    return (
        f"{title} is structurally important in the report layout, but its numeric values were not available "
        f"for direct calculation in this run."
    )


def _build_local_pbix_report(charts):
    selected = _prepare_report_charts(charts, minimum_count=7)[:8]
    data_charts = list(selected)

    if data_charts:
        overview = (
            f"The Power BI report produced {len(data_charts)} analytical chart(s) with usable numeric data in this run. "
            f"The strongest visuals focus on category, source, geography, and pricing relationships, which makes the report "
            f"useful for performance review, variance diagnosis, and action planning."
        )
        drivers = (
            f"The most relevant drivers come from charts that compare {data_charts[0].get('measure_used') or 'value'} "
            f"by {data_charts[0].get('dimension') or 'category'} and related segment views across the same dataset."
        )
        risks = (
            "The main risk is concentration or imbalance inside the highest-variance segments surfaced by the strongest reportable charts, "
            "so action should focus on those measurable gaps first."
        )
        recommendations = (
            "1. Prioritize the top-performing and underperforming segments surfaced by the strongest comparative charts.\n"
            "2. Use the pricing and mix visuals to pinpoint where value concentration or operational imbalance appears.\n"
            "3. Review metadata-only visuals separately before using them in business decisions."
        )
    else:
        overview = (
            "The report did not produce enough usable numeric visuals to support a reliable Power BI summary in this run."
        )
        drivers = "Only charts with direct numeric outputs are included in the report, so there are not enough measurable drivers yet."
        risks = "A thin chart set can distort the narrative, so low-quality or non-numeric visuals are intentionally excluded."
        recommendations = (
            "1. Re-run extraction after recovering more reportable charts.\n"
            "2. Prefer recovered analytical charts over layout-only visuals.\n"
            "3. Use supplemental charts generated from the recovered table data when the original PBIX visuals are too sparse."
        )

    chart_catalog_lines = []
    for index, chart in enumerate(selected, 1):
        chart_catalog_lines.append(
            f"---CHART_{index}---\n"
            f"Title: {chart.get('title', 'Unknown Chart')}\n"
            f"Explanation: {_build_chart_reason(chart)}"
        )

    return f"""
-----------------------------------------
Insight #1:
(skip)
-----------------------------------------

Insight #2:

#### 0. Chart Catalog
{chr(10).join(chart_catalog_lines)}

#### 1. Overall Performance Summary
{overview}

#### 2. Key Insights & Drivers
- {drivers}
- The selected analytical charts are better suited for recommendations than raw KPI cards because they preserve category and segment context.

#### 3. Risks / Issues
- {risks}

#### 4. Recommendations
{recommendations}

#### 6. Generated Charts
```json
[]
```

-----------------------------------------
""".strip()


# -------------------------
# BUILD CONTEXT FROM CHARTS (UPGRADED)
# -------------------------
def build_context(charts):

    context = []

    for c in _prepare_report_charts(charts, minimum_count=7):
        title = c.get("title", "Unknown")
        chart_type = c.get("chart_type", c.get("type", "unknown"))
        page = c.get("page", "Unknown")
        data_available = c.get("data_available", True)

        # Extract raw numbers for the LLM to analyze
        data_sample = ""
        if data_available and "series" in c:
            for s in c["series"]:
                y_sample = s.get("y", [])[:12]
                if y_sample:
                    data_sample += f"\n- Series '{s.get('name', 'N/A')}': {y_sample}"
        elif data_available and "y" in c:
            y_sample = c.get("y", [])[:12]
            if y_sample:
                data_sample += f"\n- Values: {y_sample}"
                
        x_sample = c.get("x", [])[:12] if data_available else []
        if x_sample:
            data_sample += f"\n- Categories (X-Axis): {x_sample}"

        metadata_note = ""
        data_sample_text = data_sample or "\n- No numeric sample available from the PBIX file."

        context.append(f"""
Chart Title: {title}
Chart Type: {chart_type}
Page: {page}
Data Availability: {"Embedded chart data available" if data_available else "Layout only / no embedded chart data"}
Data Sample:{data_sample_text}
{metadata_note}

Interpretation Hint:
- Extract absolute numbers, maximums, minimums, and compute clear percentages.
- If numeric chart data is unavailable, do NOT invent numbers. Explain the visual intent and clearly say the PBIX does not embed the source data.
- Do NOT just vaguely describe it. Give a strict, data-backed analytical summary.
- Detect patterns, anomalies, and exact statistical differences.
""")

    return "\n".join(context)


# -------------------------
# GENERATE REPORT (FIXED)
# -------------------------
def generate_pbix_report(charts):
    if not GEMINI_API_KEY:
        print("Gemini API key not configured. Using local PBIX report synthesis.")
        return _build_local_pbix_report(charts)

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        print(f"Gemini PBIX client initialization failed: {exc}. Using local PBIX report synthesis.")
        return _build_local_pbix_report(charts)

    context = build_context(charts)

    # ðŸ”¥ FULL PROMPT (THIS WAS MISSING)
    prompt = f"""

You are a senior business intelligence analyst.

You are given Power BI dashboard chart descriptions.

{context}

Perform a full business analysis.
Keep the writing concise, analytical, and numeric. Avoid bloated prose.
Use a broad mix of charts when available. Do not focus only on availability/inventory if the context also includes brands, categories, pricing, discounts, cities, or trend charts.
Limit availability/inventory-heavy chart discussion to at most 2 chart-catalog entries when other topics are available.

-----------------------------------------
Insight #1:
(skip)
-----------------------------------------

Insight #2:

#### 0. Chart Catalog
For EACH chart:
- What it represents
- Analytical Summary
- Exact Numerical Insights (cite the numbers from the Data Sample)
- Key takeaway
Aim to cover a diverse set of chart topics such as trends, brands, categories, price, discount, platform/source, and geography when those charts are present.

#### 1. Overall Performance Summary
- Overall business condition
- Growth / decline signals

#### 2. Key Insights & Drivers
- 5 strong insights
- Cross-chart reasoning

#### 3. Risks / Issues
- Identify weak areas

#### 4. Recommendations
- Actionable strategies

#### 6. Generated Charts (ONLY IF NEEDED)
ONLY generate new charts IF the existing charts are insufficient.

Return the generated charts as a JSON list wrapped in ```json ... ``` at the VERY END.
```json
[
  {{
    "chart_type": "bar | line",
    "title": "Chart Title",
    "x": [...],
    "y": [...]
  }}
]
```
If not needed:
```json
[]
```

-----------------------------------------

IMPORTANT RULES:

- DO NOT generate charts unnecessarily
- Prefer existing charts first
- Generated charts must directly support insights
- Do NOT invent random data
- Keep charts simple and meaningful
"""

    # ðŸ”¥ CORRECT API CALL WITH RETRY
    max_retries = 3
    import time
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            if response and getattr(response, "text", None):
                return response.text
            print("Gemini returned an empty PBIX report response. Falling back to local report synthesis.")
            return _build_local_pbix_report(charts)
        except Exception as e:
            err_str = str(e).upper()
            if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 3
                    print(f"API busy/unavailable ({e}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Gemini PBIX report generation failed after retries: {e}")
                    return _build_local_pbix_report(charts)
            else:
                print(f"Gemini PBIX report generation failed: {e}")
                return _build_local_pbix_report(charts)



# -------------------------
# SAVE REPORT
# -------------------------
def save_report(report_text):
    if not isinstance(report_text, str) or not report_text.strip():
        report_text = _build_local_pbix_report([])

    with open("analysis_report.txt", "w", encoding="utf-8") as f:
        f.write(report_text)

    print("[OK] Report saved")
