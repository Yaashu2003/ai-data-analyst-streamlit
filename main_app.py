import gemini_patch
import io
import json
import os
import shutil
import subprocess
import sys
import time
import warnings
from html import escape
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import rag_pipeline
from chatbot_engine import (
    initialize_chatbot,
    init_db as chatbot_init_db,
    load_data as chatbot_load_data,
    render_chatbot_ui,
)
from pbix_report_generator import generate_pbix_report, save_report
from report_html import generate_html
from report_viewer import render_report_tab, reset_report_session_for_new_source
from ui_report_powerbi import render_chart, render_pbix_chat_response


load_dotenv(override=True)
warnings.filterwarnings("ignore", category=UserWarning)


BASE_DIR = Path(__file__).resolve().parent
SUPERSTORE_CSV = BASE_DIR / "Superstore.csv"
ANALYSIS_SCRIPT = BASE_DIR / "main.py"
REPORT_HTML = BASE_DIR / "interactive_analysis_report.html"
DATASET_INPUT_DIR = BASE_DIR / "dataset_input"
DATASET_INPUT_DIR.mkdir(exist_ok=True)
DATASET_MANIFEST = DATASET_INPUT_DIR / "manifest.json"
REPORT_INPUT_DIR = BASE_DIR / "report_input"
REPORT_INPUT_DIR.mkdir(exist_ok=True)
REPORT_EXPORT_INPUT_DIR = BASE_DIR / "report_input_exports"
REPORT_EXPORT_INPUT_DIR.mkdir(exist_ok=True)
DATASET_SOURCE_COLUMN = "source_file"
DATASET_ROW_COLUMN = "source_row_number"


if "mode" not in st.session_state:
    st.session_state.mode = None
if "analysis_completed" not in st.session_state:
    st.session_state.analysis_completed = False
if "workspace" not in st.session_state:
    st.session_state.workspace = "report"
if "charts" not in st.session_state:
    st.session_state.charts = []
if "report_ready" not in st.session_state:
    st.session_state.report_ready = False
if "report_visible" not in st.session_state:
    st.session_state.report_visible = True
if "analysis_running" not in st.session_state:
    st.session_state.analysis_running = False
if "analysis_process" not in st.session_state:
    st.session_state.analysis_process = None
if "analysis_report_loaded" not in st.session_state:
    st.session_state.analysis_report_loaded = False
if "pbix_extraction_summary" not in st.session_state:
    st.session_state.pbix_extraction_summary = {}
if "dataset_upload_signature" not in st.session_state:
    st.session_state.dataset_upload_signature = None
if "dataset_upload_summary" not in st.session_state:
    st.session_state.dataset_upload_summary = {}
if "dataset_preview" not in st.session_state:
    st.session_state.dataset_preview = pd.DataFrame()


if "initial_cleanup" not in st.session_state:
    for folder_name in ("charts", "charts_html"):
        folder = BASE_DIR / folder_name
        if folder.exists():
            for item in folder.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    else:
                        shutil.rmtree(item)
                except Exception:
                    pass
    st.session_state.initial_cleanup = True


def clear_folder(folder: Path):
    for item in folder.iterdir():
        try:
            if item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)
        except Exception:
            pass


def split_uploaded_files(files):
    pbix_files = []
    tableau_files = []
    export_files = []
    dataset_files = []

    for file in files or []:
        suffix = Path(file.name).suffix.lower()
        if suffix == ".pbix":
            pbix_files.append(file)
        elif suffix in {".twb", ".twbx"}:
            tableau_files.append(file)
        elif suffix in {".csv", ".xlsx", ".xls"}:
            export_files.append(file)
            dataset_files.append(file)

    return pbix_files, tableau_files, export_files, dataset_files


def save_uploaded_exports(uploaded_files):
    clear_folder(REPORT_EXPORT_INPUT_DIR)
    saved_paths = []
    for file in uploaded_files or []:
        path = REPORT_EXPORT_INPUT_DIR / file.name
        with path.open("wb") as handle:
            handle.write(file.getbuffer())
        saved_paths.append(str(path))
    return saved_paths


def dataset_upload_signature(uploaded_files):
    signature = []
    for file in uploaded_files or []:
        try:
            size = int(file.getbuffer().nbytes)
        except Exception:
            size = 0
        signature.append((file.name, size))
    return tuple(signature)


def read_uploaded_dataset(file) -> pd.DataFrame:
    suffix = Path(file.name).suffix.lower()
    file_bytes = file.getvalue()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(io.BytesIO(file_bytes))
    return pd.read_csv(io.BytesIO(file_bytes), encoding="latin1")


