from pathlib import Path
from typing import Dict, Iterable, List, Optional
import re


_BASE_DIR = Path(__file__).resolve().parent
_DEFAULT_CHARTS_DIR = _BASE_DIR / "charts"
_DEFAULT_HTML_DIR = _BASE_DIR / "charts_html"

_AUTOVIZ_STEMS = {
    "dashboard",
    "category_subplots",
    "top_10_category_bar",
    "numeric_relationship_scatter",
    "market_composition_treemap",
}

_IGNORED_STEMS = {
    "kpi_distribution_violin",
    "outlier_detection_box",
    "data_quality_missing_values",
    "distribution_with_marginals",
    "predictive_forecast_trend",
    "proportion_donut",
}

_PRIORITY_TOKENS = [
    "cross_file_metric",
    "cross_file_category_mix",
    "cross_file_records",
    "dashboard",
    "correlation_network",
    "anomaly",
    "time_series",
    "bar_chart",
    "top_10",
    "category_subplots",
    "market_composition_treemap",
    "numeric_relationship_scatter",
    "scatter",
    "box_plot",
    "outlier",
    "violin",
    "distribution_with_marginals",
    "data_quality",
    "predictive_forecast",
]


def normalize_chart_stem(value: str) -> str:
    """Normalize a chart identifier down to its canonical stem."""
    return Path(str(value or "")).stem.strip()


def infer_chart_source(stem: str) -> str:
    normalized = normalize_chart_stem(stem).lower()
    return "autoviz" if normalized in _AUTOVIZ_STEMS else "intelligent"


def chart_source_priority(source: str) -> int:
    return 2 if source == "intelligent" else 1 if source == "autoviz" else 0


def chart_priority(stem: str) -> int:
    lowered = normalize_chart_stem(stem).lower()
    for idx, token in enumerate(_PRIORITY_TOKENS):
        if token in lowered:
            return len(_PRIORITY_TOKENS) - idx
    return 0


def _preferred_existing_path(path_a: Optional[str], path_b: Optional[str]) -> Optional[str]:
    if path_a and Path(path_a).exists():
        return path_a
    if path_b and Path(path_b).exists():
        return path_b
    return path_a or path_b


def _upsert_asset(
    asset_map: Dict[str, Dict[str, Optional[str]]],
    stem: str,
    source: str,
    png_path: Optional[str] = None,
    html_path: Optional[str] = None,
) -> None:
    normalized_stem = normalize_chart_stem(stem)
    lowered_stem = normalized_stem.lower()
    if (
        not normalized_stem
        or "failed" in lowered_stem
        or lowered_stem in _IGNORED_STEMS
    ):
        return

    existing = asset_map.get(normalized_stem)
    if not existing:
        asset_map[normalized_stem] = {
            "stem": normalized_stem,
            "source": source,
            "png_path": _preferred_existing_path(png_path, None),
            "html_path": _preferred_existing_path(html_path, None),
        }
        return

    if chart_source_priority(source) > chart_source_priority(existing.get("source", "")):
        existing["source"] = source

    if png_path:
        existing["png_path"] = _preferred_existing_path(existing.get("png_path"), png_path)
    if html_path:
        existing["html_path"] = _preferred_existing_path(existing.get("html_path"), html_path)


def collect_canonical_chart_assets(
    seed_paths: Optional[Iterable[str]] = None,
    charts_dir: Optional[Path] = None,
    html_dir: Optional[Path] = None,
) -> List[Dict[str, Optional[str]]]:
    """Collect one canonical chart asset per stem across raw state paths and chart folders."""
    charts_dir = Path(charts_dir or _DEFAULT_CHARTS_DIR)
    html_dir = Path(html_dir or _DEFAULT_HTML_DIR)

    asset_map: Dict[str, Dict[str, Optional[str]]] = {}

    def add_from_path(path_str: str) -> None:
        if not path_str:
            return

        candidate = Path(path_str)
        if candidate.exists():
            stem = normalize_chart_stem(candidate.name)
            source = infer_chart_source(stem)
            suffix = candidate.suffix.lower()
            if suffix == ".html":
                _upsert_asset(asset_map, stem, source, html_path=str(candidate.resolve()))
            elif suffix in {".png", ".jpg", ".jpeg"}:
                _upsert_asset(asset_map, stem, source, png_path=str(candidate.resolve()))
            else:
                _upsert_asset(asset_map, stem, source)
            return

        stem = normalize_chart_stem(path_str)
        source = infer_chart_source(stem)
        png_candidate = None
        for ext in (".png", ".jpg", ".jpeg"):
            possible = charts_dir / f"{stem}{ext}"
            if possible.exists():
                png_candidate = str(possible.resolve())
                break
        html_candidate = html_dir / f"{stem}.html"
        _upsert_asset(
            asset_map,
            stem,
            source,
            png_path=png_candidate,
            html_path=str(html_candidate.resolve()) if html_candidate.exists() else None,
        )

    for raw_path in seed_paths or []:
        add_from_path(raw_path)

    if charts_dir.is_dir():
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG", "*.JPEG"):
            for file_path in charts_dir.glob(ext):
                stem = normalize_chart_stem(file_path.name)
                _upsert_asset(
                    asset_map,
                    stem,
                    infer_chart_source(stem),
                    png_path=str(file_path.resolve()),
                )

    if html_dir.is_dir():
        for file_path in html_dir.glob("*.html"):
            stem = normalize_chart_stem(file_path.name)
            _upsert_asset(
                asset_map,
                stem,
                infer_chart_source(stem),
                html_path=str(file_path.resolve()),
            )

    assets = [
        asset
        for asset in asset_map.values()
        if chart_priority(asset["stem"]) >= 0 and (asset.get("png_path") or asset.get("html_path"))
    ]
    assets.sort(
        key=lambda asset: (
            chart_source_priority(asset.get("source", "")),
            chart_priority(asset["stem"]),
            asset["stem"].lower(),
        ),
        reverse=True,
    )
    return assets


def canonical_asset_paths(assets: List[Dict[str, Optional[str]]]) -> List[str]:
    """Return one representative path per canonical asset, preferring PNG when available."""
    representative_paths: List[str] = []
    for asset in assets:
        rep_path = asset.get("png_path") or asset.get("html_path")
        if rep_path:
            representative_paths.append(rep_path)
    return representative_paths


def canonical_asset_filenames(assets: List[Dict[str, Optional[str]]]) -> List[str]:
    """Return stable display filenames, using a PNG alias when only HTML exists."""
    filenames: List[str] = []
    for asset in assets:
        if asset.get("png_path"):
            filenames.append(Path(asset["png_path"]).name)
        else:
            filenames.append(f"{asset['stem']}.png")
    return filenames
