import json
import re
import pandas as pd


# ======================================================
# SAFE JSON LOAD
# ======================================================

def safe_json_load(text):
    try:
        return json.loads(text)
    except Exception:
        return {}


# ======================================================
# VISUAL TYPE CLASSIFICATION
# ======================================================

def classify_visual(visual_type):

    chart_types = [
        "columnChart",
        "barChart",
        "lineChart",
        "areaChart",
        "pieChart",
        "donutChart",
        "scatterChart",
        "lineStackedColumnComboChart",
        "multiAxisPieChart",
        "clusteredBarChart",
        "clusteredColumnChart",
        "stackedColumnChart",
        "stackedBarChart",
        "hundredPercentStackedColumnChart",
        "hundredPercentStackedBarChart",
        "treemap",
        "funnel",
        "gauge",
        "stackedAreaChart",
        "ribbonChart",
        "cardVisual",
    ]

    if visual_type in chart_types:
        return "chart"

    if visual_type in ["tableEx", "matrix"]:
        return "table"

    if visual_type in ["card", "multiRowCard", "kpi"]:
        return "kpi"

    if visual_type == "slicer":
        return "filter"

    return "other"


# ======================================================
# FIELD PARSING HELPERS
# ======================================================

AGGREGATION_PREFIXES = (
    "Sum",
    "Avg",
    "Average",
    "Min",
    "Max",
    "Count",
    "CountNonNull",
    "DistinctCount",
)

DATE_HIERARCHY_PATTERN = re.compile(
    r"^(?P<base>.+?)(?:\.Variation)?\.Date Hierarchy\.(?P<level>Year|Quarter|Month|Day)$",
    re.IGNORECASE,
)


def parse_query_ref(query_ref):

    if not query_ref:
        return None

    text = str(query_ref).strip()

    agg_match = re.match(r"([A-Za-z]+)\((.+)\)$", text)
    if agg_match:
        inner = agg_match.group(2).strip()
        bracket_inner = re.match(r"'?([^']+)'?\[([^\]]+)\]", inner)
        if bracket_inner:
            return (bracket_inner.group(1).strip(), bracket_inner.group(2).strip())
        field_match = re.match(r"([^.]+)\.([^)]+)$", inner)
        if field_match:
            return (field_match.group(1), field_match.group(2))
        return inner

    bracket_match = re.match(r"'?([^']+)'?\[([^\]]+)\]", text)
    if bracket_match:
        return (bracket_match.group(1).strip(), bracket_match.group(2).strip())

    dotted = re.match(r"([^.]+)\.([A-Za-z0-9_ ]+?)(\d+)?$", text)
    if dotted:
        return (dotted.group(1), dotted.group(2).strip())

    return text


def field_display_name(field):

    if isinstance(field, tuple):
        return f"{field[0]}.{field[1]}"

    return str(field)


def field_name_only(field):

    if isinstance(field, tuple):
        return str(field[1])

    text = str(field)

    agg_match = re.match(r"[A-Za-z]+\((.+)\)$", text)
    if agg_match:
        text = agg_match.group(1)

    if "." in text:
        return text.split(".", 1)[1]

    return text


def hierarchy_granularity(field):

    text = field_name_only(field)
    match = DATE_HIERARCHY_PATTERN.match(text)
    if not match:
        return None

    return match.group("level").lower()


def hierarchy_rank(field):

    granularity = hierarchy_granularity(field)
    ranks = {
        "year": 1,
        "quarter": 2,
        "month": 3,
        "day": 4,
    }
    return ranks.get(granularity, 0)


def collect_roles_from_config(single_visual):

    roles_map = {}
    query_refs_map = {}

    for role, items in single_visual.get("projections", {}).items():
        parsed_fields = []
        raw_refs = []
        for item in items or []:
            raw_query_ref = item.get("queryRef")
            if raw_query_ref:
                raw_refs.append(str(raw_query_ref))
            field = parse_query_ref(raw_query_ref)
            if field:
                parsed_fields.append(field)
        if parsed_fields:
            roles_map.setdefault(role, []).extend(parsed_fields)
        if raw_refs:
            query_refs_map.setdefault(role, []).extend(raw_refs)

    return roles_map, query_refs_map


