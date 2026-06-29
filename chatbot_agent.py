# --------------------------------------------------------------
# chatbot_agent.py
# LangGraph Dataset Analysis Agent
# --------------------------------------------------------------

import uuid
import json
from typing_extensions import TypedDict
from typing import List, Dict, Any, Literal, Optional
from pydantic import BaseModel, Field, ValidationError
def clean_sql(sql):
    # Remove markdown code blocks
    sql = sql.replace("```sql", "")
    sql = sql.replace("```", "")
    return sql.strip()
import pandas as pd
import plotly.express as px
from langgraph.graph import StateGraph, END

# --------------------------------------------------------------
# Shared caches (used by main_app.py to display charts)
# --------------------------------------------------------------

RESULTS_CACHE: Dict[str, pd.DataFrame] = {}
GRAPH_CACHE: Dict[str, Any] = {}

# --------------------------------------------------------------
# Chart Config Schema
# --------------------------------------------------------------

class ChartConfig(BaseModel):

    chart_type: Literal["bar", "line", "pie", "scatter"]
    x_axis_column: str
    y_axis_column: Optional[str] = None
    title_suffix: Optional[str] = Field(default="Data Visualization")

# --------------------------------------------------------------
# Agent State
# --------------------------------------------------------------

class State(TypedDict):

    messages: List[Dict[str, str]]
    intent: Optional[str]
    sql_query: Optional[str]
    sql_results_preview: Optional[List[Dict[str, Any]]]
    results_id: Optional[str]
    chart_config: Optional[Dict[str, Any]]
    graph_id: Optional[str]
    insight: Optional[str]

# --------------------------------------------------------------
# Create LangGraph Agent
# --------------------------------------------------------------

def create_agent(llm, conn, data):

    if llm is None:
        raise ValueError("LLM not initialized")

    # ----------------------------------------------------------
    # Intent detection
    # ----------------------------------------------------------

    def intent_node(state: State):

        user_msg = state["messages"][-1]["content"]

        prompt = f"""
Classify the user intent.

Return ONLY one word:

SQL
INSIGHT

SQL = numeric analysis, grouped data, totals, trends  
INSIGHT = explanation, interpretation

User query:
{user_msg}
"""

        reply = llm.invoke([{"role": "user", "content": prompt}])

        intent = getattr(reply, "content", "").strip().upper()

        if intent not in ["SQL", "INSIGHT"]:
            intent = "INSIGHT"

        return {**state, "intent": intent}

    # ----------------------------------------------------------
    # SQL generation
    # ----------------------------------------------------------

    def sql_node(state: State):

        if state.get("intent") != "SQL":
            return state

        user_msg = state["messages"][-1]["content"]

        columns = ", ".join([f'"{c}"' for c in data.columns])

        prompt = f"""
Generate a SQLite SELECT query.

TABLE: sales

COLUMNS:
{columns}

User request:
{user_msg}

Return ONLY the SQL query.
"""

        reply = llm.invoke([{"role": "user", "content": prompt}])

        sql = getattr(reply, "content", "").strip()

        if "select" in sql.lower():
            sql = sql[sql.lower().index("select"):]

        return {**state, "sql_query": sql}

    # ----------------------------------------------------------
    # SQL execution
    # ----------------------------------------------------------

    def sql_exec_node(state: State):

        sql = state.get("sql_query")

        if not sql:
            return state

        try:

            df = pd.read_sql_query(sql, conn)

            results_id = str(uuid.uuid4())

            RESULTS_CACHE[results_id] = df

            preview = df.head(50).to_dict(orient="records")

            return {
                **state,
                "sql_results_preview": preview,
                "results_id": results_id
            }

        except Exception as e:

            err = [{"error": str(e)}]

            results_id = str(uuid.uuid4())

            RESULTS_CACHE[results_id] = pd.DataFrame(err)

            return {
                **state,
                "sql_results_preview": err,
                "results_id": results_id
            }

    # ----------------------------------------------------------
    # Visualization planning
    # ----------------------------------------------------------

    def visualization_planner_node(state: State):

        preview = state.get("sql_results_preview")
        results_id = state.get("results_id")

        if not preview:
            return {**state, "chart_config": None}

        df = RESULTS_CACHE.get(results_id)

        if df is None or df.empty:
            return {**state, "chart_config": None}

        numeric_cols = list(df.select_dtypes(include="number").columns)

        all_cols = list(df.columns)

        if not numeric_cols:
            return {**state, "chart_config": None}

        chart_cfg = {
            "chart_type": "bar",
            "x_axis_column": all_cols[0],
            "y_axis_column": numeric_cols[0],
            "title_suffix": "Auto chart"
        }

        return {**state, "chart_config": chart_cfg}

    # ----------------------------------------------------------
    # Chart generation
    # ----------------------------------------------------------

    def generate_graph_node(state: State):

        cfg = state.get("chart_config")
        results_id = state.get("results_id")

        if not cfg or not results_id:
            return {**state, "graph_id": None}

        df = RESULTS_CACHE.get(results_id)

        if df is None or df.empty:
            return {**state, "graph_id": None}

        chart_type = cfg["chart_type"]
        x = cfg["x_axis_column"]
        y = cfg["y_axis_column"]

        fig = None

        try:

            if chart_type == "bar":
                fig = px.bar(df, x=x, y=y)

            elif chart_type == "line":
                fig = px.line(df, x=x, y=y)

            elif chart_type == "scatter":
                fig = px.scatter(df, x=x, y=y)

            elif chart_type == "pie":
                fig = px.pie(df, names=x, values=y)

        except Exception:
            fig = None

        graph_id = None

        if fig:

            graph_id = str(uuid.uuid4())

            GRAPH_CACHE[graph_id] = fig

        return {**state, "graph_id": graph_id}

    # ----------------------------------------------------------
    # Insight generation
    # ----------------------------------------------------------

    def insight_node(state: State):

        results_id = state.get("results_id")

        df = RESULTS_CACHE.get(results_id, pd.DataFrame())

        if df.empty:

            return {**state, "insight": "No results available"}

        preview = df.head(10).to_markdown(index=False)

        user_msg = state["messages"][-1]["content"]

        prompt = f"""
Provide business insights based on this dataset.

User query:
{user_msg}

Data preview:
{preview}

Return concise analysis.
"""

        reply = llm.invoke([{"role": "user", "content": prompt}])

        insight = getattr(reply, "content", "")

        return {**state, "insight": insight}

    # ----------------------------------------------------------
    # Final output
    # ----------------------------------------------------------

    def output_node(state: State):

        insight = state.get("insight", "")

        graph_id = state.get("graph_id")
        results_id = state.get("results_id")

        content = insight

        if graph_id:
            content += f"\n\n(graph_id: {graph_id})"

        if results_id:
            content += f"\n(results_id: {results_id})"

        messages = state["messages"] + [
            {"role": "assistant", "content": content}
        ]

        return {**state, "messages": messages}

    # ----------------------------------------------------------
    # Build graph
    # ----------------------------------------------------------

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

    return graph.compile()