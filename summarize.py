# In your separate script (e.g., run_analysis.py)

import pandas as pd
from executor import IntelligentAnalysisExecutor # Import the class
from utils import print_saved_results           # Import the result reader
# In your separate script (e.g., run_analysis.py)

# Define the inputs (or load them)
df_data = pd.read_csv('your_data.csv')
analysis_plan = { /* ... your analysis plan dictionary ... */ }
OUTPUT_FILE = 'analysis_results.json'

# Call the function from the separate script
IntelligentAnalysisExecutor.execute_analysis_plan(
    df_data, 
    analysis_plan, 
    output_filename=OUTPUT_FILE
)

# And then fetch the results from the saved file
print_saved_results(OUTPUT_FILE)