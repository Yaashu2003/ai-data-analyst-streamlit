import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import rag_pipeline
from pbix_report_generator import _build_local_pbix_report, generate_pbix_report
from report_html import generate_html


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "fastapi_runtime"
RUNTIME_DIR.mkdir(exist_ok=True)
REPORT_HTML_VERSION_MARKER = "AI_DATA_ANALYST_REPORT_HTML_VERSION=lazy_chart_iframe_v2"

DASHBOARD_EXTENSIONS = {".pbix", ".twb", ".twbx"}
TABLE_EXTENSIONS = {".csv", ".xlsx", ".xls"}

router = APIRouter(prefix="/api", tags=["BI Analysis"])
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=2)


class ChatRequest(BaseModel):
    query: str


class FeedbackItem(BaseModel):
    text: str
    feedback: str


class FeedbackRequest(BaseModel):
    edited_text: Optional[str] = None
    general_feedback: str = ""
    sentence_feedbacks: List[FeedbackItem] = []


def _job_dir(job_id: str) -> Path:
    return RUNTIME_DIR / job_id


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def _update_job(job_id: str, **updates: Any) -> Dict[str, Any]:
    with _jobs_lock:
        job = _jobs.setdefault(job_id, {})
        job.update(_jsonable(updates))
        job["updated_at"] = time.time()
        return dict(job)


def _get_job(job_id: str) -> Dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(job)


def _persist_job_payload(job_id: str, payload: Dict[str, Any]) -> None:
    path = _job_dir(job_id) / "job_payload.json"
    path.write_text(json.dumps(_jsonable(payload), indent=2, default=str), encoding="utf-8")


def _generate_bi_report_text(charts: List[Dict[str, Any]]) -> str:
    if os.getenv("FASTAPI_USE_GEMINI_REPORTS", "").strip().lower() in {"1", "true", "yes"}:
        return generate_pbix_report(charts)
    return _build_local_pbix_report(charts)


