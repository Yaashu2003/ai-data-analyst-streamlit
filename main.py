import gemini_patch
# main.py
# Final Intelligent Multi-Agent Data Analysis System

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

from typing import List, Dict, Any
import json
import os
import re
from datetime import datetime, date
from dotenv import load_dotenv
import shutil 
import subprocess 
import sys 
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from orchestrator import IntelligentAnalysisOrchestrator
from chart_assets import collect_canonical_chart_assets, canonical_asset_paths
from visualizer import ComprehensiveVisualizationGenerator

# --- CONFIGURATION (Ensure consistency with autoviz.py) ---
DATA_PATH = "Superstore.csv"
#CHARTS_DIR = "charts"

_BASE_DIR = Path(__file__).resolve().parent
DATASET_INPUT_DIR = _BASE_DIR / "dataset_input"
DATASET_MANIFEST = DATASET_INPUT_DIR / "manifest.json"
CHARTS_DIR = str(_BASE_DIR / "charts")
CHARTS_DIR_HTML = str(_BASE_DIR / "charts_html")
AUTOVIZ_SCRIPT_NAME = 'autoviz_charts.py' 
REPORT_FILE = 'analysis_report.txt' 
# >> NEW CONFIGURATION ADDED HERE <<
REPORT_GENERATOR_SCRIPT_NAME = 'report.py' 
# -----------------------------------------------------------

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

# ... (make_json_serializable function remains the same) ...
def make_json_serializable(obj):
    """Convert numpy/pandas objects to JSON serializable format"""
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(v) for v in obj]
    elif hasattr(obj, 'dtype'):
        return str(obj)
    elif isinstance(obj, (np.int64, np.float64, np.int32, np.float32)):
        return float(obj) if 'float' in str(type(obj)) else int(obj)
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif hasattr(obj, 'item'):
        return obj.item()
    elif hasattr(obj, 'strftime'):
        return str(obj)
    elif 'Period' in str(type(obj)):
        return str(obj)
    else:
        return obj

def save_results_to_file(results: Dict[str, Any], filename: str):
    """
    Saves the final analysis results and detailed Gemini VLM insights 
    to a text file, properly formatting the Gemini output.
    """
    
    # Check if results are valid
    if not results:
        print(f"[ERROR] Cannot save results: Results dictionary is empty or None.")
        return
        
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"--- Intelligent Data Analysis Report ---\n")
            f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=========================================\n\n")

            # 1. Print Summary
            f.write("### 1. Analysis Summary ###\n")
            f.write(f"Total Specialized Analyses: {len(results.get('specialized_analyses', {}))}\n")
            f.write(f"Total Charts Analyzed: {len(results.get('chart_paths', []))}\n")
            f.write(f"Total Insights Generated: {len(results.get('insights', []))}\n")
            f.write(f"Errors Encountered: {len(results.get('errors', []))}\n\n")

            # 2. Print Detailed Insights (Gemini VLM Output)
            insights = results.get('insights', [])
            if insights:
                f.write("### 2. Generated Insights (Gemini VLM Output) ###\n")
                
                # Each 'insight' string contains the chart type and the bulleted VLM output.
                for i, insight in enumerate(insights, 1):
                    f.write(f"-----------------------------------------\n")
                    f.write(f"Insight #{i}:\n")
                    
                    if insight.startswith("[GEMINI INSIGHTS]") or insight.startswith("[SPECIALIZED INSIGHTS]"):
                        f.write(f"{insight.strip()}\n")
                    else:
                        parts = insight.split(': ', 1)
                        if len(parts) == 2:
                            # Clean up chart context part (removes '' and '**')
                            title = parts[0].replace('', '').replace('**', '').strip()
                            body = parts[1].strip()
                            
                            f.write(f"Context: {title}\n")
                            f.write(f"VLM Findings:\n")
                            
                            # Write the raw VLM bulleted output
                            f.write(body + "\n")
                        else:
                            # Fallback for poorly formatted insights
                            f.write(f"Raw Insight: {insight.strip()}\n")
                        
                f.write("-----------------------------------------\n\n")
            else:
                f.write("No high-level insights were generated.\n\n")
            
            # 3. Print Specialized Analyses (Keys Only)
            if results.get('specialized_analyses'):
                f.write("### 3. Specialized Analysis Results (Keys Only) ###\n")
                f.write(str(list(results['specialized_analyses'].keys())) + "\n")
            
        print(f"[INFO] Analysis results successfully saved to: {filename}")

    except Exception as e:
        print(f"[ERROR] Failed to write report file {filename}: {e}")

