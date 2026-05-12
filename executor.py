from utils import summarize_for_llm
from anl_funcs import SpecializedAnalytics
import json
from typing import TypedDict, List, Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np

# =============================================================================
# INTELLIGENT ANALYSIS EXECUTOR
# =============================================================================

class IntelligentAnalysisExecutor:
    """Executes the LLM-generated analysis plan using specialized functions"""

    @staticmethod
    def _sanitize_for_json(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(IntelligentAnalysisExecutor._sanitize_for_json(key)): IntelligentAnalysisExecutor._sanitize_for_json(val)
                for key, val in value.items()
            }
        if isinstance(value, list):
            return [IntelligentAnalysisExecutor._sanitize_for_json(item) for item in value]
        if isinstance(value, tuple):
            return [IntelligentAnalysisExecutor._sanitize_for_json(item) for item in value]
        if isinstance(value, (pd.Timestamp, pd.Timedelta)):
            return str(value)
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, pd.Series):
            return IntelligentAnalysisExecutor._sanitize_for_json(value.to_dict())
        if isinstance(value, pd.DataFrame):
            return IntelligentAnalysisExecutor._sanitize_for_json(value.to_dict(orient="records"))
        return value
    
    @staticmethod
    def execute_analysis_plan(df: pd.DataFrame, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the specialized analyses defined in the plan"""
        
        results = {}
        print(plan.get('specialized_analyses', []))
        output_filename = 'analysis_results.txt'
        for analysis_index, analysis in enumerate(plan.get('specialized_analyses', [])):
            function_name = analysis.get('function')
            print(function_name)
            columns = analysis.get('columns', [])
            parameters = analysis.get('parameters', {})
            
            try:
                # Get the analysis function
                if not hasattr(SpecializedAnalytics, function_name):
                    results[f"{function_name}_error_{analysis_index}"] = {'error': f'Unsupported analysis function: {function_name}'}
                    continue
                analysis_func = getattr(SpecializedAnalytics, function_name)
                
                # Execute analysis with appropriate parameters
                if function_name == 'time_series_decomposition':
                    if len(columns) >= 2:
                        result = analysis_func(df, columns[0], columns[1])
                elif function_name == 'cohort_analysis':
                    if len(columns) >= 2:
                        value_col = columns[2] if len(columns) > 2 else None
                        result = analysis_func(df, columns[0], columns[1], value_col)
                elif function_name == 'customer_segmentation':
                    if len(columns) >= 2:
                        result = analysis_func(df, columns[0], columns[1:])
                elif function_name == 'correlation_network_analysis':
                    threshold = parameters.get('threshold', 0.5)
                    result = analysis_func(df, columns, threshold)
                elif function_name == 'anomaly_detection':
                    result = analysis_func(df, columns)
                elif function_name == 'distribution_comparison':
                    if len(columns) >= 2:
                        result = analysis_func(df, columns[0], columns[1])
                else:
                    result = {'error': f'Unknown analysis function: {function_name}'}
                print(summarize_for_llm(result))
                
                results[f"{function_name}_{analysis_index}"] = result
                
            except Exception as e:
                print(e)
                results[f"{function_name}_error_{analysis_index}"] = {'error': str(e)}
        try:
            sanitized_results = IntelligentAnalysisExecutor._sanitize_for_json(results)
            results_string = json.dumps(sanitized_results, indent=4)
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write(results_string)
            
            print(f"\n Results saved to plain text file {output_filename}.")
        except Exception as e:
            print(f"\n ERROR: Could not write results to text file: {e}")
        return results
