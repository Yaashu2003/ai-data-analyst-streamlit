import gemini_patch
import os
import json
import uuid
import re
import difflib
from pathlib import Path
from html import escape
import pandas as pd
import streamlit as st
import plotly.express as px
import duckdb
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
GEMINI_MODEL = "gemini-3.1-flash-lite"

GENERIC_QUERY_TOKENS = {
    "a", "an", "and", "are", "brand", "brands", "by", "for", "from", "in", "is",
    "of", "on", "show", "the", "to", "top", "what", "whats", "which", "with",
}

ANALYSIS_QUERY_TOKENS = {
    "analysis", "analyse", "analyze", "breakdown", "insight", "insights",
    "overview", "summary", "trend", "trends",
}

SOURCE_FILE_COLUMN = "source_file"
DATASET_INPUT_DIR = Path(__file__).resolve().parent / "dataset_input"
DATASET_MANIFEST = DATASET_INPUT_DIR / "manifest.json"
DATA_PATH = Path(__file__).resolve().parent / "Superstore.csv"
MULTI_FILE_QUERY_TOKENS = {
    "csv", "csvs", "dataset", "datasets", "file", "files", "input", "inputs",
    "source", "sources", "upload", "uploaded", "compare", "comparison",
}

DATA_QUERY_TOKENS = {
    "chart", "compare", "count", "average", "avg", "sum", "total", "top",
    "bottom", "trend", "rank", "ranking", "highest", "lowest", "best", "worst",
    "branch", "branches", "recovery", "performance", "recommendation",
    "recommendations", "risk", "risks", "insight", "insights",
}


def _normalize_text_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()


def _sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _duckdb_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _safe_table_name(file_name: str, index: int) -> str:
    stem = Path(str(file_name or f"dataset_{index + 1}")).stem.lower()
    safe = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    if not safe:
        safe = f"dataset_{index + 1}"
    if re.match(r"^\d", safe):
        safe = f"dataset_{safe}"
    return f"csv_{index + 1}_{safe[:42]}"


def _read_dataset_manifest() -> Dict[str, Any]:
    if not DATASET_MANIFEST.exists():
        return {}
    try:
        return json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_table_file(path: str) -> pd.DataFrame:
    table_path = Path(path)
    if table_path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(table_path)
    return pd.read_csv(table_path, encoding="latin1")


def _load_combined_dataset_from_manifest() -> Optional[pd.DataFrame]:
    """Build the chatbot bridge table from the uploaded source files, not from disk fallback."""
    manifest = _read_dataset_manifest()
    files = manifest.get("files", [])
    if not files:
        return None

    frames = []
    for index, item in enumerate(files):
        file_name = item.get("name") or f"dataset_{index + 1}"
        file_path = item.get("path")
        if not file_path or not Path(file_path).exists():
            continue

        frame = _read_table_file(file_path).copy()
        frame[SOURCE_FILE_COLUMN] = file_name
        frame["source_row_number"] = range(1, len(frame) + 1)
        frames.append(frame)

    if not frames:
        return None
    return pd.concat(frames, ignore_index=True, sort=False)


def _sample_table_context(frame: pd.DataFrame, max_columns: int = 16) -> Dict[str, Any]:
    numeric_summary = {}
    for column in frame.select_dtypes(include="number").columns[:6]:
        series = frame[column].dropna()
        if series.empty:
            continue
        numeric_summary[column] = {
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": round(float(series.mean()), 3),
        }

    categorical_examples = {}
    for column in frame.select_dtypes(include=["object", "category", "string"]).columns[:8]:
        values = (
            frame[column].dropna().astype(str).str.strip().loc[lambda s: s.ne("")]
            .value_counts().head(5).index.tolist()
        )
        if values:
            categorical_examples[column] = values

    return {
        "columns": list(frame.columns)[:max_columns],
        "row_count": int(len(frame)),
        "sample_rows": frame.head(3).fillna("").astype(str).to_dict(orient="records"),
        "numeric_summary": numeric_summary,
        "categorical_examples": categorical_examples,
    }