def prepare_uploaded_dataset_frame(dataframe: pd.DataFrame, file_name: str) -> pd.DataFrame:
    prepared = dataframe.copy()
    rename_map = {}
    if DATASET_SOURCE_COLUMN in prepared.columns:
        rename_map[DATASET_SOURCE_COLUMN] = f"{DATASET_SOURCE_COLUMN}_original"
    if DATASET_ROW_COLUMN in prepared.columns:
        rename_map[DATASET_ROW_COLUMN] = f"{DATASET_ROW_COLUMN}_original"
    if "row_id" in prepared.columns:
        rename_map["row_id"] = "row_id_original"
    if rename_map:
        prepared = prepared.rename(columns=rename_map)

    prepared.insert(0, DATASET_ROW_COLUMN, range(1, len(prepared) + 1))
    prepared.insert(0, DATASET_SOURCE_COLUMN, Path(file_name).name)
    return prepared


def save_uploaded_datasets(uploaded_files) -> pd.DataFrame:
    clear_folder(DATASET_INPUT_DIR)

    frames = []
    files_summary = []
    for file in uploaded_files or []:
        original_path = DATASET_INPUT_DIR / Path(file.name).name
        with original_path.open("wb") as handle:
            handle.write(file.getbuffer())

        dataframe = read_uploaded_dataset(file)
        frames.append(prepare_uploaded_dataset_frame(dataframe, file.name))
        files_summary.append({
            "name": file.name,
            "rows": int(len(dataframe)),
            "columns": int(len(dataframe.columns)),
            "path": str(original_path),
        })

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined.insert(0, "row_id", range(len(combined)))
    combined.to_csv(SUPERSTORE_CSV, index=False, encoding="latin1")

    manifest = {
        "mode": "multi_dataset" if len(files_summary) > 1 else "single_dataset",
        "source_column": DATASET_SOURCE_COLUMN,
        "row_column": DATASET_ROW_COLUMN,
        "combined_file": str(SUPERSTORE_CSV),
        "total_rows": int(len(combined)),
        "total_columns": int(len(combined.columns)),
        "files": files_summary,
    }
    DATASET_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    st.session_state.dataset_upload_summary = manifest

    try:
        chatbot_load_data.clear()
        chatbot_init_db.clear()
    except Exception:
        pass

    return combined


def load_current_dataset_preview() -> pd.DataFrame:
    if isinstance(st.session_state.get("dataset_preview"), pd.DataFrame) and not st.session_state.dataset_preview.empty:
        return st.session_state.dataset_preview
    if SUPERSTORE_CSV.exists():
        try:
            return pd.read_csv(SUPERSTORE_CSV, encoding="latin1")
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def build_pbix_prompt_suggestions(pbix_summary, charts):
    report_names = [
        str(item.get("report", "")).strip()
        for item in pbix_summary.get("reports", [])
        if str(item.get("report", "")).strip()
    ]
    report_label = ", ".join(report_names[:3]) if report_names else "the uploaded dashboards"
    chart_titles = []
    for chart in charts or []:
        title = str(chart.get("title", "")).strip()
        if title and title not in chart_titles:
            chart_titles.append(title)
        if len(chart_titles) >= 3:
            break

    prompts = [
        f"Compare the strongest performance drivers across {report_label} and create one comparison chart.",
        f"Which dashboard shows the biggest operational risk or weakest segment, and what exact chart evidence supports it across {report_label}?",
        f"Summarize the top insights from {report_label} report by report, then give one cross-dashboard recommendation.",
    ]

    if chart_titles:
        prompts.append(
            f"Compare `{chart_titles[0]}` across {report_label} and explain which report leads, which lags, and why."
        )
    if len(report_names) >= 3:
        prompts.append(
            "Create a single chart comparing the uploaded dashboards on the most comparable metric available, then list 3 insights."
        )

    return prompts[:5]


