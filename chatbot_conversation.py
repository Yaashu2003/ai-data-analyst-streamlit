from pathlib import Path

import streamlit as st


def load_report_content(file_path: Path) -> str:
    if not file_path.is_file():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _build_chart_text_context(max_charts: int = 12) -> str:
    """Build lightweight chart context without loading image files."""
    charts = st.session_state.get("charts", []) or []
    if not charts:
        return "No extracted dashboard chart context is currently available."

    lines = []
    for index, chart in enumerate(charts[:max_charts], 1):
        title = chart.get("title", "Untitled visual")
        platform = chart.get("dashboard_platform") or chart.get("platform") or "Dashboard"
        chart_type = chart.get("chart_type") or chart.get("visual_type") or "chart"
        dimension = chart.get("dimension") or "category"
        measure = chart.get("measure_used") or chart.get("measure") or "value"
        x_sample = ", ".join(str(value) for value in (chart.get("x") or [])[:5])
        y_sample = ", ".join(str(value) for value in (chart.get("y") or [])[:5])
        status = "data-backed" if chart.get("data_available", True) else "layout-only"
        lines.append(
            f"{index}. {title} ({platform}, {chart_type}, {status}) - "
            f"dimension: {dimension}; measure: {measure}; "
            f"x sample: {x_sample or 'n/a'}; y sample: {y_sample or 'n/a'}"
        )

    if len(charts) > max_charts:
        lines.append(f"... plus {len(charts) - max_charts} more extracted visual(s).")
    return "\n".join(lines)


def _init_report_chat_session() -> bool:
    if "report_chat_session" in st.session_state:
        return True

    with st.spinner("Initializing lightweight report chat..."):
        try:
            from insights import setup_gemini
        except Exception as error:
            st.error(f"Gemini setup is unavailable. Check insights.py imports. Error: {error}")
            return False

        client, model_id = setup_gemini()
        if not client:
            st.error("Gemini client initialization failed.")
            return False

        st.session_state.report_gemini_client = client
        st.session_state.report_chat_session = client.chats.create(model=model_id)

        report_path = Path(__file__).resolve().parent / "analysis_report.txt"
        report_content = load_report_content(report_path)
        chart_context = _build_chart_text_context()

        system_instr = """
You are an expert data analyst. Use only the provided report text and compact chart metadata.
Keep answers concise, readable, and business-focused.
When the question asks for analysis, use these sections:
- Performance Index: KPI summary, strongest metric, or business health.
- Highs & Lows: top performers, weak spots, anomalies, or contrasts.
- Key Driver: the main reason and recommended action.
Do not invent data. If exact chart rows are needed, tell the user to open the BI Chart Workspace.
""".strip()

        initial_prompt = [
            system_instr,
            f"--- REPORT CONTENT ---\n{report_content}",
            f"--- EXTRACTED CHART METADATA SUMMARY ---\n{chart_context}",
            "Acknowledge the context silently and wait for questions.",
        ]
        st.session_state.report_chat_session.send_message(initial_prompt)
        st.session_state.report_chat_history = []
        return True


def display_chatbot():
    st.subheader("Report Insights Chat")
    st.caption(
        "This report chat is lightweight. It uses report text and compact chart metadata. "
        "Use BI Chart Workspace for merged Power BI/Tableau chart comparisons and generated visuals."
    )
    for legacy_key in ("chat_session", "chat_history", "gemini_client"):
        st.session_state.pop(legacy_key, None)

    if "report_chat_session" not in st.session_state and not st.session_state.get("report_chat_enabled"):
        st.info("Start the report chat only when you need it. This keeps the report page responsive.")
        if st.button("Start report chat", key="start_report_chat", width="stretch"):
            st.session_state.report_chat_enabled = True
            st.rerun()
        return

    if not _init_report_chat_session():
        return

    if st.button("Reset report chat", key="reset_report_chat", width="stretch"):
        for key in ("report_chat_session", "report_chat_history", "report_gemini_client", "report_chat_enabled"):
            st.session_state.pop(key, None)
        st.rerun()

    for msg in st.session_state.get("report_chat_history", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if user_input := st.chat_input("Ask a question about the report...", key="report_chat_input"):
        st.session_state.report_chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            try:
                response = st.session_state.report_chat_session.send_message(user_input)
                response_text = response.text or ""
                st.markdown(response_text)
                st.session_state.report_chat_history.append(
                    {"role": "assistant", "content": response_text}
                )
            except Exception as error:
                st.error(f"Chat Error: {error}")
                if "closed" in str(error).lower():
                    st.session_state.pop("report_chat_session", None)
