import re
import os
import base64
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from chart_assets import collect_canonical_chart_assets, canonical_asset_filenames


# --- CRITICAL CONFIGURATION FIX: USE PATHLIB TO ENSURE CORRECT RELATIVE PATHS ---
# 1. Determine the directory where this script resides (e.g., 'fin').
BASE_DIR = Path(__file__).parent


# 2. Set all file paths relative to the script's directory.
CHART_DIRECTORY = BASE_DIR / "charts"
CHARTS_HTML_DIRECTORY = BASE_DIR / "charts_html"
ANALYSIS_REPORT_PATH = BASE_DIR / "analysis_report.txt"
HTML_OUTPUT_FILE = BASE_DIR / "interactive_analysis_report.html"


# NOTE: The CHART_DIRECTORY is now a Path object, which is handled correctly below.
# ----------------------------------------------------------------------------------



def run_file_system_diagnostic(chart_dir: Path):
    """
    Dynamically scans the chart directory and returns a list of all 
    PNG, JPG, and JPEG filenames found.
    """
    found_files = []


    # Check if the path exists and is a directory
    if not chart_dir.is_dir():
        print(f" CRITICAL PATH ERROR: Chart directory NOT FOUND at: {chart_dir}")
        print("Please check the 'CHART_DIRECTORY' variable in the script.")
        return []


    # Search for common image extensions
    for ext in ['.png', '.jpg', '.jpeg']:
        found_files.extend([str(f.name) for f in chart_dir.glob(f'*{ext}')])
    
    unique_files = sorted(list(set(found_files)))
    print(f" Found {len(unique_files)} charts in directory: {chart_dir}")
    return unique_files


def discover_chart_assets(chart_dir: Path, html_dir: Path) -> List[str]:
    """Return canonical chart filenames shared across VLM and report assembly."""
    assets = collect_canonical_chart_assets(charts_dir=chart_dir, html_dir=html_dir)
    discovered = canonical_asset_filenames(assets)
    print(f"Found {len(discovered)} total canonical chart assets after merging PNG and HTML charts.")
    return discovered


def extract_insights_and_charts(report_path: Path):
    """
    Parses the analysis report to extract all insights and assigns the new titles.
    
    MODIFICATION: This function now skips Insight #1 (Textual Analysis).
    """
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: Report file not found at {report_path}")
        return []


    insights = []
    
    # 1. Robust Regex to find ALL Insight blocks
    insight_blocks = re.findall(
        r'Insight #(\d+):(?:\r?\n)(.*?)\r?\n-----------------------------------------',
        content, 
        re.DOTALL
    )
    
    # 2. Process the extracted blocks
    for number_str, block_content_raw in insight_blocks:
        number = int(number_str)
        block_content = block_content_raw.strip()
        
        # --- MODIFICATION START: Skip Insight #1 ---
        if number == 1:
            # Skip Textual Insights Analysis
            continue
        # --- MODIFICATION END ---
        
        title = f"Insight #{number_str}"
        is_vlm_report = False


        if number == 2:
            title = "⭐ Gemini VLM Executive Report & Visual Analysis"
            is_vlm_report = True
            
        insights.append({
            'id': f'insight-{number_str}',
            'title': title,
            'content': block_content,
            'is_vlm_report': is_vlm_report
        })
        
    return insights


def extract_specialized_fallback(report_path: Path) -> str:
    """Extract Insight #1 so the HTML report can recover summary sections when VLM output is incomplete."""
    try:
        content = report_path.read_text(encoding="utf-8")
    except Exception:
        return ""

    match = re.search(
        r'Insight #1:(?:\r?\n)(.*?)\r?\n-----------------------------------------',
        content,
        re.DOTALL,
    )
    if not match:
        return ""

    specialized_text = match.group(1).strip()
    specialized_text = re.sub(r'^\[SPECIALIZED INSIGHTS\]\s*', '', specialized_text).strip()
    specialized_text = specialized_text.replace("--- Specialized Analysis Results (Gemini) ---", "").strip()
    return specialized_text


def get_html_version_for_chart(png_filename: str, html_dir: Path) -> str:
    """
    Check if an HTML version exists for the given PNG chart.
    Returns the HTML filename if found, otherwise returns the PNG filename.
    """
    # Extract base filename without extension
    base_name = Path(png_filename).stem
    
    # Look for corresponding HTML file
    for html_file in html_dir.glob(f"{base_name}*.html"):
        return html_file.name
    
    # Fallback to PNG if no HTML found
    return png_filename