def _generate_report_html(job_id: str, charts: List[Dict[str, Any]], report_text: str) -> str:
    html_path = _job_dir(job_id) / "interactive_analysis_report.html"
    try:
        generate_html(charts_data=charts, report_text=report_text, output_path=html_path)
    except Exception as error:
        print(f"Streamlit-style report HTML generation failed for job {job_id}: {error}")
        escaped_report = escape(report_text or "")
        fallback = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <title>Analysis Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; background: #f6f8fb; color: #172033; margin: 0; }}
                main {{ max-width: 1120px; margin: 24px auto; background: #fff; border-radius: 14px; padding: 28px; box-shadow: 0 18px 50px rgba(15,23,42,.10); }}
                pre {{ white-space: pre-wrap; line-height: 1.55; font-family: inherit; }}
            </style>
        </head>
        <body><main><h1>Analysis Report</h1><pre>{escaped_report}</pre></main></body>
        </html>
        """.strip()
        html_path.write_text(fallback, encoding="utf-8")
    return str(html_path)


def _read_table_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for encoding in ("utf-8", "utf-8-sig", "latin1"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except Exception:
                continue
        return pd.read_csv(path)

    if suffix in {".xlsx", ".xls"}:
        workbook = pd.ExcelFile(path)
        return pd.read_excel(path, sheet_name=workbook.sheet_names[0])

    raise ValueError(f"Unsupported dataset file: {path.name}")


def _numeric_series(series: pd.Series) -> Optional[pd.Series]:
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


def _datetime_series(series: pd.Series) -> Optional[pd.Series]:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    if parsed.notna().sum() < 3:
        return None
    return parsed


def _dataset_chart(report: str, title: str, chart_type: str, dimension: str, measure: str, x, y=None, series=None):
    chart = {
        "report": report,
        "dashboard_platform": "Dataset",
        "chart_type": chart_type,
        "visual_type": chart_type,
        "visual_category": "chart",
        "page": "Dataset Analysis",
        "title": title,
        "dimension": dimension,
        "measure_used": measure,
        "source_table": report,
        "x": list(x),
        "data_available": True,
        "data_status": "dataset_generated",
        "data_note": "Generated by the FastAPI dataset analysis path.",
    }
    if series is not None:
        chart["series"] = series
    else:
        chart["y"] = [float(value) for value in (y or [])]
    return chart


def _build_dataset_outputs(dataset_path: Path) -> Dict[str, Any]:
    df = _read_table_file(dataset_path)
    report_name = dataset_path.stem
    rows, cols = df.shape

    numeric_columns = []
    datetime_columns = []
    categorical_columns = []
    for column in df.columns:
        if _numeric_series(df[column]) is not None:
            numeric_columns.append(column)
        elif _datetime_series(df[column]) is not None:
            datetime_columns.append(column)
        else:
            nunique = df[column].nunique(dropna=True)
            if 1 < nunique <= max(50, len(df) // 2):
                categorical_columns.append(column)

    charts = []
    if categorical_columns:
        category = categorical_columns[0]
        grouped = df[category].dropna().astype(str).value_counts().head(15)
        charts.append(_dataset_chart(
            report_name,
            f"Top {category} by record count",
            "clusteredBarChart",
            str(category),
            "record_count",
            grouped.index.tolist(),
            grouped.values.tolist(),
        ))

    if categorical_columns and numeric_columns:
        category = categorical_columns[0]
        measure = numeric_columns[0]
        numeric = _numeric_series(df[measure])
        working = pd.DataFrame({"category": df[category], "measure": numeric}).dropna()
        if not working.empty:
            grouped = working.groupby("category", as_index=False)["measure"].mean()
            grouped = grouped.sort_values("measure", ascending=False).head(15)
            charts.append(_dataset_chart(
                report_name,
                f"Average {measure} by {category}",
                "clusteredColumnChart",
                str(category),
                f"avg_{measure}",
                grouped["category"].astype(str).tolist(),
                grouped["measure"].tolist(),
            ))

    if datetime_columns and numeric_columns:
        date_col = datetime_columns[0]
        measure = numeric_columns[0]
        parsed = _datetime_series(df[date_col])
        numeric = _numeric_series(df[measure])
        working = pd.DataFrame({"date": parsed, "measure": numeric}).dropna()
        if not working.empty:
            grouped = (
                working.assign(bucket=working["date"].dt.date.astype(str))
                .groupby("bucket", as_index=False)["measure"]
                .mean()
                .sort_values("bucket")
                .tail(24)
            )
            charts.append(_dataset_chart(
                report_name,
                f"{measure} trend",
                "lineChart",
                str(date_col),
                f"avg_{measure}",
                grouped["bucket"].tolist(),
                grouped["measure"].tolist(),
            ))

    if len(numeric_columns) >= 2:
        x_col, y_col = numeric_columns[:2]
        x_values = _numeric_series(df[x_col])
        y_values = _numeric_series(df[y_col])
        working = pd.DataFrame({"x": x_values, "y": y_values}).dropna().head(800)
        if not working.empty:
            charts.append(_dataset_chart(
                report_name,
                f"{y_col} vs {x_col}",
                "scatterChart",
                str(x_col),
                str(y_col),
                working["x"].tolist(),
                working["y"].tolist(),
            ))

    report_lines = [
        "# Dataset Analysis Report",
        "",
        f"Dataset `{dataset_path.name}` contains {rows:,} rows and {cols:,} columns.",
        f"Detected {len(numeric_columns)} numeric column(s), {len(categorical_columns)} categorical column(s), and {len(datetime_columns)} date/time column(s).",
        "",
        "## Generated Charts",
    ]
    for chart in charts:
        y_values = chart.get("y", [])
        if y_values:
            report_lines.append(
                f"- {chart['title']}: highest value is {max(y_values):,.2f}, lowest value is {min(y_values):,.2f}."
            )
        else:
            report_lines.append(f"- {chart['title']}: generated for visual exploration.")

    if not charts:
        report_lines.append("- No robust chart could be generated from the uploaded dataset.")

    report_lines.extend([
        "",
        "## Recommendations",
        "- Use the chart workspace to ask follow-up questions about rankings, trends, outliers, and segment performance.",
        "- Validate any business-critical finding against the source data before acting on it.",
    ])

    return {
        "mode": "dataset",
        "charts": charts,
        "summary": {
            "reports_processed": 1,
            "chart_data_count": len(charts),
            "metadata_visual_count": 0,
            "visuals_seen": len(charts),
            "pbix_report_count": 0,
            "tableau_report_count": 0,
            "dataset_report_count": 1,
            "dataset_rows": rows,
            "dataset_columns": cols,
            "reports": [{
                "report": report_name,
                "platform": "Dataset",
                "chart_data_count": len(charts),
                "metadata_visual_count": 0,
            }],
        },
        "report_text": "\n".join(report_lines),
    }


def _merge_dataset_outputs(dataset_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    charts = []
    report_sections = []
    summary = {
        "reports_processed": 0,
        "chart_data_count": 0,
        "metadata_visual_count": 0,
        "visuals_seen": 0,
        "pbix_report_count": 0,
        "tableau_report_count": 0,
        "dataset_report_count": 0,
        "dataset_rows": 0,
        "dataset_columns": 0,
        "reports": [],
    }

    for output in dataset_outputs:
        charts.extend(output.get("charts", []))
        if output.get("report_text"):
            report_sections.append(output["report_text"])
        item_summary = output.get("summary", {})
        summary["reports_processed"] += int(item_summary.get("reports_processed", 0) or 0)
        summary["chart_data_count"] += int(item_summary.get("chart_data_count", 0) or 0)
        summary["metadata_visual_count"] += int(item_summary.get("metadata_visual_count", 0) or 0)
        summary["visuals_seen"] += int(item_summary.get("visuals_seen", 0) or 0)
        summary["dataset_report_count"] += int(item_summary.get("dataset_report_count", 0) or 0)
        summary["dataset_rows"] += int(item_summary.get("dataset_rows", 0) or 0)
        summary["dataset_columns"] += int(item_summary.get("dataset_columns", 0) or 0)
        summary["reports"].extend(item_summary.get("reports", []))

    return {
        "mode": "dataset",
        "charts": charts,
        "summary": summary,
        "report_text": "\n\n".join(report_sections),
    }


def _merge_summary(base_summary: Dict[str, Any], extra_summary: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base_summary or {})
    for key in (
        "reports_processed",
        "chart_data_count",
        "metadata_visual_count",
        "total_items_returned",
        "visuals_seen",
        "missing_table_visuals",
        "pbix_report_count",
        "tableau_report_count",
        "dataset_report_count",
        "dataset_rows",
        "dataset_columns",
    ):
        merged[key] = int(merged.get(key, 0) or 0) + int(extra_summary.get(key, 0) or 0)

    merged["reports"] = list(merged.get("reports", [])) + list(extra_summary.get("reports", []))
    return merged


def _run_job(job_id: str) -> None:
    job = _get_job(job_id)
    upload_dir = Path(job["upload_dir"])
    dashboard_files = [Path(path) for path in job.get("dashboard_files", [])]
    table_files = [Path(path) for path in job.get("table_files", [])]

    try:
        _update_job(job_id, status="processing", progress=10, message="Files saved. Starting analysis.")
        if dashboard_files:
            _update_job(job_id, progress=35, message="Extracting dashboard visuals and packaged data.")
            charts = rag_pipeline.process_all_reports(str(upload_dir), external_data_files=[str(path) for path in table_files])
            summary = rag_pipeline.get_last_extraction_summary()
            if table_files:
                _update_job(job_id, progress=58, message="Adding uploaded dataset analysis to the same workspace.")
                dataset_outputs = []
                for table_path in table_files:
                    try:
                        dataset_outputs.append(_build_dataset_outputs(table_path))
                    except Exception as dataset_error:
                        print(f"Dataset side-analysis failed for {table_path}: {dataset_error}")
                if dataset_outputs:
                    dataset_bundle = _merge_dataset_outputs(dataset_outputs)
                    charts = list(charts) + list(dataset_bundle.get("charts", []))
                    summary = _merge_summary(summary, dataset_bundle.get("summary", {}))
            _update_job(job_id, progress=75, message="Generating unified report.")
            report_text = _generate_bi_report_text(charts)
            mode = "mixed" if table_files else "bi"
        elif table_files:
            _update_job(job_id, progress=35, message="Analyzing dataset.")
            outputs = _merge_dataset_outputs([_build_dataset_outputs(path) for path in table_files])
            charts = outputs["charts"]
            summary = outputs["summary"]
            report_text = outputs["report_text"]
            mode = "dataset"
        else:
            raise ValueError("Upload at least one dashboard or dataset file.")

        _update_job(job_id, progress=88, message="Building Streamlit-style HTML report.")
        report_html_path = _generate_report_html(job_id, charts, report_text or "")
        payload = {
            "job_id": job_id,
            "mode": mode,
            "charts": _jsonable(charts),
            "summary": _jsonable(summary),
            "report_text": report_text or "",
            "report_html_path": report_html_path,
        }
        _persist_job_payload(job_id, payload)
        _update_job(
            job_id,
            status="complete",
            progress=100,
            message="Analysis complete.",
            mode=mode,
            charts=payload["charts"],
            summary=payload["summary"],
            report_text=payload["report_text"],
            report_html_path=payload["report_html_path"],
        )
    except Exception as error:
        _update_job(job_id, status="failed", progress=100, message=str(error), error=str(error))


@router.post("/jobs")
async def create_job(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    job_id = uuid.uuid4().hex
    job_root = _job_dir(job_id)
    upload_dir = job_root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    dashboard_files = []
    table_files = []
    filenames = []

    for uploaded in files:
        filename = Path(uploaded.filename or "upload.bin").name
        suffix = Path(filename).suffix.lower()
        if suffix not in DASHBOARD_EXTENSIONS | TABLE_EXTENSIONS:
            continue
        target = upload_dir / filename
        with target.open("wb") as handle:
            shutil.copyfileobj(uploaded.file, handle)
        filenames.append(filename)
        if suffix in DASHBOARD_EXTENSIONS:
            dashboard_files.append(str(target))
        elif suffix in TABLE_EXTENSIONS:
            table_files.append(str(target))

    if not dashboard_files and not table_files:
        raise HTTPException(status_code=400, detail="Supported files: PBIX, TWB, TWBX, CSV, XLSX, XLS")

    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Queued for processing.",
        "created_at": time.time(),
        "updated_at": time.time(),
        "filenames": filenames,
        "upload_dir": str(upload_dir),
        "dashboard_files": dashboard_files,
        "table_files": table_files,
        "mode": "bi" if dashboard_files else "dataset",
        "charts": [],
        "summary": {},
        "report_text": "",
        "report_html_path": "",
    }
    with _jobs_lock:
        _jobs[job_id] = job

    background_tasks.add_task(_executor.submit, _run_job, job_id)
    return _jsonable(job)


@router.get("/jobs")
async def list_jobs():
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda item: item.get("created_at", 0), reverse=True)
        return [_jsonable({k: v for k, v in job.items() if k != "charts"}) for job in jobs[:25]]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = _get_job(job_id)
    return _jsonable({k: v for k, v in job.items() if k != "charts"})


@router.get("/jobs/{job_id}/charts")
async def get_job_charts(job_id: str):
    job = _get_job(job_id)
    return {"job_id": job_id, "charts": _jsonable(job.get("charts", []))}


@router.get("/jobs/{job_id}/report")
async def get_job_report(job_id: str):
    job = _get_job(job_id)
    return {
        "job_id": job_id,
        "report_text": job.get("report_text", ""),
        "report_html_url": f"/api/jobs/{job_id}/report-html",
        "summary": _jsonable(job.get("summary", {})),
    }


@router.get("/jobs/{job_id}/report-html", response_class=HTMLResponse)
async def get_job_report_html(job_id: str):
    job = _get_job(job_id)
    html_path = Path(job.get("report_html_path") or "")
    should_regenerate = not html_path.is_file()
    if html_path.is_file():
        try:
            existing_html = html_path.read_text(encoding="utf-8", errors="ignore")
            should_regenerate = REPORT_HTML_VERSION_MARKER not in existing_html
        except Exception:
            should_regenerate = True
    if should_regenerate:
        report_text = job.get("report_text", "")
        if not report_text:
            raise HTTPException(status_code=404, detail="Report HTML is not available yet")
        html_path = Path(_generate_report_html(job_id, job.get("charts", []), report_text))
        _update_job(job_id, report_html_path=str(html_path))
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@router.post("/jobs/{job_id}/chat")
async def chat_with_job(job_id: str, request: ChatRequest):
    job = _get_job(job_id)
    if job.get("status") != "complete":
        raise HTTPException(status_code=409, detail="Job is not complete yet")
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")
    result = rag_pipeline.ask_gemini_charts(query, job.get("charts", []))
    return {"job_id": job_id, "response": _jsonable(result)}


@router.post("/jobs/{job_id}/feedback")
async def regenerate_report_with_feedback(job_id: str, request: FeedbackRequest):
    job = _get_job(job_id)
    if job.get("status") != "complete":
        raise HTTPException(status_code=409, detail="Job is not complete yet")

    original = job.get("report_text", "")
    if not original:
        raise HTTPException(status_code=400, detail="No report is available for this job")

    sentence_feedbacks = [item.model_dump() for item in request.sentence_feedbacks]
    try:
        from report_viewer import generate_final_report_with_feedback

        final_report = generate_final_report_with_feedback(
            original_text=original,
            edited_text=request.edited_text or original,
            general_feedback=request.general_feedback or "",
            sentence_feedbacks=sentence_feedbacks,
        )
    except Exception as error:
        final_report = (request.edited_text or original).rstrip()
        if request.general_feedback or sentence_feedbacks:
            final_report += "\n\n## Feedback Notes\n"
            if request.general_feedback:
                final_report += f"- General feedback: {request.general_feedback}\n"
            for item in sentence_feedbacks:
                final_report += f"- {item.get('text', '')[:120]} -> {item.get('feedback', '')}\n"
            final_report += f"\nFallback reason: {error}"

    _update_job(job_id, report_text=final_report, message="Report regenerated with feedback.")
    report_html_path = _generate_report_html(job_id, job.get("charts", []), final_report)
    _update_job(job_id, report_html_path=report_html_path)
    payload = {
        "job_id": job_id,
        "mode": job.get("mode"),
        "charts": job.get("charts", []),
        "summary": job.get("summary", {}),
        "report_text": final_report,
        "report_html_path": report_html_path,
    }
    _persist_job_payload(job_id, payload)
    return {"job_id": job_id, "report_text": final_report}


@router.get("/health")
async def api_health():
    return {"status": "healthy", "runtime_dir": str(RUNTIME_DIR)}