def role_field_rows(roles_map):

    rows = []

    for role, fields in roles_map.items():
        for field in fields:
            if isinstance(field, tuple):
                rows.append({
                    "role": role,
                    "table": str(field[0]),
                    "field": str(field[1]),
                })
            else:
                rows.append({
                    "role": role,
                    "table": "",
                    "field": str(field),
                })

    return rows


# ======================================================
# EXTRACT VISUAL TITLE
# ======================================================

def extract_title(visual, config):

    title = None

    try:
        title = (
            visual.get("objects", {})
            .get("title", [{}])[0]
            .get("properties", {})
            .get("text", {})
            .get("expr", {})
            .get("Literal", {})
            .get("Value")
        )
    except:
        pass

    if not title:
        try:
            title = (
                visual.get("vcObjects", {})
                .get("title", [{}])[0]
                .get("properties", {})
                .get("text", {})
                .get("expr", {})
                .get("Literal", {})
                .get("Value")
            )
        except:
            pass

    if not title:
        try:
            title = (
                config.get("vcObjects", {})
                .get("title", [{}])[0]
                .get("properties", {})
                .get("text", {})
                .get("expr", {})
                .get("Literal", {})
                .get("Value")
            )
        except:
            pass

    if isinstance(title, str):
        title = title.strip()
        if len(title) >= 2 and title[0] == "'" and title[-1] == "'":
            title = title[1:-1]
        if len(title) >= 2 and title[0] == '"' and title[-1] == '"':
            title = title[1:-1]

    return title


# ======================================================
# GENERATE FALLBACK TITLE
# ======================================================

def generate_fallback_title(roles_map, visual_type):

    fields = []

    for role_fields in roles_map.values():

        for f in role_fields:

            if isinstance(f, tuple):
                fields.append(f[1])
            else:
                fields.append(str(f))

    if len(fields) >= 2:
        return f"{fields[1]} by {fields[0]}"

    if len(fields) == 1:
        return fields[0]

    return visual_type


# ======================================================
# EXTRACT VISUALS FROM PBIX LAYOUT
# ======================================================

def extract_visuals(layout_path):

    with open(layout_path, "r", encoding="utf-16-le") as f:
        layout = json.load(f)

    visuals = []

    for section in layout.get("sections", []):

        page_name = section.get("displayName", "Unknown Page")

        for vc in section.get("visualContainers", []):

            config = safe_json_load(vc.get("config", "{}"))

            visual = config.get("singleVisual", {})

            visual_type = visual.get("visualType", "unknown")

            title = extract_title(visual, config)

            roles_map = {}

            if "dataTransforms" in vc:

                dt = safe_json_load(vc.get("dataTransforms", "{}"))

                for sel in dt.get("selects", []):

                    expr = sel.get("expr", {})
                    roles = sel.get("roles", {})

                    field = None

                    if "Column" in expr:
                        try:
                            entity = expr["Column"]["Expression"]["SourceRef"]["Entity"]
                            prop = expr["Column"]["Property"]
                            field = (entity, prop)
                        except:
                            continue

                    elif "Measure" in expr:
                        try:
                            field = expr["Measure"]["Property"]
                        except:
                            continue

                    if not field:
                        continue

                    for role in roles:
                        roles_map.setdefault(role, []).append(field)

            config_roles, query_refs = collect_roles_from_config(visual)
            for role, fields in config_roles.items():
                existing = roles_map.setdefault(role, [])
                for field in fields:
                    if field not in existing:
                        existing.append(field)

            if not title:
                title = generate_fallback_title(roles_map, visual_type)

            visuals.append({
                "page_name": page_name,
                "visual_type": visual_type,
                "visual_category": classify_visual(visual_type),
                "title": title,
                "roles": roles_map,
                "query_refs": query_refs,
                "field_rows": role_field_rows(roles_map),
            })

    return visuals


