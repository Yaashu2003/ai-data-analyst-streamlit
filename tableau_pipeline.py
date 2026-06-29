import os
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


TABLEAU_EXTENSIONS = {".twb", ".twbx"}
DATA_EXTENSIONS = {".csv", ".xlsx", ".xls", ".txt"}
AGGREGATION_MAP = {
    "sum": "sum",
    "avg": "mean",
    "average": "mean",
    "mean": "mean",
    "min": "min",
    "max": "max",
    "cnt": "count",
    "count": "count",
    "ctd": "nunique",
    "countd": "nunique",
}


def _strip_namespace(tag):
    return str(tag).split("}", 1)[-1]


def _normalize_name(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean_field_token(token):
    token = str(token or "").strip().strip("[]")
    if ":" in token:
        parts = token.split(":")
        if len(parts) >= 2:
            token = parts[1]
    return token.strip("[]")


def _field_candidates_from_ref(value):
    text = str(value or "")
    bracket_tokens = re.findall(r"\[([^\]]+)\]", text)
    candidates = []
    for token in reversed(bracket_tokens):
        cleaned = _clean_field_token(token)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    if not candidates and text:
        cleaned = _clean_field_token(text)
        if cleaned:
            candidates.append(cleaned)
    return candidates


def _aggregation_from_ref(value):
    text = str(value or "").lower()
    bracket_tokens = re.findall(r"\[([^\]]+)\]", text)
    for token in bracket_tokens:
        if ":" not in token:
            continue
        prefix = token.split(":", 1)[0].lower()
        if prefix in AGGREGATION_MAP:
            return AGGREGATION_MAP[prefix]
    return None


def _safe_read_csv(path):
    for encoding in ("utf-8", "utf-8-sig", "latin1"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except Exception:
            continue
    return None


def _load_table_file(path):
    path = Path(path)
    tables = {}
    try:
        if path.suffix.lower() in {".csv", ".txt"}:
            dataframe = _safe_read_csv(path)
            if dataframe is not None:
                tables[path.stem] = dataframe
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            workbook = pd.ExcelFile(path)
            for sheet_name in workbook.sheet_names:
                tables[sheet_name] = pd.read_excel(path, sheet_name=sheet_name)
            if len(workbook.sheet_names) == 1:
                tables[path.stem] = tables[workbook.sheet_names[0]]
    except Exception as error:
        print(f"Tableau packaged table read error for {path}: {error}")
    return tables


def _load_hyper_file(path):
    try:
        from tableauhyperapi import Connection, HyperProcess, Telemetry
    except Exception:
        return {}

    tables = {}
    try:
        with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
            with Connection(endpoint=hyper.endpoint, database=str(path)) as connection:
                for schema_name in connection.catalog.get_schema_names():
                    for table_name in connection.catalog.get_table_names(schema=schema_name):
                        definition = connection.catalog.get_table_definition(table_name)
                        columns = [column.name.unescaped for column in definition.columns]
                        rows = connection.execute_list_query(f"SELECT * FROM {table_name}")
                        dataframe = pd.DataFrame(rows, columns=columns)
                        table_key = table_name.name.unescaped
                        tables[table_key] = dataframe
    except Exception as error:
        print(f"Tableau Hyper extract read error for {path}: {error}")
    return tables


def _extract_workbook_file(report_folder, workbook_path):
    workbook_path = Path(workbook_path)
    if workbook_path.suffix.lower() == ".twb":
        return workbook_path, workbook_path.parent

    extract_folder = Path(report_folder) / f"{workbook_path.stem}_tableau_extracted"
    if extract_folder.exists():
        shutil.rmtree(extract_folder)
    extract_folder.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(workbook_path, "r") as zip_ref:
            zip_ref.extractall(extract_folder)
    except zipfile.BadZipFile:
        print(f"[ERROR] {workbook_path.name} is not a valid TWBX/ZIP")
        return None, extract_folder

    twb_files = sorted(extract_folder.rglob("*.twb"), key=lambda item: item.stat().st_size, reverse=True)
    if not twb_files:
        print(f"[WARN] Tableau workbook XML not found for {workbook_path.name}")
        return None, extract_folder
    return twb_files[0], extract_folder


def _parse_column_map(root):
    column_map = {}
    column_meta = {}

    for node in root.iter():
        if _strip_namespace(node.tag) != "column":
            continue
        raw_name = node.attrib.get("name") or node.attrib.get("caption") or ""
        caption = node.attrib.get("caption") or raw_name
        raw_candidates = _field_candidates_from_ref(raw_name) or [raw_name.strip("[]")]
        caption_clean = str(caption).strip("[]")

        for candidate in raw_candidates + [caption_clean]:
            if not candidate:
                continue
            column_map[_normalize_name(candidate)] = caption_clean
            column_meta[_normalize_name(caption_clean)] = {
                "datatype": node.attrib.get("datatype", ""),
                "role": node.attrib.get("role", ""),
                "type": node.attrib.get("type", ""),
            }

    return column_map, column_meta


def _resolve_field_name(raw_value, column_map):
    for candidate in _field_candidates_from_ref(raw_value):
        mapped = column_map.get(_normalize_name(candidate))
        if mapped:
            return mapped
        if candidate:
            return candidate
    return None


def _fields_from_shelf_text(text, column_map):
    fields = []
    for token in re.findall(r"\[[^\]]+\](?:\.\[[^\]]+\])*", str(text or "")):
        field = _resolve_field_name(token, column_map)
        if field and field not in fields:
            fields.append(field)
    return fields


def _parse_dashboards(root):
    worksheet_pages = {}
    for dashboard in root.iter():
        if _strip_namespace(dashboard.tag) != "dashboard":
            continue
        dashboard_name = dashboard.attrib.get("name") or "Dashboard"
        for zone in dashboard.iter():
            if _strip_namespace(zone.tag) != "zone":
                continue
            worksheet_name = zone.attrib.get("name") or zone.attrib.get("worksheet")
            if worksheet_name:
                worksheet_pages.setdefault(worksheet_name, []).append(dashboard_name)
    return worksheet_pages


def _parse_connections(root, workbook_dir):
    paths = []
    for node in root.iter():
        if _strip_namespace(node.tag) != "connection":
            continue
        for attr in ("filename", "dbname", "server"):
            value = node.attrib.get(attr)
            if not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = Path(workbook_dir) / value
            if candidate.exists() and candidate.suffix.lower() in DATA_EXTENSIONS | {".hyper"}:
                paths.append(candidate)
    return paths


def _parse_worksheets(root, column_map, worksheet_pages):
    worksheets = []

    for node in root.iter():
        if _strip_namespace(node.tag) != "worksheet":
            continue
        name = node.attrib.get("name") or "Worksheet"
        row_fields = []
        col_fields = []
        encoding_fields = {}
        field_aggs = {}
        mark_type = ""

        for child in node.iter():
            tag = _strip_namespace(child.tag)
            if tag in {"rows", "cols"}:
                fields = _fields_from_shelf_text(" ".join(child.itertext()), column_map)
                if tag == "rows":
                    row_fields.extend([field for field in fields if field not in row_fields])
                else:
                    col_fields.extend([field for field in fields if field not in col_fields])
                for match in re.findall(r"\[[^\]]+\](?:\.\[[^\]]+\])*", " ".join(child.itertext())):
                    field = _resolve_field_name(match, column_map)
                    aggregation = _aggregation_from_ref(match)
                    if field and aggregation:
                        field_aggs[field] = aggregation
            elif tag == "mark":
                mark_type = child.attrib.get("class", mark_type)
            elif tag in {"color", "size", "text", "shape", "lod-detail", "tooltip"}:
                field = _resolve_field_name(child.attrib.get("column"), column_map)
                if field:
                    encoding_fields.setdefault(tag, []).append(field)
                    aggregation = _aggregation_from_ref(child.attrib.get("column"))
                    if aggregation:
                        field_aggs[field] = aggregation

        worksheets.append({
            "name": name,
            "page": ", ".join(worksheet_pages.get(name, [])) or "Worksheet",
            "rows": row_fields,
            "cols": col_fields,
            "encodings": encoding_fields,
            "field_aggs": field_aggs,
            "mark_type": mark_type,
        })

    return worksheets


def _load_packaged_tables(extract_dir, connection_paths):
    tables = {}
    source_paths = []

    candidate_paths = []
    for path in Path(extract_dir).rglob("*"):
        if path.suffix.lower() in DATA_EXTENSIONS | {".hyper"}:
            candidate_paths.append(path)
    candidate_paths.extend(connection_paths)

    seen = set()
    for path in candidate_paths:
        path = Path(path)
        key = str(path.resolve()).lower() if path.exists() else str(path).lower()
        if key in seen:
            continue
        seen.add(key)

        loaded = _load_hyper_file(path) if path.suffix.lower() == ".hyper" else _load_table_file(path)
        if not loaded:
            continue
        source_paths.append(str(path))
        for name, dataframe in loaded.items():
            tables[name] = dataframe
            tables[path.stem] = dataframe

    return tables, source_paths


def _numeric_series(series):
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


def _resolve_dataframe_column(df, field):
    normalized_target = _normalize_name(field)
    for column in df.columns:
        if str(column) == str(field) or _normalize_name(column) == normalized_target:
            return column
    return None


def _find_table_for_fields(tables, fields):
    usable_fields = [field for field in fields if field]
    if not usable_fields and len(tables) == 1:
        name, dataframe = next(iter(tables.items()))
        return name, dataframe, {}

    best = None
    best_score = -1
    best_mapping = {}
    for table_name, dataframe in tables.items():
        if not isinstance(dataframe, pd.DataFrame) or dataframe.empty:
            continue
        mapping = {}
        score = 0
        for field in usable_fields:
            column = _resolve_dataframe_column(dataframe, field)
            if column is not None:
                mapping[field] = column
                score += 1
        if score > best_score:
            best = (table_name, dataframe)
            best_score = score
            best_mapping = mapping

    if best and (best_score > 0 or len(tables) == 1):
        return best[0], best[1], best_mapping
    return None, None, {}


def _classify_field(df, column, column_meta=None):
    column_meta = column_meta or {}
    meta = column_meta.get(_normalize_name(column), {})
    datatype = str(meta.get("datatype", "")).lower()
    role = str(meta.get("role", "")).lower()
    if role == "measure" or datatype in {"integer", "real", "float", "double"}:
        return "measure"
    if _numeric_series(df[column]) is not None:
        return "measure"
    return "dimension"


def _chart_type_for_worksheet(worksheet, df, dimension_col, measure_col, scatter_candidate=False):
    mark = str(worksheet.get("mark_type", "")).lower()
    if scatter_candidate:
        return "scatterChart"
    if dimension_col is not None and _datetime_series(df[dimension_col]) is not None:
        return "lineChart"
    if "line" in mark:
        return "lineChart"
    if "area" in mark:
        return "areaChart"
    if "pie" in mark:
        return "donutChart"
    if "square" in mark:
        return "treemap"
    return "clusteredBarChart" if dimension_col is not None else "cardVisual"


def _aggregate_dataframe(df, dimension_col, measure_col, aggregation):
    working = df.copy()
    if dimension_col is not None:
        working = working.dropna(subset=[dimension_col])

    if measure_col is None:
        grouped = (
            working.groupby(dimension_col, as_index=False)
            .size()
            .rename(columns={"size": "__metric__"})
        )
        return grouped

    if aggregation == "count":
        grouped = (
            working.dropna(subset=[dimension_col, measure_col])
            .groupby(dimension_col, as_index=False)[measure_col]
            .count()
            .rename(columns={measure_col: "__metric__"})
        )
        return grouped

    if aggregation == "nunique":
        grouped = (
            working.dropna(subset=[dimension_col, measure_col])
            .groupby(dimension_col, as_index=False)[measure_col]
            .nunique()
            .rename(columns={measure_col: "__metric__"})
        )
        return grouped

    numeric = _numeric_series(working[measure_col])
    if numeric is None:
        return None
    working["__metric_numeric__"] = numeric
    grouped = (
        working.dropna(subset=[dimension_col, "__metric_numeric__"])
        .groupby(dimension_col, as_index=False)["__metric_numeric__"]
        .agg(aggregation or "sum")
        .rename(columns={"__metric_numeric__": "__metric__"})
    )
    return grouped


def _build_chart_from_worksheet(report_name, worksheet, tables, column_meta):
    all_fields = []
    for field in worksheet.get("rows", []) + worksheet.get("cols", []):
        if field not in all_fields:
            all_fields.append(field)
    for fields in worksheet.get("encodings", {}).values():
        for field in fields:
            if field not in all_fields:
                all_fields.append(field)

    table_name, df, mapping = _find_table_for_fields(tables, all_fields)
    if df is None:
        return None

    df = df.loc[:, ~df.columns.duplicated()].copy()
    resolved_fields = [mapping.get(field) or _resolve_dataframe_column(df, field) for field in all_fields]
    resolved_fields = [field for field in resolved_fields if field is not None]
    measure_fields = [field for field in resolved_fields if _classify_field(df, field, column_meta) == "measure"]
    dimension_fields = [field for field in resolved_fields if field not in measure_fields]

    encoded_series = []
    for role in ("color", "shape", "lod-detail"):
        for raw_field in worksheet.get("encodings", {}).get(role, []):
            col = mapping.get(raw_field) or _resolve_dataframe_column(df, raw_field)
            if col is not None and col not in measure_fields and col not in encoded_series:
                encoded_series.append(col)

    if len(measure_fields) >= 2 and (worksheet.get("rows") and worksheet.get("cols")):
        x_col, y_col = measure_fields[:2]
        x_numeric = _numeric_series(df[x_col])
        y_numeric = _numeric_series(df[y_col])
        if x_numeric is not None and y_numeric is not None:
            working = pd.DataFrame({"__x__": x_numeric, "__y__": y_numeric})
            chart = {
                "report": report_name,
                "dashboard_platform": "Tableau",
                "chart_type": "scatterChart",
                "visual_type": "scatterChart",
                "visual_category": "chart",
                "page": worksheet.get("page", "Worksheet"),
                "title": worksheet.get("name", "Tableau scatter"),
                "dimension": str(x_col),
                "source_table": str(table_name),
                "measure_used": str(y_col),
                "data_available": True,
                "data_status": "tableau_packaged_data",
                "data_note": "Reconstructed from Tableau workbook visual roles and packaged/local source data.",
            }
            series_col = encoded_series[0] if encoded_series else None
            if series_col is not None and series_col in df.columns:
                working["__series__"] = df[series_col]
                chart["series"] = []
                for series_name, subset in working.dropna().groupby("__series__"):
                    chart["series"].append({
                        "name": str(series_name),
                        "x": subset["__x__"].astype(float).tolist(),
                        "y": subset["__y__"].astype(float).tolist(),
                    })
                chart["x"] = []
            else:
                working = working.dropna()
                chart["x"] = working["__x__"].astype(float).tolist()
                chart["y"] = working["__y__"].astype(float).tolist()
            return chart

    dimension_col = dimension_fields[0] if dimension_fields else None
    measure_col = measure_fields[0] if measure_fields else None
    if dimension_col is None and measure_col is None:
        return None

    aggregation = worksheet.get("field_aggs", {}).get(str(measure_col), "sum")

    if dimension_col is None and measure_col is not None:
        numeric = _numeric_series(df[measure_col])
        if numeric is None:
            return None
        value = float(getattr(numeric.dropna(), aggregation if aggregation in {"sum", "mean", "min", "max"} else "sum")())
        return {
            "report": report_name,
            "dashboard_platform": "Tableau",
            "chart_type": "cardVisual",
            "visual_type": "cardVisual",
            "visual_category": "chart",
            "page": worksheet.get("page", "Worksheet"),
            "title": worksheet.get("name", str(measure_col)),
            "dimension": str(measure_col),
            "source_table": str(table_name),
            "x": [str(measure_col)],
            "y": [value],
            "measure_used": str(measure_col),
            "card_value": value,
            "data_available": True,
            "data_status": "tableau_packaged_data",
            "data_note": "Reconstructed from Tableau workbook visual roles and packaged/local source data.",
        }

    chart_type = _chart_type_for_worksheet(worksheet, df, dimension_col, measure_col)
    series_col = encoded_series[0] if encoded_series and encoded_series[0] != dimension_col else None

    if series_col is not None:
        aggregation = aggregation or "sum"
        grouped_parts = []
        for series_name, subset in df.groupby(series_col):
            grouped = _aggregate_dataframe(subset, dimension_col, measure_col, aggregation)
            if grouped is None or grouped.empty:
                continue
            grouped["__series__"] = str(series_name)
            grouped_parts.append(grouped)
        if grouped_parts:
            grouped_all = pd.concat(grouped_parts, ignore_index=True)
            x_values = sorted(grouped_all[dimension_col].astype(str).unique())
            chart = {
                "report": report_name,
                "dashboard_platform": "Tableau",
                "chart_type": chart_type,
                "visual_type": chart_type,
                "visual_category": "chart",
                "page": worksheet.get("page", "Worksheet"),
                "title": worksheet.get("name", "Tableau visual"),
                "dimension": str(dimension_col),
                "source_table": str(table_name),
                "x": x_values,
                "series": [],
                "measure_used": str(measure_col or "record_count"),
                "data_available": True,
                "data_status": "tableau_packaged_data",
                "data_note": "Reconstructed from Tableau workbook visual roles and packaged/local source data.",
            }
            for series_name, subset in grouped_all.groupby("__series__"):
                mapping_y = dict(zip(subset[dimension_col].astype(str), subset["__metric__"]))
                chart["series"].append({
                    "name": str(series_name),
                    "y": [float(mapping_y.get(x, 0)) for x in x_values],
                })
            return chart

    grouped = _aggregate_dataframe(df, dimension_col, measure_col, aggregation or "sum")
    if grouped is None or grouped.empty:
        return None

    return {
        "report": report_name,
        "dashboard_platform": "Tableau",
        "chart_type": chart_type,
        "visual_type": chart_type,
        "visual_category": "chart",
        "page": worksheet.get("page", "Worksheet"),
        "title": worksheet.get("name", "Tableau visual"),
        "dimension": str(dimension_col),
        "source_table": str(table_name),
        "x": grouped[dimension_col].astype(str).tolist(),
        "y": grouped["__metric__"].astype(float).tolist(),
        "measure_used": str(measure_col or "record_count"),
        "data_available": True,
        "data_status": "tableau_packaged_data",
        "data_note": "Reconstructed from Tableau workbook visual roles and packaged/local source data.",
    }


def _build_metadata_descriptor(report_name, worksheet, source_paths):
    fields = []
    for field in worksheet.get("rows", []) + worksheet.get("cols", []):
        if field not in fields:
            fields.append(field)
    for role_fields in worksheet.get("encodings", {}).values():
        for field in role_fields:
            if field not in fields:
                fields.append(field)

    return {
        "report": report_name,
        "dashboard_platform": "Tableau",
        "chart_type": "tableauWorksheet",
        "visual_type": "tableauWorksheet",
        "visual_category": "chart",
        "page": worksheet.get("page", "Worksheet"),
        "title": worksheet.get("name", "Tableau worksheet"),
        "dimension": fields[0] if fields else None,
        "measure_used": fields[1] if len(fields) > 1 else None,
        "data_available": False,
        "data_status": "metadata_only",
        "source_fields": fields,
        "missing_tables": [] if source_paths else ["Tableau packaged/local source data"],
        "note": (
            "Tableau worksheet metadata was extracted, but no packaged/local source table "
            "was available to reconstruct numeric chart values."
        ),
    }


def process_tableau_reports(report_folder):
    report_folder = Path(report_folder)
    charts = []
    metadata = []
    reports = []
    total_visuals_seen = 0

    for workbook_file in sorted(report_folder.iterdir()):
        if workbook_file.suffix.lower() not in TABLEAU_EXTENSIONS:
            continue

        print(f"[Processing Tableau] {workbook_file.name}")
        workbook_xml, extract_dir = _extract_workbook_file(report_folder, workbook_file)
        if workbook_xml is None:
            continue

        try:
            root = ET.parse(workbook_xml).getroot()
        except Exception as error:
            print(f"Tableau workbook parse error for {workbook_file.name}: {error}")
            continue

        report_name = workbook_file.stem
        column_map, column_meta = _parse_column_map(root)
        worksheet_pages = _parse_dashboards(root)
        worksheets = _parse_worksheets(root, column_map, worksheet_pages)
        connection_paths = _parse_connections(root, workbook_xml.parent)
        tables, source_paths = _load_packaged_tables(extract_dir, connection_paths)
        total_visuals_seen += len(worksheets)

        report_charts = []
        report_metadata = []
        for worksheet in worksheets:
            chart = _build_chart_from_worksheet(report_name, worksheet, tables, column_meta)
            if chart:
                report_charts.append(chart)
            else:
                report_metadata.append(_build_metadata_descriptor(report_name, worksheet, source_paths))

        charts.extend(report_charts)
        metadata.extend(report_metadata)
        reports.append({
            "report": report_name,
            "platform": "Tableau",
            "embedded_tables_available": sorted(tables.keys()),
            "tables_available": sorted(tables.keys()),
            "embedded_table_count": len(tables),
            "chart_data_count": len(report_charts),
            "metadata_visual_count": len(report_metadata),
            "missing_tables": sorted({
                missing
                for descriptor in report_metadata
                for missing in descriptor.get("missing_tables", [])
            }),
            "fallback_aliases": {},
            "source_files": source_paths,
        })

    return {
        "charts": charts,
        "metadata": metadata,
        "reports": reports,
        "visuals_seen": total_visuals_seen,
    }
