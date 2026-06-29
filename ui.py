# ui.py
import os
import sqlite3
import re
import pandas as pd
from dateutil import parser
import difflib
import plotly.express as px
from dotenv import load_dotenv
import streamlit as st
import streamlit.components.v1 as components
from typing_extensions import TypedDict
from typing import List, Dict, Any

# LLM + LangGraph imports
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from langgraph.graph import StateGraph, END

# -------------------------
# Config
# -------------------------
load_dotenv()
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
if not NVIDIA_API_KEY:
    raise RuntimeError("NVIDIA_API_KEY missing in .env (put it there or export it)")

CSV_FILE = "Superstore.csv"
DB_FILE = "sales_data.db"
MAX_SQL_RETRIES = 3
LLM_MODEL = "meta/llama-3.1-70b-instruct"
LLM_TEMPERATURE = 0.05

# -------------------------
# Load & prepare data (cached)
# -------------------------
@st.cache_data
def load_and_prepare_data(csv_path: str = CSV_FILE, db_path: str = DB_FILE) -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding="latin1")
    def normalize_date_col(col_series: pd.Series):
        def _norm(v):
            try:
                if pd.isna(v) or v == "":
                    return None
                return parser.parse(str(v), dayfirst=True).strftime("%Y-%m-%d")
            except Exception:
                return None
        return col_series.apply(_norm)

    for c in ("Order Date", "Ship Date", "OrderDate", "ShipDate"):
        if c in df.columns and df[c].dtype == object:
            df[c] = normalize_date_col(df[c])
    for c in list(df.columns):
        if "date" in c.lower() and df[c].dtype == object:
            df[c] = normalize_date_col(df[c])

    conn = sqlite3.connect(db_path)
    df.to_sql("sales", conn, if_exists="replace", index=False)
    conn.close()
    return df

df_master = load_and_prepare_data()
COLUMN_LIST = list(df_master.columns)
PREVIEW_5 = df_master.head(5).to_string(index=False)

def prefeed_context() -> str:
    cols = ", ".join([f'"{c}"' for c in COLUMN_LIST])
    return f"COLUMNS: {cols}\nFIRST_5_ROWS:\n{PREVIEW_5}\nNote: 'Order Date' values (if present) are in yyyy-mm-dd."

# -------------------------
# LLM factory (cached)
# -------------------------
@st.cache_resource
def get_llm():
    return ChatNVIDIA(model=LLM_MODEL, temperature=LLM_TEMPERATURE, api_key=NVIDIA_API_KEY)

llm = get_llm()

# -------------------------
# Utilities: column matching and safe sql replace
# -------------------------
def find_closest_col(token: str) -> str:
    if not token:
        return None
    token_clean = token.strip('"\'').strip()
    if token_clean in COLUMN_LIST:
        return token_clean
    low_map = {c.lower(): c for c in COLUMN_LIST}
    if token_clean.lower() in low_map:
        return low_map[token_clean.lower()]
    matches = difflib.get_close_matches(token_clean, COLUMN_LIST, n=1, cutoff=0.6)
    if matches:
        return matches[0]
    matches = difflib.get_close_matches(token_clean.lower(), [c.lower() for c in COLUMN_LIST], n=1, cutoff=0.6)
    if matches:
        for c in COLUMN_LIST:
            if c.lower() == matches[0]:
                return c
    return None

def replace_identifier_in_sql(sql: str, old: str, new: str) -> str:
    pattern = re.compile(r'(?P<prefix>["\']?)(?P<name>' + re.escape(old.strip('"\' ')) + r')(?P<suffix>["\']?)\b', flags=re.IGNORECASE)
    def _repl(m):
        return f'"{new}"'
    return pattern.sub(_repl, sql)