# >> NEW FUNCTION TO EXECUTE THE REPORT GENERATOR SCRIPT <<
def run_report_generator(script_name: str):
    """Executes the HTML report generation script."""
    print(f"\n[INFO] Launching {script_name} to generate the interactive HTML report...")
    
    # Construct the absolute path to the script
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_name)

    try:
        result = subprocess.run(
            [sys.executable, script_path], 
            check=True, 
            capture_output=True, 
            text=True, 
            encoding='utf-8'
        )
        print(f"[INFO] {script_name} completed successfully.")
        
        # Print the output from the report generator script (which includes the success message)
        if result.stdout:
            print("--- report_generator.py Output ---\n" + result.stdout)
        if result.stderr:
             # Print any errors/warnings from the script
             print("--- report_generator.py Warnings/Errors ---\n" + result.stderr)

    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error running {script_name}. Interactive report was NOT generated.")
        print(f"--- Subprocess Error Output ---\n{e.stderr}")
    except FileNotFoundError:
        print(f"[ERROR] Error: {script_name} not found. Check your file path.")


def read_dataset_manifest() -> Dict[str, Any]:
    if not DATASET_MANIFEST.exists():
        return {}
    try:
        return json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
    except Exception as error:
        print(f"[WARNING] Could not read dataset manifest: {error}")
        return {}


def dataset_label_from_manifest(manifest: Dict[str, Any]) -> str:
    files = [
        str(item.get("name", "")).strip()
        for item in manifest.get("files", [])
        if str(item.get("name", "")).strip()
    ]
    if len(files) > 1:
        return "Combined Dataset: " + ", ".join(files[:3]) + (f" + {len(files) - 3} more" if len(files) > 3 else "")
    if files:
        return files[0]
    return "Uploaded Dataset"