def image_to_base64(file_path: Path):
    """Converts a local image file to a base64 string for embedding in HTML."""
    try:
        if not file_path.is_file():
            return None
            
        with open(file_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            mime_type = f"image/{file_path.suffix.lstrip('.')}"
            return f"data:{mime_type};base64,{encoded_string}"
    except Exception as e:
        print(f" Error converting {file_path} to base64: {e}")
        return None


def _normalize_asset_key(value: str) -> str:
    """Normalize chart identifiers for fuzzy filename matching."""
    return re.sub(r'[^a-z0-9]+', '', (value or "").lower())


def _display_name_from_filename(filename: str) -> str:
    """Convert a chart filename into a readable chart title."""
    stem = Path(filename).stem
    return stem.replace("_", " ").replace("-", " ").strip().title()


def _strip_supporting_charts_section(text: str) -> str:
    """Remove any legacy Supporting Charts section from report text."""
    return re.sub(
        r"(?is)(?:^|\n)\s*(?:####\s*5\.\s*Supporting Charts|5\.\s*Supporting Charts|Supporting Charts)\s*:?\s*.*?(?=\n\s*(?:####\s*\d+\.|(?:\d+\.\s+[A-Z][^\n]*))|\Z)",
        "\n",
        text or "",
    ).strip()


def _prefers_full_width_chart(asset: Dict[str, Any]) -> bool:
    """Dashboard-style charts need their own row to avoid cramped Plotly layouts."""
    normalized_name = _normalize_asset_key(asset.get("filename", "")) + _normalize_asset_key(asset.get("stem", ""))
    return "dashboard" in normalized_name


def _iframe_height_for_chart(html_file: str) -> str:
    """Choose a taller iframe for dense dashboard-style charts."""
    lowered = (html_file or "").lower()
    if "dashboard" in lowered:
        return "980px"
    if any(token in lowered for token in ["category_subplots", "treemap"]):
        return "920px"
    return "780px"


def _build_chart_assets(chart_names: List[str], chart_dir: Path, html_chart_dir: Path) -> List[Dict[str, Any]]:
    """Build a deterministic list of generated chart assets."""
    chart_assets: List[Dict[str, Any]] = []

    for name in chart_names:
        full_path = chart_dir / name
        base64_img = image_to_base64(full_path)
        html_version = get_html_version_for_chart(name, html_chart_dir)
        chart_assets.append({
            "filename": name,
            "stem": Path(name).stem,
            "normalized_filename": _normalize_asset_key(name),
            "normalized_stem": _normalize_asset_key(Path(name).stem),
            "base64": base64_img,
            "html_file": html_version if html_version != name else None,
            "display_name": _display_name_from_filename(name),
        })

    return chart_assets


def _resolve_chart_catalog_entries(
    parsed_charts: List[Dict[str, str]],
    chart_assets: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Resolve Gemini chart-catalog rows onto actual generated chart assets."""
    resolved_entries: List[Dict[str, Any]] = []
    used_asset_indices = set()

    def pick_asset(raw_name: str, entry_index: int) -> Optional[int]:
        raw_name = raw_name or ""
        extracted = re.search(r'([\w.-]+\.(png|jpg|jpeg))', raw_name, re.IGNORECASE)
        normalized_raw = _normalize_asset_key(raw_name)
        normalized_extracted = _normalize_asset_key(extracted.group(1)) if extracted else ""
        normalized_extracted_stem = _normalize_asset_key(Path(extracted.group(1)).stem) if extracted else ""

        for idx, asset in enumerate(chart_assets):
            if idx in used_asset_indices:
                continue
            if normalized_extracted and asset["normalized_filename"] == normalized_extracted:
                return idx
            if normalized_extracted_stem and asset["normalized_stem"] == normalized_extracted_stem:
                return idx

        for idx, asset in enumerate(chart_assets):
            if idx in used_asset_indices:
                continue
            if asset["normalized_stem"] and asset["normalized_stem"] in normalized_raw:
                return idx
            if normalized_raw and normalized_raw in asset["normalized_filename"]:
                return idx

        return None

    for entry_index, entry in enumerate(parsed_charts):
        asset_index = pick_asset(entry.get("name", ""), entry_index)
        if asset_index is None:
            continue

        used_asset_indices.add(asset_index)
        asset = chart_assets[asset_index]
        resolved_entries.append({
            "asset": asset,
            "display_name": entry.get("display_name") or asset["display_name"],
            "explanation": entry.get("explanation", "").strip(),
            "source_name": entry.get("name", asset["filename"]),
        })

    for idx, asset in enumerate(chart_assets):
        if idx in used_asset_indices:
            continue
        resolved_entries.append({
            "asset": asset,
            "display_name": asset["display_name"],
            "explanation": "Auto-generated supplementary visualization for extended data exploration.",
            "source_name": asset["filename"],
        })

    return resolved_entries


def _parse_chart_catalog(chart_catalog_text: str) -> List[Dict[str, str]]:
    """
    Parses the raw text of the Chart Catalog (Section 0) into a structured list 
    of chart names and their VLM explanations.
    """
    charts = []
    # Regex to capture chart entry: starts with number + '.', then captures filename/title, 
    # and all following text until the next numbered entry or end of string.
    chart_entry_pattern = re.compile(
        r'\d+\.\s*(?:\*\*|)(.*?)(?::\s*|\*\*:\s*)(?:\r?\n|)(?:VLM Findings:\s*\r?\n)?(.*?)(?=\r?\n\d+\.|\Z)', 
        re.DOTALL
    )
    
    matches = chart_entry_pattern.findall(chart_catalog_text)


    for chart_name_raw, explanation_raw in matches:
        # The raw name is the filename (e.g., 'image_123.png - Sales Trend')
        raw_name = chart_name_raw.strip().replace('**', '')
        # Clean the chart name to remove the filename for display (User Request)
        # Find the last space followed by a hyphen or a closing parenthesis to split on
        # and prioritize the text AFTER the last image extension if present.
        match_title = re.search(r'[\w]+\.(png|jpg|jpeg)\s*[-\u2013]?\s*(.*)', raw_name, re.IGNORECASE)
        if match_title and match_title.group(2):
              display_name = match_title.group(2).strip()
        else:
            # Fallback to the raw name if no clean split point is found
            display_name = raw_name
            
        explanation = explanation_raw.strip() # Keep newlines for formatting later


        charts.append({
            'name': raw_name,  # Keep raw name for file lookup
            'display_name': display_name, # Use clean name for display
            'explanation': explanation
        })
        
    return charts


def _apply_markdown_to_html(text: str) -> str:
    """Converts **bold** markdown to <strong> HTML and handles newlines."""
    text = _strip_supporting_charts_section(text)
    # 1. Convert **text** to <strong>text</strong>
    # Use re.sub to globally replace markdown bolding
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text, flags=re.DOTALL)
    
    # 2. Convert newlines to HTML line breaks for internal formatting
    text = text.replace('\r\n', '<br>').replace('\n', '<br>')
    
    return text


def _format_content_to_html(content_text: str, is_recommendation: bool) -> str:
    """
    Converts raw text content into structured HTML, ensuring full text capture
    and selective bolding via markdown processing.
    """
    content_text = _strip_supporting_charts_section(content_text).strip()
    
    # 1. Handle Bulleted Lists (Section 2: Key Insights & Drivers, Llama Report)
    if content_text.startswith('*'):
        # Pattern to find a bullet point: starts with *, followed by content, up to the next * or end of string.
        # Uses DOTALL to capture internal newlines within a single bullet point (CRITICAL FIX).
        # We strip the leading bullet characters and any surrounding whitespace.
        items = re.findall(r'^\s*\*+\s*(.*?)(?=\r?\n\s*\*+|\Z)', content_text, re.DOTALL | re.MULTILINE)


        list_html = ""
        for item_content in items:
            # Apply markdown processing to the content of the list item
            item_content = _apply_markdown_to_html(item_content.strip())
            list_html += f"<li>{item_content}</li>"
            
        return f"<ul class='key-points-list'>{list_html}</ul>"


    # 2. Handle Numbered Lists (Section 3: Data-Driven Recommendations, Horizontal Summary)
    elif re.match(r'^\d+\.', content_text) or is_recommendation:
        # Regex to find 1. [Content], 2. [Content]... (CRITICAL FIX)
        items = re.findall(r'(\d+\.\s*)(.*?)(?=\r?\n\d+\.|\Z)', content_text, re.DOTALL)
        list_html = ""
        for number_raw, item_content in items:
            # Apply markdown processing to the content of the list item
            item_content = _apply_markdown_to_html(item_content.strip())
            # Strip out any leading numbering/bullets that might have been part of the raw content
            list_html += f"<li>{item_content}</li>"
        # We use a custom ul with custom bullets
        return f"<ul class='key-points-list'>{list_html}</ul>"


    # 3. Handle Paragraphs (Section 1: Overall Performance Summary)
    else:
        # Simple cleanup. Apply markdown processing.
        content_html = _apply_markdown_to_html(content_text)
        return f"<div class='key-summary-text'>{content_html}</div>"


def _render_chart_catalog_item(
    chart_entry: Dict[str, Any],
    chart_data: Dict[str, str],
    chart_html_map: Dict[str, str],
) -> str:
    """Render one chart tile for the catalog."""
    asset = chart_entry["asset"]
    display_name = chart_entry["display_name"]
    explanation = _apply_markdown_to_html(chart_entry["explanation"])
    data = chart_data.get(asset["filename"])
    html_file = chart_html_map.get(asset["filename"])

    if html_file:
        iframe_height = _iframe_height_for_chart(html_file)
        visual_html = (
            f"<iframe src='charts_html/{html_file}' width='100%' height='{iframe_height}' "
            "frameborder='0' style='border: 1px solid #ccc; border-radius: 4px; background: #fff; display: block;'></iframe>"
        )
    else:
        visual_html = (
            f"<img src=\"{data}\" alt=\"{display_name}\" title=\"Click to view full size: {display_name}\" "
            f"onclick=\"showFullChart('{data}', '{display_name}')\">"
            if data
            else f"<div class='chart-placeholder'>Image not found: {display_name}</div>"
        )

    return f"""
    <div class="chart-item">
        <h5>{display_name}</h5>
        <div class="chart-visual-wrapper">
            {visual_html}
        </div>
        <div class="chart-explanation">
            <p>{explanation}</p>
        </div>
    </div>
    """



def _reorder_gemini_report_content(
    raw_content: str,
    title: str,
    specialized_fallback_text: str = "",
) -> Tuple[str, str, str]:
    """
    Parses the Gemini VLM report content (Insight #2) to reorder the sections:
    1. Horizontal Header (Section 1 and 3)
    2. Body Content (Section 2)
    3. Chart Catalog (Section 0)
    
    Returns: (sections_1_3_html, section_2_html, chart_catalog_raw_text)
    
    MODIFICATION: Removed redundant Context: [GEMINI INSIGHTS] removal.
    """
    
    # Adjusted regex to correctly capture section numbers and content
    section_pattern = re.compile(
        r'(####\s*(\d+)\.\s*(.*?))(?:\r?\n)(.*?)(?=\r?\n####|\Z)', 
        re.DOTALL | re.IGNORECASE
    )
    
    parsed_sections: Dict[int, Tuple[str, str]] = {}
    
    content_to_parse = _strip_supporting_charts_section(raw_content.strip())
    # Removing any header lines that might precede the first #### section
    content_to_parse = re.sub(r'(?:Context: \[GEMINI INSIGHTS\])?\r?\n###\s*EXECUTIVE SUMMARY REPORT\r?\n', '', content_to_parse, flags=re.IGNORECASE).strip()


    matches = section_pattern.findall(content_to_parse)


    for header_raw, number_str, _, content_raw in matches:
        try:
            key = int(number_str.strip())
            content_cleaned = content_raw.strip()
            parsed_sections[key] = (header_raw.strip(), content_cleaned)
        except ValueError:
            continue

    fallback_bullets: List[str] = []
    if specialized_fallback_text:
        for line in specialized_fallback_text.splitlines():
            cleaned = line.strip().lstrip("•*- ").strip()
            if not cleaned:
                continue
            if cleaned.lower().startswith("here are the key insights"):
                continue
            fallback_bullets.append(cleaned)

    if 1 not in parsed_sections and fallback_bullets:
        summary_text = " ".join(fallback_bullets[:2])
        parsed_sections[1] = ("#### 1. Overall Performance Summary", summary_text)

    if 2 not in parsed_sections and fallback_bullets:
        key_driver_text = "\n".join(f"* {item}" for item in fallback_bullets[:4])
        parsed_sections[2] = ("#### 2. Key Insights & Drivers", key_driver_text)

    if 4 not in parsed_sections and 3 in parsed_sections:
        parsed_sections[4] = ("#### 4. Recommendations", parsed_sections[3][1])

    if 4 not in parsed_sections:
        recommendation_text = "\n".join([
            "1. Investigate the largest anomalies and validate whether they reflect true business behavior or data-quality issues.",
            "2. Prioritize the strongest-performing categories, sources, or regions highlighted across the charts and protect their availability.",
            "3. Review weak or underperforming segments and use pricing, assortment, or inventory actions to reduce risk.",
        ])
        parsed_sections[4] = ("#### 4. Recommendations", recommendation_text)


    # --- 1. Build the Horizontal Header (Section 1 and 3) ---
    header_html = ""
    header_html += "<div class='horizontal-summary-row'>" 
    
    # Process Section 1: Overall Performance Summary (LEFT BLOCK)
    if 1 in parsed_sections:
        header_md, content_text = parsed_sections[1]
        header_html += f"""
        <div class='summary-block summary-performance'>
            <h4>Overall Performance Summary</h4>
            {_format_content_to_html(content_text, is_recommendation=False)}
        </div>
        """
        
    # Process Section 4: Recommendations (RIGHT BLOCK)
    if 4 in parsed_sections:
        header_md, content_text = parsed_sections[4]
        header_html += f"""
        <div class='summary-block summary-recommendations'>
            <h4>Data-Driven Recommendations</h4>
            {_format_content_to_html(content_text, is_recommendation=True)}
        </div>
        """
        
    header_html += "</div>" # Close horizontal-summary-row


    # --- 2. Build the Body Content (Section 2) ---
    body_html = ""
    if 2 in parsed_sections:
        header_md, content_text = parsed_sections[2]
        # Keep Section 2 header but clean it up
        body_html += f"<h4 class='body-insights-header'>{header_md.lstrip('# ').strip()}</h4>\n"
        body_html += _format_content_to_html(content_text, is_recommendation=False)
    if 3 in parsed_sections:
        header_md, content_text = parsed_sections[3]
        body_html += f"<h4 class='body-insights-header'>{header_md.lstrip('# ').strip()}</h4>\n"
        body_html += _format_content_to_html(content_text, is_recommendation=False)
    
    # --- 3. Extract Chart Catalog (Section 0) ---
    chart_catalog_text = parsed_sections.get(0, ('', ''))[1].strip()
    
    return header_html.strip(), body_html.strip(), chart_catalog_text



def generate_html_report(
    insights: List[Dict[str, Any]],
    chart_names: List[str],
    chart_dir: Path,
    html_chart_dir: Path,
    specialized_fallback_text: str = "",
):
    """Generates the full interactive HTML file with reordering logic and new chart display logic."""
    
    # 1. Prepare Chart Data (Load all charts into base64)
    chart_assets = _build_chart_assets(chart_names, chart_dir, html_chart_dir)
    chart_data = {}
    chart_html_map = {}
    charts_found_count = 0
    
    print(f"Attempting to embed {len(chart_names)} dynamically discovered charts...")
    
    for asset in chart_assets:
        if asset["base64"]:
            chart_data[asset["filename"]] = asset["base64"]
            charts_found_count += 1
        if asset["html_file"]:
            chart_html_map[asset["filename"]] = asset["html_file"]
            if asset["base64"]:
                print(f"   HTML version found for {asset['filename']}: {asset['html_file']}")
            else:
                print(f"   HTML-only chart found for {asset['filename']}: {asset['html_file']}")
    
    print(f"Successfully embedded {charts_found_count} charts into Base64.")
    print(f"Found {len(chart_html_map)} HTML chart versions available.")


    # 2. Build the Tabs/Navigation and Content 
    nav_html = ""
    content_html = ""
    
    # Set Insight #2 as the default active tab. Since Insight #1 is removed, we default to the first remaining insight.
    active_index = 0 
    
    for i, insight in enumerate(insights):
        # We manually set the active tab to the first one now (which is Insight #2)
        active_class = "active" if i == active_index else ""
        display_style = 'display: block;' if i == active_index else 'display: none;'
        
        clean_content = _strip_supporting_charts_section(insight['content']).strip() 
        tab_class = ""
        
        nav_html += f"""
        <button class="tablinks {active_class} {tab_class}" onclick="openInsight(event, '{insight['id']}')">
            {insight['title']}
        </button>
        """
        
        report_display = ""
        chart_display_content = ""
        
        if insight['is_vlm_report']:
            # 2a. Separate Header (1 & 3), Body (2), and Chart Catalog (0)
            header_summary_html, key_insights_html, chart_catalog_raw_text = _reorder_gemini_report_content(
                clean_content,
                insight['title'],
                specialized_fallback_text=specialized_fallback_text,
            )
            
            # The new report display combines the title, horizontal header (1 & 3) and the body (2)
            report_display = f"<h3>{insight['title']}</h3>{header_summary_html}{key_insights_html}"
            
            # 2b. Parse the Chart Catalog text
            parsed_charts = _parse_chart_catalog(chart_catalog_raw_text)
            resolved_charts = _resolve_chart_catalog_entries(parsed_charts, chart_assets)
            
            # 2c. Build the Chart Catalog List (Image + Text)
            chart_display_content = "<div class='charts-header'><h4>4. Chart Catalog (Sequentially Explained)</h4></div>"
            chart_display_content += "<div class='sequential-chart-list'>"
            
            # Group charts into pairs for side-by-side display (CRITICAL FIX)
            for i in range(0, len(resolved_charts), 2):
                chart_display_content += "<div class='chart-pair'>"
                
                # --- Process Chart 1 (i) ---
                chart_entry_1 = resolved_charts[i]
                asset_1 = chart_entry_1["asset"]
                display_name_1 = chart_entry_1["display_name"]
                explanation_1 = _apply_markdown_to_html(chart_entry_1["explanation"])
                data_1 = chart_data.get(asset_1["filename"])
                html_file_1 = chart_html_map.get(asset_1["filename"])
                use_html_1 = html_file_1 is not None
                
                if use_html_1:
                    # Use iframe for HTML charts — relative path so report is portable
                    iframe_height_1 = _iframe_height_for_chart(html_file_1)
                    image_tag_1 = f"<iframe src='charts_html/{html_file_1}' width='100%' height='{iframe_height_1}' frameborder='0' style='border: 1px solid #ccc; border-radius: 4px;'></iframe>"
                else:
                    # Use image for PNG charts
                    image_tag_1 = f"<img src=\"{data_1}\" alt=\"{display_name_1}\" title=\"Click to view full size: {display_name_1}\" onclick=\"showFullChart('{data_1}', '{display_name_1}')\">" if data_1 else f"<div class='chart-placeholder'>Image not found: {display_name_1}</div>"


                chart_display_content += f"""
                <div class="chart-item">
                    <h5>{display_name_1}</h5>
                    <div class="chart-visual-wrapper">
                        {image_tag_1}
                    </div>
                    <div class="chart-explanation">
                        <p>{explanation_1}</p>
                    </div>
                </div>
                """
                
                # --- Process Chart 2 (i+1), if it exists ---
                if i + 1 < len(resolved_charts):
                    chart_entry_2 = resolved_charts[i + 1]
                    asset_2 = chart_entry_2["asset"]
                    display_name_2 = chart_entry_2["display_name"] 
                    explanation_2 = _apply_markdown_to_html(chart_entry_2["explanation"])
                    data_2 = chart_data.get(asset_2["filename"])
                    html_file_2 = chart_html_map.get(asset_2["filename"])
                    use_html_2 = html_file_2 is not None
                    
                    if use_html_2:
                        # Use iframe for HTML charts — relative path so report is portable
                        iframe_height_2 = _iframe_height_for_chart(html_file_2)
                        image_tag_2 = f"<iframe src='charts_html/{html_file_2}' width='100%' height='{iframe_height_2}' frameborder='0' style='border: 1px solid #ccc; border-radius: 4px;'></iframe>"
                    else:
                        # Use image for PNG charts
                        image_tag_2 = f"<img src=\"{data_2}\" alt=\"{display_name_2}\" title=\"Click to view full size: {display_name_2}\" onclick=\"showFullChart('{data_2}', '{display_name_2}')\">" if data_2 else f"<div class='chart-placeholder'>Image not found: {display_name_2}</div>"


                    chart_display_content += f"""
                    <div class="chart-item">
                        <h5>{display_name_2}</h5>
                        <div class="chart-visual-wrapper">
                            {image_tag_2}
                        </div>
                        <div class="chart-explanation">
                            <p>{explanation_2}</p>
                        </div>
                    </div>
                    """
                    
                chart_display_content += "</div>" # Close chart-pair
            
            chart_display_content += "</div>" # Close sequential-chart-list
            
            # Append chart content to the report section content
            report_display += chart_display_content
            
            # Fallback for empty/failed parsing
            if not resolved_charts and chart_catalog_raw_text:
                report_display += (
                    "<div class='chart-placeholder'>"
                    "Charts are being refreshed. Run the analysis once more to repopulate the chart catalog."
                    "</div>"
                )
            
        else:
            # This 'else' block should now only be hit if we add more insight numbers later
            formatted_content = _format_content_to_html(clean_content, is_recommendation=False)
            report_display = f"<h3>{insight['title']}</h3>{formatted_content}"
            
        # The entire content for both tabs is now in the report-section
        content_html += f"""
        <div id="{insight['id']}" class="tabcontent" style="{display_style}">
            <div class="report-section">{report_display}</div>
        </div>
        """


    # 3. Assemble the Final HTML - CRITICAL CHANGES HERE
    html_template = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Intelligent Data Analysis Report</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background-color: #f4f7f6; }}
            .container {{ width: 95%; margin: 20px auto; background: #fff; box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 8px; }}
            h1 {{ padding: 20px; margin: 0; background-color: #3f51b5; color: white; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
            .tab {{ overflow: hidden; border-bottom: 1px solid #ccc; background-color: #e9e9e9; display: flex; }}
            .tab button {{ background-color: inherit; border: none; outline: none; cursor: pointer; padding: 14px 16px; transition: 0.3s; font-size: 15px; border-right: 1px solid #ddd; flex-grow: 1; min-width: 150px; text-align: left; }}
            .tab button:hover {{ background-color: #ddd; }}
            .tab button.active {{ background-color: #ccc; font-weight: bold; }}
            
            /* Base tab content styling */
            .tabcontent {{ display: none; padding: 20px; border-top: none; min-height: 600px; }}
            
            /* All content is now full width */
            .report-section {{ width: 100%; overflow-y: auto; }}


            
            /* --- HORIZONTAL EXECUTIVE SUMMARY STYLES --- */
            .horizontal-summary-row {{
                display: flex;
                gap: 20px;
                margin-bottom: 25px;
                border: 2px solid #3f51b5; 
                padding: 15px;
                border-radius: 10px;
                background-color: #e8eaf6; 
            }}
            .summary-block {{
                flex: 1;
                padding: 0;
            }}
            .summary-block h4 {{
                margin-top: 0;
                color: #3f51b5; 
                border-bottom: 1px solid #c5cae9;
                padding-bottom: 5px;
                font-size: 1.15em;
                font-weight: bold;
            }}
            /* Keypoint Formatting - Selective bolding applied only via markdown conversion */
            .key-points-list {{
                list-style-type: none;
                padding-left: 0;
                margin: 0;
            }}
            .key-points-list li {{
                margin-bottom: 10px;
                line-height: 1.4;
                padding-left: 25px;
                text-indent: -25px;
                list-style: none;
                font-size: 0.95em;
                font-weight: normal; 
            }}
            .key-points-list li::before {{
                content: "•";
                color: #ff9800; /* Orange bullet point */
                font-weight: bold;
                display: inline-block;
                width: 25px;
            }}
            .key-summary-text {{
                line-height: 1.6;
                color: #333;
                font-size: 1em;
                font-weight: 500;
            }}
            .body-insights-header {{
                color: #3f51b5 !important;
                border-bottom: 2px solid #3f51b533 !important;
                padding-bottom: 5px !important;
                margin-top: 25px !important;
                font-size: 1.2em;
            }}


            /* --- CHART CATALOG STYLING (Sequential, Side-by-Side) --- */
            .charts-header {{ 
                margin-top: 30px; 
                margin-bottom: 15px; 
                border-bottom: 2px solid #3f51b5; 
                padding-bottom: 5px; 
            }}
            .charts-header h4 {{
                color: #3f51b5 !important;
                font-size: 1.2em;
                margin-bottom: 0;
            }}
            .sequential-chart-list {{ 
                display: flex; 
                flex-direction: column; 
                gap: 40px; 
                margin-top: 20px; 
                padding: 15px;
                background-color: #fcfcfc;
                border: 1px solid #eee;
                border-radius: 8px;
            }}
            .chart-pair {{
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 20px;
                width: 100%;
                align-items: start;
            }}
            .chart-item {{ 
                min-width: 0;
                border: 1px solid #e0e0e0; 
                padding: 20px; 
                border-radius: 8px; 
                background: #fff; 
                box-shadow: 0 1px 3px rgba(0,0,0,0.05); 
                display: flex; 
                flex-direction: column;
            }}
            .chart-item h5 {{ 
                margin: 0 0 15px 0; 
                color: #2e7d32; 
                border-bottom: 1px dashed #eee; 
                padding-bottom: 5px; 
                font-size: 1.1em; 
            }}
            .chart-explanation {{ width: 100%; }}
            .chart-explanation p {{ 
                font-size: 0.95em; 
                line-height: 1.5; 
                margin-top: 5px; 
                color: #333; 
            }}
            .chart-visual-wrapper {{ 
                width: 100%; 
                text-align: left; 
                margin-bottom: 15px;
                overflow-x: auto;
                overflow-y: hidden;
                padding-bottom: 8px;
                border-radius: 8px;
                background: #fff;
                border: 1px solid #e5e7eb;
            }}
            .chart-visual-wrapper::-webkit-scrollbar {{
                height: 10px;
            }}
            .chart-visual-wrapper::-webkit-scrollbar-thumb {{
                background: #cbd5e1;
                border-radius: 999px;
            }}
            .chart-visual-wrapper iframe {{
                display: block;
                width: 760px !important;
                max-width: none !important;
                background: #fff;
                border: 0 !important;
            }}
            .chart-visual-wrapper iframe[src*='Dashboard.html'] {{
                width: 980px !important;
            }}
            .chart-visual-wrapper iframe[src*='Category_Subplots.html'],
            .chart-visual-wrapper iframe[src*='Market_Composition_Treemap.html'],
            .chart-visual-wrapper iframe[src*='Top_10_Category_Bar.html'],
            .chart-visual-wrapper iframe[src*='anomaly_detection_'] {{
                width: 860px !important;
            }}
            .chart-visual-wrapper img {{ 
                max-width: 100%; 
                height: auto; 
                border: 1px solid #ccc; 
                border-radius: 4px; 
                cursor: pointer;
                transition: transform 0.2s;
            }}
            .chart-visual-wrapper img:hover {{ transform: scale(1.01); }}
            .chart-placeholder {{ color: #cc0000; border: 1px dashed #cc0000; padding: 10px; text-align: center; }}
            @media (max-width: 820px) {{
                .chart-pair {{
                    grid-template-columns: 1fr;
                }}
            }}
            /* --- END CHART CATALOG STYLING --- */


            /* MODAL STYLES (Unchanged) */
            .modal {{ display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(0,0,0,0.9); }}
            .modal-content {{ margin: auto; display: block; width: 80%; max-width: 1200px; max-height: 90%; margin-top: 5vh; }}
            #caption {{ margin: auto; display: block; width: 80%; text-align: center; color: #ccc; padding: 10px 0; }}
            .close {{ position: absolute; top: 15px; right: 35px; color: #f1f1f1; font-size: 40px; font-weight: bold; transition: 0.3s; }}
            .close:hover, .close:focus {{ color: #bbb; text-decoration: none; cursor: pointer; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Intelligent Data Analysis Report</h1>
            <div class="tab">
                {nav_html}
            </div>


            {content_html}
        </div>


        <div id="fullChartModal" class="modal" onclick="closeModal()">
            <span class="close" onclick="closeModal()">&times;</span>
            <img class="modal-content" id="img01">
            <div id="caption"></div>
        </div>


        <script>
            function openInsight(evt, insightId) {{
                var i, tabcontent, tablinks;


                tabcontent = document.getElementsByClassName("tabcontent");
                for (i = 0; i < tabcontent.length; i++) {{
                    tabcontent[i].style.display = "none";
                }}


                tablinks = document.getElementsByClassName("tablinks");
                for (i = 0; i < tablinks.length; i++) {{
                    tablinks[i].className = tablinks[i].className.replace(" active", "");
                }}


                var currentInsight = document.getElementById(insightId);


                // All content is now full-width (block display)
                currentInsight.style.display = "block"; 
                
                evt.currentTarget.className += " active";
            }}
            
            var modal = document.getElementById("fullChartModal");
            var modalImg = document.getElementById("img01");
            var captionText = document.getElementById("caption");
            
            function showFullChart(imgData, imgName) {{
              modal.style.display = "block";
              modalImg.src = imgData;
              captionText.innerHTML = "Chart: " + imgName;
            }}


            function closeModal() {{
              modal.style.display = "none";
            }}
            
            document.addEventListener('DOMContentLoaded', function() {{
                var activeTab = document.querySelector('.tablinks.active');
                if (activeTab) {{
                    activeTab.click();
                }} else {{
                    // Fallback to click the first tab if none is initially active
                    var firstTab = document.querySelector('.tablinks');
                    if (firstTab) {{
                        firstTab.click();
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    # 4. Write the file
    try:
        with open(HTML_OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(html_template)
        
        print(f"\n Successfully generated interactive report: {HTML_OUTPUT_FILE}")
        print("Open this file in your web browser to view the report.")
    except Exception as e:
        print(f"Error writing HTML file: {e}")


# --- Main Execution (Unchanged) ---
if __name__ == "__main__":
    
    # 1. Dynamically discover files in the charts directory
    found_chart_names = discover_chart_assets(CHART_DIRECTORY, CHARTS_HTML_DIRECTORY)
    
    # Use os.path.exists on the Path objects
    if not CHART_DIRECTORY.is_dir():
        pass 
    elif not ANALYSIS_REPORT_PATH.is_file():
        print(f" Error: Analysis report file not found at {ANALYSIS_REPORT_PATH}")
    else:
        try:
            insights = extract_insights_and_charts(ANALYSIS_REPORT_PATH)
            specialized_fallback_text = extract_specialized_fallback(ANALYSIS_REPORT_PATH)
             
            if insights:
                # 2. Generate the report
                generate_html_report(
                    insights,
                    found_chart_names,
                    CHART_DIRECTORY,
                    CHARTS_HTML_DIRECTORY,
                    specialized_fallback_text=specialized_fallback_text,
                )
            else:
                print(" Could not parse any insights from the analysis report. Check file formatting.")
        except Exception as e:
            print(f" A critical error occurred during HTML generation: {e}")
            import traceback
            traceback.print_exc()
            print("Please ensure your analysis_report.txt file structure matches the expected format.")
