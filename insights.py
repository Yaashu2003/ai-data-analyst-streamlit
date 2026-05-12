# insights.py
from state import IntelligentAnalysisState

from typing import TypedDict, List, Dict, Any, Optional, Tuple
import pandas as pd
import json
import numpy as np
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from google import genai
from PIL import Image, ImageDraw
from google.genai import types
from pathlib import Path
import warnings
import os
import re
import textwrap
from gemini_patch import generate_content_with_model_fallback
from chart_assets import collect_canonical_chart_assets

# Suppress warnings that might clutter the output
warnings.filterwarnings('ignore', category=UserWarning)

def setup_gemini(api_key=None):
    """Setup Google Gemini vision model"""
    print(" Checking if Gemini is available...")
    
    try:
        # Load directly if not provided to ensure we have the absolute latest from .env
        if not api_key:
            api_key = os.getenv("GEMINI_API_KEY")
            
        # Client initializes using explicitly provided key
        client = genai.Client(api_key=api_key)
        print(" Gemini client initialized successfully!")
        # IMPORTANT: Use gemini-2.5-flash as default, fall back to 1.5 if needed
        return client, "gemini-2.5-flash" 
    except Exception as e:
        print(f" Error initializing Gemini: {e}")
        print("Ensure your GEMINI_API_KEY is set correctly.")
        return None, None

