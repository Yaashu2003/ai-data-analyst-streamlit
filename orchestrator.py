import pandas as pd
from pathlib import Path
from typing import Any, Dict

from autoviz_charts import AutovizChartGenerator
from chart_assets import collect_canonical_chart_assets, canonical_asset_paths, normalize_chart_stem
from executor import IntelligentAnalysisExecutor
from insights import EnhancedInsightGenerator
from planner import IntelligentAnalysisPlanner
from state import IntelligentAnalysisState
from visualizer import ComprehensiveVisualizationGenerator


class IntelligentAnalysisOrchestrator:
    """Main orchestrator for intelligent multi-agent data analysis."""

    def __init__(self, groq_api_key: str):
        self.groq_api_key = groq_api_key
        self.planner = IntelligentAnalysisPlanner(groq_api_key)
        self.insight_generator = EnhancedInsightGenerator(groq_api_key)
        self.chart_dir = Path("charts")
        self.chart_html_dir = Path("charts_html")
        self.chart_dir.mkdir(exist_ok=True)
        self.chart_html_dir.mkdir(exist_ok=True)

    def _clear_generated_chart_outputs(self) -> None:
        for directory in [self.chart_dir, self.chart_html_dir, Path("chart_snapshots")]:
            if not directory.exists():
                continue
            for file_path in directory.iterdir():
                if file_path.is_file():
                    try:
                        file_path.unlink()
                    except OSError:
                        pass

    def analyze_dataset(self, df: pd.DataFrame, dataset_name: str = "Dataset") -> Dict[str, Any]:
        print(f"\nStarting intelligent analysis of {dataset_name}")
        print(f"Dataset: {df.shape[0]:,} rows x {df.shape[1]} columns")

        state = IntelligentAnalysisState(
            dataset=df,
            dataset_info={"name": dataset_name},
            analysis_plan={},
            specialized_analyses={},
            chart_paths=[],
            reports={},
            insights=[],
            current_step="initialized",
            error_log=[],
        )

        try:
            print("\nCreating analysis plan...")
            state["analysis_plan"] = self.planner.create_analysis_plan(df, state["dataset_info"])
            if not state["analysis_plan"]:
                raise ValueError("Analysis plan is empty")
            state["current_step"] = "plan_ready"

            print("\nExecuting analysis plan...")
            state["specialized_analyses"] = IntelligentAnalysisExecutor.execute_analysis_plan(
                df,
                state["analysis_plan"],
            )
            state["current_step"] = "analysis_done"

            print("\nGenerating charts...")
            self._clear_generated_chart_outputs()
            chart_paths, chart_paths_html = ComprehensiveVisualizationGenerator.create_intelligent_charts(
                df,
                state["analysis_plan"],
                state["specialized_analyses"],
            )
            state["chart_paths"] = list(chart_paths or [])
            if chart_paths_html:
                state["chart_paths"].extend(chart_paths_html)

            print("\nGenerating AutoViz charts...")
            autoviz_generator = AutovizChartGenerator()
            intelligent_stems = {
                normalize_chart_stem(path)
                for path in (chart_paths or []) + (chart_paths_html or [])
                if path
            }
            autoviz_charts = autoviz_generator.generate_autoviz_charts(
                df,
                dataset_name,
                existing_chart_stems=intelligent_stems,
            )
            if autoviz_charts:
                state["chart_paths"].extend(autoviz_charts)

            print("\nScanning chart directories...")
            canonical_assets = collect_canonical_chart_assets(
                state["chart_paths"],
                self.chart_dir,
                self.chart_html_dir,
            )
            state["chart_paths"] = canonical_asset_paths(canonical_assets)

            print(f"Found {len(canonical_assets)} canonical chart assets")
            print("\nDEBUG -> Canonical chart assets:")
            for asset in canonical_assets:
                print(
                    " -",
                    asset["stem"],
                    f"[source={asset.get('source')}]",
                    f"png={asset.get('png_path')}",
                    f"html={asset.get('html_path')}",
                )

            print("\nGenerating insights...")
            if not state["chart_paths"]:
                print("No charts found -> skipping insight generation")
            else:
                state["insights"] = self.insight_generator.generate_comprehensive_insights(state)
            state["current_step"] = "insights_done"

            results = {
                "dataset_info": {
                    "name": dataset_name,
                    "shape": df.shape,
                    "columns": df.columns.tolist(),
                },
                "analysis_plan": state["analysis_plan"],
                "specialized_analyses": state["specialized_analyses"],
                "chart_paths": state["chart_paths"],
                "insights": state["insights"],
                "errors": state["error_log"],
            }

            print("\nANALYSIS COMPLETE")
            print(f"Charts: {len(state['chart_paths'])}")
            print(f"Insights: {len(state['insights'])}")
            return results

        except Exception as error:
            print(f"\nORCHESTRATOR FAILED: {error}")
            return {
                "error": str(error),
                "dataset_info": {"name": dataset_name},
            }

    def print_analysis_plan(self, results: Dict[str, Any]):
        print("\nANALYSIS PLAN")
        print(results.get("analysis_plan", {}))

    def print_insights(self, results: Dict[str, Any]):
        print("\nINSIGHTS")
        for index, insight in enumerate(results.get("insights", []), 1):
            print(f"{index}. {insight}")