def safe_dataset_prefix(file_name: str, index: int) -> str:
    stem = Path(file_name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or f"dataset_{index + 1}"
    return f"{index + 1:02d}_{stem[:34]}__"


def read_dataset_table(path: str) -> pd.DataFrame:
    dataset_path = Path(path)
    suffix = dataset_path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(dataset_path)
    return pd.read_csv(dataset_path, encoding="latin1")


def normalize_common_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    if 'Order Date' in normalized.columns:
        normalized['Order_Date'] = pd.to_datetime(normalized['Order Date'], dayfirst=True, format='mixed')
    if 'Ship Date' in normalized.columns:
        normalized['Ship_Date'] = pd.to_datetime(normalized['Ship Date'], dayfirst=True, format='mixed')
    return normalized


def merge_analysis_result(target: Dict[str, Any], result: Dict[str, Any], label: str) -> None:
    if not result:
        return

    for key, value in result.get("specialized_analyses", {}).items():
        target.setdefault("specialized_analyses", {})[f"{label}::{key}"] = value
    target.setdefault("chart_paths", []).extend(result.get("chart_paths", []) or [])
    target.setdefault("errors", []).extend(result.get("errors", []) or [])


def intelligent_multi_dataset_analysis(
    analyzer: IntelligentAnalysisOrchestrator,
    combined_df: pd.DataFrame,
    manifest: Dict[str, Any],
    dataset_name: str,
) -> Dict[str, Any]:
    files = manifest.get("files", [])
    aggregate_results: Dict[str, Any] = {
        "dataset_info": {
            "name": dataset_name,
            "shape": combined_df.shape,
            "columns": combined_df.columns.tolist(),
            "source_files": [item.get("name") for item in files if item.get("name")],
        },
        "analysis_plan": {
            "strategy": "Per-file EDA first, then combined cross-file comparison charts.",
        },
        "specialized_analyses": {},
        "chart_paths": [],
        "insights": [],
        "errors": [],
    }

    print(f"\n[INFO] Running separate analysis for {len(files)} uploaded dataset files...")
    for index, item in enumerate(files):
        file_name = item.get("name") or f"dataset_{index + 1}"
        file_path = item.get("path")
        if not file_path or not Path(file_path).exists():
            aggregate_results["errors"].append(f"Source file missing for per-file analysis: {file_name}")
            continue

        try:
            source_df = normalize_common_date_columns(read_dataset_table(file_path))
        except Exception as error:
            aggregate_results["errors"].append(f"Failed to read {file_name}: {error}")
            continue

        prefix = safe_dataset_prefix(file_name, index)
        print(f"\n[INFO] Per-file analysis: {file_name} -> prefix {prefix}")
        result = analyzer.analyze_dataset(
            source_df,
            file_name,
            clear_outputs=(index == 0),
            generate_insights=False,
            filename_prefix=prefix,
        )
        merge_analysis_result(aggregate_results, result, file_name)

    print("\n[INFO] Generating combined cross-file bridge charts...")
    combined_png, combined_html = ComprehensiveVisualizationGenerator.create_intelligent_charts(
        combined_df,
        {"visualizations": []},
        {},
        filename_prefix="combined__",
    )
    aggregate_results["chart_paths"].extend(combined_png or [])
    aggregate_results["chart_paths"].extend(combined_html or [])

    if "source_file" in combined_df.columns:
        source_counts = combined_df["source_file"].astype(str).value_counts()
        aggregate_results["specialized_analyses"]["cross_file_source_summary"] = {
            "analysis_type": "cross_file_source_summary",
            "rows_by_source_file": {str(key): int(value) for key, value in source_counts.items()},
            "total_rows": int(len(combined_df)),
            "total_source_files": int(source_counts.size),
        }

    canonical_assets = collect_canonical_chart_assets(
        aggregate_results["chart_paths"],
        Path(CHARTS_DIR),
        Path(CHARTS_DIR_HTML),
    )
    aggregate_results["chart_paths"] = canonical_asset_paths(canonical_assets)

    print(f"[INFO] Gemini chart selection pool: {len(aggregate_results['chart_paths'])} chart assets.")
    if aggregate_results["chart_paths"]:
        final_state = {
            "dataset": combined_df,
            "dataset_info": aggregate_results["dataset_info"],
            "analysis_plan": aggregate_results["analysis_plan"],
            "specialized_analyses": aggregate_results["specialized_analyses"],
            "chart_paths": aggregate_results["chart_paths"],
            "reports": {},
            "insights": [],
            "current_step": "multi_dataset_selection",
            "error_log": aggregate_results["errors"],
        }
        aggregate_results["insights"] = analyzer.insight_generator.generate_comprehensive_insights(final_state)

    return aggregate_results

# =============================================================================
# USAGE EXAMPLE
# =============================================================================

def intelligent_data_analysis(dataset_path=None, dataset_name=None):
    """
    Example usage of the intelligent analysis system.
    Automates directory cleanup, chart generation, and analysis.
    """
    
    manifest = read_dataset_manifest()
    current_data_path = dataset_path if dataset_path else DATA_PATH
    current_dataset_name = dataset_name or dataset_label_from_manifest(manifest)
    
    # Ensure chart directories exist and are CLEAR for the new analysis
    for d in [CHARTS_DIR, CHARTS_DIR_HTML]:
        if os.path.exists(d):
            for item in os.listdir(d):
                item_path = os.path.join(d, item)
                try:
                    if os.path.isfile(item_path):
                        os.remove(item_path)
                    elif os.path.is_dir(item_path):
                        shutil.rmtree(item_path)
                except:
                    pass
        else:
            os.makedirs(d, exist_ok=True)
    
    # =========================================================================
    # 1. AUTOMATED CHART GENERATION (Runs autoviz.py)
    # =========================================================================
    
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), AUTOVIZ_SCRIPT_NAME)

    print(f"\n[INFO] Launching {AUTOVIZ_SCRIPT_NAME} to generate charts...")
    try:
        result = subprocess.run(
            [sys.executable, script_path], 
            check=True, 
            capture_output=True, 
            text=True, 
            encoding='utf-8'
        )
        print("[INFO] Chart generation completed successfully.") 
        if result.stderr:
             print("--- autoviz.py Warnings/Errors ---\n" + result.stderr)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Error running {AUTOVIZ_SCRIPT_NAME}. Analysis cannot proceed.")
        print(f"--- Subprocess Error Output ---\n{e.stderr}")
        return None
    except FileNotFoundError:
        print(f"[ERROR] Error: {AUTOVIZ_SCRIPT_NAME} not found. Check your file path.")
        return None

    
    # =========================================================================
    # 2. LOAD DATA AND RUN ORCHESTRATOR
    # =========================================================================
    
    # Load your dataset
    try:
        df = pd.read_csv(current_data_path, encoding="latin1")
    except FileNotFoundError:
        print(f"[ERROR] {current_data_path} not found.") 
        return None
        
    df = normalize_common_date_columns(df)
    
    # Initialize the intelligent analysis system
    load_dotenv(dotenv_path=".env", override=True)
    groq_api_key = os.getenv('GROQ_API_KEY')
    
    if not groq_api_key:
        print("[ERROR] GROQ_API_KEY not found in environment variables")
        return None
    
    print(f"[INFO] Using Groq API Key: {groq_api_key[:10]}...")
    analyzer = IntelligentAnalysisOrchestrator(groq_api_key)

    if len(manifest.get("files", [])) > 1:
        return intelligent_multi_dataset_analysis(
            analyzer,
            df,
            manifest,
            current_dataset_name,
        )
    
    # Run intelligent analysis
    print("\n[INFO] Running Intelligent Analysis Orchestrator...")
    results = analyzer.analyze_dataset(df, current_dataset_name)
    
    return results

