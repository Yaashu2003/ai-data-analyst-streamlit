import gemini_patch
import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(override=True)

from pbixray import PBIXRay
from google import genai
import pandas as pd
from unzipping import extract_all_reports
from chart_pipeline import extract_visuals, build_chart_data, build_visual_descriptor
from gemini_patch import generate_content_with_model_fallback
import tableau_pipeline as _twb


# ======================================================
# GEMINI CLIENT
# ======================================================

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None
LAST_EXTRACTION_SUMMARY = {}
PBIX_ANALYSIS_SECTION_KEYS = (
    "overall_performance_summary",
    "key_insights_and_drivers",
    "risks_and_issues",
    "data_driven_recommendations",
    "insights",
)
PBIX_TEXT_SECTION_KEYS = ("text", "answer", "explanation")


# ======================================================
# INTENT DETECTION
# ======================================================

def detect_chart_intent(query):

    q = query.lower()

    chart_words = [
        "chart","draw","plot","visualize","graph",
        "compare","vs","distribution","trend"
    ]

    for w in chart_words:
        if w in q:
            return True

    return False


def _normalize_query_text(value):

    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _tokenize_query(value):

    return [token for token in _normalize_query_text(value).split() if token]


def _extract_report_mentions(query, charts):

    normalized_query = _normalize_query_text(query)
    mentioned_reports = set()

    for chart in charts:
        report_name = str(chart.get("report", "")).strip()
        if not report_name:
            continue
        normalized_report = _normalize_query_text(report_name)
        report_tokens = [token for token in normalized_report.split() if len(token) > 2]

        if normalized_report and normalized_report in normalized_query:
            mentioned_reports.add(report_name)
            continue

        if report_tokens and all(token in normalized_query for token in report_tokens):
            mentioned_reports.add(report_name)

    return sorted(mentioned_reports)


def _is_multi_report_compare(query, charts):

    report_names = sorted({str(chart.get("report", "")).strip() for chart in charts if chart.get("report")})
    if len(report_names) < 2:
        return False

    normalized_query = _normalize_query_text(query)
    query_tokens = normalized_query.split()
    if any(phrase in normalized_query for phrase in ["all three", "all reports", "all dashboards", "across dashboards"]):
        return True
    if any(token in query_tokens for token in ["compare", "vs", "versus", "across"]):
        return True
    if len(_extract_report_mentions(query, charts)) >= 2:
        return True
    return False


def _chart_relevance_score(query, chart):

    normalized_query = _normalize_query_text(query)
    query_tokens = _tokenize_query(query)

    title = _normalize_query_text(chart.get("title", ""))
    page = _normalize_query_text(chart.get("page", ""))
    report = _normalize_query_text(chart.get("report", ""))
    dimension = _normalize_query_text(chart.get("dimension", ""))
    measure = _normalize_query_text(chart.get("measure_used", ""))
    chart_type = _normalize_query_text(chart.get("chart_type", ""))

    score = 0
    if title and title in normalized_query:
        score += 28
    if dimension and dimension in normalized_query:
        score += 16
    if measure and measure in normalized_query:
        score += 14
    if report and report in normalized_query:
        score += 12
    if page and page in normalized_query:
        score += 8
    if chart.get("data_available", True):
        score += 4

    search_fields = [title, page, report, dimension, measure, chart_type]
    for token in query_tokens:
        if len(token) < 3:
            continue
        for field in search_fields:
            if token and field and token in field:
                score += 3
                break

    return score


# ======================================================
# PBIX TABLE EXTRACTION
# ======================================================

def extract_tables(pbix_path):

    tables = {}

    try:
        model = PBIXRay(pbix_path)
        power_query_map = {}
        try:
            pq_df = model.power_query
            if hasattr(pq_df, "iterrows"):
                for _, row in pq_df.iterrows():
                    table_name = str(row.get("TableName", "")).strip()
                    expression = str(row.get("Expression", "") or "")
                    if table_name and expression:
                        power_query_map[table_name] = expression
        except Exception as pq_error:
            print("Power Query metadata read error:", pq_error)

        for table in model.tables:

            try:
                tables[table] = model.get_table(table)
            except Exception as table_error:
                recovered_table = recover_table_from_power_query(
                    table_name=table,
                    expression=power_query_map.get(str(table), ""),
                )
                if recovered_table is not None:
                    tables[table] = recovered_table
                    print(f"Recovered '{table}' from Power Query source.")
                else:
                    print(f"PBIX table extract failed for '{table}': {table_error}")
                    continue

    except Exception as e:
        print("PBIX read error:", e)

    return tables


def recover_table_from_power_query(table_name, expression):

    if not expression:
        return None

    file_match = re.search(r'File\.Contents\("([^"]+)"\)', expression)
    if not file_match:
        return None

    source_path = file_match.group(1)
    if not os.path.exists(source_path):
        print(f"Power Query source file not found for '{table_name}': {source_path}")
        return None

    lower_expr = expression.lower()
    suffix = Path(source_path).suffix.lower()

    try:
        if "excel.workbook" in lower_expr or suffix in {".xlsx", ".xls"}:
            sheet_match = re.search(r'\[Item="([^"]+)",Kind="Sheet"\]', expression)
            sheet_name = sheet_match.group(1) if sheet_match else 0
            return pd.read_excel(source_path, sheet_name=sheet_name)

        if "csv.document" in lower_expr or suffix == ".csv":
            try:
                return pd.read_csv(source_path, encoding="utf-8")
            except Exception:
                return pd.read_csv(source_path, encoding="latin1")
    except Exception as recover_error:
        print(f"Power Query recovery failed for '{table_name}': {recover_error}")

    return None


