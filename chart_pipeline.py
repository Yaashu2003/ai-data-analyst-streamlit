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


def coerce_numeric_series(series):

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == 0:
        return None
    return numeric


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

    category_fields = (
        roles.get("Category", [])
        or roles.get("X", [])
        or roles.get("Rows", [])
        or roles.get("Group", [])
    )

    value_fields = (
        roles.get("Values", [])
        or roles.get("Y", [])
        or roles.get("Measure", [])
        or roles.get("Columns", [])
    )

    series_fields = (
        roles.get("Series", [])
        or roles.get("Legend", [])
    )

    if not category_fields:
        return charts

    resolved_category = resolve_table_field(category_fields[0], tables)
    if not resolved_category:
        return charts

    x_table, x_col = resolved_category

    if x_table not in tables:
        return charts

    df = tables[x_table].copy()

    df = df.loc[:, ~df.columns.duplicated()]

    if x_col not in df.columns:
        return charts

    df = ensure_series(df, x_col)

    measure_strategy = determine_measure_strategy(df, value_fields, query_refs)
    if not measure_strategy:
        return charts

    y_col = measure_strategy.get("field")
    if y_col and y_col in df.columns:
        df = ensure_series(df, y_col)

    try:

        # MULTI SERIES
        if series_fields:

            resolved_series = resolve_table_field(series_fields[0], {x_table: df})
            if resolved_series:
                _, s_col = resolved_series
            else:
                s_col = field_name_only(series_fields[0])

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
        or roles.get("X", [])
        or roles.get("Rows", [])
        or roles.get("Group", [])
    )
    value_fields = (
        roles.get("Values", [])
        or roles.get("Y", [])
        or roles.get("Measure", [])
        or roles.get("Columns", [])
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
