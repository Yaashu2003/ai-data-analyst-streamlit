import streamlit as st
import os
import json
import time
import shutil
import markdown

# Import specific project files
import rag_pipeline
from main import intelligent_data_analysis

st.set_page_config(
    page_title="Unified Analysis Pipeline",
    page_icon="🔮",
    layout="wide"
)

# =====================================================
# STYLING & UI
# =====================================================

st.title("🔮 Unified Dataset & Power BI Analysis")

with st.sidebar:
    st.header("🧭 App Navigation")
    st.page_link("Home.py", label="📄 Report Viewer", icon="📄")
    st.page_link("ui.py", label="🤖 Data Chatbot", icon="🤖")
    st.page_link("ui_report_powerbi.py", label="💬 Power BI Chatbot", icon="💬")
    st.page_link("ui_combined_pipeline.py", label="🔮 Unified Analysis Pipeline", icon="🔮")
    st.markdown("---")

st.markdown("""
Upload a **raw dataset (.csv)** and your **Power BI dashboards (.pbix)**.  
The pipeline will extract insights from your raw data and visual metrics from Power BI, then use AI to provide a single, comprehensive synthesized report.
""")

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Upload Raw Dataset")
    csv_file = st.file_uploader("Upload Dataset (.csv)", type=["csv"], key="csv_upload")

with col2:
    st.subheader("2. Upload Power BI Reports")
    pbix_files = st.file_uploader("Upload Power BI (.pbix) files", type=["pbix"], accept_multiple_files=True, key="pbix_upload")

st.markdown("---")

if st.button("🚀 Run Unified Analysis", use_container_width=True):
    if not csv_file:
        st.error("Please upload a CSV dataset first.")
    elif not pbix_files:
        st.error("Please upload at least one Power BI report.")
    else:
        with st.status("Running Unified Intelligent Analysis...", expanded=True) as status:
            # ---------------------------------------------------------
            # 1. SETUP WORKSPACES
            # ---------------------------------------------------------
            st.write("📁 Setting up processing directories...")
            
            # Setup CSV path
            csv_path = "unified_upload.csv"
            with open(csv_path, "wb") as f:
                f.write(csv_file.getbuffer())
                
            # Setup PBIX dir
            REPORT_INPUT = "report_input"
            if os.path.exists(REPORT_INPUT):
                shutil.rmtree(REPORT_INPUT)
            os.makedirs(REPORT_INPUT, exist_ok=True)
            for file in pbix_files:
                path = os.path.join(REPORT_INPUT, file.name)
                with open(path, "wb") as f:
                    f.write(file.getbuffer())
                    
            # ---------------------------------------------------------
            # 2. RUN CSV ANALYSIS (MAIN.PY ORCHESTRATOR)
            # ---------------------------------------------------------
            st.write("🧠 Analyzing raw dataset (Agentic Pipeline)...")
            csv_results = intelligent_data_analysis(dataset_path=csv_path)
            
            if not csv_results:
                st.error("Dataset analysis failed. Please check the logs.")
                status.update(label="Analysis Failed", state="error")
                st.stop()
                
            csv_insights = {
                "specialized_analyses": csv_results.get("specialized_analyses", {}),
                "insights": csv_results.get("insights", [])
            }
            
            # ---------------------------------------------------------
            # 3. RUN POWER BI ANALYSIS
            # ---------------------------------------------------------
            st.write("📊 Extracting visuals and data from Power BI Reports...")
            pbix_charts = rag_pipeline.process_all_reports(REPORT_INPUT)
            pbix_summary = rag_pipeline.get_last_extraction_summary()
            if pbix_summary.get("metadata_visual_count", 0):
                st.info(
                    f"PBIX extraction found {pbix_summary.get('chart_data_count', 0)} charts with embedded data and "
                    f"{pbix_summary.get('metadata_visual_count', 0)} layout-only visuals."
                )
            pbix_simplified = [rag_pipeline.simplify_chart_for_llm(c) for c in pbix_charts]
            
            # ---------------------------------------------------------
            # 4. GENERATE UNIFIED REPORT
            # ---------------------------------------------------------
            st.write("💡 Synthesizing unified insights using Gemini...")
            unified_report = rag_pipeline.generate_unified_insights(csv_insights, pbix_simplified)
            
            # Write to file
            with open("unified_analysis_report.txt", "w", encoding="utf-8") as f:
                f.write(unified_report)
                
            # Basic HTML wrapper for download
            html_content = markdown.markdown(unified_report, extensions=["fenced_code", "tables"])
            html_report = f"""
            <html>
            <head>
                <title>Unified Analysis Report</title>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; padding: 20px; max-width: 1200px; margin: auto; }}
                    h1, h2, h3, h4 {{ color: #333; }}
                    table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
                    th, td {{ border: 1px solid #ddd; padding: 8px; }}
                    th {{ background-color: #f2f2f2; text-align: left; }}
                </style>
            </head>
            <body>
                <h1>Unified Analysis Report</h1>
                {html_content}
            </body>
            </html>
            """
            with open("unified_analysis_report.html", "w", encoding="utf-8") as f:
                f.write(html_report)
                
            status.update(label="Analysis Complete! 🎉", state="complete", expanded=False)

        # ---------------------------------------------------------
        # DISPLAY RESULTS
        # ---------------------------------------------------------
        st.success("✅ Unified Analysis generated and saved to 'unified_analysis_report.txt' and 'unified_analysis_report.html'")
        
        st.header("📄 Unified Report")
        st.markdown(unified_report)
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            st.download_button(
                "⬇️ Download Text Report",
                data=unified_report,
                file_name="unified_analysis_report.txt",
                mime="text/plain",
                use_container_width=True
            )
        with col_btn2:
            st.download_button(
                "⬇️ Download HTML Report",
                data=html_report,
                file_name="unified_analysis_report.html",
                mime="text/html",
                use_container_width=True
            )