# ======================================================
# REMOVE DUPLICATE CHARTS
# ======================================================

def remove_duplicate_charts(charts):

    # 1. Strictly remove identical charts (same values)
    unique = {}
    for c in charts:
        y_rep = str(c.get("y", [])) if "y" in c else str(c.get("series", []))
        key_dedup = (c.get("report"), c.get("page"), c.get("dimension"), c.get("measure_used"), y_rep)
        if key_dedup not in unique:
            unique[key_dedup] = c
            
    deduped_charts = list(unique.values())
    
    # 2. Combine charts with the same dimension into multi-series charts
    combined = {}
    for c in deduped_charts:
        dim = c.get("dimension")
        if not dim:
            key = (c.get("report"), c.get("page"), c.get("title"))
            combined[key] = c
            continue
            
        key = (c.get("report"), c.get("page"), dim)
        
        if key not in combined:
            combined[key] = c
        else:
            existing = combined[key]
            
            existing_measures = []
            if "series" in existing:
                existing_measures = [s.get("name") for s in existing["series"]]
            else:
                existing_measures = [existing.get("measure_used") or existing.get("title")]
                
            new_measure = c.get("measure_used") or c.get("title")
            
            if new_measure in existing_measures:
                continue
                
            # Convert single existing to multi-series
            if "series" not in existing:
                y_name = existing.get("measure_used") or existing.get("title") or "Series 1"
                y_data = existing.pop("y", [])
                existing["series"] = [
                    {"name": str(y_name), "y": y_data}
                ]
            
            # Combine
            if "series" in c:
                for s in c["series"]:
                    if s.get("name") not in existing_measures:
                        existing["series"].append(s)
            elif "y" in c:
                existing_x = existing.get("x", [])
                new_y_map = dict(zip(c.get("x", []), c.get("y", [])))
                aligned_y = [new_y_map.get(x_val, 0) for x_val in existing_x]
                
                existing["series"].append({
                    "name": str(new_measure),
                    "y": aligned_y
                })
                
            if new_measure and str(new_measure) not in existing.get("title", ""):
                existing["title"] = f"{existing.get('title')} + {new_measure}"
                
            if "pie" in existing.get("chart_type", "").lower() or "donut" in existing.get("chart_type", "").lower():
                existing["chart_type"] = "columnChart"

    return list(combined.values())


def dedupe_metadata_visuals(visuals):

    unique = {}

    for visual in visuals:
        key = (
            visual.get("report"),
            visual.get("page"),
            visual.get("title"),
            visual.get("chart_type"),
        )
        if key not in unique:
            unique[key] = visual

    return list(unique.values())


def merge_real_and_metadata_charts(real_charts, metadata_visuals):

    real_keys = {
        (
            chart.get("report"),
            chart.get("page"),
            chart.get("title"),
            chart.get("chart_type"),
        )
        for chart in real_charts
    }

    merged = list(real_charts)

    for visual in metadata_visuals:
        key = (
            visual.get("report"),
            visual.get("page"),
            visual.get("title"),
            visual.get("chart_type"),
        )
        if key not in real_keys:
            merged.append(visual)

    return merged


def get_last_extraction_summary():

    return dict(LAST_EXTRACTION_SUMMARY)


def _read_external_table_file(file_path):

    path = Path(file_path)
    suffix = path.suffix.lower()
    tables = {}

    try:
        if suffix == ".csv":
            try:
                dataframe = pd.read_csv(path, encoding="utf-8")
            except Exception:
                dataframe = pd.read_csv(path, encoding="latin1")
            tables[path.stem] = dataframe
        elif suffix in {".xlsx", ".xls"}:
            workbook = pd.ExcelFile(path)
            for sheet_name in workbook.sheet_names:
                dataframe = pd.read_excel(path, sheet_name=sheet_name)
                tables[sheet_name] = dataframe
            if len(workbook.sheet_names) == 1:
                only_sheet = workbook.sheet_names[0]
                tables[path.stem] = tables[only_sheet]
    except Exception as error:
        print(f"External table read error for {path}: {error}")

    return tables


def load_external_tables(external_data_files=None):

    external_tables = {}
    source_info = []

    for file_path in external_data_files or []:
        loaded_tables = _read_external_table_file(file_path)
        if not loaded_tables:
            continue
        source_info.append(str(file_path))
        for name, dataframe in loaded_tables.items():
            external_tables[name] = dataframe

    return external_tables, source_info


def _normalize_table_key(name):

    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