def auto_fix_sql(sql: str, last_error: str) -> str:
    fixed = sql
    if not last_error:
        return fixed
    m = re.search(r"no such column: ([\w\"']+)", last_error, flags=re.I)
    if m:
        token = m.group(1).strip('"\'')
        closest = find_closest_col(token)
        if closest:
            fixed = replace_identifier_in_sql(fixed, token, closest)
    for col in COLUMN_LIST:
        pattern = re.compile(r'(?<!["\'])\b' + re.escape(col) + r'\b(?!["\'])')
        fixed = pattern.sub(f'"{col}"', fixed)
    fixed = fixed.strip().rstrip(';')
    if '"Order Date"' in fixed and re.search(r'\b(19|20)\d{2}\b', fixed) and 'between' not in fixed.lower():
        y_match = re.search(r'\b(19|20)\d{2}\b', fixed)
        if y_match:
            y = y_match.group(0)
            if re.search(r'\bwhere\b', fixed, flags=re.I):
                fixed = fixed + f" AND \"Order Date\" >= '{y}-01-01' AND \"Order Date\" <= '{y}-12-31'"
            else:
                fixed = fixed + f" WHERE \"Order Date\" >= '{y}-01-01' AND \"Order Date\" <= '{y}-12-31'"
    return fixed

# -------------------------
# SQL executor with retries
# -------------------------
def try_execute_sql(conn: sqlite3.Connection, sql: str):
    try:
        df_sql = pd.read_sql(sql, conn)
        return True, "", df_sql
    except Exception as e:
        return False, str(e), pd.DataFrame()

def execute_with_retries(sql: str, max_retries: int = MAX_SQL_RETRIES) -> Dict[str, Any]:
    conn = sqlite3.connect(DB_FILE)
    attempts = 0
    current_sql = sql.strip().rstrip(';')
    last_err = ""
    df_sql = pd.DataFrame()
    while attempts < max_retries:
        attempts += 1
        ok, err, df_try = try_execute_sql(conn, current_sql)
        if ok:
            df_sql = df_try
            last_err = ""
            break
        last_err = err
        fixed = auto_fix_sql(current_sql, last_err)
        if fixed != current_sql:
            current_sql = fixed
            continue
        prompt = (
            "You are a SQLite expert. The SQL below produced an error. "
            "Provide a corrected single SQLite SELECT statement only, using the table 'sales' and the known columns. "
            "Do not invent columns. If you cannot fix it, respond with 'CANNOT_FIX'.\n\n"
            f"SQL: {current_sql}\nERROR: {last_err}\n\nCorrected SELECT:"
        )
        llm_resp = llm.invoke([{"role":"user","content":prompt}]).content.strip()
        if "CANNOT_FIX" in llm_resp.upper():
            break
        if "select" in llm_resp.lower():
            corrected = llm_resp[llm_resp.lower().index("select"):].strip().rstrip(';')
            if corrected != current_sql:
                current_sql = corrected
                continue
        break
    conn.close()
    if df_sql.empty and last_err:
        return {"ok": False, "error": last_err, "attempts": attempts, "df": pd.DataFrame(), "sql": current_sql}
    return {"ok": True, "error": "", "attempts": attempts, "df": df_sql, "sql": current_sql}

# -------------------------
# LangGraph State Type
# -------------------------
class State(TypedDict):
    messages: List[Dict[str, Any]]
    intent: str
    sql_query: str
    sql_results: str
    insight: str
    df: pd.DataFrame
    chart_html: str
    attempts: int

