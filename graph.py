# graph.py
from langgraph.graph import StateGraph, END
from state import IntelligentAnalysisState
from insights import EnhancedInsightGenerator
from typing import Dict, Any
import os
import pandas as pd # Import pandas for the initial state

# --- Configuration ---
# IMPORTANT: Replace with your actual Groq API Key
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "YOUR_GROQ_API_KEY_HERE") 

# Instantiate the Agent/Tool handler
insight_generator = EnhancedInsightGenerator(groq_api_key=GROQ_API_KEY)

# --- Define the Graph ---
workflow = StateGraph(IntelligentAnalysisState)

# 1. Define the Nodes
workflow.add_node("load_charts", insight_generator._get_all_chart_paths_node)
workflow.add_node("analyze_chart", insight_generator.analyze_single_chart_node)
workflow.add_node("final_aggregate", insight_generator.aggregate_insights_node)


# 2. Define the Conditional Edge (The loop controller)
def continue_analysis_router(state: IntelligentAnalysisState) -> str:
    """Routes the graph: 'continue' loop, or 'end_loop' to aggregate."""
    index = state.get('current_chart_index', 0)
    total_charts = len(state.get('chart_paths',))
    
    if index < total_charts:
        # Loop back to analyze the next chart
        return "continue"
    else:
        # All charts processed, exit the loop
        return "end_loop"


# 3. Define the Graph Structure
workflow.set_entry_point("load_charts")

# Step A: After loading, move to the first analysis step
workflow.add_edge("load_charts", "analyze_chart")

# Step B: Define the LOOP control using the conditional router
workflow.add_conditional_edges(
    "analyze_chart", # Source Node
    continue_analysis_router, # Router function
    {
        "continue": "analyze_chart", # If 'continue', loop back to the analysis node
        "end_loop": "final_aggregate" # If 'end_loop', move to aggregation
    }
)

# Step C: After aggregation, the process is complete
workflow.add_edge("final_aggregate", END)

# Compile the graph
app = workflow.compile()


# --- Execution Example ---
if __name__ == "__main__":
    # FINAL CORRECTED INITIAL STATE: All list fields are initialized with
    initial_state = {
        'dataset': pd.DataFrame({'a': [1]}),
        'dataset_info': {'name': 'Financial Performance Data'},
        'analysis_plan': {},
        'specialized_analyses': {},
        'chart_paths':,        # FIXED: Initialized with
        'reports': {},
        'insights':,           # FIXED: Initialized with
        'current_step': "INIT",
        'error_log':,          # FIXED: Initialized with
        'current_chart_index': 0, # Required integer loop counter, set to start position
    }

    print("🚀 Starting sequential chart analysis workflow...")
    
    # Run the graph
    final_state = None
    for step in app.stream(initial_state):
        # Print the output of each node execution
        node_name, state_update = next(iter(step.items()))
        if node_name!= END:
            print(f"|--- Node Executed: {node_name} ---|")
        else:
            print("🛑 Workflow End Reached.")

    # Retrieve the final persistent state
    final_state = app.get_state().values
    
    # The stored knowledge base for your chatbot (retrieved from the reports field)
    knowledge_base = final_state.get('reports', {}).get('chatbot_knowledge', 'Knowledge base not created.')
    
    print("\n==============================================")
    print(" SEQUENTIAL ANALYSIS COMPLETE: KNOWLEDGE BASE GENERATED")
    print("==============================================")
    print(knowledge_base)
    print("==============================================")