def inject_app_css():
    st.markdown(
        """
<style>
:root {
    --page-bg-1: #f5f7fb;
    --page-bg-2: #eef6f2;
    --card-bg: rgba(255, 255, 255, 0.95);
    --card-border: #d7e3dc;
    --accent: #0f766e;
    --accent-strong: #134e4a;
    --accent-soft: #dff5ef;
    --text-main: #112031;
    --text-muted: #516173;
}

.stApp {
    background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.10), transparent 34%),
        radial-gradient(circle at top right, rgba(249, 115, 22, 0.08), transparent 24%),
        linear-gradient(180deg, var(--page-bg-1), var(--page-bg-2));
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 2.5rem;
    max-width: 1320px;
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f8fbff 0%, #eef7f3 100%);
    border-right: 1px solid #dbe6df;
}

.hero-panel {
    background: linear-gradient(135deg, #0f172a 0%, #134e4a 55%, #0f766e 100%);
    color: #ffffff;
    border-radius: 24px;
    padding: 28px 30px;
    box-shadow: 0 18px 48px rgba(15, 23, 42, 0.16);
    margin-bottom: 1.2rem;
}

.hero-panel h1 {
    margin: 0;
    font-size: 2.2rem;
    font-weight: 800;
    letter-spacing: -0.03em;
}

.hero-panel p {
    margin: 0.9rem 0 0;
    max-width: 820px;
    color: rgba(255, 255, 255, 0.88);
    font-size: 1rem;
    line-height: 1.55;
}

.status-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 12px;
    margin: 1rem 0 1.2rem;
}

.status-card,
.panel-card,
.upload-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 18px;
    box-shadow: 0 12px 32px rgba(15, 23, 42, 0.06);
}

.status-card {
    padding: 16px 18px;
}

.status-label {
    color: var(--text-muted);
    font-size: 0.82rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.35rem;
}

.status-value {
    color: var(--text-main);
    font-size: 1.1rem;
    font-weight: 700;
}

.panel-card,
.upload-card {
    padding: 18px 20px;
    margin-bottom: 1rem;
}

.panel-card h3,
.upload-card h3 {
    color: var(--text-main);
    margin: 0 0 0.45rem;
    font-size: 1.05rem;
}

.panel-card p,
.upload-card p,
.sidebar-note,
.file-pill {
    color: var(--text-muted);
}

.file-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 0.85rem;
}

.file-pill {
    background: #eef5ff;
    border: 1px solid #d3e3fd;
    border-radius: 999px;
    padding: 0.35rem 0.75rem;
    font-size: 0.84rem;
}

.section-title {
    color: var(--text-main);
    font-size: 1.45rem;
    font-weight: 760;
    margin: 1.2rem 0 0.55rem;
}

.section-copy {
    color: var(--text-muted);
    margin-bottom: 1rem;
}

.report-assistant {
    background: linear-gradient(180deg, #fffef8 0%, #f6fbff 100%);
    border: 1px solid #e7e4cf;
}

.report-assistant-rail {
    position: sticky;
    top: 96px;
    align-self: start;
}

.report-assistant-rail .panel-card {
    margin-bottom: 0.85rem;
}

.dataset-chat-page {
    position: relative;
    overflow: hidden;
    background:
        radial-gradient(circle at top right, rgba(249, 115, 22, 0.12), transparent 28%),
        radial-gradient(circle at left center, rgba(15, 118, 110, 0.12), transparent 34%),
        linear-gradient(180deg, rgba(255,255,255,0.96) 0%, rgba(244,250,247,0.98) 100%);
    border: 1px solid #d7e3dc;
    border-radius: 30px;
    padding: 26px 26px 24px;
    box-shadow: 0 20px 48px rgba(15, 23, 42, 0.07);
}

.dataset-chat-page::before {
    content: "";
    position: absolute;
    inset: auto -70px -90px auto;
    width: 230px;
    height: 230px;
    border-radius: 999px;
    background: radial-gradient(circle, rgba(15, 118, 110, 0.12) 0%, rgba(15, 118, 110, 0) 72%);
    pointer-events: none;
}

.dataset-workspace-hero {
    position: relative;
    overflow: hidden;
    background: linear-gradient(135deg, #102235 0%, #14324c 58%, #0f766e 100%);
    border-radius: 26px;
    padding: 24px 24px 20px;
    color: #f8fafc;
    box-shadow: 0 18px 44px rgba(15, 23, 42, 0.16);
    margin-bottom: 1rem;
}

.dataset-workspace-hero::after {
    content: "";
    position: absolute;
    right: -70px;
    top: -80px;
    width: 210px;
    height: 210px;
    border-radius: 999px;
    background: radial-gradient(circle, rgba(255,255,255,0.24) 0%, rgba(255,255,255,0) 72%);
}

.dataset-workspace-hero > * {
    position: relative;
    z-index: 1;
}

.dataset-workspace-kicker {
    display: inline-flex;
    align-items: center;
    padding: 0.35rem 0.72rem;
    border-radius: 999px;
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.16);
    color: rgba(255,255,255,0.88);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.9rem;
}

.dataset-workspace-title {
    font-size: 1.75rem;
    font-weight: 780;
    letter-spacing: -0.03em;
    margin-bottom: 0.45rem;
}

.dataset-workspace-copy {
    max-width: 760px;
    color: rgba(248,250,252,0.84);
    line-height: 1.6;
}

.dataset-workspace-meta-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 12px;
    margin-top: 1rem;
}

.dataset-workspace-meta-card {
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 18px;
    padding: 14px 15px;
    backdrop-filter: blur(6px);
}

.dataset-workspace-meta-label {
    color: rgba(226, 232, 240, 0.78);
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.35rem;
}

.dataset-workspace-meta-value {
    color: #ffffff;
    font-size: 1rem;
    font-weight: 700;
}

div[data-testid="stPopover"] {
    position: fixed;
    top: 164px;
    right: 36px;
    width: min(320px, calc(100vw - 48px));
    z-index: 999999;
}

div[data-testid="stPopover"] > div {
    width: 100%;
}

div[data-testid="stPopover"] button {
    width: 100%;
    min-height: 54px;
    background: rgba(255,255,255,0.96);
    border: 1px solid #d8e5de;
    color: var(--text-main);
    font-weight: 700;
    border-radius: 18px;
    box-shadow: 0 14px 34px rgba(15, 23, 42, 0.12);
}

.report-editor-shell {
    background: linear-gradient(180deg, rgba(255,255,255,0.96) 0%, rgba(245,249,247,0.98) 100%);
    border: 1px solid #d7e3dc;
    border-radius: 24px;
    padding: 20px 22px;
    box-shadow: 0 14px 36px rgba(15, 23, 42, 0.06);
    margin-top: 0.8rem;
}

.report-editor-header {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 1rem;
}

.report-editor-title {
    color: var(--text-main);
    font-size: 1.2rem;
    font-weight: 760;
    margin-bottom: 0.2rem;
}

.report-editor-copy {
    color: var(--text-muted);
    max-width: 720px;
    line-height: 1.55;
}

.report-editor-kicker {
    display: inline-flex;
    align-items: center;
    padding: 0.34rem 0.74rem;
    border-radius: 999px;
    background: #effaf5;
    border: 1px solid #cde8dc;
    color: var(--accent-strong);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.8rem;
}

.report-feedback-card {
    background: rgba(255,255,255,0.94);
    border: 1px solid #d9e5dd;
    border-radius: 20px;
    padding: 16px 18px;
    box-shadow: 0 10px 26px rgba(15, 23, 42, 0.04);
    height: 100%;
}

.report-feedback-card h4 {
    margin: 0 0 0.45rem;
    color: var(--text-main);
    font-size: 1rem;
}

.report-feedback-card p {
    margin: 0;
    color: var(--text-muted);
    line-height: 1.5;
}

.report-action-band {
    background: linear-gradient(135deg, #102235 0%, #14324c 58%, #0f766e 100%);
    border-radius: 22px;
    padding: 16px 18px;
    color: #f8fafc;
    margin: 1rem 0 0.8rem;
}

.report-action-band strong {
    display: block;
    margin-bottom: 0.25rem;
    font-size: 1rem;
}

.report-action-band span {
    color: rgba(248, 250, 252, 0.82);
    line-height: 1.5;
}

.report-section iframe,
.chart-visual-wrapper iframe,
div[data-testid="stPopover"] iframe {
    width: 100% !important;
}

.report-section iframe,
.chart-visual-wrapper iframe {
    min-height: 780px !important;
    border: 0;
}

div[data-testid="stTabs"] button[role="tab"] {
    border-radius: 999px;
    border: 1px solid #d7e3dc;
    background: rgba(255, 255, 255, 0.82);
    color: var(--text-main);
    font-weight: 600;
    padding: 0.55rem 0.95rem;
}

div[data-testid="stTabs"] button[aria-selected="true"] {
    background: var(--accent-soft);
    color: var(--accent-strong);
    border-color: #9fd8cb;
}

div[data-testid="stButton"] > button,
div[data-testid="stDownloadButton"] > button {
    border-radius: 14px;
    border: 1px solid #bad9d1;
    background: linear-gradient(180deg, #ffffff 0%, #f2fbf8 100%);
    color: var(--text-main);
    font-weight: 650;
}

div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #0f766e 0%, #115e59 100%);
    border-color: #0f766e;
    color: #ffffff;
}

div[data-testid="stFileUploader"] section {
    border-radius: 16px;
    border: 1px dashed #8abaae;
    background: rgba(255, 255, 255, 0.72);
}

div[data-testid="stTextArea"] textarea {
    white-space: pre-wrap !important;
    overflow-wrap: anywhere !important;
    word-break: break-word !important;
    resize: vertical;
    min-height: 118px;
    line-height: 1.5;
}

[data-testid="stMetric"] {
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid #d7e3dc;
    border-radius: 18px;
    padding: 0.75rem 1rem;
}
</style>
""",
        unsafe_allow_html=True,
    )


