# --------------------------------------------------------------
# data_agent_core.py — Shared Setup, Data Loading, and LLM/LangGraph Logic
# --------------------------------------------------------------
import os
import sqlite3
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

import streamlit as st
from langchain_nvidia_ai_endpoints import ChatNVIDIA 
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict
from typing import List, Dict, Any
import re
import plotly.express as px

# --- Configuration and Paths (relative to project root for portability) ---
load_dotenv(override=True)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

_BASE_DIR = Path(__file__).resolve().parent
REPORT_HTML = _BASE_DIR / "interactive_analysis_report.html"
CHARTS_HTML_DIR = _BASE_DIR / "charts_html"
SUPERSTORE_CSV = _BASE_DIR / "Superstore.csv"
SQLITE_DB_PATH = _BASE_DIR /"sales_temp.db" 

# --------------------------------------------------------------
# --- Data Loading & Database Functions (Cached) ---
# --------------------------------------------------------------

@st.cache_data(show_spinner="Loading and preparing data...")
def load_data(csv_path: str | Path):
    if not Path(csv_path).is_file():
        return pd.DataFrame()

    df = pd.read_csv(csv_path, encoding="latin1")
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
    conn = sqlite3.connect(db_path, check_same_thread=False)
    if not df.empty:
        df.to_sql("sales", conn, if_exists="replace", index=False)
    return conn

# --------------------------------------------------------------
# --- Global State Initialization ---
# --------------------------------------------------------------
data = load_data(SUPERSTORE_CSV)
conn = init_db(data, SQLITE_DB_PATH)

llm = None
if NVIDIA_API_KEY:
    try:
        llm = ChatNVIDIA(model="meta/llama-3.1-70b-instruct", temperature=0.05, api_key=NVIDIA_API_KEY)
    except Exception:
        llm = None
        
# --------------------------------------------------------------
# --- LangGraph Definition (The Agent's Core Workflow) ---
# --------------------------------------------------------------
class State(TypedDict):
    messages: List[Dict]
    intent: str
    sql_query: str
    sql_results: Any
    insight: str
    graph: Any

def intent_node(state: State):
    user_msg = state["messages"][-1]["content"]
    intent = "SQL"
    if llm:
        prompt = f"""
Identify user intent as:
SQL → If asking for numbers, totals, trends, grouped values, time-based analysis.
INSIGHT → If asking for explanation, reasoning, patterns, or interpretation.
Return ONLY: SQL or INSIGHT.
User query: "{user_msg}"
"""
        try:
            res = llm.invoke([{"role": "user", "content": prompt}])
            intent = res.content.strip().upper()
            if intent not in ("SQL", "INSIGHT"): intent = "SQL"
        except Exception:
            pass
    return {**state, "intent": intent}

def sql_node(state: State):
    if state["intent"] != "SQL" or data.empty:
        return {**state, "sql_query": ""}

    user_msg = state["messages"][-1]["content"]
    columns = ", ".join([f'"{c}"' for c in data.columns])

    sql = ""
    if llm:
        prompt = f"""
You are an expert SQLite query generator.
Return ONLY a valid SELECT query, no explanation.
TABLE: sales
COLUMNS: {columns}
DATE FORMAT ("Order Date" dd/mm/yyyy → YYYY-MM-DD): Use 'order_date' column
User query: {user_msg}
"""
        try:
            sql_raw = llm.invoke([{"role": "user", "content": prompt}]).content.strip()
            sql_clean = re.sub(r"```sql|```", "", sql_raw, flags=re.IGNORECASE).strip()
            if "select" in sql_clean.lower():
                sql = sql_clean
        except Exception:
            pass

    if not sql:
        sql = 'SELECT * FROM sales LIMIT 10'

    return {**state, "sql_query": sql}

def sql_exec_node(state: State):
    sql = state.get("sql_query")
    if not sql: return {**state, "sql_results": pd.DataFrame()} 

    try:
        df = pd.read_sql_query(sql, conn)
        fig = None
        
        # Simple visualization logic from original deploy.py
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        x_candidates = df.select_dtypes(include=["object", "datetime"]).columns.tolist()

        if not df.empty and numeric_cols and x_candidates:
            x_col = x_candidates[0]
            y_col = numeric_cols[0]
            try:
                if "date" in x_col.lower():
                    df[x_col] = pd.to_datetime(df[x_col], errors="coerce")
                    df = df.dropna(subset=[x_col])
                fig = px.line(df, x=x_col, y=y_col, markers=True, title=f"📈 Trend of {y_col} by {x_col}")
            except Exception:
                fig = None
                
        return {**state, "sql_results": df, "graph": fig}
    except Exception as e:
        return {**state, "sql_results": pd.DataFrame({"error": [f"SQL Execution Error: {str(e)}"]})}

def insight_node(state: State):
    df = state.get("sql_results")
    user_msg = state["messages"][-1]["content"]
    insight = ""
    
    if df is not None and not df.empty and 'error' not in df.columns:
        if llm:
            try:
                prompt = f"""
Provide a clear business insight summary based on the following data.
User question: {user_msg}
SQL Results (first 10 rows):
{df.head(10).to_string(index=False)}
Include: Trends, Peaks & lows, Possible reasons, Business interpretation.
"""
                insight = llm.invoke([{"role": "user", "content": prompt}]).content.strip()
            except Exception:
                insight = "Could not generate insight due to LLM failure."
        else:
            insight = "LLM not initialized. Cannot generate insight."
    elif df is not None and 'error' in df.columns:
        insight = f"Error during analysis: {df['error'][0]}"
    else:
        insight = "No meaningful data available for analysis."
        
    return {**state, "insight": insight}

def output_node(state: State):
    insight_text = state.get('insight', 'No specific insights generated.')
    out = f"### 🧠 Insights\n{insight_text}"
    return {**state, "messages": state["messages"] + [{"role": "assistant", "content": out}]}

# Function to compile the graph, called by the Chatbot page
def compile_langgraph():
    graph = StateGraph(State)
    graph.add_node("intent", intent_node)
    graph.add_node("sql_gen", sql_node)
    graph.add_node("sql_exec", sql_exec_node)
    graph.add_node("insight", insight_node)
    graph.add_node("output", output_node)
    graph.set_entry_point("intent")
    graph.add_edge("intent", "sql_gen")
    graph.add_edge("sql_gen", "sql_exec")
    graph.add_edge("sql_exec", "insight")
    graph.add_edge("insight", "output")
    graph.add_edge("output", END)
    return graph.compile()