if __name__ == "__main__":
    results = intelligent_data_analysis()
    if results:
        # 1. Save results to file
        save_results_to_file(results, REPORT_FILE)
        
        # 2. Print final console summary
        print(f"\n[SUCCESS] Intelligent analysis completed successfully!") 
        print(f"Specialized analyses: {len(results.get('specialized_analyses', {}))}")
        print(f"Charts analyzed: {len(results.get('chart_paths', []))}")
        print(f"Insights generated: {len(results.get('insights', []))}")
        print(f"Errors encountered: {len(results.get('errors', []))}")

        # 3. >> NEW STEP: Generate the interactive HTML report <<
        run_report_generator(REPORT_GENERATOR_SCRIPT_NAME)
            # =========================================================================
    # FINAL CLEANUP (ADDED - NO STRUCTURE CHANGE)
    # =========================================================================
    print("\n[INFO] Running final cleanup...")

    # NOTE: Dataset file is intentionally preserved for the dataset chatbot
    print(f"[INFO] Dataset file preserved for chatbot: {DATA_PATH}")

    # Delete DB files / directories
    DB_PATHS = [
        "database.db",
        "analysis.db",
        "chroma_db",
        "vectorstore",
        "embeddings_cache"
    ]

    for path in DB_PATHS:
        try:
            if os.path.isfile(path):
                os.remove(path)
                print(f"[INFO] Deleted DB file: {path}")
            elif os.path.isdir(path):
                shutil.rmtree(path)
                print(f"[INFO] Deleted DB directory: {path}")
            else:
                print(f"[WARNING] DB path not found: {path}")
        except Exception as e:
            print(f"[ERROR] Failed to delete {path}: {e}")