def alias_missing_tables(base_tables, visuals, external_tables=None):

    merged_tables = dict(base_tables)
    alias_map = {}
    external_tables = external_tables or {}

    for name, dataframe in external_tables.items():
        if name not in merged_tables:
            merged_tables[name] = dataframe

    missing_required_tables = sorted({
        str(field[0])
        for visual in visuals
        if visual.get("visual_category") in {"chart", "kpi"}
        for fields in visual.get("roles", {}).values()
        for field in fields
        if isinstance(field, tuple) and str(field[0]) not in merged_tables
    })

    if not missing_required_tables:
        return merged_tables, alias_map

    normalized_external = {}
    for name, dataframe in external_tables.items():
        normalized_external[_normalize_table_key(name)] = (name, dataframe)

    single_external_item = None
    if len(external_tables) == 1:
        single_external_item = next(iter(external_tables.items()))

    user_tables = {k: v for k, v in base_tables.items() if not k.startswith("DateTableTemplate") and not k.startswith("LocalDateTable")}
    single_internal_item = None
    if len(user_tables) == 1:
        single_internal_item = next(iter(user_tables.items()))

    for missing_name in missing_required_tables:
        normalized_name = _normalize_table_key(missing_name)
        matched = normalized_external.get(normalized_name)
        if matched:
            source_name, dataframe = matched
            merged_tables[missing_name] = dataframe
            alias_map[missing_name] = source_name
            continue

        if single_external_item:
            source_name, dataframe = single_external_item
            merged_tables[missing_name] = dataframe
            alias_map[missing_name] = source_name
            continue

        if single_internal_item:
            source_name, dataframe = single_internal_item
            merged_tables[missing_name] = dataframe
            alias_map[missing_name] = source_name
            continue

    return merged_tables, alias_map


def _pbix_user_tables(tables):

    return {
        name: dataframe
        for name, dataframe in (tables or {}).items()
        if isinstance(dataframe, pd.DataFrame)
        and not dataframe.empty
        and not str(name).startswith("DateTableTemplate")
        and not str(name).startswith("LocalDateTable")
    }


def _numeric_series(series):

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() < 2:
        cleaned = (
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace(r"[^0-9.\-]", "", regex=True)
        )
        numeric = pd.to_numeric(cleaned, errors="coerce")
    if numeric.notna().sum() < 2:
        return None
    return numeric


def _datetime_series(series):

    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    if parsed.notna().sum() < 3:
        return None
    return parsed


def _normalized_title(title):

    return _normalize_query_text(title).replace(" ", "")


def _build_chart_object(report_name, source_table, title, chart_type, dimension, measure_used, x, y=None, series=None):

    chart = {
        "report": report_name,
        "chart_type": chart_type,
        "page": f"Supplemental Analysis - {title}",
        "title": title,
        "dimension": dimension,
        "source_table": source_table,
        "measure_used": measure_used,
        "data_available": True,
        "data_status": "supplemental_analysis",
        "data_note": "Generated from recovered PBIX source data to strengthen the analytical report.",
    }
    if series is not None:
        chart["x"] = list(x)
        chart["series"] = series
    else:
        chart["x"] = list(x)
        chart["y"] = [float(item) for item in (y or [])]
    return chart