def _strip_sql_fences(sql: str) -> str:
    clean = (sql or "").strip()
    if clean.startswith("```"):
        clean = clean.strip("`").strip()
        if clean.lower().startswith("sql"):
            clean = clean[3:].strip()
    lowered = clean.lower()
    if lowered.startswith(("select", "with")):
        pass
    elif "with" in lowered and ("select" not in lowered or lowered.index("with") < lowered.index("select")):
        clean = clean[lowered.index("with"):]
    elif "select" in lowered:
        clean = clean[lowered.index("select"):]
    return clean.strip().rstrip(";")


def _is_safe_select_sql(sql: str) -> bool:
    cleaned = (sql or "").strip().lower()
    if not (cleaned.startswith("select") or cleaned.startswith("with")):
        return False
    blocked = (
        " insert ", " update ", " delete ", " drop ", " alter ", " create ",
        " attach ", " detach ", " copy ", " pragma ", " call ", " export ",
    )
    padded = f" {cleaned} "
    return not any(token in padded for token in blocked)


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
        SOURCE_FILE_COLUMN: ["source file", "source", "file", "files", "csv", "csvs", "dataset", "datasets", "upload", "uploads"],
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


def _query_mentions_multi_file(query: str, frame: pd.DataFrame) -> bool:
    if frame is None or SOURCE_FILE_COLUMN not in frame.columns:
        return False
    if frame[SOURCE_FILE_COLUMN].nunique(dropna=True) < 2:
        return False
    return any(token in query for token in MULTI_FILE_QUERY_TOKENS)