# -------------------------
# Nodes (safe early returns)
# -------------------------
def intent_node(state: State) -> State:
    user_msg = state["messages"][-1]["content"]
    low = user_msg.lower()
    if any(word in low for word in ("plot","chart","graph","visualize","visualisation","visualization","show me a chart","bar chart","line chart","scatter")):
        intent = "SQL+CHART"
    elif any(word in low for word in ("insight","analyze","analysis","trends","summary","explain","review","why","recommend")):
        prompt = f"""Classify this single user query as exactly one token: SQL, INSIGHT, or SQL+CHART.
Context: {prefeed_context()}
User query: "{user_msg}"
Respond with exactly one token."""
        resp = llm.invoke([{"role":"user","content":prompt}]).content.strip().upper()
        intent = resp.split()[0] if resp and resp.split()[0] in ("SQL","INSIGHT","SQL+CHART") else "INSIGHT"
    else:
        prompt = f"""Classify this single user query as exactly one token: SQL, INSIGHT, or SQL+CHART.
Context: {prefeed_context()}
User query: "{user_msg}"
Respond with exactly one token."""
        resp = llm.invoke([{"role":"user","content":prompt}]).content.strip().upper()
        intent = resp.split()[0] if resp and resp.split()[0] in ("SQL","INSIGHT","SQL+CHART") else "SQL"
    return {**state, "intent": intent}

def sql_generator_node(state: State) -> State:
    if state.get("intent") not in ("SQL", "SQL+CHART"):
        return state
    user_msg = state["messages"][-1]["content"]
    base_prompt = f"""
You are an expert SQLite SELECT generator for the table 'sales'.
Prefeed: {prefeed_context()}

User request: {user_msg}

OUTPUT RULES (must follow):
- Output only one valid SQLite SELECT statement (no explanations).
- Use double quotes around column identifiers (e.g., "Order Date").
- If a year like 2020 is mentioned, use "Order Date" BETWEEN 'YYYY-01-01' AND 'YYYY-12-31'.
- Do not use unsupported SQLite functions (e.g., ILIKE, DATE_TRUNC).
- If aggregating, include GROUP BY and ORDER BY when appropriate.
"""
    sql = llm.invoke([{"role":"user","content":base_prompt}]).content.strip()
    if "select" in sql.lower():
        sql = sql[sql.lower().index("select"):].strip().rstrip(';')
    else:
        sql = f'SELECT * FROM sales LIMIT 100'
    return {**state, "sql_query": sql, "attempts": 0}

def sql_executor_node(state: State) -> State:
    sql = state.get("sql_query", "")
    if not sql or state.get("intent") not in ("SQL", "SQL+CHART"):
        return state
    res = execute_with_retries(sql)
    if not res["ok"]:
        formatted = f"SQL could not be executed after {res['attempts']} attempts. Last error: {res['error']}\nFinal SQL tried:\n{res['sql']}"
        return {**state, "sql_results": formatted, "df": pd.DataFrame(), "attempts": res["attempts"]}
    df_sql = res["df"]
    formatted_preview = df_sql.head(20).to_string(index=False)
    return {**state, "sql_results": formatted_preview, "df": df_sql, "attempts": res["attempts"], "sql_query": res["sql"]}

def chart_node(state: State) -> State:
    if state.get("intent") != "SQL+CHART":
        return {**state, "chart_html": ""}
    df_sql = state.get("df")
    if df_sql is None or df_sql.empty:
        return {**state, "chart_html": ""}
    df_work = df_sql.copy()
    date_col = next((c for c in df_work.columns if "date" in c.lower()), None)
    numeric_cols = df_work.select_dtypes(include="number").columns.tolist()
    cat_cols = df_work.select_dtypes(include="object").columns.tolist()
    fig = None
    try:
        if date_col and numeric_cols:
            df_work[date_col] = pd.to_datetime(df_work[date_col], errors="coerce")
            y = numeric_cols[0]
            fig = px.line(df_work.sort_values(by=date_col), x=date_col, y=y, title=f"{y} over {date_col}")
        elif cat_cols and numeric_cols:
            x = cat_cols[0]
            y = numeric_cols[0]
            if df_work.shape[0] > 30:
                df_plot = df_work.groupby(x)[y].sum().reset_index().sort_values(y, ascending=False).head(20)
                fig = px.bar(df_plot, x=x, y=y, title=f"Top {len(df_plot)} {x} by {y}")
            else:
                fig = px.bar(df_work, x=x, y=y, title=f"{y} by {x}")
        elif len(numeric_cols) >= 2:
            fig = px.scatter(df_work, x=numeric_cols[0], y=numeric_cols[1], title=f"{numeric_cols[1]} vs {numeric_cols[0]}")
        elif len(numeric_cols) == 1:
            fig = px.histogram(df_work, x=numeric_cols[0], nbins=20, title=f"Distribution of {numeric_cols[0]}")
        else:
            if df_work.shape[1] >= 2:
                x = df_work.columns[0]
                y = df_work.columns[1]
                try:
                    df_work[y] = pd.to_numeric(df_work[y], errors="coerce")
                    fig = px.bar(df_work, x=x, y=y, title=f"{y} by {x}")
                except:
                    fig = None
    except Exception:
        fig = None
    chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn') if fig else ""
    return {**state, "chart_html": chart_html}

