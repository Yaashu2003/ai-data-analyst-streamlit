
from typing import TypedDict, List, Dict, Any, Optional, Tuple
import pandas as pd

# =============================================================================
# ENHANCED SYSTEM STATE DEFINITION
# =============================================================================

class IntelligentAnalysisState(TypedDict):
    """Enhanced state for intelligent analysis system"""
    dataset: pd.DataFrame
    dataset_info: Dict[str, Any]
    analysis_plan: Dict[str, Any]  # LLM-generated analysis plan
    specialized_analyses: Dict[str, Any]  # Results from specialized analysis functions
    chart_paths: List[str]
    reports: Dict[str, str]
    
    # --- ADDED FOR SEQUENTIAL CHART ANALYSIS LOOP CONTROL ---
    current_chart_index: int  # Tracks the index of the chart currently being analyzed
    
    # The existing insights field will be used to store the sequential 2-line analyses
    insights: List[str]
    
    current_step: str
    error_log: List[str]