def _metric_alias(column: str, prefix: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
    return f"{prefix}_{cleaned or 'value'}"


def _metric_column_for_query(query: str, frame: pd.DataFrame) -> Optional[str]:
    if frame is None or frame.empty:
        return None

    if any(phrase in query for phrase in ["record count", "row count", "rows count", "number of records", "how many records"]):
        return None

    numeric_cols = [
        column for column in frame.select_dtypes(include="number").columns
        if not any(token in str(column).lower() for token in ["id", "row", "postal", "zip", "pincode", "code"])
    ]
    if not numeric_cols:
        return None

    metric_tokens = [
        "sales", "revenue", "gmv", "amount", "profit", "margin", "price",
        "cost", "quantity", "qty", "orders", "count", "value", "score",
    ]
    for token in metric_tokens:
        if token not in query:
            continue
        for column in numeric_cols:
            if token in str(column).lower():
                return column

    return numeric_cols[0]


def _should_force_sql_intent(user_msg: str) -> bool:
    query = _normalize_text_token(user_msg)
    return any(token in query for token in DATA_QUERY_TOKENS)


def _manifest_table_name_for(*keywords: str) -> Optional[str]:
    """Find the registered DuckDB table for an uploaded source file by filename keywords."""
    lowered_keywords = [keyword.lower() for keyword in keywords if keyword]
    for item in (query_context or {}).get("source_files", []):
        haystack = f"{item.get('file_name', '')} {item.get('table_name', '')}".lower()
        if all(keyword in haystack for keyword in lowered_keywords):
            return item.get("table_name")
    return None


def _build_specialized_duckdb_sql(user_msg: str) -> Optional[str]:
    query = _normalize_text_token(user_msg)
    wants_branch = "branch" in query or "branches" in query
    wants_recovery = "recovery" in query or "recoveries" in query
    wants_performance = "performance" in query or "achievement" in query
    wants_top_bottom = "top" in query and "bottom" in query
    wants_bottom_percent = "bottom" in query and ("10" in query or "percent" in query or "pct" in query)
    wants_anomaly_count = "anomaly" in query and ("count" in query or "counts" in query or "high" in query)

    if wants_branch and wants_recovery and wants_performance and wants_bottom_percent and wants_anomaly_count:
        performance_table = _manifest_table_name_for("branch", "performance")
        recovery_table = _manifest_table_name_for("recovery") or _manifest_table_name_for("collections")
        if performance_table and recovery_table:
            performance_source = _duckdb_identifier(performance_table)
            recovery_source = _duckdb_identifier(recovery_table)
        else:
            performance_source = recovery_source = '"sales"'

        return f"""
WITH performance_by_branch AS (
    SELECT
        "branch",
        AVG(CAST("achievement_pct" AS DOUBLE)) AS "avg_achievement_pct",
        AVG(CAST("net_deposit_cr" AS DOUBLE)) AS "avg_net_deposit_cr",
        AVG(CAST("npa_amount_cr" AS DOUBLE)) AS "avg_npa_amount_cr"
    FROM {performance_source}
    WHERE "branch" IS NOT NULL
      AND "achievement_pct" IS NOT NULL
    GROUP BY "branch"
),
performance_cutoff AS (
    SELECT QUANTILE_CONT("avg_achievement_pct", 0.10) AS "bottom_10_cutoff"
    FROM performance_by_branch
),
recovery_rows AS (
    SELECT
        "branch",
        CAST("recovery_rate_pct" AS DOUBLE) AS "recovery_rate_pct",
        CAST("amount_recovered" AS DOUBLE) AS "amount_recovered",
        CAST("dpd" AS DOUBLE) AS "dpd",
        CAST("collateral_value" AS DOUBLE) AS "collateral_value",
        CAST("credit_score" AS DOUBLE) AS "credit_score",
        CAST("contact_attempts" AS DOUBLE) AS "contact_attempts"
    FROM {recovery_source}
    WHERE "branch" IS NOT NULL
),
recovery_stats AS (
    SELECT
        AVG("recovery_rate_pct") AS "avg_recovery_rate_pct_all",
        STDDEV_SAMP("recovery_rate_pct") AS "std_recovery_rate_pct",
        AVG("amount_recovered") AS "avg_amount_recovered_all",
        STDDEV_SAMP("amount_recovered") AS "std_amount_recovered",
        AVG("dpd") AS "avg_dpd_all",
        STDDEV_SAMP("dpd") AS "std_dpd",
        AVG("collateral_value") AS "avg_collateral_value_all",
        STDDEV_SAMP("collateral_value") AS "std_collateral_value",
        AVG("credit_score") AS "avg_credit_score_all",
        STDDEV_SAMP("credit_score") AS "std_credit_score",
        AVG("contact_attempts") AS "avg_contact_attempts_all",
        STDDEV_SAMP("contact_attempts") AS "std_contact_attempts"
    FROM recovery_rows
),
recovery_scored_rows AS (
    SELECT
        rr.*,
        (
            CASE WHEN rs."std_recovery_rate_pct" > 0 AND ABS(rr."recovery_rate_pct" - rs."avg_recovery_rate_pct_all") / rs."std_recovery_rate_pct" >= 2 THEN 1 ELSE 0 END +
            CASE WHEN rs."std_amount_recovered" > 0 AND ABS(rr."amount_recovered" - rs."avg_amount_recovered_all") / rs."std_amount_recovered" >= 2 THEN 1 ELSE 0 END +
            CASE WHEN rs."std_dpd" > 0 AND ABS(rr."dpd" - rs."avg_dpd_all") / rs."std_dpd" >= 2 THEN 1 ELSE 0 END +
            CASE WHEN rs."std_collateral_value" > 0 AND ABS(rr."collateral_value" - rs."avg_collateral_value_all") / rs."std_collateral_value" >= 2 THEN 1 ELSE 0 END +
            CASE WHEN rs."std_credit_score" > 0 AND ABS(rr."credit_score" - rs."avg_credit_score_all") / rs."std_credit_score" >= 2 THEN 1 ELSE 0 END +
            CASE WHEN rs."std_contact_attempts" > 0 AND ABS(rr."contact_attempts" - rs."avg_contact_attempts_all") / rs."std_contact_attempts" >= 2 THEN 1 ELSE 0 END
        ) AS "row_anomaly_count"
    FROM recovery_rows rr
    CROSS JOIN recovery_stats rs
),
recovery_anomalies_by_branch AS (
    SELECT
        "branch",
        COUNT(*) AS "collection_case_count",
        SUM("row_anomaly_count") AS "anomaly_count",
        SUM(CASE WHEN "row_anomaly_count" > 0 THEN 1 ELSE 0 END) AS "anomalous_case_count",
        AVG("recovery_rate_pct") AS "avg_recovery_rate_pct",
        AVG("amount_recovered") AS "avg_amount_recovered",
        AVG("dpd") AS "avg_dpd"
    FROM recovery_scored_rows
    GROUP BY "branch"
),
anomaly_cutoff AS (
    SELECT QUANTILE_CONT("anomaly_count", 0.75) AS "high_anomaly_cutoff"
    FROM recovery_anomalies_by_branch
),
joined AS (
    SELECT
        p."branch",
        p."avg_achievement_pct",
        p."avg_net_deposit_cr",
        p."avg_npa_amount_cr",
        r."collection_case_count",
        r."anomaly_count",
        r."anomalous_case_count",
        r."avg_recovery_rate_pct",
        r."avg_amount_recovered",
        r."avg_dpd",
        pc."bottom_10_cutoff",
        ac."high_anomaly_cutoff",
        p."avg_achievement_pct" <= pc."bottom_10_cutoff" AS "bottom_10_performance",
        r."anomaly_count" >= ac."high_anomaly_cutoff" AS "high_anomaly_count"
    FROM performance_by_branch p
    JOIN recovery_anomalies_by_branch r ON p."branch" = r."branch"
    CROSS JOIN performance_cutoff pc
    CROSS JOIN anomaly_cutoff ac
),
flagged AS (
    SELECT
        *,
        ("bottom_10_performance" AND "high_anomaly_count") AS "appears_in_both",
        CASE
            WHEN "bottom_10_performance" AND "high_anomaly_count" THEN 'Exact overlap: bottom performance and high recovery anomalies'
            WHEN "bottom_10_performance" THEN 'Bottom performance only'
            WHEN "high_anomaly_count" THEN 'High recovery anomaly count only'
            ELSE 'Near miss / context'
        END AS "overlap_status"
    FROM joined
)
SELECT
    "overlap_status",
    "appears_in_both",
    "branch",
    "avg_achievement_pct",
    "bottom_10_cutoff",
    "anomaly_count",
    "high_anomaly_cutoff",
    "anomalous_case_count",
    "collection_case_count",
    "avg_recovery_rate_pct",
    "avg_amount_recovered",
    "avg_dpd",
    "avg_net_deposit_cr",
    "avg_npa_amount_cr"
FROM flagged
WHERE "appears_in_both"
   OR "bottom_10_performance"
   OR "high_anomaly_count"
ORDER BY "appears_in_both" DESC, "bottom_10_performance" DESC, "high_anomaly_count" DESC, "anomaly_count" DESC, "avg_achievement_pct" ASC
""".strip()

    if wants_branch and wants_recovery and wants_performance and wants_top_bottom:
        return """
WITH branch_metrics AS (
    SELECT
        "branch",
        AVG(CAST("achievement_pct" AS DOUBLE)) AS "avg_achievement_pct",
        AVG(CAST("recovery_rate_pct" AS DOUBLE)) AS "avg_recovery_rate_pct",
        AVG(CAST("net_deposit_cr" AS DOUBLE)) AS "avg_net_deposit_cr",
        AVG(CAST("amount_recovered" AS DOUBLE)) AS "avg_amount_recovered",
        AVG(CAST("npa_amount_cr" AS DOUBLE)) AS "avg_npa_amount_cr",
        AVG(CAST("achievement_pct" AS DOUBLE)) + AVG(CAST("recovery_rate_pct" AS DOUBLE)) AS "composite_score"
    FROM "sales"
    WHERE "branch" IS NOT NULL
    GROUP BY "branch"
    HAVING "avg_achievement_pct" IS NOT NULL
       AND "avg_recovery_rate_pct" IS NOT NULL
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (ORDER BY "composite_score" DESC) AS "top_rank",
        ROW_NUMBER() OVER (ORDER BY "composite_score" ASC) AS "bottom_rank"
    FROM branch_metrics
),
final_rows AS (
    SELECT
        0 AS "sort_group",
        'Top 5' AS "branch_group",
        "top_rank" AS "display_rank",
        "branch",
        "avg_achievement_pct",
        "avg_recovery_rate_pct",
        "avg_net_deposit_cr",
        "avg_amount_recovered",
        "avg_npa_amount_cr",
        "composite_score"
    FROM ranked
    WHERE "top_rank" <= 5
    UNION ALL
    SELECT
        1 AS "sort_group",
        'Bottom 5' AS "branch_group",
        "bottom_rank" AS "display_rank",
        "branch",
        "avg_achievement_pct",
        "avg_recovery_rate_pct",
        "avg_net_deposit_cr",
        "avg_amount_recovered",
        "avg_npa_amount_cr",
        "composite_score"
    FROM ranked
    WHERE "bottom_rank" <= 5
)
SELECT
    "branch_group",
    "display_rank",
    "branch",
    "avg_achievement_pct",
    "avg_recovery_rate_pct",
    "avg_net_deposit_cr",
    "avg_amount_recovered",
    "avg_npa_amount_cr",
    "composite_score"
FROM final_rows
ORDER BY "sort_group", "display_rank"
""".strip()

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
    wants_source_comparison = _query_mentions_multi_file(query, frame)

    target_dimension = SOURCE_FILE_COLUMN if wants_source_comparison else _infer_target_dimension(query, list(frame.columns))
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
    metric_column = _metric_column_for_query(query, frame)
    wants_average = any(token in query for token in ["avg", "average", "mean"])
    if metric_column:
        metric_func = "AVG" if wants_average else "SUM"
        metric_expr = f'{metric_func}(CAST("{metric_column}" AS REAL))'
        metric_alias = _metric_alias(metric_column, "avg" if wants_average else "total")

    if target_dimension == "name" and "product" not in query and "item" not in query and "name" not in query:
        return None

    sql_parts = ["FROM sales"]
    if filter_clauses:
        sql_parts.append("WHERE " + " AND ".join(filter_clauses))

    if target_dimension:
        select_parts = [f'"{target_dimension}"', f'{metric_expr} AS "{metric_alias}"']
        if wants_analysis and metric_column:
            select_parts.append(f'AVG(CAST("{metric_column}" AS REAL)) AS "{_metric_alias(metric_column, "avg")}"')
        sql_parts.insert(0, "SELECT " + ", ".join(select_parts))
        sql_parts.append(f'GROUP BY "{target_dimension}"')
        sql_parts.append(f'ORDER BY "{metric_alias}" {sort_direction}')
        if limit or wants_analysis:
            sql_parts.append(f"LIMIT {limit or 10}")
        return "\n".join(sql_parts)

    aggregate_parts = ['COUNT(*) AS "record_count"']
    if metric_column:
        aggregate_parts.extend([
            f'SUM(CAST("{metric_column}" AS REAL)) AS "{_metric_alias(metric_column, "total")}"',
            f'AVG(CAST("{metric_column}" AS REAL)) AS "{_metric_alias(metric_column, "avg")}"',
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
    if SOURCE_FILE_COLUMN in frame.columns:
        source_counts = frame[SOURCE_FILE_COLUMN].astype(str).value_counts().head(20)
        context["multi_file_context"] = {
            "source_column": SOURCE_FILE_COLUMN,
            "source_files": source_counts.index.tolist(),
            "rows_by_source_file": {str(key): int(value) for key, value in source_counts.items()},
            "usage": "Use source_file to compare uploaded CSV/XLSX inputs.",
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
            "### CSV Findings & Trends",
            f"- Ranked the top {len(top_rows)} {heading_label.lower()} entries using **{metric_label.lower()}**.",
            f"- The current leader is **{leader[label_col]}** with **{leader[value_col]:,.0f}**.",
            "",
            "### CSV Peaks, Lows & Outliers",
            *[f"- {line}" for line in ranked_lines],
            "",
            "### CSV Business Interpretation",
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
# Load datasets into DuckDB (cached)
# ---------------------------
@st.cache_data
def load_data():
    manifest_data = _load_combined_dataset_from_manifest()
    if manifest_data is not None:
        return manifest_data
    if DATA_PATH.exists():
        return pd.read_csv(DATA_PATH, encoding="latin1")
    raise FileNotFoundError(
        "No dataset found. Upload a CSV/Excel file first so dataset_input/manifest.json can be built."
    )

@st.cache_resource
def init_db(data: pd.DataFrame):
    connection = duckdb.connect(database=":memory:")

    table_context: Dict[str, Any] = {
        "dialect": "DuckDB SQL",
        "combined_table": "sales",
        "tables": {},
        "source_files": [],
    }

    connection.register("_combined_sales_df", data)
    connection.execute("CREATE OR REPLACE TABLE sales AS SELECT * FROM _combined_sales_df")
    connection.unregister("_combined_sales_df")
    table_context["tables"]["sales"] = {
        "description": "Combined bridge table across uploaded datasets. Use this for union-style cross-file comparisons.",
        **_sample_table_context(data),
    }

    manifest = _read_dataset_manifest()
    for index, item in enumerate(manifest.get("files", [])):
        file_name = item.get("name") or f"dataset_{index + 1}"
        file_path = item.get("path")
        if not file_path or not Path(file_path).exists():
            continue

        table_name = _safe_table_name(file_name, index)
        try:
            frame = _read_table_file(file_path)
        except Exception:
            continue

        connection.register(f"_dataset_df_{index}", frame)
        connection.execute(
            f"CREATE OR REPLACE TABLE {_duckdb_identifier(table_name)} AS SELECT * FROM {_duckdb_identifier(f'_dataset_df_{index}')}"
        )
        connection.unregister(f"_dataset_df_{index}")
        table_context["tables"][table_name] = {
            "description": f"Original uploaded dataset file: {file_name}",
            "file_name": file_name,
            **_sample_table_context(frame),
        }
        table_context["source_files"].append({
            "file_name": file_name,
            "table_name": table_name,
            "rows": int(len(frame)),
            "columns": int(len(frame.columns)),
        })

    return connection, table_context

data = None
conn = None
query_context: Dict[str, Any] = {}

def initialize_chatbot():
    global data, conn, query_context
    data = load_data()
    conn, query_context = init_db(data)

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
    if _should_force_sql_intent(user_msg):
        intent = "SQL"
    elif intent not in ("SQL", "INSIGHT"):
        intent = "INSIGHT"
    return {**state, "intent": intent}

# ---------------------------
# Node 2 â€” SQL Generator
# ---------------------------
def _legacy_sqlite_sql_node_disabled(state: State):
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
    specialized_sql = _build_specialized_duckdb_sql(user_msg)
    if specialized_sql:
        return {**state, "sql_query": specialized_sql}

    dataset_context = json.dumps(query_context or {}, ensure_ascii=False, indent=2)

    prompt = f"""
You are an expert DuckDB SQL query generator.
Return ONLY a single valid DuckDB SELECT query (no explanations).

IMPORTANT RULES:
- Use only SELECT or WITH queries.
- Always quote table names and column names with double quotes.
- Always quote string filter values.
- For top/bottom grouped requests, aggregate by the requested dimension.
- If the user asks for top/bottom items without a metric, default to COUNT(*).
- Use the DuckDB schema map below to choose the right table.
- Prefer the separate per-file tables for file-specific questions or joins between uploaded CSV files.
- Use "sales" only as a combined bridge table for union-style comparisons across uploaded files.
- If the user asks about multiple CSVs, uploaded files, sources, or comparing inputs and no join key is needed, group by "source_file" from "sales".
- "source_file" means the uploaded dataset filename, not a business source/channel unless the user clearly asks for a business source column.
- If joining per-file tables, infer likely join keys from matching column names. Use explicit JOIN conditions.
- Do not treat filler words like "the", "what", or "top" as literal filter values unless they clearly appear as business values in the sample/profile.
- Use DuckDB syntax. For dates, prefer TRY_STRPTIME or CAST only when needed.

DuckDB schema map:
{dataset_context}

User query:
{user_msg}
"""
    reply = _gemini_invoke(prompt)

    sql = _strip_sql_fences(reply)
    if _is_safe_select_sql(sql) and not _looks_suspicious_sql(sql):
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
        sql = _strip_sql_fences(sql)
        if not _is_safe_select_sql(sql):
            raise ValueError("Only SELECT/WITH DuckDB queries are allowed.")
        df = conn.execute(sql).fetchdf()
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
    user_msg = state["messages"][-1]["content"]
    query = _normalize_text_token(user_msg)

    fig = None
    try:
        numeric_cols = list(df.select_dtypes(include="number").columns)
        if (
            "branch" in df.columns
            and {"avg_achievement_pct", "anomaly_count"}.issubset(df.columns)
            and any(token in query for token in ["anomaly", "anomalies", "bottom", "performance", "recovery"])
        ):
            plot_df = df.copy()
            if "overlap_status" not in plot_df.columns:
                plot_df["overlap_status"] = "Branch risk profile"
            size_col = "collection_case_count" if "collection_case_count" in plot_df.columns else None
            fig = px.scatter(
                plot_df,
                x="avg_achievement_pct",
                y="anomaly_count",
                color="overlap_status",
                size=size_col,
                text="branch",
                title="Branch Performance vs Recovery Anomaly Count",
                labels={
                    "avg_achievement_pct": "Avg achievement %",
                    "anomaly_count": "Recovery anomaly count",
                    "overlap_status": "Risk segment",
                    "collection_case_count": "Collection cases",
                },
            )
            fig.update_traces(textposition="top center")
        elif (
            "branch" in df.columns
            and {"avg_achievement_pct", "avg_recovery_rate_pct"}.issubset(df.columns)
            and any(token in query for token in ["both", "performance", "recovery", "single chart", "compare"])
        ):
            id_columns = ["branch"]
            if "branch_group" in df.columns:
                id_columns.append("branch_group")
            metric_columns = ["avg_achievement_pct", "avg_recovery_rate_pct"]
            long_df = df[id_columns + metric_columns].melt(
                id_vars=id_columns,
                value_vars=metric_columns,
                var_name="metric",
                value_name="value",
            )
            long_df["metric"] = long_df["metric"].replace({
                "avg_achievement_pct": "Avg achievement %",
                "avg_recovery_rate_pct": "Avg recovery rate %",
            })
            long_df["branch_label"] = long_df["branch"]
            if "branch_group" in long_df.columns:
                long_df["branch_label"] = long_df["branch_group"].astype(str) + " - " + long_df["branch"].astype(str)
            fig = px.bar(
                long_df,
                x="branch_label",
                y="value",
                color="metric",
                barmode="group",
                title="Top and Bottom Branches: Performance vs Recovery",
                labels={"branch_label": "Branch", "value": "Percent", "metric": "Metric"},
            )
            fig.update_layout(xaxis={'categoryorder': 'array', 'categoryarray': long_df["branch_label"].drop_duplicates().tolist()})
        elif chart_type == "pie":
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

    query = _normalize_text_token(user_msg)
    wants_risk_or_recommendation = any(token in query for token in ["risk", "risks", "recommendation", "recommendations"])
    ranked_insight = None if wants_risk_or_recommendation or "branch_group" in df.columns else _build_ranked_insight(user_msg, df)
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

You MUST structure your response using these CSV/dataframe-chat headings:
1.  **### CSV Findings & Trends** (What are the most important conclusions from the queried CSV rows?)
2.  **### CSV Peaks, Lows & Outliers** (Identify the highest values, lowest values, or notable row-level anomalies.)
3.  **### CSV Data Risks** (Explain data quality, operational, or business risks visible in the result.)
4.  **### CSV Action Recommendations** (Give practical actions tied directly to the CSV metrics.)

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
    <span class="dataset-metric-chip">DuckDB multi-table answers</span>
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
        "Compare uploaded files by record count",
        "Top 10 products by sales",
        "Monthly sales trend",
        "Compare profit by region",
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
        user_input = st.text_area(
            "Type your question...",
            key="user_input_box",
            label_visibility="collapsed",
            height=110,
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