def build_supplemental_charts(tables, report_name, existing_titles=None, minimum_total=12, current_count=0):

    if current_count >= minimum_total:
        return []

    user_tables = _pbix_user_tables(tables)
    if not user_tables:
        return []

    source_table, df = next(iter(user_tables.items()))
    if df.empty:
        return []

    raw_existing_titles = [str(title or "") for title in (existing_titles or [])]
    existing_titles = {_normalized_title(title) for title in raw_existing_titles}
    supplemental = []
    existing_text = " ".join(raw_existing_titles).lower()
    existing_has_availability = "availability" in existing_text or "stock" in existing_text

    def add_chart(chart):
        if not chart:
            return
        norm_title = _normalized_title(chart.get("title"))
        if not norm_title or norm_title in existing_titles:
            return
        existing_titles.add(norm_title)
        supplemental.append(chart)

    def enough():
        return current_count + len(supplemental) >= minimum_total

    date_candidates = [
        column for column in df.columns
        if any(token in str(column).lower() for token in ["date", "time", "day", "month", "year", "timestamp"])
    ]
    date_column = None
    date_series = None
    for candidate in date_candidates + list(df.columns):
        if candidate in {"price", "discount", "latitude", "longitude", "pincode"}:
            continue
        parsed = _datetime_series(df[candidate])
        if parsed is not None:
            date_column = candidate
            date_series = parsed
            break

    if {"source", "availability"}.issubset(df.columns) and not existing_has_availability:
        grouped = (
            df.dropna(subset=["source", "availability"])
            .groupby(["source", "availability"], as_index=False)
            .size()
            .rename(columns={"size": "__metric__"})
        )
        if not grouped.empty:
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Source Mix by Availability",
                    "treemap",
                    "source_availability",
                    "record_count",
                    [
                        f"{row['source']} — {row['availability']}"
                        for _, row in grouped.iterrows()
                    ],
                    y=grouped["__metric__"].tolist(),
                )
            )
        if enough():
            return supplemental

    if date_column and date_series is not None and "price" in df.columns:
        numeric_price = _numeric_series(df["price"])
        if numeric_price is not None:
            working = pd.DataFrame({"__date__": date_series, "__price__": numeric_price}).dropna()
            if not working.empty:
                grouped = (
                    working.assign(__date_bucket__=working["__date__"].dt.date.astype(str))
                    .groupby("__date_bucket__", as_index=False)["__price__"]
                    .mean()
                    .sort_values("__date_bucket__")
                    .tail(18)
                )
                add_chart(
                    _build_chart_object(
                        report_name,
                        source_table,
                        "Average Price Trend",
                        "lineChart",
                        date_column,
                        "avg_price",
                        grouped["__date_bucket__"].tolist(),
                        y=grouped["__price__"].tolist(),
                    )
                )
        if enough():
            return supplemental

    if date_column and date_series is not None:
        working = pd.DataFrame({"__date__": date_series}).dropna()
        if not working.empty:
            grouped = (
                working.assign(__date_bucket__=working["__date__"].dt.date.astype(str))
                .groupby("__date_bucket__", as_index=False)
                .size()
                .rename(columns={"size": "__metric__"})
                .sort_values("__date_bucket__")
                .tail(18)
            )
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Record Volume Trend",
                    "areaChart",
                    date_column,
                    "record_count",
                    grouped["__date_bucket__"].tolist(),
                    y=grouped["__metric__"].tolist(),
                )
            )
        if enough():
            return supplemental

    if {"source", "price"}.issubset(df.columns):
        numeric_price = _numeric_series(df["price"])
        if numeric_price is not None:
            working = df.assign(__price__=numeric_price).dropna(subset=["source", "__price__"])
            grouped = (
                working.groupby("source", as_index=False)["__price__"]
                .mean()
                .sort_values("__price__", ascending=False)
                .head(10)
            )
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Average Price by Source",
                    "clusteredBarChart",
                    "source",
                    "avg_price",
                    grouped["source"].astype(str).tolist(),
                    y=grouped["__price__"].tolist(),
                )
            )
        if enough():
            return supplemental

    if "brand" in df.columns:
        grouped = (
            df.dropna(subset=["brand"])
            .assign(brand=df["brand"].astype(str).str.strip())
            .loc[lambda frame: frame["brand"].ne("")]
            .groupby("brand", as_index=False)
            .size()
            .rename(columns={"size": "__metric__"})
            .sort_values("__metric__", ascending=False)
            .head(10)
        )
        if not grouped.empty:
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Brand Value Mix",
                    "treemap",
                    "brand",
                    "record_count",
                    grouped["brand"].tolist(),
                    y=grouped["__metric__"].tolist(),
                )
            )
        if enough():
            return supplemental

    if {"category", "price"}.issubset(df.columns):
        numeric_price = _numeric_series(df["price"])
        if numeric_price is not None:
            working = df.assign(__price__=numeric_price).dropna(subset=["category", "__price__"])
            grouped = (
                working.groupby("category", as_index=False)["__price__"]
                .mean()
                .sort_values("__price__", ascending=False)
                .head(10)
            )
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Top 10 Categories by Average Price",
                    "clusteredBarChart",
                    "category",
                    "avg_price",
                    grouped["category"].astype(str).tolist(),
                    y=grouped["__price__"].tolist(),
                )
            )
        if enough():
            return supplemental

    if {"city", "price"}.issubset(df.columns):
        numeric_price = _numeric_series(df["price"])
        if numeric_price is not None:
            working = df.assign(__price__=numeric_price).dropna(subset=["city", "__price__"])
            grouped = (
                working.groupby("city", as_index=False)["__price__"]
                .mean()
                .sort_values("__price__", ascending=False)
                .head(10)
            )
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Top Cities by Average Price",
                    "clusteredBarChart",
                    "city",
                    "avg_price",
                    grouped["city"].astype(str).tolist(),
                    y=grouped["__price__"].tolist(),
                )
            )
        if enough():
            return supplemental

    if {"source", "discount"}.issubset(df.columns):
        numeric_discount = _numeric_series(df["discount"])
        if numeric_discount is not None:
            working = df.assign(__discount__=numeric_discount).dropna(subset=["source", "__discount__"])
            grouped = (
                working.groupby("source", as_index=False)["__discount__"]
                .mean()
                .sort_values("__discount__", ascending=False)
                .head(10)
            )
            if not grouped.empty:
                add_chart(
                    _build_chart_object(
                        report_name,
                        source_table,
                        "Average Discount by Source",
                        "clusteredColumnChart",
                        "source",
                        "avg_discount",
                        grouped["source"].astype(str).tolist(),
                        y=grouped["__discount__"].tolist(),
                    )
                )
        if enough():
            return supplemental

    if {"availability", "price"}.issubset(df.columns):
        numeric_price = _numeric_series(df["price"])
        if numeric_price is not None:
            working = df.assign(__price__=numeric_price).dropna(subset=["availability", "__price__"])
            if not working.empty:
                sampled = working.groupby("availability", group_keys=False).head(180)
                add_chart(
                    _build_chart_object(
                        report_name,
                        source_table,
                        "Price Spread by Availability",
                        "boxPlot",
                        "availability",
                        "price",
                        sampled["availability"].astype(str).tolist(),
                        y=sampled["__price__"].tolist(),
                    )
                )
        if enough():
            return supplemental

    if "price" in df.columns:
        numeric_price = _numeric_series(df["price"])
        if numeric_price is not None:
            sampled = numeric_price.dropna().head(400)
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Price Distribution",
                    "histogram",
                    "price",
                    "frequency",
                    sampled.round(3).tolist(),
                    y=[],
                )
            )
        if enough():
            return supplemental

    if {"discount", "price"}.issubset(df.columns):
        numeric_discount = _numeric_series(df["discount"])
        numeric_price = _numeric_series(df["price"])
        if numeric_discount is not None and numeric_price is not None:
            scatter = (
                pd.DataFrame({
                    "discount": numeric_discount,
                    "price": numeric_price,
                })
                .dropna()
                .head(250)
            )
            if not scatter.empty:
                add_chart(
                    _build_chart_object(
                        report_name,
                        source_table,
                        "Price vs Discount Pattern",
                        "scatterChart",
                        "discount",
                        "price",
                        scatter["discount"].round(3).tolist(),
                        y=scatter["price"].round(3).tolist(),
                    )
                )

    if {"source", "availability"}.issubset(df.columns) and existing_has_availability:
        grouped = (
            df.dropna(subset=["source", "availability"])
            .groupby(["source", "availability"], as_index=False)
            .size()
            .rename(columns={"size": "__metric__"})
        )
        if not grouped.empty:
            add_chart(
                _build_chart_object(
                    report_name,
                    source_table,
                    "Source Mix by Availability",
                    "treemap",
                    "source_availability",
                    "record_count",
                    [
                        f"{row['source']} — {row['availability']}"
                        for _, row in grouped.iterrows()
                    ],
                    y=grouped["__metric__"].tolist(),
                )
            )

    return supplemental


