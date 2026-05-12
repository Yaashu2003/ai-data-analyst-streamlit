import gemini_patch
import os
import sqlite3
import json
import uuid
import re
import difflib
from html import escape
import pandas as pd
import streamlit as st
import plotly.express as px
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict
from typing import List, Dict, Any, Literal, Optional
from pydantic import BaseModel, Field, ValidationError

# ---------------------------
# Load API Key
# ---------------------------
load_dotenv(override=True)

# ---------------------------
# Initialize Gemini Client
# ---------------------------
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
gemini_client = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else None
GEMINI_MODEL = "gemini-2.5-flash"

GENERIC_QUERY_TOKENS = {
    "a", "an", "and", "are", "brand", "brands", "by", "for", "from", "in", "is",
    "of", "on", "show", "the", "to", "top", "what", "whats", "which", "with",
}

ANALYSIS_QUERY_TOKENS = {
    "analysis", "analyse", "analyze", "breakdown", "insight", "insights",
    "overview", "summary", "trend", "trends",
}


def _normalize_text_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _collect_candidate_filters(frame: pd.DataFrame, limit: int = 40) -> Dict[str, set[str]]:
    candidates: Dict[str, set[str]] = {}
    for column in frame.columns:
        series = frame[column]
        if not pd.api.types.is_object_dtype(series) and not pd.api.types.is_string_dtype(series):
            continue
        unique_values = (
            series.dropna()
            .astype(str)
            .str.strip()
            .loc[lambda s: s.ne("")]
            .value_counts()
            .head(limit)
            .index.tolist()
        )
        normalized = {_normalize_text_token(value) for value in unique_values}
        normalized.discard("")
        if normalized:
            candidates[column] = normalized
    return candidates


def _query_phrases(query: str, max_terms: int = 3) -> list[str]:
    tokens = [token for token in query.split() if token]
    phrases: list[str] = []
    for size in range(1, min(max_terms, len(tokens)) + 1):
        for start in range(0, len(tokens) - size + 1):
            phrase = " ".join(tokens[start:start + size]).strip()
            if phrase:
                phrases.append(phrase)
    return phrases


def _best_query_match(query: str, values: set[str]) -> Optional[str]:
    phrases = _query_phrases(query)
    exact_candidates = []
    for value in values:
        if len(value) < 3 or value in GENERIC_QUERY_TOKENS:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])"
        if re.search(pattern, query):
            exact_candidates.append(value)
    if exact_candidates:
        return sorted(exact_candidates, key=len, reverse=True)[0]

    best_value = None
    best_score = 0.0
    for value in values:
        if len(value) < 3 or value in GENERIC_QUERY_TOKENS:
            continue
        for phrase in phrases:
            if len(phrase) < 3 or phrase in GENERIC_QUERY_TOKENS:
                continue
            score = difflib.SequenceMatcher(None, phrase, value).ratio()
            if score > best_score:
                best_score = score
                best_value = value
    return best_value if best_score >= 0.74 else None


def _infer_target_dimension(query: str, available_columns: list[str]) -> Optional[str]:
    dimension_aliases = {
        "brand": ["brand", "brands"],
        "category": ["category", "categories"],
        "source": ["source", "sources", "channel", "channels", "platform", "platforms"],
        "city": ["city", "cities"],
        "availability": ["availability", "stock", "stock status"],
        "name": ["product", "products", "item", "items", "name", "names"],
    }
    for column, aliases in dimension_aliases.items():
        if column not in available_columns:
            continue
        if any(alias in query for alias in aliases):
            return column
        alias_match = _best_query_match(query, {_normalize_text_token(alias) for alias in aliases})
        if alias_match:
            return column
    return None


def _resolve_query_filters(
    query: str,
    frame: pd.DataFrame,
    target_dimension: Optional[str] = None,
) -> tuple[list[str], dict[str, str]]:
    filter_clauses: list[str] = []
    matched_filters: dict[str, str] = {}
    for column, values in _collect_candidate_filters(frame).items():
        if column == target_dimension:
            continue
        matched_value = _best_query_match(query, values)
        if not matched_value:
            continue
        matched_filters[column] = matched_value
        filter_clauses.append(f'lower("{column}") = {_sql_quote(matched_value)}')
    return filter_clauses, matched_filters


def _default_analysis_dimension(frame: pd.DataFrame, excluded_columns: set[str]) -> Optional[str]:
    for column in ["category", "brand", "source", "city", "availability", "name"]:
        if column in frame.columns and column not in excluded_columns:
            return column
    return None