class EnhancedInsightGenerator:
    """Enhanced insight generator with Google Gemini Vision"""
    
    # Chart directories relative to project root (script location)
    _INSIGHTS_BASE = Path(__file__).resolve().parent
    CHART_DIR_1 = str(_INSIGHTS_BASE / "charts")
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.gemini_client, self.gemini_model = setup_gemini()
        self.vlm_available = self.gemini_client is not None
    
    
    def _extract_plotly_metadata(self, html_path: str) -> Dict[str, Any]:
        metadata = {"title": "", "xaxis": "", "yaxis": "", "traces": []}
        try:
            html_text = Path(html_path).read_text(encoding="utf-8", errors="ignore")
            title_match = re.search(r'"title"\s*:\s*\{\s*"text"\s*:\s*"([^"]+)"', html_text)
            xaxis_match = re.search(r'"xaxis"\s*:\s*\{.*?"title"\s*:\s*\{\s*"text"\s*:\s*"([^"]+)"', html_text, re.DOTALL)
            yaxis_match = re.search(r'"yaxis"\s*:\s*\{.*?"title"\s*:\s*\{\s*"text"\s*:\s*"([^"]+)"', html_text, re.DOTALL)
            trace_matches = re.findall(r'"name"\s*:\s*"([^"]+)"', html_text)
            metadata["title"] = title_match.group(1).strip() if title_match else ""
            metadata["xaxis"] = xaxis_match.group(1).strip() if xaxis_match else ""
            metadata["yaxis"] = yaxis_match.group(1).strip() if yaxis_match else ""
            metadata["traces"] = [trace.strip() for trace in trace_matches[:5] if trace.strip()]
        except Exception as error:
            print(f" Warning: Could not parse HTML chart metadata for {html_path}: {error}")
        return metadata

    def _build_html_snapshot(self, asset: Dict[str, Any], metadata: Dict[str, Any]) -> Optional[str]:
        html_path = asset.get("html_path")
        if not html_path:
            return None

        snapshot_dir = self._INSIGHTS_BASE / "chart_snapshots"
        snapshot_dir.mkdir(exist_ok=True)
        output_path = snapshot_dir / f"{asset['stem']}_snapshot.png"

        try:
            image = Image.new("RGB", (1280, 720), "#f8fafc")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((40, 40, 1240, 680), radius=28, outline="#cbd5e1", width=4, fill="#ffffff")
            draw.rectangle((40, 40, 1240, 150), fill="#0f766e")

            title = metadata.get("title") or asset["stem"].replace("_", " ").title()
            subtitle = f"HTML fallback snapshot for {Path(html_path).name}"
            draw.text((80, 80), title[:120], fill="#ffffff")
            draw.text((80, 112), subtitle[:120], fill="#d1fae5")

            info_lines = [
                f"Chart stem: {asset['stem']}",
                f"Source: {asset.get('source', 'unknown')}",
            ]
            if metadata.get("xaxis"):
                info_lines.append(f"X axis: {metadata['xaxis']}")
            if metadata.get("yaxis"):
                info_lines.append(f"Y axis: {metadata['yaxis']}")
            if metadata.get("traces"):
                info_lines.append(f"Series: {', '.join(metadata['traces'][:4])}")

            y_pos = 200
            for line in info_lines:
                for wrapped in textwrap.wrap(line, width=82):
                    draw.text((88, y_pos), wrapped, fill="#0f172a")
                    y_pos += 34
                y_pos += 8

            notes = [
                "The original chart was available as interactive HTML only.",
                "This snapshot preserves chart metadata for VLM review when PNG export is unavailable.",
            ]
            y_pos += 24
            for note in notes:
                for wrapped in textwrap.wrap(note, width=88):
                    draw.text((88, y_pos), wrapped, fill="#475569")
                    y_pos += 30

            image.save(output_path)
            return str(output_path.resolve())
        except Exception as error:
            print(f" Warning: Failed to create HTML snapshot for {asset['stem']}: {error}")
            return None

    def _prepare_chart_assets_for_vlm(self, state: 'IntelligentAnalysisState') -> List[Dict[str, Any]]:
        canonical_assets = collect_canonical_chart_assets(state.get("chart_paths", []))
        print(f" Collected {len(canonical_assets)} canonical chart assets for VLM review.")

        selected_entries: List[Dict[str, Any]] = []
        snapshot_failures = 0
        for asset in canonical_assets:
            image_path = asset.get("png_path")
            metadata = {}
            if not image_path and asset.get("html_path"):
                metadata = self._extract_plotly_metadata(asset["html_path"])
                image_path = self._build_html_snapshot(asset, metadata)
                if not image_path:
                    snapshot_failures += 1
                    continue
            elif asset.get("html_path"):
                metadata = self._extract_plotly_metadata(asset["html_path"])

            if not image_path or not os.path.exists(image_path):
                snapshot_failures += 1
                continue

            selected_entries.append({
                "stem": asset["stem"],
                "display_name": Path(image_path).name,
                "image_path": image_path,
                "source": asset.get("source", "unknown"),
                "metadata": metadata,
            })
            if len(selected_entries) >= 10:
                break

        print(f" Selected {len(selected_entries)} chart assets for VLM.")
        print(f" Skipped {snapshot_failures} assets because no usable PNG or HTML snapshot was available.")
        return selected_entries


    def generate_comprehensive_insights(self, state: 'IntelligentAnalysisState') -> List[str]:
        """
        Generate insights with Gemini Vision, labeling each insight source.
        """
        insights = []
        
        try:
            # 1. Analyze specialized results (Text-based, LLM - Gemini)
            if state['specialized_analyses']:
                print("Running Specialized (Gemini) Analysis...")
                
                # Call the Gemini analysis, which now returns a single string
                specialized_insights_content = self._analyze_specialized_results(state)
                
                #  NEW: Label and append the single Gemini insight string
                if specialized_insights_content and isinstance(specialized_insights_content, str):
                    # Prepend the GEMINI label for the final report
                    insights.append(f"[SPECIALIZED INSIGHTS]\n{specialized_insights_content}")
            
            # 2. Analyze charts holistically with Gemini Vision (VLM)
            if self.vlm_available:
                all_charts_to_analyze = self._prepare_chart_assets_for_vlm(state)
                
                if all_charts_to_analyze:
                    print(f"Running Holistic VLM Analysis on {len(all_charts_to_analyze)} charts...")
                    
                    # Call the Gemini analysis, which now returns a single string
                    visual_insights_content = self._analyze_charts_holistically_with_gemini(state, all_charts_to_analyze) 
                    
                    #  NEW: Label and append the single Gemini insight string
                    if visual_insights_content and isinstance(visual_insights_content, str):
                        # Prepend the GEMINI label for the final report
                        insights.append(f"[GEMINI INSIGHTS]\n{visual_insights_content}")
            
        except Exception as e:
            # This is the last resort catch block for fatal, unexpected errors
            print(f" An unexpected FATAL error occurred: {str(e)}")
            insights.append(f"[FATAL ERROR]\nAn unexpected error occurred: {str(e)}")
        
        return insights
    
    
    def _analyze_charts_holistically_with_gemini(self, state: 'IntelligentAnalysisState', chart_entries: List[Dict[str, Any]]) -> str:
        """
        Use Google Gemini to analyze ALL charts simultaneously and generate a single, 
        comprehensive business intelligence report. Returns a single string report.
        """
        if not self.gemini_client:
            return " Gemini VLM not available for holistic chart analysis."
        
        dataset_name = state['dataset_info'].get('name', 'Business Dataset')
        dataset_shape = state['dataset'].shape
        
        # --- 1. Prepare Content for VLM ---
        content_parts = []
        chart_names = []
        
        metadata_sections = []
        for chart_entry in chart_entries:
            chart_path = chart_entry["image_path"]
            path = Path(chart_path)
            try:
                img = Image.open(chart_path)
                content_parts.append(img)
                chart_names.append(chart_entry.get("stem") or path.name)
                metadata = chart_entry.get("metadata") or {}
                metadata_lines = []
                if metadata.get("title"):
                    metadata_lines.append(f"title={metadata['title']}")
                if metadata.get("xaxis"):
                    metadata_lines.append(f"x_axis={metadata['xaxis']}")
                if metadata.get("yaxis"):
                    metadata_lines.append(f"y_axis={metadata['yaxis']}")
                if metadata.get("traces"):
                    metadata_lines.append(f"series={', '.join(metadata['traces'][:4])}")
                metadata_sections.append(
                    f"- {chart_entry.get('stem')}: source={chart_entry.get('source')} " +
                    ("; ".join(metadata_lines) if metadata_lines else "metadata unavailable")
                )
            except Exception as e:
                print(f" Error loading image {chart_path} (Skipping): {e}")
                continue
        
        if not content_parts:
            return " No readable charts were found for holistic VLM analysis."

        print(f" Sending {len(content_parts)} charts to Gemini VLM for holistic report generation...")
        
        # --- 2. Construct the Comprehensive Prompt ---
        #  Ensure the list is correctly formatted for Gemini
        chart_list_for_prompt = "\n".join([f"- **{name}**" for name in chart_names])
        chart_list_for_catalog = "\n".join([f"{i+1}. **{name}**: [Provide a 2-3 sentence strategic business insight. Do not describe colors or shapes. Focus on KPI meaning, risk, growth, anomaly, or action.]" for i, name in enumerate(chart_names)])
        chart_metadata_context = "\n".join(metadata_sections) if metadata_sections else "- No extra chart metadata extracted."
        
        holistic_prompt = f"""
You are a **Senior Business Analyst and Strategy Consultant**. Your task is to analyze the 
{len(content_parts)} provided charts holistically, treating them as a complete business intelligence dashboard 
for the **{dataset_name}** ({dataset_shape[0]:,} records).

You must provide an analysis that synthesizes information across all visuals and strictly adheres to the 
REQUIRED OUTPUT FORMAT below.

###  Reference File List (CRITICAL: COPY THESE NAMES EXACTLY)
This is the list of **{len(chart_names)}** chart filenames you must use for the **Chart Catalog (Section 0)**.
{chart_list_for_prompt}

### Supplemental Chart Metadata
Use this metadata to interpret HTML-backed fallback snapshots accurately whenever the image itself is simplified.
{chart_metadata_context}

###  EXECUTIVE SUMMARY REPORT (REQUIRED OUTPUT FORMAT)

#### 0. Chart Catalog
**CRITICAL INSTRUCTION**: Generate a complete list of ALL {len(chart_names)} charts. Use this exact format for every item, and do not stop until the list is complete.

{chart_list_for_catalog}

#### 1. Overall Performance Summary
(Write one short paragraph, ideally 70-110 words, on business health, KPI direction, and operating posture.)

#### 2. Key Insights & Drivers
Provide exactly 3 short bullets:
* **[Performance Driver]:** Identify the biggest positive contributor and quantify it when possible.
* **[Risk / Weakness]:** Identify the most important margin, demand, inventory, pricing, or operational risk.
* **[Trend / Pattern or Anomaly]:** Describe the strongest structural pattern or the most important outlier.

#### 3. Risks / Issues
Provide 2-3 concise bullets on the main commercial or analytical risks surfaced by the charts.

#### 4. Recommendations
Provide exactly 3 concise, business-ready actions. Tie each action back to chart evidence.

NOTE: Keep the report crisp and executive-friendly. Give chart insights from a business point of view, not a visual description. Do NOT say "this chart shows"; say what the chart means.


        """
        # --- 3. Execute VLM Call ---
        config = types.GenerateContentConfig(max_output_tokens=9000, temperature=0.35)
        content_parts_with_prompt = [holistic_prompt, *content_parts]

        try:
            response = generate_content_with_model_fallback(
                client=self.gemini_client,
                model="gemini-2.5-flash",
                contents=content_parts_with_prompt,
                config=config,
                api_key=self.api_key,
            )
            if response and response.text:
                return response.text.strip()
            return " VLM returned an empty response for the holistic analysis."
        except Exception as e:
            print(f" Error during holistic VLM analysis: {e}")
            return f" FATAL VLM ANALYSIS ERROR: {str(e)}"
    
    
    def _analyze_specialized_results(self, state: 'IntelligentAnalysisState') -> str:
        """
        Analyze specialized results with Gemini. Returns a single string.
        """
        
        summarized_results = {}
        for key, result in state['specialized_analyses'].items():
            if 'error' not in result:
                safe_result = self._make_json_safe(self._summarize_for_llm(result, max_items=10))
                summarized_results[str(key)] = safe_result 
        
        if not summarized_results:
            return "No specialized analysis results to analyze."
        
        specialized_prompt = f"""
        Analyze these business analysis results and provide key insights:
        
        {json.dumps(summarized_results, indent=2)}
        
        You are a **Senior Business Analyst and Strategy Consultant**.
        
        Provide the analysis in a bulleted list, focusing on the most important, quantified findings.
        """
        
        try:
            if not self.gemini_client:
                return "Gemini not available for specialized analysis."
                
            response = generate_content_with_model_fallback(
                client=self.gemini_client,
                model="gemini-2.5-flash",
                contents=[specialized_prompt],
                config=types.GenerateContentConfig(temperature=0.1),
                api_key=self.api_key,
            )
            
            if response and response.text:
                insights_list = [
                    item.strip(" -*•\n\r\t")
                    for item in re.split(r"(?:^|\n)\s*(?:[-*•]+|\d+\.)\s*", response.text)
                    if item.strip(" -*•\n\r\t")
                ]
                if not insights_list:
                    insights_list = [response.text.strip()]

                formatted_insights = "--- Specialized Analysis Results (Gemini) ---\n" + \
                                     "\n".join([f"- {item}" for item in insights_list if item])
                
                return formatted_insights
            else:
                return "Error analyzing specialized results: Gemini returned empty."
                
        except Exception as e:
            return f" Error analyzing specialized results: {str(e)}"
    
    
    # --- Utility functions remain unchanged ---

    def _make_json_safe(self, obj):
        """Convert dict keys and values to JSON-safe types, stringify Timestamps/Periods."""
        if isinstance(obj, dict):
            return {str(k): self._make_json_safe(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._make_json_safe(v) for v in obj]
        elif isinstance(obj, (pd.Timestamp, pd.Period)):
            return str(obj)
        else:
            return obj
    
    def _summarize_for_llm(self, data, max_items=10):
        """Recursive summarization for nested data structures"""
        if isinstance(data, dict):
            if len(data) > max_items:
                items = list(data.items())[:max_items]
                summary = dict(items)
                summary['_summary'] = f"... {len(data) - max_items} more items truncated"
                return summary
            return {k: self._summarize_for_llm(v, max_items) for k, v in data.items()}
        elif isinstance(data, list):
            if len(data) > max_items:
                return [self._summarize_for_llm(item, max_items) for item in data[:max_items]] + [f"... {len(data) - max_items} more items"]
            return [self._summarize_for_llm(item, max_items) for item in data]
        return data
# insights.py - Standalone function for generating insights using Gemini (including images)

from PIL import Image
from google.genai import types  # Import necessary Gemini API components

def generate_insights_from_report(gemini_client, gemini_model, user_query, report_content, image_paths=[]):
    """
    This function generates insights using Gemini for both text (report content) and images (charts).
    It takes a user query and generates insights based on both the report text and the provided images.
    """
    # Prepare the text-based prompt for Gemini
    text_prompt = f"""
    Based on the following report content, provide insights for the user query.

    **Report Content:**
    {report_content}

    **User Query:**
    {user_query}

    Provide the insights in a structured format, focusing on business trends, key findings, and recommendations.
    """

    # Combine text and images for Gemini VLM processing
    contents = [text_prompt]  # Text prompt first
    
    # Add images for VLM processing (send them to Gemini)
    images = []
    for image_path in image_paths:
        try:
            img = Image.open(image_path)
            images.append(img)
        except Exception as e:
            print(f"Error loading image {image_path}: {e}")
    
    # Use Gemini VLM to process both text and images
    if images:
        print(f"Sending {len(images)} images to Gemini VLM for analysis...")
        try:
            # Call Gemini's VLM to analyze both text and images
            response = gemini_client.models.generate_content(
                model=gemini_model,
                contents=contents + images,  # Include the images with the text prompt
                config=types.GenerateContentConfig(max_output_tokens=1000, temperature=0.4)
            )
            insight = response.text.strip() if response and response.text else "No insights generated."
            return insight
        except Exception as e:
            return f"Error generating insights with VLM: {str(e)}"

    # Fallback if no images provided, just generate insights based on text content
    else:
        # Call Gemini's model for text insights only
        response = gemini_client.models.generate_content(
            model=gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(max_output_tokens=1000, temperature=0.4)
        )
        insight = response.text.strip() if response and response.text else "No insights generated."
        return insight
