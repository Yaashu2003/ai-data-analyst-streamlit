import gemini_patch
# --------------------------------------------------------------
# report_viewer.py - Utility functions and UI for the Report Viewer
# --------------------------------------------------------------
import sys
import subprocess
from pathlib import Path
from bs4 import BeautifulSoup
import streamlit as st
import streamlit.components.v1 as components
import os
from typing import Dict, List, Optional
import json
import requests
import difflib
import re
from html import escape

# Import declare_component for custom text selector
from streamlit.components.v1 import declare_component

# --- Paths (relative to project root so charts load regardless of CWD) ---
_BASE_DIR = Path(__file__).resolve().parent
REPORT_HTML = _BASE_DIR / "interactive_analysis_report.html"
CHARTS_HTML_DIR = _BASE_DIR / "charts_html"
SUPERSTORE_CSV = _BASE_DIR / "Superstore.csv"

MAX_REPORT_CHUNK_CHARS = 7000


def _is_transient_ai_error(error: Exception) -> bool:
    err = str(error).upper()
    return any(code in err for code in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED"))


def _generate_gemini_content_with_retry(client, prompt: str, system_instruction: str = None, max_retries: int = 3):
    import time

    last_error = None
    for attempt in range(max_retries):
        try:
            config = {"temperature": 0.3}
            if system_instruction:
                config["system_instruction"] = system_instruction
                
            return client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[prompt],
                config=config
            )
        except Exception as e:
            last_error = e
            if not _is_transient_ai_error(e) or attempt == max_retries - 1:
                raise
            time.sleep((2 ** attempt) + 2)
    raise last_error


def _split_text_for_model(text: str, max_chars: int = MAX_REPORT_CHUNK_CHARS) -> List[str]:
    """Split a report into ordered chunks without dropping any content."""
    text = text or ""
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current = ""
    parts = re.split(r'(\n{2,})', text)

    for part in parts:
        if not part:
            continue
        if len(current) + len(part) <= max_chars:
            current += part
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(part) > max_chars:
            chunks.append(part[:max_chars])
            part = part[max_chars:]
        current = part

    if current:
        chunks.append(current)
    return chunks


def _build_feedback_context(
    general_feedback: str,
    sentence_feedbacks: List[Dict],
    edit_analysis: Optional[List[Dict]] = None,
) -> str:
    feedback_parts = []
    if edit_analysis:
        feedback_parts.append("Human Edit Analysis (incorporate these refinements):")
        for e in edit_analysis:
            feedback_parts.append(
                f"  - Original: \"{e.get('original', '')}\" -> "
                f"Edited: \"{e.get('edited', '')}\" | Feedback: {e.get('feedback', '')}"
            )
    if general_feedback:
        feedback_parts.append(f"General Feedback: {general_feedback}")
    if sentence_feedbacks:
        feedback_parts.append("Sentence-Level Feedback:")
        for sf in sentence_feedbacks:
            feedback_parts.append(f"  - Text: \"{sf.get('text', '')}\" | Feedback: {sf.get('feedback', '')}")
    return "\n".join(feedback_parts) if feedback_parts else "No specific feedback provided."


def _rewrite_large_report_in_chunks(
    client,
    report_text: str,
    combined_feedback: str,
    placeholder_instruction: str,
) -> str:
    chunks = _split_text_for_model(report_text)
    rewritten_chunks = []

    for index, chunk in enumerate(chunks, 1):
        chunk_prompt = f"""
You are editing chunk {index} of {len(chunks)} from one report. Every chunk will be reassembled in the same order.

STRICT RULES:
1. Use the feedback below, but apply only the parts relevant to this chunk.
2. Preserve every data point, number, chart placeholder, heading, and factual detail in this chunk.
3. Do NOT summarize, omit, compress, or replace details with placeholders.
4. Modify ONLY textual content. Do NOT change images, charts, HTML graph components, or embedded visual elements.
{placeholder_instruction}

User Feedback:
{combined_feedback}

Report Chunk {index}/{len(chunks)}:
{chunk}

Return ONLY the improved version of this chunk. No commentary.
""".strip()
        try:
            response = _generate_gemini_content_with_retry(client, chunk_prompt, system_instruction="You are a precise report editor.")
            rewritten_chunks.append(response.text.strip())
        except Exception as e:
            st.warning(f"Gemini could not rewrite chunk {index}; keeping that chunk unchanged. Error: {e}")
            rewritten_chunks.append(chunk)

    return "\n\n".join(rewritten_chunks)

def run_analysis_script(report_ready_state_key):
    """Runs the external main.py analysis script to generate the report."""
    if REPORT_HTML.is_file():
        REPORT_HTML.unlink()
        st.session_state[report_ready_state_key] = False

    with st.spinner("Running analysisâ€¦ please wait"):
        try:
            # Assumes 'main.py' is the external analysis script that generates the report
            subprocess.run([sys.executable, "-u", "main.py"], check=True)
        except Exception as e:
            st.error(f"âŒ Analysis failed: {e}. Check if 'main.py' exists and its dependencies are installed.")
            return

    if REPORT_HTML.is_file():
        st.session_state[report_ready_state_key] = True
        st.success("✅ Report generated successfully!")
    else:
        st.error("âŒ Report generation failed â€” report file missing.")