# ======================================================
# PROCESS ALL REPORTS
# ======================================================

def process_all_reports(report_folder, external_data_files=None):

    global LAST_EXTRACTION_SUMMARY

    layout_map = extract_all_reports(report_folder)
    external_tables, external_sources = load_external_tables(external_data_files)

    all_charts = []
    metadata_visuals = []
    summary_reports = []
    total_visuals_seen = 0
    total_missing_table_visuals = 0
    total_fallback_aliases = 0

    for report_name, layout_path in layout_map.items():

        # --------------------------------------------------
        # TABLEAU: TWB / TWBX
        # --------------------------------------------------
        if isinstance(layout_path, tuple) and layout_path[0] == "tableau":
            twb_path = layout_path[1]
            print("Processing Tableau report:", report_name)
            try:
                # process_tableau_reports scans a folder, so pass the folder
                twb_folder = str(Path(twb_path).parent)
                twb_result = _twb.process_tableau_reports(twb_folder)
                twb_charts = twb_result.get("charts", [])
                twb_meta = twb_result.get("metadata", [])
                all_charts.extend(twb_charts)
                metadata_visuals.extend(twb_meta)
                total_visuals_seen += len(twb_charts) + len(twb_meta)
            except Exception as twb_err:
                print(f"Tableau extraction error for {report_name}: {twb_err}")
            continue

        # --------------------------------------------------
        # POWER BI: PBIX
        # --------------------------------------------------
        pbix_path = os.path.join(report_folder, f"{report_name}.pbix")

        if not os.path.exists(pbix_path):
            continue

        print("Processing report:", report_name)

        extracted_tables = extract_tables(pbix_path)
        visuals = extract_visuals(layout_path)
        base_tables_available = sorted(extracted_tables.keys())
        eligible_visuals = [
            vis for vis in visuals if vis.get("visual_category") in {"chart", "kpi"}
        ]
        total_visuals_seen += len(eligible_visuals)

        def run_visual_extraction(tables, fallback_aliases=None):
            fallback_aliases = fallback_aliases or {}
            report_charts_local = []
            report_metadata_local = []
            report_missing_tables_local = set()

            for vis in eligible_visuals:
                charts = build_chart_data(tables, vis, report_name)

                for c in charts:
                    c["report"] = report_name
                    c["visual_type"] = vis.get("visual_type", "unknown")
                    c["visual_category"] = vis.get("visual_category", "chart")
                    c["data_available"] = True
                    source_table = c.get("source_table")
                    fallback_source_name = fallback_aliases.get(source_table)
                    if fallback_source_name:
                        c["data_status"] = "external_fallback"
                        c["data_note"] = (
                            f"Chart data reconstructed from uploaded export table '{fallback_source_name}' "
                            f"to satisfy PBIX table '{source_table}'."
                        )
                        c["fallback_source"] = fallback_source_name
                    else:
                        c["data_status"] = "embedded_data"

                if charts:
                    report_charts_local.extend(charts)
                    continue

                descriptor = build_visual_descriptor(tables, vis, report_name)
                report_metadata_local.append(descriptor)
                if descriptor.get("missing_tables"):
                    report_missing_tables_local.update(descriptor["missing_tables"])

            return report_charts_local, report_metadata_local, report_missing_tables_local

        report_charts, report_metadata, report_missing_tables = run_visual_extraction(extracted_tables)
        fallback_aliases = {}
        tables_available = sorted(extracted_tables.keys())

        if report_missing_tables or (not report_charts and external_tables):
            tables_with_fallback, fallback_aliases = alias_missing_tables(extracted_tables, visuals, external_tables)
            if fallback_aliases:
                total_fallback_aliases += len(fallback_aliases)
                report_charts, report_metadata, report_missing_tables = run_visual_extraction(
                    tables_with_fallback,
                    fallback_aliases,
                )
                tables_available = sorted(tables_with_fallback.keys())
                extracted_tables = tables_with_fallback

        supplemental_charts = build_supplemental_charts(
            extracted_tables,
            report_name,
            existing_titles=[chart.get("title", "") for chart in report_charts],
            minimum_total=12,
            current_count=len(report_charts),
        )
        if supplemental_charts:
            report_charts.extend(supplemental_charts)

        total_missing_table_visuals += sum(1 for descriptor in report_metadata if descriptor.get("missing_tables"))

        all_charts.extend(report_charts)
        metadata_visuals.extend(report_metadata)

        summary_reports.append({
            "report": report_name,
            "embedded_tables_available": base_tables_available,
            "tables_available": tables_available,
            "embedded_table_count": len(base_tables_available),
            "chart_data_count": len(report_charts),
            "metadata_visual_count": len(report_metadata),
            "missing_tables": sorted(report_missing_tables),
            "fallback_aliases": fallback_aliases,
        })

    all_charts = remove_duplicate_charts(all_charts)
    metadata_visuals = dedupe_metadata_visuals(metadata_visuals)
    merged_items = merge_real_and_metadata_charts(all_charts, metadata_visuals)

    LAST_EXTRACTION_SUMMARY = {
        "reports_processed": len(summary_reports),
        "chart_data_count": len(all_charts),
        "metadata_visual_count": len(metadata_visuals),
        "total_items_returned": len(merged_items),
        "visuals_seen": total_visuals_seen,
        "missing_table_visuals": total_missing_table_visuals,
        "external_table_count": len(external_tables),
        "external_sources": external_sources,
        "fallback_alias_count": total_fallback_aliases,
        "reports": summary_reports,
    }

    print(
        "PBIX extraction summary:",
        json.dumps(LAST_EXTRACTION_SUMMARY, indent=2, default=str),
    )

    return merged_items