def _build_heuristic_sql(user_msg: str, frame: pd.DataFrame) -> Optional[str]:
    if frame is None or frame.empty:
        return None

    query = _normalize_text_token(user_msg)
    if not query:
        return None

    limit_match = re.search(r"\btop\s+(\d+)\b", query)
    limit = int(limit_match.group(1)) if limit_match else 10 if "top" in query else None
    wants_bottom = "bottom" in query
    sort_direction = "ASC" if wants_bottom else "DESC"
    wants_analysis = any(token in query for token in ANALYSIS_QUERY_TOKENS)

    target_dimension = _infer_target_dimension(query, list(frame.columns))
    filter_clauses, matched_filters = _resolve_query_filters(query, frame, target_dimension=target_dimension)
    filter_columns = set(matched_filters)

    if target_dimension in filter_columns:
        if target_dimension == "availability" and not wants_analysis and not limit and not wants_bottom:
            matched_filters.pop(target_dimension, None)
            filter_columns.discard(target_dimension)
            filter_clauses = [
                clause for clause in filter_clauses
                if f'lower("{target_dimension}")' not in clause
            ]
        else:
            target_dimension = None

    if target_dimension is None and (filter_clauses or wants_analysis):
        target_dimension = _default_analysis_dimension(frame, filter_columns)

    if target_dimension is None and not filter_clauses:
        return None

    metric_expr = 'COUNT(*)'
    metric_alias = "record_count"
    query_has_sales_words = any(token in query for token in ["sales", "revenue", "gmv", "value"])
    if query_has_sales_words and "price" in frame.columns:
        metric_expr = 'SUM(CAST("price" AS REAL))'
        metric_alias = "total_price"
    elif "price" in query and "price" in frame.columns:
        metric_expr = 'AVG(CAST("price" AS REAL))'
        metric_alias = "avg_price"

    if target_dimension == "name" and "product" not in query and "item" not in query and "name" not in query:
        return None

    sql_parts = ["FROM sales"]
    if filter_clauses:
        sql_parts.append("WHERE " + " AND ".join(filter_clauses))

    if target_dimension:
        select_parts = [f'"{target_dimension}"', f'{metric_expr} AS "{metric_alias}"']
        if wants_analysis and "price" in frame.columns:
            select_parts.append('AVG(CAST("price" AS REAL)) AS "avg_price"')
        sql_parts.insert(0, "SELECT " + ", ".join(select_parts))
        sql_parts.append(f'GROUP BY "{target_dimension}"')
        sql_parts.append(f'ORDER BY "{metric_alias}" {sort_direction}')
        if limit or wants_analysis:
            sql_parts.append(f"LIMIT {limit or 10}")
        return "\n".join(sql_parts)

    aggregate_parts = ['COUNT(*) AS "record_count"']
    if "price" in frame.columns:
        aggregate_parts.extend([
            'SUM(CAST("price" AS REAL)) AS "total_price"',
            'AVG(CAST("price" AS REAL)) AS "avg_price"',
        ])
    sql_parts.insert(0, "SELECT " + ", ".join(aggregate_parts))
    return "\n".join(sql_parts)