def _resolve_chart_path(chart_name: str) -> Optional[Path]:
    """Resolve chart path: exact match, then same base (e.g. customer_segments_0_*.html), then same type (e.g. time_series_*.html)."""
    exact = CHARTS_HTML_DIR / chart_name
    if exact.is_file():
        return exact
    stem = Path(chart_name).stem
    parts = stem.split("_")
    # Try 1: base prefix = name_index (strip timestamp YYYYMMDD_HHMMSS)
    if len(parts) >= 3:
        try:
            int(parts[-1])
            int(parts[-2])
            base_prefix = "_".join(parts[:-2])
        except (ValueError, IndexError):
            base_prefix = stem
    else:
        base_prefix = stem
    matches = sorted(CHARTS_HTML_DIR.glob(f"{base_prefix}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    # Try 2: same chart type with different index (e.g. time_series_4 -> time_series_*.html)
    if len(parts) >= 4:
        type_prefix = "_".join(parts[:-3])  # strip timestamp (2 parts)
        matches = sorted(CHARTS_HTML_DIR.glob(f"{type_prefix}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    # Try 3: chart type only, no index (e.g. box_plot_7 -> box_plot_*.html to match box_plot_0)
    if len(parts) >= 5:
        type_only = "_".join(parts[:-4])  # strip index + timestamp (3 parts)
        matches = sorted(CHARTS_HTML_DIR.glob(f"{type_only}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def _inject_html_before_close(html: str, close_tag: str, injection: str) -> str:
    lowered = (html or "").lower()
    index = lowered.rfind(close_tag)
    if index == -1:
        return (html or "") + injection
    return (html or "")[:index] + injection + (html or "")[index:]


def _make_chart_srcdoc_responsive(chart_html: str) -> str:
    marker = "codex-responsive-chart-srcdoc"
    if marker in (chart_html or ""):
        return chart_html or ""

    injection = f"""
<style id="{marker}">
html, body {{ width: 100%; margin: 0; padding: 0; overflow: hidden; background: #fff; }}
.plotly-graph-div,
.js-plotly-plot,
.svg-container {{
    width: 100% !important;
    max-width: 100% !important;
    min-width: 0 !important;
}}
svg.main-svg {{ max-width: 100% !important; }}
</style>
<script>
(function () {{
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
}})();
</script>
"""
    if "</head>" in (chart_html or "").lower():
        return _inject_html_before_close(chart_html or "", "</head>", injection)
    return injection + (chart_html or "")


def _make_report_html_responsive(html_content: str) -> str:
    """Apply responsive chart/report overrides to old and newly generated report HTML."""
    soup = BeautifulSoup(html_content or "", "html.parser")

    for existing in soup.find_all("style", id="codex-responsive-report-css"):
        existing.decompose()

    style = soup.new_tag("style", id="codex-responsive-report-css")
    style.string = """
.container {
    max-width: min(1500px, 96vw) !important;
}
.report-section {
    overflow: visible !important;
}
.chart-pair {
    display: grid !important;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 620px), 1fr)) !important;
    gap: 20px !important;
    align-items: start !important;
}
.chart-item {
    min-width: 0 !important;
    overflow: hidden !important;
}
.chart-visual-wrapper {
    overflow: hidden !important;
    touch-action: auto !important;
    padding-bottom: 0 !important;
}
.chart-visual-wrapper iframe,
.chart-frame {
    width: 100% !important;
    max-width: 100% !important;
    min-width: 0 !important;
    height: clamp(540px, 58vh, 720px) !important;
    border: 0 !important;
    display: block !important;
}
.chart-visual-wrapper iframe[src*="Dashboard"],
.chart-visual-wrapper iframe[src*="dashboard"],
.chart-visual-wrapper iframe[src*="Category_Subplots"],
.chart-visual-wrapper iframe[src*="Market_Composition_Treemap"],
.chart-visual-wrapper iframe[src*="Top_10_Category_Bar"],
.chart-visual-wrapper iframe[src*="anomaly_detection_"] {
    height: clamp(620px, 66vh, 820px) !important;
}
.chart-visual-wrapper .plotly-graph-div,
.chart-visual-wrapper .js-plotly-plot,
.chart-visual-wrapper .svg-container {
    width: 100% !important;
    max-width: 100% !important;
    min-width: 0 !important;
}
@media (max-width: 760px) {
    .report-section { padding: 18px !important; }
    .chart-visual-wrapper iframe,
    .chart-frame {
        height: 560px !important;
    }
}
"""
    if soup.head:
        soup.head.append(style)
    else:
        soup.insert(0, style)

    for iframe in soup.find_all("iframe"):
        iframe["width"] = "100%"
        iframe["style"] = "width:100%;max-width:100%;border:0;display:block;background:#fff;"
        if iframe.get("srcdoc"):
            iframe["srcdoc"] = _make_chart_srcdoc_responsive(iframe.get("srcdoc", ""))

    return str(soup)


def load_full_report_html():
    """Loads the main report HTML and embeds iframe charts using srcdoc."""
    if not REPORT_HTML.is_file(): return "<h3>No report generated yet.</h3>"

    html_content = REPORT_HTML.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")
    for iframe in soup.find_all("iframe"):
        iframe["loading"] = "lazy"
        src = iframe.get("src", "") or iframe.get("data-src", "")
        if not src: continue
        clean_src = src.replace("file://", "").replace("\\", "/").lstrip("/")
        chart_name = Path(clean_src).name
        chart_path = _resolve_chart_path(chart_name)

        if chart_path and chart_path.is_file():
            chart_html = _make_chart_srcdoc_responsive(chart_path.read_text(encoding="utf-8"))
            lower_name = chart_name.lower()
            iframe_height = "920px" if any(token in lower_name for token in ["dashboard", "category_subplots", "treemap"]) else "780px"
            iframe["srcdoc"] = chart_html
            iframe["src"] = ""
            iframe["width"] = "100%"
            iframe["height"] = iframe_height
        else:
            error_div = soup.new_tag("div")
            error_div.string = f"âš ï¸ Chart not available: {chart_name} (run analysis to generate charts)"
            iframe.replace_with(error_div)
    return _make_report_html_responsive(str(soup))

def extract_text_from_html(html_content: str) -> str:
    """Extract readable text content from HTML report."""
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()
    
    # Get text
    text = soup.get_text()
    
    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)
    
    return text


# --- Human-in-the-loop: report segments (text vs chart/image) for edit-only-text ---
_CHART_PLACEHOLDER_PREFIX = "---CHART_"
_CHART_PLACEHOLDER_SUFFIX = "---"


def parse_report_to_segments(html_content: str) -> List[Dict]:
    """
    Parse report HTML into segments: text (editable) and chart (visual preserved).
    Charts and images are NOT editable; only surrounding text and chart explanations are.
    Returns list of {"type": "text"|"chart", ...}.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    segments: List[Dict] = []
    report_section = soup.find("div", class_="report-section")
    if not report_section:
        # Fallback: treat whole body as one text block
        return [{"type": "text", "content": extract_text_from_html(html_content)}]

    # Find all chart-item blocks (each has visual iframe/img and explanation)
    chart_items = report_section.find_all("div", class_="chart-item")
    if not chart_items:
        # No charts: single text segment
        text_plain = extract_text_from_html(str(report_section))
        return [{"type": "text", "content": text_plain}]

    # Build text block: clone section, remove chart-items, remainder is intro text
    section_copy = BeautifulSoup(str(report_section), "html.parser")
    for chart_tag in section_copy.find_all("div", class_="chart-item"):
        chart_tag.decompose()
    text_plain = extract_text_from_html(str(section_copy))
    if text_plain.strip():
        segments.append({"type": "text", "content": text_plain})

    # Each chart segment: preserve visual (iframe/img), editable explanation
    for chart_item in chart_items:
        title = ""
        h5 = chart_item.find("h5")
        if h5:
            title = h5.get_text(strip=True)
        visual_html = ""
        wrapper = chart_item.find("div", class_="chart-visual-wrapper")
        if wrapper:
            iframe = wrapper.find("iframe")
            img = wrapper.find("img")
            if iframe:
                visual_html = str(iframe)
            elif img:
                visual_html = str(img)
        explanation = ""
        expl_div = chart_item.find("div", class_="chart-explanation")
        if expl_div:
            p = expl_div.find("p")
            if p:
                explanation = p.get_text(strip=True)
        segments.append({
            "type": "chart",
            "title": title,
            "visual_html": visual_html,
            "explanation": explanation,
            "chart_item_html": str(chart_item),
        })

    return segments


def build_editable_report_text(segments: List[Dict]) -> str:
    """
    Build a single editable document from segments. Chart blocks are represented
    by placeholders so the user edits only text; charts/images are preserved on regenerate.
    """
    parts = []
    for i, seg in enumerate(segments):
        if seg.get("type") == "text":
            parts.append(seg.get("content", ""))
        else:
            # Placeholder so we can split back; user can edit the explanation
            parts.append(
                f"\n\n{_CHART_PLACEHOLDER_PREFIX}{i}{_CHART_PLACEHOLDER_SUFFIX}\n"
                f"Title: {seg.get('title', '')}\n"
                f"Explanation: {seg.get('explanation', '')}\n"
            )
    return "\n".join(parts).strip()


def parse_editable_text_back_to_segments(editable_text: str, segments: List[Dict]) -> List[Dict]:
    """
    Parse the edited (or LLM-regenerated) text back into segment updates.
    Preserves original segment order; updates only 'content' for text and 'explanation' for chart.
    """
    pattern = re.compile(
        re.escape(_CHART_PLACEHOLDER_PREFIX) + r"(\d+)" + re.escape(_CHART_PLACEHOLDER_SUFFIX)
    )
    parts = pattern.split(editable_text)
    # parts: [intro_text, chart_idx_0, block_0, chart_idx_1, block_1, ...]
    out_segments = [{**s} for s in segments]
    if len(parts) < 2:
        # No chart placeholders: treat whole as first text segment
        if segments and segments[0].get("type") == "text":
            out_segments[0]["content"] = editable_text.strip()
        return out_segments

    # First part is text before first chart placeholder
    if segments and segments[0].get("type") == "text":
        out_segments[0]["content"] = (parts[0] or "").strip()
    # Then each pair (chart_idx, block) updates that chart's explanation
    for i in range(1, len(parts) - 1, 2):
        try:
            chart_idx = int(parts[i])
            block = (parts[i + 1] or "").strip()
            new_explanation = ""
            for line in block.splitlines():
                if line.strip().lower().startswith("explanation:"):
                    new_explanation = line.split(":", 1)[-1].strip()
                    break
            if chart_idx < len(out_segments) and out_segments[chart_idx].get("type") == "chart":
                out_segments[chart_idx]["explanation"] = new_explanation or out_segments[chart_idx].get("explanation", "")
        except (ValueError, IndexError):
            continue
    return out_segments


def build_final_report_html_preserving_charts(
    original_html: str,
    segments: List[Dict],
    updated_segments: List[Dict],
) -> str:
    """
    Build final report HTML by merging updated text/explanation with original
    structure. All iframes and images are preserved; only text and chart explanations change.
    """
    soup = BeautifulSoup(original_html, "html.parser")
    report_section = soup.find("div", class_="report-section")
    if not report_section or len(updated_segments) != len(segments):
        return original_html

    def _text_to_html_block(text: str) -> str:
        text = re.sub(
            r"(?is)(?:^|\n)\s*(?:####\s*5\.\s*Supporting Charts|5\.\s*Supporting Charts|Supporting Charts)\s*:?\s*.*?(?=\n\s*(?:####\s*\d+\.|(?:\d+\.\s+[A-Z][^\n]*))|\Z)",
            "\n",
            text or "",
        ).strip()
        heading_tokens = (
            "overall performance summary",
            "key insights & drivers",
            "data-driven recommendations",
            "risks / issues",
            "generated charts",
            "chart catalog",
        )
        title_tokens = (
            "gemini vlm executive report",
            "visual analysis",
        )

        blocks: List[str] = []
        paragraph_lines: List[str] = []
        bullet_items: List[str] = []
        numbered_items: List[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph_lines
            if paragraph_lines:
                content = " ".join(part.strip() for part in paragraph_lines if part.strip())
                if content:
                    blocks.append(f"<p class='key-summary-text report-final-paragraph'>{escape(content)}</p>")
                paragraph_lines = []

        def flush_bullets() -> None:
            nonlocal bullet_items
            if bullet_items:
                items = "".join(
                    f"<li>{escape(item)}</li>" for item in bullet_items if item.strip()
                )
                blocks.append(f"<ul class='key-points-list report-final-list'>{items}</ul>")
                bullet_items = []

        def flush_numbered() -> None:
            nonlocal numbered_items
            if numbered_items:
                items = "".join(
                    f"<li>{escape(item)}</li>" for item in numbered_items if item.strip()
                )
                blocks.append(f"<ol class='report-final-ordered'>{items}</ol>")
                numbered_items = []

        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                flush_paragraph()
                flush_bullets()
                flush_numbered()
                continue

            lowered = line.lower().strip(":")
            is_title = any(token in lowered for token in title_tokens)
            is_heading = any(token in lowered for token in heading_tokens)
            if re.match(r"^\d+\.\s+", line) and any(token in lowered for token in heading_tokens):
                is_heading = True

            if is_title:
                flush_paragraph()
                flush_bullets()
                flush_numbered()
                blocks.append(f"<h3 class='report-final-title'>{escape(line)}</h3>")
                continue

            if is_heading:
                flush_paragraph()
                flush_bullets()
                flush_numbered()
                blocks.append(f"<h4 class='body-insights-header report-final-heading'>{escape(line)}</h4>")
                continue

            if re.match(r"^[-*•]\s+", line):
                flush_paragraph()
                flush_numbered()
                bullet_items.append(re.sub(r"^[-*•]\s+", "", line).strip())
                continue

            numbered_match = re.match(r"^\d+\.\s+(.*)", line)
            if numbered_match:
                flush_paragraph()
                flush_bullets()
                numbered_items.append(numbered_match.group(1).strip())
                continue

            paragraph_lines.append(line)

        flush_paragraph()
        flush_bullets()
        flush_numbered()
        return "".join(blocks)

    text_blocks: List[str] = []
    chart_markup: List[str] = []
    for i, seg in enumerate(updated_segments):
        if seg.get("type") == "text":
            text_blocks.append(_text_to_html_block(seg.get("content", "")))
            continue

        orig = segments[i] if i < len(segments) else seg
        chart_soup = BeautifulSoup(orig.get("chart_item_html", ""), "html.parser")
        expl_div = chart_soup.find("div", class_="chart-explanation")
        if expl_div:
            p = expl_div.find("p") or expl_div
            p.clear()
            p.append(updated_segments[i].get("explanation", "") or orig.get("explanation", ""))
        chart_markup.append(str(chart_soup))

    rebuilt_html = ""
    if text_blocks:
        rebuilt_html += "<div class='report-final-text-shell'>" + "".join(text_blocks) + "</div>"

    if chart_markup:
        rebuilt_html += "<div class='charts-header'><h4>Chart Catalog</h4></div><div class='sequential-chart-list'>"
        for i in range(0, len(chart_markup), 2):
            rebuilt_html += "<div class='chart-pair'>"
            rebuilt_html += chart_markup[i]
            if i + 1 < len(chart_markup):
                rebuilt_html += chart_markup[i + 1]
            rebuilt_html += "</div>"
        rebuilt_html += "</div>"

    report_section.clear()
    report_section.append(BeautifulSoup(rebuilt_html, "html.parser"))
    return _make_report_html_responsive(str(soup))


# --- HITL: Identify human edits and generate per-edit feedback (text only; no graphs/images) ---

_CHART_MARKER_RE = re.compile(r"---CHART_\d+---", re.IGNORECASE)


def _strip_chart_markers(text: str) -> str:
    """Remove ---CHART_N--- blocks and their metadata from text for comparison."""
    text = re.sub(r"---CHART_\d+---.*?(?=---CHART_\d+---|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _is_chart_block(text: str) -> bool:
    """Return True if the block is a chart placeholder, not prose."""
    return bool(_CHART_MARKER_RE.search(text or ""))


def identify_human_edits(original_text: str, edited_text: str) -> List[Dict]:
    """
    STEP 1 - Identify Human Edits.
    Compare original and human-edited report text, detecting only meaningful prose changes.
    Chart placeholders (---CHART_N---) are stripped before diffing so chart metadata
    differences never pollute the human-edit list.
    Returns list of {"original": "...", "edited": "...", "feedback": ""}.
    """
    if not (original_text or "").strip() or not (edited_text or "").strip():
        return []

    orig = _strip_chart_markers((original_text or "").strip())
    edit = _strip_chart_markers((edited_text or "").strip())

    if orig == edit:
        return []

    edits: List[Dict] = []
    orig_lines = orig.splitlines()
    edit_lines = edit.splitlines()
    matcher = difflib.SequenceMatcher(None, orig_lines, edit_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        orig_block = "\n".join(orig_lines[i1:i2]).strip()
        edit_block = "\n".join(edit_lines[j1:j2]).strip()

        # Skip empty-on-both-sides and identical blocks
        if not orig_block and not edit_block:
            continue
        if orig_block == edit_block:
            continue

        # Skip chart marker / metadata blocks
        if _is_chart_block(orig_block) or _is_chart_block(edit_block):
            continue

        # Skip trivially short or whitespace-only diffs
        if len((orig_block + edit_block).strip()) < 10:
            continue

        edits.append({"original": orig_block, "edited": edit_block, "feedback": ""})
    return edits


def generate_human_edit_analysis(edits: List[Dict], api_key: Optional[str] = None) -> List[Dict]:
    """
    STEP 2 â€” Provide Feedback.
    For each modified sentence: explain what was improved (clarity, tone, accuracy, structure),
    suggest improvements if needed, or say "No further refinement needed."
    """
    if not edits:
        return []
    api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        for e in edits:
            e["feedback"] = "No further refinement needed." if e.get("edited") else "Removed or empty."
        return edits

    from google import genai
    try:
        client = genai.Client(api_key=api_key)
        
        prompt = """Analyze these human edits to a report. For each edit, provide brief, constructive feedback on what was improved (e.g., clarity, tone, accuracy) or suggest further refinement.
Return the result strictly as a JSON array of objects with keys "original", "edited", and "feedback".

Edits:
"""
        payload = json.dumps([{"original": e.get("original", ""), "edited": e.get("edited", "")} for e in edits])
        
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[prompt + payload],
            config={"temperature": 0.2}
        )
        content = response.text.strip()
        # Parse JSON array from response (allow markdown code block wrapper)
        raw = re.sub(r"^```\w*\n?", "", content).strip()
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            for i, item in enumerate(parsed):
                if i < len(edits) and isinstance(item, dict):
                    fb = item.get("feedback") or item.get("comment") or ""
                    if fb:
                        edits[i]["feedback"] = str(fb).strip()
        else:
            for e in edits:
                e["feedback"] = e.get("feedback") or "No further refinement needed."
    except (json.JSONDecodeError, KeyError, IndexError, requests.RequestException):
        for e in edits:
            e["feedback"] = e.get("feedback") or "No further refinement needed."
    return edits


def regenerate_final_report_text_only(
    original_text: str,
    edited_text: str,
    edit_analysis: List[Dict],
    general_feedback: str,
    sentence_feedbacks: List[Dict],
    api_key: Optional[str] = None,
) -> str:
    """
    STEP 3 â€” Regenerate Final Report (text only).
    Produce a clean final version: maintain structure, keep all chart placeholders and visuals unchanged,
    only update text where human changes were made. Ensures consistency in tone and formatting.
    """
    api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    has_placeholders = _CHART_PLACEHOLDER_PREFIX in (original_text or "") or _CHART_PLACEHOLDER_PREFIX in (edited_text or "")
    placeholder_rule = ""
    if has_placeholders:
        placeholder_rule = """
CRITICAL: The report contains chart placeholders like ---CHART_0---, ---CHART_1---, etc. with "Title:" and "Explanation:" lines.
You MUST keep every ---CHART_N--- block exactly (same placeholder and structure). You may refine only the Explanation text within each block.
Do NOT modify, remove, or reorder placeholders. Graphs and images are preserved separately and must not be changed.
"""

    feedback_section = ""
    if edit_analysis:
        feedback_section = "Human Edit Analysis (what was improved per edit):\n" + "\n".join(
            f"- Original: \"{e.get('original', '')}\" â†’ Edited: \"{e.get('edited', '')}\" | Feedback: {e.get('feedback', '')}"
            for e in edit_analysis
        )
    if general_feedback:
        feedback_section += "\nGeneral Feedback: " + general_feedback
    if sentence_feedbacks:
        for sf in sentence_feedbacks:
            feedback_section += f"\nFeedback on \"{sf.get('text', '')}\": {sf.get('feedback', '')}"

    prompt = f"""You are an AI report refinement assistant. Produce the final, production-ready report text.

STRICT RULES:
1. Modify ONLY textual content. Do NOT change any images, charts, or HTML graph components.
2. Maintain the original structure (headings, sections, order).
3. Incorporate all human edits and feedback below. Keep tone and formatting consistent.
{placeholder_rule}

Original report text:
{original_text}

Human-edited version:
{edited_text}

{feedback_section}

Return ONLY the improved report text. No commentary, no explanations. Preserve all chart placeholders and structure exactly."""

    if not api_key:
        return edited_text or original_text

    from google import genai
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[prompt],
            config={"temperature": 0.3}
        )
        return response.text.strip()
    except Exception:
        return edited_text or original_text


def convert_markdown_to_html_report(markdown_text: str) -> str:
    """Convert markdown text to HTML report with same styling as original report."""
    import re
    markdown_text = re.sub(
        r"(?is)(?:^|\n)\s*(?:####\s*5\.\s*Supporting Charts|5\.\s*Supporting Charts|Supporting Charts)\s*:?\s*.*?(?=\n\s*(?:####\s*\d+\.|(?:\d+\.\s+[A-Z][^\n]*))|\Z)",
        "\n",
        markdown_text or "",
    ).strip()
    
    # Get the CSS styling from the original report
    try:
        html_content = load_full_report_html()
        soup = BeautifulSoup(html_content, "html.parser")
        # Extract CSS from style tag
        style_tag = soup.find("style")
        css_styles = style_tag.string if style_tag else ""
    except:
        # Fallback CSS if original report not available
        css_styles = """
            .key-points-list {
                list-style-type: none;
                padding-left: 0;
                margin: 0;
            }
            .key-points-list li {
                margin-bottom: 10px;
                line-height: 1.4;
                padding-left: 25px;
                text-indent: -25px;
                list-style: none;
                font-size: 0.95em;
            }
            .key-points-list li::before {
                content: "â€¢";
                color: #ff9800;
                font-weight: bold;
                display: inline-block;
                width: 25px;
            }
            .key-summary-text {
                line-height: 1.6;
                color: #333;
                font-size: 1em;
                margin-bottom: 15px;
            }
            .body-insights-header {
                color: #3f51b5 !important;
                border-bottom: 2px solid #3f51b533 !important;
                padding-bottom: 5px !important;
                margin-top: 25px !important;
                font-size: 1.2em;
            }
        """
    
    # Convert markdown to HTML
    html_body = ""
    lines = markdown_text.split('\n')
    in_list = False
    list_items = []
    current_paragraph = []
    
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        
        # Empty line - close lists/paragraphs
        if not line.strip():
            if in_list and list_items:
                html_body += "<ul class='key-points-list'>"
                for item in list_items:
                    item_clean = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', item)
                    html_body += f"<li>{item_clean}</li>"
                html_body += "</ul>"
                list_items = []
                in_list = False
            elif current_paragraph:
                para_text = ' '.join(current_paragraph)
                para_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', para_text)
                html_body += f"<p class='key-summary-text'>{para_text}</p>"
                current_paragraph = []
            html_body += "<br>"
            i += 1
            continue
        
        # Headers
        if line.strip().startswith('#'):
            if in_list and list_items:
                html_body += "<ul class='key-points-list'>"
                for item in list_items:
                    item_clean = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', item)
                    html_body += f"<li>{item_clean}</li>"
                html_body += "</ul>"
                list_items = []
                in_list = False
            if current_paragraph:
                para_text = ' '.join(current_paragraph)
                para_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', para_text)
                html_body += f"<p class='key-summary-text'>{para_text}</p>"
                current_paragraph = []
            
            level = len(line) - len(line.lstrip('#'))
            text = line.lstrip('# ').strip()
            if level == 1:
                html_body += f"<h1 class='body-insights-header'>{text}</h1>"
            elif level == 2:
                html_body += f"<h2 class='body-insights-header'>{text}</h2>"
            elif level == 3:
                html_body += f"<h3 class='body-insights-header'>{text}</h3>"
            else:
                html_body += f"<h4 class='body-insights-header'>{text}</h4>"
        
        # Bullet lists
        elif line.strip().startswith('*') or line.strip().startswith('-'):
            if current_paragraph:
                para_text = ' '.join(current_paragraph)
                para_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', para_text)
                html_body += f"<p class='key-summary-text'>{para_text}</p>"
                current_paragraph = []
            in_list = True
            item = line.lstrip('*- ').strip()
            list_items.append(item)
        
        # Numbered lists
        elif re.match(r'^\s*\d+\.', line):
            if current_paragraph:
                para_text = ' '.join(current_paragraph)
                para_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', para_text)
                html_body += f"<p class='key-summary-text'>{para_text}</p>"
                current_paragraph = []
            in_list = True
            item = re.sub(r'^\s*\d+\.\s*', '', line).strip()
            list_items.append(item)
        
        # Regular text
        else:
            if in_list and list_items:
                html_body += "<ul class='key-points-list'>"
                for item in list_items:
                    item_clean = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', item)
                    html_body += f"<li>{item_clean}</li>"
                html_body += "</ul>"
                list_items = []
                in_list = False
            current_paragraph.append(line.strip())
        
        i += 1
    
    # Close any remaining lists or paragraphs
    if in_list and list_items:
        html_body += "<ul class='key-points-list'>"
        for item in list_items:
            item_clean = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', item)
            html_body += f"<li>{item_clean}</li>"
        html_body += "</ul>"
    elif current_paragraph:
        para_text = ' '.join(current_paragraph)
        para_text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', para_text)
        html_body += f"<p class='key-summary-text'>{para_text}</p>"
    
    # Wrap in report section
    html_body = f"<div class='report-section'>{html_body}</div>"
    
    # Create full HTML document with same styling as original
    html_template = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Final Analysis Report</title>
        <style>
            body {{ 
                font-family: Arial, sans-serif; 
                margin: 0; 
                padding: 0; 
                background-color: #f4f7f6; 
            }}
            .container {{ 
                width: 95%; 
                margin: 20px auto; 
                background: #fff; 
                box-shadow: 0 4px 12px rgba(0,0,0,0.1); 
                border-radius: 8px; 
            }}
            .report-header {{
                padding: 20px;
                margin: 0;
                background-color: #3f51b5;
                color: white;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
            .report-header h1 {{
                margin: 0;
                padding: 0;
                font-size: 1.8em;
            }}
            .report-header p {{
                margin: 5px 0 0 0;
                opacity: 0.9;
                font-size: 0.95em;
            }}
            .report-content {{
                padding: 20px;
                min-height: 600px;
            }}
            .report-section {{
                margin-bottom: 30px;
                padding: 20px;
                background-color: #fcfcfc;
                border: 1px solid #eee;
                border-radius: 8px;
            }}
            {css_styles}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="report-header">
                <h1>Final Analysis Report</h1>
                <p>Generated with edits and feedback incorporated</p>
            </div>
            <div class="report-content">
                {html_body}
            </div>
        </div>
    </body>
    </html>
    """
    
    return html_template

def create_git_style_diff(original_text: str, final_text: str) -> str:
    """Create a git-style diff view showing additions (green +) and deletions (red -)."""
    
    # Split texts into lines
    original_lines = original_text.splitlines(keepends=True)
    final_lines = final_text.splitlines(keepends=True)
    
    # Use difflib to compute differences
    diff = difflib.unified_diff(
        original_lines,
        final_lines,
        fromfile='Original Report',
        tofile='Final Report',
        lineterm='',
        n=3  # Context lines
    )
    
    # Convert diff to HTML with git-style coloring
    html_diff = []
    html_diff.append("""
    <style>
        .diff-container {
            font-family: 'Courier New', monospace;
            font-size: 14px;
            line-height: 1.6;
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #dee2e6;
        }
        .diff-line {
            padding: 2px 8px;
            margin: 1px 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .diff-header {
            background-color: #e9ecef;
            padding: 10px;
            margin: 10px 0;
            border-radius: 4px;
            font-weight: bold;
            color: #495057;
        }
        .diff-added {
            background-color: #d4edda;
            color: #155724;
            border-left: 4px solid #28a745;
        }
        .diff-added::before {
            content: "+ ";
            color: #28a745;
            font-weight: bold;
        }
        .diff-removed {
            background-color: #f8d7da;
            color: #721c24;
            border-left: 4px solid #dc3545;
        }
        .diff-removed::before {
            content: "- ";
            color: #dc3545;
            font-weight: bold;
        }
        .diff-context {
            background-color: #ffffff;
            color: #212529;
        }
        .diff-meta {
            color: #6c757d;
            font-style: italic;
            padding: 5px 8px;
        }
    </style>
    <div class="diff-container">
    """)
    
    has_changes = False
    # Process diff lines
    for line in diff:
        if line.startswith('---') or line.startswith('+++'):
            # File header
            html_diff.append(f'<div class="diff-meta">{line}</div>')
        elif line.startswith('@@'):
            # Hunk header
            html_diff.append(f'<div class="diff-header">{line}</div>')
        elif line.startswith('+') and not line.startswith('+++'):
            # Added line
            content = line[1:].rstrip('\n')
            # Escape HTML
            content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_diff.append(f'<div class="diff-line diff-added">{content}</div>')
            has_changes = True
        elif line.startswith('-') and not line.startswith('---'):
            # Removed line
            content = line[1:].rstrip('\n')
            # Escape HTML
            content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_diff.append(f'<div class="diff-line diff-removed">{content}</div>')
            has_changes = True
        elif line.startswith(' '):
            # Context line (unchanged)
            content = line[1:].rstrip('\n')
            # Escape HTML
            content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_diff.append(f'<div class="diff-line diff-context">{content}</div>')
    
    if not has_changes:
        html_diff.append('<div class="diff-line diff-context">No differences found.</div>')

    html_diff.append("</div>")
    
    return '\n'.join(html_diff)

def create_word_level_diff(original_text: str, final_text: str) -> str:
    """Create a word-level diff showing inline changes with colors."""
    
    # Split into words for more granular diff
    original_words = original_text.split()
    final_words = final_text.split()
    
    # Use SequenceMatcher for word-level diff
    matcher = difflib.SequenceMatcher(None, original_words, final_words)
    
    html_diff = []
    html_diff.append("""
    <style>
        .word-diff-container {
            font-family: Arial, sans-serif;
            font-size: 15px;
            line-height: 1.8;
            padding: 20px;
            background-color: #ffffff;
            border-radius: 8px;
            border: 1px solid #dee2e6;
        }
        .word-diff-added {
            background-color: #d4edda;
            color: #155724;
            padding: 2px 4px;
            border-radius: 3px;
            text-decoration: none;
        }
        .word-diff-added::before {
            content: "+";
            color: #28a745;
            font-weight: bold;
            margin-right: 4px;
        }
        .word-diff-removed {
            background-color: #f8d7da;
            color: #721c24;
            padding: 2px 4px;
            border-radius: 3px;
            text-decoration: line-through;
        }
        .word-diff-removed::before {
            content: "-";
            color: #dc3545;
            font-weight: bold;
            margin-right: 4px;
        }
        .word-diff-unchanged {
            color: #212529;
        }
        .diff-section {
            margin-bottom: 20px;
            padding: 15px;
            background-color: #f8f9fa;
            border-radius: 5px;
        }
    </style>
    <div class="word-diff-container">
    """)
    
    has_changes = False
    # Process opcodes
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            # Unchanged text
            unchanged = ' '.join(original_words[i1:i2])
            html_diff.append(f'<span class="word-diff-unchanged">{unchanged} </span>')
        elif tag == 'delete':
            # Removed text
            removed = ' '.join(original_words[i1:i2])
            removed_escaped = removed.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_diff.append(f'<span class="word-diff-removed">{removed_escaped}</span> ')
            has_changes = True
        elif tag == 'insert':
            # Added text
            added = ' '.join(final_words[j1:j2])
            added_escaped = added.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_diff.append(f'<span class="word-diff-added">{added_escaped}</span> ')
            has_changes = True
        elif tag == 'replace':
            # Replaced text (show both)
            removed = ' '.join(original_words[i1:i2])
            added = ' '.join(final_words[j1:j2])
            removed_escaped = removed.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            added_escaped = added.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            html_diff.append(f'<span class="word-diff-removed">{removed_escaped}</span> ')
            html_diff.append(f'<span class="word-diff-added">{added_escaped}</span> ')
            has_changes = True
    
    if not has_changes:
        html_diff.append('<span class="word-diff-unchanged">No differences found.</span>')

    html_diff.append("</div>")
    
    return '\n'.join(html_diff)

def initialize_report_editing_state():
    """Initialize session state for report editing (Human-in-the-loop: edit text only, preserve charts/images)."""
    if 'report_original_text' not in st.session_state:
        st.session_state.report_original_text = None
    if 'report_edited_text' not in st.session_state:
        st.session_state.report_edited_text = None
    if 'report_final_text' not in st.session_state:
        st.session_state.report_final_text = None
    if 'report_final_html' not in st.session_state:
        st.session_state.report_final_html = None  # Final report HTML with charts/images preserved
    if 'report_segments' not in st.session_state:
        st.session_state.report_segments = None  # Parsed segments (text + chart) for HITL
    if 'report_original_html' not in st.session_state:
        st.session_state.report_original_html = None  # Full report HTML for merging
    if 'report_edit_mode' not in st.session_state:
        st.session_state.report_edit_mode = False
    if 'report_sentence_feedbacks' not in st.session_state:
        st.session_state.report_sentence_feedbacks = []
    if 'report_show_comparison' not in st.session_state:
        st.session_state.report_show_comparison = False
    if 'report_general_feedback' not in st.session_state:
        st.session_state.report_general_feedback = ""
    if 'report_editors_initialized' not in st.session_state:
        st.session_state.report_editors_initialized = False
    if 'formatted_editor' not in st.session_state:
        st.session_state.formatted_editor = ""
    if 'plain_editor' not in st.session_state:
        st.session_state.plain_editor = ""
    if 'report_last_edit_source' not in st.session_state:
        st.session_state.report_last_edit_source = None
    if 'report_human_edit_analysis' not in st.session_state:
        st.session_state.report_human_edit_analysis = None  # List of {original, edited, feedback} from STEP 1+2
    if 'report_segment_edits' not in st.session_state:
        st.session_state.report_segment_edits = None  # Per-segment content for formatted editing [str, ...]
    if 'report_source_mtime' not in st.session_state:
        st.session_state.report_source_mtime = None
    if 'report_pending_editor_sync' not in st.session_state:
        st.session_state.report_pending_editor_sync = None
    if 'report_feedback_dialog_open' not in st.session_state:
        st.session_state.report_feedback_dialog_open = False
    if 'report_feedback_dialog_text' not in st.session_state:
        st.session_state.report_feedback_dialog_text = ""
    if 'last_selection_timestamp' not in st.session_state:
        st.session_state.last_selection_timestamp = 0
    if 'last_edit_timestamp' not in st.session_state:
        st.session_state.last_edit_timestamp = 0
    if 'report_feedback_input_pending_reset' not in st.session_state:
        st.session_state.report_feedback_input_pending_reset = False
    if 'report_feedback_input_nonce' not in st.session_state:
        st.session_state.report_feedback_input_nonce = 0
    if 'report_fullscreen_open' not in st.session_state:
        st.session_state.report_fullscreen_open = False


def _current_report_mtime() -> Optional[float]:
    """Return the current report file modified time if it exists."""
    try:
        if REPORT_HTML.is_file():
            return REPORT_HTML.stat().st_mtime
    except OSError:
        return None
    return None


def reset_report_session_for_new_source() -> None:
    """Clear derived report state so a freshly generated report reloads immediately."""
    st.session_state.report_original_text = None
    st.session_state.report_edited_text = None
    st.session_state.report_final_text = None
    st.session_state.report_final_html = None
    st.session_state.report_segments = None
    st.session_state.report_original_html = None
    st.session_state.report_sentence_feedbacks = []
    st.session_state.report_show_comparison = False
    st.session_state.report_general_feedback = ""
    st.session_state.report_editors_initialized = False
    st.session_state.formatted_editor = ""
    st.session_state.plain_editor = ""
    st.session_state.report_last_edit_source = None
    st.session_state.report_human_edit_analysis = None
    st.session_state.report_segment_edits = None
    st.session_state.report_source_mtime = None
    st.session_state.report_pending_editor_sync = ""
    st.session_state.report_feedback_dialog_open = False
    st.session_state.report_feedback_dialog_text = ""
    st.session_state.last_selection_timestamp = 0
    st.session_state.report_feedback_input_pending_reset = True
    st.session_state.report_feedback_input_nonce = 0


def _queue_editor_refresh(text: str) -> None:
    """Schedule the plain-text editor to refresh on the next rerun before widget creation."""
    synced_text = text or ""
    st.session_state.plain_editor = synced_text
    st.session_state.report_edited_text = synced_text
    st.session_state.report_pending_editor_sync = synced_text


def _apply_pending_editor_refresh() -> None:
    """Apply deferred editor updates before the text-area widget is instantiated."""
    pending_text = st.session_state.get("report_pending_editor_sync")
    if pending_text is None:
        return
    st.session_state.plain_editor = pending_text
    st.session_state.plain_editor_main = pending_text
    st.session_state.report_pending_editor_sync = None


def _queue_feedback_input_reset() -> None:
    """Clear the dialog feedback input on the next rerun before the widget is created."""
    st.session_state.report_feedback_input_pending_reset = True
    st.session_state.report_feedback_input_nonce = st.session_state.get("report_feedback_input_nonce", 0) + 1


def _apply_pending_feedback_input_reset() -> None:
    """Apply any queued dialog input reset before the text-area widget is instantiated."""
    if not st.session_state.get("report_feedback_input_pending_reset"):
        return
    st.session_state.report_feedback_input_pending_reset = False


def _current_feedback_input_key() -> str:
    """Return the active widget key for the sentence-feedback dialog input."""
    return f"dialog_sentence_feedback_input_{st.session_state.get('report_feedback_input_nonce', 0)}"

def _load_report_into_session(force_editor_reset: bool = False) -> None:
    """Load the generated report into session state so edit/HITL actions read the same source."""
    html_content = load_full_report_html()
    if not html_content or html_content == "<h3>No report generated yet.</h3>":
        return

    previous_original = st.session_state.get("report_original_text")
    segments = parse_report_to_segments(html_content)
    editable_text = build_editable_report_text(segments)

    st.session_state.report_original_html = html_content
    st.session_state.report_segments = segments
    st.session_state.report_original_text = editable_text
    st.session_state.report_source_mtime = _current_report_mtime()

    should_reset_editor = force_editor_reset or st.session_state.get("report_edited_text") is None
    if previous_original and st.session_state.get("report_edited_text") == previous_original:
        should_reset_editor = True

    if should_reset_editor:
        _queue_editor_refresh(editable_text)

def _sync_report_text_from_editor(clear_dependent_state: bool = False) -> str:
    """Persist the live textbox contents before analyze/regenerate actions run."""
    latest_text = st.session_state.get("plain_editor_main")
    if latest_text is None:
        latest_text = (
            st.session_state.get("report_edited_text")
            or st.session_state.get("plain_editor")
            or st.session_state.get("report_original_text")
            or ""
        )

    previous_text = st.session_state.get("report_edited_text") or ""
    st.session_state.plain_editor = latest_text
    st.session_state.report_edited_text = latest_text

    if clear_dependent_state and latest_text.strip() != previous_text.strip():
        st.session_state.report_final_text = None
        st.session_state.report_final_html = None
        st.session_state.report_human_edit_analysis = None

    return latest_text

def _init_segment_edits_from_edited_text(segments: List[Dict], edited_text: str) -> List[str]:
    """Build list of per-segment content strings from full edited text (for sectioned editing)."""
    updated = parse_editable_text_back_to_segments(edited_text, segments)
    out = []
    for i, seg in enumerate(updated):
        if seg.get("type") == "text":
            out.append(seg.get("content", ""))
        else:
            out.append(f"Title: {seg.get('title', '')}\nExplanation: {seg.get('explanation', '')}")
    return out

def _rebuild_report_edited_text_from_segment_edits(segments: List[Dict], segment_edits: List[str]) -> str:
    """Rebuild full report_edited_text from per-segment edits (for sectioned editing)."""
    parts = []
    for i, seg in enumerate(segments):
        if i >= len(segment_edits):
            break
        if seg.get("type") == "text":
            parts.append(segment_edits[i])
        else:
            parts.append(f"\n\n{_CHART_PLACEHOLDER_PREFIX}{i}{_CHART_PLACEHOLDER_SUFFIX}\n{segment_edits[i]}\n")
    return "\n".join(parts).strip()

def generate_final_report_with_feedback(
    original_text: str,
    edited_text: Optional[str],
    general_feedback: str,
    sentence_feedbacks: List[Dict],
    edit_analysis: Optional[List[Dict]] = None,
) -> str:
    """Generate final report version incorporating all feedback and edits via Gemini. Text only; graphs/images unchanged."""
    gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    if not gemini_api_key:
        st.warning("GEMINI_API_KEY not found. Using fallback feedback merge.")
        return apply_feedback_fallback(original_text, edited_text, general_feedback, sentence_feedbacks)

    # Apply sentence-level feedback via Gemini first to ensure visible changes
    base_text = edited_text if edited_text else original_text
    if sentence_feedbacks:
        base_text = apply_sentence_feedbacks_with_gemini(base_text, sentence_feedbacks, gemini_api_key)
        edited_text = base_text

    # Important behavior: sentence-level feedback should only rewrite the selected text.
    # A full-report rewrite is triggered only when the user provides broad/general guidance.
    needs_full_rewrite = bool((general_feedback or "").strip())
    if not needs_full_rewrite:
        return base_text

    # Combine all feedback (including Human Edit Analysis when available)
    feedback_parts = []
    if edit_analysis:
        feedback_parts.append("Human Edit Analysis (incorporate these refinements):")
        for e in edit_analysis:
            feedback_parts.append(f"  - Original: \"{e.get('original', '')[:200]}\" â†’ Edited: \"{e.get('edited', '')[:200]}\" | Feedback: {e.get('feedback', '')}")
    if general_feedback:
        feedback_parts.append(f"General Feedback: {general_feedback}")
    if sentence_feedbacks:
        for sf in sentence_feedbacks:
            feedback_parts.append(
                f"Feedback on '{sf.get('text', '')}': "
                f"{sf.get('feedback', '')}"
            )

    combined_feedback = _build_feedback_context(general_feedback, sentence_feedbacks, edit_analysis)

    has_chart_placeholders = _CHART_PLACEHOLDER_PREFIX in (original_text or "") or _CHART_PLACEHOLDER_PREFIX in (edited_text or "")
    placeholder_instruction = ""
    if has_chart_placeholders:
        placeholder_instruction = """
IMPORTANT: The report contains chart placeholders like ---CHART_0---, ---CHART_1---, etc. with "Title:" and "Explanation:" lines.
You MUST keep every ---CHART_N--- block exactly (same placeholder and structure). You may improve only the Explanation text within each block; do not remove or reorder placeholders.
"""
    text_only_rule = """
STRICT: You are a report refinement assistant. Modify ONLY textual content. Do NOT change, suggest changes to, or reference modifying any images, charts, HTML graph components, or embedded visual elements. They are preserved exactly as provided.
"""
    try:
        from google import genai
        client = genai.Client(api_key=gemini_api_key)
    except Exception as e:
        st.error(f"Error initializing Gemini: {str(e)}. Using fallback feedback merge.")
        return apply_feedback_fallback(original_text, edited_text, general_feedback, sentence_feedbacks)

    if len(base_text or "") > MAX_REPORT_CHUNK_CHARS:
        st.info("Large report detected, so the full report will be sent to Gemini in ordered chunks.")
        return _rewrite_large_report_in_chunks(client, base_text, combined_feedback, placeholder_instruction)

    prompt = f"""
You are a professional report editor. Based on the following original report and user feedback, generate an improved final version.
{text_only_rule}
{placeholder_instruction}

Original Report:
{original_text}

User Edits (if provided):
{edited_text if edited_text else "No direct edits provided."}

User Feedback:
{combined_feedback}

Please generate the final version of the report that:
1. Incorporates all user edits if provided
2. Addresses all feedback points
3. Maintains the same structure and format
4. Improves clarity, accuracy, and completeness based on feedback
5. Keeps the writing concise and executive-friendly. Avoid unnecessary repetition and keep summaries, drivers, and recommendations compact unless the feedback explicitly asks for more detail.

Return only the improved report text, without any additional commentary.
""".strip()

    try:
        full_prompt = prompt
        response = _generate_gemini_content_with_retry(client, full_prompt, system_instruction="You are a precise report editor.")
        return response.text.strip()
    except Exception as e:
        st.error(f"Error generating final report: {str(e)}. Using fallback feedback merge.")
        return apply_feedback_fallback(original_text, edited_text, general_feedback, sentence_feedbacks)

def apply_sentence_feedbacks_with_gemini(
    base_text: str,
    sentence_feedbacks: List[Dict],
    gemini_api_key: str,
) -> str:
    """Rewrite specific sentences using Gemini based on feedback."""
    feedback_items = []
    for sf in sentence_feedbacks:
        text = (sf.get("text") or "").strip()
        feedback = (sf.get("feedback") or "").strip()
        if text and feedback:
            feedback_items.append({
                "original": text,
                "feedback": feedback,
            })

    if not feedback_items:
        return base_text

    prompt = f"""
You are a professional report editor. For each item below, rewrite the given text according to the user's feedback.

CRITICAL: Apply the feedback fully and literally.
- If the feedback says "describe in detail" or "more detail": expand the text into a detailed paragraph. Explain what is shown, why it matters, and how the parts relate. Use multiple sentences as needed. Do not keep it short.
- If the feedback asks to simplify, shorten, or clarify: do exactly that.
- If the feedback asks to add numbers or specifics: add relevant quantification where appropriate (without inventing data).
- Preserve the original meaning and facts; only change style, length, or emphasis as the feedback requests.

The "rewritten" output can be one sentence OR multiple sentences (a full paragraph) when the feedback asks for more detail or elaboration. Make the rewritten text production-ready for a report.

Return ONLY valid JSON (no markdown, no code block), an array of objects with keys "original" and "rewritten":
[
  {{"original": "exact original text", "rewritten": "your rewritten text (can be longer than original)"}}
]

Input (sentences and feedback):
{json.dumps(feedback_items, ensure_ascii=False)}
""".strip()

    try:
        from google import genai
        client = genai.Client(api_key=gemini_api_key)
        response = _generate_gemini_content_with_retry(
            client, 
            prompt, 
            system_instruction="You apply feedback literally and return only valid JSON. For 'describe in detail' you expand into a full detailed paragraph."
        )
        content = response.text
        # Try to extract JSON array even if the model wraps it
        match = re.search(r"\[[\s\S]*\]", content)
        json_text = match.group(0) if match else content
        rewrites = json.loads(json_text)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error in apply_sentence_feedbacks_with_gemini: {e}")
        # If JSON parse or API fails, fall back to original text
        return base_text

    updated_text = base_text
    for item in rewrites:
        original = (item.get("original") or "").strip()
        rewritten = (item.get("rewritten") or "").strip()
        if not original or not rewritten:
            continue
        # Try exact match first
        pattern = re.escape(original)
        if re.search(pattern, updated_text):
            updated_text = re.sub(pattern, rewritten, updated_text, count=1)
            continue
        # Fallback: match with flexible whitespace (same words, any whitespace between)
        words = original.split()
        if words:
            pattern_flex = r"\s+".join(re.escape(w) for w in words)
            match = re.search(pattern_flex, updated_text, re.DOTALL)
            if match:
                updated_text = updated_text[: match.start()] + rewritten + updated_text[match.end() :]

    return updated_text

def apply_feedback_fallback(
    original_text: str,
    edited_text: Optional[str],
    general_feedback: str,
    sentence_feedbacks: List[Dict],
) -> str:
    """Fallback merge that annotates the report with feedback notes."""
    base_text = edited_text if edited_text else original_text
    notes: List[str] = []

    for sf in sentence_feedbacks:
        target_text = (sf.get("text") or "").strip()
        feedback = (sf.get("feedback") or "").strip()
        if not target_text or not feedback:
            continue
        notes.append(f"- For text: \"{target_text[:120]}\" -> {feedback}")

    if general_feedback:
        notes.append(f"- General feedback: {general_feedback}")

    if notes:
        return base_text.rstrip() + "\n\n## Feedback Notes\n" + "\n".join(notes)

    return base_text


def _store_final_report_output(final_report: str) -> None:
    """Persist the generated final report text and merged HTML."""
    st.session_state.report_final_text = final_report
    _queue_editor_refresh(final_report)
    segs = st.session_state.get("report_segments")
    orig_html = st.session_state.get("report_original_html")
    if segs and orig_html:
        updated_segs = parse_editable_text_back_to_segments(final_report, segs)
        st.session_state.report_final_html = build_final_report_html_preserving_charts(
            orig_html, segs, updated_segs
        )
    else:
        st.session_state.report_final_html = None


def _run_report_regeneration(success_prefix: str) -> None:
    """Generate a final report from the latest editor state and queued feedback."""
    original = st.session_state.report_original_text or ""
    edited = _sync_report_text_from_editor(clear_dependent_state=True)
    general = st.session_state.report_general_feedback
    sentence_feedbacks = st.session_state.report_sentence_feedbacks
    edit_analysis = st.session_state.get("report_human_edit_analysis")

    if not original:
        st.error("No original report found. Please generate a report first.")
        return

    needs_full_rewrite = bool((general or "").strip())

    if needs_full_rewrite and (edited or "").strip() and (edited or "").strip() != original.strip() and not edit_analysis:
        edits = identify_human_edits(original, edited or original)
        if edits:
            st.session_state.report_human_edit_analysis = generate_human_edit_analysis(edits)
            edit_analysis = st.session_state.report_human_edit_analysis
    elif not needs_full_rewrite:
        edit_analysis = None

    feedback_count = len(sentence_feedbacks) if sentence_feedbacks else 0
    if feedback_count > 0 or general or edit_analysis:
        feedback_msg = []
        if edit_analysis:
            feedback_msg.append(f"{len(edit_analysis)} edit(s) analyzed")
        if general:
            feedback_msg.append("general feedback")
        if feedback_count > 0:
            feedback_msg.append(f"{feedback_count} sentence-level feedback(s)")
        st.info(f"Using {', '.join(feedback_msg)} to refine the final report while preserving charts and visuals.")
    else:
        st.info("No explicit feedback was provided, so the current edited draft will be promoted as the final report.")

    final_report = generate_final_report_with_feedback(
        original,
        edited,
        general,
        sentence_feedbacks,
        edit_analysis=edit_analysis,
    )
    _store_final_report_output(final_report)

    message = success_prefix
    if sentence_feedbacks and (os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")):
        message += f" {len(sentence_feedbacks)} sentence(s) were rewritten with Gemini."
    st.success(message)
    st.rerun()


def _handle_report_selection(sel_data) -> None:
    if not sel_data:
        return

    event_type = sel_data.get("type", "selection")  # backwards-compat default

    # ── Inline edit event: user edited text directly in the report ──
    if event_type == "edit":
        edited_text = sel_data.get("edited_text", "")
        edit_ts = sel_data.get("edit_timestamp", 0)
        if edited_text and edit_ts != st.session_state.get("last_edit_timestamp", 0):
            st.session_state.last_edit_timestamp = edit_ts
            st.session_state.report_edited_text = edited_text
            st.session_state.plain_editor = edited_text
        return

    # ── Selection event: user right-clicked selected text for feedback ──
    current_text = sel_data.get("text", "")
    current_ts = sel_data.get("timestamp", 0)
    if current_text and current_ts != st.session_state.last_selection_timestamp:
        st.session_state.last_selection_timestamp = current_ts
        st.session_state.report_feedback_dialog_text = current_text
        st.session_state.report_feedback_dialog_open = True
        _queue_feedback_input_reset()


def _render_selectable_report_preview(report_html: str, height: int = 900, key: str = "report_html_text_selector") -> None:
    component_path = _BASE_DIR / "st_text_selector"
    try:
        text_selector = declare_component("text_selector", path=str(component_path))
        sel_data = text_selector(
            html=_make_report_html_responsive(report_html),
            height=height,
            key=key,
        )
        _handle_report_selection(sel_data)
    except Exception:
        components.html(_make_report_html_responsive(report_html), height=height, scrolling=True)


def _render_fullscreen_report_dialog(report_html: str) -> None:
    @st.dialog("Full-screen report", width="large")
    def fullscreen_dialog():
        st.markdown(
            """
<style>
div[data-testid="stDialog"],
div[role="dialog"] {
    width: min(96vw, 1700px) !important;
    max-width: min(96vw, 1700px) !important;
}
div[data-testid="stDialog"] > div,
div[role="dialog"] > div {
    max-height: 94vh !important;
}
</style>
""",
            unsafe_allow_html=True,
        )
        # Reset the flag so the native × close button works correctly.
        # The dialog being open IS the state; the flag is just a trigger.
        st.session_state.report_fullscreen_open = False
        st.caption("Full-screen report preview")
        components.html(_make_report_html_responsive(report_html), height=820, scrolling=True)


    fullscreen_dialog()


def render_report_editing_ui():
    """Render the editing and feedback UI for the report."""
    st.markdown("---")
    st.markdown(
        """
<div class="report-editor-shell">
    <div class="report-editor-kicker">Edit Workspace</div>
    <div class="report-editor-header">
        <div>
            <div class="report-editor-title">Refine the report before publishing the final draft</div>
            <div class="report-editor-copy">Review the narrative, add high-level guidance, collect sentence-level rewrite requests, and regenerate a polished final report while preserving the embedded charts.</div>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1.2, 1.2], gap="medium")
    with col1:
        if st.button("Compare Versions", width="stretch"):
            st.session_state.report_show_comparison = not st.session_state.report_show_comparison
            st.rerun()

    with col2:
        if st.button("Analyze Draft Changes", width="stretch", key="report_analyze_top"):
            orig = st.session_state.report_original_text or ""
            edited = _sync_report_text_from_editor(clear_dependent_state=True)
            if not orig or not edited:
                st.warning("Load the report and make some edits first.")
            elif orig.strip() == (edited or "").strip():
                st.info("No edits detected yet.")
                st.session_state.report_human_edit_analysis = []
            else:
                with st.spinner("Reviewing your draft changes..."):
                    edits = identify_human_edits(orig, edited or orig)
                    st.session_state.report_human_edit_analysis = generate_human_edit_analysis(edits) if edits else []
                st.success("Draft change analysis updated.")
                st.rerun()

    with st.expander("Original report text", expanded=False):
        st.text_area(
            "Original report",
            value=st.session_state.report_original_text or "",
            height=280,
            disabled=True,
            key="original_view",
            label_visibility="collapsed",
        )
    
    feedback_col1, feedback_col2 = st.columns([1.35, 1], gap="large")
    with feedback_col1:
        st.markdown(
            """
<div class="report-feedback-card">
    <h4>Overall guidance</h4>
    <p>Use this area for broad instructions such as tightening the executive summary, improving clarity, adding specifics, or expanding the business implications.</p>
</div>
""",
            unsafe_allow_html=True,
        )
        general_feedback = st.text_area(
            "Provide overall feedback for the report:",
            value=st.session_state.report_general_feedback,
            placeholder="E.g., make the executive summary sharper, add quantified findings, or explain the risk areas in more detail.",
            height=150,
            key="general_feedback_input",
        )
        st.session_state.report_general_feedback = general_feedback

    with feedback_col2:
        st.markdown(
            """
<div class="report-feedback-card">
    <h4>Sentence-level feedback</h4>
    <p>Select text from the report preview above and use the dialog to add a precise rewrite request for that sentence or paragraph.</p>
</div>
""",
            unsafe_allow_html=True,
        )

    @st.dialog("Provide Feedback on Selection")
    def feedback_dialog():
        selected_text = st.session_state.get("report_feedback_dialog_text", "").strip()
        st.markdown(f"**Selected Text:**\n\n> {selected_text}")
        _apply_pending_feedback_input_reset()
        feedback_input_key = _current_feedback_input_key()

        with st.form("report_sentence_feedback_form", clear_on_submit=False):
            st.text_area(
                "Feedback for this text:",
                placeholder="E.g., make this more concise, add more detail, or explain the business impact.",
                height=150,
                key=feedback_input_key,
            )
            submit_col, cancel_col = st.columns(2, gap="medium")
            with submit_col:
                add_feedback = st.form_submit_button("Add Feedback", use_container_width=True)
            with cancel_col:
                cancel_feedback = st.form_submit_button("Cancel", use_container_width=True)

        if add_feedback:
            sentence_feedback = st.session_state.get(feedback_input_key, "").strip()
            if selected_text and sentence_feedback:
                st.session_state.report_sentence_feedbacks.append({
                    "text": selected_text,
                    "feedback": sentence_feedback
                })
                st.session_state.report_feedback_dialog_open = False
                st.session_state.report_feedback_dialog_text = ""
                _queue_feedback_input_reset()
                st.success("Sentence feedback added!")
                st.rerun()
            else:
                st.warning("Please provide feedback before submitting.")

        if cancel_feedback:
            st.session_state.report_feedback_dialog_open = False
            st.session_state.report_feedback_dialog_text = ""
            _queue_feedback_input_reset()
            st.rerun()

    component_path = _BASE_DIR / "st_text_selector"
    try:
        text_selector = declare_component("text_selector", path=str(component_path))
        sel_data = text_selector(key="report_text_selector")
    except Exception:
        sel_data = None

    if sel_data and sel_data.get("text"):
        current_text = sel_data.get("text")
        current_ts = sel_data.get("timestamp")

        if current_ts != st.session_state.last_selection_timestamp:
            st.session_state.last_selection_timestamp = current_ts
            st.session_state.report_feedback_dialog_text = current_text
            st.session_state.report_feedback_dialog_open = True
            _queue_feedback_input_reset()

    # Only open the feedback dialog when the fullscreen dialog is NOT already open.
    # Streamlit allows only one @st.dialog to be open per script run.
    if (
        st.session_state.get("report_feedback_dialog_open")
        and st.session_state.get("report_feedback_dialog_text")
        and not st.session_state.get("report_fullscreen_open")
    ):
        feedback_dialog()

    st.markdown(
        """
<div class="report-action-band">
    <strong>Feedback queue</strong>
    <span>Anything collected here will be applied when you regenerate the final report. The chart layout and embedded visuals stay preserved.</span>
</div>
""",
        unsafe_allow_html=True,
    )

    if st.session_state.report_sentence_feedbacks:
        st.markdown(f"**Collected Sentence Feedback ({len(st.session_state.report_sentence_feedbacks)}):**")
        for i, sf in enumerate(st.session_state.report_sentence_feedbacks):
            col1, col2 = st.columns([4.5, 1], gap="medium")
            with col1:
                st.write(f'**Text:** \"{sf["text"][:160]}\"')
                st.write(f'**Feedback:** {sf["feedback"]}')
            with col2:
                if st.button("Remove", key=f"report_remove_sf_{i}", width="stretch"):
                    st.session_state.report_sentence_feedbacks.pop(i)
                    st.rerun()
        if st.button("Clear All Feedback", type="secondary", key="report_clear_feedbacks", width="stretch"):
            st.session_state.report_sentence_feedbacks = []
            st.success("All feedbacks cleared!")
    else:
        st.info("No sentence-level feedback has been collected yet.")

    if False:
        """
        with st.popover("Feedback", width="stretch"):
        st.markdown("#### General Feedback")
        general_feedback = st.text_area(
            "Provide overall feedback for the report:",
            value=st.session_state.report_general_feedback,
            placeholder="E.g., 'Make the executive summary more concise', 'Add more statistical details', 'Improve the insights section', etc.",
            height=120,
            key="general_feedback_input"
        )
        st.session_state.report_general_feedback = general_feedback

        st.subheader("2. Add Sentence-Level Feedback (Optional)")
        st.caption("Select any text from the report above, and right-click on it to automatically launch a feedback dialog.")
        
        @st.dialog("Provide Feedback on Selection")
        def feedback_dialog(text):
            st.markdown(f"**Selected Text:**\n\n> {text}")
            
            # Use session state local variables to hold the feedback text
            sentence_feedback = st.text_area(
                "Feedback for this text:",
                placeholder="E.g., Make this more concise, add more detail...",
                height=150,
                key="dialog_sentence_feedback_input"
            )
            
            if st.button("Add Feedback"):
                if text and sentence_feedback:
                    st.session_state.report_sentence_feedbacks.append({
                        "text": text,
                        "feedback": sentence_feedback
                    })
                    st.success("Sentence feedback added!")
                    import time
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.warning("Please provide feedback before submitting.")
        
        # Initialize the text selector component
        component_path = _BASE_DIR / "st_text_selector"
        try:
            text_selector = declare_component("text_selector", path=str(component_path))
            sel_data = text_selector(key="report_text_selector")
        except Exception:
            sel_data = None
            
        if sel_data and sel_data.get("text"):
            current_text = sel_data.get("text")
            current_ts = sel_data.get("timestamp")
            
            if "last_selection_timestamp" not in st.session_state:
                st.session_state.last_selection_timestamp = 0
                
            if current_ts != st.session_state.last_selection_timestamp:
                st.session_state.last_selection_timestamp = current_ts
                feedback_dialog(current_text)
        if st.session_state.report_sentence_feedbacks:
            st.markdown(f"**Collected Sentence Feedback ({len(st.session_state.report_sentence_feedbacks)}):**")
            for i, sf in enumerate(st.session_state.report_sentence_feedbacks):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.write(f"**Text:** \"{sf['text'][:120]}\"")
                    st.write(f"**Feedback:** {sf['feedback']}")
                with col2:
                    if st.button("Remove", key=f"report_remove_sf_{i}"):
                        st.session_state.report_sentence_feedbacks.pop(i)
                        st.rerun()
            if st.button("Clear All Feedback", type="secondary", key="report_clear_feedbacks"):
                st.session_state.report_sentence_feedbacks = []
                st.success("All feedbacks cleared!")
        else:
            st.info("No sentence-level feedbacks collected yet.")

        st.markdown("---")
        if st.button("Generate Final Report", type="primary", width="stretch"):
            original = st.session_state.report_original_text or ""
            edited = _sync_report_text_from_editor(clear_dependent_state=True)
            general = st.session_state.report_general_feedback
            sentence_feedbacks = st.session_state.report_sentence_feedbacks

            if original:
                with st.spinner("Generating final report with OpenAI..."):
                    final_report = generate_final_report_with_feedback(
                        original, edited, general, sentence_feedbacks
                    )
                    st.session_state.report_final_text = final_report
                    segs = st.session_state.get("report_segments")
                    orig_html = st.session_state.get("report_original_html")
                    if segs and orig_html:
                        updated_segs = parse_editable_text_back_to_segments(final_report, segs)
                        st.session_state.report_final_html = build_final_report_html_preserving_charts(
                            orig_html, segs, updated_segs
                        )
                    else:
                        st.session_state.report_final_html = None
                    st.success("✅ Final report generated successfully!")
                    st.rerun()
            else:
                st.error("âŒ No original report found. Please generate a report first.")
    
        """

    # --- HITL: 1. Human Edit Analysis (identify edits + feedback per sentence) ---
    st.markdown("---")
    st.markdown("### 1. Human Edit Analysis")
    st.caption("Compare original vs your edits; get feedback per modified sentence. Only textual content is analyzed; charts and images are never modified.")
    if st.button("Analyze my edits", key="hitl_analyze_edits"):
        orig = st.session_state.report_original_text or ""
        edited = _sync_report_text_from_editor(clear_dependent_state=True)
        if not orig or not edited:
            st.warning("Load the report and make some edits first.")
        elif orig.strip() == (edited or "").strip():
            st.info("No edits detected. Original and current text are the same.")
            st.session_state.report_human_edit_analysis = []
        else:
            with st.spinner("Identifying edits and generating feedback..."):
                edits = identify_human_edits(orig, edited or orig)
                if not edits:
                    st.info("No distinct text changes detected.")
                    st.session_state.report_human_edit_analysis = []
                else:
                    st.session_state.report_human_edit_analysis = generate_human_edit_analysis(edits)
                    st.success(f"Found {len(st.session_state.report_human_edit_analysis)} edit(s). Feedback generated.")
            st.rerun()

    if st.session_state.get("report_human_edit_analysis"):
        analysis = st.session_state.report_human_edit_analysis
        st.markdown("**Edited sentences and feedback:**")
        for i, item in enumerate(analysis):
            orig_preview = (item.get("original") or "").strip()[:80]
            if len((item.get("original") or "").strip()) > 80:
                orig_preview += "..."
            with st.expander(f"Edit {i + 1}: \"{orig_preview}\"", expanded=(i < 2)):
                st.markdown("**Original:**")
                st.text(item.get("original", ""))
                st.markdown("**Edited:**")
                st.text(item.get("edited", ""))
                st.markdown("**Feedback:**")
                st.info(item.get("feedback", "No further refinement needed."))

    # Comparison view with git-style diff
    if st.session_state.report_show_comparison:
        st.markdown("---")
        st.markdown("### Version Comparison")
        
        # Get current version for comparison
        current_text = st.session_state.report_final_text or st.session_state.report_edited_text or st.session_state.report_original_text or ""
        
        if st.session_state.report_original_text and current_text:
            if st.session_state.report_original_text.strip() == current_text.strip():
                st.info("No differences found between the original and current version.")
                return
            # Diff view mode selector
            diff_mode = st.radio(
                "Comparison Mode:",
                ["Git-Style Diff (Line-by-Line)", "Word-Level Diff (Inline Changes)", "Side-by-Side View"],
                horizontal=True,
                key="edit_diff_mode_selector"
            )
            
            if diff_mode == "Git-Style Diff (Line-by-Line)":
                # Git-style line-by-line diff
                diff_html = create_git_style_diff(
                    st.session_state.report_original_text,
                    current_text
                )
                components.html(diff_html, height=600, scrolling=True)
            
            elif diff_mode == "Word-Level Diff (Inline Changes)":
                # Word-level inline diff
                word_diff_html = create_word_level_diff(
                    st.session_state.report_original_text,
                    current_text
                )
                components.html(word_diff_html, height=600, scrolling=True)
            
            else:
                # Side-by-side view
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("#### Original Report")
                    st.text_area(
                        "Original",
                        value=st.session_state.report_original_text,
                        height=400,
                        key="original_report_diff_view",
                        disabled=True
                    )
                with col2:
                    st.markdown("#### Current Version")
                    st.text_area(
                        "Current",
                        value=current_text,
                        height=400,
                        key="current_report_diff_view",
                        disabled=True
                    )
        else:
            st.info("Original report is required for comparison.")
    
    # Generate final report button
    st.markdown("---")
    st.markdown("### Generate Final Report")
    
    # Show feedback summary before regenerate
    if st.session_state.report_sentence_feedbacks or st.session_state.report_general_feedback:
        with st.expander("Feedback Summary (used for regeneration)", expanded=True):
            if st.session_state.report_general_feedback:
                st.markdown("**General Feedback:**")
                st.info(st.session_state.report_general_feedback)
            if st.session_state.report_sentence_feedbacks:
                st.markdown(f"**Sentence-Level Feedbacks ({len(st.session_state.report_sentence_feedbacks)}):**")
                for i, sf in enumerate(st.session_state.report_sentence_feedbacks, 1):
                    st.markdown(f"{i}. **Text:** \"{sf['text'][:80]}...\" -> **Feedback:** {sf['feedback']}")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        if st.button("Regenerate with Feedback", type="primary", width="stretch"):
            with st.spinner("Analyzing edits, then generating final report (text only; charts/images preserved)..."):
                _run_report_regeneration("Final report regenerated successfully.")
                return
                if False:
                    original = st.session_state.report_original_text or ""
                edited = _sync_report_text_from_editor(clear_dependent_state=True)
                general = st.session_state.report_general_feedback
                sentence_feedbacks = st.session_state.report_sentence_feedbacks
                edit_analysis = st.session_state.get("report_human_edit_analysis")
                
                if original:
                    # STEP 1+2: If user edited text and we don't have analysis yet, run Human Edit Analysis
                    if (edited or "").strip() and (edited or "").strip() != original.strip() and not edit_analysis:
                        edits = identify_human_edits(original, edited or original)
                        if edits:
                            st.session_state.report_human_edit_analysis = generate_human_edit_analysis(edits)
                            edit_analysis = st.session_state.report_human_edit_analysis
                    
                    feedback_count = len(sentence_feedbacks) if sentence_feedbacks else 0
                    if feedback_count > 0 or general or edit_analysis:
                        feedback_msg = []
                        if edit_analysis:
                            feedback_msg.append(f"{len(edit_analysis)} edit(s) analyzed")
                        if general:
                            feedback_msg.append("general feedback")
                        if feedback_count > 0:
                            feedback_msg.append(f"{feedback_count} sentence-level feedback(s)")
                        st.info(f"Using {', '.join(feedback_msg)} to regenerate text only (graphs/images unchanged)...")
                    
                    # STEP 3: Regenerate final report (text only; structure and charts preserved)
                    final_report = generate_final_report_with_feedback(
                        original, edited, general, sentence_feedbacks, edit_analysis=edit_analysis
                    )
                    st.session_state.report_final_text = final_report
                    # Merge back into original HTML so charts/images are preserved exactly
                    segs = st.session_state.get("report_segments")
                    orig_html = st.session_state.get("report_original_html")
                    if segs and orig_html:
                        updated_segs = parse_editable_text_back_to_segments(final_report, segs)
                        st.session_state.report_final_html = build_final_report_html_preserving_charts(
                            orig_html, segs, updated_segs
                        )
                    else:
                        st.session_state.report_final_html = None
                    msg = "✅ Final report generated. Text refined; graphs and images unchanged."
                    if sentence_feedbacks and (os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")):
                        msg += f" {len(sentence_feedbacks)} sentence(s) rewritten with Gemini."
                    st.success(msg)
                    st.rerun()
                else:
                    st.error("âŒ No original report found. Please generate a report first.")
    with col2:
        if st.button("Clear All", type="secondary", width="stretch"):
            st.session_state.report_sentence_feedbacks = []
            st.session_state.report_general_feedback = ""
            st.session_state.report_human_edit_analysis = None
            orig = st.session_state.report_original_text
            _queue_editor_refresh(orig or "")
            st.session_state.report_final_text = None
            st.session_state.report_final_html = None
            st.success("All feedbacks and edits cleared!")
            st.rerun()

def render_report_tab(report_ready_key, report_visible_key):
    """Render the report viewer and keep the report visible once it exists."""

    initialize_report_editing_state()

    if st.session_state.get(report_ready_key):
        st.session_state[report_visible_key] = True
        current_report_mtime = _current_report_mtime()
        if (
            current_report_mtime is not None
            and current_report_mtime != st.session_state.get("report_source_mtime")
        ):
            reset_report_session_for_new_source()
        if (
            st.session_state.get("report_original_text") is None
            or st.session_state.get("report_segments") is None
            or st.session_state.get("report_original_html") is None
        ):
            _load_report_into_session(force_editor_reset=True)

    st.header("Interactive Analysis Report")

    if not st.session_state.get(report_ready_key):
        st.info("No report generated yet. Run analysis first.")
        return

    report_view_mode = st.radio(
        "Report workflow",
        ["View Report", "Edit and Feedback", "Final Report"],
        horizontal=True,
        label_visibility="collapsed",
        key="report_view_mode",
    )

    current_report_html = (
        st.session_state.get("report_final_html")
        or st.session_state.get("report_original_html")
        or load_full_report_html()
    )

    if st.session_state.get("mode") in {"dataset", "pbix"}:
        from chatbot_conversation import display_chatbot

        _, report_action_col = st.columns([3.2, 1], gap="large")
        with report_action_col:
            with st.popover("Ask about your report", width="stretch"):
                st.caption(
                    "Use the generated report text and extracted charts for follow-up questions while you review the report."
                )
                display_chatbot()
            if st.button("Open full-screen report", width="stretch", key="report_fullscreen_btn"):
                st.session_state.report_fullscreen_open = True
                st.rerun()

    # Only open the fullscreen dialog when the feedback dialog is NOT also queued.
    # Streamlit allows only one @st.dialog to be open per script run.
    feedback_also_queued = (
        st.session_state.get("report_feedback_dialog_open")
        and st.session_state.get("report_feedback_dialog_text")
    )
    if st.session_state.get("report_fullscreen_open") and not feedback_also_queued:
        _render_fullscreen_report_dialog(current_report_html)

    if report_view_mode == "View Report":
        btn_label = (
            "Open Dataset Chat Workspace"
            if st.session_state.get("mode") == "dataset"
            else "Open BI Chart Workspace"
        )
        if st.button(
            btn_label,
            width="stretch",
            key="report_workspace_btn_top"
        ):
            st.session_state.workspace = "dataset" if st.session_state.get("mode") == "dataset" else "pbix_chat"
            st.rerun()
            """

                with st.popover("💬 Ask about your report", width="stretch"):
            """
        st.markdown("---")
        components.html(_make_report_html_responsive(current_report_html), height=900, scrolling=True)

    elif report_view_mode == "Edit and Feedback":
        if st.session_state.report_original_text:
            report_html = (
                st.session_state.get("report_final_html")
                or st.session_state.get("report_original_html")
                or load_full_report_html()
            )
            current_text = st.session_state.report_edited_text or st.session_state.report_original_text

            if not st.session_state.report_editors_initialized:
                st.session_state.plain_editor = current_text
                st.session_state.report_editors_initialized = True

            def _sync():
                _sync_report_text_from_editor(clear_dependent_state=True)

            # ── Report is directly editable in the viewer below ─────────
            # The st_text_selector component now supports contenteditable;
            # right-click sends sentence feedback, typing sends edited_text.
            _render_selectable_report_preview(report_html, height=950, key="report_edit_selectable_preview")

            st.markdown("---")

            render_report_editing_ui()
        else:
            st.info("Report content is not loaded yet.")

    else:
        final_text = st.session_state.report_final_text

        if not final_text:
            st.info("Generate the final report first.")
            return

        st.markdown("### Final Report")

        segs = st.session_state.get("report_segments")
        orig_html = st.session_state.get("report_original_html")
        display_html = st.session_state.get("report_final_html")

        if not display_html and segs and orig_html:
            updated = parse_editable_text_back_to_segments(final_text, segs)
            display_html = build_final_report_html_preserving_charts(
                orig_html, segs, updated
            )

        if display_html:
            components.html(_make_report_html_responsive(display_html), height=900, scrolling=True)
        else:
            st.markdown(final_text)

        col1, col2 = st.columns(2)

        with col1:
            st.download_button(
                "Download HTML",
                (display_html or final_text).encode("utf-8"),
                file_name="final_report.html",
                width="stretch",
                key="download_final_html"
            )

        with col2:
            st.download_button(
                "Download Markdown",
                final_text.encode("utf-8"),
                file_name="final_report.md",
                width="stretch",
                key="download_final_md"
            )