# ======================================================
# CHART RETRIEVAL
# ======================================================

def retrieve_relevant_charts(query, charts, top_k=6):
    comparison_mode = _is_multi_report_compare(query, charts)
    mentioned_reports = _extract_report_mentions(query, charts)
    all_reports = sorted({str(chart.get("report", "")).strip() for chart in charts if chart.get("report")})
    target_reports = mentioned_reports or (all_reports if comparison_mode else [])

    scored = []

    for chart in charts:
        score = _chart_relevance_score(query, chart)
        report_name = str(chart.get("report", "")).strip()
        if comparison_mode and report_name in target_reports:
            score += 8

        if score > 0 or (comparison_mode and report_name in target_reports):
            scored.append((score, chart))

    scored.sort(
        key=lambda item: (
            item[0],
            1 if item[1].get("data_available", True) else 0,
            1 if item[1].get("series") else 0,
        ),
        reverse=True,
    )

    if comparison_mode and target_reports:
        selected = []
        per_report_counts = {report: 0 for report in target_reports}
        max_items = max(top_k, len(target_reports) * 2)

        for report_name in target_reports:
            first_chart = next(
                (chart for _, chart in scored if str(chart.get("report", "")).strip() == report_name),
                None,
            )
            if first_chart is not None:
                selected.append(first_chart)
                per_report_counts[report_name] += 1

        for score, chart in scored:
            report_name = str(chart.get("report", "")).strip()
            if report_name not in target_reports:
                continue
            if chart in selected:
                continue
            if per_report_counts[report_name] >= 3:
                continue
            selected.append(chart)
            per_report_counts[report_name] += 1
            if len(selected) >= max_items:
                break

        if len(selected) < len(target_reports):
            for report_name in target_reports:
                if per_report_counts[report_name]:
                    continue
                fallback_chart = next(
                    (chart for chart in charts if str(chart.get("report", "")).strip() == report_name),
                    None,
                )
                if fallback_chart is not None:
                    selected.append(fallback_chart)
                    per_report_counts[report_name] += 1

        return selected[:max_items]

    return [chart for _, chart in scored[:top_k]]


# ======================================================
# SIMPLIFY CHART FOR LLM
# ======================================================

def simplify_chart_for_llm(chart):

    clean = {
        "report": chart.get("report"),
        "page": chart.get("page"),
        "title": chart.get("title"),
        "chart_type": chart.get("chart_type"),
        "dimension": chart.get("dimension"),
        "measure_used": chart.get("measure_used"),
        "data_available": chart.get("data_available", True),
        "data_status": chart.get("data_status"),
    }

    if "x" in chart:
        clean["x"] = chart["x"][:20]

    if "y" in chart:
        clean["y"] = chart["y"][:20]

    if "series" in chart:
        clean["series"] = chart["series"][:5]

    if chart.get("fallback_source"):
        clean["fallback_source"] = chart.get("fallback_source")

    if not chart.get("data_available", True):
        clean["required_tables"] = chart.get("required_tables", [])
        clean["missing_tables"] = chart.get("missing_tables", [])
        clean["field_rows"] = chart.get("field_rows", [])[:12]
        clean["measures"] = chart.get("measures", [])[:8]
        clean["data_note"] = chart.get("data_note")

    return clean


# ======================================================
# NORMALIZE OUTPUT
# ======================================================

def normalize_chart_output(result):

    if not isinstance(result, dict):
        return result

    preserved_sections = {}
    for key in PBIX_ANALYSIS_SECTION_KEYS + PBIX_TEXT_SECTION_KEYS:
        value = result.get(key)
        if value:
            preserved_sections[key] = value

    x = result.get("x", [])

    if "series" in result:

        normalized = {
            "response_type":"new_chart",
            "title":result.get("title","Generated Chart"),
            "chart_type":result.get("chart_type","clusteredColumnChart"),
            "x":x,
            "series":result["series"],
        }
        normalized.update(preserved_sections)
        return normalized

    if "y" in result:

        normalized = {
            "response_type":"new_chart",
            "title":result.get("title","Generated Chart"),
            "chart_type":result.get("chart_type","clusteredColumnChart"),
            "x":x,
            "y":result["y"],
        }
        normalized.update(preserved_sections)
        return normalized

    return result