def render_hero():
    st.markdown(
        """
<section class="hero-panel">
    <h1>AI Data Analyst</h1>
    <p>
        Upload a dataset, Power BI file, or Tableau workbook, run the analysis, and review the report in one place.
        The report stays visible after generation so editing and reviewing are easier.
    </p>
</section>
""",
        unsafe_allow_html=True,
    )


def render_status_cards(items: Iterable[tuple[str, str]]):
    cards = []
    for label, value in items:
        cards.append(
            f"""
<div class="status-card">
    <div class="status-label">{label}</div>
    <div class="status-value">{value}</div>
</div>
"""
        )
    st.markdown(f'<div class="status-row">{"".join(cards)}</div>', unsafe_allow_html=True)


def parse_pbix_chat_reply(reply):
    if not isinstance(reply, str):
        return reply

    clean = reply.strip()
    if not clean:
        return reply

    if clean.startswith("```json"):
        clean = clean[7:].strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
    elif clean.startswith("```") and clean.endswith("```"):
        clean = clean[3:-3].strip()

    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end > start:
        clean = clean[start:end + 1]

    try:
        parsed = json.loads(clean)
    except Exception:
        return reply

    return parsed if isinstance(parsed, dict) else reply


def render_section_heading(title: str, description: str):
    st.markdown(
        f"""
<div class="section-title">{title}</div>
<div class="section-copy">{description}</div>
""",
        unsafe_allow_html=True,
    )


