# --------------------------------------------------------------
# Home.py — Report Viewer and Data Management Page
# --------------------------------------------------------------
import sys
import subprocess
from pathlib import Path
import time
from bs4 import BeautifulSoup
import os
import re
import sqlite3
import pandas as pd
# Removed: plotly.express and LangChain/LangGraph imports

import streamlit as st
import streamlit.components.v1 as components

# --------------------------------------------------------------
# Configuration and Setup
# --------------------------------------------------------------
# Note: st.set_page_config should typically be only in the main app file (Home.py)
# but for multi-page it can be repeated without issue.
st.set_page_config(layout="wide", page_title="Data Analyst - Report Viewer")
st.title("📄 Interactive Analysis Report Viewer")

# Removed: Load NVIDIA API Key (no longer needed on this page)

# --------------------------------------------------------------
# Paths (relative to project root so charts load regardless of CWD)
# --------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent
REPORT_HTML = _BASE_DIR / "interactive_analysis_report.html"
CHARTS_HTML_DIR = _BASE_DIR / "charts_html"
SUPERSTORE_CSV = _BASE_DIR / "Superstore.csv"
SQLITE_DB_PATH = "sales_temp.db" 

# --------------------------------------------------------------
# Session State Defaults
# --------------------------------------------------------------
st.session_state.setdefault("report_ready", REPORT_HTML.is_file())
# Removed: st.session_state.setdefault("chat_history", [])
st.session_state.setdefault("report_visible", False)
# Track the last uploaded file to prevent infinite loops
st.session_state.setdefault("last_uploaded_file_id", None)

# --------------------------------------------------------------
# --- Data Loading & Database Functions ---
# --------------------------------------------------------------

@st.cache_data(show_spinner="Loading and preparing data...")
def load_data(csv_path: str | Path):
    if not Path(csv_path).is_file():
        return pd.DataFrame()

    df = pd.read_csv(csv_path, encoding="latin1")
    # Standardize dates if possible
    if "Order Date" in df.columns:
        def conv(d):
            try:
                parts = str(d).split("/")
                if len(parts) == 3:
                    dd, mm, yy = parts
                    yy = yy.zfill(4)
                    return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
            except:
                return None
            return None
        df["order_date"] = df["Order Date"].apply(conv)
    return df

@st.cache_resource(show_spinner="Initializing SQLite database...")
def init_db(df: pd.DataFrame, db_path: str):
    """
    Creates a connection and writes data. 
    check_same_thread=False is needed for Streamlit.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    if not df.empty:
        # Use 'replace' so we don't necessarily have to delete the file every time
        df.to_sql("sales", conn, if_exists="replace", index=False)
    return conn

# --------------------------------------------------------------
# --- Global State Initialization (for Database management) ---
# --------------------------------------------------------------
data = load_data(SUPERSTORE_CSV)
conn = init_db(data, SQLITE_DB_PATH)

# Removed: LLM initialization and LangGraph setup

# --------------------------------------------------------------
# --- Report Handlers ---
# --------------------------------------------------------------

def run_analysis_script():
    if REPORT_HTML.is_file():
        REPORT_HTML.unlink()
        st.session_state.report_ready = False

    with st.spinner("Running analysis… please wait"):
        try:
            # Using r.py subprocess logic
            subprocess.run([sys.executable, "-u", "main.py"], check=True)
        except Exception as e:
            st.error(f"❌ Analysis failed: {e}")
            return

    if REPORT_HTML.is_file():
        st.session_state.report_ready = True
        st.success("✅ Report generated successfully!")
    else:
        st.error("❌ Report generation failed — report file missing.")

def _resolve_chart_path(chart_name: str):
    """Exact match, then same base (name_index_*.html), then same type (e.g. time_series_*.html)."""
    exact = CHARTS_HTML_DIR / chart_name
    if exact.is_file():
        return exact
    stem = Path(chart_name).stem
    parts = stem.split("_")
    if len(parts) >= 3:
        try:
            int(parts[-1]); int(parts[-2])
            base_prefix = "_".join(parts[:-2])
        except (ValueError, IndexError):
            base_prefix = stem
    else:
        base_prefix = stem
    matches = sorted(CHARTS_HTML_DIR.glob(f"{base_prefix}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    if len(parts) >= 4:
        type_prefix = "_".join(parts[:-3])
        matches = sorted(CHARTS_HTML_DIR.glob(f"{type_prefix}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    if len(parts) >= 5:
        type_only = "_".join(parts[:-4])  # e.g. box_plot_7 -> box_plot to match box_plot_0
        matches = sorted(CHARTS_HTML_DIR.glob(f"{type_only}_*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matches:
            return matches[0]
    return None


def load_full_report_html():
    if not REPORT_HTML.is_file():
        return "<h3>No report generated yet.</h3>"

    html_content = REPORT_HTML.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")

    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "") or iframe.get("data-src", "")
        if not src: continue
        clean_src = src.replace("file://", "").replace("\\", "/").lstrip("/")
        chart_name = Path(clean_src).name
        chart_path = _resolve_chart_path(chart_name)

        if chart_path and chart_path.is_file():
            chart_html = chart_path.read_text(encoding="utf-8")
            iframe["srcdoc"] = chart_html
            iframe["src"] = ""
            iframe["width"] = "100%"
            iframe["height"] = "600px"
        else:
            error_div = soup.new_tag("div")
            error_div.string = f"⚠️ Chart not available: {chart_name} (run analysis to generate charts)"
            iframe.replace_with(error_div)
    return str(soup)

# Removed: handle_chat_message (no longer needed on this page)

# --------------------------------------------------------------
# --- UI Layout ---
# --------------------------------------------------------------

# Sidebar for Data/Analysis Controls
with st.sidebar:
    st.subheader("⚙️ Data & Analysis Controls")

    # Uploader logic
    uploaded = st.file_uploader("Upload CSV", type=['csv'], key="csv_uploader")
    
    if uploaded and uploaded != st.session_state.get("last_uploaded_file_id"):
        st.session_state["last_uploaded_file_id"] = uploaded

        # CRITICAL FIX for WinError 32: Close DB connection before writing files
        if 'conn' in globals() and conn:
            conn.close()
        
        st.cache_resource.clear()
        st.cache_data.clear()

        with open(SUPERSTORE_CSV, "wb") as f:
            f.write(uploaded.getbuffer())
        
        if os.path.exists(SQLITE_DB_PATH):
            try:
                time.sleep(0.5)
                os.remove(SQLITE_DB_PATH)
            except Exception:
                pass 

        st.success("CSV uploaded successfully. Reloading...")
        time.sleep(1)
        st.rerun()

    # Run Analysis button
    if st.button("Run Analysis (main.py)"):
        if not SUPERSTORE_CSV.is_file():
            st.error("Upload a CSV first.")
        else:
            run_analysis_script()

    st.markdown("---")
    # Removed: Clear Chat History button

# Main Body - Report Viewer
button_label = '✅ Show Interactive Report' if not st.session_state.report_visible else '❌ Hide Interactive Report'
if st.button(button_label):
    st.session_state.report_visible = not st.session_state.report_visible
    # st.rerun() # Re-running only needed if the button changed state visibility

if st.session_state.report_visible:
    if st.session_state.report_ready:
        final_html = load_full_report_html()
        components.html(final_html, height=900, scrolling=True)
        st.download_button("⬇️ Download Report", data=REPORT_HTML.read_bytes(), file_name="interactive_analysis_report.html", mime="text/html")
    else:
        st.info("No report generated yet. Use the Controls in the sidebar to run analysis.")

# Removed: Tab selection and Chatbot section