def _extract_pbix_analysis_payload(result):

    if not isinstance(result, dict):
        return {}

    payload = {}
    for key in PBIX_ANALYSIS_SECTION_KEYS + PBIX_TEXT_SECTION_KEYS:
        value = result.get(key)
        if value:
            payload[key] = value
    return payload


def _has_structured_pbix_analysis(result):

    if not isinstance(result, dict):
        return False

    return any(
        result.get(key)
        for key in (
            "overall_performance_summary",
            "key_insights_and_drivers",
            "risks_and_issues",
            "data_driven_recommendations",
        )
    )


# ======================================================
# GEMINI QUERY
# ======================================================

def ask_gemini_charts(query, charts):

    if not charts:
        return {
            "response_type":"explanation",
            "text":"No chart data available."
        }

    chart_intent = detect_chart_intent(query)
    comparison_mode = _is_multi_report_compare(query, charts)

    charts_to_use = retrieve_relevant_charts(query, charts)

    if not charts_to_use:
        charts_to_use = charts[:5]

    charts_for_llm = [simplify_chart_for_llm(c) for c in charts_to_use]
    report_scope = sorted({chart.get("report") for chart in charts_for_llm if chart.get("report")})


    # ==================================================
    # CHART PROMPT (COMPARISON AWARE)
    # ==================================================

    chart_prompt = f"""
You are a BI dashboard analyst.

Use the provided dashboard chart data to answer the user's question.

If the question asks to compare two charts:

â€¢ create ONE chart
â€¢ keep datasets separate
â€¢ use the same x-axis
â€¢ create two series
â€¢ do NOT merge or sum values

Return JSON only.

Example format:

{{
 "response_type":"new_chart",
 "title":"Comparison Chart",
 "chart_type":"clusteredColumnChart",
 "x":["Category1","Category2"],
 "series":[
   {{"name":"Dataset A","y":[10,20]}},
   {{"name":"Dataset B","y":[15,18]}}
 ],
 "insights":[
   "Insight 1",
   "Insight 2"
 ]
}}

Available chart data:
{json.dumps(charts_for_llm)}

User question:
{query}

If a visual has "data_available": false, do not invent numeric chart values.
You may explain the chart definition, fields, and missing embedded tables instead.
"""


    explanation_prompt = f"""
Explain the dashboard insights thoroughly using explicit numbers from the data. Do not give vague descriptions; instead, compute exact numerical differences, percentage jumps, minimums, maximums, and data-backed trends.

Data:
{json.dumps(charts_for_llm)}

Question:
{query}

If a visual has "data_available": false, clearly say the PBIX contains the visual definition
but not the embedded source data required for exact numeric analysis.

Return JSON only.

{{
 "response_type":"explanation",
 "text":"Explanation of the dashboards"
}}
"""

    chart_prompt = f"""
You are a BI dashboard analyst.

Use the provided dashboard chart data to answer the user's question.

If the question asks to compare two or more reports/charts:

- create ONE comparison chart
- keep each report separate as its own series
- use the same x-axis categories across reports
- create one series per report when possible
- do NOT merge, sum, or average different reports together unless the user explicitly asks for it
- prefer charts that share the same dimension and metric across reports
- if a clean numeric comparison is not possible, return an explanation instead of inventing data

Return JSON only.

Example format:

{{
 "response_type":"new_chart",
 "title":"Comparison Chart",
 "chart_type":"lineChart",
 "x":["Category1","Category2"],
 "series":[
   {{"name":"Report A","y":[10,20]}},
   {{"name":"Report B","y":[15,18]}},
   {{"name":"Report C","y":[12,25]}}
 ],
 "overall_performance_summary":"2-4 sentence summary with explicit numbers.",
 "key_insights_and_drivers":[
   "Insight 1 with an exact metric",
   "Insight 2 with an exact metric"
 ],
 "risks_and_issues":[
   "Risk 1 with explicit evidence"
 ],
 "data_driven_recommendations":[
   "Recommendation 1 tied to the chart evidence"
 ],
 "insights":[
   "Short supporting takeaway"
 ]
}}

Reports in scope:
{json.dumps(report_scope)}

Comparison mode:
{json.dumps(comparison_mode)}

Available chart data:
{json.dumps(charts_for_llm)}

User question:
{query}

If a visual has "data_available": false, do not invent numeric chart values.
You may explain the chart definition, fields, and missing embedded tables instead.
If you return a chart, you must also return:
- overall_performance_summary
- key_insights_and_drivers
- risks_and_issues
- data_driven_recommendations
Keep these concise, business-focused, and numeric where possible.
Choose chart types based on fit:
- lineChart or areaChart for trends over time
- treemap for composition/share across categories
- scatterChart for numeric relationships
- clusteredBarChart or clusteredColumnChart for rankings and category comparisons
- donutChart only for very small composition views
"""

    explanation_prompt = f"""
Explain the dashboard insights thoroughly using explicit numbers from the data. Do not give vague descriptions; instead, compute exact numerical differences, percentage jumps, minimums, maximums, and data-backed trends.

Data:
{json.dumps(charts_for_llm)}

Question:
{query}

Reports in scope:
{json.dumps(report_scope)}

If multiple reports are in scope, structure the answer as:
- Report-by-report snapshot
- Cross-report comparison
- Final recommendation

If a visual has "data_available": false, clearly say the PBIX contains the visual definition
but not the embedded source data required for exact numeric analysis.

Return JSON only.

{{
 "response_type":"explanation",
 "overall_performance_summary":"2-4 sentence summary with exact numbers.",
 "key_insights_and_drivers":[
   "Key driver 1 with numeric evidence",
   "Key driver 2 with numeric evidence"
 ],
 "risks_and_issues":[
   "Risk 1 with numeric evidence"
 ],
 "data_driven_recommendations":[
   "Recommendation 1 tied to the metrics"
 ],
 "insights":[
   "Short supporting takeaway"
 ],
 "text":"Optional closing sentence."
}}
"""

    prompt = chart_prompt if chart_intent else explanation_prompt


    def run_prompt(selected_prompt):
        max_retries = 3
        import time

        if not API_KEY or client is None:
            if chart_intent:
                return {
                    "answer": "Gemini API key is not configured in this environment, so AI chart insights are temporarily unavailable.",
                    "insights": [],
                    "key_insights_and_drivers": [],
                    "risks_and_issues": [],
                    "data_driven_recommendations": [],
                    "overall_performance_summary": "",
                    "chart": None,
                }
            return {
                "answer": "Gemini API key is not configured in this environment, so AI-generated analysis is temporarily unavailable.",
                "insights": [],
                "key_insights_and_drivers": [],
                "risks_and_issues": [],
                "data_driven_recommendations": [],
                "overall_performance_summary": "",
                "text": "",
            }

        for attempt in range(max_retries):
            try:
                response = generate_content_with_model_fallback(
                    client=client,
                    model="gemini-3.1-flash-lite",
                    contents=[selected_prompt],
                    api_key=API_KEY,
                )

                text = response.text.strip()

                # Robust JSON extraction: handles markdown fences and
                # conversational text surrounding the JSON block
                try:
                    clean = text
                    if "```json" in clean:
                        clean = clean[clean.find("```json") + 7:]
                        clean = clean[:clean.rfind("```")]
                    elif clean.startswith("```"):
                        clean = clean[3:]
                        if clean.endswith("```"):
                            clean = clean[:-3]

                    clean = clean.strip()

                    # Extract outermost { ... } block
                    start = clean.find("{")
                    end = clean.rfind("}") + 1
                    if start != -1 and end > start:
                        clean = clean[start:end]

                    return json.loads(clean)
                except Exception:
                    return {
                        "response_type": "explanation",
                        "text": text
                    }

            except Exception as e:
                err_str = str(e).upper()
                if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
                    if attempt < max_retries - 1:
                        wait_time = (2 ** attempt) + 3
                        print(f"API busy/unavailable ({e}). Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print("Gemini error (fatal API issue):", e)
                        return {
                            "response_type":"explanation",
                            "text": f"AI analysis failed due to persistent API error: {e}"
                        }
                else:
                    print("Gemini general error:", e)
                    return {
                        "response_type":"explanation",
                        "text": "AI analysis failed."
                    }

    result = run_prompt(prompt)

    if chart_intent:
        normalized = normalize_chart_output(result)
        if (
            isinstance(normalized, dict)
            and normalized.get("response_type") == "new_chart"
            and not _has_structured_pbix_analysis(normalized)
        ):
            explanation_result = run_prompt(explanation_prompt)
            for key, value in _extract_pbix_analysis_payload(explanation_result).items():
                if value and not normalized.get(key):
                    normalized[key] = value
        return normalized

    return result

# ======================================================
# UNIFIED REPORT GENERATION
# ======================================================

def generate_unified_insights(csv_insights, pbix_charts_json):
    """
    Combines CSV dataset insights (from orchestrator) with PowerBI charts context.
    """
    prompt = f"""
You are an expert Data Analyst and Business Intelligence Professional.

We have extracted data from two sources:
1. A raw dataset (CSV)
2. Power BI Dashboards (.pbix)

Please synthesize a comprehensive 'Unified Business Analysis Report' integrating both data sources.

### CSV Dataset Insights:
{json.dumps(csv_insights, indent=2, default=str)}

### Power BI Extracted Data:
{json.dumps(pbix_charts_json, indent=2, default=str)}

Write a professional, well-structured multi-section markdown report. 
Highlight the key combined findings, anomalies, and recommendations. 
Ensure that your summaries are strictly analytical and cite exact numerical insights (metrics, growth percentages, exact counts) rather than vague qualitative descriptions.
Do not return JSON, just return professional formatting in markdown.
"""

    max_retries = 3
    import time
    if client is None or not API_KEY:
        return "Gemini API key is not configured in this environment, so the unified report could not be generated."
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[prompt]
            )
            return response.text.strip()
        except Exception as e:
            err_str = str(e).upper()
            if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 3
                    print(f"API busy/unavailable ({e}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    return f"Failed to generate unified report due to API error: {e}"
            else:
                return f"Failed to generate unified report due to API error: {e}"