# ======================================================
# SELECT NUMERIC MEASURE
# ======================================================

def select_measure(df, value_fields):

    if value_fields:

        candidate = value_fields[0]

        if isinstance(candidate, tuple):
            candidate = candidate[1]

        if candidate in df.columns:
            return candidate

    numeric_cols = df.select_dtypes(include="number").columns

    filtered = [
        c for c in numeric_cols
        if not any(x in c.lower() for x in ["id", "key", "code"])
    ]

    if filtered:
        return filtered[0]

    if len(numeric_cols) > 0:
        return numeric_cols[0]

    return None


# ======================================================
# ENSURE SERIES (FIX MULTI DIMENSION ERROR)
# ======================================================

def ensure_series(df, col):

    if col not in df.columns:
        return df

    if isinstance(df[col], pd.DataFrame):
        df[col] = df[col].iloc[:, 0]

    return df


def parse_aggregation_name(query_ref):

    if not query_ref:
        return None

    match = re.match(r"([A-Za-z]+)\((.+)\)$", str(query_ref).strip())
    if not match:
        return None

    agg = match.group(1).lower()

    if agg in {"count", "countnonnull"}:
        return "count"
    if agg == "distinctcount":
        return "nunique"
    if agg in {"sum", "avg", "average", "min", "max"}:
        return {"avg": "mean", "average": "mean"}.get(agg, agg)

    return None


def first_query_ref(query_refs_map, roles):

    for role in roles:
        refs = query_refs_map.get(role, [])
        if refs:
            return refs[0]

    return None


def first_field_name(fields):

    if not fields:
        return None

    first = fields[0]
    if isinstance(first, tuple):
        return first[1]

    return str(first)


def resolve_table_field(field, tables):

    if isinstance(field, tuple) and len(field) >= 2:
        return str(field[0]), str(field[1])

    parsed = parse_query_ref(field)
    if isinstance(parsed, tuple) and len(parsed) >= 2:
        return str(parsed[0]), str(parsed[1])

    field_name = field_name_only(parsed)
    user_tables = [
        name for name in tables.keys()
        if not str(name).startswith("DateTableTemplate")
        and not str(name).startswith("LocalDateTable")
    ]
    if len(user_tables) == 1:
        return str(user_tables[0]), str(field_name)

    return None


def resolve_dataframe_column(df, field_name):

    candidate = str(field_name)
    if candidate in df.columns:
        return candidate, None

    match = DATE_HIERARCHY_PATTERN.match(candidate)
    if match:
        base = match.group("base").strip()
        if base in df.columns:
            return base, match.group("level").lower()

    normalized_columns = {
        re.sub(r"[^a-z0-9]+", "", str(column).lower()): column
        for column in df.columns
    }

    direct_key = re.sub(r"[^a-z0-9]+", "", candidate.lower())
    if direct_key in normalized_columns:
        return normalized_columns[direct_key], None

    if match:
        base_key = re.sub(r"[^a-z0-9]+", "", match.group("base").lower())
        if base_key in normalized_columns:
            return normalized_columns[base_key], match.group("level").lower()

    return None, None


