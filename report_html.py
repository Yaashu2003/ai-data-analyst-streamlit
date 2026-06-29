import re
import difflib
from pathlib import Path
import json
from html import escape
from pbix_chart_viz import build_pbix_figure

BASE_DIR = Path(__file__).parent
REPORT_PATH = BASE_DIR / "analysis_report.txt"
OUTPUT_HTML = BASE_DIR / "interactive_analysis_report.html"


def chart_priority(chart):
    chart_type = str(chart.get("chart_type", "")).lower()
    score = 0
    if chart.get("data_available", True):
        score += 30
    if "line" in chart_type or "combo" in chart_type or "area" in chart_type:
        score += 18
    if "scatter" in chart_type:
        score += 16
    if "box" in chart_type or "histogram" in chart_type:
        score += 15
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


def is_reportable_chart(chart):
    if not chart or not chart.get("data_available", True):
        return False
    x_count = len(chart.get("x", []) or [])
    chart_type = str(chart.get("chart_type", "")).lower()
    if not x_count:
        return False
    if "scatter" in chart_type:
        return x_count >= 2
    if "histogram" in chart_type:
        return x_count >= 6
    if "pie" in chart_type or "donut" in chart_type:
        return x_count <= 16
    return True


def fallback_chart_reason(chart):
    title = chart.get("title", "Unknown Chart")
    dimension = chart.get("dimension") or "category"
    measure = chart.get("measure_used") or "value"
    if chart.get("data_available", True):
        return (
            f"{title} was selected because it keeps useful business context by comparing {measure} across {dimension}, "
            f"which makes it relevant for analysis, summary writing, and recommendations."
        )
    return ""


def _normalize_title_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def chart_topic(chart):
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
    if any(token in title_blob for token in ["brand"]):
        return "brand"
    if any(token in title_blob for token in ["category"]):
        return "category"
    if any(token in title_blob for token in ["city", "latitude", "longitude", "pincode", "geo"]):
        return "geography"
    if any(token in title_blob for token in ["trend", "time", "date", "month", "year", "day"]):
        return "trend"
    if any(token in title_blob for token in ["source", "platform", "channel"]):
        return "platform"
    if any(token in title_blob for token in ["price"]):
        return "price"
    return str(chart.get("chart_type", "other")).lower() or "other"


def select_diverse_reportable_charts(charts, limit=8, max_per_topic=2):
    selected = []
    topic_counts = {}
    seen_titles = set()
    for chart in sorted(charts, key=chart_priority, reverse=True):
        normalized_title = _normalize_title_key(chart.get("title"))
        if not normalized_title or normalized_title in seen_titles:
            continue
        topic = chart_topic(chart)
        if topic_counts.get(topic, 0) >= max_per_topic:
            continue
        selected.append(chart)
        seen_titles.add(normalized_title)
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
        if len(selected) >= limit:
            return selected

    if len(selected) < limit:
        for chart in sorted(charts, key=chart_priority, reverse=True):
            normalized_title = _normalize_title_key(chart.get("title"))
            if not normalized_title or normalized_title in seen_titles:
                continue
            selected.append(chart)
            seen_titles.add(normalized_title)
            if len(selected) >= limit:
                break
    return selected


def match_catalog_reason(chart, parsed_entries, used_indices):
    target = str(chart.get("title", "")).strip()
    target_key = _normalize_title_key(target)
    if not target_key:
        return None

    best_index = None
    best_score = 0.0
    target_tokens = set(re.findall(r"[a-z0-9]+", target.lower()))

    for index, entry in enumerate(parsed_entries):
        if index in used_indices:
            continue
        candidate = str(entry.get("title", "")).strip()
        candidate_key = _normalize_title_key(candidate)
        if not candidate_key:
            continue

        if candidate_key == target_key:
            best_index = index
            best_score = 1.0
            break

        candidate_tokens = set(re.findall(r"[a-z0-9]+", candidate.lower()))
        overlap = len(target_tokens & candidate_tokens) / max(len(target_tokens | candidate_tokens), 1)
        similarity = difflib.SequenceMatcher(None, target_key, candidate_key).ratio()
        score = max(overlap, similarity * 0.92)
        if score > best_score:
            best_index = index
            best_score = score

    if best_index is not None and best_score >= 0.55:
        used_indices.add(best_index)
        return parsed_entries[best_index]
    return None


