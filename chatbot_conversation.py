import os
import sys
from typing import List
from pathlib import Path
from PIL import Image
import streamlit as st
from google import genai
from chart_assets import collect_canonical_chart_assets

# --- Import necessary components ---
try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from insights import setup_gemini, EnhancedInsightGenerator
    CHART_DIRECTORY = Path(EnhancedInsightGenerator.CHART_DIR_1)
    #CHART_DIRECTORY = r"C:\Users\Swarna\Desktop\NVIDIA_agenticAI\fin\chart"
except ImportError as e:
    st.error("FATAL: Could not import from local files (insights.py).")
    st.stop()

def find_and_load_charts() -> List[Image.Image]:
    loaded_images = []
    snapshot_dir = Path(__file__).resolve().parent / "chart_snapshots"
    assets = collect_canonical_chart_assets(charts_dir=CHART_DIRECTORY, html_dir=Path(__file__).resolve().parent / "charts_html")
    chart_candidates = []
    for asset in assets:
        png_path = asset.get("png_path")
        if png_path and Path(png_path).is_file():
            chart_candidates.append(Path(png_path))
            continue
        snapshot_path = snapshot_dir / f"{asset['stem']}_snapshot.png"
        if snapshot_path.is_file():
            chart_candidates.append(snapshot_path)
    for path in chart_candidates:
        try:
            loaded_images.append(Image.open(path))
        except Exception:
            pass
    return loaded_images

def load_report_content(file_path: Path) -> str:
    if not file_path.is_file():
        return ""
    try:
        return file_path.read_text(encoding='utf-8')
    except Exception:
        return ""

def display_chatbot():
    st.subheader("🤖 Data Insights Chat")

    # 1. Initialize Gemini Client & Session ONCE
    if "chat_session" not in st.session_state:
        with st.spinner("Initializing AI context..."):
            client, model_id = setup_gemini()
            if not client:
                st.error("Client initialization failed.")
                return

            # Store client in state to prevent "client closed" error
            st.session_state.gemini_client = client
            st.session_state.chat_session = client.chats.create(model=model_id)
            
            # Load Data
            loaded_images = find_and_load_charts()
            REPORT_PATH = Path(__file__).resolve().parent / "analysis_report.txt"
            report_content = load_report_content(REPORT_PATH)

            # System Instructions
            system_instr = """You are an expert Data Scientist. Use ONLY the provided charts and report text.
Your answers must be concise, readable, and business-focused.
Use short bullets, not long paragraphs.
Always structure answers with these headings whenever the question is about analysis, charts, visuals, or takeaways:
- **🎯 Performance Index**: 2-4 bullets with KPI summary, business health, or strongest metric.
- **📈 Highs & 📉 Lows**: 2-4 bullets on top performers, weak spots, anomalies, or contrasts.
- **💡 Key Driver**: 1-2 bullets with the main reason and what action it suggests.

If the user asks which charts or visualizations are useful, use this exact output style:
- one short intro sentence
- **🎯 Performance Index**
- chart suggestion bullets in this format:
  - **Chart Name**: X-Axis: `...`, Y-Axis: `...`, optional Legend/Group: `...`. (One business reason.)
- **📈 Highs & 📉 Lows**
- more chart suggestion bullets
- **💡 Key Driver**
- one bullet with the global slicer, segmentation, or filter recommendation

When recommending visuals, prioritize KPI cards, bar charts, correlation network, numeric relationship scatter, anomaly views, time trends, and segmentation views when the dataset supports them.
Do not invent data. Do not use generic filler. Keep it punchy and data-driven."""
            
            # Seed the chat with history/context
            initial_prompt = [
                system_instr,
                f"--- REPORT CONTENT ---\n{report_content}",
                *loaded_images,
                "I have provided the charts and report. Acknowledge and wait for my questions."
            ]
            
            # Send initial context
            st.session_state.chat_session.send_message(initial_prompt)
            st.session_state.chat_history = []

    # 2. Display Chat History
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 3. Handle User Input
    if user_input := st.chat_input("Ask a question about the charts..."):
        # Add user message to history
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Get AI Response
        with st.chat_message("assistant"):
            try:
                # IMPORTANT: Use the session from state
                response = st.session_state.chat_session.send_message(user_input)
                st.markdown(response.text)
                st.session_state.chat_history.append({"role": "assistant", "content": response.text})
            except Exception as e:
                st.error(f"Chat Error: {e}")
                # If client died, clear state to force re-init next time
                if "closed" in str(e).lower():
                    del st.session_state.chat_session
