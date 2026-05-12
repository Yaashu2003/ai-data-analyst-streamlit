import streamlit as st
import os
from pathlib import Path
import rag_pipeline
import json
from pbix_chart_viz import build_pbix_figure


PBIX_ANALYSIS_SECTIONS = (
    ("overall_performance_summary", "Overall Performance Summary"),
    ("key_insights_and_drivers", "Key Insights & Drivers"),
    ("risks_and_issues", "Risks / Issues"),
    ("data_driven_recommendations", "Data-Driven Recommendations"),
    ("insights", "Supporting Insights"),
)


# =====================================================
# PAGE CONFIG
# =====================================================

if __name__ == "__main__":
    st.set_page_config(
        page_title="BI.AI",
        page_icon="🧠",
        layout="wide"
    )

    st.markdown(
        """
    <style>
    div[data-testid="stPopover"] {
        position: fixed;
        top: 120px;
        right: 40px;
        width: 360px;
        z-index: 999999;
    }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # =====================================================
    # SESSION STATE
    # =====================================================

    if "charts" not in st.session_state:
        st.session_state["charts"] = []

    if "selected_chart" not in st.session_state:
        st.session_state["selected_chart"] = None


    # =====================================================
    # HEADER
    # =====================================================

    st.title("🧠 BI.AI")
    st.caption("Upload Power BI reports and analyze dashboards using natural language.")


    # =====================================================
    # FILE UPLOAD
    # =====================================================

    REPORT_INPUT = "report_input"
    os.makedirs(REPORT_INPUT, exist_ok=True)

    uploaded_files = st.file_uploader(
        "Upload Power BI (.pbix) files and optional exported CSV/XLSX fallback files",
        type=["pbix", "csv", "xlsx", "xls"],
        accept_multiple_files=True
    )

    if uploaded_files:

        if st.button("🚀 Process Reports"):

            with st.spinner("Processing reports..."):
                pbix_files = [file for file in uploaded_files if Path(file.name).suffix.lower() == ".pbix"]
                export_files = [file for file in uploaded_files if Path(file.name).suffix.lower() in {".csv", ".xlsx", ".xls"}]
                export_dir = os.path.join(REPORT_INPUT, "_exports")
                os.makedirs(export_dir, exist_ok=True)

                for file in pbix_files:

                    path = os.path.join(REPORT_INPUT, file.name)

                    with open(path, "wb") as f:
                        f.write(file.getbuffer())

                external_paths = []
                for file in export_files:
                    path = os.path.join(export_dir, file.name)
                    with open(path, "wb") as f:
                        f.write(file.getbuffer())
                    external_paths.append(path)

                charts = rag_pipeline.process_all_reports(REPORT_INPUT, external_data_files=external_paths)
                summary = rag_pipeline.get_last_extraction_summary()

                st.session_state["charts"] = charts

                st.success(
                    f"{summary.get('chart_data_count', 0)} charts with embedded data, "
                    f"{summary.get('metadata_visual_count', 0)} visual definitions extracted"
                )
                if summary.get("metadata_visual_count", 0):
                    st.info(
                        "Some visuals were extracted from the PBIX layout only. "
                        "If this PBIX was created from the Power BI cloud service, "
                        "the underlying dataset may not be embedded locally."
                    )
                if summary.get("fallback_alias_count", 0):
                    st.success(
                        f"Fallback exports were used for {summary.get('fallback_alias_count', 0)} missing table alias mappings."
                    )


# =====================================================
# CHART RENDERER
# =====================================================

def render_chart(chart):

    if not chart.get("data_available", True):
        field_list = ", ".join(
            f"{row.get('role')}: {row.get('table') + '.' if row.get('table') else ''}{row.get('field')}"
            for row in chart.get("field_rows", [])[:8]
        ) or "Not detected"
        st.markdown(
            f"""
<div style="border:1px solid #d7e3dc;border-radius:16px;padding:16px 18px;background:#f8fbff;">
    <div style="font-weight:700;color:#12324d;margin-bottom:8px;">{chart.get("title", "Visual")}</div>
    <div style="color:#516173;margin-bottom:10px;">{chart.get("data_note", "This visual definition was extracted, but embedded chart data was not available.")}</div>
    <div style="font-size:0.92rem;color:#213547;"><strong>Page:</strong> {chart.get("page", "Unknown")}</div>
    <div style="font-size:0.92rem;color:#213547;"><strong>Visual type:</strong> {chart.get("chart_type", "unknown")}</div>
    <div style="font-size:0.92rem;color:#213547;"><strong>Required tables:</strong> {", ".join(chart.get("required_tables", []) or ["Not detected"])}</div>
    <div style="font-size:0.92rem;color:#213547;"><strong>Fields:</strong> {field_list}</div>
</div>
""",
            unsafe_allow_html=True,
        )
        return

    fig = build_pbix_figure(chart)
    if fig is None:
        st.warning("No data for chart")
        return
    st.plotly_chart(fig, use_container_width=True)


def _render_pbix_section_value(value):

    if isinstance(value, list):
        for item in value:
            st.markdown(f"- {item}")
        return

    if isinstance(value, dict):
        for key, item in value.items():
            st.markdown(f"- **{str(key).replace('_', ' ').title()}**: {item}")
        return

    st.markdown(str(value))


def render_pbix_analysis_sections(reply_data):

    lead_parts = []
    for key in ("text", "answer", "explanation"):
        value = reply_data.get(key)
        if value:
            lead_parts.append(str(value))

    if lead_parts:
        st.markdown("\n\n".join(lead_parts))

    rendered_any = bool(lead_parts)
    for key, label in PBIX_ANALYSIS_SECTIONS:
        value = reply_data.get(key)
        if not value:
            continue
        st.markdown(f"**{label}**")
        _render_pbix_section_value(value)
        rendered_any = True

    if rendered_any:
        return

    fallback_parts = []
    for key, value in reply_data.items():
        if key in {"response_type", "title", "chart_type", "x", "y", "series"}:
            continue
        label = str(key).replace("_", " ").title()
        if isinstance(value, list):
            joined = "\n".join(f"- {item}" for item in value)
            fallback_parts.append(f"**{label}**:\n{joined}")
        else:
            fallback_parts.append(f"**{label}**: {value}")

    if fallback_parts:
        st.markdown("\n\n".join(fallback_parts))
    else:
        st.markdown(f"(Received empty or unparseable response: `{reply_data}`)")


def render_pbix_chat_response(reply_data):

    if not isinstance(reply_data, dict):
        st.markdown(str(reply_data))
        return

    response_type = reply_data.get("response_type", "")
    if response_type == "new_chart" and "title" in reply_data:
        st.markdown(
            f"**AI:** Here is the newly generated chart: *{reply_data.get('title', 'Generated Chart')}*"
        )
        render_chart(reply_data)

    render_pbix_analysis_sections(reply_data)


# =====================================================
# CHAT QUERY
# =====================================================

if __name__ == "__main__":
    # =====================================================
    # CHAT QUERY
    # =====================================================
    
    with st.popover("💬 Ask about Report"):
        st.write("Ask questions about the charts and report.")
        
        # Add Chart Previews directly in the chatbot popover
        charts = st.session_state.get("charts", [])
        if charts:
            with st.expander("📊 Extracted Charts Preview", expanded=False):
                chart_titles = [c.get("title", "Untitled") for c in charts]
                selected_title = st.selectbox("Select a chart to preview:", chart_titles, key="chat_preview_chart")
                for c in charts:
                    if c.get("title", "Untitled") == selected_title:
                        render_chart(c)
                        break
                        
        query = st.chat_input("Ask about your dashboard...")
        
        if query:
            # Display user query in chat format
            with st.chat_message("user"):
                st.markdown(query)
                
            charts = st.session_state.get("charts", [])
        
            if not charts:
                with st.chat_message("assistant"):
                    st.warning("Upload reports first")
            else:
                with st.spinner("Analyzing dashboards..."):
                    result = rag_pipeline.ask_gemini_charts(query, charts)
        
                # Handle string JSON
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except:
                        with st.chat_message("assistant"):
                            st.write(result)
                        st.stop()
        
                if isinstance(result, dict):
                    with st.chat_message("assistant"):
                        render_pbix_chat_response(result)

                if isinstance(result, dict) and False:
                    rtype = result.get("response_type", "")
        
                    with st.chat_message("assistant"):
                        # ------------------------------------
                        # CHART RESPONSE
                        # ------------------------------------
                        if rtype == "new_chart":
                            render_chart(result)
                            insights = result.get("insights", [])
                            if insights:
                                st.subheader("📌 Insights")
                                for ins in insights:
                                    st.markdown(f"• {ins}")
                        elif rtype == "explanation":
                            st.subheader("📌 Insights")
                            
                            text_parts = []
                            if "text" in result and result["text"]: text_parts.append(str(result["text"]))
                            if "explanation" in result and result["explanation"]: text_parts.append(str(result["explanation"]))
                            if "answer" in result and result["answer"]: text_parts.append(str(result["answer"]))
                            if "insights" in result and result["insights"]:
                                if isinstance(result["insights"], list):
                                    text_parts.append("\n".join(f"- {i}" for i in result["insights"]))
                                else:
                                    text_parts.append(str(result["insights"]))
                                    
                            if not text_parts:
                                for k, v in result.items():
                                    if k not in ["response_type", "title", "chart_type", "x", "y", "series"]:
                                        if isinstance(v, list):
                                            text_parts.append(f"**{str(k).replace('_', ' ').title()}**:\n" + "\n".join(f"- {i}" for i in v))
                                        else:
                                            text_parts.append(f"**{str(k).replace('_', ' ').title()}**: {v}")
        
                            out_text = "\n\n".join(text_parts).strip()
                            if out_text:
                                st.markdown(out_text)
                            else:
                                st.markdown(f"(Received empty or unparseable response: `{result}`)")
    
    
    # =====================================================
    # SIDEBAR NAVIGATOR
    # =====================================================
    
    with st.sidebar:
        st.header("🧭 App Navigation")
        st.page_link("Home.py", label="📄 Report Viewer", icon="📄")
        st.page_link("ui.py", label="🤖 Data Chatbot", icon="🤖")
        st.page_link("ui_report_powerbi.py", label="💬 Power BI Chatbot", icon="💬")
        st.page_link("ui_combined_pipeline.py", label="🔮 Unified Analysis Pipeline", icon="🔮")
        st.markdown("---")

    charts = st.session_state.get("charts", [])
    
    if charts:
    
        with st.sidebar:
    
            st.header("📂 Report Navigator")
    
            reports = {}
    
            for c in charts:
    
                r = c["report"]
                p = c["page"]
                t = c["title"]
    
                reports.setdefault(r, {})
                reports[r].setdefault(p, [])
                reports[r][p].append(t)
    
            report_select = st.selectbox("Report", list(reports.keys()))
    
            page_select = st.selectbox(
                "Page",
                list(reports[report_select].keys())
            )
    
            chart_select = st.selectbox(
                "Chart",
                reports[report_select][page_select]
            )
    
            for c in charts:
    
                if (
                    c["report"] == report_select
                    and c["page"] == page_select
                    and c["title"] == chart_select
                ):
    
                    st.subheader("📊 Preview")
    
                    render_chart(c)
    
                    break
    
            st.markdown("---")
    
            st.subheader("📊 Chart Index")
    
            for report in reports:
    
                st.markdown(f"**📁 {report}**")
    
                for page in reports[report]:
    
                    st.markdown(f"• **{page}**")
    
                    for chart in reports[report][page]:
    
                        if st.button(chart, key=f"{report}_{page}_{chart}"):
    
                            for c in charts:
    
                                if (
                                    c["report"] == report
                                    and c["page"] == page
                                    and c["title"] == chart
                                ):
                                    st.session_state["selected_chart"] = c
    
    
    # =====================================================
    # RIGHT PANEL CHART VIEWER
    # =====================================================
    
    selected_chart = st.session_state.get("selected_chart")
    
    if selected_chart:
    
        st.subheader("📊 Chart Viewer")
    
        render_chart(selected_chart)
