from typing import Dict, Any
import pandas as pd
import json
import numpy as np
import os
import re

from utils import extract_json_from_response
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate


class IntelligentAnalysisPlanner:
    """Robust planner with Groq + Gemini fallback"""

    SUPPORTED_ANALYSES = {
        "time_series_decomposition",
        "cohort_analysis",
        "customer_segmentation",
        "correlation_network_analysis",
        "anomaly_detection",
        "distribution_comparison",
    }

    SUPPORTED_VISUALIZATIONS = {
        "correlation_heatmap",
        "time_series_line",
        "line_chart",
        "bar_chart",
        "histogram",
        "scatter_plot",
        "pie_chart",
        "box_plot",
        "violin_plot",
        "bubble_chart",
    }

    def __init__(self, api_key: str):
        self.api_key = api_key  # Groq API Key
        from google import genai
        try:
            gemini_api_key = os.getenv("GEMINI_API_KEY")
            self.client = genai.Client(api_key=gemini_api_key)
        except:
            self.client = None

    # =========================================================
    # MAIN ENTRY
    # =========================================================
    def create_analysis_plan(self, df: pd.DataFrame, dataset_info: Dict[str, Any]) -> Dict[str, Any]:

        profile = self._profile_dataset(df)

        # 1️⃣ USE GEMINI
        try:
            print("⚡ Using Gemini planner...")

            response = self.client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[self._build_prompt(profile)]
            )

            plan = extract_json_from_response(response.text)

            if plan:
                return self._validate_analysis_plan(plan, df)

        except Exception as e:
            print(f"⚠️ Gemini planner failed: {e}")

        # 2️⃣ FINAL SAFE FALLBACK
        print("🛑 Using static fallback plan")

        return self._static_fallback(profile, df)

    # =========================================================
    # PROMPT
    # =========================================================
    def _build_prompt(self, profile):

        return f"""
You are an expert data analyst.

Dataset profile:
{json.dumps(profile, indent=2)}

If a column named "source_file" exists, it identifies which uploaded CSV/XLSX file each row came from.
For multi-file datasets, include cross-file comparisons using "source_file" when useful.

Return JSON ONLY:

{{
  "specialized_analyses": [
    {{
      "function": "correlation_network_analysis",
      "columns": ["Sales", "Profit"]
    }},
    {{
      "function": "anomaly_detection",
      "columns": ["Sales"]
    }}
  ],
  "visualizations": [
    {{
      "chart_type": "correlation_heatmap",
      "columns": ["Sales", "Profit"]
    }},
    {{
      "chart_type": "bar_chart",
      "columns": ["Category", "Sales"]
    }},
    {{
      "chart_type": "time_series_line",
      "columns": ["Date", "Sales"]
    }}
    // ... GENERATE 5-8 UNIQUE, COMPLEX CHARTS.
    // CRITICAL INSTRUCTION: DO NOT generate treemap, pie_chart, donut_chart, basic scatter_plot, or basic box_plot. These are handled elsewhere or are not required. Focus ONLY on strong multi-variable comparisons, complex time-series, safe numeric bubble_chart options, and advanced distributions.
  ]
}}
"""

    # =========================================================
    # STATIC FALLBACK (ALWAYS WORKS)
    # =========================================================
    def _static_fallback(self, profile, df):

        numeric = profile.get("numeric_columns", {}).get("columns", [])
        categorical = profile.get("categorical_columns", {}).get("columns", [])
        date_cols = profile.get("date_columns", {}).get("columns", [])

        plan = {
            "specialized_analyses": [],
            "visualizations": []
        }

        if len(numeric) >= 2:
            plan["specialized_analyses"].append({
                "function": "correlation_network_analysis",
                "columns": numeric[:2]
            })

        if numeric:
            plan["specialized_analyses"].append({
                "function": "anomaly_detection",
                "columns": numeric[:1]
            })

        if "source_file" in categorical and numeric:
            plan["specialized_analyses"].append({
                "function": "distribution_comparison",
                "columns": [numeric[0], "source_file"]
            })
            plan["visualizations"].append({
                "chart_type": "bar_chart",
                "columns": ["source_file", numeric[0]],
                "title": f"Cross-File Comparison by Total {numeric[0].title()}"
            })

        if len(numeric) >= 3 and categorical:
            plan["visualizations"].append({
                "chart_type": "bubble_chart",
                "columns": [numeric[0], numeric[1], numeric[2]],
                "title": f"Multi-Variable: {numeric[0]} vs {numeric[1]} (Size: {numeric[2]})"
            })

        if categorical and numeric:
            plan["visualizations"].append({
                "chart_type": "bar_chart",
                "columns": [categorical[0], numeric[0]],
                "title": f"{categorical[0].title()} vs {numeric[0].title()}"
            })

        if categorical and len(numeric) >= 2:
            plan["visualizations"].append({
                "chart_type": "violin_plot",
                "columns": [categorical[0], numeric[0]],
                "title": f"Density of {numeric[0]} across {categorical[0]}"
            })

        if len(numeric) >= 2:
            plan["visualizations"].append({
                "chart_type": "scatter_plot",
                "columns": [numeric[0], numeric[1]],
                "title": f"Numeric Relationship: {numeric[0]} vs {numeric[1]}"
            })

        if date_cols and len(numeric) >= 1 and self._has_sufficient_time_series(df, date_cols[0], numeric[0]):
            plan["specialized_analyses"].append({
                "function": "time_series_decomposition",
                "columns": [date_cols[0], numeric[0]]
            })

        return plan

    def _normalize_columns_key(self, columns) -> tuple:
        return tuple(str(col).strip().lower() for col in (columns or []))

    def _is_identifier_like(self, series: pd.Series, column_name: str) -> bool:
        lowered = str(column_name).lower()
        if any(token in lowered for token in ["id", "postal", "zip", "pincode", "code", "index"]):
            return True

        cleaned = series.dropna()
        if cleaned.empty:
            return False
        if not pd.api.types.is_numeric_dtype(cleaned):
            cleaned = pd.to_numeric(cleaned, errors="coerce").dropna()
        if cleaned.empty:
            return False

        unique_ratio = cleaned.nunique() / max(len(cleaned), 1)
        is_integer_like = np.allclose(cleaned, np.round(cleaned))
        return unique_ratio >= 0.9 and is_integer_like and len(cleaned) >= 25

    def _coerce_numeric_series(self, series: pd.Series) -> pd.Series:
        if pd.api.types.is_numeric_dtype(series):
            return pd.to_numeric(series, errors="coerce")
        cleaned = (
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.extract(r"([-+]?\d*\.?\d+)", expand=False)
        )
        return pd.to_numeric(cleaned, errors="coerce")

    def _numeric_profile(self, series: pd.Series) -> Dict[str, float]:
        non_null = series.dropna()
        if non_null.empty:
            return {"coercion_ratio": 0.0, "dirty_text_ratio": 0.0}
        coerced = self._coerce_numeric_series(non_null)
        dirty_mask = non_null.astype(str).str.contains(r"[A-Za-z]", regex=True, na=False)
        return {
            "coercion_ratio": float(coerced.notna().mean()),
            "dirty_text_ratio": float(dirty_mask.mean()),
        }

    def _is_safe_numeric_column(self, df: pd.DataFrame, column_name: str) -> bool:
        if column_name not in df.columns:
            return False
        series = df[column_name]
        if self._is_identifier_like(series, column_name):
            return False
        if pd.api.types.is_numeric_dtype(series):
            return True
        profile = self._numeric_profile(series)
        return profile["coercion_ratio"] >= 0.95 and profile["dirty_text_ratio"] <= 0.05

    def _parse_date_series(self, series: pd.Series) -> pd.Series:
        return pd.to_datetime(series, errors="coerce")

    def _is_date_like_column(self, df: pd.DataFrame, column_name: str) -> bool:
        if column_name not in df.columns:
            return False
        series = df[column_name]
        if pd.api.types.is_datetime64_any_dtype(series):
            return True
        parsed = self._parse_date_series(series)
        threshold = max(10, int(len(df) * 0.2))
        return parsed.notna().sum() >= threshold

    def _has_sufficient_time_series(self, df: pd.DataFrame, date_col: str, value_col: str) -> bool:
        if date_col not in df.columns or value_col not in df.columns or not self._is_safe_numeric_column(df, value_col):
            return False

        ts_df = df[[date_col, value_col]].copy()
        ts_df[date_col] = self._parse_date_series(ts_df[date_col])
        ts_df[value_col] = self._coerce_numeric_series(ts_df[value_col])
        ts_df = ts_df.dropna(subset=[date_col, value_col]).sort_values(date_col)
        if ts_df.empty:
            return False

        freq = 'D' if len(ts_df) > 365 else 'M'
        min_required = 60 if freq == 'D' else 24
        ts_resampled = ts_df.set_index(date_col)[value_col].resample(freq).mean().dropna()
        return len(ts_resampled) >= min_required

    # =========================================================
    # PROFILE (same as yours, kept intact)
    # =========================================================
    def _profile_dataset(self, df: pd.DataFrame) -> Dict[str, Any]:

        profile = {
            'shape': df.shape,
            'columns': df.columns.tolist(),
            'dtypes': {str(k): str(v) for k, v in df.dtypes.to_dict().items()},
            'missing_percentage': {
                col: float((df[col].isnull().sum() / len(df)) * 100)
                for col in df.columns
            }
        }

        numeric_cols = [
            col for col in df.columns
            if self._is_safe_numeric_column(df, col)
        ]
        categorical_cols = [
            col for col in df.select_dtypes(include=['object', 'category']).columns.tolist()
            if 2 <= df[col].nunique(dropna=True) <= 15
            and not any(token in col.lower() for token in ["id", "name", "address", "code"])
        ]
        date_cols = df.select_dtypes(include=['datetime64', 'datetime']).columns.tolist()
        if not date_cols:
            for col in df.columns:
                if any(token in col.lower() for token in ["date", "time", "timestamp", "created", "updated"]) and self._is_date_like_column(df, col):
                        date_cols.append(col)

        if numeric_cols:
            profile['numeric_columns'] = {
                'columns': numeric_cols
            }

        if categorical_cols:
            profile['categorical_columns'] = {
                'columns': categorical_cols
            }

        numeric_like_details = []
        for col in categorical_cols:
            column_profile = self._numeric_profile(df[col])
            if column_profile["coercion_ratio"] >= 0.5:
                numeric_like_details.append({
                    "column": col,
                    **column_profile,
                })
        if numeric_like_details:
            profile["numeric_like_columns"] = numeric_like_details

        if date_cols:
            profile['date_columns'] = {
                'columns': date_cols
            }

        if "source_file" in df.columns:
            source_counts = df["source_file"].astype(str).value_counts().head(12)
            profile["multi_file_context"] = {
                "source_column": "source_file",
                "source_files": source_counts.index.tolist(),
                "rows_by_source_file": {str(key): int(value) for key, value in source_counts.items()},
            }

        return profile

    # =========================================================
    # VALIDATION (same as yours)
    # =========================================================
    def _validate_analysis_plan(self, plan: Dict[str, Any], df: pd.DataFrame) -> Dict[str, Any]:

        valid = {
            'specialized_analyses': [],
            'visualizations': []
        }

        profile = self._profile_dataset(df)
        cols = set(df.columns)
        numeric_cols = set(profile.get("numeric_columns", {}).get("columns", []))
        categorical_cols = set(profile.get("categorical_columns", {}).get("columns", []))
        date_like_cols = set(profile.get("date_columns", {}).get("columns", []))
        seen_analysis_keys = set()
        seen_visual_keys = set()

        for a in plan.get("specialized_analyses", []):
            function_name = a.get("function")
            if function_name not in self.SUPPORTED_ANALYSES:
                continue
            columns = a.get("columns", [])
            if not set(columns).issubset(cols):
                continue
            analysis_key = (function_name, self._normalize_columns_key(columns))
            if analysis_key in seen_analysis_keys:
                continue
            if function_name == "anomaly_detection" and (not columns or not set(columns).issubset(numeric_cols)):
                continue
            if function_name == "correlation_network_analysis":
                usable_numeric = [column for column in columns if column in numeric_cols]
                if len(usable_numeric) < 2:
                    continue
            if function_name == "time_series_decomposition":
                if len(columns) < 2 or columns[0] not in date_like_cols or columns[1] not in numeric_cols:
                    continue
                if not self._has_sufficient_time_series(df, columns[0], columns[1]):
                    continue
            if function_name == "distribution_comparison":
                if len(columns) < 2 or columns[0] not in numeric_cols or columns[1] not in categorical_cols:
                    continue
            valid["specialized_analyses"].append(a)
            seen_analysis_keys.add(analysis_key)

        for v in plan.get("visualizations", []):
            chart_type = v.get("chart_type")
            if chart_type not in self.SUPPORTED_VISUALIZATIONS:
                continue
            columns = v.get("columns", [])
            if not set(columns).issubset(cols):
                continue
            visual_key = (chart_type, self._normalize_columns_key(columns))
            if visual_key in seen_visual_keys:
                continue

            if chart_type in {"histogram"} and not set(columns[:1]).issubset(numeric_cols):
                continue
            if chart_type in {"time_series_line", "line_chart"}:
                if len(columns) < 2 or columns[0] not in date_like_cols or columns[1] not in numeric_cols:
                    continue
                if not self._has_sufficient_time_series(df, columns[0], columns[1]):
                    continue
            if chart_type in {"scatter_plot"}:
                if len(columns) < 2 or not set(columns[:2]).issubset(numeric_cols):
                    continue
            if chart_type in {"bubble_chart"}:
                if len(columns) < 3 or not set(columns[:3]).issubset(numeric_cols):
                    continue
            if chart_type in {"bar_chart", "violin_plot"}:
                if len(columns) < 2 or columns[0] not in categorical_cols or columns[1] not in numeric_cols:
                    continue
            if chart_type in {"pie_chart"} and columns and columns[0] not in categorical_cols:
                continue

            valid["visualizations"].append(v)
            seen_visual_keys.add(visual_key)

        fallback = self._static_fallback(profile, df)

        existing_analysis_keys = {
            (item.get("function"), tuple(item.get("columns", [])))
            for item in valid["specialized_analyses"]
        }
        for item in fallback.get("specialized_analyses", []):
            key = (item.get("function"), tuple(item.get("columns", [])))
            if key not in existing_analysis_keys:
                valid["specialized_analyses"].append(item)
                existing_analysis_keys.add(key)

        existing_visual_keys = {
            (item.get("chart_type"), tuple(item.get("columns", [])))
            for item in valid["visualizations"]
        }
        for item in fallback.get("visualizations", []):
            key = (item.get("chart_type"), tuple(item.get("columns", [])))
            if key not in existing_visual_keys:
                valid["visualizations"].append(item)
                existing_visual_keys.add(key)

        return valid
