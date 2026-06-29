import gemini_patch
import os
import json
from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# ------------------------------
# MASTER PROMPT (YOUR FINAL ONE)
# ------------------------------
def build_prompt(charts):

    return f"""
You are a Senior Business Intelligence Analyst.

You are given Power BI charts in JSON format.

=========================
INPUT
=========================
{charts}

=========================
TASK
=========================

Perform full analytics and generate a structured report.

RETURN STRICTLY IN THIS TEXT FORMAT:

-----------------------------------------
Insight #1:
(IGNORE THIS - leave empty or minimal)
-----------------------------------------

Insight #2:
#### 0. Chart Catalog

For EACH chart:
1. Chart Title:
Explain what it shows + business insight

#### 1. Overall Performance Summary
(Executive summary)

#### 2. Key Insights & Drivers
(Bullet points)

#### 3. Data-Driven Recommendations
(Numbered recommendations)
-----------------------------------------

IMPORTANT:
- Use ALL charts
- No hallucination
- Business-focused
"""


# ------------------------------
# GENERATE REPORT TEXT
# ------------------------------
def generate_pbix_analysis_report(charts):

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = build_prompt(charts)

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=prompt,
    )

    return response.text


# ------------------------------
# SAVE REPORT
# ------------------------------
def save_report(report_text, output_path="analysis_report.txt"):

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print("âœ… Report saved:", output_path)