def _build_sql_generation_context(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "No dataset profile available."

    sample_rows = frame.head(5).fillna("").astype(str).to_dict(orient="records")
    numeric_summary = {}
    numeric_frame = frame.select_dtypes(include="number")
    for column in numeric_frame.columns[:6]:
        series = numeric_frame[column].dropna()
        if series.empty:
            continue
        numeric_summary[column] = {
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": round(float(series.mean()), 3),
        }

    categorical_examples = {}
    for column, values in _collect_candidate_filters(frame, limit=10).items():
        categorical_examples[column] = sorted(list(values))[:6]
        if len(categorical_examples) >= 8:
            break

    context = {
        "columns": list(frame.columns),
        "sample_rows": sample_rows,
        "numeric_summary": numeric_summary,
        "categorical_examples": categorical_examples,
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


def _looks_suspicious_sql(sql: str) -> bool:
    lowered = (sql or "").lower()
    for token in GENERIC_QUERY_TOKENS:
        if len(token) < 3:
            continue
        if re.search(rf'lower\(".*?"\)\s*=\s*\'{re.escape(token)}\'', lowered):
            return True
    return False


def _build_ranked_insight(user_msg: str, df: pd.DataFrame) -> Optional[str]:
    query = _normalize_text_token(user_msg)
    if not any(token in query for token in ["top", "bottom"]):
        return None
    if df is None or df.empty or len(df.columns) < 2:
        return None

    numeric_cols = list(df.select_dtypes(include="number").columns)
    text_cols = [col for col in df.columns if col not in numeric_cols]
    if not numeric_cols or not text_cols:
        return None

    label_col = text_cols[0]
    value_col = numeric_cols[0]
    ordered = df[[label_col, value_col]].dropna().copy()
    if ordered.empty:
        return None

    metric_label = value_col.replace("_", " ").title()
    heading_label = label_col.replace("_", " ").title()
    top_rows = ordered.head(10)
    ranked_lines = [
        f"{idx + 1}. **{row[label_col]}** — {row[value_col]:,.0f}"
        for idx, (_, row) in enumerate(top_rows.iterrows())
    ]
    leader = top_rows.iloc[0]
    tail = top_rows.iloc[-1]
    spread = float(leader[value_col]) - float(tail[value_col]) if len(top_rows) > 1 else float(leader[value_col])

    return "\n".join(
        [
            "### Key Findings & Trends",
            f"- Ranked the top {len(top_rows)} {heading_label.lower()} entries using **{metric_label.lower()}**.",
            f"- The current leader is **{leader[label_col]}** with **{leader[value_col]:,.0f}**.",
            "",
            "### Peaks & Lows / Top Performers",
            *[f"- {line}" for line in ranked_lines],
            "",
            "### Business Interpretation",
            f"- The gap between the highest and lowest entry in this ranked view is **{spread:,.0f}** {metric_label.lower()}.",
            f"- Use this ranking to focus inventory, promotions, or partnership review around the strongest **{heading_label.lower()}** performers first.",
        ]
    )


def _looks_like_datetime_series(series: pd.Series) -> bool:
    if series is None or series.empty:
        return False
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    return parsed.notna().mean() >= 0.6


def _finalize_chart_config(user_msg: str, df: pd.DataFrame, cfg_dict: Dict[str, Any]) -> Dict[str, Any]:
    if df is None or df.empty:
        return cfg_dict

    all_cols = list(df.columns)
    numeric_cols = list(df.select_dtypes(include="number").columns)
    x_col = cfg_dict.get("x_axis_column")
    y_col = cfg_dict.get("y_axis_column")
    chart_type = str(cfg_dict.get("chart_type", "bar")).lower()
    query = _normalize_text_token(user_msg)

    if x_col not in all_cols and x_col != "index":
        x_col = all_cols[0] if all_cols else "index"
        cfg_dict["x_axis_column"] = x_col
    if y_col and y_col not in all_cols:
        y_col = numeric_cols[0] if numeric_cols else None
        cfg_dict["y_axis_column"] = y_col

    x_series = None if x_col == "index" else df.get(x_col)
    x_is_datetime = _looks_like_datetime_series(x_series) if x_series is not None else False
    x_is_numeric = x_col in numeric_cols if x_col and x_col != "index" else False
    y_is_numeric = y_col in numeric_cols if y_col else False
    categorical_count = int(x_series.astype(str).nunique()) if x_series is not None and not x_is_numeric else 0

    asks_trend = any(token in query for token in ["trend", "timeline", "over time", "daily", "weekly", "monthly", "yearly"])
    asks_distribution = any(token in query for token in ["distribution", "spread", "variance", "outlier"])
    asks_mix = any(token in query for token in ["mix", "share", "composition", "breakdown", "split"])

    if x_is_datetime and y_is_numeric:
        cfg_dict["chart_type"] = "area" if asks_mix else "line"
        return cfg_dict

    if x_is_numeric and y_is_numeric:
        cfg_dict["chart_type"] = "scatter"
        return cfg_dict

    if asks_distribution and y_is_numeric and x_col != "index" and categorical_count and categorical_count <= 12:
        cfg_dict["chart_type"] = "box"
        return cfg_dict

    if asks_distribution and not y_col and x_is_numeric:
        cfg_dict["chart_type"] = "histogram"
        return cfg_dict

    if asks_mix and y_is_numeric and x_col != "index":
        if 4 <= categorical_count <= 18:
            cfg_dict["chart_type"] = "treemap"
            return cfg_dict
        if categorical_count <= 8:
            cfg_dict["chart_type"] = "pie"
            return cfg_dict

    if chart_type == "pie" and categorical_count > 8 and y_is_numeric:
        cfg_dict["chart_type"] = "treemap"
        return cfg_dict

    if chart_type == "treemap" and (not y_is_numeric or categorical_count < 3):
        cfg_dict["chart_type"] = "bar"
        return cfg_dict

    if chart_type in {"line", "area"} and not y_is_numeric:
        cfg_dict["chart_type"] = "bar"
        return cfg_dict

    if chart_type == "scatter" and not (x_is_numeric and y_is_numeric):
        cfg_dict["chart_type"] = "bar"
        return cfg_dict

    if chart_type == "box" and not y_is_numeric:
        cfg_dict["chart_type"] = "bar"
        return cfg_dict

    if chart_type == "histogram":
        if y_col and y_is_numeric and x_col == "index":
            cfg_dict["x_axis_column"] = y_col
            cfg_dict["y_axis_column"] = None
        elif not x_is_numeric:
            cfg_dict["chart_type"] = "bar"
        return cfg_dict

    if chart_type == "bar" and x_is_datetime and y_is_numeric and asks_trend:
        cfg_dict["chart_type"] = "line"

    return cfg_dict

def _gemini_invoke(prompt: str) -> str:
    """Helper to invoke Gemini and return the text response, with retry for rate limits."""
    import time
    if gemini_client is None:
        return "Gemini API key is not configured in this environment, so AI-generated dataset analysis is temporarily unavailable."
    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[prompt],
                config=types.GenerateContentConfig(temperature=0.05, max_output_tokens=4096),
            )
            return (response.text or "").strip()
        except Exception as e:
            err_msg = str(e)
            if ("429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg) and attempt < max_retries - 1:
                wait = 15 * (attempt + 1)  # 15s, 30s, 45s
                print(f"[RATE LIMIT] Gemini 429 â€” retrying in {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            raise

# ---------------------------
# Chatbot CSS
# ---------------------------
def inject_chatbot_css():
    st.markdown(
        """
    <style>
    .dataset-chat-shell {
      position: relative;
      overflow: hidden;
      background:
        radial-gradient(circle at top right, rgba(249, 115, 22, 0.16), transparent 30%),
        radial-gradient(circle at left center, rgba(15, 118, 110, 0.14), transparent 34%),
        linear-gradient(135deg, #0f1d2e 0%, #14324c 58%, #0f766e 100%);
      border: 1px solid rgba(191, 219, 254, 0.18);
      border-radius: 26px;
      padding: 24px;
      box-shadow: 0 18px 44px rgba(15, 23, 42, 0.14);
      margin-top: 0.5rem;
      margin-bottom: 1rem;
    }
    .dataset-chat-shell > * {
      position: relative;
      z-index: 1;
    }
    .dataset-chat-shell::after {
      content: "";
      position: absolute;
      width: 240px;
      height: 240px;
      right: -90px;
      top: -110px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(255,255,255,0.22) 0%, rgba(255,255,255,0) 72%);
      pointer-events: none;
    }
    .dataset-chat-eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 0.35rem 0.7rem;
      border-radius: 999px;
      background: rgba(255,255,255,0.12);
      border: 1px solid rgba(255,255,255,0.16);
      color: rgba(255,255,255,0.84);
      font-size: 0.8rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 0.8rem;
    }
    .dataset-chat-shell h3 {
      margin: 0 0 0.35rem;
      color: #f8fafc;
      font-size: 1.6rem;
      letter-spacing: -0.02em;
    }
    .dataset-chat-shell p {
      margin: 0;
      color: rgba(241, 245, 249, 0.82);
      line-height: 1.55;
    }
    .dataset-chat-metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 1rem;
    }
    .dataset-metric-chip {
      padding: 0.42rem 0.82rem;
      border-radius: 999px;
      background: rgba(255,255,255,0.10);
      border: 1px solid rgba(255,255,255,0.15);
      color: #f8fafc;
      font-size: 0.86rem;
      font-weight: 600;
    }
    .dataset-chat-tips {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 0.4rem 0 1rem;
    }
    .dataset-tip-card {
      background: linear-gradient(180deg, rgba(255,255,255,0.95) 0%, rgba(246,250,255,0.95) 100%);
      border: 1px solid #d8e4dc;
      border-radius: 18px;
      padding: 14px 16px;
      box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
    }
    .dataset-tip-title {
      color: #112031;
      font-size: 0.95rem;
      font-weight: 700;
      margin-bottom: 0.3rem;
    }
    .dataset-tip-copy {
      color: #5c6b7d;
      font-size: 0.88rem;
      line-height: 1.45;
    }
    .dataset-chat-input-row {
      background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(244,249,247,0.98) 100%);
      border: 1px solid #d6e4db;
      border-radius: 20px;
      padding: 14px 14px 6px 14px;
      box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
      margin: 0.85rem 0 1rem;
    }
    .dataset-quick-prompt-label {
      color: #516173;
      font-size: 0.84rem;
      margin-bottom: 0.45rem;
    }
    .dataset-chat-stream {
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin-top: 1rem;
      width: min(860px, 100%);
      margin-left: auto;
      margin-right: auto;
    }
    .chat-row {
      display: flex;
      width: 100%;
      justify-content: flex-start;
    }
    .chat-row.user {
      justify-content: flex-start;
    }
    .chat-row.assistant {
      justify-content: flex-start;
    }
    .chat-card {
      width: min(760px, 100%);
      max-width: 100%;
      padding: 16px 18px 14px;
      border-radius: 22px;
      border: 1px solid transparent;
      box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
      white-space: pre-wrap;
      line-height: 1.55;
    }
    .chat-card-label {
      display: block;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.09em;
      margin-bottom: 0.45rem;
      opacity: 0.72;
    }
    .chat-card.user {
      background: linear-gradient(135deg, #0f766e 0%, #115e59 100%);
      color: #f8fafc;
      border-color: rgba(15, 118, 110, 0.32);
      border-left: 4px solid rgba(255,255,255,0.34);
      margin-left: 0;
    }
    .chat-card.assistant {
      background: linear-gradient(180deg, #ffffff 0%, #f7fbff 100%);
      color: #162032;
      border-color: #d7e4f2;
      margin-right: 0;
    }
    .dataset-answer-block {
      background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
      border: 1px solid #d7e4f2;
      border-radius: 24px;
      padding: 18px 18px 16px;
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
      margin-bottom: 0.9rem;
    }
    .dataset-answer-kicker {
      color: #5c6b7d;
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 0.55rem;
      font-weight: 700;
    }
    .dataset-answer-question {
      background: linear-gradient(135deg, #0f766e 0%, #115e59 100%);
      color: #f8fafc;
      border-radius: 18px;
      padding: 14px 16px;
      margin-bottom: 0.9rem;
      box-shadow: 0 10px 22px rgba(15, 118, 110, 0.18);
    }
    .dataset-answer-question strong {
      display: block;
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      opacity: 0.8;
      margin-bottom: 0.35rem;
    }
    .dataset-answer-meta {
      color: #6b7a8c;
      font-size: 0.82rem;
      margin: 0.15rem 0 0.8rem;
    }
    .sql-card {
      background: linear-gradient(180deg, #0f172a 0%, #162235 100%);
      padding: 16px;
      border-radius: 18px;
      margin-top: 10px;
      border: 1px solid #22314a;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
    }
    .sql-card-caption {
      color: #dbeafe;
      font-size: 0.82rem;
      margin-bottom: 0.75rem;
    }
    .section-title {color:#0f766e; font-size:1.02rem; font-weight:700;}
    </style>
    """,
        unsafe_allow_html=True,
    )

# ---------------------------
# Caches for non-serializables
# ---------------------------
RESULTS_CACHE: Dict[str, pd.DataFrame] = {}
GRAPH_CACHE: Dict[str, Any] = {}

# ---------------------------
# Load CSV into SQLite (cached)
# ---------------------------
@st.cache_data
def load_data():
    return pd.read_csv("Superstore.csv", encoding="latin1")

@st.cache_resource
def init_db(data: pd.DataFrame):
    conn = sqlite3.connect("sales_temp.db", check_same_thread=False)
    data.to_sql("sales", conn, if_exists="replace", index=False)
    return conn

data = None
conn = None

def initialize_chatbot():
    global data, conn
    data = load_data()
    conn = init_db(data)

    return data, conn

# ---------------------------
# Pydantic Schema for ChartConfig
# ---------------------------
class ChartConfig(BaseModel):
    chart_type: Literal["bar", "line", "area", "pie", "treemap", "scatter", "histogram", "box"]
    x_axis_column: str
    y_axis_column: Optional[str] = None
    title_suffix: Optional[str] = Field(default="Data Visualization")

# ---------------------------
# LangGraph State (JSON-serializable only)
# ---------------------------
class State(TypedDict):
    messages: List[Dict[str, str]]
    intent: Optional[str]
    sql_query: Optional[str]
    sql_results_preview: Optional[List[Dict[str, Any]]]
    results_id: Optional[str]  # key into RESULTS_CACHE
    chart_config: Optional[Dict[str, Any]]
    graph_id: Optional[str]  # key into GRAPH_CACHE
    insight: Optional[str]

# ---------------------------
# Node 1 â€” Intent Detection
# ---------------------------
def intent_node(state: State):
    user_msg = state["messages"][-1]["content"]

    prompt = f"""
Identify user intent as ONLY one of:
SQL
INSIGHT

SQL â†’ If asking for numbers, totals, trends, grouped values, time-based analysis.
INSIGHT â†’ If asking for explanation, reasoning, patterns, or interpretation.

Return ONLY: SQL or INSIGHT.

User query: "{user_msg}"
"""
    reply = _gemini_invoke(prompt)
    intent = reply.strip().upper() or "INSIGHT"
    if intent not in ("SQL", "INSIGHT"):
        intent = "INSIGHT"
    return {**state, "intent": intent}

# ---------------------------
# Node 2 â€” SQL Generator
# ---------------------------
def sql_node(state: State):
    if state.get("intent") != "SQL":
        return state

    user_msg = state["messages"][-1]["content"]
    columns = ", ".join([f'"{c}"' for c in data.columns])

    prompt = f"""
You are an expert SQLite query generator.
Return ONLY a single valid SQLite SELECT query (no explanations).
TABLE: sales
COLUMNS: {columns}

DATE FORMAT ("Order Date" dd/mm/yyyy â†’ YYYY-MM-DD):
Use: SUBSTR("Order Date",7,4)||'-'||SUBSTR("Order Date",4,2)||'-'||SUBSTR("Order Date",1,2)

User query:
{user_msg}
"""
    reply = _gemini_invoke(prompt)

    sql = reply.strip()
    # Strip markdown code fences if present
    if sql.startswith("```"):
        sql = sql.strip("`").strip()
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()
    
    low = sql.lower()
    if "select" in low:
        sql = sql[low.index("select") :].strip()
    return {**state, "sql_query": sql}


def sql_node(state: State):
    if state.get("intent") != "SQL":
        return state

    user_msg = state["messages"][-1]["content"]
    columns = ", ".join([f'"{c}"' for c in data.columns])
    dataset_context = _build_sql_generation_context(data)

    prompt = f"""
You are an expert SQLite query generator.
Return ONLY a single valid SQLite SELECT query (no explanations).
TABLE: sales
COLUMNS: {columns}

IMPORTANT RULES:
- Always quote column names with double quotes.
- Always quote string filter values.
- For top/bottom grouped requests, aggregate by the requested dimension.
- If the user asks for top/bottom items without a metric, default to COUNT(*).
- Use the dataset profile below to infer which columns contain brands, sources, categories, prices, availability, cities, and similar filters.
- Do not treat filler words like "the", "what", or "top" as literal filter values unless they clearly appear as business values in the sample/profile.
- Use only SQLite syntax.

DATE FORMAT ("Order Date" dd/mm/yyyy → YYYY-MM-DD):
Use: SUBSTR("Order Date",7,4)||'-'||SUBSTR("Order Date",4,2)||'-'||SUBSTR("Order Date",1,2)

Dataset profile (schema, first 5 rows, numeric summary, categorical examples):
{dataset_context}

User query:
{user_msg}
"""
    reply = _gemini_invoke(prompt)

    sql = reply.strip()
    if sql.startswith("```"):
        sql = sql.strip("`").strip()
        if sql.lower().startswith("sql"):
            sql = sql[3:].strip()

    low = sql.lower()
    if "select" in low:
        sql = sql[low.index("select") :].strip()
    if sql.lower().startswith("select") and not _looks_suspicious_sql(sql):
        return {**state, "sql_query": sql}

    heuristic_sql = _build_heuristic_sql(user_msg, data)
    return {**state, "sql_query": heuristic_sql or sql}

# ---------------------------
# Node 3 â€” SQL Executor
# ---------------------------
def sql_exec_node(state: State):
    sql = state.get("sql_query")
    if not sql:
        return state

    try:
        df = pd.read_sql_query(sql, conn)
        results_id = str(uuid.uuid4())
        RESULTS_CACHE[results_id] = df
        preview = df.head(100).to_dict(orient="records")
        return {**state, "sql_results_preview": preview, "results_id": results_id}
    except Exception as e:
        err_preview = [{"error": f"SQL Error: {str(e)}"}]
        results_id = str(uuid.uuid4())
        RESULTS_CACHE[results_id] = pd.DataFrame(err_preview)
        return {**state, "sql_results_preview": err_preview, "results_id": results_id}

# ---------------------------
# Node 4 â€” Visualization Planner
# ---------------------------
def visualization_planner_node(state: State):
    preview = state.get("sql_results_preview")
    results_id = state.get("results_id")
    user_msg = state["messages"][-1]["content"]

    if not preview or len(preview) == 0:
        return {**state, "chart_config": None}

    df = RESULTS_CACHE.get(results_id, pd.DataFrame(preview))
    numeric_cols = list(df.select_dtypes(include="number").columns)
    all_cols = list(df.columns)

    prompt = f"""
Analyze the user's question and the SQL result data preview.
Return a JSON object ONLY with keys:
{{"chart_type": one of ["bar","line","area","pie","treemap","scatter","histogram","box"],
 "x_axis_column": string,
 "y_axis_column": string or null,
 "title_suffix": string}}

Rules:
- Choose x_axis_column as a categorical/grouping column (or "index").
- Choose y_axis_column as a numeric column if available, otherwise null.
- Prefer "line" or "area" for time/date trends.
- Prefer "treemap" or "pie" for composition/share questions.
- Prefer "scatter" when both axes are numeric.
- Prefer "histogram" for numeric distributions.
- Prefer "box" for spread/comparison by category.
- Keep names exactly as column headers in data.

User Question: "{user_msg}"
Columns: {all_cols}
Numeric columns: {numeric_cols}
Data preview (first rows): {json.dumps(preview[:5])}
"""
    raw = _gemini_invoke(prompt)

    try:
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
        parsed = json.loads(raw)
        cfg = ChartConfig.parse_obj(parsed)
        cfg_dict = cfg.dict()
        if cfg_dict["x_axis_column"] != "index" and cfg_dict["x_axis_column"] not in all_cols:
            cfg_dict["x_axis_column"] = all_cols[0] if all_cols else "index"
        if cfg_dict.get("y_axis_column") and cfg_dict["y_axis_column"] not in all_cols:
            cfg_dict["y_axis_column"] = numeric_cols[0] if numeric_cols else None
    except (json.JSONDecodeError, ValidationError, Exception):
        x_col = next((c for c in all_cols if c not in numeric_cols), "index")
        y_col = numeric_cols[0] if numeric_cols else None
        chart_type = "bar" if y_col else "pie"
        cfg_dict = {
            "chart_type": chart_type,
            "x_axis_column": x_col,
            "y_axis_column": y_col,
            "title_suffix": "Auto chart"
        }

    cfg_dict = _finalize_chart_config(user_msg, df, cfg_dict)

    return {**state, "chart_config": cfg_dict}

# ---------------------------
# Node 5 â€” Generate Graph
# ---------------------------
def generate_graph_node(state: State):
    cfg = state.get("chart_config")
    results_id = state.get("results_id")
    if not cfg or not results_id:
        return {**state, "graph_id": None}

    df = RESULTS_CACHE.get(results_id)
    if df is None or df.empty or "error" in df.columns:
        return {**state, "graph_id": None}

    chart_type = cfg.get("chart_type", "bar")
    x_col = cfg.get("x_axis_column")
    y_col = cfg.get("y_axis_column")
    title_suffix = cfg.get("title_suffix") or "Data Visualization"

    fig = None
    try:
        if chart_type == "pie":
            if y_col is None:
                series = df[x_col].value_counts().reset_index()
                series.columns = [x_col, "value"]
                fig = px.pie(series, names=x_col, values="value", title=f"Data Visualization: {title_suffix}")
            else:
                fig = px.pie(df, names=x_col if x_col != "index" else df.columns[0], values=y_col, title=f"Data Visualization: {title_suffix}")
        elif chart_type == "treemap":
            tree_names = x_col if x_col != "index" else df.columns[0]
            tree_values = y_col
            if tree_values is None:
                series = df[tree_names].value_counts().reset_index()
                series.columns = [tree_names, "value"]
                fig = px.treemap(series, path=[tree_names], values="value", title=f"Data Visualization: {title_suffix}")
            else:
                fig = px.treemap(df, path=[tree_names], values=tree_values, title=f"Data Visualization: {title_suffix}")
        elif chart_type == "histogram":
            hist_x = x_col if x_col != "index" else (y_col or (numeric_cols[0] if numeric_cols else df.columns[0]))
            fig = px.histogram(df, x=hist_x, title=f"Data Visualization: {title_suffix}", nbins=min(20, max(8, len(df) // 5 or 8)))
        elif chart_type == "box":
            box_x = None if x_col == "index" else x_col
            box_y = y_col or (numeric_cols[0] if numeric_cols else None)
            if box_y is not None:
                fig = px.box(df, x=box_x, y=box_y, points="outliers", title=f"Data Visualization: {title_suffix}")
        elif chart_type == "area":
            x_arg = df.index if x_col == "index" else x_col
            fig = px.area(df, x=x_arg, y=y_col, title=f"Data Visualization: {title_suffix}")
        else:
            x_arg = df.index if x_col == "index" else x_col
            fig = {
                "bar": px.bar,
                "line": px.line,
                "scatter": px.scatter,
            }[chart_type](df, x=x_arg, y=y_col, title=f"Data Visualization: {title_suffix}")
            if chart_type == "bar" and x_col != "index":
                fig.update_layout(xaxis={'categoryorder': 'total descending'})
        if fig is not None:
            fig.update_layout(
                template="plotly_white",
                colorway=["#2563eb", "#f97316", "#14b8a6", "#a855f7", "#ef4444", "#eab308"],
                margin=dict(l=56, r=24, t=70, b=76),
                paper_bgcolor="#ffffff",
                plot_bgcolor="#fcfdff",
            )
            fig.update_xaxes(showgrid=True, gridcolor="rgba(37, 99, 235, 0.08)", automargin=True)
            fig.update_yaxes(showgrid=True, gridcolor="rgba(37, 99, 235, 0.08)", automargin=True)
    except Exception as e:
        print(f"Plotly Generation Error: {e}")
        fig = None

    graph_id = None
    if fig is not None:
        graph_id = str(uuid.uuid4())
        GRAPH_CACHE[graph_id] = fig

    return {**state, "graph_id": graph_id}

# ---------------------------
# Node 6 â€” Insights
# ---------------------------
def insight_node(state: State):
    user_msg = state["messages"][-1]["content"]
    results_id = state.get("results_id")
    df = RESULTS_CACHE.get(results_id, pd.DataFrame())

    if df.empty or ("error" in df.columns and len(df) > 0):
        err_msg = df.iloc[0]["error"] if "error" in df.columns else "No results to analyze."
        return {**state, "insight": f"Could not analyze data. {err_msg}"}

    ranked_insight = _build_ranked_insight(user_msg, df)
    if ranked_insight:
        return {**state, "insight": ranked_insight}

    # Use to_markdown for clearer tabular data presentation to the LLM
    preview_text = df.head(10).to_markdown(index=False) 
    
    prompt = f"""
You are an expert data analyst. Based on the **USER QUERY** and the **SQL RESULT DATA**, provide a clear and comprehensive business insight summary.

**USER QUERY:**
{user_msg}

**SQL RESULTS (Top 10 rows):**
{preview_text}

You MUST structure your response using the following Markdown headings:
1.  **### Key Findings & Trends** (What are the most important conclusions?)
2.  **### Peaks & Lows / Top Performers** (Identify the highest and lowest values or key categorical leaders.)
3.  **### Business Interpretation** (Provide possible reasons and business implications for the findings.)

Return ONLY the structured text using these headings. Do not include any introductory or concluding remarks outside of the structured sections.
"""
    try:
        insight = _gemini_invoke(prompt)
        if not insight:
            insight = "No detailed insight generated by the model."
    except Exception as e:
        print(f"Insight Generation Error: {e}")
        insight = f"Error generating insight: {e}"
        
    return {**state, "insight": insight}
# ---------------------------
# Node 7 â€” Final Output
# ---------------------------
def output_node(state: State):
    insight_text = state.get('insight', 'No specific insights generated.')
    # The output now uses the structured text generated by insight_node
    out = insight_text 
    
    chart_cfg = state.get("chart_config")

    content_lines = [out]
    if chart_cfg:
        content_lines.append(f"\n\n**Suggested chart**: {chart_cfg.get('chart_type')}, X: {chart_cfg.get('x_axis_column')}, Y: {chart_cfg.get('y_axis_column')}")

    final_content = "\n\n".join(content_lines)
    new_messages = state["messages"] + [{"role": "assistant", "content": final_content}]
    return {**state, "messages": new_messages}
# ---------------------------
# Build LangGraph Flow
# ---------------------------
graph = StateGraph(State)
graph.add_node("intent", intent_node)
graph.add_node("sql_gen", sql_node)
graph.add_node("sql_exec", sql_exec_node)
graph.add_node("planner", visualization_planner_node)
graph.add_node("graph_gen", generate_graph_node)
graph.add_node("insight", insight_node)
graph.add_node("output", output_node)

graph.set_entry_point("intent")
graph.add_edge("intent", "sql_gen")
graph.add_edge("sql_gen", "sql_exec")
graph.add_edge("sql_exec", "planner")
graph.add_edge("planner", "graph_gen")
graph.add_edge("graph_gen", "insight")
graph.add_edge("insight", "output")
graph.add_edge("output", END)

app = graph.compile()

def render_chatbot_ui():
    inject_chatbot_css()

    if "conversation" not in st.session_state:
        st.session_state.conversation = []

    def submit_dataset_query(prompt_text: str):
        cleaned_prompt = (prompt_text or "").strip()
        if not cleaned_prompt:
            return

        st.session_state.conversation.append({"role": "user", "content": cleaned_prompt})

        init_state: State = {
            "messages": st.session_state.conversation.copy(),
            "intent": None,
            "sql_query": None,
            "sql_results_preview": None,
            "results_id": None,
            "chart_config": None,
            "graph_id": None,
            "insight": None,
        }
        result = app.invoke(init_state)
        assistant_msg = result["messages"][-1]["content"]
        st.session_state.conversation.append(
            {
                "role": "assistant",
                "content": assistant_msg,
                "results_id": result.get("results_id"),
                "graph_id": result.get("graph_id"),
            }
        )

    st.markdown(
        """
<div class="dataset-chat-shell">
  <div class="dataset-chat-eyebrow">Dataset Copilot</div>
  <h3>Ask the dataset directly</h3>
  <p>Run SQL-backed questions, review concise analyst answers, and inspect supporting charts without leaving this workspace.</p>
  <div class="dataset-chat-metrics">
    <span class="dataset-metric-chip">Live SQL answers</span>
    <span class="dataset-metric-chip">Charts when useful</span>
    <span class="dataset-metric-chip">Conversation memory</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown(
        """
<div class="dataset-chat-tips">
  <div class="dataset-tip-card">
    <div class="dataset-tip-title">Ask for totals</div>
    <div class="dataset-tip-copy">Compare categories, regions, profit, sales, or any available field with exact numbers.</div>
  </div>
  <div class="dataset-tip-card">
    <div class="dataset-tip-title">Request a chart</div>
    <div class="dataset-tip-copy">Use prompts like "show a monthly trend" or "plot category sales by region".</div>
  </div>
  <div class="dataset-tip-card">
    <div class="dataset-tip-title">Get interpretation</div>
    <div class="dataset-tip-copy">Ask why a pattern appears, where anomalies sit, or which segment is driving change.</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="dataset-quick-prompt-label">Quick starts</div>', unsafe_allow_html=True)
    prompt_options = [
        "Top 10 products by sales",
        "Monthly sales trend",
        "Compare profit by region",
        "Which segment is underperforming?",
    ]
    quick_prompt = None
    prompt_columns = st.columns(len(prompt_options))
    for index, prompt_label in enumerate(prompt_options):
        with prompt_columns[index]:
            if st.button(prompt_label, key=f"dataset_prompt_{index}", width="stretch"):
                quick_prompt = prompt_label

    st.markdown('<div class="dataset-chat-input-row">', unsafe_allow_html=True)
    col1, col2 = st.columns([10, 2])
    with col1:
        user_input = st.text_input(
            "Type your question...",
            key="user_input_box",
            label_visibility="collapsed",
            placeholder="Ask about trends, anomalies, top categories, totals, comparisons, or request a chart...",
        )
    with col2:
        send = st.button("Send", width="stretch")
    st.markdown('</div>', unsafe_allow_html=True)

    if quick_prompt:
        submit_dataset_query(quick_prompt)
    elif send and user_input:
        submit_dataset_query(user_input)

    assistant_count = len(
        [msg for msg in st.session_state.conversation if msg.get("role") == "assistant"]
    )
    st.caption(f"{assistant_count} dataset answers in this session. Recent answers appear first.")

    turns = []
    pending_user = None
    for msg in st.session_state.conversation:
        if msg.get("role") == "user":
            pending_user = msg
            continue
        if msg.get("role") == "assistant":
            turns.append({"user": pending_user, "assistant": msg})
            pending_user = None

    st.markdown('<div class="dataset-chat-stream">', unsafe_allow_html=True)
    for turn_index, turn in enumerate(reversed(turns), start=1):
        user_msg = turn.get("user") or {"content": ""}
        assistant_msg = turn.get("assistant") or {}

        st.markdown(
            f"""
<div class="dataset-answer-block">
  <div class="dataset-answer-kicker">Recent answer #{turn_index}</div>
  <div class="dataset-answer-question">
    <strong>You asked</strong>
    {escape(user_msg.get("content", ""))}
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        st.markdown(
            """
<div class="dataset-answer-meta">Dataset analyst response</div>
""",
            unsafe_allow_html=True,
        )
        st.markdown(assistant_msg.get("content", ""))

        graph_id = assistant_msg.get("graph_id")
        if graph_id:
            fig = GRAPH_CACHE.get(graph_id)
            if fig:
                st.plotly_chart(fig, width="stretch", key=f"dataset_chart_{graph_id}_{turn_index}")

        results_id = assistant_msg.get("results_id")
        if results_id:
            with st.expander(f"View result table for recent answer #{turn_index}", expanded=False):
                st.markdown("<div class='section-title'>SQL Query Output</div>", unsafe_allow_html=True)
                st.markdown("<div class='sql-card'>", unsafe_allow_html=True)
                df = RESULTS_CACHE.get(results_id, pd.DataFrame())
                if not df.empty:
                    st.markdown(
                        f"<div class='sql-card-caption'>{len(df):,} rows x {len(df.columns)} columns returned from the dataset query.</div>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(df, width="stretch")
                else:
                    st.warning("No data available for display.")
                st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