def extract_chart_catalog_entries(section_text):
    if not section_text:
        return []

    entries = []
    block_pattern = re.compile(
        r"---CHART_(\d+)---\s*Title:\s*(.*?)\s*Explanation:\s*(.*?)(?=(?:---CHART_\d+---)|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    for _, title, explanation in block_pattern.findall(section_text):
        entries.append({
            "title": title.strip().strip("'").strip('"'),
            "reason": " ".join(explanation.strip().split()),
        })

    if entries:
        return entries

    markdown_chart_blocks = re.findall(
        r"\*\*Chart\s+[A-Za-z0-9]+\s*:\s*(.*?)\*\*\s*(.*?)(?=\n\s*\*\*Chart\s+[A-Za-z0-9]+\s*:|\Z)",
        section_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if markdown_chart_blocks:
        parsed_entries = []
        for raw_title, raw_block in markdown_chart_blocks:
            title = raw_title.strip().strip("'").strip('"')
            reasons = []
            for line in raw_block.splitlines():
                line = line.strip()
                if not line:
                    continue
                bullet_match = re.match(r"^\*\s+\*\*([^*]+)\*\*:\s*(.*)$", line)
                if bullet_match:
                    label = bullet_match.group(1).strip().lower()
                    detail = bullet_match.group(2).strip()
                    if label in {"analytical summary", "exact numerical insights", "key takeaway", "representation"} and detail:
                        reasons.append(detail)
                    continue
                cleaned = re.sub(r"^\*\s*", "", line).strip()
                if cleaned:
                    reasons.append(cleaned)
            parsed_entries.append({
                "title": title,
                "reason": " ".join(reasons).strip(),
            })
        if parsed_entries:
            return parsed_entries

    gemini_entries = []
    current_title = None
    current_parts = []

    def flush_gemini_entry():
        nonlocal current_title, current_parts
        if current_title:
            cleaned_parts = [part.strip() for part in current_parts if part.strip()]
            gemini_entries.append({
                "title": current_title,
                "reason": " ".join(cleaned_parts).strip(),
            })
        current_title = None
        current_parts = []

    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        title_match = re.search(r"Chart Title:\s*(.+?)\*{0,2}$", line, re.IGNORECASE)
        if title_match:
            flush_gemini_entry()
            current_title = re.sub(r"[*`]+", "", title_match.group(1)).strip().strip("'").strip('"')
            continue

        if current_title:
            detail_match = re.search(r"\*\*([^*]+)\*\*:\s*(.*)", line)
            if detail_match:
                label = detail_match.group(1).strip().lower()
                detail = detail_match.group(2).strip()
                if label in {"analytical summary", "exact numerical insights", "key takeaway"}:
                    current_parts.append(detail)
                elif label == "what it represents" and not current_parts:
                    current_parts.append(detail)
                continue

            cleaned_line = re.sub(r"^[-*]\s*", "", line).strip()
            if cleaned_line and not cleaned_line.lower().startswith("chart title:"):
                current_parts.append(cleaned_line)

    flush_gemini_entry()
    if gemini_entries:
        return gemini_entries

    current_title = None
    current_reason = []

    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        title_match = re.match(
            r"^(?:[-*]|\d+\.)\s*\*{0,2}(.*?)\*{0,2}\s*:\s*(.*)$",
            line,
        )
        if title_match:
            if current_title:
                entries.append({
                    "title": current_title,
                    "reason": " ".join(current_reason).strip(),
                })
            current_title = title_match.group(1).strip().strip("'").strip('"')
            current_reason = [title_match.group(2).strip()]
            continue

        if current_title:
            current_reason.append(line)

    if current_title:
        entries.append({
            "title": current_title,
            "reason": " ".join(current_reason).strip(),
        })

    return entries

# -------------------------
# READ REPORT
# -------------------------
def read_report():
    if not REPORT_PATH.exists():
        return ""
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        return f.read()

# -------------------------
# EXTRACT MAIN REPORT
# -------------------------
def extract_main_report(content):
    match = re.search(r'Insight #2:(.*?)-----------------------------------------', content, re.DOTALL)
    if match: return match.group(1).strip()
    return content

# -------------------------
# EXTRACT SUPPORTING CHARTS
# -------------------------
def extract_supporting_charts(content):
    match = re.search(r'#### 5\. Supporting Charts(.*?)####', content, re.DOTALL)
    if not match: return []
    block = match.group(1)
    lines = block.split("\n")
    charts = []
    for line in lines:
        if "Chart Title:" in line:
            charts.append(line.replace("Chart Title:", "").replace("*", "").strip())
    return charts

# -------------------------
# BUILD PLOTLY CHART HTML
# -------------------------
def build_chart_html(chart):
    if not chart:
        return ""

    if not chart.get("data_available", True):
        field_rows = chart.get("field_rows", [])[:8]
        field_lines = "".join(
            f"<li><strong>{row.get('role')}</strong>: "
            f"{(row.get('table') + '.') if row.get('table') else ''}{row.get('field')}</li>"
            for row in field_rows
        )
        return f"""
        <div class='chart-metadata-card'>
            <div class='chart-metadata-note'>{chart.get('data_note', 'Visual definition extracted without embedded numeric data.')}</div>
            <div><strong>Page:</strong> {chart.get('page', 'Unknown')}</div>
            <div><strong>Visual type:</strong> {chart.get('chart_type', 'unknown')}</div>
            <div><strong>Required tables:</strong> {", ".join(chart.get('required_tables', []) or ['Not detected'])}</div>
            <div><strong>Measures:</strong> {", ".join(chart.get('measures', []) or ['Not detected'])}</div>
            <div class='chart-metadata-fields'>
                <strong>Field mapping</strong>
                <ul>{field_lines or '<li>Not detected</li>'}</ul>
            </div>
        </div>
        """

    fig = build_pbix_figure(chart)
    if fig is None:
        return ""
    fig.update_layout(autosize=True, width=None)
    # Chart fragments are placed inside lazy iframes that load Plotly on demand.
    # Do not include Plotly in the fragment itself.
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config={"responsive": True, "displayModeBar": False},
        default_width="100%",
        default_height="500px",
    )


def build_chart_iframe(chart):
    chart_html = build_chart_html(chart)
    if not chart_html:
        return "<div class='chart-placeholder'>Chart could not be rendered.</div>"
    if not chart.get("data_available", True):
        return chart_html

    chart_document = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <style>
            html, body {{ margin: 0; padding: 0; background: #fff; overflow: hidden; width: 100%; }}
            body {{ font-family: Arial, sans-serif; }}
            .plotly-graph-div,
            .js-plotly-plot,
            .svg-container {{
                width: 100% !important;
                max-width: 100% !important;
            }}
        </style>
    </head>
    <body>
        {chart_html}
        <script>
            function resizeCharts() {{
                if (!window.Plotly) return;
                document.querySelectorAll('.js-plotly-plot, .plotly-graph-div').forEach(function (chart) {{
                    chart.style.width = '100%';
                    window.Plotly.Plots.resize(chart);
                }});
            }}
            window.addEventListener('load', resizeCharts);
            window.addEventListener('resize', resizeCharts);
            setTimeout(resizeCharts, 150);
        </script>
    </body>
    </html>
    """.strip()
    return (
        "<iframe class='chart-frame' loading='lazy' "
        f"srcdoc=\"{escape(chart_document, quote=True)}\"></iframe>"
    )

# -------------------------
# TEXT FORMATTING (From CSV app logic)
# -------------------------
def _apply_markdown_to_html(text: str) -> str:
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text, flags=re.DOTALL)
    text = text.replace('\r\n', '<br>').replace('\n', '<br>')
    return text

def _format_content_to_html(content_text: str, is_recommendation: bool) -> str:
    content_text = content_text.strip()
    
    if content_text.startswith('-') or content_text.startswith('*'):
        items = re.findall(r'^\s*[-*]+\s*(.*?)(?=\r?\n\s*[-*]+|\Z)', content_text, re.DOTALL | re.MULTILINE)
        list_html = ""
        for item_content in items:
            item_content = _apply_markdown_to_html(item_content.strip())
            list_html += f"<li>{item_content}</li>"
        return f"<ul class='key-points-list'>{list_html}</ul>"
        
    elif re.match(r'^\d+\.', content_text) or is_recommendation:
        items = re.findall(r'(?:\d+\.\s*)(.*?)(?=\r?\n\d+\.|\Z)', content_text, re.DOTALL)
        list_html = ""
        for item_content in items:
            item_content = _apply_markdown_to_html(item_content.strip())
            list_html += f"<li>{item_content}</li>"
        if not items:
            return f"<div class='key-summary-text'>{_apply_markdown_to_html(content_text)}</div>"
        return f"<ul class='key-points-list'>{list_html}</ul>"
    else:
        content_html = _apply_markdown_to_html(content_text)
        return f"<div class='key-summary-text'>{content_html}</div>"

# -------------------------
# GENERATE HTML REPORT
# -------------------------
def generate_html(charts_data=None, report_text=None, output_path=None):
    if charts_data is None:
        charts_data = []
    reportable_charts = [chart for chart in charts_data if is_reportable_chart(chart)]

    content = report_text if report_text is not None else read_report()
    if not content:
        print("[ERROR] No report found")
        return ""
    
    main_text = extract_main_report(content)
    
    # 1. Parse Sections dynamically just like report.py
    section_pattern = re.compile(
        r'(#{3,4}\s*(\d+)\.\s*(.*?))(?:\r?\n)(.*?)(?=\r?\n#{3,4}|\Z)', 
        re.DOTALL | re.IGNORECASE
    )
    
    parsed_sections = {}
    matches = section_pattern.findall(main_text)
    for header_raw, number_str, _, content_raw in matches:
        try:
            key = int(number_str.strip())
            parsed_sections[key] = (header_raw.strip(), content_raw.strip())
        except ValueError:
            pass

    # Build Header Horizontal Row
    header_html = "<div class='horizontal-summary-row'>"
    if 1 in parsed_sections:
        header_md, content_text = parsed_sections[1]
        header_html += f'''
        <div class='summary-block summary-performance'>
            <h4>Overall Performance Summary</h4>
            {_format_content_to_html(content_text, is_recommendation=False)}
        </div>
        '''
    if 4 in parsed_sections:
        header_md, content_text = parsed_sections[4]
        header_html += f'''
        <div class='summary-block summary-recommendations'>
            <h4>Data-Driven Recommendations</h4>
            {_format_content_to_html(content_text, is_recommendation=True)}
        </div>
        '''
    header_html += "</div>"
    
    # Build Body Key Insights
    body_html = ""
    
    if not parsed_sections:
        # Fallback if LLM didn't use the expected "#### 1. ..." headers
        body_html += f"<h4 class='body-insights-header'>Full Analysis Report</h4>\n"
        body_html += _format_content_to_html(main_text, is_recommendation=False)
    else:
        if 2 in parsed_sections:
            header_md, content_text = parsed_sections[2]
            body_html += f"<h4 class='body-insights-header'>Key Insights & Drivers</h4>\n"
            body_html += _format_content_to_html(content_text, is_recommendation=False)
            
        if 3 in parsed_sections:
            header_md, content_text = parsed_sections[3]
            body_html += f"<h4 class='body-insights-header'>Risks & Issues</h4>\n"
            body_html += _format_content_to_html(content_text, is_recommendation=False)

    # 3. Chart Catalog
    chart_catalog_text = parsed_sections.get(0, ("", ""))[1] or parsed_sections.get(5, ("", ""))[1]
    parsed_charts = extract_chart_catalog_entries(chart_catalog_text)

    # Build sequential-chart-list matching the layout of report.py
    chart_display_content = "<div class='charts-header'><h4>Chart Catalog (Sequentially Explained)</h4></div>"
    chart_display_content += "<div class='sequential-chart-list'>"
    
    if reportable_charts:
        minimum_chart_count = min(8, len(reportable_charts))
        target_chart_count = min(
            max(len(parsed_charts), minimum_chart_count),
            len(reportable_charts),
        )
    else:
        target_chart_count = 0
    selected_charts = select_diverse_reportable_charts(reportable_charts, limit=target_chart_count)

    matched_chart_entries = []
    multiple_reports = len({str(chart.get("report", "")).strip() for chart in selected_charts if chart.get("report")}) > 1
    used_reason_indices = set()
    for chart in selected_charts:
        matched_reason = match_catalog_reason(chart, parsed_charts, used_reason_indices)
        display_title = chart.get("title", "Unknown Chart")
        if multiple_reports and chart.get("report"):
            display_title = f"{chart.get('report')} — {display_title}"
        chart_info = {
            "title": display_title,
            "reason": (
                matched_reason.get("reason")
                if matched_reason and matched_reason.get("reason")
                else fallback_chart_reason(chart)
            ),
        }
        matched_chart_entries.append((chart_info, chart))

    # Draw them in pairs!
    for i in range(0, len(matched_chart_entries), 2):
        chart_display_content += "<div class='chart-pair'>"
        
        c1_info, c1_data = matched_chart_entries[i]
        c1_html = build_chart_iframe(c1_data)
        
        chart_display_content += f"""
        <div class="chart-item">
            <h5>{c1_info["title"]}</h5>
            <div class="chart-visual-wrapper">
                {c1_html}
            </div>
            <div class="chart-explanation">
                <p>{_apply_markdown_to_html(c1_info["reason"])}</p>
            </div>
        </div>
        """
        
        # Chart 2
        if i + 1 < len(matched_chart_entries):
            c2_info, c2_data = matched_chart_entries[i+1]
            c2_html = build_chart_iframe(c2_data)
            chart_display_content += f"""
            <div class="chart-item">
                <h5>{c2_info["title"]}</h5>
                <div class="chart-visual-wrapper">
                    {c2_html}
                </div>
                <div class="chart-explanation">
                    <p>{_apply_markdown_to_html(c2_info["reason"])}</p>
                </div>
            </div>
            """
            
        chart_display_content += "</div>" # close chart-pair
    
    chart_display_content += "</div>" # close sequential-chart-list

    if not reportable_charts or not matched_chart_entries:
        chart_display_content = (
            "<div class='charts-header'><h4>Chart Catalog (Sequentially Explained)</h4></div>"
            "<div class='sequential-chart-list'><div class='chart-item'>"
            "<div class='chart-explanation'><p>No usable numeric BI dashboard charts were available for this report run.</p></div>"
            "</div></div>"
        )

    # Check for Generated JSON charts
    json_match = re.search(r'\[\s*\{.*"chart_type".*\}\s*\]', content, re.DOTALL)
    if json_match:
        try:
            gen_charts = json.loads(json_match.group(0))
            if gen_charts:
                chart_display_content += "<div class='charts-header'><h4>Newly Generated Charts</h4></div>"
                chart_display_content += "<div class='sequential-chart-list'><div class='chart-pair'>"
                for idx, gc in enumerate(gen_charts):
                    if idx > 0 and idx % 2 == 0:
                        chart_display_content += "</div><div class='chart-pair'>"
                    html_gc = build_chart_iframe(gc)
                    title_gc = gc.get("title", "New Variable Chart")
                    chart_display_content += f"""
                    <div class="chart-item">
                        <h5>{title_gc}</h5>
                        <div class="chart-visual-wrapper">{html_gc}</div>
                        <div class="chart-explanation"><p>Dynamically generated based on insights.</p></div>
                    </div>
                    """
                chart_display_content += "</div></div>"
        except Exception:
            pass

    css_styles = """
        /* --- HORIZONTAL EXECUTIVE SUMMARY STYLES --- */
        .horizontal-summary-row {
            display: flex; gap: 20px; margin-bottom: 25px;
            border: 2px solid #3f51b5; padding: 15px;
            border-radius: 10px; background-color: #e8eaf6; 
        }
        .summary-block { flex: 1; padding: 0; }
        .summary-block h4 {
            margin-top: 0; color: #3f51b5; border-bottom: 1px solid #c5cae9;
            padding-bottom: 5px; font-size: 1.15em; font-weight: bold;
        }
        .key-points-list { list-style-type: none; padding-left: 0; margin: 0; }
        .key-points-list li { margin-bottom: 10px; line-height: 1.4; padding-left: 25px; text-indent: -25px; font-size: 0.95em; color: #333; }
        .key-points-list li::before { content: "•"; color: #ff9800; font-weight: bold; display: inline-block; width: 25px; }
        .key-summary-text { line-height: 1.6; color: #333; font-size: 1em; font-weight: 500;}
        .body-insights-header { color: #3f51b5 !important; border-bottom: 2px solid #3f51b533 !important; padding-bottom: 5px !important; margin-top: 25px !important; font-size: 1.2em; }
        
        /* Chart Catalog side-by-side elements */
        .charts-header { margin-top: 30px; margin-bottom: 15px; border-bottom: 2px solid #3f51b5; padding-bottom: 5px; }
        .charts-header h4 { color: #3f51b5 !important; font-size: 1.2em; margin-bottom: 0; }
        .sequential-chart-list { display: flex; flex-direction: column; gap: 40px; margin-top: 20px; padding: 15px; background-color: #fcfcfc; border: 1px solid #eee; border-radius: 8px; box-sizing: border-box; width: 100%;}
        .chart-pair {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 620px), 1fr));
            gap: 20px;
            width: 100%;
            box-sizing: border-box;
        }
        .chart-item { flex: 1; border: 1px solid #e0e0e0; padding: 20px; border-radius: 8px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.05); display: flex; flex-direction: column; box-sizing: border-box; overflow: hidden; }
        .chart-item h5 { margin: 0 0 15px 0; color: #2e7d32; border-bottom: 1px dashed #eee; padding-bottom: 5px; font-size: 1.1em; }
        .chart-explanation { width: 100%; }
        .chart-explanation p { font-size: 0.95em; line-height: 1.5; margin-top: 5px; color: #333; }
        .chart-visual-wrapper {
            width: 100%;
            text-align: center;
            margin-bottom: 15px;
            overflow: hidden;
            display: block;
            padding-bottom: 0;
        }
        .chart-visual-wrapper .plotly-graph-div,
        .chart-visual-wrapper .js-plotly-plot {
            width: 100% !important;
            max-width: 100% !important;
            min-width: 0 !important;
            margin: 0 auto;
        }
        .chart-visual-wrapper .plotly-graph-div .svg-container,
        .chart-visual-wrapper .js-plotly-plot .svg-container {
            width: 100% !important;
            max-width: 100% !important;
            min-width: 0 !important;
        }
        .chart-frame {
            width: 100%;
            min-height: 540px;
            height: 56vh;
            max-height: 680px;
            border: 0;
            display: block;
            background: #fff;
        }
        .chart-placeholder { color: #cc0000; border: 1px dashed #cc0000; padding: 10px; text-align: center; }
        .chart-metadata-card { width: 100%; text-align: left; border: 1px solid #d7e3dc; border-radius: 12px; background: #f8fbff; padding: 16px; color: #213547; box-sizing: border-box; }
        .chart-metadata-note { margin-bottom: 10px; color: #516173; line-height: 1.5; }
        .chart-metadata-fields ul { margin: 8px 0 0 18px; padding: 0; }
        .chart-metadata-fields li { margin-bottom: 6px; }
        
        /* Tab Styles */
        .tab { overflow: hidden; border-bottom: 1px solid #ccc; background-color: #e9e9e9; display: flex; border-top-left-radius: 8px; border-top-right-radius: 8px;}
        .tab button { background-color: #ccc; font-weight: bold; border: none; outline: none; padding: 14px 16px; font-size: 15px; flex-grow: 1; text-align: left; }
    """

    html = f'''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <!-- AI_DATA_ANALYST_REPORT_HTML_VERSION=lazy_chart_iframe_v2 -->
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Intelligent Data Analysis Report</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f7f6; }}
            .container {{ width: 95%; max-width: 1200px; margin: 20px auto; background: #fff; box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px; }}
            h1 {{ padding: 20px; margin: 0; background-color: #3f51b5; color: white; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
            .report-section {{ padding: 30px; width: 100%; box-sizing: border-box; }}
            {css_styles}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Intelligent Data Analysis Report</h1>
            <div class="tab">
                <button class="active">⭐ BI Executive Report & Visual Analysis</button>
            </div>
            <div class="report-section">
                <h3>⭐ Executive Summary</h3>
                {header_html}
                {body_html}
                {chart_display_content}
            </div>
        </div>
        <script>
            (function () {{
                function getSelectedText() {{
                    var selection = window.getSelection ? window.getSelection() : null;
                    return selection ? String(selection.toString()).trim() : "";
                }}
                function sendSelection(kind, event) {{
                    var text = getSelectedText();
                    if (!text) return;
                    if (kind === "report-selection-context" && event) event.preventDefault();
                    window.parent.postMessage({{ type: kind, text: text }}, window.location.origin);
                }}
                document.addEventListener("mouseup", function (event) {{
                    sendSelection("report-selection", event);
                }});
                document.addEventListener("contextmenu", function (event) {{
                    sendSelection("report-selection-context", event);
                }});
            }})();
        </script>
    </body>
    </html>
    '''

    target_path = Path(output_path) if output_path else OUTPUT_HTML
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("[OK] HTML report generated")
    return html

if __name__ == "__main__":
    generate_html([])
