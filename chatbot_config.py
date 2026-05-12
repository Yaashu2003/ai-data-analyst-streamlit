import sqlite3
from typing import Dict, Any, List
import json
import pandas as pd
import os 

# --- 1. The Dynamic Global State (Pre-fed Context) ---
# This MOCK_DATA_SCHEMA_CONTEXT is the agent's initial knowledge, calculated from Superstore.csv.
# This data prevents the agent from running SQL queries for high-level summaries it already knows.
MOCK_DATA_SCHEMA_CONTEXT = """
--- DATASET SCHEMA AND AUTOMATED STATISTICAL SUMMARY ---
1. Data Source & Access:
   - Primary Data Store: SQL Database (Table: 'superstore_sales')
   - Access Tool: The agent MUST use the 'sql_connector_tool' for ALL granular data queries.
   - Total Estimated Records (Approx): 99940 (Summary based on the full loaded dataset of 9994 rows.)
2. Structural Columns and SQL Types:
   - The SQL schema matches the column names below. Treat all text fields as TEXT/VARCHAR and numeric fields as REAL/INTEGER.
   - Columns: Row ID, Order ID, Order Date, Ship Date, Ship Mode, Customer ID, Customer Name, Segment, Country, City, State, Postal Code, Region, Product ID, Category, Sub-Category, Product Name, Sales, Quantity, Discount, Profit

3. Key Automated Numerical Summaries (For Reasoning):
   - Sales:
     - SUM: $2,297,200.86
     - MEAN: $229.86
     - RANGE: Min (0.44) to Max (22638.48)
   - Profit:
     - SUM: $286,397.09
     - MEAN: $28.66
     - RANGE: Min (-6599.19) to Max (8399.98)
     - Critical: 1,871 rows (18.7%) have negative profit.
   - Discount:
     - SUM: $1,561.09
     - MEAN: $0.16
     - RANGE: Min (0.0) to Max (0.8)
   - Quantity:
     - SUM: $37,870.00
     - MEAN: $3.79
     - RANGE: Min (1.0) to Max (14.0)

4. Key Dimensions (Categorical Columns):
   - Region:
     - Unique Values: 4
     - Top Values: West, East, Central, South
   - Segment:
     - Unique Values: 3
     - Top Values: Consumer, Corporate, Home Office
   - Category:
     - Unique Values: 3
     - Top Values: Office Supplies, Furniture, Technology

5. Temporal Analysis:
   - Order Date Range: From 2014-01-03 to 2017-12-30

--- END OF CONTEXT ---
"""

# Placeholder for the dynamic report content that accumulates during the conversation
MOCK_REPORT_CONTENT = {
    "Executive Summary": "The core problem is high discount rates leading to 18.7% of transactions being unprofitable. The agent should prioritize identifying the specific products and regions responsible for these losses.",
    "Key Findings": ["West region generates the most sales, but the Central region has a higher rate of loss-making transactions.", "High-discount items in the Furniture category are the main drivers of negative profit."]
}


# --- 2. The Agent's Brain: System Prompt ---
# This instruction set defines the LLM's persona and rules for using tools.
SYSTEM_PROMPT = f"""
You are a world-class Data Analysis Agent for Superstore Sales, powered by a LangGraph workflow.
Your goal is to answer user questions using the most efficient method available.

RULES FOR OPERATION:
1. EFFICIENCY FIRST: Always check the pre-fed MOCK_REPORT_CONTENT and the DATA_SCHEMA_CONTEXT first. If you can answer the question immediately, DO NOT call any tool.
2. GRANULARITY CHECK: If the user asks for a specific, granular data point or a complex aggregation not in the summaries, you MUST write and execute a SQL query using the 'sql_connector_tool'.
3. SQL RULE: When using the tool, provide only the clean, complete SQL query. DO NOT include any explanatory text or markdown. Use snake_case for column names as they are cleaned upon loading. Example column: 'Customer_Name', 'Order_Date', 'Sub_Category', 'Postal_Code'.
4. SYNTHESIS: For general questions (e.g., 'What are the risks?'), use the collected data from MOCK_REPORT_CONTENT and the results of any SQL queries to synthesize a thoughtful, conversational response.

--- PRE-FED DATA CONTEXT (Based on Superstore.csv) ---
{MOCK_DATA_SCHEMA_CONTEXT}

--- CURRENT REPORT CONTENT (Analyzed Data) ---
{json.dumps(MOCK_REPORT_CONTENT, indent=2)}
"""

# --- 3. The Agent's Capability: Tool Definition ---
# This function simulates the critical SQL tool by loading the CSV into an in-memory database.
def sql_connector_tool(query: str) -> List[Dict[str, Any]]:
    """
    Executes a read-only SQL SELECT query against the 'superstore_sales' table
    and returns the results as a list of dictionaries. 
    
    The agent MUST use this tool for all detailed data retrieval.
    The database schema is provided in the SYSTEM_PROMPT.
    
    Args:
        query: The SQL SELECT query to execute (e.g., SELECT * FROM superstore_sales LIMIT 5).
        
    Returns:
        A list of dictionaries representing the query results or an error message.
    """
    # The file name is hardcoded as it was provided by the user
    FILE_NAME = 'Superstore.csv'

    try:
        # 1. Load the CSV data
        df = pd.read_csv(FILE_NAME)
        
        # 2. Clean up column names to be SQL-friendly (snake_case)
        # This standardizes column names for SQL queries (e.g., 'Order Date' -> 'Order_Date')
        df.columns = df.columns.str.replace('[^A-Za-z0-9_]+', '_', regex=True)
        df.columns = df.columns.str.strip().str.replace(' ', '_').str.strip('_')
        
        # 3. Create an in-memory SQLite database
        conn = sqlite3.connect(':memory:')
        
        # 4. Write the DataFrame to a SQL table
        df.to_sql('superstore_sales', conn, if_exists='replace', index=False)
        
        # 5. Execute the SQL query
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [description[0] for description in cursor.description]
        results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        # Limit results for LLM readability
        if len(results) > 10:
             return results[:10] + [{"Note": f"Results truncated. Showing first 10 of {len(results)} rows."}]

        return results
    except FileNotFoundError:
        return [{"error": f"SQL Query failed: Data file '{FILE_NAME}' not found. Ensure it is accessible."}]
    except sqlite3.Error as e:
        return [{"error": f"SQL Query failed: {e}. Check table/column names against the schema. Remember to use snake_case."}]
    except Exception as e:
        return [{"error": f"An unexpected error occurred: {e}"}]
    finally:
        if 'conn' in locals() and conn:
            conn.close()