def insight_node(state: State) -> State:
    df_sql = state.get("df")
    if (state.get("intent") == "INSIGHT") and (df_sql is None or df_sql.empty):
        prompt = f"""
You are a business analyst. The user asked: "{state['messages'][-1]['content']}".
We have the schema: {', '.join(COLUMN_LIST)}.
If you need data, propose one or two short SQLite SELECT statements that would help answer the question, and then give a short (1-3 sentence) high level recommendation. Output format:
SQL1: <sql or NONE>
SQL2: <sql or NONE>
RECOMMENDATION: <short text>
"""
        resp = llm.invoke([{"role":"user","content":prompt}]).content.strip()
        return {**state, "insight": resp}
    if df_sql is None or df_sql.empty:
        return {**state, "insight": "No data available to generate insights."}
    df_small = df_sql.copy().iloc[:, :12].head(50)
    numeric_summary = df_small.describe(include="all").head(10).to_string()
    prompt = f"""
You are a business analyst. Use ONLY the data provided below.

User query: {state['messages'][-1]['content']}

Numeric summary:
{numeric_summary}

First up to 10 rows:
{df_small.head(10).to_string(index=False)}

Instructions:
1) Give a short summary (1-3 sentences) of the most important numeric findings.
2) Explicitly call out top 3 and bottom 3 items if applicable (use column names).
3) Mention any clear monthly/seasonal patterns if a date column exists.
4) Do NOT invent numbers or mention columns that are not present.
"""
    resp = llm.invoke([{"role":"user","content":prompt}]).content.strip()
    return {**state, "insight": resp}

def output_node(state: State) -> State:
    out = {
        "sql": state.get("sql_query",""),
        "sql_results_preview": state.get("sql_results",""),
        "chart_html": state.get("chart_html",""),
        "insight": state.get("insight","(no insights)"),
        "attempts": state.get("attempts",0)
    }
    assistant_text = "Full Analysis available in UI panels."
    return {**state, "messages": state["messages"] + [{"role":"assistant","content":assistant_text}], "output_struct": out}

# -------------------------
# LangGraph initializer (helper to build a fresh app each invocation)
# -------------------------
def initialize_app_fresh():
    graph = StateGraph(State)
    graph.add_node("intent", intent_node)
    graph.add_node("sql_gen", sql_generator_node)
    graph.add_node("sql_exec", sql_executor_node)
    graph.add_node("chart", chart_node)
    graph.add_node("insight", insight_node)
    graph.add_node("output", output_node)
    graph.set_entry_point("intent")
    graph.add_edge("intent", "sql_gen")
    graph.add_edge("sql_gen", "sql_exec")
    graph.add_edge("sql_exec", "chart")
    graph.add_edge("chart", "insight")
    graph.add_edge("insight", "output")
    graph.add_edge("output", END)
    return graph.compile()

# -------------------------
# Streamlit UI (one-shot per query)
# -------------------------
st.set_page_config(page_title="Sales Chatbot (One-shot UI)", layout="wide")
st.title("Sales Chatbot — One-shot per Query")
st.markdown("Every query is treated as a fresh one-shot. Use the text box below and press Send. The chatbot will recompile and run per submission.")

