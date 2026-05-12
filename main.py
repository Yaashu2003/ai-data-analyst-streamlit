import gemini_patch
# main.py
# Final Intelligent Multi-Agent Data Analysis System

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

from typing import List, Dict, Any
import os
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

# --- CONFIGURATION (Ensure consistency with autoviz.py) ---
DATA_PATH = "Superstore.csv"
#CHARTS_DIR = "charts"

_BASE_DIR = Path(__file__).resolve().parent
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

# =============================================================================
# USAGE EXAMPLE
# =============================================================================

def intelligent_data_analysis(dataset_path=None):
    """
    Example usage of the intelligent analysis system.
    Automates directory cleanup, chart generation, and analysis.
    """
    
    current_data_path = dataset_path if dataset_path else DATA_PATH
    
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
        
    # Convert date columns (FIX: Used dayfirst=True)
    if 'Order Date' in df.columns:
        df['Order_Date'] = pd.to_datetime(df['Order Date'], dayfirst=True, format='mixed') 
    if 'Ship Date' in df.columns:
        df['Ship_Date'] = pd.to_datetime(df['Ship Date'], dayfirst=True, format='mixed')
    
    # Initialize the intelligent analysis system
    load_dotenv(dotenv_path=".env", override=True)
    groq_api_key = os.getenv('GROQ_API_KEY')
    
    if not groq_api_key:
        print("[ERROR] GROQ_API_KEY not found in environment variables")
        return None
    
    print(f"[INFO] Using Groq API Key: {groq_api_key[:10]}...")
    analyzer = IntelligentAnalysisOrchestrator(groq_api_key)
    
    # Run intelligent analysis
    print("\n[INFO] Running Intelligent Analysis Orchestrator...")
    results = analyzer.analyze_dataset(df, "Superstore Sales Dataset")
    
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