def render_sidebar_file_list(files):
    if not files:
        return
    file_tags = "".join(f'<span class="file-pill">{file.name}</span>' for file in files)
    st.markdown(f'<div class="file-list">{file_tags}</div>', unsafe_allow_html=True)


def render_report_assistant():
    st.markdown(
        """
<div class="panel-card report-assistant">
    <h3>Ask About The Report</h3>
    <p>Use the generated report and chart context for VLM-connected follow-up questions while you review the report.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    from chatbot_conversation import display_chatbot

    try:
        display_chatbot()
    except Exception as error:
        st.error(f"Chatbot error: {error}")


def activate_report_view(report_state_key: str):
    """Switch the UI to the latest generated report immediately."""
    reset_report_session_for_new_source()
    st.session_state[report_state_key] = True
    st.session_state.workspace = "report"
    st.session_state.report_visible = True


def start_dataset_analysis():
    """Launch the heavy analysis in the background so the UI can poll for the report."""
    try:
        if REPORT_HTML.exists():
            REPORT_HTML.unlink()
    except OSError:
        pass

    reset_report_session_for_new_source()
    st.session_state.analysis_completed = False
    st.session_state.analysis_report_loaded = False
    st.session_state.analysis_running = True
    st.session_state.workspace = "report"
    st.session_state.report_visible = True
    st.session_state.analysis_process = subprocess.Popen(
        [sys.executable, str(ANALYSIS_SCRIPT)],
        cwd=str(BASE_DIR),
    )


def _report_matches_current_dataset() -> bool:
    """Return True when the generated HTML report is at least as new as the active dataset file."""
    try:
        return REPORT_HTML.exists() and SUPERSTORE_CSV.exists() and REPORT_HTML.stat().st_mtime >= SUPERSTORE_CSV.stat().st_mtime
    except OSError:
        return False


def recover_dataset_report_if_ready():
    """Recover the report view if analysis finished but the Streamlit session lost the completion state."""
    if st.session_state.analysis_completed:
        return
    if not _report_matches_current_dataset():
        return

    st.session_state.analysis_running = False
    st.session_state.analysis_process = None
    st.session_state.analysis_report_loaded = True
    activate_report_view("analysis_completed")
    st.rerun()


def poll_dataset_analysis():
    """Open the report as soon as the HTML file is created."""
    if not st.session_state.analysis_running:
        return

    process = st.session_state.get("analysis_process")
    if process is None:
        st.session_state.analysis_running = False
        return

    report_exists = REPORT_HTML.exists()
    exit_code = process.poll()

    if report_exists and not st.session_state.analysis_report_loaded:
        st.session_state.analysis_report_loaded = True
        st.session_state.analysis_running = False
        activate_report_view("analysis_completed")
        st.rerun()

    if exit_code is not None:
        st.session_state.analysis_running = False
        st.session_state.analysis_process = None
        if report_exists:
            activate_report_view("analysis_completed")
            st.rerun()
        else:
            st.error("Analysis finished, but the HTML report was not created.")
        return

    st.info("Analysis is running. The report will open automatically as soon as it is created.")
    time.sleep(1)
    st.rerun()


st.set_page_config(page_title="AI Data Analyst", layout="wide")
inject_app_css()
render_hero()


with st.sidebar:
    st.markdown(
        """
<div class="upload-card">
    <h3>Data Input</h3>
    <p class="sidebar-note">Choose the file you want to analyze. CSV and Excel run dataset analysis. PBIX, TWB, and TWBX files generate a combined BI dashboard report.</p>
</div>
""",
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader(
        "Upload CSV, Excel, Power BI, or Tableau files",
        type=["csv", "xlsx", "pbix", "twb", "twbx"],
        accept_multiple_files=True,
    )

    if uploaded:
        pbix_files, tableau_files, export_files, dataset_files = split_uploaded_files(uploaded)
        if pbix_files or tableau_files:
            st.session_state.mode = "pbix"
        elif dataset_files:
            st.session_state.mode = "dataset"
        else:
            st.error("Unsupported file type.")
            st.session_state.mode = None
    else:
        st.session_state.mode = None

    render_sidebar_file_list(uploaded)
    st.markdown(
        """
<div class="panel-card">
    <h3>How it works</h3>
    <p>1. Upload a file.</p>
    <p>2. Run analysis.</p>
    <p>3. Review the generated report.</p>
    <p>4. Edit or ask follow-up questions.</p>
</div>
""",
        unsafe_allow_html=True,
    )


if st.session_state.mode == "dataset":
    render_section_heading(
        "Dataset Analysis Workspace",
        "Upload one or more datasets, generate the report, and keep the report visible while you review and edit it.",
    )

    dataset_preview = pd.DataFrame()
    if uploaded:
        _, _, _, dataset_files = split_uploaded_files(uploaded)
        signature = dataset_upload_signature(dataset_files)
        if st.session_state.dataset_upload_signature != signature:
            dataset_preview = save_uploaded_datasets(dataset_files)
            st.session_state.dataset_preview = dataset_preview
            st.session_state.dataset_upload_signature = signature
        else:
            dataset_preview = load_current_dataset_preview()

        source_count = len(st.session_state.dataset_upload_summary.get("files", dataset_files))
        if source_count > 1:
            st.success(
                f"{source_count} dataset files uploaded. Each file will get its own chart set, "
                "with a combined bridge for cross-file questions."
            )
        else:
            st.success("Dataset uploaded successfully.")

    data, _conn = initialize_chatbot()
    poll_dataset_analysis()
    recover_dataset_report_if_ready()

    active_dataset = dataset_preview if not dataset_preview.empty else data
    dataset_summary = st.session_state.get("dataset_upload_summary", {})
    uploaded_file_names = [
        item.get("name", "")
        for item in dataset_summary.get("files", [])
        if item.get("name")
    ]
    source_file_count = len(uploaded_file_names) or (1 if uploaded else 0)
    current_file_name = ", ".join(uploaded_file_names[:3]) if uploaded_file_names else SUPERSTORE_CSV.name
    if len(uploaded_file_names) > 3:
        current_file_name += f" + {len(uploaded_file_names) - 3} more"
    if isinstance(active_dataset, pd.DataFrame) and not active_dataset.empty:
        render_status_cards([
            ("Mode", "Dataset"),
            ("Rows", f"{len(active_dataset):,}"),
            ("Columns", str(len(active_dataset.columns))),
            ("Source files", str(source_file_count or 1)),
            ("Current input", current_file_name),
        ])
        with st.expander("Preview dataset", expanded=False):
            st.dataframe(active_dataset.head(12), width="stretch")

    if st.button("Run dataset analysis", type="primary", disabled=st.session_state.analysis_running):
        start_dataset_analysis()
        st.rerun()

    if st.session_state.analysis_completed and st.session_state.workspace == "report":
        st.session_state.report_visible = True
        render_report_tab("analysis_completed", "report_visible")

    if st.session_state.workspace == "dataset":
        st.divider()
        st.markdown('<div class="dataset-chat-page">', unsafe_allow_html=True)
        dataset_rows = len(active_dataset) if isinstance(active_dataset, pd.DataFrame) else 0
        dataset_columns = len(active_dataset.columns) if isinstance(active_dataset, pd.DataFrame) else 0
        st.markdown(
            f"""
<div class="dataset-workspace-hero">
    <div class="dataset-workspace-kicker">Dataset Workspace</div>
    <div class="dataset-workspace-title">Explore the uploaded data with a cleaner analyst cockpit</div>
    <div class="dataset-workspace-copy">
        Ask direct questions, inspect SQL-backed answers, open tables, and request visuals while keeping the full report only one click away.
    </div>
    <div class="dataset-workspace-meta-grid">
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Current File</div>
            <div class="dataset-workspace-meta-value">{escape(current_file_name)}</div>
        </div>
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Rows</div>
            <div class="dataset-workspace-meta-value">{dataset_rows:,}</div>
        </div>
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Columns</div>
            <div class="dataset-workspace-meta-value">{dataset_columns}</div>
        </div>
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Outputs</div>
            <div class="dataset-workspace-meta-value">Answers, Tables, Charts</div>
        </div>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )
        action_col1, action_col2, _ = st.columns([1.2, 1.2, 4], gap="medium")
        with action_col1:
            if st.button("Back to report", width="stretch"):
                st.session_state.workspace = "report"
                st.session_state.report_visible = True
                st.rerun()
        with action_col2:
            if st.button("Clear chat", width="stretch"):
                st.session_state.conversation = []
                st.session_state.user_input_box = ""
                st.rerun()
        render_chatbot_ui()
        st.markdown('</div>', unsafe_allow_html=True)