def coerce_numeric_series(series):

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        text_series = series.astype(str).str.strip()
        cleaned = (
            text_series
            .str.replace(",", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace("₹", "", regex=False)
            .str.replace("$", "", regex=False)
            .str.replace("(", "-", regex=False)
            .str.replace(")", "", regex=False)
        )
        numeric = pd.to_numeric(cleaned, errors="coerce")
        if numeric.notna().sum() == 0:
            return None
    return numeric


def prepare_category_column(df, column_name, granularity=None):

    if not granularity:
        return df, column_name

    if column_name not in df.columns:
        return df, column_name

    datetime_series = pd.to_datetime(df[column_name], errors="coerce")
    if datetime_series.notna().sum() == 0:
        return df, column_name

    working = df.copy()
    derived_col = "__category_group__"

    if granularity == "year":
        working[derived_col] = datetime_series.dt.year.astype("Int64").astype(str)
    elif granularity == "quarter":
        working[derived_col] = (
            datetime_series.dt.year.astype("Int64").astype(str)
            + " Q"
            + datetime_series.dt.quarter.astype("Int64").astype(str)
        )
    elif granularity == "month":
        working[derived_col] = datetime_series.dt.to_period("M").astype(str)
    elif granularity == "day":
        working[derived_col] = datetime_series.dt.date.astype(str)
    else:
        return df, column_name

    return working, derived_col


def choose_category_field(category_fields, tables):

    if not category_fields:
        return None

    ranked_fields = sorted(category_fields, key=hierarchy_rank, reverse=True)

    for field in ranked_fields:
        resolved = resolve_table_field(field, tables)
        if resolved:
            return resolved, hierarchy_granularity(field)

    return None


def aggregate_card_value(df, measure_strategy):

    agg = measure_strategy.get("aggregation", "count")
    field = measure_strategy.get("field")

    if agg == "count":
        if field and field in df.columns:
            return float(df[field].count())
        return float(len(df))

    if agg == "nunique":
        if not field or field not in df.columns:
            return None
        return float(df[field].nunique())

    if not field or field not in df.columns:
        return None

    numeric_series = measure_strategy.get("numeric_series")
    if numeric_series is None:
        numeric_series = coerce_numeric_series(df[field])
    if numeric_series is None:
        return None

    series = numeric_series.dropna()
    if series.empty:
        return None

    if agg == "sum":
        return float(series.sum())
    if agg == "mean":
        return float(series.mean())
    if agg == "min":
        return float(series.min())
    if agg == "max":
        return float(series.max())

    return None


def build_card_chart_data(tables, visual, report_name):

    roles = visual.get("roles", {})
    query_refs = visual.get("query_refs", {})
    data_fields = roles.get("Data", [])

    if not data_fields:
        return []

    resolved_data = resolve_table_field(data_fields[0], tables)
    if not resolved_data:
        return []

    source_table, raw_col = resolved_data
    if source_table not in tables:
        return []

    df = tables[source_table].copy()
    df = df.loc[:, ~df.columns.duplicated()]
    resolved_col, _ = resolve_dataframe_column(df, raw_col)
    if not resolved_col:
        return []

    measure_strategy = determine_measure_strategy(
        df,
        [(source_table, resolved_col)],
        {"Values": query_refs.get("Data", [])},
    )
    if not measure_strategy:
        query_ref = first_query_ref(query_refs, ["Data"])
        agg = parse_aggregation_name(query_ref)
        if agg == "count":
            measure_strategy = {
                "field": resolved_col,
                "aggregation": "count",
                "label": "record_count",
            }
        elif agg == "nunique":
            measure_strategy = {
                "field": resolved_col,
                "aggregation": "nunique",
                "label": f"{resolved_col}_distinct_count",
            }
        else:
            numeric_series = coerce_numeric_series(df[resolved_col])
            if numeric_series is None:
                return []
            measure_strategy = {
                "field": resolved_col,
                "aggregation": agg or "sum",
                "label": resolved_col,
                "numeric_series": numeric_series,
            }

    metric_value = aggregate_card_value(df, measure_strategy)
    if metric_value is None:
        return []

    return [{
        "report": report_name,
        "chart_type": visual["visual_type"],
        "page": visual["page_name"],
        "title": visual["title"],
        "dimension": resolved_col,
        "source_table": source_table,
        "x": [visual.get("title") or resolved_col],
        "y": [metric_value],
        "measure_used": measure_strategy.get("label"),
        "card_value": metric_value,
    }]


def build_scatter_chart_data(tables, visual, report_name):

    roles = visual.get("roles", {})
    x_fields = roles.get("X", [])
    y_fields = roles.get("Y", []) or roles.get("Values", [])
    series_fields = roles.get("Series", []) or roles.get("Legend", [])

    if not x_fields or not y_fields:
        return []

    resolved_x = resolve_table_field(x_fields[0], tables)
    resolved_y = resolve_table_field(y_fields[0], tables)

    if not resolved_x or not resolved_y:
        return []

    x_table, raw_x_col = resolved_x
    y_table, raw_y_col = resolved_y
    if x_table != y_table or x_table not in tables:
        return []

    df = tables[x_table].copy()
    df = df.loc[:, ~df.columns.duplicated()]

    x_col, _ = resolve_dataframe_column(df, raw_x_col)
    y_col, _ = resolve_dataframe_column(df, raw_y_col)
    if not x_col or not y_col:
        return []

    x_numeric = coerce_numeric_series(df[x_col])
    y_numeric = coerce_numeric_series(df[y_col])
    if x_numeric is None or y_numeric is None:
        return []

    working = df.copy()
    working["__scatter_x__"] = x_numeric
    working["__scatter_y__"] = y_numeric
    working = working.dropna(subset=["__scatter_x__", "__scatter_y__"])
    if working.empty:
        return []

    chart = {
        "report": report_name,
        "chart_type": visual["visual_type"],
        "page": visual["page_name"],
        "title": visual["title"],
        "dimension": x_col,
        "source_table": x_table,
        "measure_used": y_col,
    }

    if series_fields:
        resolved_series = resolve_table_field(series_fields[0], tables)
        if resolved_series:
            _, raw_series_col = resolved_series
            series_col, _ = resolve_dataframe_column(working, raw_series_col)
        else:
            series_col = None

        if series_col and series_col in working.columns:
            series_payload = []
            for series_name, subset in working.groupby(series_col):
                series_payload.append({
                    "name": str(series_name),
                    "x": subset["__scatter_x__"].astype(float).tolist(),
                    "y": subset["__scatter_y__"].astype(float).tolist(),
                })
            if series_payload:
                chart["series"] = series_payload
                chart["x"] = []
                return [chart]

    chart["x"] = working["__scatter_x__"].astype(float).tolist()
    chart["y"] = working["__scatter_y__"].astype(float).tolist()
    return [chart]


def determine_measure_strategy(df, value_fields, query_refs_map):

    query_ref = first_query_ref(query_refs_map, ["Values", "Y", "Measure", "Columns"])
    aggregation = parse_aggregation_name(query_ref)
    candidate = first_field_name(value_fields)
    numeric_candidate = None

    if candidate in df.columns:
        numeric_candidate = coerce_numeric_series(df[candidate])
        if aggregation in {"count", "nunique"}:
            return {
                "field": candidate,
                "aggregation": aggregation,
                "label": "record_count" if aggregation == "count" else f"{candidate}_distinct_count",
            }

        if numeric_candidate is not None:
            return {
                "field": candidate,
                "aggregation": aggregation or "sum",
                "label": candidate,
                "numeric_series": numeric_candidate,
            }

        if aggregation in {"sum", "mean", "min", "max"}:
            return None

    fallback_field = select_measure(df, value_fields)
    if fallback_field and fallback_field in df.columns:
        numeric_fallback = coerce_numeric_series(df[fallback_field])
        if numeric_fallback is not None:
            return {
                "field": fallback_field,
                "aggregation": aggregation or "sum",
                "label": fallback_field,
                "numeric_series": numeric_fallback,
            }

    label = candidate or fallback_field or "records"
    return {
        "field": candidate if candidate in df.columns else None,
        "aggregation": "count",
        "label": "record_count",
    }


def aggregate_grouped_dataframe(df, group_cols, measure_strategy):

    working = df.copy()
    agg = measure_strategy.get("aggregation", "count")
    field = measure_strategy.get("field")

    if agg == "count":
        if field and field in group_cols:
            grouped = (
                working.dropna(subset=group_cols)
                .groupby(group_cols, as_index=False)
                .size()
                .rename(columns={"size": "__metric__"})
            )
        elif field and field in working.columns:
            grouped = (
                working.dropna(subset=group_cols + [field])
                .groupby(group_cols, as_index=False)[field]
                .count()
                .rename(columns={field: "__metric__"})
            )
        else:
            grouped = (
                working.dropna(subset=group_cols)
                .groupby(group_cols, as_index=False)
                .size()
                .rename(columns={"size": "__metric__"})
            )
        return grouped

    if agg == "nunique":
        if not field or field not in working.columns:
            return None
        grouped = (
            working.dropna(subset=group_cols + [field])
            .groupby(group_cols, as_index=False)[field]
            .nunique()
            .rename(columns={field: "__metric__"})
        )
        return grouped

    if not field or field not in working.columns:
        return None

    numeric_series = measure_strategy.get("numeric_series")
    if numeric_series is None:
        numeric_series = coerce_numeric_series(working[field])
    if numeric_series is None:
        return None

    working = working.copy()
    working["__metric_numeric__"] = numeric_series
    grouped = (
        working.dropna(subset=group_cols + ["__metric_numeric__"])
        .groupby(group_cols, as_index=False)["__metric_numeric__"]
        .agg(agg)
        .rename(columns={"__metric_numeric__": "__metric__"})
    )
    return grouped


# ======================================================
# BUILD CHART DATA
# ======================================================

def build_chart_data(tables, visual, report_name):

    charts = []

    roles = visual.get("roles", {})
    query_refs = visual.get("query_refs", {})
    visual_type = str(visual.get("visual_type", "")).lower()

    category_fields = (
        roles.get("Category", [])
        or roles.get("Axis", [])
        or roles.get("X", [])
        or roles.get("Rows", [])
        or roles.get("Group", [])
    )

    value_fields = (
        roles.get("Values", [])
        or roles.get("Y", [])
        or roles.get("Measure", [])
        or roles.get("Columns", [])
        or roles.get("Value", [])
    )

    series_fields = (
        roles.get("Series", [])
        or roles.get("Legend", [])
    )

    if "card" in visual_type:
        return build_card_chart_data(tables, visual, report_name)

    if "scatter" in visual_type:
        return build_scatter_chart_data(tables, visual, report_name)

    if not category_fields:
        return charts

    chosen_category = choose_category_field(category_fields, tables)
    if not chosen_category:
        return charts

    resolved_category, category_granularity = chosen_category
    x_table, raw_x_col = resolved_category

    if x_table not in tables:
        return charts

    df = tables[x_table].copy()

    df = df.loc[:, ~df.columns.duplicated()]

    x_col, category_granularity = resolve_dataframe_column(df, raw_x_col)
    if not x_col or x_col not in df.columns:
        return charts

    df, prepared_x_col = prepare_category_column(df, x_col, category_granularity)
    if prepared_x_col:
        x_col = prepared_x_col

    df = ensure_series(df, x_col)

    measure_strategy = determine_measure_strategy(df, value_fields, query_refs)
    if not measure_strategy:
        return charts

    y_col = measure_strategy.get("field")
    if y_col:
        resolved_y_col, _ = resolve_dataframe_column(df, y_col)
        if resolved_y_col and resolved_y_col in df.columns:
            measure_strategy = dict(measure_strategy)
            measure_strategy["field"] = resolved_y_col
            y_col = resolved_y_col
            df = ensure_series(df, y_col)

    try:

        # MULTI SERIES
        if series_fields:

            resolved_series = resolve_table_field(series_fields[0], {x_table: df})
            if resolved_series:
                _, raw_s_col = resolved_series
                s_col, _ = resolve_dataframe_column(df, raw_s_col)
            else:
                s_col, _ = resolve_dataframe_column(df, field_name_only(series_fields[0]))

            if s_col in df.columns:

                df = ensure_series(df, s_col)
                grouped = aggregate_grouped_dataframe(df, [x_col, s_col], measure_strategy)
                if grouped is None or grouped.empty:
                    return charts

                x_values = sorted(grouped[x_col].astype(str).unique())

                chart = {
                    "report": report_name,
                    "chart_type": visual["visual_type"],
                    "page": visual["page_name"],
                    "title": visual["title"],
                    "dimension": x_col,
                    "source_table": x_table,
                    "x": x_values,
                    "series": [],
                    "measure_used": measure_strategy.get("label")
                }

                for series_name in grouped[s_col].unique():

                    subset = grouped[grouped[s_col] == series_name]

                    mapping = dict(zip(subset[x_col], subset["__metric__"]))

                    y_values = [
                        float(mapping.get(x, 0))
                        for x in x_values
                    ]

                    chart["series"].append({
                        "name": str(series_name),
                        "y": y_values
                    })

                charts.append(chart)
                return charts

        grouped = aggregate_grouped_dataframe(df, [x_col], measure_strategy)
        if grouped is None or grouped.empty:
            return charts

        charts.append({
            "report": report_name,
            "chart_type": visual["visual_type"],
            "page": visual["page_name"],
            "title": visual["title"],
            "dimension": x_col,
            "source_table": x_table,
            "x": grouped[x_col].astype(str).tolist(),
            "y": grouped["__metric__"].astype(float).tolist(),
            "measure_used": measure_strategy.get("label")
        })

    except Exception as e:
        print("Chart error:", e)

    return charts


# ======================================================
# BUILD VISUAL METADATA FALLBACK
# ======================================================

def build_visual_descriptor(tables, visual, report_name):

    roles = visual.get("roles", {})

    required_tables = sorted({
        str(field[0])
        for fields in roles.values()
        for field in fields
        if isinstance(field, tuple)
    })

    missing_tables = [table for table in required_tables if table not in tables]
    available_tables = [table for table in required_tables if table in tables]

    category_fields = (
        roles.get("Category", [])
        or roles.get("Axis", [])
        or roles.get("X", [])
        or roles.get("Rows", [])
        or roles.get("Group", [])
    )
    value_fields = (
        roles.get("Values", [])
        or roles.get("Y", [])
        or roles.get("Measure", [])
        or roles.get("Columns", [])
        or roles.get("Value", [])
    )

    dimension = field_name_only(category_fields[0]) if category_fields else None
    measure_names = [field_name_only(field) for field in value_fields[:6]]
    source_fields = [row.get("field") for row in visual.get("field_rows", [])]

    if missing_tables:
        status = "missing_embedded_tables"
        note = (
            "Visual definition extracted, but the PBIX does not contain the required "
            f"embedded table(s): {', '.join(missing_tables)}."
        )
    elif required_tables:
        status = "unsupported_visual_shape"
        note = (
            "The required table exists in the PBIX, but this visual shape could not yet "
            "be reconstructed into chart data."
        )
    else:
        status = "no_field_mapping"
        note = "The visual layout was extracted, but no field mapping could be inferred."

    return {
        "report": report_name,
        "page": visual.get("page_name", "Unknown Page"),
        "title": visual.get("title", visual.get("visual_type", "Visual")),
        "chart_type": visual.get("visual_type", "unknown"),
        "visual_type": visual.get("visual_type", "unknown"),
        "visual_category": visual.get("visual_category", "other"),
        "dimension": dimension,
        "measure_used": measure_names[0] if measure_names else None,
        "measures": measure_names,
        "field_rows": visual.get("field_rows", []),
        "source_fields": source_fields,
        "required_tables": required_tables,
        "available_tables": available_tables,
        "missing_tables": missing_tables,
        "data_available": False,
        "data_status": status,
        "data_note": note,
    }