if "conversation_log" not in st.session_state:
    st.session_state.conversation_log = []

# Input area with safe clearing via callback

# Use clear_on_submit directly in the form's text_area widget
with st.form(key="input_form", clear_on_submit=True):
    user_query = st.text_area("Your message", height=140, key="user_query", placeholder="e.g. Show sales by Category in 2019 or Summarize monthly trends for Profit")
    c1, c2 = st.columns([1, 1])
    send = c1.form_submit_button("Send")
    clear = c2.form_submit_button("Clear (client-only)")

    # Clear the input when the button is pressed
    if clear:
        st.session_state["user_query"] = ""  # This will clear the text input but still keep it inside the session state
        st.experimental_rerun()


if send:
    q = st.session_state.get("user_query", "").strip()
    if not q:
        st.warning("Please type a query before hitting Send.")
    else:
        # record user message in sidebar log (visual only)
        st.session_state.conversation_log.append({"role":"user","content":q})

        # create a fresh LangGraph app and invoke it (one-shot)
        app = initialize_app_fresh()
        conv = {"messages":[{"role":"user","content":q}]}
        out = app.invoke(conv)

        # append assistant text to visual log
        if "messages" in out:
            # the assistant message is appended by output_node; show the assistant short message in sidebar log
            assistant_msg = out["messages"][-1]["content"]
            st.session_state.conversation_log.append({"role":"assistant","content":assistant_msg})

        output_struct = out.get("output_struct", {})
        # Display structured outputs in main pane
        with st.expander("Assistant Output", expanded=True):
            st.subheader("SQL (generated)")
            sql_text = output_struct.get("sql","(no sql)")
            st.code(sql_text or "(none)")

            st.subheader("SQL Results (preview)")
            sql_preview = output_struct.get("sql_results_preview","(no results)")
            st.text(sql_preview)

            st.subheader("Insight")
            st.write(output_struct.get("insight","(no insights)"))

            chart_html = output_struct.get("chart_html","")
            if chart_html:
                st.subheader("Interactive Chart")
                components.html(chart_html, height=520, scrolling=True)
            else:
                st.info("No chart generated for this query (intent did not request a chart or data unsuitable).")

        # If full df present, show table and offer download
        df_out = out.get("df")
        if isinstance(df_out, pd.DataFrame) and not df_out.empty:
            st.subheader("Full SQL Results (first 1000 rows shown)")
            st.dataframe(df_out.head(1000))
            csv_bytes = df_out.to_csv(index=False).encode('utf-8')
            st.download_button("Download results as CSV", csv_bytes, file_name="query_results.csv", mime="text/csv")

        # clear input safely by calling callback and rerunning once
        clear_input_callback()
        st.experimental_rerun()

# Sidebar conversation log and helpers
with st.sidebar:
    st.header("🧭 App Navigation")
    st.page_link("Home.py", label="📄 Report Viewer", icon="📄")
    st.page_link("ui.py", label="🤖 Data Chatbot", icon="🤖")
    st.page_link("ui_report_powerbi.py", label="💬 Power BI Chatbot", icon="💬")
    st.page_link("ui_combined_pipeline.py", label="🔮 Unified Analysis Pipeline", icon="🔮")
    st.markdown("---")

st.sidebar.header("Conversation (visual-only)")
for m in st.session_state.conversation_log[-50:]:
    if m["role"] == "user":
        st.sidebar.markdown(f"**You:** {m['content']}")
    else:
        st.sidebar.markdown(f"**Assistant:** {m['content']}")

if st.sidebar.button("Clear visual log"):
    st.session_state.conversation_log = []
    st.experimental_rerun()

st.sidebar.header("Examples")
st.sidebar.write("- `Show sales by Category for 2019`")
st.sidebar.write("- `Plot monthly revenue trend for 2020`")
st.sidebar.write("- `Summarize key insights for Profit by Region`")