elif st.session_state.mode == "pbix":
    render_section_heading(
        "BI Dashboard Report Workspace",
        "Process Power BI and Tableau dashboards, extract chart information, and review the generated report in the newer floating report workspace. "
        "When a dashboard cannot expose its tables directly, the app will use packaged workbook data or recover local source definitions when available.",
    )

    if uploaded:
        pbix_files, tableau_files, export_files, _ = split_uploaded_files(uploaded)
        pbix_summary = st.session_state.get("pbix_extraction_summary", {})
        status_cards_placeholder = st.empty()
        with status_cards_placeholder:
            render_status_cards([
                ("Mode", "BI Dashboards"),
                ("PBIX files", str(len(pbix_files))),
                ("Tableau files", str(len(tableau_files))),
                ("Fallback tables", str(len(export_files))),
                ("Charts with data", str(pbix_summary.get("chart_data_count", 0))),
                ("Visual definitions", str(pbix_summary.get("metadata_visual_count", 0))),
                ("Report ready", "Yes" if st.session_state.report_ready else "Not yet"),
            ])

        if export_files:
            export_names = ", ".join(file.name for file in export_files)
            st.info(
                f"Fallback export files detected: {export_names}. "
                "These will be used if a dashboard references tables that are not embedded or packaged locally."
            )

        if st.button("Process BI dashboards", type="primary"):
            with st.spinner("Processing BI dashboards..."):
                clear_folder(REPORT_INPUT_DIR)
                for file in pbix_files + tableau_files:
                    path = REPORT_INPUT_DIR / file.name
                    with path.open("wb") as handle:
                        handle.write(file.getbuffer())


                external_data_paths = save_uploaded_exports(export_files)
                charts = rag_pipeline.process_all_reports(
                    REPORT_INPUT_DIR,
                    external_data_files=external_data_paths,
                )
                summary = rag_pipeline.get_last_extraction_summary()
                st.session_state.charts = charts
                st.session_state.pbix_extraction_summary = summary
                
                with status_cards_placeholder:
                    render_status_cards([
                        ("Mode", "BI Dashboards"),
                        ("PBIX files", str(len(pbix_files))),
                        ("Tableau files", str(len(tableau_files))),
                        ("Fallback tables", str(len(export_files))),
                        ("Charts with data", str(summary.get("chart_data_count", 0))),
                        ("Visual definitions", str(summary.get("metadata_visual_count", 0))),
                        ("Report ready", "Generating..."),
                    ])
                st.success(
                    f"{summary.get('chart_data_count', 0)} charts with embedded or packaged data, "
                    f"{summary.get('metadata_visual_count', 0)} visual definitions extracted."
                )
                if summary.get("fallback_alias_count", 0):
                    st.success(
                        f"Fallback exports were used for {summary.get('fallback_alias_count', 0)} missing table alias mappings."
                    )
                if summary.get("metadata_visual_count", 0):
                    st.info(
                        "Some visuals were found in dashboard metadata, but their source tables were "
                        "not embedded or packaged in the local file. Those items are shown as chart definitions "
                        "with field mappings instead of numeric chart values."
                    )

                report = generate_pbix_report(charts)
                save_report(report)
                generate_html(charts)

                activate_report_view("report_ready")
                st.rerun()

    if st.session_state.report_ready and st.session_state.workspace == "report":
        st.session_state.report_visible = True
        pbix_summary = st.session_state.get("pbix_extraction_summary", {})
        if pbix_summary:
            st.markdown(
                f"""
<div class="report-action-band">
    <strong>BI extraction snapshot</strong>
    <span>
        Charts with data: {pbix_summary.get("chart_data_count", 0)} |
        layout-only visuals: {pbix_summary.get("metadata_visual_count", 0)} |
        visuals scanned: {pbix_summary.get("visuals_seen", 0)} |
        Power BI: {pbix_summary.get("pbix_report_count", 0)} |
        Tableau: {pbix_summary.get("tableau_report_count", 0)}
    </span>
</div>
""",
                unsafe_allow_html=True,
            )
        render_report_tab("report_ready", "report_visible")

    if st.session_state.workspace == "pbix_chat":
        st.divider()
        if "pbix_conversation" not in st.session_state:
            st.session_state.pbix_conversation = []

        charts = st.session_state.get("charts", [])
        pbix_summary = st.session_state.get("pbix_extraction_summary", {})
        st.markdown('<div class="dataset-chat-page">', unsafe_allow_html=True)
        st.markdown(
            f"""
<div class="dataset-workspace-hero">
    <div class="dataset-workspace-kicker">BI Chart Workspace</div>
    <div class="dataset-workspace-title">Review extracted visuals, ask analytical questions, and generate follow-up chart views</div>
    <div class="dataset-workspace-copy">
        This workspace uses Power BI and Tableau visual structure plus recovered chart data when available, so you can ask for comparisons,
        performance drivers, risks, and recommendation-ready summaries without losing the report context.
    </div>
    <div class="dataset-workspace-meta-grid">
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Charts With Data</div>
            <div class="dataset-workspace-meta-value">{pbix_summary.get("chart_data_count", 0)}</div>
        </div>
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Layout-Only Visuals</div>
            <div class="dataset-workspace-meta-value">{pbix_summary.get("metadata_visual_count", 0)}</div>
        </div>
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Visuals Scanned</div>
            <div class="dataset-workspace-meta-value">{pbix_summary.get("visuals_seen", len(charts))}</div>
        </div>
        <div class="dataset-workspace-meta-card">
            <div class="dataset-workspace-meta-label">Fallback Aliases</div>
            <div class="dataset-workspace-meta-value">{pbix_summary.get("fallback_alias_count", 0)}</div>
        </div>
    </div>
</div>
""",
            unsafe_allow_html=True,
        )

        action_col1, action_col2, _ = st.columns([1.2, 1.2, 4], gap="medium")
        with action_col1:
            if st.button("Back to report", width="stretch", key="pbix_back_to_report"):
                st.session_state.workspace = "report"
                st.session_state.report_visible = True
                st.rerun()
        with action_col2:
            if st.button("Clear chart chat", width="stretch", key="pbix_clear_chat"):
                st.session_state.pbix_conversation = []
                st.session_state.pbix_user_input = ""
                st.rerun()

        preview_col, summary_col = st.columns([1.3, 0.9], gap="large")
        with preview_col:
            st.markdown(
                """
<div class="report-feedback-card">
    <h4>Visual preview deck</h4>
    <p>Preview the extracted BI visuals before asking a question. This helps you keep the active chart, page, and measure context in view while you analyze the report.</p>
</div>
""",
                unsafe_allow_html=True,
            )
            if charts:
                chart_titles = [chart.get("title", "Untitled") for chart in charts]
                selected_title = st.selectbox(
                    "Select a visual to preview:",
                    chart_titles,
                    key="chat_preview_chart",
                )
                if st.toggle(
                    "Show selected visual preview",
                    value=False,
                    key="pbix_show_selected_visual_preview",
                    help="Keep this off for smoother chat. Turn it on only when you need to inspect a visual.",
                ):
                    for chart in charts:
                        if chart.get("title", "Untitled") == selected_title:
                            render_chart(chart)
                            break
            else:
                st.info("No extracted charts are available yet.")

        with summary_col:
            prompt_suggestions = build_pbix_prompt_suggestions(pbix_summary, charts)
            prompt_lines = "".join(
                f"<li><code>{escape(prompt)}</code></li>"
                for prompt in prompt_suggestions
            )
            st.markdown(
                f"""
<div class="report-feedback-card">
    <h4>Good questions to ask</h4>
    <p>These prompts are tuned for the current BI chart logic, especially when you upload multiple dashboards together.</p>
    <ul>{prompt_lines}</ul>
</div>
""",
                unsafe_allow_html=True,
            )
            if pbix_summary:
                st.markdown(
                    f"""
<div class="report-feedback-card" style="margin-top: 0.9rem;">
    <h4>Extraction snapshot</h4>
    <p>Reports processed: {pbix_summary.get("reports_processed", 0)}<br>
    Charts with data: {pbix_summary.get("chart_data_count", 0)}<br>
    Layout-only visuals: {pbix_summary.get("metadata_visual_count", 0)}<br>
    Missing-table visuals: {pbix_summary.get("missing_table_visuals", 0)}</p>
</div>
""",
                    unsafe_allow_html=True,
                )

        col1, col2 = st.columns([8, 2])
        with col1:
            user_input = st.text_area(
                "Ask about your BI dashboard charts...",
                key="pbix_user_input",
                height=130,
                placeholder=(
                    "Ask for insights, comparisons, risks, or a chart. "
                    "Long questions will wrap down here so you can review the full prompt."
                ),
            )
        with col2:
            send = st.button("Send", key="pbix_send_btn")

        if send and user_input.strip():
            st.session_state.pbix_conversation.append({"role": "user", "content": user_input.strip()})
            with st.spinner("Analyzing charts..."):
                reply = rag_pipeline.ask_gemini_charts(user_input.strip(), st.session_state.charts)
                reply = parse_pbix_chat_reply(reply)
                st.session_state.pbix_conversation.append({"role": "assistant", "content": reply})

        visible_messages = st.session_state.pbix_conversation[-8:]
        if len(st.session_state.pbix_conversation) > len(visible_messages):
            st.caption(
                f"Showing the latest {len(visible_messages)} chart-chat messages "
                f"out of {len(st.session_state.pbix_conversation)} for performance."
            )

        for message in reversed(visible_messages):
            if message["role"] == "user":
                st.markdown(f"**You:** {message['content']}")
            else:
                reply_data = message["content"]
                if isinstance(reply_data, dict):
                    render_pbix_chat_response(reply_data)
                else:
                    st.markdown(f"**AI:** {str(reply_data)}")
            st.divider()
        st.markdown("</div>", unsafe_allow_html=True)

else:
    render_status_cards([
        ("Mode", "Waiting for file"),
        ("Dataset reports", "Supported"),
        ("BI dashboards", "PBIX, TWB, TWBX"),
        ("Next step", "Upload a file"),
    ])
    st.markdown(
        """
<div class="panel-card">
    <h3>Start here</h3>
    <p>Upload a CSV, Excel, PBIX, TWB, or TWBX file from the left sidebar to begin analysis.</p>
    <p>The report will stay visible after it is generated so you can review and edit it more easily.</p>
</div>
""",
        unsafe_allow_html=True,
    )
